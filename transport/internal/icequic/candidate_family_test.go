package icequic

import (
	"errors"
	"reflect"
	"sort"
	"testing"
	"time"

	"github.com/pion/ice/v4"
	"github.com/pion/stun/v3"
	"github.com/rupret007/webjam/transport/internal/signaling"
)

func TestAuthenticatedCandidateBundlesIPv6AndDualStack(t *testing.T) {
	t.Parallel()
	v4 := familyCandidate(t, ice.CandidateTypeServerReflexive, "8.8.8.8")
	v6 := familyCandidate(t, ice.CandidateTypeServerReflexive, "2606:4700:4700::1111")
	for _, test := range []struct {
		name       string
		networks   []ice.NetworkType
		candidates []string
	}{
		{name: "ipv6_only", networks: []ice.NetworkType{ice.NetworkTypeUDP6}, candidates: []string{v6}},
		{name: "dual_stack_ipv6_first", networks: []ice.NetworkType{ice.NetworkTypeUDP4, ice.NetworkTypeUDP6}, candidates: []string{v6, v4}},
		{name: "dual_stack_ipv4_first", networks: []ice.NetworkType{ice.NetworkTypeUDP4, ice.NetworkTypeUDP6}, candidates: []string{v4, v6}},
	} {
		t.Run(test.name, func(t *testing.T) {
			agent := newCandidateFamilyAgent(t, test.networks, nil)
			opened, err := openFamilyBundle(t, agent, test.candidates, 1)
			if err != nil {
				t.Fatalf("authenticate candidate families: %v", err)
			}
			want := append([]string(nil), test.candidates...)
			sort.Strings(want)
			if !reflect.DeepEqual(opened.Candidates, want) {
				t.Fatalf("candidate bundle lost or changed addresses: %v", opened.Candidates)
			}
			assertRemoteCandidates(t, agent, want)
		})
	}
}

func TestAuthenticatedCandidateBundleRejectsUnconfiguredFamilyAtomically(t *testing.T) {
	t.Parallel()
	v4 := familyCandidate(t, ice.CandidateTypeServerReflexive, "8.8.8.8")
	v6 := familyCandidate(t, ice.CandidateTypeServerReflexive, "2606:4700:4700::1111")
	for _, test := range []struct {
		name     string
		networks []ice.NetworkType
		valid    string
	}{
		{name: "default_ipv4", valid: familyCandidate(t, ice.CandidateTypeServerReflexive, "1.1.1.1")},
		{name: "ipv4_only", networks: []ice.NetworkType{ice.NetworkTypeUDP4}, valid: familyCandidate(t, ice.CandidateTypeServerReflexive, "1.1.1.1")},
		{name: "ipv6_only", networks: []ice.NetworkType{ice.NetworkTypeUDP6}, valid: familyCandidate(t, ice.CandidateTypeServerReflexive, "2606:4700:4700::1001")},
	} {
		t.Run(test.name, func(t *testing.T) {
			agent := newCandidateFamilyAgent(t, test.networks, nil)
			if _, err := openFamilyBundle(t, agent, []string{v4, v6}, 1); !errors.Is(err, ErrICECandidate) {
				t.Fatalf("mixed bundle outside configured families returned %v", err)
			}
			assertRemoteCandidates(t, agent, nil)
			if _, err := agent.remoteCredentials(); !errors.Is(err, ErrICEState) {
				t.Fatalf("rejected bundle installed remote credentials: %v", err)
			}
			if _, err := openFamilyBundle(t, agent, []string{test.valid}, 2); err != nil {
				t.Fatalf("fresh valid bundle after rejection failed: %v", err)
			}
			assertRemoteCandidates(t, agent, []string{test.valid})
		})
	}
}

func TestAuthenticatedIPv6RelayRequiresExactCredentialedAddress(t *testing.T) {
	t.Parallel()
	allowed := "2606:4700:4700::1111"
	for _, test := range []struct {
		name    string
		address string
		wantErr bool
	}{
		{name: "credentialed_ipv6", address: allowed},
		{name: "different_ipv6", address: "2606:4700:4700::1001", wantErr: true},
		{name: "unlisted_ipv4", address: "8.8.8.8", wantErr: true},
	} {
		t.Run(test.name, func(t *testing.T) {
			agent := newCandidateFamilyAgent(t, []ice.NetworkType{ice.NetworkTypeUDP4, ice.NetworkTypeUDP6}, []string{allowed})
			candidate := familyCandidate(t, ice.CandidateTypeRelay, test.address)
			_, err := openFamilyBundle(t, agent, []string{candidate}, 1)
			if test.wantErr {
				if !errors.Is(err, ErrICECandidate) {
					t.Fatalf("unlisted relay error = %v", err)
				}
				assertRemoteCandidates(t, agent, nil)
				return
			}
			if err != nil {
				t.Fatalf("credentialed IPv6 relay rejected: %v", err)
			}
			assertRemoteCandidates(t, agent, []string{candidate})
		})
	}
}

