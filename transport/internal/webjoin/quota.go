package webjoin

import (
	"context"
	"io"
	"net"
	"sync/atomic"

	"github.com/pion/transport/v4"
)

// All Pion sockets share one budget, including ICE/DTLS/SCTP/SRTP/RTCP and
// TURN/TLS framing, not merely decoded audio. Charges precede writes. A fixed
// per-operation overhead conservatively covers IP/UDP headers. Received bytes
// are charged before delivery to Pion. The first over-limit operation closes
// the entire endpoint; inbound packets cannot be prevented before arrival.
type byteQuota struct {
	used      atomic.Uint64
	exhausted atomic.Bool
	limit     uint64
	onLimit   func()
}

func (q *byteQuota) take(n int) bool {
	if q.exhausted.Load() {
		return false
	}
	if n <= 0 {
		return true
	}
	amount := uint64(n) + 64
	for {
		old := q.used.Load()
		if amount > q.limit || old > q.limit-amount {
			q.exhausted.Store(true)
			if q.onLimit != nil {
				q.onLimit()
			}
			return false
		}
		if q.used.CompareAndSwap(old, old+amount) {
			return true
		}
	}
}

type quotaNet struct {
	transport.Net
	quota *byteQuota
}

func (n *quotaNet) ListenPacket(network, address string) (net.PacketConn, error) {
	c, e := n.Net.ListenPacket(network, address)
	if e != nil {
		return nil, e
	}
	return &quotaPacket{PacketConn: c, q: n.quota}, nil
}
func (n *quotaNet) ListenUDP(network string, address *net.UDPAddr) (transport.UDPConn, error) {
	c, e := n.Net.ListenUDP(network, address)
	if e != nil {
		return nil, e
	}
	return &quotaUDP{UDPConn: c, q: n.quota}, nil
}
func (n *quotaNet) Dial(network, address string) (net.Conn, error) {
	c, e := n.Net.Dial(network, address)
	if e != nil {
		return nil, e
	}
	return &quotaConn{Conn: c, q: n.quota}, nil
}
func (n *quotaNet) DialUDP(network string, local, remote *net.UDPAddr) (transport.UDPConn, error) {
	c, e := n.Net.DialUDP(network, local, remote)
	if e != nil {
		return nil, e
	}
	return &quotaUDP{UDPConn: c, q: n.quota}, nil
}
func (n *quotaNet) DialTCP(network string, local, remote *net.TCPAddr) (transport.TCPConn, error) {
	c, e := n.Net.DialTCP(network, local, remote)
	if e != nil {
		return nil, e
	}
	return &quotaTCP{TCPConn: c, q: n.quota}, nil
}
func (n *quotaNet) CreateDialer(d *net.Dialer) transport.Dialer {
	return &quotaDialer{inner: n.Net.CreateDialer(d), q: n.quota}
}
func (n *quotaNet) CreateListenConfig(c *net.ListenConfig) transport.ListenConfig {
	return &quotaListenConfig{inner: n.Net.CreateListenConfig(c), q: n.quota}
}

type quotaConn struct {
	net.Conn
	q *byteQuota
}

func (c *quotaConn) Read(b []byte) (int, error) {
	n, e := c.Conn.Read(b)
	if !c.q.take(n) {
		return 0, ErrQuota
	}
	return n, e
}
func (c *quotaConn) Write(b []byte) (int, error) {
	if !c.q.take(len(b)) {
		return 0, ErrQuota
	}
	return c.Conn.Write(b)
}

type quotaTCP struct {
	transport.TCPConn
	q *byteQuota
}

func (c *quotaTCP) Read(b []byte) (int, error) {
	n, e := c.TCPConn.Read(b)
	if !c.q.take(n) {
		return 0, ErrQuota
	}
	return n, e
}
func (c *quotaTCP) Write(b []byte) (int, error) {
	if !c.q.take(len(b)) {
		return 0, ErrQuota
	}
	return c.TCPConn.Write(b)
}
func (c *quotaTCP) ReadFrom(r io.Reader) (int64, error) { return io.Copy(struct{ io.Writer }{c}, r) }

