package ipc

import (
	"bytes"
	"context"
	"errors"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"
	"time"

	"github.com/pion/ice/v4"
	"github.com/rupret007/webjam/transport/internal/directstun"
	"github.com/rupret007/webjam/transport/internal/icequic"
	"github.com/rupret007/webjam/transport/internal/loopback"
	"github.com/rupret007/webjam/transport/internal/profile"
)

func TestDirectStunSymmetricNATFallsBackToTURNAndCarriesDatagrams(t *testing.T) {
	stopTURN := startHarnessTURN(t)
	t.Cleanup(stopTURN)
	blackhole, err := net.ListenUDP("udp4", &net.UDPAddr{IP: net.IPv4(127, 0, 0, 1)})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = blackhole.Close() })
	previousGather, previousParse, previousTimeout := gatherListenCandidates, parseDialTarget, directPathTimeout
	t.Cleanup(func() {
		gatherListenCandidates, parseDialTarget, directPathTimeout = previousGather, previousParse, previousTimeout
	})
	directPathTimeout = 200 * time.Millisecond
	// Model the relevant effect of endpoint-dependent (symmetric) mappings:
	// STUN returns a distinct mapping for each peer, but traffic from the other
	// peer cannot traverse that mapping. All rejected probes stay on loopback.
	gatherListenCandidates = func(_ context.Context, conn *net.UDPConn) ([]string, bool, error) {
		candidate, err := ice.NewCandidateServerReflexive(&ice.CandidateServerReflexiveConfig{
			Network: "udp", Address: "192.0.2.1", Port: conn.LocalAddr().(*net.UDPAddr).Port,
			Component: 1, RelAddr: "0.0.0.0", RelPort: conn.LocalAddr().(*net.UDPAddr).Port,
		})
		if err != nil {
			return nil, false, err
		}
		return []string{candidate.Marshal()}, true, nil
	}
	parseDialTarget = func(encoded string) (*net.UDPAddr, error) {
		if _, err := directstun.ParseDialTarget(encoded); err != nil {
			return nil, err
		}
		return blackhole.LocalAddr().(*net.UDPAddr), nil
	}

	now := time.Now().UTC().Truncate(time.Second)
	hostIdentity, guestIdentity := mustIdentities(t, now)
	expiresAt := now.Add(2 * time.Minute)
	host, hostApp := startNetworkTestOperation(t, "host", func() time.Time { return now }, expiresAt, hostIdentity, hostIdentity, nil, 1)
	envelope := waitHostRegistered(t, host)
	guest, guestApp := startNetworkTestOperation(t, "guest", func() time.Time { return now }, expiresAt, hostIdentity, guestIdentity, envelope, 1)
	waitNetworkTestConnected(t, host, guest, "relay")

	// Observe real punch packets from both sockets: successful relay setup
	// must not accidentally satisfy this test by using a direct loopback path.
	if err := blackhole.SetReadDeadline(time.Now().Add(2 * time.Second)); err != nil {
		t.Fatal(err)
	}
	probed := make(map[int]bool)
	buffer := make([]byte, 1500)
	for len(probed) < 2 {
		n, source, err := blackhole.ReadFromUDP(buffer)
		if err != nil {
			t.Fatalf("both NAT mappings must receive a direct probe before fallback: %v", err)
		}
		if n >= 4 && string(buffer[:4]) == "WJHP" {
			probed[source.Port] = true
		}
	}
	for _, op := range []*directStunOperation{host, guest} {
		if op.mux.Nominated() != nil || op.SessionPath() != "relay" {
			t.Fatal("blocked direct mappings must not be nominated")
		}
		if op.RelayBytes() == 0 {
			t.Fatal("connected relay did not account for any traffic")
		}
	}
	assertNetworkTestDatagrams(t, hostApp, guestApp, "symmetric-nat")
}