func TestAuthenticatedIPv6CandidateRejectsNonPublicAddresses(t *testing.T) {
	t.Parallel()
	for _, address := range []string{"::", "::1", "fe80::1", "fd00::1", "ff02::1", "2001:db8::1", "2001:2::1", "3fff::1"} {
		t.Run(address, func(t *testing.T) {
			agent := newCandidateFamilyAgent(t, []ice.NetworkType{ice.NetworkTypeUDP6}, nil)
			candidate := familyCandidate(t, ice.CandidateTypeServerReflexive, address)
			if _, err := openFamilyBundle(t, agent, []string{candidate}, 1); !errors.Is(err, ErrICECandidate) {
				t.Fatalf("nonpublic IPv6 candidate error = %v", err)
			}
			assertRemoteCandidates(t, agent, nil)
		})
	}
}

func newCandidateFamilyAgent(t *testing.T, networks []ice.NetworkType, relays []string) *Agent {
	t.Helper()
	config := AgentConfig{
		URLs:           []*stun.URI{{Scheme: stun.SchemeTypeSTUN, Host: "127.0.0.1", Port: 3478, Proto: stun.ProtoTypeUDP}},
		CandidateTypes: []ice.CandidateType{ice.CandidateTypeServerReflexive},
		NetworkTypes:   networks, AllowedRelayAddresses: relays,
	}
	if len(relays) > 0 {
		config.URLs = append(config.URLs, &stun.URI{Scheme: stun.SchemeTypeTURN, Host: "127.0.0.1", Port: 3478, Proto: stun.ProtoTypeUDP})
		config.CandidateTypes = append(config.CandidateTypes, ice.CandidateTypeRelay)
	}
	agent, err := NewAgent(config)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = agent.Close() })
	// Exercise authenticated candidate ingestion without interface-dependent
	// gathering or connections to the public fixture addresses.
	agent.gathered = true
	return agent
}

func familyCandidate(t *testing.T, kind ice.CandidateType, address string) string {
	t.Helper()
	var candidate ice.Candidate
	var err error
	if kind == ice.CandidateTypeRelay {
		candidate, err = ice.NewCandidateRelay(&ice.CandidateRelayConfig{
			Network: "udp", Address: address, Port: 5000, Component: 1,
			RelAddr: "0.0.0.0", RelPort: 4000,
		})
	} else {
		candidate, err = ice.NewCandidateServerReflexive(&ice.CandidateServerReflexiveConfig{
			Network: "udp", Address: address, Port: 5000, Component: 1,
			RelAddr: "0.0.0.0", RelPort: 4000,
		})
	}
	if err != nil {
		t.Fatal(err)
	}
	return candidate.Marshal()
}

func openFamilyBundle(t *testing.T, agent *Agent, candidates []string, nonce byte) (signaling.Bundle, error) {
	t.Helper()
	now := time.Now().UTC().Truncate(time.Second)
	capability := signaling.Capability{1}
	session := signaling.SessionID{2}
	pin := signaling.PeerPin{3}
	envelope, err := signaling.Seal(capability, signaling.Bundle{
		SessionID: session, SenderRole: signaling.RoleHost, HostPin: pin, HostPinKnown: true,
		Nonce: signaling.BundleNonce{nonce}, Generation: 1, ExpiresAt: now.Add(time.Minute),
		ICEUfrag: "test-ufrag", ICEPassword: "test-password", Candidates: candidates,
	})
	if err != nil {
		t.Fatal(err)
	}
	return agent.OpenAuthenticatedRemote(capability, signaling.Expected{
		SessionID: session, SenderRole: signaling.RoleHost, HostPin: pin, HostPinKnown: true, Generation: 1,
	}, envelope, now, signaling.NewReplayCache())
}

func assertRemoteCandidates(t *testing.T, agent *Agent, want []string) {
	t.Helper()
	// Pion enqueues candidate additions from another goroutine. Wait for all
	// accepted candidates to appear instead of depending on its scheduling.
	deadline := time.NewTimer(2 * time.Second)
	defer deadline.Stop()
	tick := time.NewTicker(time.Millisecond)
	defer tick.Stop()
	for {
		candidates, err := agent.inner.GetRemoteCandidates()
		if err != nil {
			t.Fatal(err)
		}
		var got []string
		for _, candidate := range candidates {
			got = append(got, candidate.Marshal())
		}
		sort.Strings(got)
		if reflect.DeepEqual(got, want) {
			return
		}
		if len(got) >= len(want) {
			t.Fatalf("Pion remote candidates = %v, want %v", got, want)
		}
		select {
		case <-tick.C:
		case <-deadline.C:
			t.Fatalf("Pion remote candidates = %v, want %v", got, want)
		}
	}
}
