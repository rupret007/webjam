package icequic

import (
	"errors"
	"net"
	"testing"
)

func TestFixedPeerPacketConnRejectsOtherAddress(t *testing.T) {
	t.Parallel()
	left, right := net.Pipe()
	defer left.Close()
	defer right.Close()
	local := &net.UDPAddr{IP: net.IPv4(127, 0, 0, 1), Port: 4000}
	peer := &net.UDPAddr{IP: net.IPv4(127, 0, 0, 1), Port: 4001}
	packetConn, err := NewFixedPeerPacketConn(left, local, peer)
	if err != nil {
		t.Fatal(err)
	}
	_, err = packetConn.WriteTo([]byte{1}, &net.UDPAddr{IP: net.IPv4(127, 0, 0, 1), Port: 4999})
	if !errors.Is(err, ErrUnexpectedPeer) {
		t.Fatalf("error = %v", err)
	}
}
