package ipc

import (
	"context"
	"net"
	"testing"
	"time"

	"github.com/pion/ice/v4"
	"github.com/pion/turn/v5"
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

func TestHostFetchesTURNInParallelAndSkipsWaitOnLAN(t *testing.T) {
	previousGather := gatherListenCandidates
	previousAlloc := tryAllocateRelay
	t.Cleanup(func() {
		gatherListenCandidates = previousGather
		tryAllocateRelay = previousAlloc
	})
	gatherStarted := make(chan struct{})
	gatherRelease := make(chan struct{})
	allocateStarted := make(chan struct{})
	allocateRelease := make(chan struct{})
	gatherListenCandidates = func(_ context.Context, conn *net.UDPConn) ([]string, bool, error) {
		close(gatherStarted)
		<-gatherRelease
		local, ok := conn.LocalAddr().(*net.UDPAddr)
		if !ok {
			t.Fatal("missing UDP port")
		}
		encoded, err := directstun.LoopbackCandidate(local.Port)
		return []string{encoded}, false, err
	}
	tryAllocateRelay = func(context.Context) *directstun.Allocation {
		close(allocateStarted)
		<-allocateRelease
		return nil
	}

	now := time.Now().UTC().Truncate(time.Second)
	hostIdentity, _ := mustIdentities(t, now)
	hostOp, cancel := startDirectStunHost(t, now, hostIdentity)
	defer cancel()
	defer hostOp.Close(context.Background())

	select {
	case <-gatherStarted:
	case <-time.After(time.Second):
		t.Fatal("gather should start immediately")
	}
	select {
	case <-allocateStarted:
	case <-time.After(time.Second):
		t.Fatal("TURN fetch must start in parallel with gather, not after")
	}
	close(gatherRelease)

	deadline := time.After(2 * time.Second)
	for {
		select {
		case update, ok := <-hostOp.Updates():
			if !ok {
				t.Fatal("host updates closed before register")
			}
			if update.kind == updateFabricFailed {
				t.Fatalf("host failed: %v", update.err)
			}
			if update.kind == updateHostRegistered {
				if update.route != "local" {
					t.Fatalf("LAN host route=%q", update.route)
				}
				close(allocateRelease)
				return
			}
		case <-deadline:
			t.Fatal("LAN host waited on TURN instead of registering")
		}
	}
}

func TestDirectStunDirectFailsThenRelayConnects(t *testing.T) {
	stopTURN := startHarnessTURN(t)
	defer stopTURN()
	previousTimeout := directPathTimeout
	directPathTimeout = 200 * time.Millisecond
	t.Cleanup(func() { directPathTimeout = previousTimeout })
	installUnreachableDirectGather(t)

	now := time.Now().UTC().Truncate(time.Second)
	hostIdentity, guestIdentity := mustIdentities(t, now)
	hostOp, cancelHost := startDirectStunHost(t, now, hostIdentity)
	defer cancelHost()
	defer hostOp.Close(context.Background())
	envelope := waitHostRegistered(t, hostOp)
	guestOp, cancelGuest := startDirectStunGuest(t, now, hostIdentity, guestIdentity, envelope)
	defer cancelGuest()
	defer guestOp.Close(context.Background())
	waitBothConnected(t, hostOp, guestOp, true)
}

func TestDirectStunRelayRejectsWrongPin(t *testing.T) {
	stopTURN := startHarnessTURN(t)
	defer stopTURN()
	previousTimeout := directPathTimeout
	directPathTimeout = 200 * time.Millisecond
	t.Cleanup(func() { directPathTimeout = previousTimeout })
	installUnreachableDirectGather(t)

	now := time.Now().UTC().Truncate(time.Second)
	hostIdentity, guestIdentity := mustIdentities(t, now)
	wrong := hostIdentity
	wrong.SPKIFingerprint[0] ^= 0xff
	hostOp, cancelHost := startDirectStunHost(t, now, hostIdentity)
	defer cancelHost()
	defer hostOp.Close(context.Background())
	envelope := waitHostRegistered(t, hostOp)
	guestOp, cancelGuest := startDirectStunGuest(t, now, wrong, guestIdentity, envelope)
	defer cancelGuest()
	defer guestOp.Close(context.Background())
	waitGuestFailed(t, guestOp)
}

func TestDirectStunRelayRejectsBadProof(t *testing.T) {
	stopTURN := startHarnessTURN(t)
	defer stopTURN()
	previousTimeout := directPathTimeout
	directPathTimeout = 200 * time.Millisecond
	t.Cleanup(func() { directPathTimeout = previousTimeout })
	installUnreachableDirectGather(t)

	now := time.Now().UTC().Truncate(time.Second)
	hostIdentity, guestIdentity := mustIdentities(t, now)
	hostOp, cancelHost := startDirectStunHost(t, now, hostIdentity)
	defer cancelHost()
	defer hostOp.Close(context.Background())
	envelope := waitHostRegistered(t, hostOp)

	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	var session SessionID
	session[0] = 9
	var capability Capability
	capability[0] = 9
	guestEndpoint, err := loopback.NewGuestProxy()
	if err != nil {
		t.Fatal(err)
	}
	guestOp, err := newDirectStunOrchestrator(func() time.Time { return now }).Start(
		ctx,
		&enrollmentConfig{
			Profile:              mustProfile(t, profile.DirectStunID),
			Mode:                 "guest",
			Generation:           1,
			ExpiresAtUnix:        uint64(now.Add(2 * time.Minute).Unix()),
			HostSPKISHA256:       PublicPin(hostIdentity.SPKIFingerprint),
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
	waitGuestFailed(t, guestOp)
}

func mustProfile(t *testing.T, id string) profile.Profile {
	t.Helper()
	resolved, ok := profile.Lookup(id)
	if !ok {
		t.Fatalf("missing profile %s", id)
	}
	return resolved
}

func mustIdentities(t *testing.T, now time.Time) (icequic.Identity, icequic.Identity) {
	t.Helper()
	hostIdentity, err := icequic.NewEphemeralIdentity(now, time.Hour)
	if err != nil {
		t.Fatal(err)
	}
	guestIdentity, err := icequic.NewEphemeralIdentity(now, time.Hour)
	if err != nil {
		t.Fatal(err)
	}
	return hostIdentity, guestIdentity
}

func startDirectStunHost(t *testing.T, now time.Time, hostIdentity icequic.Identity) (fabricOperation, context.CancelFunc) {
	t.Helper()
	hostEndpoint, err := loopback.NewHostProxy(&net.UDPAddr{IP: net.IPv4(127, 0, 0, 1), Port: 22124})
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	var session SessionID
	session[0] = 9
	var capability Capability
	capability[0] = 4
	hostOp, err := newDirectStunOrchestrator(func() time.Time { return now }).Start(
		ctx,
		&enrollmentConfig{
			Profile:              mustProfile(t, profile.DirectStunID),
			Mode:                 "host",
			TargetPort:           22124,
			Generation:           1,
			ExpiresAtUnix:        uint64(now.Add(2 * time.Minute).Unix()),
			HostSPKISHA256:       PublicPin(hostIdentity.SPKIFingerprint),
			SessionID:            session,
			EnrollmentCapability: capability,
		},
		&hostIdentity,
		hostEndpoint,
	)
	if err != nil {
		cancel()
		t.Fatalf("host start: %v", err)
	}
	return hostOp, cancel
}

func startDirectStunGuest(
	t *testing.T,
	now time.Time,
	hostIdentity icequic.Identity,
	guestIdentity icequic.Identity,
	envelope []byte,
) (fabricOperation, context.CancelFunc) {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	var session SessionID
	session[0] = 9
	var capability Capability
	capability[0] = 4
	guestEndpoint, err := loopback.NewGuestProxy()
	if err != nil {
		cancel()
		t.Fatal(err)
	}
	guestOp, err := newDirectStunOrchestrator(func() time.Time { return now }).Start(
		ctx,
		&enrollmentConfig{
			Profile:              mustProfile(t, profile.DirectStunID),
			Mode:                 "guest",
			Generation:           1,
			ExpiresAtUnix:        uint64(now.Add(2 * time.Minute).Unix()),
			HostSPKISHA256:       PublicPin(hostIdentity.SPKIFingerprint),
			SessionID:            session,
			EnrollmentCapability: capability,
			HostSignal:           envelope,
		},
		&guestIdentity,
		guestEndpoint,
	)
	if err != nil {
		cancel()
		t.Fatalf("guest start: %v", err)
	}
	return guestOp, cancel
}

func waitHostRegistered(t *testing.T, hostOp fabricOperation) []byte {
	t.Helper()
	deadline := time.After(8 * time.Second)
	for {
		select {
		case update, ok := <-hostOp.Updates():
			if !ok {
				t.Fatal("host updates closed before register")
			}
			if update.kind == updateFabricFailed {
				t.Fatalf("host failed: %v", update.err)
			}
			if update.kind == updateHostRegistered {
				if len(update.signal) == 0 {
					t.Fatal("missing host signal")
				}
				return append([]byte(nil), update.signal...)
			}
		case <-deadline:
			t.Fatal("timed out waiting for host register")
		}
	}
}

func waitBothConnected(t *testing.T, hostOp, guestOp fabricOperation, applyReply bool) {
	t.Helper()
	hostConnected := false
	guestConnected := false
	deadline := time.After(10 * time.Second)
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
				if applyReply && update.sessionPath != "relay" {
					t.Fatalf("expected relay path, got %q", update.sessionPath)
				}
				hostConnected = true
			}
		case update, ok := <-guestOp.Updates():
			if !ok {
				t.Fatal("guest updates closed")
			}
			if update.kind == updateFabricFailed {
				t.Fatalf("guest failed: %v", update.err)
			}
			if update.kind == updateGuestSignal && applyReply {
				if applyErr := hostOp.ApplyGuestSignal(context.Background(), update.signal); applyErr != nil {
					t.Fatalf("apply guest signal: %v", applyErr)
				}
			}
			if update.kind == updatePeerConnected {
				if applyReply && update.sessionPath != "relay" {
					t.Fatalf("expected relay path, got %q", update.sessionPath)
				}
				guestConnected = true
			}
		case <-deadline:
			t.Fatalf("timed out host_connected=%v guest_connected=%v", hostConnected, guestConnected)
		}
	}
}

