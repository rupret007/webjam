package reference

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/binary"
	"errors"
	"net"
	"strings"
	"testing"
	"time"
)

func TestRelayPacketConnBindsAndExposesOnlyOneSyntheticPeer(t *testing.T) {
	t.Parallel()
	server, client := relayPair(t, RoleHost)
	bind, source := readRelayPacket(t, server)
	assertRelayPacket(t, bind, filledSession(1), RoleHost, relayKindBind, 1, 1, nil, fixedToken(2))
	if source == nil || client.LocalAddr().String() != "local" || client.PeerAddr().String() != "peer" {
		t.Fatal("relay exposed unexpected address contract")
	}
	if client.LocalAddr().Network() != "webjam-reference-v3" {
		t.Fatalf("network = %q", client.LocalAddr().Network())
	}
	other := &syntheticAddress{label: "peer"}
	if _, err := client.WriteTo([]byte("cipher"), other); !errors.Is(err, ErrUnexpectedPeer) {
		t.Fatalf("other peer error = %v", err)
	}
	if _, err := client.WriteTo(make([]byte, MaxRelayPayloadBytes+1), client.PeerAddr()); !errors.Is(err, ErrInvalidInput) {
		t.Fatalf("oversize error = %v", err)
	}
}

func TestRelayPacketConnWritesCanonicalAuthenticatedData(t *testing.T) {
	t.Parallel()
	server, client := relayPair(t, RoleHost)
	_, _ = readRelayPacket(t, server) // bind
	payload := []byte("opaque-quic-ciphertext")
	written, err := client.WriteTo(payload, client.PeerAddr())
	if err != nil || written != len(payload) {
		t.Fatalf("write = %d, %v", written, err)
	}
	packet, _ := readRelayPacket(t, server)
	assertRelayPacket(t, packet, filledSession(1), RoleHost, relayKindData, 1, 2, payload, fixedToken(2))
	if len(packet) != relayOverhead+len(payload) || len(packet) > MaxRelayDatagramBytes {
		t.Fatalf("packet bytes = %d", len(packet))
	}
}

func TestRelayPacketConnReadsOnlyAuthenticatedServiceDelivery(t *testing.T) {
	t.Parallel()
	server, client := relayPair(t, RoleHost)
	_, clientEndpoint := readRelayPacket(t, server)
	keyToken := fixedToken(2)
	rogue, err := net.ListenUDP("udp4", &net.UDPAddr{IP: net.IPv4(127, 0, 0, 1)})
	if err != nil {
		t.Fatal(err)
	}
	defer rogue.Close()
	valid := encodeTestRelay(
		filledSession(1), RoleGuest, relayKindDelivery, 1, 9, []byte("first"), keyToken,
	)
	if _, err = rogue.WriteToUDP(valid, client.conn.LocalAddr().(*net.UDPAddr)); err != nil {
		t.Fatal(err)
	}
	badMAC := append([]byte(nil), valid...)
	badMAC[len(badMAC)-1] ^= 1
	if _, err = server.WriteToUDP(badMAC, clientEndpoint); err != nil {
		t.Fatal(err)
	}
	wrongRole := encodeTestRelay(
		filledSession(1), RoleHost, relayKindDelivery, 1, 10, []byte("wrong"), keyToken,
	)
	if _, err = server.WriteToUDP(wrongRole, clientEndpoint); err != nil {
		t.Fatal(err)
	}
	wrongSession := encodeTestRelay(
		filledSession(9), RoleGuest, relayKindDelivery, 1, 11, []byte("wrong"), keyToken,
	)
	if _, err = server.WriteToUDP(wrongSession, clientEndpoint); err != nil {
		t.Fatal(err)
	}
	if _, err = server.WriteToUDP(valid, clientEndpoint); err != nil {
		t.Fatal(err)
	}
	if err = client.SetReadDeadline(time.Now().Add(time.Second)); err != nil {
		t.Fatal(err)
	}
	buffer := make([]byte, 64)
	n, peer, err := client.ReadFrom(buffer)
	if err != nil || string(buffer[:n]) != "first" || peer != client.PeerAddr() {
		t.Fatalf("read = %q, %v, %v", buffer[:n], peer, err)
	}
}

func TestRelayPacketConnDropsReplayAndAllowsBoundedReordering(t *testing.T) {
	t.Parallel()
	server, client := relayPair(t, RoleGuest)
	_, endpoint := readRelayPacket(t, server)
	token := fixedToken(2)
	for _, packet := range [][]byte{
		encodeTestRelay(filledSession(1), RoleHost, relayKindDelivery, 1, 12, []byte("twelve"), token),
		encodeTestRelay(filledSession(1), RoleHost, relayKindDelivery, 1, 12, []byte("replay"), token),
		encodeTestRelay(filledSession(1), RoleHost, relayKindDelivery, 1, 11, []byte("eleven"), token),
	} {
		if _, err := server.WriteToUDP(packet, endpoint); err != nil {
			t.Fatal(err)
		}
	}
	if err := client.SetReadDeadline(time.Now().Add(time.Second)); err != nil {
		t.Fatal(err)
	}
	buffer := make([]byte, 32)
	n, _, err := client.ReadFrom(buffer)
	if err != nil || string(buffer[:n]) != "twelve" {
		t.Fatalf("first read = %q, %v", buffer[:n], err)
	}
	n, _, err = client.ReadFrom(buffer)
	if err != nil || string(buffer[:n]) != "eleven" {
		t.Fatalf("reordered read = %q, %v", buffer[:n], err)
	}
}

