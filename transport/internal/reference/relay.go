package reference

import (
	"crypto/hmac"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/binary"
	"errors"
	"io"
	"net"
	"sync"
	"time"
)

const (
	relayMagic      = "WJR3"
	relayHeaderSize = 54
	relayTagSize    = 16
	relayOverhead   = relayHeaderSize + relayTagSize
	relayKeyDomain  = "webjam-reference-relay-v3"

	relayKindBind      = 1
	relayKindData      = 2
	relayKindKeepalive = 3
	relayKindDelivery  = 4
)

type syntheticAddress struct{ label string }

func (a *syntheticAddress) Network() string { return "webjam-reference-v3" }
func (a *syntheticAddress) String() string  { return a.label }

type RelayPacketConn struct {
	conn       *net.UDPConn
	relay      *net.UDPAddr
	local      *syntheticAddress
	peer       *syntheticAddress
	session    SessionID
	role       Role
	generation uint32
	key        [32]byte

	writeMu  sync.Mutex
	sequence uint64
	readMu   sync.Mutex
	replay   relayReplayWindow
	closeMu  sync.Mutex
	closed   bool
}

type relayReplayWindow struct {
	highest     uint64
	mask        uint64
	initialized bool
}

type relayTimeoutError struct{}

func (relayTimeoutError) Error() string   { return "reference relay deadline exceeded" }
func (relayTimeoutError) Timeout() bool   { return true }
func (relayTimeoutError) Temporary() bool { return true }
func (relayTimeoutError) Unwrap() error   { return ErrRelayUnavailable }

func OpenRelayLocal(
	session SessionID, role Role, token *RoleToken, generation uint32,
) (*RelayPacketConn, error) {
	if !nonzero(session) || !role.valid() || !token.valid() || generation == 0 {
		return nil, ErrInvalidInput
	}
	conn, err := net.ListenUDP("udp4", &net.UDPAddr{IP: net.IPv4(127, 0, 0, 1)})
	if err != nil {
		return nil, ErrRelayUnavailable
	}
	relay := &net.UDPAddr{IP: net.IPv4(127, 0, 0, 1), Port: 47132}
	packetConn, err := newRelayPacketConn(conn, relay, session, role, token, generation)
	if err != nil {
		_ = conn.Close()
		return nil, err
	}
	return packetConn, nil
}

func newRelayPacketConn(
	conn *net.UDPConn,
	relay *net.UDPAddr,
	session SessionID,
	role Role,
	token *RoleToken,
	generation uint32,
) (*RelayPacketConn, error) {
	if conn == nil || relay == nil || relay.Port < 1 || relay.Port > 65_535 ||
		!relay.IP.IsLoopback() || !nonzero(session) || !role.valid() ||
		!token.valid() || generation == 0 {
		return nil, ErrInvalidInput
	}
	packetConn := &RelayPacketConn{
		conn: conn, relay: cloneUDPAddress(relay),
		local: &syntheticAddress{label: "local"}, peer: &syntheticAddress{label: "peer"},
		session: session, role: role, generation: generation, sequence: 1,
	}
	packetConn.key = deriveRelayKey(token)
	bind := packetConn.encode(relayKindBind, packetConn.sequence, nil)
	if n, err := conn.WriteToUDP(bind, packetConn.relay); err != nil || n != len(bind) {
		clear(packetConn.key[:])
		return nil, ErrRelayUnavailable
	}
	return packetConn, nil
}

func (c *RelayPacketConn) PeerAddr() net.Addr {
	if c == nil {
		return nil
	}
	return c.peer
}

