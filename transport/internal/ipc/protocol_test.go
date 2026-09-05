package ipc

import (
	"bufio"
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/rupret007/webjam/transport/internal/icequic"
	"github.com/rupret007/webjam/transport/internal/limits"
	"github.com/rupret007/webjam/transport/internal/loopback"
	"github.com/rupret007/webjam/transport/internal/profile"
	"github.com/rupret007/webjam/transport/internal/room"
)

var protocolTestNow = time.Date(2026, 7, 13, 17, 0, 0, 0, time.UTC)

func TestStrictCommandRejectsUnknownDuplicateTrailingAndEscapedJSON(t *testing.T) {
	t.Parallel()
	cases := []string{
		`{"version":1,"id":1,"type":"hello","capability":"secret"}`,
		`{"version":1,"version":1,"id":1,"type":"hello"}`,
		`{"version":1,"id":1,"type":"hello"} {}`,
		`{"version":2,"id":1,"type":"hello"}`,
		`{"version":1,"id":1,"type":"prepare_host","mode":"host"}`,
		`{"version":1,"id":1,"type":"prepare_host","mode":""}`,
		`{"version":1,"id":1,"type":"prepare_host","target_port":0}`,
		`{"version":1,"id":1,"type":"prepare_host","generation":0}`,
		`{"version":1,"id":1,"type":"close_peer","session_reference":""}`,
		`{"version":1,"id":1,"type":"h\u0065llo"}`,
		`{"vers\u0069on":1,"id":1,"type":"hello"}`,
		`{"version":1,"id":1,"type":"héllo"}`,
		`{"version":1,"id":1,"type":null}`,
		`[]`,
	}
	for _, encoded := range cases {
		if _, err := ParseCommandAt([]byte(encoded), protocolTestNow); !errors.Is(err, ErrProtocol) {
			t.Fatalf("ParseCommandAt(%q) error = %v", encoded, err)
		}
	}
}

func TestHelpCommandIsNFCBoundedPlainTextAndCleared(t *testing.T) {
	t.Parallel()
	encoded, err := json.Marshal(map[string]any{
		"version":    1,
		"id":         17,
		"type":       "send_help",
		"generation": 7,
		"text":       "Try headphones — café \"mix\"",
	})
	if err != nil {
		t.Fatal(err)
	}
	command, err := ParseCommandAt(encoded, protocolTestNow)
	if err != nil {
		t.Fatal(err)
	}
	if command.Type != CommandSendHelp || command.ID != 17 || command.Generation != 7 ||
		string(command.HelpText) != "Try headphones — café \"mix\"" {
		t.Fatalf("help command = %+v", command)
	}
	owned := command.HelpText
	command.ClearSensitive()
	if command.HelpText != nil {
		t.Fatal("cleared help command retained text")
	}
	for _, value := range owned {
		if value != 0 {
			t.Fatal("cleared help command bytes were not wiped")
		}
	}

	for _, fields := range []map[string]any{
		{"version": 1, "id": 1, "type": "send_help", "generation": 0, "text": "help"},
		{"version": 1, "id": 1, "type": "send_help", "generation": 7, "text": ""},
		{"version": 1, "id": 1, "type": "send_help", "generation": 7, "text": "<b>help</b>"},
		{"version": 1, "id": 1, "type": "send_help", "generation": 7, "text": "cafe\u0301"},
		{"version": 1, "id": 1, "type": "send_help", "generation": 7, "text": strings.Repeat("é", 251)},
		{"version": 1, "id": 1, "type": "send_help", "generation": 7, "text": "help", "target_port": 22},
	} {
		candidate, marshalErr := json.Marshal(fields)
		if marshalErr != nil {
			t.Fatal(marshalErr)
		}
		if _, parseErr := ParseCommandAt(candidate, protocolTestNow); !errors.Is(parseErr, ErrProtocol) {
			t.Fatalf("unsafe help command %s error = %v", candidate, parseErr)
		}
	}
}

func TestOpenPeerDecodesTypedValuesAndDerivesSessionID(t *testing.T) {
	t.Parallel()
	encoded := openCommand(7, "guest", profile.ReferenceLocalID, protocolTestNow.Add(time.Minute), 0, testPin())
	command, err := ParseCommandAt([]byte(encoded), protocolTestNow)
	if err != nil {
		t.Fatal(err)
	}
	if command.ID != 7 || command.Type != CommandOpenPeer || command.Mode != "guest" ||
		command.Generation != 7 || command.TargetPort != 0 || command.Profile.ID != profile.ReferenceLocalID ||
		!command.Profile.LabOnly || !command.HasHostSPKI {
		t.Fatalf("command metadata = %+v", command)
	}
	if command.SessionReference != (Reference{1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1}) ||
		command.InviteReference != (Reference{2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2}) ||
		command.EnrollmentCapability != (Capability{3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3}) ||
		command.HostSPKISHA256 != (PublicPin{4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4}) {
		t.Fatal("typed enrollment values changed")
	}
	preimage := append([]byte(sessionIDDomain), command.SessionReference[:]...)
	preimage = append(preimage, command.InviteReference[:]...)
	wantSessionID := sha256.Sum256(preimage)
	if command.SessionID != SessionID(wantSessionID) {
		t.Fatalf("session ID = %x, want %x", command.SessionID, wantSessionID)
	}
	if got := fmt.Sprintf("%x", [32]byte(command.SessionID)); got != "1afab637606f656bb625ec5b1a04b2f44db3051c26ffce54b8a73d193f0ef02b" {
		t.Fatalf("session derivation vector = %s", got)
	}
	otherInvite := command.InviteReference
	otherInvite[0] ^= 0xff
	if command.SessionID == deriveSessionID(command.SessionReference, otherInvite) {
		t.Fatal("rotated invite reference reused the tombstoned service session")
	}
	formatted := fmt.Sprintf("%v %+v %#v", command, command, command)
	if strings.Contains(formatted, testCapability()) || strings.Contains(formatted, testSessionReference()) ||
		!strings.Contains(formatted, "redacted") {
		t.Fatalf("command formatting was not redacted: %s", formatted)
	}
	if serialized, marshalErr := json.Marshal(command); marshalErr == nil ||
		strings.Contains(string(serialized), testCapability()) || strings.Contains(marshalErr.Error(), testCapability()) {
		t.Fatalf("command JSON serialization was not blocked: bytes=%s error=%v", serialized, marshalErr)
	}
	command.ClearSensitive()
	if command.SessionReference != (Reference{}) || command.InviteReference != (Reference{}) ||
		command.EnrollmentCapability != (Capability{}) || command.HostSPKISHA256 != (PublicPin{}) ||
		command.SessionID != (SessionID{}) || command.ExpiresAtUnix != 0 {
		t.Fatal("ClearSensitive retained enrollment material")
	}
}

