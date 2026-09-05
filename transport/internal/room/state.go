// Package room carries only the host's ephemeral creator and Art follow state.
// It has no recording, capture, chat, persistence, or launch authority.
package room

import (
	"bytes"
	"encoding/json"
	"errors"
	"io"
	"math"
	"net/url"
	"regexp"
	"strconv"
	"strings"
	"unicode"
	"unicode/utf8"

	"github.com/rupret007/webjam/transport/internal/limits"
	"golang.org/x/text/unicode/norm"
)

var ErrInvalid = errors.New("invalid room state")

const MaxRevision uint64 = (1 << 53) - 1
const maxGeneration uint64 = (1 << 63) - 1

type State struct {
	Schema            int    `json:"schema"`
	Revision          uint64 `json:"revision"`
	CreatorProfileKey string `json:"creator_profile_key"`
	ArtStartKey       string `json:"art_start_key"`
	ReferenceVideo    Video  `json:"reference_video"`
	SharedCanvas      Canvas `json:"shared_canvas"`
}
type Video struct {
	Schema             int     `json:"schema"`
	Generation         uint64  `json:"generation"`
	PlaybackGeneration uint64  `json:"playback_generation"`
	State              string  `json:"state"`
	Shared             bool    `json:"shared"`
	SourceDisplayName  string  `json:"source_display_name"`
	IdentityDigest     string  `json:"identity_digest"`
	PositionS          float64 `json:"position_s"`
	DurationS          float64 `json:"duration_s"`
	NeedsAttention     bool    `json:"needs_attention"`
}
type Canvas struct {
	Schema       int    `json:"schema"`
	Generation   uint64 `json:"generation"`
	Shared       bool   `json:"shared"`
	JoinURL      string `json:"join_url"`
	ServerLabel  string `json:"server_label"`
	SessionLabel string `json:"session_label"`
}

func (State) String() string    { return "room.State{redacted}" }
func (State) GoString() string  { return "room.State{redacted}" }
func (Video) String() string    { return "room.Video{redacted}" }
func (Video) GoString() string  { return "room.Video{redacted}" }
func (Canvas) String() string   { return "room.Canvas{redacted}" }
func (Canvas) GoString() string { return "room.Canvas{redacted}" }

// Decode rejects unknown/duplicate/case-aliased fields and non-canonical domain
// values before they can enter an IPC event or reach an existing Art owner.
func Decode(encoded []byte) (*State, error) {
	if len(encoded) == 0 || len(encoded) > limits.MaxRoomStateBytes || !utf8.Valid(encoded) || !validEscapedUnicode(encoded) {
		return nil, ErrInvalid
	}
	d := json.NewDecoder(bytes.NewReader(encoded))
	d.UseNumber()
	if err := uniqueValue(d); err != nil {
		return nil, ErrInvalid
	}
	if _, err := d.Token(); !errors.Is(err, io.EOF) {
		return nil, ErrInvalid
	}
	var root map[string]json.RawMessage
	if json.Unmarshal(encoded, &root) != nil || !keys(root, "schema", "revision", "creator_profile_key", "art_start_key", "reference_video", "shared_canvas") {
		return nil, ErrInvalid
	}
	var video, canvas map[string]json.RawMessage
	if json.Unmarshal(root["reference_video"], &video) != nil || !keys(video, "schema", "generation", "playback_generation", "state", "shared", "source_display_name", "identity_digest", "position_s", "duration_s", "needs_attention") {
		return nil, ErrInvalid
	}
	if json.Unmarshal(root["shared_canvas"], &canvas) != nil || !keys(canvas, "schema", "generation", "shared", "join_url", "server_label", "session_label") {
		return nil, ErrInvalid
	}
	var state State
	if json.Unmarshal(encoded, &state) != nil || state.Validate() != nil {
		return nil, ErrInvalid
	}
	return &state, nil
}

// encoding/json replaces unpaired escaped surrogates. Reject them before that
// lossy conversion while continuing to allow a real printable U+FFFD name.
func validEscapedUnicode(data []byte) bool {
	for i := 0; i < len(data); i++ {
		if data[i] != '\\' {
			continue
		}
		i++
		if i >= len(data) {
			return false
		}
		if data[i] != 'u' {
			continue
		}
		if i+4 >= len(data) {
			return false
		}
		n, err := strconv.ParseUint(string(data[i+1:i+5]), 16, 16)
		if err != nil {
			return false
		}
		i += 4
		if n >= 0xd800 && n <= 0xdbff {
			if i+6 >= len(data) || data[i+1] != '\\' || data[i+2] != 'u' {
				return false
			}
			low, err := strconv.ParseUint(string(data[i+3:i+7]), 16, 16)
			if err != nil || low < 0xdc00 || low > 0xdfff {
				return false
			}
			i += 6
		} else if n >= 0xdc00 && n <= 0xdfff {
			return false
		}
	}
	return true
}

