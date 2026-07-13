package loopback

import (
	"bytes"
	"context"
	"errors"
	"net"
	"testing"
	"time"
)

func TestGuestProxyPreservesBoundariesAndLocksPeer(t *testing.T) {
	t.Parallel()
	proxy, err := NewGuestProxy()
	if err != nil {
		t.Fatal(err)
	}
	defer proxy.Close()
	client, err := net.DialUDP("udp4", nil, &net.UDPAddr{IP: net.IPv4(127, 0, 0, 1), Port: proxy.LocalPort()})
	if err != nil {
		t.Fatal(err)
	}
	defer client.Close()

	want := []byte{1, 2, 3, 4}
	if _, err = client.Write(want); err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	got, err := proxy.ReadDatagram(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(got, want) {
		t.Fatalf("got %v, want %v", got, want)
	}
	reply := []byte{9, 8, 7}
	if err = proxy.WriteDatagram(ctx, reply); err != nil {
		t.Fatal(err)
	}
	buffer := make([]byte, 8)
	if err = client.SetReadDeadline(time.Now().Add(time.Second)); err != nil {
		t.Fatal(err)
	}
	n, err := client.Read(buffer)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(buffer[:n], reply) {
		t.Fatalf("reply = %v", buffer[:n])
	}
}

func TestHostProxyUsesDistinctSourcePorts(t *testing.T) {
	t.Parallel()
	server, err := net.ListenUDP("udp4", &net.UDPAddr{IP: net.IPv4(127, 0, 0, 1), Port: 0})
	if err != nil {
		t.Fatal(err)
	}
	defer server.Close()
	target := server.LocalAddr().(*net.UDPAddr)
	first, err := NewHostProxy(target)
	if err != nil {
		t.Fatal(err)
	}
	defer first.Close()
	second, err := NewHostProxy(target)
	if err != nil {
		t.Fatal(err)
	}
	defer second.Close()
	if first.LocalPort() == second.LocalPort() {
		t.Fatalf("host proxies shared source port %d", first.LocalPort())
	}

	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	if err = first.WriteDatagram(ctx, []byte("one")); err != nil {
		t.Fatal(err)
	}
	buffer := make([]byte, 16)
	n, remote, err := server.ReadFromUDP(buffer)
	if err != nil {
		t.Fatal(err)
	}
	if remote.Port != first.LocalPort() || string(buffer[:n]) != "one" {
		t.Fatalf("first source=%v payload=%q", remote, buffer[:n])
	}
	if _, err = server.WriteToUDP([]byte("reply"), remote); err != nil {
		t.Fatal(err)
	}
	reply, err := first.ReadDatagram(ctx)
	if err != nil || string(reply) != "reply" {
		t.Fatalf("reply=%q err=%v", reply, err)
	}
}

func TestProxyRejectsPublicTargetAndOversize(t *testing.T) {
	t.Parallel()
	if _, err := NewHostProxy(&net.UDPAddr{IP: net.ParseIP("192.0.2.1"), Port: 22124}); !errors.Is(err, ErrNotLoopback) {
		t.Fatalf("public target error = %v", err)
	}
	proxy, err := NewGuestProxy()
	if err != nil {
		t.Fatal(err)
	}
	defer proxy.Close()
	if err = proxy.WriteDatagram(context.Background(), make([]byte, 1025)); !errors.Is(err, ErrOversize) {
		t.Fatalf("oversize error = %v", err)
	}
}