func TestOpenPeerRejectsBase64AmbiguityAndWrongLengths(t *testing.T) {
	t.Parallel()
	valid := openFields(9, "guest", profile.ReferenceLocalID, protocolTestNow.Add(time.Minute), 0, testPin())
	cases := []map[string]any{
		withField(valid, "session_reference", ""),
		withField(valid, "session_reference", "AA"),
		withField(valid, "session_reference", strings.Repeat("A", 22)),
		withField(valid, "session_reference", "AQEBAQEBAQEBAQEBAQEBAQ="),
		withField(valid, "session_reference", "AQEBAQEBAQEBAQEBAQEBAR"),
		withField(valid, "session_reference", "AQEBAQEBAQEBAQEBAQEBA+"),
		withField(valid, "enrollment_capability", strings.Repeat("A", 43)),
	}
	for _, fields := range cases {
		encoded := mustJSON(t, fields)
		if _, err := ParseCommandAt(encoded, protocolTestNow); !errors.Is(err, ErrEnrollmentInvalid) {
			t.Fatalf("fields=%v error=%v", fields, err)
		}
	}
	escaped := strings.Replace(openCommand(10, "guest", profile.ReferenceLocalID, protocolTestNow.Add(time.Minute), 0, testPin()), testSessionReference(), `\u0041`+testSessionReference()[1:], 1)
	if _, err := ParseCommandAt([]byte(escaped), protocolTestNow); !errors.Is(err, ErrProtocol) {
		t.Fatalf("escaped base64 error = %v", err)
	}
	unicodeFields := withField(valid, "host_spki_sha256", "р"+testPin()[2:])
	if _, err := ParseCommandAt(mustJSON(t, unicodeFields), protocolTestNow); !errors.Is(err, ErrProtocol) {
		t.Fatalf("Unicode base64 error = %v", err)
	}
}

func TestOpenPeerExpiryClockSkewBounds(t *testing.T) {
	t.Parallel()
	accepted := []time.Time{
		protocolTestNow.Add(-limits.EnrollmentClockSkew),
		protocolTestNow,
		protocolTestNow.Add(limits.MaxEnrollmentLifetime + limits.EnrollmentClockSkew),
	}
	for index, expiry := range accepted {
		if _, err := ParseCommandAt([]byte(openCommand(uint64(20+index), "guest", profile.ReferenceLocalID, expiry, 0, testPin())), protocolTestNow); err != nil {
			t.Fatalf("accepted expiry %v: %v", expiry, err)
		}
	}
	rejected := []time.Time{
		protocolTestNow.Add(-limits.EnrollmentClockSkew - time.Second),
		protocolTestNow.Add(limits.MaxEnrollmentLifetime + limits.EnrollmentClockSkew + time.Second),
	}
	for index, expiry := range rejected {
		if _, err := ParseCommandAt([]byte(openCommand(uint64(30+index), "guest", profile.ReferenceLocalID, expiry, 0, testPin())), protocolTestNow); !errors.Is(err, ErrEnrollmentInvalid) {
			t.Fatalf("rejected expiry %v error = %v", expiry, err)
		}
	}
	fields := openFields(40, "guest", profile.ReferenceLocalID, protocolTestNow, 0, testPin())
	fields["expires_at_unix"] = uint64(^uint64(0))
	if _, err := ParseCommandAt(mustJSON(t, fields), protocolTestNow); !errors.Is(err, ErrEnrollmentInvalid) {
		t.Fatalf("overflow expiry error = %v", err)
	}
}

func TestOpenPeerRejectsUnsupportedProfileAndCrossRoleFields(t *testing.T) {
	t.Parallel()
	unsupported := openCommand(50, "guest", "unknown-profile", protocolTestNow.Add(time.Minute), 0, testPin())
	command, err := ParseCommandAt([]byte(unsupported), protocolTestNow)
	if !errors.Is(err, ErrUnsupportedProfile) || command.ID != 50 {
		t.Fatalf("unsupported profile command=%+v error=%v", command, err)
	}
	cases := []map[string]any{
		openFields(51, "guest", profile.ReferenceLocalID, protocolTestNow.Add(time.Minute), 22124, testPin()),
		withField(openFields(51, "guest", profile.ReferenceLocalID, protocolTestNow.Add(time.Minute), 0, testPin()), "target_port", 0),
		openFields(52, "guest", profile.ReferenceLocalID, protocolTestNow.Add(time.Minute), 0, ""),
		openFields(53, "host", profile.ReferenceLocalID, protocolTestNow.Add(time.Minute), 22124, testPin()),
		openFields(54, "host", profile.ReferenceLocalID, protocolTestNow.Add(time.Minute), 0, ""),
		openFields(55, "listener", profile.ReferenceLocalID, protocolTestNow.Add(time.Minute), 0, ""),
	}
	for _, fields := range cases {
		if _, err = ParseCommandAt(mustJSON(t, fields), protocolTestNow); !errors.Is(err, ErrEnrollmentInvalid) {
			t.Fatalf("cross-role fields=%v error=%v", fields, err)
		}
	}
}

func TestOpenPeerRejectsMissingFieldsAndDesktopNetworkConfiguration(t *testing.T) {
	t.Parallel()
	valid := openFields(60, "guest", profile.ReferenceLocalID, protocolTestNow.Add(time.Minute), 0, testPin())
	for _, required := range []string{
		"mode", "profile_id", "session_reference", "invite_reference", "enrollment_capability",
		"expires_at_unix", "generation", "host_spki_sha256",
	} {
		fields := withField(valid, required, nil)
		delete(fields, required)
		if _, err := ParseCommandAt(mustJSON(t, fields), protocolTestNow); !errors.Is(err, ErrEnrollmentInvalid) {
			t.Fatalf("missing %s error = %v", required, err)
		}
	}
	for _, forbidden := range []string{
		"control_origin", "signaling_url", "stun_uri", "turn_uri", "ice_username", "ice_password",
		"candidate", "certificate", "private_key", "path", "endpoint", "host_address",
	} {
		fields := withField(valid, forbidden, "PRIVATE-FREE-FORM-SENTINEL")
		if _, err := ParseCommandAt(mustJSON(t, fields), protocolTestNow); !errors.Is(err, ErrProtocol) {
			t.Fatalf("forbidden %s error = %v", forbidden, err)
		}
	}
}