func (c *RelayPacketConn) ReadFrom(payload []byte) (int, net.Addr, error) {
	if c == nil || len(payload) == 0 {
		return 0, nil, ErrInvalidInput
	}
	buffer := make([]byte, MaxRelayDatagramBytes+1)
	for {
		n, source, err := c.conn.ReadFromUDP(buffer)
		if err != nil {
			return 0, nil, c.safeNetworkError(err)
		}
		if n > MaxRelayDatagramBytes || !sameUDPAddress(source, c.relay) {
			continue
		}
		c.readMu.Lock()
		decoded, sequence, ok := c.decodeDelivery(buffer[:n])
		if ok {
			ok = c.replay.accept(sequence)
		}
		if !ok {
			c.readMu.Unlock()
			continue
		}
		if len(decoded) > len(payload) {
			c.readMu.Unlock()
			return 0, nil, ErrRelayFrame
		}
		copy(payload, decoded)
		c.readMu.Unlock()
		return len(decoded), c.peer, nil
	}
}

func (c *RelayPacketConn) WriteTo(payload []byte, peer net.Addr) (int, error) {
	if c == nil || peer == nil || peer != c.peer || len(payload) > MaxRelayPayloadBytes {
		if c != nil && peer != c.peer {
			return 0, ErrUnexpectedPeer
		}
		return 0, ErrInvalidInput
	}
	c.writeMu.Lock()
	defer c.writeMu.Unlock()
	if c.isClosed() {
		return 0, ErrClosed
	}
	if c.sequence >= uint64(^uint64(0)>>1) {
		return 0, ErrRelayFrame
	}
	c.sequence++
	encoded := c.encode(relayKindData, c.sequence, payload)
	n, err := c.conn.WriteToUDP(encoded, c.relay)
	if err != nil || n != len(encoded) {
		return 0, c.safeNetworkError(err)
	}
	return len(payload), nil
}

func (c *RelayPacketConn) Close() error {
	if c == nil {
		return nil
	}
	c.writeMu.Lock()
	defer c.writeMu.Unlock()
	c.readMu.Lock()
	defer c.readMu.Unlock()
	c.closeMu.Lock()
	defer c.closeMu.Unlock()
	if c.closed {
		return nil
	}
	c.closed = true
	clear(c.key[:])
	clear(c.session[:])
	if err := c.conn.Close(); err != nil {
		return ErrRelayUnavailable
	}
	return nil
}

func (c *RelayPacketConn) LocalAddr() net.Addr {
	if c == nil {
		return nil
	}
	return c.local
}

func (c *RelayPacketConn) SetDeadline(deadline time.Time) error {
	if c == nil {
		return ErrInvalidInput
	}
	if err := c.conn.SetDeadline(deadline); err != nil {
		return ErrRelayUnavailable
	}
	return nil
}

func (c *RelayPacketConn) SetReadDeadline(deadline time.Time) error {
	if c == nil {
		return ErrInvalidInput
	}
	if err := c.conn.SetReadDeadline(deadline); err != nil {
		return ErrRelayUnavailable
	}
	return nil
}

func (c *RelayPacketConn) SetWriteDeadline(deadline time.Time) error {
	if c == nil {
		return ErrInvalidInput
	}
	if err := c.conn.SetWriteDeadline(deadline); err != nil {
		return ErrRelayUnavailable
	}
	return nil
}

// SetReadBuffer and SetWriteBuffer let quic-go tune the owned UDP socket
// without exposing syscall.RawConn, which would bypass the single-peer API.
func (c *RelayPacketConn) SetReadBuffer(bytes int) error {
	if c == nil || bytes <= 0 {
		return ErrInvalidInput
	}
	if err := c.conn.SetReadBuffer(bytes); err != nil {
		return ErrRelayUnavailable
	}
	return nil
}

func (c *RelayPacketConn) SetWriteBuffer(bytes int) error {
	if c == nil || bytes <= 0 {
		return ErrInvalidInput
	}
	if err := c.conn.SetWriteBuffer(bytes); err != nil {
		return ErrRelayUnavailable
	}
	return nil
}