func TestDirectStunRetryAfterNetworkSocketChanges(t *testing.T) {
	previousGather, previousParse, previousAllocate := gatherListenCandidates, parseDialTarget, tryAllocateRelay
	t.Cleanup(func() {
		gatherListenCandidates, parseDialTarget, tryAllocateRelay = previousGather, previousParse, previousAllocate
	})
	var gathers atomic.Int32
	gatherListenCandidates = func(_ context.Context, conn *net.UDPConn) ([]string, bool, error) {
		gathers.Add(1)
		encoded, err := directstun.LoopbackCandidate(conn.LocalAddr().(*net.UDPAddr).Port)
		return []string{encoded}, false, err
	}
	parseDialTarget = directstun.ParseLoopbackDialTarget
	tryAllocateRelay = func(context.Context) *directstun.Allocation { return nil }
	now := time.Now().UTC().Truncate(time.Second)
	hostIdentity, guestIdentity := mustIdentities(t, now)
	expiresAt := now.Add(2 * time.Minute)
	host, hostApp := startNetworkTestOperation(t, "host", func() time.Time { return now }, expiresAt, hostIdentity, hostIdentity, nil, 1)
	firstEnvelope := waitHostRegistered(t, host)
	guest, guestApp := startNetworkTestOperation(t, "guest", func() time.Time { return now }, expiresAt, hostIdentity, guestIdentity, firstEnvelope, 1)
	waitNetworkTestConnected(t, host, guest, "direct")
	assertNetworkTestDatagrams(t, hostApp, guestApp, "before-network-change")
	oldHostAddr := host.packetConn.LocalAddr().(*net.UDPAddr)
	oldGuestAddr := guest.packetConn.LocalAddr().(*net.UDPAddr)

	// Losing the interface invalidates both UDP sockets. Observe failure and
	// teardown before the explicit Host/Join retry starts with fresh mappings.
	for _, op := range []*directStunOperation{host, guest} {
		if err := op.packetConn.Close(); err != nil {
			t.Fatal(err)
		}
	}
	for _, op := range []*directStunOperation{host, guest} {
		waitNetworkTestFailed(t, op, nil)
	}
	// Keep the retired ports occupied so a fresh gather cannot accidentally
	// reuse a previous network mapping and disguise stale candidate reuse.
	for _, addr := range []*net.UDPAddr{oldHostAddr, oldGuestAddr} {
		reserved, err := net.ListenUDP("udp4", addr)
		if err != nil {
			t.Fatalf("retired network socket was not released: %v", err)
		}
		t.Cleanup(func() { _ = reserved.Close() })
	}

	retryHost, retryHostApp := startNetworkTestOperation(t, "host", func() time.Time { return now }, expiresAt, hostIdentity, hostIdentity, nil, 2)
	retryEnvelope := waitHostRegistered(t, retryHost)
	if bytes.Equal(firstEnvelope, retryEnvelope) {
		t.Fatal("retry reused its retired host signal")
	}
	retryGuest, retryGuestApp := startNetworkTestOperation(t, "guest", func() time.Time { return now }, expiresAt, hostIdentity, guestIdentity, retryEnvelope, 2)
	waitNetworkTestConnected(t, retryHost, retryGuest, "direct")
	if gathers.Load() != 4 {
		t.Fatalf("both peers must gather again on retry; gather calls=%d", gathers.Load())
	}
	for _, op := range []*directStunOperation{retryHost, retryGuest} {
		port := op.packetConn.LocalAddr().(*net.UDPAddr).Port
		if port == oldHostAddr.Port || port == oldGuestAddr.Port {
			t.Fatal("retry retained a socket from the old network")
		}
	}
	assertNetworkTestDatagrams(t, retryHostApp, retryGuestApp, "after-network-change")
}

