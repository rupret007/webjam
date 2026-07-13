// Package icequic adapts Pion ICE's packet-preserving net.Conn to quic-go's
// net.PacketConn API and owns the session TLS/QUIC configuration.
package icequic

import (
	"errors"
	"net"
	"sync"
	"time"
)

var ErrUnexpectedPeer = errors.New("packet addressed to unexpected peer")

// FixedPeerPacketConn presents a stable synthetic address to QUIC while Pion
// ICE is free to change the selected candidate pair beneath the ICE Conn.
type FixedPeerPacketConn struct {
	conn       net.Conn
	local      net.Addr
	peer       net.Addr
	closeOnce  sync.Once
	closeError error
}

func NewFixedPeerPacketConn(conn net.Conn, local, peer net.Addr) (*FixedPeerPacketConn, error) {
	if conn == nil || local == nil || peer == nil {
		return nil, errors.New("fixed peer PacketConn requires connection and addresses")
	}
	return &FixedPeerPacketConn{conn: conn, local: local, peer: peer}, nil
}

func (c *FixedPeerPacketConn) ReadFrom(payload []byte) (int, net.Addr, error) {
	n, err := c.conn.Read(payload)
	if err != nil {
		return n, nil, err
	}
	return n, c.peer, nil
}

func (c *FixedPeerPacketConn) WriteTo(payload []byte, peer net.Addr) (int, error) {
	if peer == nil || peer.Network() != c.peer.Network() || peer.String() != c.peer.String() {
		return 0, ErrUnexpectedPeer
	}
	return c.conn.Write(payload)
}

func (c *FixedPeerPacketConn) Close() error {
	c.closeOnce.Do(func() { c.closeError = c.conn.Close() })
	return c.closeError
}

func (c *FixedPeerPacketConn) LocalAddr() net.Addr                { return c.local }
func (c *FixedPeerPacketConn) SetDeadline(t time.Time) error      { return c.conn.SetDeadline(t) }
func (c *FixedPeerPacketConn) SetReadDeadline(t time.Time) error  { return c.conn.SetReadDeadline(t) }
func (c *FixedPeerPacketConn) SetWriteDeadline(t time.Time) error { return c.conn.SetWriteDeadline(t) }

var _ net.PacketConn = (*FixedPeerPacketConn)(nil)