func (c *RelayPacketConn) encode(kind byte, sequence uint64, payload []byte) []byte {
	encoded := make([]byte, relayOverhead+len(payload))
	copy(encoded[:4], relayMagic)
	encoded[4] = ProtocolVersion
	encoded[5] = byte(c.role)
	encoded[6] = kind
	encoded[7] = 0
	copy(encoded[8:40], c.session[:])
	binary.BigEndian.PutUint32(encoded[40:44], c.generation)
	binary.BigEndian.PutUint64(encoded[44:52], sequence)
	binary.BigEndian.PutUint16(encoded[52:54], uint16(len(payload)))
	copy(encoded[54:54+len(payload)], payload)
	mac := hmac.New(sha256.New, c.key[:])
	_, _ = mac.Write(encoded[:len(encoded)-relayTagSize])
	copy(encoded[len(encoded)-relayTagSize:], mac.Sum(nil)[:relayTagSize])
	return encoded
}

func (c *RelayPacketConn) decodeDelivery(encoded []byte) ([]byte, uint64, bool) {
	if len(encoded) < relayOverhead || len(encoded) > MaxRelayDatagramBytes ||
		string(encoded[:4]) != relayMagic || encoded[4] != ProtocolVersion ||
		Role(encoded[5]) != c.role.opposite() || encoded[6] != relayKindDelivery || encoded[7] != 0 ||
		subtle.ConstantTimeCompare(encoded[8:40], c.session[:]) != 1 ||
		binary.BigEndian.Uint32(encoded[40:44]) != c.generation {
		return nil, 0, false
	}
	sequence := binary.BigEndian.Uint64(encoded[44:52])
	payloadBytes := int(binary.BigEndian.Uint16(encoded[52:54]))
	if sequence == 0 || sequence > uint64(^uint64(0)>>1) || payloadBytes > MaxRelayPayloadBytes ||
		payloadBytes != len(encoded)-relayOverhead {
		return nil, 0, false
	}
	mac := hmac.New(sha256.New, c.key[:])
	_, _ = mac.Write(encoded[:len(encoded)-relayTagSize])
	if subtle.ConstantTimeCompare(mac.Sum(nil)[:relayTagSize], encoded[len(encoded)-relayTagSize:]) != 1 {
		return nil, 0, false
	}
	return encoded[relayHeaderSize : len(encoded)-relayTagSize], sequence, true
}

func (w *relayReplayWindow) accept(sequence uint64) bool {
	if sequence == 0 || sequence > uint64(^uint64(0)>>1) {
		return false
	}
	if !w.initialized {
		w.highest, w.mask, w.initialized = sequence, 1, true
		return true
	}
	if sequence > w.highest {
		shift := sequence - w.highest
		if shift >= 64 {
			w.mask = 1
		} else {
			w.mask = (w.mask << shift) | 1
		}
		w.highest = sequence
		return true
	}
	delta := w.highest - sequence
	if delta >= 64 || w.mask&(uint64(1)<<delta) != 0 {
		return false
	}
	w.mask |= uint64(1) << delta
	return true
}

func (c *RelayPacketConn) isClosed() bool {
	c.closeMu.Lock()
	defer c.closeMu.Unlock()
	return c.closed
}

func (c *RelayPacketConn) safeNetworkError(err error) error {
	if c.isClosed() || errors.Is(err, net.ErrClosed) {
		return ErrClosed
	}
	var networkError net.Error
	if errors.As(err, &networkError) && networkError.Timeout() {
		return relayTimeoutError{}
	}
	return ErrRelayUnavailable
}

func deriveRelayKey(token *RoleToken) [32]byte {
	mac := hmac.New(sha256.New, token.value[:])
	_, _ = io.WriteString(mac, relayKeyDomain)
	var key [32]byte
	copy(key[:], mac.Sum(nil))
	return key
}

func cloneUDPAddress(address *net.UDPAddr) *net.UDPAddr {
	clone := &net.UDPAddr{Port: address.Port, Zone: address.Zone}
	clone.IP = append(net.IP(nil), address.IP...)
	return clone
}

func sameUDPAddress(left, right *net.UDPAddr) bool {
	return left != nil && right != nil && left.Port == right.Port && left.Zone == right.Zone &&
		left.IP.Equal(right.IP)
}

var _ net.PacketConn = (*RelayPacketConn)(nil)