func TestEnrollmentOwnershipCopiesThenClearsFixedArrays(t *testing.T) {
	t.Parallel()
	command, err := ParseCommandAt(
		[]byte(openCommand(65, "guest", profile.ReferenceLocalID, protocolTestNow.Add(time.Minute), 0, testPin())),
		protocolTestNow,
	)
	if err != nil {
		t.Fatal(err)
	}
	configuration := enrollmentFromCommand(&command, PublicPin{})
	command.ClearSensitive()
	if configuration.EnrollmentCapability == (Capability{}) || configuration.SessionReference == (Reference{}) ||
		configuration.InviteReference == (Reference{}) || configuration.HostSPKISHA256 == (PublicPin{}) ||
		configuration.SessionID == (SessionID{}) {
		t.Fatal("ownership handoff lost enrollment material")
	}
	configuration.clear()
	if configuration.EnrollmentCapability != (Capability{}) || configuration.SessionReference != (Reference{}) ||
		configuration.InviteReference != (Reference{}) || configuration.HostSPKISHA256 != (PublicPin{}) ||
		configuration.SessionID != (SessionID{}) || configuration.Profile.ID != "" || configuration.ExpiresAtUnix != 0 {
		t.Fatal("cleared enrollment configuration retained material")
	}
}

func TestHostPreparationRegistrationAndExplicitResetLifecycle(t *testing.T) {
	t.Parallel()
	factory := &recordingFactory{port: 41000}
	orchestrator := &recordingOrchestrator{}
	hostOpen := openCommand(4, "host", profile.ReferenceLocalID, protocolTestNow.Add(time.Minute), 22124, "")
	resetFields := openFields(7, "host", profile.ReferenceLocalID, protocolTestNow.Add(time.Minute), 22124, "")
	resetFields["session_reference"] = fixedBase64(16, 7)
	resetFields["invite_reference"] = fixedBase64(16, 5)
	resetFields["enrollment_capability"] = fixedBase64(32, 6)
	resetFields["generation"] = uint32(8)
	resetOpen := string(mustJSON(t, resetFields))
	input := strings.Join([]string{
		`{"version":1,"id":1,"type":"hello"}`,
		`{"version":1,"id":2,"type":"prepare_host"}`,
		`{"version":1,"id":3,"type":"prepare_host"}`,
		hostOpen,
		`{"version":1,"id":5,"type":"hello"}`,
		`{"version":1,"id":6,"type":"close_peer"}`,
		resetOpen,
		`{"version":1,"id":8,"type":"hello"}`,
		`{"version":1,"id":9,"type":"shutdown"}`,
	}, "\n") + "\n"
	var output bytes.Buffer
	if err := runWithFactoryAndClock(context.Background(), strings.NewReader(input), &output, "test-build", factory, orchestrator, func() time.Time { return protocolTestNow }); err != nil {
		t.Fatal(err)
	}
	events := decodeEvents(t, output.String())
	if len(events) != 10 {
		t.Fatalf("event count = %d, output=%s", len(events), output.String())
	}
	prepared := events[2]
	if prepared.Type != "host_prepared" || prepared.State != "identity_ready" || len(prepared.HostSPKISHA256) != 43 {
		t.Fatalf("prepared event = %+v", prepared)
	}
	var pin PublicPin
	if err := decodeFixed(prepared.HostSPKISHA256, pin[:]); err != nil {
		t.Fatalf("public pin = %q: %v", prepared.HostSPKISHA256, err)
	}
	clear(pin[:])
	if events[3].Code != CodeEnrollmentInvalid || events[4].Type != "host_registered" || events[4].State != "host_waiting" ||
		events[4].Mode != "host" || events[4].ProfileID != profile.ReferenceLocalID ||
		events[4].Generation != 7 || events[4].LoopbackPort != 41000 {
		t.Fatalf("prepare/open events = %+v / %+v", events[3], events[4])
	}
	if events[6].Type != "peer_closed" || events[6].State != "closed" ||
		events[7].Type != "host_registered" || events[7].Generation != 8 ||
		events[8].State != "host_waiting" || events[8].Generation != 8 || events[9].Type != "stopped" {
		t.Fatalf("close/replay events = %+v", events[6:])
	}
	if factory.calls != 2 || factory.mode != "host" || factory.targetPort != 22124 || !factory.endpoint.closed ||
		orchestrator.calls != 2 || len(orchestrator.identities) != 2 || orchestrator.identities[0] != orchestrator.identities[1] {
		t.Fatalf("factory=%+v orchestrator=%+v", factory, orchestrator)
	}
	assertSecretsAbsent(t, output.String())
	if strings.Contains(output.String(), fixedBase64(16, 7)) || strings.Contains(output.String(), fixedBase64(16, 5)) ||
		strings.Contains(output.String(), fixedBase64(32, 6)) {
		t.Fatalf("reset invitation material leaked: %s", output.String())
	}
}

func TestHostOpenRequiresPreparedIdentityButDoesNotConsumeOnFailure(t *testing.T) {
	t.Parallel()
	factory := &recordingFactory{port: 42000}
	orchestrator := &recordingOrchestrator{}
	hostOpen := openCommand(1, "host", profile.ReferenceLocalID, protocolTestNow.Add(time.Minute), 22124, "")
	input := strings.Join([]string{
		hostOpen,
		`{"version":1,"id":2,"type":"prepare_host"}`,
		hostOpen,
		`{"version":1,"id":4,"type":"close_peer"}`,
		`{"version":1,"id":5,"type":"shutdown"}`,
	}, "\n") + "\n"
	var output bytes.Buffer
	if err := runWithFactoryAndClock(context.Background(), strings.NewReader(input), &output, "test-build", factory, orchestrator, func() time.Time { return protocolTestNow }); err != nil {
		t.Fatal(err)
	}
	events := decodeEvents(t, output.String())
	if events[1].Code != CodeIdentityNotPrepared || events[2].Type != "host_prepared" ||
		events[3].Type != "host_registered" || events[3].State != "host_waiting" || factory.calls != 1 {
		t.Fatalf("events=%+v factory=%+v", events, factory)
	}
}

