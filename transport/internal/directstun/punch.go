package directstun

import (
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"net"
	"sync"
	"time"
)

const (
	probeMagic         = "WJHP"
	probeVersion       = 1
	probeSize          = 38
	probeInterval      = 50 * time.Millisecond
	RoleHost      byte = 1
	RoleGuest     byte = 2
)

type inbound struct {
	n    int
	addr *net.UDPAddr
	buf  []byte
	err  error
}

// Mux is a PacketConn that runs ICE-lite style UDP connectivity checks on the
// same socket QUIC later uses. Both sides probe advertised host and srflx
// candidates at once; the first authenticated source becomes the nominated
// peer-reflexive path.
type Mux struct {
	udp      *net.UDPConn
	password []byte
	nonce    [16]byte
	role     byte

	mu        sync.Mutex
	remotes   []*net.UDPAddr
	nominated *net.UDPAddr
	closed    chan struct{}
	closeOnce sync.Once
	incoming  chan inbound
}

func NewMux(udp *net.UDPConn, password string, nonce [16]byte, role byte) *Mux {
	mux := &Mux{
		udp:      udp,
		password: append([]byte(nil), password...),
		nonce:    nonce,
		role:     role,
		closed:   make(chan struct{}),
		incoming: make(chan inbound, 16),
	}
	go mux.probeLoop()
	go mux.readLoop()
	return mux
}

func (m *Mux) SetRemotes(addrs []*net.UDPAddr) {
	if m == nil {
		return
	}
	copied := make([]*net.UDPAddr, 0, len(addrs))
	for _, addr := range addrs {
		if addr == nil || addr.Port < 1 {
			continue
		}
		copied = append(copied, cloneUDPAddr(addr))
	}
	m.mu.Lock()
	m.remotes = copied
	m.mu.Unlock()
	m.sendProbes()
}

func (m *Mux) Nominated() *net.UDPAddr {
	if m == nil {
		return nil
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	return cloneUDPAddr(m.nominated)
}

func (m *Mux) WaitNominated(ctx context.Context) (*net.UDPAddr, error) {
	if m == nil || ctx == nil {
		return nil, ErrUnreachable
	}
	ticker := time.NewTicker(10 * time.Millisecond)
	defer ticker.Stop()
	for {
		if addr := m.Nominated(); addr != nil {
			return addr, nil
		}
		select {
		case <-ctx.Done():
			return nil, ErrUnreachable
		case <-m.closed:
			return nil, ErrUnreachable
		case <-ticker.C:
		}
	}
}

func (m *Mux) ReadFrom(payload []byte) (int, net.Addr, error) {
	select {
	case <-m.closed:
		return 0, nil, net.ErrClosed
	case packet, ok := <-m.incoming:
		if !ok {
			return 0, nil, net.ErrClosed
		}
		if packet.err != nil {
			return 0, packet.addr, packet.err
		}
		n := copy(payload, packet.buf[:packet.n])
		return n, packet.addr, nil
	}
}

func (m *Mux) WriteTo(payload []byte, peer net.Addr) (int, error) {
	return m.udp.WriteTo(payload, peer)
}

func (m *Mux) Close() error {
	if m == nil {
		return nil
	}
	m.closeOnce.Do(func() { close(m.closed) })
	return m.udp.Close()
}

func (m *Mux) LocalAddr() net.Addr { return m.udp.LocalAddr() }

// QUIC may set PacketConn deadlines. Those must not stop the UDP read loop
// that still has to receive ICE-lite probes, so read deadlines are ignored.
func (m *Mux) SetDeadline(t time.Time) error      { return m.udp.SetWriteDeadline(t) }
func (m *Mux) SetReadDeadline(time.Time) error    { return nil }
func (m *Mux) SetWriteDeadline(t time.Time) error { return m.udp.SetWriteDeadline(t) }

func (m *Mux) readLoop() {
	defer close(m.incoming)
	buffer := make([]byte, 1500)
	for {
		n, addr, err := m.udp.ReadFromUDP(buffer)
		if err != nil {
			if ne, ok := err.(net.Error); ok && ne.Timeout() {
				continue
			}
			select {
			case <-m.closed:
				return
			case m.incoming <- inbound{err: err}:
				return
			}
		}
		if n >= probeSize && isProbe(buffer[:n]) {
			m.handleProbe(buffer[:n], addr)
			continue
		}
		copied := append([]byte(nil), buffer[:n]...)
		select {
		case <-m.closed:
			return
		case m.incoming <- inbound{n: n, addr: cloneUDPAddr(addr), buf: copied}:
		default:
			// QUIC retransmits; never block probe handling behind a full queue.
		}
	}
}

func (m *Mux) probeLoop() {
	ticker := time.NewTicker(probeInterval)
	defer ticker.Stop()
	for {
		select {
		case <-m.closed:
			return
		case <-ticker.C:
			m.sendProbes()
		}
	}
}

func (m *Mux) sendProbes() {
	m.mu.Lock()
	remotes := m.remotes
	if nominated := m.nominated; nominated != nil {
		remotes = []*net.UDPAddr{nominated}
	}
	m.mu.Unlock()
	if len(remotes) == 0 {
		return
	}
	packet := m.encodeProbe(m.role)
	for _, remote := range remotes {
		_, _ = m.udp.WriteToUDP(packet, remote)
	}
}

func (m *Mux) handleProbe(packet []byte, source *net.UDPAddr) {
	if source == nil || !m.validProbe(packet) || packet[5] == m.role {
		return
	}
	m.mu.Lock()
	if m.nominated == nil {
		m.nominated = cloneUDPAddr(source)
	}
	m.mu.Unlock()
	_, _ = m.udp.WriteToUDP(m.encodeProbe(m.role), source)
}

func (m *Mux) encodeProbe(role byte) []byte {
	packet := make([]byte, probeSize)
	copy(packet[0:4], probeMagic)
	packet[4] = probeVersion
	packet[5] = role
	copy(packet[6:22], m.nonce[:])
	copy(packet[22:], m.mac(packet[:22]))
	return packet
}

func (m *Mux) validProbe(packet []byte) bool {
	if len(packet) < probeSize || !isProbe(packet) || packet[4] != probeVersion {
		return false
	}
	if packet[5] != RoleHost && packet[5] != RoleGuest {
		return false
	}
	if !hmac.Equal(packet[6:22], m.nonce[:]) {
		return false
	}
	return hmac.Equal(packet[22:probeSize], m.mac(packet[:22]))
}

func (m *Mux) mac(message []byte) []byte {
	writer := hmac.New(sha256.New, m.password)
	_, _ = writer.Write(message)
	sum := writer.Sum(nil)
	return sum[:16]
}

func isProbe(packet []byte) bool {
	return len(packet) >= 4 && string(packet[:4]) == probeMagic
}

func cloneUDPAddr(addr *net.UDPAddr) *net.UDPAddr {
	if addr == nil {
		return nil
	}
	return &net.UDPAddr{IP: append(net.IP(nil), addr.IP...), Port: addr.Port, Zone: addr.Zone}
}

var _ net.PacketConn = (*Mux)(nil)
