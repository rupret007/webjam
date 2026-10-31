package webjoin

import (
	"errors"
	"strings"
	"testing"
)

func mixedIPVersionSDP() (string, string) {
	pin := strings.Repeat("11", 32)
	fingerprint := strings.TrimSuffix(strings.Repeat("11:", 32), ":")
	return strings.Join([]string{
		"v=0",
		"o=- 1 1 IN IP4 0.0.0.0",
		"s=-",
		"t=0 0",
		"a=fingerprint:sha-256 " + fingerprint,
		"m=audio 9 UDP/TLS/RTP/SAVPF 111",
		"c=IN IP4 0.0.0.0",
		"a=rtpmap:111 opus/48000/2",
		"a=candidate:1 1 udp 12345 8.8.8.8 42345 typ srflx raddr 0.0.0.0 rport 0",
		"a=candidate:2 1 udp 12345 2606:4700:4700::1111 42346 typ srflx raddr :: rport 0",
		"m=application 9 UDP/DTLS/SCTP webrtc-datachannel",
		"a=sctp-port:5000",
		"",
	}, "\r\n"), pin
}

func TestLocalSDPStripsIPv6AndRetainsBrowserCompatibleIPv4(t *testing.T) {
	raw, pin := mixedIPVersionSDP()
	sanitized, err := validateSDP(raw, pin, nil, true, false)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(sanitized, "2606:4700:4700::1111") {
		t.Fatal("IPv6 candidate would make the browser reject the whole host offer")
	}
	if !strings.Contains(sanitized, "8.8.8.8 42345 typ srflx") {
		t.Fatal("usable IPv4 fallback was removed")
	}
}

func TestRemoteSDPRejectsIPv6AlongsideValidIPv4(t *testing.T) {
	raw, pin := mixedIPVersionSDP()
	if _, err := validateSDP(raw, pin, nil, false, false); !errors.Is(err, ErrCandidate) {
		t.Fatalf("remote IPv6 candidate: got %v, want %v", err, ErrCandidate)
	}
}

func TestSDPRejectsIPv4MappedIPv6CandidateText(t *testing.T) {
	raw, pin := mixedIPVersionSDP()
	raw = strings.ReplaceAll(raw, "2606:4700:4700::1111", "::ffff:8.8.4.4")
	sanitized, err := validateSDP(raw, pin, nil, true, false)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(sanitized, "::ffff:") {
		t.Fatal("IPv4-mapped IPv6 text would make the browser reject the host offer")
	}
	if !strings.Contains(sanitized, "8.8.8.8 42345 typ srflx") {
		t.Fatal("usable IPv4 fallback was removed")
	}
	if _, err := validateSDP(raw, pin, nil, false, false); !errors.Is(err, ErrCandidate) {
		t.Fatalf("remote mapped IPv6 candidate: got %v, want %v", err, ErrCandidate)
	}
}
