package ipc

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"time"

	"github.com/rupret007/webjam/transport/internal/limits"
	"github.com/rupret007/webjam/transport/internal/webjoin"
)

const (
	commandWebJoinOpen   CommandType = "web_join_open"
	commandWebJoinAnswer CommandType = "web_join_answer"
	commandWebJoinClose  CommandType = "web_join_close"
	maxWebJoinEnvelope               = 8192
)

// Web Join has its own fixed shape. In particular, it accepts neither ICE
// servers nor an address, URL, TURN credential, or SDP supplied by the desktop.
// A sealed answer is authenticated inside webjoin before Pion sees any SDP.
type webJoinCommand struct {
	session     [32]byte
	capability  [32]byte
	bridgeToken [32]byte
	bridgePort  uint16
	generation  uint32
	expires     time.Time
	answer      []byte
}

func (w *webJoinCommand) clear() {
	clear(w.session[:])
	clear(w.capability[:])
	clear(w.bridgeToken[:])
	clear(w.answer)
	*w = webJoinCommand{}
}

func parseWebJoinCommand(line []byte, now time.Time) (Command, bool, error) {
	var header struct {
		Type string `json:"type"`
	}
	if json.Unmarshal(line, &header) != nil {
		return Command{}, false, nil
	}
	kind := CommandType(header.Type)
	if kind != commandWebJoinOpen && kind != commandWebJoinAnswer && kind != commandWebJoinClose {
		return Command{}, false, nil
	}
	var fields map[string]json.RawMessage
	if json.Unmarshal(line, &fields) != nil {
		return Command{}, true, ErrProtocol
	}
	expected := 4
	if kind == commandWebJoinOpen {
		expected = 9
	}
	if kind == commandWebJoinAnswer {
		expected = 5
	}
	if len(fields) != expected {
		return Command{}, true, ErrProtocol
	}
	for _, value := range fields {
		if bytes.Equal(bytes.TrimSpace(value), []byte("null")) {
			return Command{}, true, ErrProtocol
		}
	}
	var wire struct {
		Version     int           `json:"version"`
		ID          uint64        `json:"id"`
		Type        strictString  `json:"type"`
		Session     *strictString `json:"session_id,omitempty"`
		Capability  *strictString `json:"capability,omitempty"`
		BridgeToken *strictString `json:"bridge_token,omitempty"`
		BridgePort  *uint16       `json:"bridge_port,omitempty"`
		Generation  uint32        `json:"generation"`
		Expires     *int64        `json:"expires_at_unix,omitempty"`
		Answer      *strictString `json:"answer,omitempty"`
	}
	decoder := json.NewDecoder(bytes.NewReader(line))
	decoder.DisallowUnknownFields()
	if decoder.Decode(&wire) != nil || requireEOF(decoder) != nil || wire.Version != limits.IPCVersion || wire.ID == 0 || wire.Generation == 0 {
		return Command{}, true, ErrProtocol
	}
	w := &webJoinCommand{generation: wire.Generation}
	cmd := Command{Version: wire.Version, ID: wire.ID, Type: kind, WebJoin: w}
	fail := func() (Command, bool, error) { cmd.ClearSensitive(); return cmd, true, ErrEnrollmentInvalid }
	switch kind {
	case commandWebJoinOpen:
		if wire.Session == nil || wire.Capability == nil || wire.BridgeToken == nil || wire.BridgePort == nil || *wire.BridgePort == 0 || wire.Expires == nil || wire.Answer != nil {
			return fail()
		}
		if decodeFixed(string(*wire.Session), w.session[:]) != nil || decodeFixed(string(*wire.Capability), w.capability[:]) != nil || decodeFixed(string(*wire.BridgeToken), w.bridgeToken[:]) != nil {
			return fail()
		}
		w.expires = time.Unix(*wire.Expires, 0)
		if !w.expires.After(now) || w.expires.After(now.Add(limits.MaxEnrollmentLifetime)) {
			return fail()
		}
		w.bridgePort = *wire.BridgePort
	case commandWebJoinAnswer, commandWebJoinClose:
		if wire.Session != nil || wire.Capability != nil || wire.BridgeToken != nil || wire.BridgePort != nil || wire.Expires != nil {
			return fail()
		}
		if kind == commandWebJoinClose {
			if wire.Answer != nil {
				return fail()
			}
		} else {
			if wire.Answer == nil {
				return fail()
			}
			var err error
			w.answer, err = decodeWebJoinEnvelope(string(*wire.Answer))
			if err != nil {
				return fail()
			}
		}
	}
	return cmd, true, nil
}

func decodeWebJoinEnvelope(encoded string) ([]byte, error) {
	if len(encoded) > base64.RawURLEncoding.EncodedLen(maxWebJoinEnvelope) {
		return nil, ErrProtocol
	}
	raw, err := base64.RawURLEncoding.Strict().DecodeString(encoded)
	if err != nil || len(raw) < 33 || len(raw) > maxWebJoinEnvelope || string(raw[:5]) != "WJWS1" || base64.RawURLEncoding.EncodeToString(raw) != encoded {
		clear(raw)
		return nil, ErrProtocol
	}
	return raw, nil
}

