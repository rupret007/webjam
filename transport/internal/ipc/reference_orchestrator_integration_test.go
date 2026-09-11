package ipc

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/rupret007/webjam/transport/internal/icequic"
	"github.com/rupret007/webjam/transport/internal/loopback"
	"github.com/rupret007/webjam/transport/internal/profile"
)

func TestRunnerHostAndGuestThroughIndependentReferenceRelay(t *testing.T) {
	stopService := startIPCReferenceService(t)
	defer stopService()

	jamulus, err := net.ListenUDP("udp4", &net.UDPAddr{IP: net.IPv4(127, 0, 0, 1), Port: 0})
	if err != nil {
		t.Fatal(err)
	}
	defer jamulus.Close()

	now := time.Now().UTC().Truncate(time.Second)
	hostFailures := make(chan string, 1)
	guestFailures := make(chan string, 1)
	hostOrchestrator := captureReferenceOperations(&referenceFabricOrchestrator{
		now: time.Now,
		observe: func(stage string, err error) {
			hostFailures <- stage + ": " + err.Error()
		},
	})
	guestOrchestrator := captureReferenceOperations(&referenceFabricOrchestrator{
		now: time.Now,
		observe: func(stage string, err error) {
			guestFailures <- stage + ": " + err.Error()
		},
	})
	host := newRunnerHarnessWithTimeout(
		t, lifetimeEndpointFactory{}, hostOrchestrator, now,
		15*time.Second, 8*time.Second,
	)
	defer host.stop(t)
	guest := newRunnerHarnessWithTimeout(
		t, systemEndpointFactory{}, guestOrchestrator, now,
		15*time.Second, 8*time.Second,
	)
	defer guest.stop(t)
	if event := host.next(t); event.Type != "ready" {
		t.Fatalf("host ready = %+v", event)
	}
	if event := guest.next(t); event.Type != "ready" {
		t.Fatalf("guest ready = %+v", event)
	}

	host.send(t, `{"version":1,"id":1,"type":"prepare_host"}`)
	prepared := host.next(t)
	if prepared.Type != "host_prepared" || len(prepared.HostSPKISHA256) != 43 {
		t.Fatalf("host prepared = %+v", prepared)
	}
	expiresAt := now.Add(2 * time.Minute)
	hostFields := openFields(
		2, "host", profile.ReferenceLocalID, expiresAt,
		jamulus.LocalAddr().(*net.UDPAddr).Port, "",
	)
	host.send(t, string(mustJSON(t, hostFields)))
	registered := host.next(t)
	if registered.Type != "host_registered" || registered.State != "host_waiting" || registered.ID != 2 {
		t.Fatalf("host registration boundary = %+v", registered)
	}

	guestFields := openFields(2, "guest", profile.ReferenceLocalID, expiresAt, 0, prepared.HostSPKISHA256)
	guest.send(t, string(mustJSON(t, guestFields)))
	guestConnected := guest.next(t)
	if guestConnected.Type != "peer_connected" || guestConnected.State != "connected" ||
		guestConnected.Mode != "guest" || guestConnected.ID != 2 || guestConnected.LoopbackPort == 0 {
		t.Fatalf(
			"guest authenticated boundary = %+v; guest_stage=%s host_stage=%s",
			guestConnected, observedFailure(guestFailures), observedFailure(hostFailures),
		)
	}
	hostConnected := host.next(t)
	if hostConnected.Type != "peer_connected" || hostConnected.State != "connected" ||
		hostConnected.Mode != "host" || hostConnected.ID != 0 {
		t.Fatalf("host authenticated boundary = %+v; stage=%s", hostConnected, observedFailure(hostFailures))
	}
	hostOperation := hostOrchestrator.next(t)
	guestOperation := guestOrchestrator.next(t)
	assertEstablishedOperationContexts(t, hostOperation)
	assertEstablishedOperationContexts(t, guestOperation)
	hostOperation.resourceMu.Lock()
	originalControl := hostOperation.client
	hostOperation.resourceMu.Unlock()
	if originalControl == nil {
		t.Fatal("connected host has no original reference control client")
	}
	// Model a control connection that idled out, without a thirty-second wait.
	// Room/help and datagram exchange must continue on the authenticated QUIC
	// connection, and final host removal will need a fresh control connection.
	if err = originalControl.Close(); err != nil {
		t.Fatal("could not close the original host control client")
	}
	beforeClose := referenceLifetimeDiagnostics(t)
	if beforeClose.Sessions.Active != 1 || beforeClose.Sessions.Enrolled != 1 ||
		beforeClose.Totals.Registered != 1 || beforeClose.Totals.Closed != 0 {
		t.Fatal("isolated reference service did not report exactly the enrolled room")
	}
	// These are real proof/room-handshake completions. All room, help and live
	// datagram assertions below now run after both enrollment children ended.
	// The harness parent expires before the invitation; no claim about a longer
	// operation deadline follows from this short integration test.

	// Art follow works before either app sends Session help. This is a full
	// initial snapshot of already-shared video/canvas, not a presence-only badge.
	initialRoom := ipcRoomState()
	host.send(t, roomCommand(t, 3, 7, initialRoom))
	if accepted := host.next(t); accepted.Type != "room_state_accepted" || accepted.RequestID != 3 {
		t.Fatal("initial room state not accepted")
	}
	initialReceived := guest.next(t)
	if initialReceived.Type != "room_state_received" || initialReceived.RoomState == nil || *initialReceived.RoomState != *initialRoom {
		t.Fatal("initial host Art state did not reach the guest")
	}
	host.send(t, roomCommand(t, 4, 7, initialRoom))
	if replay := host.next(t); replay.Type != "error" || replay.Code != CodeRoomInvalid {
		t.Fatal("replayed host revision accepted")
	}
	guest.send(t, roomCommand(t, 3, 7, initialRoom))
	if rejected := guest.next(t); rejected.Type != "error" || rejected.Code != CodeRoomInvalid {
		t.Fatal("guest published host Art state")
	}
	initialRoom.Revision = 2
	initialRoom.ReferenceVideo.PositionS = 5
	host.send(t, roomCommand(t, 5, 7, initialRoom))
	if accepted := host.next(t); accepted.Type != "room_state_accepted" {
		t.Fatal("follow refresh rejected")
	}
	if received := guest.next(t); received.Type != "room_state_received" || received.RoomState.ReferenceVideo.PositionS != 5 {
		t.Fatal("host playback refresh lost")
	}

	host.send(t, `{"version":1,"id":10,"type":"send_help","generation":7,"text":"Try headphones — café"}`)
	hostAccepted := host.next(t)
	if hostAccepted.Type != "help_accepted" || hostAccepted.ID != 10 ||
		hostAccepted.RequestID != 10 || hostAccepted.Text != "" {
		t.Fatalf("host help acceptance = %+v", hostAccepted)
	}
	guestReceived := guest.next(t)
	if guestReceived.Type != "help_received" || guestReceived.ID != 0 ||
		guestReceived.RequestID != 10 || guestReceived.Text != "Try headphones — café" {
		t.Fatalf("guest did not receive the authenticated help message")
	}
	hostDelivered := host.next(t)
	if hostDelivered.Type != "help_delivered" || hostDelivered.ID != 0 ||
		hostDelivered.RequestID != 10 || hostDelivered.Text != "" {
		t.Fatalf("host help delivery receipt = %+v", hostDelivered)
	}

	guest.send(t, `{"version":1,"id":10,"type":"send_help","generation":7,"text":"I can hear you now"}`)
	guestAccepted := guest.next(t)
	if guestAccepted.Type != "help_accepted" || guestAccepted.ID != 10 ||
		guestAccepted.RequestID != 10 || guestAccepted.Text != "" {
		t.Fatalf("guest help acceptance = %+v", guestAccepted)
	}
	hostReceived := host.next(t)
	if hostReceived.Type != "help_received" || hostReceived.ID != 0 ||
		hostReceived.RequestID != 10 || hostReceived.Text != "I can hear you now" {
		t.Fatalf("host did not receive the authenticated help message")
	}
	guestDelivered := guest.next(t)
	if guestDelivered.Type != "help_delivered" || guestDelivered.ID != 0 ||
		guestDelivered.RequestID != 10 || guestDelivered.Text != "" {
		t.Fatalf("guest help delivery receipt = %+v", guestDelivered)
	}

	guestJamulus, err := net.DialUDP(
		"udp4", nil,
		&net.UDPAddr{IP: net.IPv4(127, 0, 0, 1), Port: guestConnected.LoopbackPort},
	)
	if err != nil {
		t.Fatal(err)
	}
	defer guestJamulus.Close()
	guestPayload := []byte("guest-to-host-live-after-auth")
	if _, err = guestJamulus.Write(guestPayload); err != nil {
		t.Fatal(err)
	}
	if err = jamulus.SetReadDeadline(time.Now().Add(3 * time.Second)); err != nil {
		t.Fatal(err)
	}
	buffer := make([]byte, 1024)
	n, hostProxyAddress, err := jamulus.ReadFromUDP(buffer)
	if err != nil || !bytes.Equal(buffer[:n], guestPayload) {
		t.Fatalf("host received %q: %v", buffer[:n], err)
	}
	hostPayload := []byte("host-to-guest-live-after-auth")
	if _, err = jamulus.WriteToUDP(hostPayload, hostProxyAddress); err != nil {
		t.Fatal(err)
	}
	if err = guestJamulus.SetReadDeadline(time.Now().Add(3 * time.Second)); err != nil {
		t.Fatal(err)
	}
	n, err = guestJamulus.Read(buffer)
	if err != nil || !bytes.Equal(buffer[:n], hostPayload) {
		t.Fatalf("guest received %q: %v", buffer[:n], err)
	}

	// Hold the real endpoint's canceled read at the pump boundary. Explicit
	// close must not report operation completion while that worker remains.
	hostEndpoint, ok := hostOperation.endpoint.(*lifetimeEndpoint)
	if !ok {
		t.Fatal("host endpoint observation was not installed")
	}
	defer hostEndpoint.allowReadExit()
	hostEndpoint.holdReadExit.Store(true)
	host.send(t, `{"version":1,"id":11,"type":"close_peer"}`)
	waitLifetimeSignal(t, hostEndpoint.readCanceled, "canceled host read")
	// A concurrent, short Close must exhaust its own budget while the real
	// read worker is held. Merely checking done immediately after cancellation
	// could pass because cleanup had not been scheduled yet.
	blockedCloseCtx, cancelBlockedClose := context.WithTimeout(context.Background(), 50*time.Millisecond)
	blockedCloseErr := hostOperation.Close(blockedCloseCtx)
	cancelBlockedClose()
	if blockedCloseErr == nil || blockedCloseCtx.Err() != context.DeadlineExceeded {
		t.Fatal("Close finished instead of waiting for the held live worker")
	}
	select {
	case <-hostOperation.done:
		t.Fatal("operation reported done before the live read worker returned")
	default:
	}
	select {
	case _, open := <-hostOperation.updates:
		if !open {
			t.Fatal("updates closed before the live read worker returned")
		}
		t.Fatal("unexpected update while explicit close is joining the worker")
	default:
	}
	hostEndpoint.allowReadExit()
	if closed := host.next(t); closed.Type != "peer_closed" || closed.ID != 11 {
		t.Fatalf("host close boundary = %+v", closed)
	}
	assertClosedReferenceOperation(t, hostOperation)
	afterClose := referenceLifetimeDiagnostics(t)
	if afterClose.Sessions.Active != 0 || afterClose.Totals.Closed != 1 || afterClose.Totals.Expired != 0 {
		t.Fatal("fresh authenticated close did not remove the isolated service room")
	}
	select {
	case <-hostEndpoint.readExited:
	default:
		t.Fatal("peer_closed preceded the observed worker's return")
	}
	// Local close retains the prepared identity. A fresh invitation can
	// register without rotating its pin; no missing service acknowledgment
	// should be mistaken for proof of remote state removal.
	resetFields := openFields(
		12, "host", profile.ReferenceLocalID, expiresAt,
		jamulus.LocalAddr().(*net.UDPAddr).Port, "",
	)
	// Reset Invite preserves the logical session reference, but rotates the
	// invitation reference and capability. The derived service session must
	// therefore evade the closed invitation's replay tombstone.
	resetFields["invite_reference"] = fixedBase64(16, 8)
	resetFields["enrollment_capability"] = fixedBase64(32, 9)
	resetFields["generation"] = uint32(8)
	host.send(t, string(mustJSON(t, resetFields)))
	resetRegistered := host.next(t)
	if resetRegistered.Type != "host_registered" || resetRegistered.ID != 12 ||
		resetRegistered.Generation != 8 || resetRegistered.State != "host_waiting" {
		t.Fatalf("host reset registration = %+v", resetRegistered)
	}
	resetHostOperation := hostOrchestrator.next(t)
	// A new desktop knows only the invitation, not the host's local counter.
	// Its local generation starts at one while the host is at eight. Their
	// IPC facts retain those unequal local generations; the authenticated wire
	// epoch belongs to this one-use invitation. Old local commands still fail.
	resetGuestOrchestrator := captureReferenceOperations(&referenceFabricOrchestrator{now: time.Now})
	resetGuest := newRunnerHarnessWithTimeout(t, systemEndpointFactory{}, resetGuestOrchestrator, now, 15*time.Second, 8*time.Second)
	defer resetGuest.stop(t)
	resetGuest.next(t)
	resetGuestFields := openFields(2, "guest", profile.ReferenceLocalID, expiresAt, 0, prepared.HostSPKISHA256)
	resetGuestFields["invite_reference"] = fixedBase64(16, 8)
	resetGuestFields["enrollment_capability"] = fixedBase64(32, 9)
	resetGuestFields["generation"] = uint32(1)
	resetGuest.send(t, string(mustJSON(t, resetGuestFields)))
	if connected := resetGuest.next(t); connected.Type != "peer_connected" || connected.Generation != 1 {
		t.Fatal("fresh guest did not authenticate")
	}
	if connected := host.next(t); connected.Type != "peer_connected" || connected.Generation != 8 {
		t.Fatal("host did not adopt reset generation")
	}
	resetGuestOperation := resetGuestOrchestrator.next(t)
	assertEstablishedOperationContexts(t, resetHostOperation)
	assertEstablishedOperationContexts(t, resetGuestOperation)
	host.send(t, roomCommand(t, 15, 7, ipcRoomState()))
	if stale := host.next(t); stale.Code != CodeRoomNotReady {
		t.Fatal("old generation published after reset")
	}
	host.send(t, roomCommand(t, 16, 8, ipcRoomState()))
	if accepted := host.next(t); accepted.Type != "room_state_accepted" {
		t.Fatal("fresh generation retained stale room revision")
	}
	if received := resetGuest.next(t); received.Type != "room_state_received" || received.Generation != 1 || received.RoomState.Revision != 1 {
		t.Fatal("reset room state not delivered")
	}
	host.send(t, `{"version":1,"id":13,"type":"close_peer"}`)
	if closed := host.next(t); closed.Type != "peer_closed" || closed.ID != 13 {
		t.Fatalf("reset close boundary = %+v", closed)
	}
	assertClosedReferenceOperation(t, resetHostOperation)
	afterResetClose := referenceLifetimeDiagnostics(t)
	if afterResetClose.Sessions.Active != 0 || afterResetClose.Totals.Registered != 2 ||
		afterResetClose.Totals.Closed != 2 || afterResetClose.Totals.Expired != 0 {
		t.Fatal("reset room did not receive its own authenticated service close")
	}

	resetGuest.send(t, `{"version":1,"id":3,"type":"shutdown"}`)
	waitForRunnerEvent(t, resetGuest, "stopped")
	waitLifetimeSignal(t, resetGuestOperation.done, "reset guest operation cleanup")

	// Shutdown remains bounded even though the guest observes the host-side
	// connection close independently.
	host.send(t, `{"version":1,"id":14,"type":"shutdown"}`)
	guest.send(t, `{"version":1,"id":11,"type":"shutdown"}`)
	waitForRunnerEvent(t, host, "stopped")
	waitForRunnerEvent(t, guest, "stopped")
	waitLifetimeSignal(t, guestOperation.done, "original guest operation cleanup")
}

