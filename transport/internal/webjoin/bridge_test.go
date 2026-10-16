package webjoin

import (
	"context"
	"net"
	"testing"
	"time"

	"github.com/pion/rtp"
)

func TestUDPBridgeAuthenticatesAndPreservesOpus(t *testing.T) {
	server, err := net.ListenUDP("udp4", &net.UDPAddr{IP: net.IPv4(127, 0, 0, 1)})
	if err != nil {
		t.Fatal(err)
	}
	defer server.Close()
	token := [32]byte{77}
	result := make(chan error, 1)
	go func() {
		buffer := make([]byte, 1500)
		_ = server.SetDeadline(time.Now().Add(3 * time.Second))
		n, peer, err := server.ReadFromUDP(buffer)
		if err != nil {
			result <- err
			return
		}
		if n != 37 || string(buffer[:5]) != "WJWB1" || buffer[5] != 77 {
			result <- ErrBridge
			return
		}
		if _, err = server.WriteToUDP(append([]byte("WJWA1"), token[:]...), peer); err != nil {
			result <- err
			return
		}
		n, _, err = server.ReadFromUDP(buffer)
		if err != nil {
			result <- err
			return
		}
		var packet rtp.Packet
		if packet.Unmarshal(buffer[:n]) != nil || packet.PayloadType != 111 || packet.SequenceNumber != 20 || packet.Timestamp != 960 || string(packet.Payload) != "opus" {
			result <- ErrBridge
			return
		}
		_, err = server.WriteToUDP(buffer[:n], peer)
		if err != nil {
			result <- err
			return
		}
		n, _, err = server.ReadFromUDP(buffer)
		if err != nil {
			result <- err
			return
		}
		if n != 37 || string(buffer[:5]) != "WJWC1" {
			result <- ErrBridge
			return
		}
		result <- nil
	}()
	bridge, err := NewUDPBridge(context.Background(), uint16(server.LocalAddr().(*net.UDPAddr).Port), token)
	if err != nil {
		t.Fatal(err)
	}
	if err = bridge.WriteOpus(context.Background(), Packet{Payload: []byte("opus"), SequenceNumber: 20, Timestamp: 960}); err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	packet, err := bridge.ReadOpus(ctx)
	if err != nil || packet.SequenceNumber != 20 || string(packet.Payload) != "opus" {
		t.Fatalf("RTP roundtrip: %v %+v", err, packet)
	}
	_ = bridge.Close()
	if err = <-result; err != nil {
		t.Fatal(err)
	}
}
func TestUDPBridgeRejectsWrongToken(t *testing.T) {
	server, err := net.ListenUDP("udp4", &net.UDPAddr{IP: net.IPv4(127, 0, 0, 1)})
	if err != nil {
		t.Fatal(err)
	}
	defer server.Close()
	go func() {
		buffer := make([]byte, 100)
		n, peer, err := server.ReadFromUDP(buffer)
		if err == nil && n == 37 {
			buffer[4] = 'A'
			buffer[5] ^= 1
			_, _ = server.WriteToUDP(buffer[:n], peer)
		}
	}()
	bridge, err := NewUDPBridge(context.Background(), uint16(server.LocalAddr().(*net.UDPAddr).Port), [32]byte{77})
	if err != nil {
		t.Fatal(err)
	}
	defer bridge.Close()
	if err = bridge.WriteOpus(context.Background(), Packet{Payload: []byte("opus")}); err != ErrBridge {
		t.Fatalf("wrong token accepted: %v", err)
	}
}
func TestUDPBridgeSendsNothingBeforeEnrollment(t *testing.T) {
	server, err := net.ListenUDP("udp4", &net.UDPAddr{IP: net.IPv4(127, 0, 0, 1)})
	if err != nil {
		t.Fatal(err)
	}
	defer server.Close()
	bridge, err := NewUDPBridge(context.Background(), uint16(server.LocalAddr().(*net.UDPAddr).Port), [32]byte{1})
	if err != nil {
		t.Fatal(err)
	}
	_ = server.SetReadDeadline(time.Now().Add(50 * time.Millisecond))
	buffer := make([]byte, 100)
	if n, _, err := server.ReadFromUDP(buffer); err == nil || n != 0 {
		t.Fatal("bridge started before enrollment")
	}
	_ = bridge.Close()
	_ = server.SetReadDeadline(time.Now().Add(50 * time.Millisecond))
	if n, _, err := server.ReadFromUDP(buffer); err == nil || n != 0 {
		t.Fatal("unused bridge close sent a token")
	}
}
func TestUDPBridgeCloseDuringConcurrentHandshake(t *testing.T) {
	server, err := net.ListenUDP("udp4", &net.UDPAddr{IP: net.IPv4(127, 0, 0, 1)})
	if err != nil {
		t.Fatal(err)
	}
	defer server.Close()
	bridge, err := NewUDPBridge(context.Background(), uint16(server.LocalAddr().(*net.UDPAddr).Port), [32]byte{2})
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	results := make(chan error, 2)
	go func() { results <- bridge.WriteOpus(ctx, Packet{Payload: []byte("opus")}) }()
	go func() { _, err := bridge.ReadOpus(ctx); results <- err }()
	_ = server.SetReadDeadline(time.Now().Add(time.Second))
	buffer := make([]byte, 100)
	if _, _, err = server.ReadFromUDP(buffer); err != nil {
		t.Fatal(err)
	}
	_ = bridge.Close()
	for n := 0; n < 2; n++ {
		select {
		case err := <-results:
			if err == nil {
				t.Fatal("closed unauthenticated bridge returned success")
			}
		case <-time.After(time.Second):
			t.Fatal("concurrent close stuck")
		}
	}
}