func TestRelayPacketConnDeadlineAndCleanupAreCategorical(t *testing.T) {
	t.Parallel()
	server, client := relayPair(t, RoleHost)
	_, _ = readRelayPacket(t, server)
	if err := client.SetReadDeadline(time.Now().Add(20 * time.Millisecond)); err != nil {
		t.Fatal(err)
	}
	_, _, err := client.ReadFrom(make([]byte, 64))
	var timeout net.Error
	if !errors.As(err, &timeout) || !timeout.Timeout() || strings.Contains(err.Error(), "127.0.0.1") {
		t.Fatalf("deadline error = %#v", err)
	}
	if err = client.Close(); err != nil {
		t.Fatal(err)
	}
	if err = client.Close(); err != nil {
		t.Fatal(err)
	}
	if client.key != ([32]byte{}) || client.session != (SessionID{}) {
		t.Fatal("relay cleanup retained key or session")
	}
	if _, err = client.WriteTo([]byte("cipher"), client.PeerAddr()); !errors.Is(err, ErrClosed) {
		t.Fatalf("closed write error = %v", err)
	}
}

func TestRelayReplayWindowIsFixedAndRejectsDuplicatesAndOldPackets(t *testing.T) {
	t.Parallel()
	var window relayReplayWindow
	if window.accept(0) {
		t.Fatal("window accepted zero sequence")
	}
	if !window.accept(10) || !window.accept(12) || !window.accept(11) {
		t.Fatal("window rejected valid reordered sequences")
	}
	if window.accept(11) || !window.accept(80) || window.accept(10) {
		t.Fatal("window accepted duplicate or old sequence")
	}
}

func relayPair(t *testing.T, role Role) (*net.UDPConn, *RelayPacketConn) {
	t.Helper()
	server, err := net.ListenUDP("udp4", &net.UDPAddr{IP: net.IPv4(127, 0, 0, 1)})
	if err != nil {
		t.Fatal(err)
	}
	clientSocket, err := net.ListenUDP("udp4", &net.UDPAddr{IP: net.IPv4(127, 0, 0, 1)})
	if err != nil {
		server.Close()
		t.Fatal(err)
	}
	client, err := newRelayPacketConn(
		clientSocket, server.LocalAddr().(*net.UDPAddr), filledSession(1), role, fixedToken(2), 1,
	)
	if err != nil {
		server.Close()
		clientSocket.Close()
		t.Fatal(err)
	}
	t.Cleanup(func() {
		_ = client.Close()
		_ = server.Close()
	})
	return server, client
}

func readRelayPacket(t *testing.T, server *net.UDPConn) ([]byte, *net.UDPAddr) {
	t.Helper()
	if err := server.SetReadDeadline(time.Now().Add(time.Second)); err != nil {
		t.Fatal(err)
	}
	buffer := make([]byte, MaxRelayDatagramBytes+1)
	n, source, err := server.ReadFromUDP(buffer)
	if err != nil {
		t.Fatal(err)
	}
	return append([]byte(nil), buffer[:n]...), source
}

func assertRelayPacket(
	t *testing.T,
	packet []byte,
	session SessionID,
	role Role,
	kind byte,
	generation uint32,
	sequence uint64,
	payload []byte,
	token *RoleToken,
) {
	t.Helper()
	if len(packet) != relayOverhead+len(payload) || string(packet[:4]) != relayMagic ||
		packet[4] != ProtocolVersion || Role(packet[5]) != role || packet[6] != kind || packet[7] != 0 ||
		string(packet[8:40]) != string(session[:]) || binary.BigEndian.Uint32(packet[40:44]) != generation ||
		binary.BigEndian.Uint64(packet[44:52]) != sequence ||
		int(binary.BigEndian.Uint16(packet[52:54])) != len(payload) ||
		string(packet[54:54+len(payload)]) != string(payload) {
		t.Fatal("relay packet was not canonical")
	}
	key := deriveRelayKey(token)
	mac := hmac.New(sha256.New, key[:])
	_, _ = mac.Write(packet[:len(packet)-relayTagSize])
	if !hmac.Equal(mac.Sum(nil)[:relayTagSize], packet[len(packet)-relayTagSize:]) {
		t.Fatal("relay packet MAC failed")
	}
}

func encodeTestRelay(
	session SessionID,
	role Role,
	kind byte,
	generation uint32,
	sequence uint64,
	payload []byte,
	token *RoleToken,
) []byte {
	temporary := &RelayPacketConn{session: session, role: role, generation: generation}
	temporary.key = deriveRelayKey(token)
	return temporary.encode(kind, sequence, payload)
}