// Capture the real production operation without replacing any authentication,
// signaling, service, QUIC or room-channel behavior.
type referenceOperationCapture struct {
	delegate *referenceFabricOrchestrator
	started  chan *referenceFabricOperation
}

func captureReferenceOperations(delegate *referenceFabricOrchestrator) *referenceOperationCapture {
	return &referenceOperationCapture{delegate: delegate, started: make(chan *referenceFabricOperation, 2)}
}

func (c *referenceOperationCapture) Start(
	ctx context.Context,
	configuration *enrollmentConfig,
	identity *icequic.Identity,
	endpoint loopback.Endpoint,
) (fabricOperation, error) {
	operation, err := c.delegate.Start(ctx, configuration, identity, endpoint)
	if err == nil {
		c.started <- operation.(*referenceFabricOperation)
	}
	return operation, err
}

func (c *referenceOperationCapture) next(t *testing.T) *referenceFabricOperation {
	t.Helper()
	select {
	case operation := <-c.started:
		return operation
	case <-time.After(2 * time.Second):
		t.Fatal("production operation was not captured")
		return nil
	}
}

func assertEstablishedOperationContexts(t *testing.T, operation *referenceFabricOperation) {
	t.Helper()
	if operation.enrollmentCtx.Err() != context.Canceled {
		t.Fatal("authenticated room retained its pending enrollment context")
	}
	if operation.ctx.Err() != nil {
		t.Fatal("completing enrollment canceled the established operation")
	}
	select {
	case <-operation.done:
		t.Fatal("authenticated operation had already ended")
	default:
	}
}