func TestDirectStunGuestRejectsExpiredHostInviteBeforeNetworkWork(t *testing.T) {
	previousGather, previousParse, previousAllocate := gatherListenCandidates, parseDialTarget, tryAllocateRelay
	t.Cleanup(func() {
		gatherListenCandidates, parseDialTarget, tryAllocateRelay = previousGather, previousParse, previousAllocate
	})
	var gathers, allocations atomic.Int32
	allocationFinished := make(chan struct{}, 2)
	gatherListenCandidates = func(_ context.Context, conn *net.UDPConn) ([]string, bool, error) {
		gathers.Add(1)
		encoded, err := directstun.LoopbackCandidate(conn.LocalAddr().(*net.UDPAddr).Port)
		return []string{encoded}, false, err
	}
	parseDialTarget = directstun.ParseLoopbackDialTarget
	tryAllocateRelay = func(context.Context) *directstun.Allocation {
		allocations.Add(1)
		allocationFinished <- struct{}{}
		return nil
	}
	now := time.Now().UTC().Truncate(time.Second)
	expiresAt := now.Add(2 * time.Minute)
	hostIdentity, guestIdentity := mustIdentities(t, now)
	host, hostApp := startNetworkTestOperation(t, "host", func() time.Time { return now }, expiresAt, hostIdentity, hostIdentity, nil, 1)
	envelope := waitHostRegistered(t, host)
	select {
	case <-allocationFinished:
	case <-time.After(time.Second):
		t.Fatal("host did not finish its credential attempt")
	}
	for _, offset := range []time.Duration{0, time.Second} {
		t.Run(map[time.Duration]string{0: "at_expiry", time.Second: "after_expiry"}[offset], func(t *testing.T) {
			// A caller extending its own setup deadline must not renew the
			// authenticated expiry in an already-issued host invitation.
			guestNow := expiresAt.Add(offset)
			guest, _ := startNetworkTestOperation(t, "guest", func() time.Time { return guestNow }, guestNow.Add(time.Minute), hostIdentity, guestIdentity, envelope, 1)
			waitNetworkTestFailed(t, guest, ErrEnrollmentInvalid)
			if gathers.Load() != 1 || allocations.Load() != 1 {
				t.Fatalf("expired invite performed network work: gathers=%d allocations=%d", gathers.Load(), allocations.Load())
			}
		})
	}
	// A rejected late guest must leave the host available to a valid guest
	// whose clock is still inside the authenticated invitation lifetime.
	guest, guestApp := startNetworkTestOperation(t, "guest", func() time.Time { return now }, expiresAt, hostIdentity, guestIdentity, envelope, 1)
	waitNetworkTestConnected(t, host, guest, "direct")
	assertNetworkTestDatagrams(t, hostApp, guestApp, "valid-invitation")
}

