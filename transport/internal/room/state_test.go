package room

import (
	"encoding/json"
	"fmt"
	"strings"
	"testing"
)

func testState() State {
	return State{Schema: 1, Revision: 1, CreatorProfileKey: "art", ArtStartKey: "paint_along", ReferenceVideo: Video{Schema: 1, Generation: 3, PlaybackGeneration: 4, State: "playing", Shared: true, SourceDisplayName: "Étude.mp4", IdentityDigest: strings.Repeat("a", 64), PositionS: 12.5, DurationS: 120}, SharedCanvas: Canvas{Schema: 1, Generation: 2, Shared: true, JoinURL: "drawpile://canvas.local/session:private?p=PRIVATE", ServerLabel: "canvas.local", SessionLabel: "session"}}
}
func encodeState(t *testing.T, s State) []byte {
	t.Helper()
	b, e := json.Marshal(s)
	if e != nil {
		t.Fatal(e)
	}
	return b
}
func TestStateCarriesOnlyCanonicalHostArtFollowFacts(t *testing.T) {
	state := testState()
	encoded := encodeState(t, state)
	got, err := Decode(encoded)
	if err != nil || *got != state {
		t.Fatal("valid state did not round trip", err)
	}
	for _, value := range []any{state, state.ReferenceVideo, state.SharedCanvas} {
		for _, format := range []string{"%v", "%+v", "%#v"} {
			text := fmt.Sprintf(format, value)
			if strings.Contains(text, "PRIVATE") || strings.Contains(text, "Étude") || strings.Contains(text, "drawpile") {
				t.Fatal("private state leaked")
			}
		}
	}
	// Withdrawal counters survive creator changes, but Art payloads cannot.
	state.CreatorProfileKey = "music"
	state.ArtStartKey = ""
	state.ReferenceVideo = Video{Schema: 1, Generation: 5, State: "idle"}
	state.SharedCanvas = Canvas{Schema: 1, Generation: 7}
	if _, err := Decode(encodeState(t, state)); err != nil {
		t.Fatal("valid withdrawal rejected", err)
	}
}
func TestStateRejectsWrongTypesUnknownFieldsAndNonCanonicalValues(t *testing.T) {
	base := string(encodeState(t, testState()))
	cases := []string{
		strings.Replace(base, `"schema":1`, `"schema":true`, 1),
		strings.Replace(base, `"revision":1`, `"revision":1.0`, 1),
		strings.Replace(base, `"revision":1`, `"revision":9007199254740992`, 1),
		strings.Replace(base, `"revision":1`, `"revision":0`, 1),
		strings.Replace(base, `"revision":1`, `"Revision":1`, 1),
		strings.Replace(base, `"revision":1`, `"revision":1,"revision":2`, 1),
		strings.Replace(base, `"revision":1`, `"revision":1,"capture_arm":true`, 1),
		strings.Replace(base, `"generation":3`, `"generation":9223372036854775808`, 1),
		strings.Replace(base, `"generation":3`, `"generation":null`, 1),
		strings.Replace(base, `"shared":true`, `"shared":1`, 1),
		strings.Replace(base, `"playing"`, `"recording"`, 1),
		strings.Replace(base, `"position_s":12.5`, `"position_s":121`, 1),
		strings.Replace(base, `"position_s":12.5`, `"position_s":1e309`, 1),
		strings.Replace(base, `"position_s":12.5`, `"position_s":true`, 1),
		strings.Replace(base, "Étude.mp4", "../private.mp4", 1),
		strings.Replace(base, "Étude.mp4", `Etude\n.mp4`, 1),
		strings.Replace(base, "Étude.mp4", "E\u0301tude.mp4", 1),
		strings.Replace(base, "Étude.mp4", `\ud800.mp4`, 1),
		strings.Replace(base, `"paint_along"`, `"talk_and_make"`, 1),
		strings.Replace(base, `"art"`, `"music"`, 1),
		strings.Replace(base, `"shared_canvas":{`, `"shared_canvas":{"recording":false,`, 1),
		strings.Replace(base, `"reference_video":{`, `"reference_video":{"file_path":"/private",`, 1),
		strings.Replace(base, "canvas.local\"", "canvas.local \"", 1),
		base + ` {}`,
	}
	for i, text := range cases {
		if _, err := Decode([]byte(text)); err == nil {
			t.Errorf("case %d accepted", i)
		}
	}
	for _, path := range []string{"file:///private", "https://example.com/invites/canvas/x", "drawpile://user:password@host/session", "drawpile://host/session#secret", "drawpile://HOST/session", "drawpile://host:99999/session", "drawpile://host:027750/session", "drawpile://[::1]/session", "drawpile://[2001:db8::1]:27750/session", "drawpile://host/session/", "drawpile://host/a%2fb", "drawpile://host/a?", "drawpile://host/a?" + strings.Repeat("p=x&", 8) + "p=x"} {
		state := testState()
		state.SharedCanvas.JoinURL = path
		if _, err := Decode(encodeState(t, state)); err == nil {
			t.Errorf("unsafe/noncanonical URL accepted: %q", path)
		}
	}
}
func TestUnsharedStateCannotExposeMediaFacts(t *testing.T) {
	for _, edit := range []func(*State){
		func(s *State) { s.ReferenceVideo.Shared = false }, func(s *State) { s.SharedCanvas.Shared = false },
		func(s *State) { s.ReferenceVideo.DurationS = 0 }, func(s *State) { s.ReferenceVideo.IdentityDigest = "" },
		func(s *State) { s.SharedCanvas.JoinURL = "" },
	} {
		state := testState()
		edit(&state)
		if state.Validate() == nil {
			t.Fatal("contradictory state accepted")
		}
	}
}
func FuzzDecodeState(f *testing.F) {
	data, _ := json.Marshal(testState())
	f.Add(data)
	f.Add([]byte(`{}`))
	f.Fuzz(func(t *testing.T, data []byte) {
		state, err := Decode(data)
		if err == nil {
			encoded := encodeState(t, *state)
			if _, err := Decode(encoded); err != nil {
				t.Fatal("accepted state cannot be encoded safely")
			}
		}
	})
}
