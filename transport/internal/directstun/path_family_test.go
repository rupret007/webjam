package directstun

import (
	"net/netip"
	"testing"
)

func TestDirectSTUNCandidateAddressFamilies(t *testing.T) {
	t.Parallel()
	for _, test := range []struct {
		name    string
		address string
		host    bool
		want    string
	}{
		{name: "ipv6_public_srflx", address: "2606:4700:4700::1111"},
		{name: "ipv6_private_host", address: "fd00::1", host: true},
		{name: "ipv4_mapped_srflx", address: "::ffff:203.0.113.1", want: "203.0.113.1"},
		{name: "ipv4_mapped_host", address: "::ffff:192.168.1.20", host: true, want: "192.168.1.20"},
	} {
		t.Run(test.name, func(t *testing.T) {
			address := netip.MustParseAddr(test.address)
			var encoded string
			var err error
			if test.host {
				encoded, err = hostCandidate(address, 22124)
			} else {
				encoded, err = srflxCandidate(netip.AddrPortFrom(address, 22124), 4000)
			}
			if err != nil {
				t.Fatal(err)
			}
			target, err := ParseDialTarget(encoded)
			if test.want == "" {
				if err != errRejectedCandidate || target != nil {
					t.Fatalf("udp4 transport accepted native IPv6: target=%v, error=%v", target, err)
				}
				return
			}
			if err != nil || target == nil || target.IP.String() != test.want || target.Port != 22124 {
				t.Fatalf("mapped IPv4 target=%v, error=%v; want %s:22124", target, err, test.want)
			}
		})
	}
}