func TestPreparedHostRejectsGuestRoleReconfiguration(t *testing.T) {
	t.Parallel()
	factory := &recordingFactory{port: 42500}
	orchestrator := &recordingOrchestrator{}
	guestOpen := openCommand(2, "guest", profile.ReferenceLocalID, protocolTestNow.Add(time.Minute), 0, testPin())
	hostOpen := openCommand(3, "host", profile.ReferenceLocalID, protocolTestNow.Add(time.Minute), 22124, "")
	input := strings.Join([]string{
		`{"version":1,"id":1,"type":"prepare_host"}`,
		guestOpen,
		hostOpen,
		`{"version":1,"id":4,"type":"close_peer"}`,
		`{"version":1,"id":5,"type":"shutdown"}`,
	}, "\n") + "\n"
	var output bytes.Buffer
	if err := runWithFactoryAndClock(context.Background(), strings.NewReader(input), &output, "test-build", factory, orchestrator, func() time.Time { return protocolTestNow }); err != nil {
		t.Fatal(err)
	}
	events := decodeEvents(t, output.String())
	if events[2].Code != CodeEnrollmentInvalid || events[2].State != "identity_ready" ||
		events[3].Type != "host_registered" || events[3].Mode != "host" || factory.calls != 1 || factory.mode != "host" {
		t.Fatalf("events=%+v factory=%+v", events, factory)
	}
}

func TestGuestLifecycleRejectsDuplicateOpenAndNeverEmitsSecrets(t *testing.T) {
	t.Parallel()
	factory := &recordingFactory{port: 43000}
	orchestrator := &recordingOrchestrator{}
	guestOpen := openCommand(2, "guest", profile.ReferenceLocalID, protocolTestNow.Add(time.Minute), 0, testPin())
	input := strings.Join([]string{
		guestOpen,
		`{"version":1,"id":3,"type":"hello"}`,
		guestOpen,
		`{"version":1,"id":5,"type":"close_peer"}`,
		guestOpen,
		`{"version":1,"id":7,"type":"shutdown"}`,
	}, "\n") + "\n"
	var output bytes.Buffer
	if err := runWithFactoryAndClock(context.Background(), strings.NewReader(input), &output, "test-build", factory, orchestrator, func() time.Time { return protocolTestNow }); err != nil {
		t.Fatal(err)
	}
	events := decodeEvents(t, output.String())
	if len(events) != 7 || events[1].Type != "peer_connected" || events[1].State != "connected" || events[1].Mode != "guest" ||
		events[2].State != "connected" || events[3].Code != CodePeerAlreadyOpen ||
		events[4].Type != "peer_closed" || events[5].Code != CodeEnrollmentInvalid || events[6].Type != "stopped" {
		t.Fatalf("events = %+v", events)
	}
	if factory.calls != 1 || factory.targetPort != 0 || !factory.endpoint.closed {
		t.Fatalf("factory = %+v", factory)
	}
	assertSecretsAbsent(t, output.String())
}

func TestConnectedEventsWaitForAuthenticatedFabricBoundary(t *testing.T) {
	for _, mode := range []string{"host", "guest"} {
		mode := mode
		t.Run(mode, func(t *testing.T) {
			now := time.Now().Truncate(time.Second)
			factory := &recordingFactory{port: 43500}
			orchestrator := &recordingOrchestrator{manual: true, started: make(chan *recordingOperation, 1)}
			harness := newRunnerHarness(t, factory, orchestrator, now)
			defer harness.stop(t)
			if ready := harness.next(t); ready.Type != "ready" {
				t.Fatalf("initial event = %+v", ready)
			}
			if mode == "host" {
				harness.send(t, `{"version":1,"id":1,"type":"prepare_host"}`)
				if prepared := harness.next(t); prepared.Type != "host_prepared" {
					t.Fatalf("prepared event = %+v", prepared)
				}
				harness.send(t, openCommand(2, mode, profile.ReferenceLocalID, now.Add(time.Minute), 22124, ""))
			} else {
				harness.send(t, openCommand(2, mode, profile.ReferenceLocalID, now.Add(time.Minute), 0, testPin()))
			}
			var operation *recordingOperation
			select {
			case operation = <-orchestrator.started:
			case <-time.After(time.Second):
				t.Fatal("fabric operation did not start")
			}
			harness.expectNone(t, 50*time.Millisecond)
			if mode == "host" {
				operation.updates <- fabricUpdate{kind: updateHostRegistered}
				registered := harness.next(t)
				if registered.Type != "host_registered" || registered.State != "host_waiting" || registered.ID != 2 {
					t.Fatalf("registered event = %+v", registered)
				}
				harness.expectNone(t, 50*time.Millisecond)
			}
			operation.updates <- fabricUpdate{kind: updatePeerConnected}
			connected := harness.next(t)
			wantID := uint64(2)
			if mode == "host" {
				wantID = 0
			}
			if connected.Type != "peer_connected" || connected.State != "connected" ||
				connected.Mode != mode || connected.ID != wantID {
				t.Fatalf("connected event = %+v", connected)
			}
			harness.send(t, `{"version":1,"id":3,"type":"close_peer"}`)
			if closed := harness.next(t); closed.Type != "peer_closed" {
				t.Fatalf("closed event = %+v", closed)
			}
			operation.mu.Lock()
			operationClosed := operation.closed
			configurationCleared := operation.configuration.SessionID == (SessionID{}) &&
				operation.configuration.EnrollmentCapability == (Capability{})
			operation.mu.Unlock()
			if !operationClosed || !configurationCleared {
				t.Fatal("closed operation retained resources or enrollment material")
			}
			harness.send(t, `{"version":1,"id":4,"type":"shutdown"}`)
			if stopped := harness.next(t); stopped.Type != "stopped" {
				t.Fatalf("stopped event = %+v", stopped)
			}
		})
	}
}

