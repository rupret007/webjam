package ipc

import (
	"context"
	"net"
	"testing"
	"time"

	"github.com/rupret007/webjam/transport/internal/directstun"
	"github.com/rupret007/webjam/transport/internal/icequic"
	"github.com/rupret007/webjam/transport/internal/loopback"
	"github.com/rupret007/webjam/transport/internal/profile"
	"github.com/rupret007/webjam/transport/internal/signaling"
)

func TestDirectStunLoopbackHostGuestConnects(t *testing.T) {
	previousGather := gatherListenCandidates
	previousParse := parseDialTarget
	t.Cleanup(func() {
		gatherListenCandidates = previousGather
		parseDialTarget = previousParse
	})
	gatherListenCandidates = func(_ context.Context, conn *net.UDPConn) ([]string, bool, error) {
		local, ok := conn.LocalAddr().(*net.UDPAddr)
		if !ok {
			t.Fatal("missing UDP port")
		}
		encoded, err := directstun.LoopbackCandidate(local.Port)
		if err != nil {
			return nil, false, err
		}
		return []string{encoded}, false, nil
	}
	parseDialTarget = func(encoded string) (*net.UDPAddr, error) {
		return directstun.ParseLoopbackDialTarget(encoded)
	}

	now := time.Now().UTC().Truncate(time.Second)
	hostIdentity, err := icequic.NewEphemeralIdentity(now, time.Hour)
	if err != nil {
		t.Fatal(err)
	}
	guestIdentity, err := icequic.NewEphemeralIdentity(now, time.Hour)
	if err != nil {
		t.Fatal(err)
	}
	var session SessionID
	session[0] = 9
	var capability Capability
	capability[0] = 4
	hostPin := PublicPin(hostIdentity.SPKIFingerprint)
	expires := uint64(now.Add(2 * time.Minute).Unix())

	hostEndpoint, err := loopback.NewHostProxy(&net.UDPAddr{IP: net.IPv4(127, 0, 0, 1), Port: 22124})
	if err != nil {
		t.Fatal(err)
	}
	guestEndpoint, err := loopback.NewGuestProxy()
	if err != nil {
		t.Fatal(err)
	}

	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	hostOp, err := newDirectStunOrchestrator(func() time.Time { return now }).Start(
		ctx,
		&enrollmentConfig{
			Profile:              mustProfile(t, profile.DirectStunID),
			Mode:                 "host",
			TargetPort:           22124,
			Generation:           1,
			ExpiresAtUnix:        expires,
			HostSPKISHA256:       hostPin,
			SessionID:            session,
			EnrollmentCapability: capability,
		},
		&hostIdentity,
		hostEndpoint,
	)
	if err != nil {
		t.Fatalf("host start: %v", err)
	}
	defer hostOp.Close(ctx)

	var envelope []byte
	deadline := time.After(8 * time.Second)
	for envelope == nil {
		select {
		case update, ok := <-hostOp.Updates():
			if !ok {
				t.Fatal("host updates closed before register")
			}
			if update.kind == updateFabricFailed {
				t.Fatalf("host failed: %v", update.err)
			}
			if update.kind == updateHostRegistered {
				if update.route != "local" || len(update.signal) == 0 {
					t.Fatalf("host register route=%q signal=%d", update.route, len(update.signal))
				}
				envelope = append([]byte(nil), update.signal...)
			}
		case <-deadline:
			t.Fatal("timed out waiting for host register")
		}
	}
	if _, err = signaling.Open(
		signaling.Capability(capability),
		signaling.Expected{
			SessionID:    signaling.SessionID(session),
			SenderRole:   signaling.RoleHost,
			HostPin:      signaling.PeerPin(hostPin),
			HostPinKnown: true,
			Generation:   referenceWireGeneration,
		},
		envelope,
		now,
		signaling.NewReplayCache(),
	); err != nil {
		t.Fatalf("open host signal: %v", err)
	}

	guestOp, err := newDirectStunOrchestrator(func() time.Time { return now }).Start(
		ctx,
		&enrollmentConfig{
			Profile:              mustProfile(t, profile.DirectStunID),
			Mode:                 "guest",
			Generation:           1,
			ExpiresAtUnix:        expires,
			HostSPKISHA256:       hostPin,
			SessionID:            session,
			EnrollmentCapability: capability,
			HostSignal:           envelope,
		},
		&guestIdentity,
		guestEndpoint,
	)
	if err != nil {
		t.Fatalf("guest start: %v", err)
	}
	defer guestOp.Close(ctx)

	hostConnected := false
	guestConnected := false
	deadline = time.After(10 * time.Second)
	for !hostConnected || !guestConnected {
		select {
		case update, ok := <-hostOp.Updates():
			if !ok {
				t.Fatal("host updates closed")
			}
			if update.kind == updateFabricFailed {
				t.Fatalf("host failed after register: %v", update.err)
			}
			if update.kind == updatePeerConnected {
				hostConnected = true
			}
		case update, ok := <-guestOp.Updates():
			if !ok {
				t.Fatal("guest updates closed")
			}
			if update.kind == updateFabricFailed {
				t.Fatalf("guest failed: %v", update.err)
			}
			if update.kind == updateGuestSignal {
				if applyErr := hostOp.ApplyGuestSignal(ctx, update.signal); applyErr != nil {
					t.Fatalf("apply guest signal: %v", applyErr)
				}
			}
			if update.kind == updatePeerConnected {
				guestConnected = true
			}
		case <-deadline:
			t.Fatalf("timed out host_connected=%v guest_connected=%v", hostConnected, guestConnected)
		}
	}
}

func mustProfile(t *testing.T, id string) profile.Profile {
	t.Helper()
	resolved, ok := profile.Lookup(id)
	if !ok {
		t.Fatalf("missing profile %s", id)
	}
	return resolved
}
