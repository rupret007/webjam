package ipc

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"strings"
	"testing"
	"time"

	"github.com/rupret007/webjam/transport/internal/limits"
	"github.com/rupret007/webjam/transport/internal/profile"
	"github.com/rupret007/webjam/transport/internal/room"
)

func ipcRoomState() *room.State {
	return &room.State{Schema: 1, Revision: 1, CreatorProfileKey: "art", ArtStartKey: "paint_along", ReferenceVideo: room.Video{Schema: 1, State: "playing", Shared: true, SourceDisplayName: "PRIVATE-filename.mp4", IdentityDigest: strings.Repeat("a", 64), PositionS: 1, DurationS: 10}, SharedCanvas: room.Canvas{Schema: 1, Shared: true, JoinURL: "drawpile://canvas.local/session?p=PRIVATE-password", ServerLabel: "canvas.local", SessionLabel: "session"}}
}
func roomCommand(t *testing.T, id uint64, generation uint32, state *room.State) string {
	t.Helper()
	return string(mustJSON(t, map[string]any{"version": 1, "id": id, "type": "publish_room_state", "generation": generation, "room_state": state}))
}
func TestRoomCommandStrictFieldsAndPrivateRedaction(t *testing.T) {
	line := roomCommand(t, 4, 7, ipcRoomState())
	command, err := ParseCommand([]byte(line))
	if err != nil || command.RoomState == nil || command.Type != CommandPublishRoomState {
		t.Fatal("room command rejected", err)
	}
	for _, format := range []string{"%v", "%+v", "%#v"} {
		if strings.Contains(fmt.Sprintf(format, command), "PRIVATE") {
			t.Fatal("command payload leaked")
		}
	}
	state := command.RoomState
	command.ClearSensitive()
	if command.RoomState != nil || state.SharedCanvas.JoinURL != "" || state.ReferenceVideo.SourceDisplayName != "" {
		t.Fatal("clear retained private state")
	}
	for _, mutate := range []func(map[string]any){
		func(m map[string]any) { m["text"] = "wrong channel" }, func(m map[string]any) { m["capture_arm"] = true }, func(m map[string]any) { m["generation"] = 0 }, func(m map[string]any) { m["room_state"] = nil }, func(m map[string]any) { m["type"] = "send_help"; m["text"] = "help" }, func(m map[string]any) { m["type"] = "hello" },
	} {
		var m map[string]any
		if json.Unmarshal([]byte(line), &m) != nil {
			t.Fatal("fixture")
		}
		mutate(m)
		if _, err := ParseCommand(mustJSON(t, m)); err == nil {
			t.Fatal("invalid room command accepted")
		}
	}
	invalid := strings.Replace(line, `"revision":1`, `"revision":true`, 1)
	var output bytes.Buffer
	err = runWithFactoryAndClock(context.Background(), strings.NewReader(invalid+"\n"), &output, "test", &recordingFactory{}, &recordingOrchestrator{}, time.Now)
	if err == nil || strings.Contains(output.String(), "PRIVATE") || strings.Contains(output.String(), "drawpile") {
		t.Fatal("invalid state leaked or accepted")
	}
}
func TestRoomEventsStrictDirectionAndPrivateRepresentation(t *testing.T) {
	event := Event{Type: "room_state_received", Code: CodeOK, State: "connected", Mode: "guest", ProfileID: profile.ReferenceLocalID, Generation: 7, RoomState: ipcRoomState()}
	encoded, err := MarshalEvent(event)
	if err != nil || !bytes.Contains(encoded, []byte("PRIVATE-password")) {
		t.Fatal("authorized IPC payload unavailable", err)
	}
	if len(encoded) > limits.MaxRoomEventLineBytes || strings.Contains(fmt.Sprintf("%+v", event), "PRIVATE") {
		t.Fatal("event ceiling or diagnostic redaction")
	}
	for _, edit := range []func(*Event){func(e *Event) { e.ID = 1 }, func(e *Event) { e.Mode = "host" }, func(e *Event) { e.RequestID = 5 }, func(e *Event) { e.Type = "help_received"; e.RequestID = 5; e.Text = "help" }, func(e *Event) { e.RoomState = nil }, func(e *Event) { e.LoopbackPort = 12 }} {
		bad := event
		edit(&bad)
		if _, err := MarshalEvent(bad); err == nil {
			t.Fatal("invalid event accepted")
		}
	}
	accepted := Event{ID: 9, Type: "room_state_accepted", Code: CodeOK, State: "connected", Mode: "host", ProfileID: profile.ReferenceLocalID, Generation: 7, RequestID: 9}
	if _, err := MarshalEvent(accepted); err != nil {
		t.Fatal(err)
	}
	accepted.RoomState = ipcRoomState()
	if _, err := MarshalEvent(accepted); err == nil {
		t.Fatal("receipt retained private snapshot")
	}
}
func TestRoomPublishRequiresConnectedHostAndMatchingGeneration(t *testing.T) {
	for _, mode := range []string{"host", "guest"} {
		t.Run(mode, func(t *testing.T) {
			now := time.Now().Truncate(time.Second)
			orchestrator := &recordingOrchestrator{manual: true, started: make(chan *recordingOperation, 1)}
			h := newRunnerHarness(t, &recordingFactory{port: 43450}, orchestrator, now)
			defer h.stop(t)
			h.next(t)
			pin := testPin()
			port := 0
			if mode == "host" {
				h.send(t, `{"version":1,"id":1,"type":"prepare_host"}`)
				h.next(t)
				pin = ""
				port = 22124
			}
			h.send(t, openCommand(2, mode, profile.ReferenceLocalID, now.Add(time.Minute), port, pin))
			op := <-orchestrator.started
			h.send(t, roomCommand(t, 3, 7, ipcRoomState()))
			if event := h.next(t); event.Code != CodeRoomNotReady {
				t.Fatal("unauthenticated publish accepted")
			}
			if mode == "host" {
				op.updates <- fabricUpdate{kind: updateHostRegistered}
				h.next(t)
			}
			op.updates <- fabricUpdate{kind: updatePeerConnected}
			h.next(t)
			h.send(t, roomCommand(t, 4, 6, ipcRoomState()))
			if event := h.next(t); event.Code != CodeRoomNotReady {
				t.Fatal("stale generation published")
			}
			h.send(t, roomCommand(t, 5, 7, ipcRoomState()))
			event := h.next(t)
			if mode == "host" {
				if event.Type != "room_state_accepted" || event.RequestID != 5 {
					t.Fatal("host state not accepted")
				}
			} else if event.Code != CodeRoomInvalid {
				t.Fatal("guest published host state")
			}
			h.send(t, `{"version":1,"id":6,"type":"shutdown"}`)
			h.next(t)
		})
	}
}
func TestUnsupportedRoomProtocolYieldsExplicitBoundedError(t *testing.T) {
	now := time.Now().Truncate(time.Second)
	orchestrator := &recordingOrchestrator{failure: room.ErrUnsupported}
	var output bytes.Buffer
	input := openCommand(2, "guest", profile.ReferenceLocalID, now.Add(time.Minute), 0, testPin()) + "\n" + `{"version":1,"id":3,"type":"shutdown"}` + "\n"
	if err := runWithFactoryAndClock(context.Background(), strings.NewReader(input), &output, "test", &recordingFactory{port: 44440}, orchestrator, func() time.Time { return now }); err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(output.String(), CodePeerProtocolUnsupported) || strings.Contains(output.String(), "peer_connected") {
		t.Fatal("older peer received false connected state")
	}
}