func TestHelpRequiresAuthenticatedGenerationAndEmitsBoundedReceipts(t *testing.T) {
	now := time.Now().Truncate(time.Second)
	factory := &recordingFactory{port: 43600}
	orchestrator := &recordingOrchestrator{
		manual:  true,
		started: make(chan *recordingOperation, 1),
	}
	harness := newRunnerHarness(t, factory, orchestrator, now)
	defer harness.stop(t)
	if ready := harness.next(t); ready.Type != "ready" {
		t.Fatalf("initial event = %+v", ready)
	}
	harness.send(t, openCommand(
		2,
		"guest",
		profile.ReferenceLocalID,
		now.Add(time.Minute),
		0,
		testPin(),
	))
	var operation *recordingOperation
	select {
	case operation = <-orchestrator.started:
	case <-time.After(time.Second):
		t.Fatal("fabric operation did not start")
	}

	harness.send(t, `{"version":1,"id":3,"type":"send_help","generation":7,"text":"Too soon"}`)
	if early := harness.next(t); early.Type != "error" || early.Code != CodeHelpNotReady {
		t.Fatalf("pre-auth help = %+v", early)
	}
	operation.updates <- fabricUpdate{kind: updatePeerConnected}
	if connected := harness.next(t); connected.Type != "peer_connected" {
		t.Fatalf("connected event = %+v", connected)
	}

	harness.send(t, `{"version":1,"id":4,"type":"send_help","generation":6,"text":"Stale"}`)
	if stale := harness.next(t); stale.Type != "error" || stale.Code != CodeHelpNotReady {
		t.Fatalf("stale-generation help = %+v", stale)
	}
	harness.send(t, `{"version":1,"id":5,"type":"send_help","generation":7,"text":"Try headphones — café"}`)
	accepted := harness.next(t)
	if accepted.Type != "help_accepted" || accepted.ID != 5 ||
		accepted.RequestID != 5 || accepted.Text != "" {
		t.Fatalf("accepted help = %+v", accepted)
	}
	operation.mu.Lock()
	requests := append([]recordingHelpRequest(nil), operation.helpRequests...)
	operation.mu.Unlock()
	if len(requests) != 1 || requests[0].requestID != 5 ||
		requests[0].text != "Try headphones — café" {
		t.Fatalf("help requests = %+v", requests)
	}

	operation.updates <- fabricUpdate{
		kind: updateHelpReceived, helpRequestID: 91,
		helpText: []byte("I can hear you"),
	}
	received := harness.next(t)
	if received.Type != "help_received" || received.ID != 0 ||
		received.RequestID != 91 || received.Text != "I can hear you" {
		t.Fatalf("received help metadata changed")
	}
	operation.updates <- fabricUpdate{
		kind:          updateHelpDelivered,
		helpRequestID: 5,
	}
	delivered := harness.next(t)
	if delivered.Type != "help_delivered" || delivered.ID != 0 ||
		delivered.RequestID != 5 || delivered.Text != "" {
		t.Fatalf("delivery receipt = %+v", delivered)
	}

	harness.send(t, `{"version":1,"id":6,"type":"close_peer"}`)
	if closed := harness.next(t); closed.Type != "peer_closed" {
		t.Fatalf("closed event = %+v", closed)
	}
	harness.send(t, `{"version":1,"id":7,"type":"shutdown"}`)
	if stopped := harness.next(t); stopped.Type != "stopped" {
		t.Fatalf("stopped event = %+v", stopped)
	}
}

func TestExplicitCloseAcknowledgesCompletedRemoteTeardownOnce(t *testing.T) {
	now := time.Now().Truncate(time.Second)
	factory := &recordingFactory{port: 43700}
	orchestrator := &recordingOrchestrator{manual: true, started: make(chan *recordingOperation, 1)}
	harness := newRunnerHarness(t, factory, orchestrator, now)
	defer harness.stop(t)
	if ready := harness.next(t); ready.Type != "ready" {
		t.Fatalf("initial event = %+v", ready)
	}
	harness.send(t, `{"version":1,"id":1,"type":"prepare_host"}`)
	if prepared := harness.next(t); prepared.Type != "host_prepared" {
		t.Fatalf("prepared event = %+v", prepared)
	}
	harness.send(t, openCommand(2, "host", profile.ReferenceLocalID, now.Add(time.Minute), 22124, ""))
	var operation *recordingOperation
	select {
	case operation = <-orchestrator.started:
	case <-time.After(time.Second):
		t.Fatal("fabric operation did not start")
	}
	operation.updates <- fabricUpdate{kind: updateHostRegistered}
	if registered := harness.next(t); registered.Type != "host_registered" {
		t.Fatalf("registered event = %+v", registered)
	}
	operation.updates <- fabricUpdate{kind: updatePeerConnected}
	if connected := harness.next(t); connected.Type != "peer_connected" {
		t.Fatalf("connected event = %+v", connected)
	}

	// The remote peer closes first. The fabric owns and completes teardown,
	// then the desktop's close command must receive one truthful idempotent
	// acknowledgment instead of peer_not_open.
	operation.updates <- fabricUpdate{kind: updateFabricFailed, err: ErrProtocol}
	failed := harness.next(t)
	if failed.Type != "error" || failed.ID != 0 || failed.Code != CodeOpenFailed {
		t.Fatalf("asynchronous close event = %+v", failed)
	}
	harness.send(t, `{"version":1,"id":3,"type":"close_peer"}`)
	closed := harness.next(t)
	if closed.Type != "peer_closed" || closed.ID != 3 || closed.Mode != "host" ||
		closed.ProfileID != profile.ReferenceLocalID || closed.Generation != 7 {
		t.Fatalf("idempotent close boundary = %+v", closed)
	}
	harness.send(t, `{"version":1,"id":4,"type":"close_peer"}`)
	if duplicate := harness.next(t); duplicate.Type != "error" || duplicate.Code != CodePeerNotOpen {
		t.Fatalf("duplicate close event = %+v", duplicate)
	}
	harness.send(t, `{"version":1,"id":5,"type":"shutdown"}`)
	if stopped := harness.next(t); stopped.Type != "stopped" {
		t.Fatalf("stopped event = %+v", stopped)
	}
}

func TestInvalidEnrollmentNeverOpensEndpointOrEchoesSentinel(t *testing.T) {
	t.Parallel()
	factory := &recordingFactory{port: 44000}
	fields := openFields(71, "guest", "not-compiled", protocolTestNow.Add(time.Minute), 0, testPin())
	const sentinel = "PRIVATE-CAPABILITY-SENTINEL"
	fields["enrollment_capability"] = sentinel
	input := string(mustJSON(t, fields)) + "\n"
	var output bytes.Buffer
	err := runWithFactoryAndClock(context.Background(), strings.NewReader(input), &output, "test-build", factory, &recordingOrchestrator{}, func() time.Time { return protocolTestNow })
	if !errors.Is(err, ErrUnsupportedProfile) || factory.calls != 0 {
		t.Fatalf("run error=%v factory=%+v", err, factory)
	}
	if strings.Contains(output.String(), sentinel) || strings.Contains(err.Error(), sentinel) ||
		strings.Contains(output.String(), "enrollment_capability") || strings.Contains(output.String(), "peer_connected") ||
		strings.Contains(output.String(), "host_registered") {
		t.Fatalf("secret or false success leaked: %s", output.String())
	}
	if !strings.Contains(output.String(), CodeUnsupportedProfile) {
		t.Fatalf("missing safe error code: %s", output.String())
	}
}

