package webjoin

import (
	"context"
	"crypto/hmac"
	"net"
	"sync"
	"sync/atomic"
	"time"

	"github.com/pion/rtp"
)

type udpBridge struct {
	conn      *net.UDPConn
	token     [32]byte
	once      sync.Once
	handshake sync.Once
	readyErr  error
	ready     atomic.Bool
}

// NewUDPBridge prepares a host-provisioned loopback socket without sending any
// datagrams. Authentication is lazy: only an enrolled endpoint invokes the
// first ReadOpus/WriteOpus, which starts Python's Jamulus connection. Connected
// UDP rejects packets from any other source.
func NewUDPBridge(ctx context.Context, port uint16, token [32]byte) (Bridge, error) {
	if ctx.Err() != nil {
		return nil, ctx.Err()
	}
	if port == 0 || token == ([32]byte{}) {
		return nil, ErrBridge
	}
	conn, err := net.DialUDP("udp4", &net.UDPAddr{IP: net.IPv4(127, 0, 0, 1)}, &net.UDPAddr{IP: net.IPv4(127, 0, 0, 1), Port: int(port)})
	if err != nil {
		return nil, ErrBridge
	}
	return &udpBridge{conn: conn, token: token}, nil
}
func (b *udpBridge) ensureReady(ctx context.Context) error {
	b.handshake.Do(func() {
		deadline := time.Now().Add(2 * time.Second)
		if d, ok := ctx.Deadline(); ok && d.Before(deadline) {
			deadline = d
		}
		_ = b.conn.SetDeadline(deadline)
		if ctx.Err() != nil {
			b.readyErr = ctx.Err()
			return
		}
		if _, err := b.conn.Write(append([]byte("WJWB1"), b.token[:]...)); err != nil {
			b.readyErr = ErrBridge
			return
		}
		buffer := make([]byte, 64)
		n, err := b.conn.Read(buffer)
		if err != nil || n != 37 || string(buffer[:5]) != "WJWA1" || !hmac.Equal(buffer[5:n], b.token[:]) {
			b.readyErr = ErrBridge
			return
		}
		_ = b.conn.SetDeadline(time.Time{})
		b.ready.Store(true)
	})
	return b.readyErr
}
func (b *udpBridge) WriteOpus(ctx context.Context, packet Packet) error {
	if ctx.Err() != nil {
		return ctx.Err()
	}
	if len(packet.Payload) == 0 || len(packet.Payload) > MaxOpusBytes {
		return ErrBridge
	}
	if err := b.ensureReady(ctx); err != nil {
		return err
	}
	raw, err := (&rtp.Packet{Header: rtp.Header{Version: 2, PayloadType: 111, SequenceNumber: packet.SequenceNumber, Timestamp: packet.Timestamp, SSRC: 1}, Payload: packet.Payload}).Marshal()
	if err != nil {
		return ErrBridge
	}
	deadline := time.Now().Add(time.Second)
	if d, ok := ctx.Deadline(); ok && d.Before(deadline) {
		deadline = d
	}
	_ = b.conn.SetWriteDeadline(deadline)
	if _, err = b.conn.Write(raw); err != nil {
		return ErrBridge
	}
	return nil
}
func (b *udpBridge) ReadOpus(ctx context.Context) (Packet, error) {
	if err := b.ensureReady(ctx); err != nil {
		return Packet{}, err
	}
	buffer := make([]byte, 1501)
	for {
		if ctx.Err() != nil {
			return Packet{}, ctx.Err()
		}
		_ = b.conn.SetReadDeadline(time.Now().Add(100 * time.Millisecond))
		n, err := b.conn.Read(buffer)
		if timeout, ok := err.(net.Error); ok && timeout.Timeout() {
			continue
		}
		if err != nil {
			return Packet{}, ErrBridge
		}
		var packet rtp.Packet
		if n > 1500 || packet.Unmarshal(buffer[:n]) != nil || packet.Version != 2 || packet.PayloadType != 111 || len(packet.Payload) == 0 || len(packet.Payload) > MaxOpusBytes {
			return Packet{}, ErrBridge
		}
		return Packet{Payload: append([]byte(nil), packet.Payload...), SequenceNumber: packet.SequenceNumber, Timestamp: packet.Timestamp}, nil
	}
}
func (b *udpBridge) Close() error {
	b.once.Do(func() {
		_ = b.conn.SetWriteDeadline(time.Now().Add(100 * time.Millisecond))
		if b.ready.Load() {
			_, _ = b.conn.Write(append([]byte("WJWC1"), b.token[:]...))
		}
		_ = b.conn.Close()
	})
	return nil
}
