package directstun

import (
	"net/netip"
	"testing"
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