func TestDirectStunCredentialFetchFailureStillConnectsDirectly(t *testing.T) {
	for _, timeout := range []bool{false, true} {
		name := "http_503"
		if timeout {
			name = "request_deadline"
		}
		t.Run(name, func(t *testing.T) {
			previousGather, previousParse, previousAllocate := gatherListenCandidates, parseDialTarget, tryAllocateRelay
			t.Cleanup(func() {
				gatherListenCandidates, parseDialTarget, tryAllocateRelay = previousGather, previousParse, previousAllocate
			})
			var requests atomic.Int32
			canceled := make(chan struct{}, 2)
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				_, _ = io.Copy(io.Discard, r.Body)
				requests.Add(1)
				if timeout {
					<-r.Context().Done()
					canceled <- struct{}{}
					return
				}
				w.WriteHeader(http.StatusServiceUnavailable)
			}))
			t.Cleanup(server.Close)
			t.Setenv("WEBJAM_TEST_HARNESS", "1")
			t.Setenv("WEBJAM_TEST_TURN_SERVER", "")
			t.Setenv("WEBJAM_TEST_TURN_CREDENTIAL_URL", server.URL)
			gatherListenCandidates = func(_ context.Context, conn *net.UDPConn) ([]string, bool, error) {
				encoded, err := directstun.LoopbackCandidate(conn.LocalAddr().(*net.UDPAddr).Port)
				// Both host and guest must await the actual HTTP result, as a
				// host with a server-reflexive mapping does in production.
				return []string{encoded}, true, err
			}
			parseDialTarget = directstun.ParseLoopbackDialTarget
			tryAllocateRelay = func(ctx context.Context) *directstun.Allocation {
				// Bound only the credential attempt. Enrollment must remain
				// usable after the real HTTP client's request times out.
				credentialCtx, cancel := context.WithTimeout(ctx, 150*time.Millisecond)
				defer cancel()
				return directstun.TryAllocate(credentialCtx)
			}
			now := time.Now().UTC().Truncate(time.Second)
			expiresAt := now.Add(2 * time.Minute)
			hostIdentity, guestIdentity := mustIdentities(t, now)
			host, hostApp := startNetworkTestOperation(t, "host", func() time.Time { return now }, expiresAt, hostIdentity, hostIdentity, nil, 1)
			envelope := waitHostRegistered(t, host)
			guest, guestApp := startNetworkTestOperation(t, "guest", func() time.Time { return now }, expiresAt, hostIdentity, guestIdentity, envelope, 1)
			waitNetworkTestConnected(t, host, guest, "direct")
			if requests.Load() != 2 {
				t.Fatalf("credential requests=%d, want one for each peer", requests.Load())
			}
			if timeout {
				for i := 0; i < 2; i++ {
					select {
					case <-canceled:
					case <-time.After(time.Second):
						t.Fatal("timed-out credential request was not canceled")
					}
				}
			}
			if host.allocation != nil || guest.allocation != nil || host.RelayBytes() != 0 || guest.RelayBytes() != 0 {
				t.Fatal("failed credential requests must not leave relay allocations")
			}
			assertNetworkTestDatagrams(t, hostApp, guestApp, name)
		})
	}
}

// startNetworkTestOperation owns real loopback application endpoints as well
// as the transport operation, so connection receipts are checked with payloads.
func startNetworkTestOperation(
	t *testing.T, mode string, now func() time.Time, expiresAt time.Time,
	hostIdentity, identity icequic.Identity, envelope []byte, attempt byte,
) (*directStunOperation, *net.UDPConn) {
	t.Helper()
	var endpoint loopback.Endpoint
	var app *net.UDPConn
	var err error
	if mode == "host" {
		app, err = net.ListenUDP("udp4", &net.UDPAddr{IP: net.IPv4(127, 0, 0, 1)})
		if err == nil {
			endpoint, err = loopback.NewHostProxy(app.LocalAddr().(*net.UDPAddr))
		}
	} else {
		endpoint, err = loopback.NewGuestProxy()
		if err == nil {
			app, err = net.DialUDP("udp4", nil, &net.UDPAddr{IP: net.IPv4(127, 0, 0, 1), Port: endpoint.LocalPort()})
		}
	}
	if app != nil {
		t.Cleanup(func() { _ = app.Close() })
	}
	if endpoint != nil {
		t.Cleanup(func() { _ = endpoint.Close() })
	}
	if err != nil {
		t.Fatal(err)
	}
	configuration := &enrollmentConfig{
		Profile: mustProfile(t, profile.DirectStunID), Mode: mode, Generation: 1,
		ExpiresAtUnix: uint64(expiresAt.Unix()), HostSPKISHA256: PublicPin(hostIdentity.SPKIFingerprint),
		SessionID: SessionID{attempt}, EnrollmentCapability: Capability{attempt + 1}, HostSignal: append([]byte(nil), envelope...),
	}
	if mode == "host" {
		configuration.TargetPort = app.LocalAddr().(*net.UDPAddr).Port
	}
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	t.Cleanup(cancel)
	operation, err := newDirectStunOrchestrator(now).Start(ctx, configuration, &identity, endpoint)
	if err != nil {
		t.Fatalf("start %s: %v", mode, err)
	}
	t.Cleanup(func() {
		closeCtx, cancelClose := context.WithTimeout(context.Background(), 2*time.Second)
		defer cancelClose()
		if err := operation.Close(closeCtx); err != nil {
			t.Errorf("close %s: %v", mode, err)
		}
	})
	return operation.(*directStunOperation), app
}

