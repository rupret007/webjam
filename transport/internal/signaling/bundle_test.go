package signaling

import (
	"errors"
	"testing"
	"time"
)

func TestCandidateBundleAEADContextExpiryAndReplay(t *testing.T) {
	t.Parallel()
	now := time.Date(2026, 7, 13, 12, 0, 0, 0, time.UTC)
	capability := Capability{1, 2, 3, 4}
	session := SessionID{9, 8, 7}
	hostPin := PeerPin{4, 4, 4}
	nonce := BundleNonce{5, 6, 7}
	bundle := Bundle{
		SessionID: session, SenderRole: RoleHost, HostPin: hostPin, HostPinKnown: true,
		Nonce: nonce, Generation: 3, ExpiresAt: now.Add(time.Minute),
		ICEUfrag: "host-ufrag", ICEPassword: "host-password",
		Candidates: []string{"candidate:2 1 udp 1 28.1.1.1 5001 typ srflx", "candidate:1 1 udp 2 10.0.0.1 5000 typ host"},
	}
	envelope, err := Seal(capability, bundle)
	if err != nil {
		t.Fatal(err)
	}
	if containsCandidatePlaintext(envelope) {
		t.Fatal("sealed envelope exposed candidate plaintext")
	}
	expected := Expected{
		SessionID: session, SenderRole: RoleHost, HostPin: hostPin, HostPinKnown: true, Generation: 3,
	}
	cache := NewReplayCache()
	opened, err := Open(capability, expected, envelope, now, cache)
	if err != nil {
		t.Fatal(err)
	}
	if opened.Candidates[0] >= opened.Candidates[1] {
		t.Fatalf("candidates not canonical: %v", opened.Candidates)
	}
	if _, err = Open(capability, expected, envelope, now, cache); !errors.Is(err, ErrReplay) {
		t.Fatalf("replay error = %v", err)
	}
	wrongRole := expected
	wrongRole.SenderRole = RoleGuest
	if _, err = Open(capability, wrongRole, envelope, now, NewReplayCache()); !errors.Is(err, ErrWrongContext) {
		t.Fatalf("role error = %v", err)
	}
	wrongSession := expected
	wrongSession.SessionID[0] ^= 0xff
	if _, err = Open(capability, wrongSession, envelope, now, NewReplayCache()); !errors.Is(err, ErrAuthentication) {
		t.Fatalf("cross-session error = %v", err)
	}
	if _, err = Open(capability, expected, envelope, now.Add(2*time.Minute), NewReplayCache()); !errors.Is(err, ErrExpired) {
		t.Fatalf("expiry error = %v", err)
	}
	envelope[len(envelope)-1] ^= 1
	if _, err = Open(capability, expected, envelope, now, NewReplayCache()); !errors.Is(err, ErrAuthentication) {
		t.Fatalf("tamper error = %v", err)
	}
}

func containsCandidatePlaintext(envelope []byte) bool {
	needle := []byte("candidate:")
	for index := 0; index+len(needle) <= len(envelope); index++ {
		match := true
		for offset := range needle {
			if envelope[index+offset] != needle[offset] {
				match = false
				break
			}
		}
		if match {
			return true
		}
	}
	return false
}