func TestMalformedEnrollmentUsesSafeCodeBeforeEndpointOpen(t *testing.T) {
	t.Parallel()
	factory := &recordingFactory{port: 44200}
	fields := openFields(75, "guest", profile.ReferenceLocalID, protocolTestNow.Add(time.Minute), 0, testPin())
	fields["enrollment_capability"] = "not-canonical-base64"
	var output bytes.Buffer
	err := runWithFactoryAndClock(
		context.Background(), strings.NewReader(string(mustJSON(t, fields))+"\n"), &output,
		"test-build", factory, &recordingOrchestrator{}, func() time.Time { return protocolTestNow },
	)
	if !errors.Is(err, ErrEnrollmentInvalid) || factory.calls != 0 ||
		!strings.Contains(output.String(), CodeEnrollmentInvalid) || strings.Contains(output.String(), "not-canonical-base64") {
		t.Fatalf("error=%v events=%s factory=%+v", err, output.String(), factory)
	}
}

func TestStaleOpenIsRejectedBeforeEndpointOrFabric(t *testing.T) {
	t.Parallel()
	factory := &recordingFactory{port: 44300}
	orchestrator := &recordingOrchestrator{}
	input := openCommand(77, "guest", profile.ReferenceLocalID, protocolTestNow, 0, testPin()) + "\n"
	var output bytes.Buffer
	if err := runWithFactoryAndClock(
		context.Background(), strings.NewReader(input), &output, "test-build", factory, orchestrator,
		func() time.Time { return protocolTestNow },
	); err != nil {
		t.Fatal(err)
	}
	events := decodeEvents(t, output.String())
	if len(events) != 2 || events[1].Code != CodeEnrollmentInvalid || events[1].State != "idle" ||
		factory.calls != 0 || orchestrator.calls != 0 {
		t.Fatalf("events=%+v factory=%+v orchestrator=%+v", events, factory, orchestrator)
	}
}

func TestConsumedEnrollmentFailureNeverEmitsConnectedOrLeaks(t *testing.T) {
	t.Parallel()
	factory := &recordingFactory{port: 44400}
	orchestrator := &recordingOrchestrator{failure: ErrEnrollmentInvalid}
	input := strings.Join([]string{
		openCommand(78, "guest", profile.ReferenceLocalID, protocolTestNow.Add(time.Minute), 0, testPin()),
		`{"version":1,"id":79,"type":"shutdown"}`,
	}, "\n") + "\n"
	var output bytes.Buffer
	if err := runWithFactoryAndClock(
		context.Background(), strings.NewReader(input), &output, "test-build", factory, orchestrator,
		func() time.Time { return protocolTestNow },
	); err != nil {
		t.Fatal(err)
	}
	events := decodeEvents(t, output.String())
	if len(events) != 3 || events[1].Type != "error" || events[1].Code != CodeEnrollmentInvalid ||
		events[1].ID != 78 || events[2].Type != "stopped" ||
		strings.Contains(output.String(), "peer_connected") || strings.Contains(output.String(), "host_registered") {
		t.Fatalf("events = %+v", events)
	}
	if len(orchestrator.operations) != 1 {
		t.Fatalf("operations = %d", len(orchestrator.operations))
	}
	orchestrator.operations[0].mu.Lock()
	closed := orchestrator.operations[0].closed
	cleared := orchestrator.operations[0].configuration.SessionID == (SessionID{})
	orchestrator.operations[0].mu.Unlock()
	if !closed || !cleared {
		t.Fatal("failed enrollment retained operation resources")
	}
	assertSecretsAbsent(t, output.String())
}

func TestValidatedOpenFailureDoesNotEmitConnected(t *testing.T) {
	t.Parallel()
	factory := &recordingFactory{port: 44500, fail: true}
	guestOpen := openCommand(81, "guest", profile.ReferenceLocalID, protocolTestNow.Add(time.Minute), 0, testPin())
	input := guestOpen + "\n" + `{"version":1,"id":82,"type":"shutdown"}` + "\n"
	var output bytes.Buffer
	if err := runWithFactoryAndClock(context.Background(), strings.NewReader(input), &output, "test-build", factory, &recordingOrchestrator{}, func() time.Time { return protocolTestNow }); err != nil {
		t.Fatal(err)
	}
	events := decodeEvents(t, output.String())
	if factory.calls != 1 || len(events) != 3 || events[1].Code != CodeOpenFailed || events[1].State != "idle" ||
		strings.Contains(output.String(), "peer_connected") || strings.Contains(output.String(), "host_registered") {
		t.Fatalf("events=%+v factory=%+v", events, factory)
	}
	assertSecretsAbsent(t, output.String())
}

func TestRunnerNeverEchoesMalformedSecret(t *testing.T) {
	t.Parallel()
	const sentinel = "CAPABILITY-SENTINEL-MUST-NOT-LEAK"
	input := `{"version":1,"id":1,"type":"hello","capability":"` + sentinel + `"}` + "\n"
	var output bytes.Buffer
	err := Run(context.Background(), strings.NewReader(input), &output, "test-build")
	if !errors.Is(err, ErrProtocol) {
		t.Fatalf("run error = %v", err)
	}
	if strings.Contains(output.String(), sentinel) || strings.Contains(output.String(), "capability") || strings.Contains(err.Error(), sentinel) {
		t.Fatalf("secret leaked in output: %s", output.String())
	}
	if !strings.Contains(output.String(), CodeProtocolViolation) {
		t.Fatalf("missing safe error code: %s", output.String())
	}
}

func TestRunnerRejectsOversizeLineWithoutEcho(t *testing.T) {
	t.Parallel()
	input := strings.NewReader(strings.Repeat("x", 70*1024) + "\n")
	var output bytes.Buffer
	err := Run(context.Background(), input, &output, "test-build")
	if !errors.Is(err, ErrProtocol) {
		t.Fatalf("run error = %v", err)
	}
	if output.Len() > 1024 {
		t.Fatalf("unexpectedly large safe output: %d", output.Len())
	}
}

func TestMarshalEventRejectsFreeFormFields(t *testing.T) {
	t.Parallel()
	if _, err := MarshalEvent(Event{Type: "PRIVATE-SENTINEL", Code: CodeOK}); !errors.Is(err, ErrProtocol) {
		t.Fatalf("free-form event type error = %v", err)
	}
	encoded, err := MarshalEvent(Event{Type: "ready", Code: CodeOK, State: "idle", Build: "PRIVATE SENTINEL"})
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(encoded), "PRIVATE") || !strings.Contains(string(encoded), "invalid-build") {
		t.Fatalf("unsafe build event = %s", encoded)
	}
	if _, err = MarshalEvent(Event{
		ID: 1, Type: "error", Code: CodeEnrollmentInvalid, State: "failed", HostSPKISHA256: testCapability(),
	}); !errors.Is(err, ErrProtocol) {
		t.Fatalf("secret-shaped error field error = %v", err)
	}
	for _, unsafe := range []Event{
		{ID: 1, Type: "peer_opened", Code: CodeOK, State: "proxy_ready"},
		{ID: 1, Type: "peer_connected", Code: CodeOK, State: "connected", Mode: "host", ProfileID: profile.ReferenceLocalID, Generation: 1, LoopbackPort: 41000},
		{ID: 0, Type: "peer_connected", Code: CodeOK, State: "connected", Mode: "guest", ProfileID: profile.ReferenceLocalID, Generation: 1, LoopbackPort: 41000},
	} {
		if _, err = MarshalEvent(unsafe); !errors.Is(err, ErrProtocol) {
			t.Fatalf("unsafe lifecycle event %+v error = %v", unsafe, err)
		}
	}
}