func assertClosedReferenceOperation(t *testing.T, operation *referenceFabricOperation) {
	t.Helper()
	select {
	case <-operation.done:
	default:
		t.Fatal("peer_closed preceded operation completion")
	}
	if operation.ctx.Err() == nil || operation.enrollmentCtx.Err() == nil {
		t.Fatal("closed operation retained a live context")
	}
	select {
	case _, open := <-operation.updates:
		if open {
			t.Fatal("completed operation retained an unconsumed lifecycle update")
		}
	default:
		t.Fatal("completed operation retained an open updates channel")
	}
}

func waitLifetimeSignal(t *testing.T, signal <-chan struct{}, description string) {
	t.Helper()
	select {
	case <-signal:
	case <-time.After(2 * time.Second):
		t.Fatalf("timed out waiting for %s", description)
	}
}

type referenceLifetimeCounts struct {
	Sessions struct {
		Active   int `json:"active"`
		Enrolled int `json:"enrolled"`
	} `json:"sessions"`
	Totals struct {
		Registered int `json:"sessions_registered"`
		Closed     int `json:"sessions_closed"`
		Expired    int `json:"sessions_expired"`
	} `json:"totals"`
}

func referenceLifetimeDiagnostics(t *testing.T) referenceLifetimeCounts {
	t.Helper()
	transport := &http.Transport{Proxy: nil}
	defer transport.CloseIdleConnections()
	client := &http.Client{
		Transport: transport, Timeout: 2 * time.Second,
		CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse },
	}
	response, err := client.Get("http://127.0.0.1:47133/diagnostics")
	if err != nil {
		t.Fatal("isolated reference diagnostics unavailable")
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		t.Fatal("isolated reference diagnostics did not succeed")
	}
	var counts referenceLifetimeCounts
	if err = json.NewDecoder(io.LimitReader(response.Body, 4096)).Decode(&counts); err != nil {
		t.Fatal("isolated reference aggregate counts were invalid")
	}
	return counts
}

