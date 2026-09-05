package ipc

import (
	"bytes"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"sync"
	"testing"
	"time"

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
	hostOrchestrator := &referenceFabricOrchestrator{
		now: time.Now,
		observe: func(stage string, err error) {
			hostFailures <- stage + ": " + err.Error()
		},
	}
	guestOrchestrator := &referenceFabricOrchestrator{
		now: time.Now,
		observe: func(stage string, err error) {
			guestFailures <- stage + ": " + err.Error()
		},
	}
	host := newRunnerHarnessWithTimeout(
		t, systemEndpointFactory{}, hostOrchestrator, now,
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

	host.send(t, `{"version":1,"id":3,"type":"send_help","generation":7,"text":"Try headphones — café"}`)
	hostAccepted := host.next(t)
	if hostAccepted.Type != "help_accepted" || hostAccepted.ID != 3 ||
		hostAccepted.RequestID != 3 || hostAccepted.Text != "" {
		t.Fatalf("host help acceptance = %+v", hostAccepted)
	}
	guestReceived := guest.next(t)
	if guestReceived.Type != "help_received" || guestReceived.ID != 0 ||
		guestReceived.RequestID != 3 || guestReceived.Text != "Try headphones — café" {
		t.Fatalf("guest did not receive the authenticated help message")
	}
	hostDelivered := host.next(t)
	if hostDelivered.Type != "help_delivered" || hostDelivered.ID != 0 ||
		hostDelivered.RequestID != 3 || hostDelivered.Text != "" {
		t.Fatalf("host help delivery receipt = %+v", hostDelivered)
	}

	guest.send(t, `{"version":1,"id":3,"type":"send_help","generation":7,"text":"I can hear you now"}`)
	guestAccepted := guest.next(t)
	if guestAccepted.Type != "help_accepted" || guestAccepted.ID != 3 ||
		guestAccepted.RequestID != 3 || guestAccepted.Text != "" {
		t.Fatalf("guest help acceptance = %+v", guestAccepted)
	}
	hostReceived := host.next(t)
	if hostReceived.Type != "help_received" || hostReceived.ID != 0 ||
		hostReceived.RequestID != 3 || hostReceived.Text != "I can hear you now" {
		t.Fatalf("host did not receive the authenticated help message")
	}
	guestDelivered := guest.next(t)
	if guestDelivered.Type != "help_delivered" || guestDelivered.ID != 0 ||
		guestDelivered.RequestID != 3 || guestDelivered.Text != "" {
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

	// Explicit close revokes the current host registration but retains the
	// prepared identity. A fresh transport invitation can register immediately
	// without rotating the pin out from under the desktop owner.
	host.send(t, `{"version":1,"id":4,"type":"close_peer"}`)
	if closed := host.next(t); closed.Type != "peer_closed" || closed.ID != 4 {
		t.Fatalf("host close boundary = %+v", closed)
	}
	resetFields := openFields(
		5, "host", profile.ReferenceLocalID, expiresAt,
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
	if resetRegistered.Type != "host_registered" || resetRegistered.ID != 5 ||
		resetRegistered.Generation != 8 || resetRegistered.State != "host_waiting" {
		t.Fatalf("host reset registration = %+v", resetRegistered)
	}
	host.send(t, `{"version":1,"id":6,"type":"close_peer"}`)
	if closed := host.next(t); closed.Type != "peer_closed" || closed.ID != 6 {
		t.Fatalf("reset close boundary = %+v", closed)
	}

	// Shutdown remains bounded even though the guest observes the host-side
	// connection close independently.
	host.send(t, `{"version":1,"id":7,"type":"shutdown"}`)
	guest.send(t, `{"version":1,"id":4,"type":"shutdown"}`)
	waitForRunnerEvent(t, host, "stopped")
	waitForRunnerEvent(t, guest, "stopped")
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