type quotaPacket struct {
	net.PacketConn
	q *byteQuota
}

func (c *quotaPacket) ReadFrom(b []byte) (int, net.Addr, error) {
	n, a, e := c.PacketConn.ReadFrom(b)
	if !c.q.take(n) {
		return 0, a, ErrQuota
	}
	return n, a, e
}
func (c *quotaPacket) WriteTo(b []byte, a net.Addr) (int, error) {
	if !c.q.take(len(b)) {
		return 0, ErrQuota
	}
	return c.PacketConn.WriteTo(b, a)
}

type quotaUDP struct {
	transport.UDPConn
	q *byteQuota
}

func (c *quotaUDP) Read(b []byte) (int, error) {
	n, e := c.UDPConn.Read(b)
	if !c.q.take(n) {
		return 0, ErrQuota
	}
	return n, e
}
func (c *quotaUDP) ReadFrom(b []byte) (int, net.Addr, error) {
	n, a, e := c.UDPConn.ReadFrom(b)
	if !c.q.take(n) {
		return 0, a, ErrQuota
	}
	return n, a, e
}
func (c *quotaUDP) ReadFromUDP(b []byte) (int, *net.UDPAddr, error) {
	n, a, e := c.UDPConn.ReadFromUDP(b)
	if !c.q.take(n) {
		return 0, a, ErrQuota
	}
	return n, a, e
}
func (c *quotaUDP) ReadMsgUDP(b, oob []byte) (int, int, int, *net.UDPAddr, error) {
	n, o, f, a, e := c.UDPConn.ReadMsgUDP(b, oob)
	if !c.q.take(n + o) {
		return 0, 0, f, a, ErrQuota
	}
	return n, o, f, a, e
}
func (c *quotaUDP) Write(b []byte) (int, error) {
	if !c.q.take(len(b)) {
		return 0, ErrQuota
	}
	return c.UDPConn.Write(b)
}
func (c *quotaUDP) WriteTo(b []byte, a net.Addr) (int, error) {
	if !c.q.take(len(b)) {
		return 0, ErrQuota
	}
	return c.UDPConn.WriteTo(b, a)
}
func (c *quotaUDP) WriteToUDP(b []byte, a *net.UDPAddr) (int, error) {
	if !c.q.take(len(b)) {
		return 0, ErrQuota
	}
	return c.UDPConn.WriteToUDP(b, a)
}
func (c *quotaUDP) WriteMsgUDP(b, oob []byte, a *net.UDPAddr) (int, int, error) {
	if !c.q.take(len(b) + len(oob)) {
		return 0, 0, ErrQuota
	}
	return c.UDPConn.WriteMsgUDP(b, oob, a)
}

type quotaDialer struct {
	inner transport.Dialer
	q     *byteQuota
}

func (d *quotaDialer) Dial(network, address string) (net.Conn, error) {
	c, e := d.inner.Dial(network, address)
	if e != nil {
		return nil, e
	}
	return &quotaConn{Conn: c, q: d.q}, nil
}

type quotaListenConfig struct {
	inner transport.ListenConfig
	q     *byteQuota
}

func (c *quotaListenConfig) Listen(ctx context.Context, network, address string) (net.Listener, error) {
	l, e := c.inner.Listen(ctx, network, address)
	if e != nil {
		return nil, e
	}
	return &quotaListener{Listener: l, q: c.q}, nil
}
func (c *quotaListenConfig) ListenPacket(ctx context.Context, network, address string) (net.PacketConn, error) {
	p, e := c.inner.ListenPacket(ctx, network, address)
	if e != nil {
		return nil, e
	}
	return &quotaPacket{PacketConn: p, q: c.q}, nil
}

type quotaListener struct {
	net.Listener
	q *byteQuota
}

func (l *quotaListener) Accept() (net.Conn, error) {
	c, e := l.Listener.Accept()
	if e != nil {
		return nil, e
	}
	return &quotaConn{Conn: c, q: l.q}, nil
}
