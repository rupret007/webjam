package directstun

import (
	"context"
	"net"
	"net/netip"
	"testing"
	"time"
)

func TestParseDialTargetAcceptsPrivateHostAndRejectsLoopback(t *testing.T) {
	t.Parallel()
	host, err := hostCandidate(netip.MustParseAddr("192.168.1.20"), 22124)
	if err != nil {
		t.Fatal(err)
	}
	target, err := ParseDialTarget(host)
	if err != nil || target.Port != 22124 || target.IP.String() != "192.168.1.20" {
		t.Fatalf("host candidate = %v, %v", target, err)
	}

	loopback, err := LoopbackCandidate(4432)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = ParseDialTarget(loopback); err == nil {
		t.Fatal("loopback host candidate must be rejected in production parsing")
	}
	parsed, err := ParseLoopbackDialTarget(loopback)
	if err != nil || parsed.Port != 4432 {
		t.Fatalf("loopback test parser = %v, %v", parsed, err)
	}
}

func TestParseDialTargetRejectsPublicHostCandidate(t *testing.T) {
	t.Parallel()
	publicHost, err := hostCandidate(netip.MustParseAddr("8.8.8.8"), 3478)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = ParseDialTarget(publicHost); err == nil {
		t.Fatal("public host candidate must be rejected")
	}
}

func TestParseDialTargetKeepsSTUNMappedTestNet(t *testing.T) {
	t.Parallel()
	encoded, err := srflxCandidate(netip.MustParseAddrPort("203.0.113.10:41234"), 22124)
	if err != nil {
		t.Fatal(err)
	}
	target, err := ParseDialTarget(encoded)
	if err != nil || target.IP.String() != "203.0.113.10" || target.Port != 41234 {
		t.Fatalf("TEST-NET srflx = %v, %v", target, err)
	}
	if publicUnicastIPv4(netip.MustParseAddr("203.0.113.10")) {
		t.Fatal("real-world public check must still reject TEST-NET-3")
	}
}

func TestStunServerListOverrideIsHarnessOnly(t *testing.T) {
	t.Setenv(testHarnessEnv, "")
	t.Setenv(testSTUNEnv, "203.0.113.1:3478")
	if got := stunServerList(); len(got) != 2 || got[0] != stunServers[0] {
		t.Fatalf("override without harness = %v", got)
	}

	t.Setenv(testHarnessEnv, "1")
	got := stunServerList()
	if len(got) != 1 || got[0] != "203.0.113.1:3478" {
		t.Fatalf("harness override = %v", got)
	}

	t.Setenv(testSTUNEnv, "not-a-host-port")
	if got = stunServerList(); len(got) != 2 || got[0] != stunServers[0] {
		t.Fatalf("invalid override = %v", got)
	}
}

func TestPunchNominatesLoopbackPair(t *testing.T) {
	left, err := net.ListenUDP("udp4", &net.UDPAddr{IP: net.IPv4(127, 0, 0, 1), Port: 0})
	if err != nil {
		t.Fatal(err)
	}
	right, err := net.ListenUDP("udp4", &net.UDPAddr{IP: net.IPv4(127, 0, 0, 1), Port: 0})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		_ = left.Close()
		_ = right.Close()
	})
	var nonce [16]byte
	nonce[0] = 7
	hostMux := NewMux(left, "ice-password", nonce, RoleHost)
	guestMux := NewMux(right, "ice-password", nonce, RoleGuest)
	t.Cleanup(func() {
		_ = hostMux.Close()
		_ = guestMux.Close()
	})
	hostMux.SetRemotes([]*net.UDPAddr{right.LocalAddr().(*net.UDPAddr)})
	guestMux.SetRemotes([]*net.UDPAddr{left.LocalAddr().(*net.UDPAddr)})

	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	got, err := guestMux.WaitNominated(ctx)
	if err != nil || got == nil || got.Port != left.LocalAddr().(*net.UDPAddr).Port {
		t.Fatalf("guest nominated = %v, %v", got, err)
	}
	hostCtx, hostCancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer hostCancel()
	nominated, nomErr := hostMux.WaitNominated(hostCtx)
	if nomErr != nil || nominated == nil || nominated.Port != right.LocalAddr().(*net.UDPAddr).Port {
		t.Fatalf("host nominated = %v, %v", nominated, nomErr)
	}
}