type lifetimeEndpointFactory struct{}

func (lifetimeEndpointFactory) Open(mode string, port int) (loopback.Endpoint, error) {
	endpoint, err := (systemEndpointFactory{}).Open(mode, port)
	if err != nil {
		return nil, err
	}
	return &lifetimeEndpoint{
		Endpoint: endpoint, readCanceled: make(chan struct{}),
		readRelease: make(chan struct{}), readExited: make(chan struct{}),
	}, nil
}

type lifetimeEndpoint struct {
	loopback.Endpoint
	holdReadExit atomic.Bool
	readCanceled chan struct{}
	readRelease  chan struct{}
	readExited   chan struct{}
	readOnce     sync.Once
	releaseOnce  sync.Once
}

func (e *lifetimeEndpoint) ReadDatagram(ctx context.Context) ([]byte, error) {
	payload, err := e.Endpoint.ReadDatagram(ctx)
	if err != nil && e.holdReadExit.Load() {
		e.readOnce.Do(func() {
			close(e.readCanceled)
			<-e.readRelease
			close(e.readExited)
		})
	}
	return payload, err
}

func (e *lifetimeEndpoint) allowReadExit() {
	e.releaseOnce.Do(func() { close(e.readRelease) })
}

func observedFailure(failures <-chan string) string {
	select {
	case failure := <-failures:
		return failure
	default:
		return "none"
	}
}

