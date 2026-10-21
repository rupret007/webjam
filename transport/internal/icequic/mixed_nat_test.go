package icequic_test

import (
	"bytes"
	"context"
	"testing"
	"time"

	"github.com/pion/ice/v4"
	"github.com/pion/stun/v3"
	"github.com/pion/transport/v4/vnet"
	"github.com/rupret007/webjam/transport/internal/icequic"
	"github.com/rupret007/webjam/transport/internal/signaling"
)

func TestMixedCandidatesChooseDirectOrTURNForNATMapping(t *testing.T) {
	for _, mode := range []labMode{labDirect, labRelay} {
		t.Run(string(mode), func(t *testing.T) {
			// The existing virtual network uses endpoint-independent NAT for
			// direct mode and address/port-dependent mapping AND filtering for
			// relay mode. Both peers offer real srflx and relay candidates in
			// both cases; only the NAT behavior changes. No OS network is used.
			network := newLabNetwork(t, mode)
			t.Cleanup(network.Close)
			host := newMixedNATAgent(t, network.hostNet)
			guest := newMixedNATAgent(t, network.guestNet)
			ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
			t.Cleanup(cancel)
			now := time.Now().UTC().Truncate(time.Second)
			capability := signaling.Capability{1, 9, 8, 4}
			session := signaling.SessionID{7, 6, 5, 4}
			hostPin, guestPin := signaling.PeerPin{11}, signaling.PeerPin{22}
			gather := func(agent *icequic.Agent, role signaling.Role) []byte {
				t.Helper()
				envelope, err := agent.GatherSealed(ctx, capability, signaling.Bundle{
					SessionID: session, SenderRole: role, HostPin: hostPin, GuestPin: guestPin,
					HostPinKnown: true, GuestPinKnown: true, Nonce: signaling.BundleNonce{byte(role)},
					Generation: 1, ExpiresAt: now.Add(time.Minute),
				})
				if err != nil {
					t.Fatalf("gather and seal %v candidates: %v", role, err)
				}
				return envelope
			}
			hostEnvelope := gather(host, signaling.RoleHost)
			guestEnvelope := gather(guest, signaling.RoleGuest)
			for _, remote := range []struct {
				agent    *icequic.Agent
				role     signaling.Role
				envelope []byte
				publicIP string
			}{
				{guest, signaling.RoleHost, hostEnvelope, "27.1.1.1"},
				{host, signaling.RoleGuest, guestEnvelope, "28.1.1.1"},
			} {
				bundle, err := remote.agent.OpenAuthenticatedRemote(capability, signaling.Expected{
					SessionID: session, SenderRole: remote.role, HostPin: hostPin, GuestPin: guestPin,
					HostPinKnown: true, GuestPinKnown: true, Generation: 1,
				}, remote.envelope, now, signaling.NewReplayCache())
				if err != nil {
					t.Fatalf("authenticate mixed candidates: %v", err)
				}
				assertMixedNATCandidates(t, bundle.Candidates, remote.publicIP)
			}

			hostConn, guestConn := connectLabICE(t, ctx, host, guest)
			t.Cleanup(func() { _ = hostConn.Close() })
			t.Cleanup(func() { _ = guestConn.Close() })
			for name, agent := range map[string]*icequic.Agent{"host": host, "guest": guest} {
				path, err := agent.SelectedPath()
				if err != nil {
					t.Fatalf("%s selected pair: %v", name, err)
				}
				if mode == labRelay {
					if path.LocalType != ice.CandidateTypeRelay.String() && path.RemoteType != ice.CandidateTypeRelay.String() {
						t.Fatalf("%s symmetric NAT selected %s/%s without TURN", name, path.LocalType, path.RemoteType)
					}
				} else if path.LocalType != ice.CandidateTypeServerReflexive.String() || path.RemoteType != ice.CandidateTypeServerReflexive.String() {
					t.Fatalf("%s traversable NAT selected %s/%s instead of direct srflx pair", name, path.LocalType, path.RemoteType)
				}
			}

			for _, exchange := range []struct {
				sender, receiver *ice.Conn
				payload          []byte
			}{
				{guestConn, hostConn, []byte("guest-to-host through " + string(mode))},
				{hostConn, guestConn, []byte("host-to-guest through " + string(mode))},
			} {
				if err := exchange.sender.SetWriteDeadline(time.Now().Add(2 * time.Second)); err != nil {
					t.Fatal(err)
				}
				if err := exchange.receiver.SetReadDeadline(time.Now().Add(2 * time.Second)); err != nil {
					t.Fatal(err)
				}
				if n, err := exchange.sender.Write(exchange.payload); err != nil || n != len(exchange.payload) {
					t.Fatalf("send ICE payload: bytes=%d err=%v", n, err)
				}
				buffer := make([]byte, 1500)
				n, err := exchange.receiver.Read(buffer)
				if err != nil || !bytes.Equal(buffer[:n], exchange.payload) {
					t.Fatalf("ICE payload changed or lost: bytes=%q err=%v", buffer[:n], err)
				}
			}
		})
	}
}

func newMixedNATAgent(t *testing.T, network *vnet.Net) *icequic.Agent {
	t.Helper()
	agent, err := icequic.NewAgent(icequic.AgentConfig{
		URLs: []*stun.URI{
			{Scheme: stun.SchemeTypeSTUN, Host: labWANIP, Port: labTURNPort, Proto: stun.ProtoTypeUDP},
			{Scheme: stun.SchemeTypeTURN, Host: labWANIP, Port: labTURNPort, Proto: stun.ProtoTypeUDP,
				Username: labTURNUser, Password: labTURNPass},
		},
		CandidateTypes:        []ice.CandidateType{ice.CandidateTypeServerReflexive, ice.CandidateTypeRelay},
		NetworkTypes:          []ice.NetworkType{ice.NetworkTypeUDP4},
		AllowedRelayAddresses: []string{labWANIP}, Net: network,
	})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = agent.Close() })
	return agent
}

func assertMixedNATCandidates(t *testing.T, encoded []string, publicIP string) {
	t.Helper()
	found := make(map[ice.CandidateType]bool)
	for _, text := range encoded {
		candidate, err := ice.UnmarshalCandidate(text)
		if err != nil {
			t.Fatal(err)
		}
		wantIP := publicIP
		if candidate.Type() == ice.CandidateTypeRelay {
			wantIP = labWANIP
		} else if candidate.Type() != ice.CandidateTypeServerReflexive {
			t.Fatalf("unexpected gathered candidate type: %s", candidate.Type())
		}
		if candidate.Address() != wantIP || candidate.NetworkType() != ice.NetworkTypeUDP4 {
			t.Fatalf("gathered candidate address/family=%s/%s, want %s/udp4", candidate.Address(), candidate.NetworkType(), wantIP)
		}
		found[candidate.Type()] = true
	}
	if !found[ice.CandidateTypeServerReflexive] || !found[ice.CandidateTypeRelay] {
		t.Fatalf("both srflx and relay must be authenticated before ICE selection: found=%v", found)
	}
}