func waitNetworkTestConnected(t *testing.T, host, guest *directStunOperation, path string) {
	t.Helper()
	hostUpdates, guestUpdates := host.Updates(), guest.Updates()
	deadline := time.After(5 * time.Second)
	for hostUpdates != nil || guestUpdates != nil {
		select {
		case update, open := <-hostUpdates:
			if !open || update.kind == updateFabricFailed {
				t.Fatalf("host failed before connection: open=%v err=%v", open, update.err)
			}
			if update.kind == updatePeerConnected {
				if update.sessionPath != path {
					t.Fatalf("host path=%q, want %q", update.sessionPath, path)
				}
				hostUpdates = nil
			}
		case update, open := <-guestUpdates:
			if !open || update.kind == updateFabricFailed {
				t.Fatalf("guest failed before connection: open=%v err=%v", open, update.err)
			}
			if update.kind == updateGuestSignal {
				ctx, cancel := context.WithTimeout(context.Background(), time.Second)
				err := host.ApplyGuestSignal(ctx, update.signal)
				cancel()
				if err != nil {
					t.Fatalf("apply guest signal: %v", err)
				}
			}
			if update.kind == updatePeerConnected {
				if update.sessionPath != path {
					t.Fatalf("guest path=%q, want %q", update.sessionPath, path)
				}
				guestUpdates = nil
			}
		case <-deadline:
			t.Fatal("host and guest did not connect within the deadline")
		}
	}
}

func waitNetworkTestFailed(t *testing.T, operation *directStunOperation, wantErr error) {
	t.Helper()
	failures := 0
	deadline := time.After(2 * time.Second)
	for {
		select {
		case update, open := <-operation.Updates():
			if !open {
				if failures != 1 {
					t.Fatalf("operation closed with %d failure receipts, want 1", failures)
				}
				return
			}
			if update.kind != updateFabricFailed || update.err == nil {
				t.Fatalf("failed operation emitted unexpected update: kind=%v err=%v", update.kind, update.err)
			}
			if wantErr != nil && !errors.Is(update.err, wantErr) {
				t.Fatalf("failure=%v, want %v", update.err, wantErr)
			}
			failures++
		case <-deadline:
			t.Fatal("failed operation did not close within the deadline")
		}
	}
}

func assertNetworkTestDatagrams(t *testing.T, hostApp, guestApp *net.UDPConn, label string) {
	t.Helper()
	request, response := []byte(label+":guest-to-host"), []byte(label+":host-to-guest")
	if err := hostApp.SetDeadline(time.Now().Add(2 * time.Second)); err != nil {
		t.Fatal(err)
	}
	if err := guestApp.SetDeadline(time.Now().Add(2 * time.Second)); err != nil {
		t.Fatal(err)
	}
	if _, err := guestApp.Write(request); err != nil {
		t.Fatal(err)
	}
	buffer := make([]byte, 1500)
	n, peerAddr, err := hostApp.ReadFromUDP(buffer)
	if err != nil || !bytes.Equal(buffer[:n], request) {
		t.Fatalf("guest payload not delivered: bytes=%q err=%v", buffer[:n], err)
	}
	if _, err := hostApp.WriteToUDP(response, peerAddr); err != nil {
		t.Fatal(err)
	}
	n, err = guestApp.Read(buffer)
	if err != nil || !bytes.Equal(buffer[:n], response) {
		t.Fatalf("host payload not delivered: bytes=%q err=%v", buffer[:n], err)
	}
}