func TestHelpEventsAreExactEphemeralAndFormattingRedactsText(t *testing.T) {
	t.Parallel()
	base := Event{
		Type: "help_received", Code: CodeOK, State: "connected",
		Mode: "guest", ProfileID: profile.ReferenceLocalID, Generation: 7,
		RequestID: 19, Text: "Can you hear me? — café",
	}
	encoded, err := MarshalEvent(base)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(encoded), "Can you hear me?") {
		t.Fatalf("ephemeral IPC event omitted text: %s", encoded)
	}
	formatted := fmt.Sprintf("%v %+v %#v", base, base, base)
	if strings.Contains(formatted, base.Text) || !strings.Contains(formatted, "redacted") {
		t.Fatalf("help event formatting leaked text: %s", formatted)
	}

	accepted := base
	accepted.Type = "help_accepted"
	accepted.ID = 19
	accepted.Text = ""
	if _, err = MarshalEvent(accepted); err != nil {
		t.Fatal(err)
	}
	delivered := accepted
	delivered.Type = "help_delivered"
	delivered.ID = 0
	if _, err = MarshalEvent(delivered); err != nil {
		t.Fatal(err)
	}

	for _, unsafe := range []Event{
		{Type: "help_received", Code: CodeOK, State: "connected", Mode: "guest", ProfileID: profile.ReferenceLocalID, Generation: 7, RequestID: 19, Text: "<b>help</b>"},
		{ID: 1, Type: "help_received", Code: CodeOK, State: "connected", Mode: "guest", ProfileID: profile.ReferenceLocalID, Generation: 7, RequestID: 19, Text: "help"},
		{ID: 19, Type: "help_accepted", Code: CodeOK, State: "connected", Mode: "guest", ProfileID: profile.ReferenceLocalID, Generation: 7, RequestID: 20},
		{Type: "help_delivered", Code: CodeOK, State: "connected", Mode: "guest", ProfileID: profile.ReferenceLocalID, Generation: 7, RequestID: 19, Text: "leak"},
		{Type: "help_delivered", Code: CodeOK, State: "connected", Mode: "guest", ProfileID: profile.ReferenceLocalID, Generation: 0, RequestID: 19},
	} {
		if _, err = MarshalEvent(unsafe); !errors.Is(err, ErrProtocol) {
			t.Fatalf("unsafe help event %+v error = %v", unsafe, err)
		}
	}
}

type recordingFactory struct {
	calls      int
	mode       string
	targetPort int
	port       int
	fail       bool
	endpoint   *recordingEndpoint
}

func (f *recordingFactory) Open(mode string, targetPort int) (loopback.Endpoint, error) {
	f.calls++
	f.mode = mode
	f.targetPort = targetPort
	if f.fail {
		return nil, errors.New("fixed test endpoint failure")
	}
	f.endpoint = &recordingEndpoint{port: f.port}
	return f.endpoint, nil
}

type recordingEndpoint struct {
	port   int
	closed bool
}

func (e *recordingEndpoint) ReadDatagram(context.Context) ([]byte, error) {
	return nil, errors.New("not used")
}
func (e *recordingEndpoint) WriteDatagram(context.Context, []byte) error {
	return errors.New("not used")
}
func (e *recordingEndpoint) LocalPort() int { return e.port }
func (e *recordingEndpoint) Close() error {
	e.closed = true
	return nil
}

type recordingOrchestrator struct {
	mu            sync.Mutex
	calls         int
	fail          bool
	failure       error
	hostConnected bool
	manual        bool
	identities    [][32]byte
	operations    []*recordingOperation
	started       chan *recordingOperation
}

func (o *recordingOrchestrator) Start(
	_ context.Context,
	configuration *enrollmentConfig,
	identity *icequic.Identity,
	endpoint loopback.Endpoint,
) (fabricOperation, error) {
	o.mu.Lock()
	defer o.mu.Unlock()
	o.calls++
	if o.fail {
		return nil, errors.New("fixed test fabric failure")
	}
	operation := &recordingOperation{
		updates: make(chan fabricUpdate, 3), configuration: configuration, endpoint: endpoint,
	}
	if !o.manual {
		if o.failure != nil {
			operation.updates <- fabricUpdate{kind: updateFabricFailed, err: o.failure}
		} else if configuration.Mode == "host" {
			operation.updates <- fabricUpdate{kind: updateHostRegistered}
			if o.hostConnected {
				operation.updates <- fabricUpdate{kind: updatePeerConnected}
			}
		} else {
			operation.updates <- fabricUpdate{kind: updatePeerConnected}
		}
	}
	o.identities = append(o.identities, identity.SPKIFingerprint)
	o.operations = append(o.operations, operation)
	if o.started != nil {
		o.started <- operation
	}
	return operation, nil
}

type recordingOperation struct {
	mu            sync.Mutex
	updates       chan fabricUpdate
	configuration *enrollmentConfig
	endpoint      loopback.Endpoint
	closed        bool
	helpRequests  []recordingHelpRequest
	helpError     error
}

type recordingHelpRequest struct {
	requestID uint64
	text      string
}

func (o *recordingOperation) Updates() <-chan fabricUpdate { return o.updates }

func (o *recordingOperation) SendHelp(
	_ context.Context,
	requestID uint64,
	text string,
) error {
	o.mu.Lock()
	defer o.mu.Unlock()
	if o.helpError != nil {
		return o.helpError
	}
	o.helpRequests = append(o.helpRequests, recordingHelpRequest{
		requestID: requestID,
		text:      text,
	})
	return nil
}

func (o *recordingOperation) PublishRoomState(_ context.Context, state *room.State) error {
	return state.Validate()
}

func (o *recordingOperation) Close(context.Context) error {
	o.mu.Lock()
	defer o.mu.Unlock()
	if o.closed {
		return nil
	}
	o.closed = true
	if o.endpoint != nil {
		_ = o.endpoint.Close()
	}
	if o.configuration != nil {
		o.configuration.clear()
	}
	return nil
}

