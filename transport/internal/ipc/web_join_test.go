package ipc

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"testing"
	"time"
)

func webOpen(t *testing.T) map[string]any {
	t.Helper()
	secret := base64.RawURLEncoding.EncodeToString(bytes.Repeat([]byte{1}, 32))
	return map[string]any{"version": 1, "id": 1, "type": "web_join_open", "session_id": secret, "capability": secret, "bridge_token": secret, "bridge_port": 22125, "generation": 1, "expires_at_unix": 1700000300}
}

func TestWebJoinFixedShapeIPC(t *testing.T) {
	now := time.Unix(1700000000, 0)
	valid, _ := json.Marshal(webOpen(t))
	cmd, err := ParseCommandAt(valid, now)
	if err != nil || cmd.WebJoin == nil || cmd.WebJoin.bridgePort != 22125 {
		t.Fatalf("valid open: %v", err)
	}
	pointer := cmd.WebJoin
	cmd.ClearSensitive()
	if pointer.capability != ([32]byte{}) || pointer.session != ([32]byte{}) || pointer.bridgeToken != ([32]byte{}) {
		t.Fatal("secrets retained")
	}
	for _, name := range []string{"url", "address", "ice_servers", "username", "credential", "target_port", "mode", "host_signal"} {
		t.Run(name, func(t *testing.T) {
			body := webOpen(t)
			body[name] = "injected"
			raw, _ := json.Marshal(body)
			if _, err := ParseCommandAt(raw, now); err == nil {
				t.Fatal("accepted extra authority")
			}
		})
	}
	for _, field := range []string{"session_id", "capability", "bridge_token", "bridge_port", "generation", "expires_at_unix"} {
		t.Run("missing_"+field, func(t *testing.T) {
			body := webOpen(t)
			delete(body, field)
			raw, _ := json.Marshal(body)
			if _, err := ParseCommandAt(raw, now); err == nil {
				t.Fatal("accepted missing field")
			}
		})
	}
	for _, expiry := range []int64{1699999999, 1700000601} {
		body := webOpen(t)
		body["expires_at_unix"] = expiry
		raw, _ := json.Marshal(body)
		if _, err := ParseCommandAt(raw, now); err == nil {
			t.Fatal("accepted expiry")
		}
	}
	duplicated := append(valid[:len(valid)-1], []byte(`,"generation":2}`)...)
	if _, err := ParseCommandAt(duplicated, now); err == nil {
		t.Fatal("accepted duplicate")
	}
}

func TestWebJoinEnvelopeAndEvents(t *testing.T) {
	raw := append([]byte("WJWS1"), bytes.Repeat([]byte{1}, 3000)...)
	encoded := base64.RawURLEncoding.EncodeToString(raw)
	event := Event{ID: 1, Type: "web_join_offer", Code: "ok", State: "host_waiting", Generation: 1, Signal: encoded, ExpiresAtUnix: 1700000300}
	if _, err := MarshalEvent(event); err != nil {
		t.Fatal(err)
	}
	event.Text = "leak"
	if _, err := MarshalEvent(event); err == nil {
		t.Fatal("accepted extra event field")
	}
	for _, bad := range []string{"", encoded + "=", base64.RawURLEncoding.EncodeToString(bytes.Repeat([]byte{1}, 8193))} {
		if _, err := decodeWebJoinEnvelope(bad); err == nil {
			t.Fatal("accepted malformed envelope")
		}
	}
	cmd, err := ParseCommand([]byte(`{"version":1,"id":9,"type":"web_join_close","generation":2}`))
	if err != nil {
		t.Fatal(err)
	}
	state := &runnerState{}
	var output bytes.Buffer
	if err := state.handleWebJoin(context.Background(), &cmd, &emitter{writer: &output}); err != nil {
		t.Fatal(err)
	}
	if !bytes.Contains(output.Bytes(), []byte(`"type":"web_join_closed"`)) {
		t.Fatal("missing close")
	}
	if state.webJoin != nil || state.webJoinUsed {
		t.Fatal("close enabled endpoint")
	}
}

func TestWebJoinOfferExpiryIsRequiredAndExclusive(t *testing.T) {
	envelope := append([]byte("WJWS1"), bytes.Repeat([]byte{1}, 64)...)
	offer := Event{ID: 1, Type: "web_join_offer", Code: "ok", State: "host_waiting", Generation: 1, Signal: base64.RawURLEncoding.EncodeToString(envelope), ExpiresAtUnix: 1700000090}
	encoded, err := MarshalEvent(offer)
	if err != nil {
		t.Fatal(err)
	}
	var fields map[string]json.RawMessage
	if err := json.Unmarshal(encoded, &fields); err != nil {
		t.Fatal(err)
	}
	if len(fields) != 8 || string(fields["expires_at_unix"]) != "1700000090" {
		t.Fatal("offer omitted its bounded expiry metadata")
	}
	for _, expiry := range []int64{0, -1} {
		invalid := offer
		invalid.ExpiresAtUnix = expiry
		if _, err := MarshalEvent(invalid); err == nil {
			t.Fatal("accepted invalid offer expiry")
		}
	}
	for _, event := range []Event{
		{ID: 1, Type: "web_join_answer_applied", Code: "ok", State: "connecting", Generation: 1},
		{ID: 1, Type: "web_join_closed", Code: "ok", State: "closed", Generation: 1},
		{Type: "web_join_connected", Code: "ok", State: "connected", Generation: 1},
		{Type: "ready", Code: "ok", State: "idle", Build: "dev"},
	} {
		if _, err := MarshalEvent(event); err != nil {
			t.Fatal(err)
		}
		event.ExpiresAtUnix = offer.ExpiresAtUnix
		if _, err := MarshalEvent(event); err == nil {
			t.Fatalf("accepted offer expiry on %s", event.Type)
		}
	}
}