func isWebJoinEvent(kind string) bool {
	return kind == "web_join_offer" || kind == "web_join_answer_applied" || kind == "web_join_closed" || kind == "web_join_connected"
}

func validateWebJoinEvent(e *Event) error {
	if e.Code != CodeOK || e.Generation == 0 || e.Mode != "" || e.ProfileID != "" || e.LoopbackPort != 0 || e.HostSPKISHA256 != "" || e.Build != "" || e.RequestID != 0 || e.Text != "" || e.Route != "" || e.SessionPath != "" || e.RelayBytes != 0 || e.RoomState != nil {
		return ErrProtocol
	}
	switch e.Type {
	case "web_join_offer":
		if e.ID == 0 || e.State != "host_waiting" || e.ExpiresAtUnix <= 0 {
			return ErrProtocol
		}
		raw, err := decodeWebJoinEnvelope(e.Signal)
		clear(raw)
		return err
	case "web_join_answer_applied":
		if e.ID == 0 || e.State != "connecting" || e.Signal != "" || e.ExpiresAtUnix != 0 {
			return ErrProtocol
		}
	case "web_join_closed":
		if e.State != "closed" || e.Signal != "" || e.ExpiresAtUnix != 0 {
			return ErrProtocol
		}
	case "web_join_connected":
		if e.ID != 0 || e.State != "connected" || e.Signal != "" || e.ExpiresAtUnix != 0 {
			return ErrProtocol
		}
	default:
		return ErrProtocol
	}
	return nil
}

type webJoinOperation struct {
	endpoint   *webjoin.Endpoint
	generation uint32
	connected  bool
}

func (s *runnerState) webJoinEnrolled() <-chan struct{} {
	if s.webJoin == nil || s.webJoin.connected {
		return nil
	}
	return s.webJoin.endpoint.Enrolled()
}

func (s *runnerState) webJoinDone() <-chan struct{} {
	if s.webJoin == nil {
		return nil
	}
	return s.webJoin.endpoint.Done()
}

func (s *runnerState) closeWebJoin() {
	if s.webJoin != nil {
		s.webJoin.endpoint.Close()
		s.webJoin = nil
	}
}

func (s *runnerState) handleWebJoin(ctx context.Context, command *Command, events *emitter) error {
	w := command.WebJoin
	failure := func(code string) error {
		return events.emit(Event{ID: command.ID, Type: "error", Code: code, State: "failed"})
	}
	switch command.Type {
	case commandWebJoinOpen:
		// One capability per process lifetime; kick cannot be undone by
		// replaying an old open command with a fresh replay cache.
		if s.webJoinUsed {
			return failure(CodePeerAlreadyOpen)
		}
		s.webJoinUsed = true
		openCtx, cancel := context.WithTimeout(ctx, 35*time.Second)
		defer cancel()
		ice, err := webjoin.FetchICE(openCtx, w.session, w.capability)
		if err != nil {
			return failure(CodeOpenFailed)
		}
		bridge, err := webjoin.NewUDPBridge(ctx, w.bridgePort, w.bridgeToken)
		if err != nil {
			return failure(CodeOpenFailed)
		}
		endpoint, err := webjoin.New(ctx, webjoin.Config{
			Enabled: true, SessionID: w.session, Capability: w.capability,
			Generation: w.generation, ExpiresAt: w.expires, ICE: ice, Bridge: bridge,
		})
		if err != nil {
			bridge.Close()
			return failure(CodeOpenFailed)
		}
		s.webJoin = &webJoinOperation{endpoint: endpoint, generation: w.generation}
		offer, err := endpoint.Offer(openCtx)
		if err != nil {
			s.closeWebJoin()
			return failure(CodeOpenFailed)
		}
		defer clear(offer)
		return events.emit(Event{ID: command.ID, Type: "web_join_offer", Code: CodeOK, State: "host_waiting", Generation: w.generation, Signal: base64.RawURLEncoding.EncodeToString(offer), ExpiresAtUnix: endpoint.OfferExpiresAt()})
	case commandWebJoinAnswer:
		if s.webJoin == nil {
			return failure(CodePeerNotOpen)
		}
		if s.webJoin.generation != w.generation {
			s.closeWebJoin()
			return failure(CodeEnrollmentInvalid)
		}
		if err := s.webJoin.endpoint.ApplyAnswer(ctx, w.answer); err != nil {
			s.closeWebJoin()
			return failure(CodeEnrollmentInvalid)
		}
		return events.emit(Event{ID: command.ID, Type: "web_join_answer_applied", Code: CodeOK, State: "connecting", Generation: w.generation})
	case commandWebJoinClose:
		// Even a stale-generation kick removes authority immediately.
		s.closeWebJoin()
		return events.emit(Event{ID: command.ID, Type: "web_join_closed", Code: CodeOK, State: "closed", Generation: w.generation})
	}
	return ErrProtocol
}