type runnerHarness struct {
	input  *io.PipeWriter
	events <-chan Event
	done   <-chan error
	cancel context.CancelFunc
	wait   time.Duration
}

func newRunnerHarness(
	t *testing.T,
	factory endpointFactory,
	orchestrator fabricOrchestrator,
	now time.Time,
) *runnerHarness {
	return newRunnerHarnessWithTimeout(t, factory, orchestrator, now, 3*time.Second, time.Second)
}

func newRunnerHarnessWithTimeout(
	t *testing.T,
	factory endpointFactory,
	orchestrator fabricOrchestrator,
	now time.Time,
	runTimeout time.Duration,
	eventTimeout time.Duration,
) *runnerHarness {
	t.Helper()
	inputReader, inputWriter := io.Pipe()
	outputReader, outputWriter := io.Pipe()
	ctx, cancel := context.WithTimeout(context.Background(), runTimeout)
	done := make(chan error, 1)
	events := make(chan Event, 16)
	go func() {
		err := runWithFactoryAndClock(
			ctx, inputReader, outputWriter, "test-build", factory, orchestrator,
			func() time.Time { return now },
		)
		_ = outputWriter.Close()
		done <- err
	}()
	go func() {
		defer close(events)
		scanner := bufio.NewScanner(outputReader)
		for scanner.Scan() {
			var event Event
			if err := json.Unmarshal(scanner.Bytes(), &event); err != nil {
				continue
			}
			events <- event
		}
	}()
	return &runnerHarness{input: inputWriter, events: events, done: done, cancel: cancel, wait: eventTimeout}
}

func (h *runnerHarness) send(t *testing.T, command string) {
	t.Helper()
	if _, err := io.WriteString(h.input, command+"\n"); err != nil {
		t.Fatalf("send command: %v", err)
	}
}

func (h *runnerHarness) next(t *testing.T) Event {
	t.Helper()
	select {
	case event, ok := <-h.events:
		if !ok {
			t.Fatal("event stream closed")
		}
		return event
	case <-time.After(h.wait):
		t.Fatal("timed out waiting for event")
	}
	return Event{}
}

func (h *runnerHarness) expectNone(t *testing.T, duration time.Duration) {
	t.Helper()
	select {
	case event := <-h.events:
		t.Fatalf("premature lifecycle event = %+v", event)
	case <-time.After(duration):
	}
}

func (h *runnerHarness) stop(t *testing.T) {
	t.Helper()
	h.cancel()
	_ = h.input.Close()
	select {
	case err := <-h.done:
		if err != nil {
			t.Fatalf("runner error = %v", err)
		}
	case <-time.After(h.wait):
		t.Fatal("runner did not stop")
	}
}

func openCommand(id uint64, mode, profileID string, expiry time.Time, targetPort int, hostPin string) string {
	encoded, err := json.Marshal(openFields(id, mode, profileID, expiry, targetPort, hostPin))
	if err != nil {
		panic(err)
	}
	return string(encoded)
}

func openFields(id uint64, mode, profileID string, expiry time.Time, targetPort int, hostPin string) map[string]any {
	fields := map[string]any{
		"version": 1, "id": id, "type": "open_peer", "mode": mode,
		"profile_id": profileID, "session_reference": testSessionReference(),
		"invite_reference": testInviteReference(), "enrollment_capability": testCapability(),
		"expires_at_unix": uint64(expiry.Unix()), "generation": 7,
	}
	if targetPort != 0 {
		fields["target_port"] = targetPort
	}
	if hostPin != "" {
		fields["host_spki_sha256"] = hostPin
	}
	return fields
}

func withField(source map[string]any, key string, value any) map[string]any {
	copyFields := make(map[string]any, len(source))
	for name, existing := range source {
		copyFields[name] = existing
	}
	copyFields[key] = value
	return copyFields
}

func mustJSON(t *testing.T, value any) []byte {
	t.Helper()
	encoded, err := json.Marshal(value)
	if err != nil {
		t.Fatal(err)
	}
	return encoded
}

func testSessionReference() string { return fixedBase64(16, 1) }
func testInviteReference() string  { return fixedBase64(16, 2) }
func testCapability() string       { return fixedBase64(32, 3) }
func testPin() string              { return fixedBase64(32, 4) }

func fixedBase64(size int, value byte) string {
	return base64.RawURLEncoding.EncodeToString(bytes.Repeat([]byte{value}, size))
}

func decodeEvents(t *testing.T, output string) []Event {
	t.Helper()
	lines := strings.Split(strings.TrimSpace(output), "\n")
	events := make([]Event, 0, len(lines))
	for _, line := range lines {
		var event Event
		decoder := json.NewDecoder(strings.NewReader(line))
		decoder.DisallowUnknownFields()
		if err := decoder.Decode(&event); err != nil {
			t.Fatalf("decode event %q: %v", line, err)
		}
		events = append(events, event)
	}
	return events
}

func assertSecretsAbsent(t *testing.T, output string) {
	t.Helper()
	for name, secret := range map[string]string{
		"session reference": testSessionReference(), "invite reference": testInviteReference(),
		"capability": testCapability(), "host input pin": testPin(),
	} {
		if strings.Contains(output, secret) {
			t.Fatalf("%s leaked in output: %s", name, output)
		}
	}
	for _, forbidden := range []string{
		"session_reference", "invite_reference", "enrollment_capability", "expires_at_unix",
		"certificate", "private_key", "control_origin", "signaling_origin", "stun_uri", "turn_uri",
	} {
		if strings.Contains(output, forbidden) {
			t.Fatalf("secret field %q leaked in output: %s", forbidden, output)
		}
	}
}

func ExampleEvent_hostPrepared() {
	fmt.Println(`{"version":1,"id":2,"type":"host_prepared","code":"ok","state":"identity_ready","host_spki_sha256":"<43-char unpadded base64url>"}`)
	// Output: {"version":1,"id":2,"type":"host_prepared","code":"ok","state":"identity_ready","host_spki_sha256":"<43-char unpadded base64url>"}
}

func FuzzParseCommand(f *testing.F) {
	f.Add([]byte(`{"version":1,"id":1,"type":"hello"}`))
	f.Add([]byte(`{"version":1,"id":1,"type":"open_peer"}`))
	f.Add([]byte(openCommand(1, "guest", profile.ReferenceLocalID, protocolTestNow.Add(time.Minute), 0, testPin())))
	f.Add(bytes.Repeat([]byte{'x'}, limits.MaxIPCLineBytes+1))
	f.Fuzz(func(t *testing.T, encoded []byte) {
		command, _ := ParseCommandAt(encoded, protocolTestNow)
		command.ClearSensitive()
	})
}