func keys(value map[string]json.RawMessage, names ...string) bool {
	if len(value) != len(names) {
		return false
	}
	for _, name := range names {
		if _, ok := value[name]; !ok {
			return false
		}
	}
	return true
}
func uniqueValue(d *json.Decoder) error {
	t, err := d.Token()
	if err != nil || t == nil {
		return ErrInvalid
	}
	delimiter, ok := t.(json.Delim)
	if !ok {
		return nil
	}
	if delimiter != '{' {
		return ErrInvalid
	}
	seen := map[string]bool{}
	for d.More() {
		token, err := d.Token()
		key, ok := token.(string)
		if err != nil || !ok || seen[key] {
			return ErrInvalid
		}
		seen[key] = true
		if uniqueValue(d) != nil {
			return ErrInvalid
		}
	}
	end, err := d.Token()
	if err != nil || end != json.Delim('}') {
		return ErrInvalid
	}
	return nil
}
func (s State) Validate() error {
	if s.Schema != 1 || s.Revision == 0 || s.Revision > MaxRevision {
		return ErrInvalid
	}
	switch s.CreatorProfileKey {
	case "art":
		if s.ArtStartKey != "talk_and_make" && s.ArtStartKey != "paint_along" {
			return ErrInvalid
		}
	case "music", "podcast_voice", "review_rehearsal":
		if s.ArtStartKey != "" || s.ReferenceVideo.Shared || s.SharedCanvas.Shared || s.ReferenceVideo.State != "idle" || s.ReferenceVideo.NeedsAttention {
			return ErrInvalid
		}
	default:
		return ErrInvalid
	}
	if s.ReferenceVideo.Validate() != nil || s.SharedCanvas.Validate() != nil {
		return ErrInvalid
	}
	if s.ReferenceVideo.Shared && s.ArtStartKey != "paint_along" {
		return ErrInvalid
	}
	return nil
}

var digestPattern = regexp.MustCompile(`\A[0-9a-f]{64}\z`)

func (v Video) Validate() error {
	if v.Schema != 1 || v.Generation > maxGeneration || v.PlaybackGeneration > maxGeneration || !label(v.SourceDisplayName, 255, 1024) || strings.ContainsAny(v.SourceDisplayName, "/\\") {
		return ErrInvalid
	}
	if v.IdentityDigest != "" && !digestPattern.MatchString(v.IdentityDigest) {
		return ErrInvalid
	}
	if !seconds(v.PositionS) || !seconds(v.DurationS) {
		return ErrInvalid
	}
	switch v.State {
	case "idle", "ready", "playing", "paused", "failed":
	default:
		return ErrInvalid
	}
	if !v.Shared {
		if v.SourceDisplayName != "" || v.IdentityDigest != "" || v.PositionS != 0 || v.DurationS != 0 || (v.State != "idle" && v.State != "failed") {
			return ErrInvalid
		}
	} else if v.State == "idle" || v.DurationS <= 0 || v.PositionS > v.DurationS || v.IdentityDigest == "" {
		return ErrInvalid
	}
	return nil
}
func seconds(v float64) bool { return !math.IsNaN(v) && !math.IsInf(v, 0) && v >= 0 && v <= 86400 }
func label(s string, chars, byteLimit int) bool {
	if !utf8.ValidString(s) || !norm.NFC.IsNormalString(s) || utf8.RuneCountInString(s) > chars || len(s) > byteLimit || strings.Join(strings.Fields(s), " ") != s {
		return false
	}
	for _, r := range s {
		if r != ' ' && !unicode.IsGraphic(r) || unicode.In(r, unicode.Zl, unicode.Zp, unicode.Zs) && r != ' ' {
			return false
		}
	}
	return true
}
func (c Canvas) Validate() error {
	if c.Schema != 1 || c.Generation > maxGeneration || !label(c.ServerLabel, 80, 320) || !label(c.SessionLabel, 80, 320) {
		return ErrInvalid
	}
	if !c.Shared {
		if c.JoinURL != "" || c.ServerLabel != "" || c.SessionLabel != "" {
			return ErrInvalid
		}
		return nil
	}
	if !canvasURL(c.JoinURL) {
		return ErrInvalid
	}
	return nil
}

var hostPattern = regexp.MustCompile(`\A[a-z0-9._-]{1,253}\z`)
var sessionPattern = regexp.MustCompile(`\A[A-Za-z0-9:-]{1,50}\z`)

func canvasURL(raw string) bool {
	if raw == "" || len(raw) > 512 {
		return false
	}
	for _, r := range raw {
		if r <= 0x20 || r > 0x7e {
			return false
		}
	}
	u, err := url.Parse(raw)
	if err != nil || u.User != nil || u.Fragment != "" || u.Opaque != "" || (u.Scheme != "drawpile" && u.Scheme != "ws" && u.Scheme != "wss") {
		return false
	}
	host := u.Hostname()
	if host != strings.ToLower(host) {
		return false
	}
	// Match the desktop's canonical Drawpile projection: its current session
	// URL parser accepts hostnames and IPv4, but cannot round-trip IPv6. Do
	// not send a room state that the receiving desktop must reject.
	if !hostPattern.MatchString(host) {
		return false
	}
	if strings.HasSuffix(u.Host, ":") {
		return false
	}
	if p := u.Port(); p != "" {
		n, e := strconv.Atoi(p)
		if e != nil || n < 1 || n > 65535 || strconv.Itoa(n) != p {
			return false
		}
	}
	if !strings.HasPrefix(u.Path, "/") || !sessionPattern.MatchString(strings.TrimPrefix(u.Path, "/")) || u.RawPath != "" {
		return false
	}
	if u.RawQuery != "" {
		tokens := strings.Split(u.RawQuery, "&")
		if len(tokens) > 8 {
			return false
		}
		for _, token := range tokens {
			key, value, _ := strings.Cut(token, "=")
			if key == "" || len(key) > 16 || len(value) > 128 {
				return false
			}
		}
	}
	return u.String() == raw && !u.ForceQuery
}
