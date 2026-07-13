package reference

import (
	"bytes"
	"context"
	"encoding/hex"
	"errors"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"sync"
	"testing"
	"time"

	"github.com/rupret007/webjam/transport/internal/icequic"
	"github.com/rupret007/webjam/transport/internal/sessionauth"
	"github.com/rupret007/webjam/transport/internal/wire"
)

func TestNativeClientAgainstIndependentReferenceService(t *testing.T) {
	stopService := startIndependentReferenceService(t)
	defer stopService()
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	hostClient, err := DialLocal(ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer hostClient.Close()
	guestClient, err := DialLocal(ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer guestClient.Close()
	hostToken, err := NewRoleToken()
	if err != nil {
		t.Fatal(err)
	}
	defer hostToken.Destroy()
	guestToken, err := NewRoleToken()
	if err != nil {
		t.Fatal(err)
	}
	defer guestToken.Destroy()
	session := filledSession(31)
	capability := filledCapability(32)
	enrollment, err := DeriveEnrollmentToken(capability, session)
	if err != nil {
		t.Fatal(err)
	}
	defer enrollment.Destroy()
	const generation = 7

	// Register returning is the synchronous host-waiting boundary. It is not a
	// peer-connected or audio-ready claim.
	if err = hostClient.Register(
		ctx, session, hostToken, enrollment, generation, time.Minute,
	); err != nil {
		t.Fatal(err)
	}
	if err = guestClient.Enroll(ctx, session, enrollment, guestToken); err != nil {
		t.Fatal(err)
	}
	secondGuestClient, err := DialLocal(ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer secondGuestClient.Close()
	secondGuestToken, err := NewRoleToken()
	if err != nil {
		t.Fatal(err)
	}
	defer secondGuestToken.Destroy()
	if err = secondGuestClient.Enroll(
		ctx, session, enrollment, secondGuestToken,
	); !errors.Is(err, ErrEnrollmentUsed) {
		t.Fatalf("second guest enrollment error = %v", err)
	}

	now := time.Now().UTC().Truncate(time.Second)
	hostIdentity, err := icequic.NewEphemeralIdentity(now, time.Hour)
	if err != nil {
		t.Fatal(err)
	}
	defer hostIdentity.Destroy()
	guestIdentity, err := icequic.NewEphemeralIdentity(now, time.Hour)
	if err != nil {
		t.Fatal(err)
	}
	defer guestIdentity.Destroy()
	hostPin := PeerPin(hostIdentity.SPKIFingerprint)
	guestPin := PeerPin(guestIdentity.SPKIFingerprint)
	guestNonce, err := NewBootstrapNonce()
	if err != nil {
		t.Fatal(err)
	}
	guestEnvelope, err := SealGuestBootstrap(capability, GuestBootstrap{
		SessionID: session, Generation: generation, HostPin: hostPin, GuestPin: guestPin,
		Nonce: guestNonce, ExpiresAt: now.Add(time.Minute),
		CertificateDER: guestIdentity.Certificate.Certificate[0],
	}, now)
	if err != nil {
		t.Fatal(err)
	}
	if err = guestClient.Signal(
		ctx, session, RoleGuest, guestToken, generation, 1, guestEnvelope,
	); err != nil {
		t.Fatal(err)
	}
	receivedGuest, ok, err := hostClient.Poll(
		ctx, session, RoleHost, hostToken, generation, 1,
	)
	if err != nil || !ok {
		t.Fatalf("host bootstrap poll = %v, %v", ok, err)
	}
	openedGuest, err := OpenGuestBootstrap(
		capability,
		GuestBootstrapExpected{SessionID: session, Generation: generation, HostPin: hostPin},
		receivedGuest, now, NewBootstrapReplayCache(),
	)
	if err != nil || openedGuest.GuestPin != guestPin {
		t.Fatalf("open guest bootstrap = %v", err)
	}
	ackNonce, err := NewBootstrapNonce()
	if err != nil {
		t.Fatal(err)
	}
	ackEnvelope, err := SealHostAcknowledgment(capability, HostAcknowledgment{
		SessionID: session, Generation: generation, HostPin: hostPin, GuestPin: guestPin,
		GuestNonce: guestNonce, Acknowledgment: ackNonce, ExpiresAt: now.Add(time.Minute),
	}, now)
	if err != nil {
		t.Fatal(err)
	}
	if err = hostClient.Signal(
		ctx, session, RoleHost, hostToken, generation, 2, ackEnvelope,
	); err != nil {
		t.Fatal(err)
	}
	receivedAck, ok, err := guestClient.Poll(
		ctx, session, RoleGuest, guestToken, generation, 2,
	)
	if err != nil || !ok {
		t.Fatalf("guest acknowledgment poll = %v, %v", ok, err)
	}
	if _, err = OpenHostAcknowledgment(
		capability,
		HostAcknowledgmentExpected{
			SessionID: session, Generation: generation, HostPin: hostPin,
			GuestPin: guestPin, GuestNonce: guestNonce,
		},
		receivedAck, now, NewBootstrapReplayCache(),
	); err != nil {
		t.Fatal(err)
	}

	hostRelay, err := OpenRelayLocal(session, RoleHost, hostToken, generation)
	if err != nil {
		t.Fatal(err)
	}
	defer hostRelay.Close()
	guestRelay, err := OpenRelayLocal(session, RoleGuest, guestToken, generation)
	if err != nil {
		t.Fatal(err)
	}
	defer guestRelay.Close()
	assertRelayRoundTrip(t, hostRelay, guestRelay, []byte("host-to-guest-ciphertext"))
	assertRelayRoundTrip(t, guestRelay, hostRelay, []byte("guest-to-host-ciphertext"))
	assertRelayQUICAuthenticated(
		t, ctx, hostRelay, guestRelay, hostIdentity, guestIdentity,
		session, capability, generation, hostPin, guestPin, now,
	)
	if err = hostRelay.Close(); err != nil {
		t.Fatal(err)
	}
	if err = guestRelay.Close(); err != nil {
		t.Fatal(err)
	}
	if err = hostClient.CloseSession(
		ctx, session, RoleHost, hostToken, generation, 3,
	); err != nil {
		t.Fatal(err)
	}
}

func assertRelayQUICAuthenticated(
	t *testing.T,
	ctx context.Context,
	hostRelay, guestRelay *RelayPacketConn,
	hostIdentity, guestIdentity icequic.Identity,
	session SessionID,
	capability Capability,
	generation uint32,
	hostPin, guestPin PeerPin,
	now time.Time,
) {
	t.Helper()
	if err := hostRelay.SetDeadline(time.Time{}); err != nil {
		t.Fatal(err)
	}
	if err := guestRelay.SetDeadline(time.Time{}); err != nil {
		t.Fatal(err)
	}
	listener, err := icequic.Listen(hostRelay, hostIdentity)
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	type acceptResult struct {
		connection *icequic.Connection
		err        error
	}
	accepted := make(chan acceptResult, 1)
	go func() {
		connection, acceptErr := listener.Accept(ctx)
		accepted <- acceptResult{connection: connection, err: acceptErr}
	}()
	guestConnection, err := icequic.Dial(
		ctx, guestRelay, guestRelay.PeerAddr(), guestIdentity, hex.EncodeToString(hostPin[:]),
	)
	if err != nil {
		t.Fatal(err)
	}
	defer guestConnection.CloseWithError(0, "")
	hostResult := <-accepted
	if hostResult.err != nil {
		t.Fatal(hostResult.err)
	}
	hostConnection := hostResult.connection
	defer hostConnection.CloseWithError(0, "")
	assertQUICQuarantined(t, ctx, "guest before proof", guestConnection)
	assertQUICQuarantined(t, ctx, "host before proof", hostConnection)

	authCapability := sessionauth.Capability(capability)
	authSession := sessionauth.SessionID(session)
	authHostPin := sessionauth.PeerPin(hostPin)
	authGuestPin := sessionauth.PeerPin(guestPin)
	expiresAt := now.Add(time.Minute)
	guestBinding := sessionauth.Binding{
		SessionID: authSession, SenderRole: sessionauth.RoleGuest, Generation: generation,
		HostPin: authHostPin, GuestPin: authGuestPin,
		Nonce: sessionauth.Nonce{1}, ExpiresAt: expiresAt,
	}
	guestProof, err := sessionauth.CreateProof(
		guestConnection, authCapability, guestBinding, now,
	)
	if err != nil {
		t.Fatal(err)
	}
	if err = hostConnection.VerifyAndAuthorize(
		authCapability, guestBinding, guestProof, now, sessionauth.NewReplayCache(),
	); err != nil {
		t.Fatal(err)
	}
	assertQUICQuarantined(t, ctx, "guest before host proof", guestConnection)
	hostBinding := sessionauth.Binding{
		SessionID: authSession, SenderRole: sessionauth.RoleHost, Generation: generation,
		HostPin: authHostPin, GuestPin: authGuestPin,
		Nonce: sessionauth.Nonce{2}, ExpiresAt: expiresAt,
	}
	hostProof, err := sessionauth.CreateProof(hostConnection, authCapability, hostBinding, now)
	if err != nil {
		t.Fatal(err)
	}
	if err = guestConnection.VerifyAndAuthorize(
		authCapability, hostBinding, hostProof, now, sessionauth.NewReplayCache(),
	); err != nil {
		t.Fatal(err)
	}
	if err = guestConnection.SendDatagram([]byte("authenticated-guest-live")); err != nil {
		t.Fatal(err)
	}
	received, err := hostConnection.ReceiveDatagram(ctx)
	if err != nil || string(received) != "authenticated-guest-live" {
		t.Fatalf("host live datagram = %q, %v", received, err)
	}
	if err = hostConnection.SendDatagram([]byte("authenticated-host-live")); err != nil {
		t.Fatal(err)
	}
	received, err = guestConnection.ReceiveDatagram(ctx)
	if err != nil || string(received) != "authenticated-host-live" {
		t.Fatalf("guest live datagram = %q, %v", received, err)
	}
	assertReliableRoundTrip(
		t, ctx, guestConnection, hostConnection, generation, []byte("guest-reliable-frame"),
	)
	assertReliableRoundTrip(
		t, ctx, hostConnection, guestConnection, generation, []byte("host-reliable-frame"),
	)
}

func assertQUICQuarantined(
	t *testing.T, ctx context.Context, label string, connection *icequic.Connection,
) {
	t.Helper()
	if err := connection.SendDatagram([]byte("must remain quarantined")); !errors.Is(err, sessionauth.ErrQuarantined) {
		t.Fatalf("%s datagram error = %v", label, err)
	}
	if _, err := connection.OpenStreamSync(ctx); !errors.Is(err, sessionauth.ErrQuarantined) {
		t.Fatalf("%s stream error = %v", label, err)
	}
}

func assertReliableRoundTrip(
	t *testing.T,
	ctx context.Context,
	sender, receiver *icequic.Connection,
	generation uint32,
	payload []byte,
) {
	t.Helper()
	sendPlane, err := icequic.NewReliablePlane(sender)
	if err != nil {
		t.Fatal(err)
	}
	receivePlane, err := icequic.NewReliablePlane(receiver)
	if err != nil {
		t.Fatal(err)
	}
	type receiveResult struct {
		frame wire.StreamFrame
		err   error
	}
	received := make(chan receiveResult, 1)
	go func() {
		frame, acceptErr := receivePlane.Accept(ctx)
		received <- receiveResult{frame: frame, err: acceptErr}
	}()
	if err = sendPlane.Send(ctx, wire.StreamFrame{
		Kind: wire.StreamKindControl, Generation: generation, Payload: payload,
	}); err != nil {
		t.Fatal(err)
	}
	result := <-received
	if result.err != nil || result.frame.Kind != wire.StreamKindControl ||
		result.frame.Generation != generation || !bytes.Equal(result.frame.Payload, payload) {
		t.Fatalf("reliable frame = %+v, %v", result.frame, result.err)
	}
}

func assertRelayRoundTrip(
	t *testing.T, sender, receiver *RelayPacketConn, payload []byte,
) {
	t.Helper()
	buffer := make([]byte, MaxRelayPayloadBytes)
	for attempt := 0; attempt < 5; attempt++ {
		if _, err := sender.WriteTo(payload, sender.PeerAddr()); err != nil {
			t.Fatal(err)
		}
		if err := receiver.SetReadDeadline(time.Now().Add(100 * time.Millisecond)); err != nil {
			t.Fatal(err)
		}
		n, peer, err := receiver.ReadFrom(buffer)
		if err == nil {
			if peer != receiver.PeerAddr() || !bytes.Equal(buffer[:n], payload) {
				t.Fatal("relay changed peer or ciphertext")
			}
			return
		}
		var networkError net.Error
		if !errors.As(err, &networkError) || !networkError.Timeout() {
			t.Fatal(err)
		}
	}
	t.Fatal("relay peer did not become ready within bounded retries")
}

type boundedProcessLog struct {
	mu     sync.Mutex
	buffer bytes.Buffer
}

func (b *boundedProcessLog) Write(payload []byte) (int, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	const maximum = 4_096
	remaining := maximum - b.buffer.Len()
	if remaining > 0 {
		if len(payload) < remaining {
			remaining = len(payload)
		}
		_, _ = b.buffer.Write(payload[:remaining])
	}
	return len(payload), nil
}

func (b *boundedProcessLog) String() string {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.buffer.String()
}

func startIndependentReferenceService(t *testing.T) func() {
	t.Helper()
	releaseLock := acquireReferenceServicePortLock(t)
	var releaseLockOnce sync.Once
	unlock := func() { releaseLockOnce.Do(releaseLock) }
	t.Cleanup(unlock)
	repositoryRoot, err := filepath.Abs(filepath.Join("..", "..", ".."))
	if err != nil {
		t.Fatal("reference service root unavailable")
	}
	python := findPython(repositoryRoot)
	if python == "" {
		t.Skip("Python 3.10+ is unavailable for the reference-service integration")
	}
	command := exec.Command(python, "-m", "webjam_reference")
	command.Dir = filepath.Join(repositoryRoot, "reference_service")
	logs := &boundedProcessLog{}
	command.Stdout = logs
	command.Stderr = logs
	if err = command.Start(); err != nil {
		unlock()
		t.Fatal("reference service process failed to start")
	}
	done := make(chan struct{})
	go func() {
		_ = command.Wait()
		close(done)
	}()
	var once sync.Once
	stop := func() {
		once.Do(func() {
			if command.Process != nil {
				if err := command.Process.Signal(os.Interrupt); err != nil {
					_ = command.Process.Kill()
				}
			}
			select {
			case <-done:
			case <-time.After(2 * time.Second):
				_ = command.Process.Kill()
				<-done
			}
			unlock()
		})
	}
	t.Cleanup(stop)
	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) {
		select {
		case <-done:
			t.Fatalf("reference service exited before readiness: %s", logs.String())
		default:
		}
		connection, dialErr := net.DialTimeout("tcp4", ControlAddress, 100*time.Millisecond)
		if dialErr == nil {
			_ = connection.Close()
			return stop
		}
		time.Sleep(20 * time.Millisecond)
	}
	stop()
	t.Fatalf("reference service did not become ready: %s", logs.String())
	return stop
}

func acquireReferenceServicePortLock(t *testing.T) func() {
	t.Helper()
	lockPath := filepath.Join(os.TempDir(), "webjam-reference-v3-fixed-ports.lock")
	deadline := time.Now().Add(30 * time.Second)
	for {
		err := os.Mkdir(lockPath, 0o700)
		if err == nil {
			return func() { _ = os.Remove(lockPath) }
		}
		if !errors.Is(err, os.ErrExist) {
			t.Fatalf("reference-service test lock unavailable: %v", err)
		}
		if info, statErr := os.Stat(lockPath); statErr == nil &&
			time.Since(info.ModTime()) > 2*time.Minute {
			_ = os.Remove(lockPath)
			continue
		}
		if !time.Now().Before(deadline) {
			t.Fatal("timed out waiting for reference-service fixed ports")
		}
		time.Sleep(25 * time.Millisecond)
	}
}

func findPython(repositoryRoot string) string {
	candidates := []string{
		filepath.Join(repositoryRoot, ".venv", "bin", "python"),
		"python3",
		"python",
	}
	if runtime.GOOS == "windows" {
		candidates = append([]string{
			filepath.Join(repositoryRoot, ".venv", "Scripts", "python.exe"),
		}, candidates...)
	}
	for _, candidate := range candidates {
		path := candidate
		if !filepath.IsAbs(path) {
			resolved, err := exec.LookPath(path)
			if err != nil {
				continue
			}
			path = resolved
		}
		probe := exec.Command(
			path, "-c", "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)",
		)
		if probe.Run() == nil {
			return path
		}
	}
	return ""
}
