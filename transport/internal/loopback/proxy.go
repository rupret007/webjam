// Package loopback owns the only UDP sockets visible to Jamulus. GuestProxy
// locks to the first loopback Jamulus client; HostProxy uses one connected
// loopback socket per remote musician so JamulusServer sees distinct sources.
package loopback

import (
	"context"
	"errors"
	"net"
	"sync"
	"time"

	"github.com/rupret007/webjam/transport/internal/limits"
)

const maxUDPPacketBytes = 65_535

var (
	ErrClosed      = errors.New("loopback proxy closed")
	ErrNoPeer      = errors.New("loopback guest peer not learned")
	ErrNotLoopback = errors.New("UDP endpoint is not loopback")
	ErrOversize    = errors.New("UDP datagram exceeds live payload limit")
)

type Endpoint interface {
	ReadDatagram(context.Context) ([]byte, error)
	WriteDatagram(context.Context, []byte) error
	LocalPort() int
	Close() error
}

type GuestProxy struct {
	conn      *net.UDPConn
	peerMu    sync.RWMutex
	peer      *net.UDPAddr
	readMu    sync.Mutex
	readBuf   []byte
	closeOnce sync.Once
	closeErr  error
}

func NewGuestProxy() (*GuestProxy, error) {
	conn, err := net.ListenUDP("udp4", &net.UDPAddr{IP: net.IPv4(127, 0, 0, 1), Port: 0})
	if err != nil {
		return nil, err
	}
	return &GuestProxy{conn: conn, readBuf: make([]byte, maxUDPPacketBytes)}, nil
}

func (p *GuestProxy) LocalPort() int { return p.conn.LocalAddr().(*net.UDPAddr).Port }

func (p *GuestProxy) ReadDatagram(ctx context.Context) ([]byte, error) {
	p.readMu.Lock()
	defer p.readMu.Unlock()
	for {
		if err := setReadDeadline(ctx, p.conn); err != nil {
			return nil, err
		}
		n, remote, err := p.conn.ReadFromUDP(p.readBuf)
		if err != nil {
			if isRetryableTimeout(ctx, err) {
				continue
			}
			return nil, normalizeClose(err)
		}
		if !remote.IP.IsLoopback() {
			continue
		}
		p.peerMu.Lock()
		if p.peer == nil {
			p.peer = cloneUDPAddr(remote)
		}
		accepted := p.peer.IP.Equal(remote.IP) && p.peer.Port == remote.Port
		p.peerMu.Unlock()
		if !accepted {
			continue
		}
		if n < 1 || n > limits.MaxLivePayloadBytes {
			return nil, ErrOversize
		}
		return append([]byte(nil), p.readBuf[:n]...), nil
	}
}

func (p *GuestProxy) WriteDatagram(ctx context.Context, payload []byte) error {
	if err := validatePayload(payload); err != nil {
		return err
	}
	p.peerMu.RLock()
	peer := cloneUDPAddr(p.peer)
	p.peerMu.RUnlock()
	if peer == nil {
		return ErrNoPeer
	}
	if err := setWriteDeadline(ctx, p.conn); err != nil {
		return err
	}
	_, err := p.conn.WriteToUDP(payload, peer)
	return normalizeClose(err)
}

func (p *GuestProxy) Close() error {
	p.closeOnce.Do(func() { p.closeErr = p.conn.Close() })
	return p.closeErr
}

type HostProxy struct {
	conn      *net.UDPConn
	readMu    sync.Mutex
	readBuf   []byte
	closeOnce sync.Once
	closeErr  error
}

func NewHostProxy(target *net.UDPAddr) (*HostProxy, error) {
	if target == nil || !target.IP.IsLoopback() || target.IP.To4() == nil || target.Port < 1 || target.Port > 65_535 {
		return nil, ErrNotLoopback
	}
	conn, err := net.DialUDP(
		"udp4",
		&net.UDPAddr{IP: net.IPv4(127, 0, 0, 1), Port: 0},
		target,
	)
	if err != nil {
		return nil, err
	}
	return &HostProxy{conn: conn, readBuf: make([]byte, maxUDPPacketBytes)}, nil
}

func (p *HostProxy) LocalPort() int { return p.conn.LocalAddr().(*net.UDPAddr).Port }

func (p *HostProxy) ReadDatagram(ctx context.Context) ([]byte, error) {
	p.readMu.Lock()
	defer p.readMu.Unlock()
	for {
		if err := setReadDeadline(ctx, p.conn); err != nil {
			return nil, err
		}
		n, err := p.conn.Read(p.readBuf)
		if err != nil {
			if isRetryableTimeout(ctx, err) {
				continue
			}
			return nil, normalizeClose(err)
		}
		if n < 1 || n > limits.MaxLivePayloadBytes {
			return nil, ErrOversize
		}
		return append([]byte(nil), p.readBuf[:n]...), nil
	}
}

func (p *HostProxy) WriteDatagram(ctx context.Context, payload []byte) error {
	if err := validatePayload(payload); err != nil {
		return err
	}
	if err := setWriteDeadline(ctx, p.conn); err != nil {
		return err
	}
	_, err := p.conn.Write(payload)
	return normalizeClose(err)
}

func (p *HostProxy) Close() error {
	p.closeOnce.Do(func() { p.closeErr = p.conn.Close() })
	return p.closeErr
}

func validatePayload(payload []byte) error {
	if len(payload) < 1 || len(payload) > limits.MaxLivePayloadBytes {
		return ErrOversize
	}
	return nil
}

func setReadDeadline(ctx context.Context, conn *net.UDPConn) error {
	deadline := time.Now().Add(limits.SocketPollInterval)
	if contextDeadline, ok := ctx.Deadline(); ok && contextDeadline.Before(deadline) {
		deadline = contextDeadline
	}
	return conn.SetReadDeadline(deadline)
}

func setWriteDeadline(ctx context.Context, conn *net.UDPConn) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	deadline := time.Now().Add(limits.SocketPollInterval)
	if contextDeadline, ok := ctx.Deadline(); ok && contextDeadline.Before(deadline) {
		deadline = contextDeadline
	}
	return conn.SetWriteDeadline(deadline)
}

func isRetryableTimeout(ctx context.Context, err error) bool {
	if ctx.Err() != nil {
		return false
	}
	var netErr net.Error
	return errors.As(err, &netErr) && netErr.Timeout()
}

func normalizeClose(err error) error {
	if err == nil {
		return nil
	}
	if errors.Is(err, net.ErrClosed) {
		return ErrClosed
	}
	return err
}

func cloneUDPAddr(addr *net.UDPAddr) *net.UDPAddr {
	if addr == nil {
		return nil
	}
	return &net.UDPAddr{IP: append(net.IP(nil), addr.IP...), Port: addr.Port, Zone: addr.Zone}
}
