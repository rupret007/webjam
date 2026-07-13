package icequic

import (
	"testing"

	"github.com/pion/ice/v4"
	"github.com/pion/stun/v3"
)

func TestCandidateClassAndSchemeAllowlist(t *testing.T) {
	t.Parallel()
	agent := &Agent{
		allowedCandidates:   map[ice.CandidateType]struct{}{ice.CandidateTypeServerReflexive: {}},
		allowedNetworkTypes: map[ice.NetworkType]struct{}{ice.NetworkTypeUDP4: {}},
	}
	publicCandidate, err := ice.NewCandidateServerReflexive(&ice.CandidateServerReflexiveConfig{
		Network: "udp4", Address: "8.8.8.8", Port: 5000, Component: 1,
		RelAddr: "10.0.0.1", RelPort: 4000,
	})
	if err != nil {
		t.Fatal(err)
	}
	if !agent.candidateAllowed(publicCandidate) {
		t.Fatal("allowed public UDP4 server-reflexive candidate rejected")
	}
	privateCandidate, err := ice.NewCandidateServerReflexive(&ice.CandidateServerReflexiveConfig{
		Network: "udp4", Address: "192.168.1.20", Port: 5000, Component: 1,
		RelAddr: "10.0.0.1", RelPort: 4000,
	})
	if err != nil {
		t.Fatal(err)
	}
	if agent.candidateAllowed(privateCandidate) {
		t.Fatal("private server-reflexive candidate crossed address-class allowlist")
	}
	relay, err := ice.NewCandidateRelay(&ice.CandidateRelayConfig{
		Network: "udp4", Address: "1.1.1.1", Port: 5001, Component: 1,
		RelAddr: "10.0.0.1", RelPort: 4000,
	})
	if err != nil {
		t.Fatal(err)
	}
	if agent.candidateAllowed(relay) {
		t.Fatal("relay candidate crossed server-reflexive-only class allowlist")
	}

	invalidScheme := AgentConfig{
		URLs: []*stun.URI{{
			Scheme: stun.SchemeTypeTURN, Host: "relay.invalid", Port: 3478, Proto: stun.ProtoTypeUDP,
		}},
		CandidateTypes: []ice.CandidateType{ice.CandidateTypeServerReflexive},
		NetworkTypes:   []ice.NetworkType{ice.NetworkTypeUDP4},
	}
	if err = validateAgentConfig(invalidScheme); err == nil {
		t.Fatal("TURN URL accepted as the only server-reflexive scheme")
	}
	hostCandidate := invalidScheme
	hostCandidate.URLs = []*stun.URI{{
		Scheme: stun.SchemeTypeSTUN, Host: "stun.invalid", Port: 3478, Proto: stun.ProtoTypeUDP,
	}}
	hostCandidate.CandidateTypes = []ice.CandidateType{ice.CandidateTypeHost}
	if err = validateAgentConfig(hostCandidate); err == nil {
		t.Fatal("v3 agent accepted a host candidate")
	}
	relayWithoutFixedAddress := invalidScheme
	relayWithoutFixedAddress.CandidateTypes = []ice.CandidateType{ice.CandidateTypeRelay}
	if err = validateAgentConfig(relayWithoutFixedAddress); err == nil {
		t.Fatal("relay candidate accepted without an exact address allowlist")
	}
}