func waitGuestFailed(t *testing.T, guestOp fabricOperation) {
	t.Helper()
	deadline := time.After(8 * time.Second)
	for {
		select {
		case update, ok := <-guestOp.Updates():
			if !ok {
				return
			}
			if update.kind == updatePeerConnected {
				t.Fatal("guest must not connect with a bad pin or proof")
			}
			if update.kind == updateFabricFailed {
				return
			}
		case <-deadline:
			t.Fatal("guest did not fail closed")
		}
	}
}

func installUnreachableDirectGather(t *testing.T) {
	t.Helper()
	previousGather := gatherListenCandidates
	t.Cleanup(func() { gatherListenCandidates = previousGather })
	encoded, err := unreachablePublicCandidate()
	if err != nil {
		t.Fatal(err)
	}
	gatherListenCandidates = func(context.Context, *net.UDPConn) ([]string, bool, error) {
		return []string{encoded}, true, nil
	}
}

func unreachablePublicCandidate() (string, error) {
	candidate, err := ice.NewCandidateServerReflexive(&ice.CandidateServerReflexiveConfig{
		Network:   "udp",
		Address:   "192.0.2.1",
		Port:      9,
		Component: 1,
		RelAddr:   "0.0.0.0",
		RelPort:   9,
	})
	if err != nil {
		return "", err
	}
	return candidate.Marshal(), nil
}

func startHarnessTURN(t *testing.T) func() {
	t.Helper()
	secret := "relay-orchestrator-secret"
	packet, err := net.ListenUDP("udp4", &net.UDPAddr{IP: net.IPv4(127, 0, 0, 1), Port: 0})
	if err != nil {
		t.Fatal(err)
	}
	server, err := turn.NewServer(turn.ServerConfig{
		Realm:       "webjam.test",
		AuthHandler: turn.LongTermTURNRESTAuthHandler(secret, nil),
		PacketConnConfigs: []turn.PacketConnConfig{{
			PacketConn: packet,
			RelayAddressGenerator: &turn.RelayAddressGeneratorStatic{
				RelayAddress: net.ParseIP("127.0.0.1"),
				Address:      "127.0.0.1",
			},
		}},
	})
	if err != nil {
		_ = packet.Close()
		t.Fatal(err)
	}
	t.Setenv("WEBJAM_TEST_HARNESS", "1")
	t.Setenv("WEBJAM_TEST_TURN_SECRET", secret)
	t.Setenv("WEBJAM_TEST_TURN_SERVER", packet.LocalAddr().String())
	return func() {
		_ = server.Close()
		_ = packet.Close()
	}
}