func waitForRunnerEvent(t *testing.T, harness *runnerHarness, eventType string) Event {
	t.Helper()
	for range 4 {
		event := harness.next(t)
		if event.Type == eventType {
			return event
		}
		if event.Type != "error" {
			t.Fatalf("unexpected event while waiting for %s: %+v", eventType, event)
		}
	}
	t.Fatalf("missing %s event", eventType)
	return Event{}
}

type boundedIPCProcessLog struct {
	mu     sync.Mutex
	buffer bytes.Buffer
}

func (b *boundedIPCProcessLog) Write(payload []byte) (int, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	remaining := 4_096 - b.buffer.Len()
	if remaining > 0 {
		if len(payload) < remaining {
			remaining = len(payload)
		}
		_, _ = b.buffer.Write(payload[:remaining])
	}
	return len(payload), nil
}

func (b *boundedIPCProcessLog) String() string {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.buffer.String()
}

func startIPCReferenceService(t *testing.T) func() {
	t.Helper()
	releaseLock := acquireReferencePortLock(t)
	repositoryRoot, err := filepath.Abs(filepath.Join("..", "..", ".."))
	if err != nil {
		releaseLock()
		t.Fatal("reference service root unavailable")
	}
	python := findIPCPython(repositoryRoot)
	if python == "" {
		releaseLock()
		t.Skip("Python 3.10+ is unavailable for the reference-service integration")
	}
	command := exec.Command(python, "-m", "webjam_reference")
	command.Dir = filepath.Join(repositoryRoot, "reference_service")
	logs := &boundedIPCProcessLog{}
	command.Stdout = logs
	command.Stderr = logs
	if err = command.Start(); err != nil {
		releaseLock()
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
				if signalErr := command.Process.Signal(os.Interrupt); signalErr != nil {
					_ = command.Process.Kill()
				}
			}
			select {
			case <-done:
			case <-time.After(2 * time.Second):
				_ = command.Process.Kill()
				<-done
			}
			releaseLock()
		})
	}
	t.Cleanup(stop)
	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) {
		select {
		case <-done:
			stop()
			t.Fatalf("reference service exited before readiness: %s", logs.String())
		default:
		}
		connection, dialErr := net.DialTimeout("tcp4", "127.0.0.1:47131", 100*time.Millisecond)
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

func acquireReferencePortLock(t *testing.T) func() {
	t.Helper()
	lockPath := filepath.Join(os.TempDir(), "webjam-reference-v3-fixed-ports.lock")
	deadline := time.Now().Add(30 * time.Second)
	for time.Now().Before(deadline) {
		if err := os.Mkdir(lockPath, 0o700); err == nil {
			return func() { _ = os.Remove(lockPath) }
		} else if !os.IsExist(err) {
			t.Fatalf("acquire reference port lock: %v", err)
		}
		if info, err := os.Stat(lockPath); err == nil && time.Since(info.ModTime()) > 2*time.Minute {
			_ = os.Remove(lockPath)
		}
		time.Sleep(25 * time.Millisecond)
	}
	t.Fatal("timed out acquiring reference port lock")
	return func() {}
}

func findIPCPython(repositoryRoot string) string {
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
