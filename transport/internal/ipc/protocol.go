// Package ipc implements the only desktop/sidecar control protocol. It is a
// strict, versioned JSON-lines protocol. Secret-bearing input is decoded into
// fixed-size values; events contain only allowlisted public state and never
// echo parser text, credentials, addresses, paths, or underlying errors.
package ipc

import (
	"bytes"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"errors"
	"io"
	"math"
	"regexp"
	"time"

	"github.com/rupret007/webjam/transport/internal/limits"
	"github.com/rupret007/webjam/transport/internal/profile"
)

const sessionIDDomain = "webjam/v3/session-id\x00"

var (
	ErrProtocol           = errors.New("IPC protocol violation")
	ErrDuplicateField     = errors.New("duplicate JSON field")
	ErrEventTooLarge      = errors.New("IPC event exceeds limit")
	ErrUnsupportedProfile = errors.New("unsupported compiled profile")
	ErrEnrollmentInvalid  = errors.New("invalid enrollment configuration")
)

type CommandType string

const (
	CommandHello       CommandType = "hello"
	CommandPrepareHost CommandType = "prepare_host"
	CommandOpenPeer    CommandType = "open_peer"
	CommandClosePeer   CommandType = "close_peer"
	CommandShutdown    CommandType = "shutdown"
)

type Reference [16]byte
type Capability [32]byte
type PublicPin [32]byte
type SessionID [32]byte

func (Reference) String() string                { return "[redacted-reference]" }
func (Capability) String() string               { return "[redacted-capability]" }
func (PublicPin) String() string                { return "[redacted-public-pin]" }
func (SessionID) String() string                { return "[redacted-session-id]" }
func (Reference) GoString() string              { return "ipc.Reference{redacted}" }
func (Capability) GoString() string             { return "ipc.Capability{redacted}" }
func (PublicPin) GoString() string              { return "ipc.PublicPin{redacted}" }
func (SessionID) GoString() string              { return "ipc.SessionID{redacted}" }
func (Reference) MarshalJSON() ([]byte, error)  { return nil, ErrProtocol }
func (Capability) MarshalJSON() ([]byte, error) { return nil, ErrProtocol }
func (PublicPin) MarshalJSON() ([]byte, error)  { return nil, ErrProtocol }
func (SessionID) MarshalJSON() ([]byte, error)  { return nil, ErrProtocol }

// Command never retains the JSON spellings of enrollment values. Profile is
// a compiled value selected by exact ID, not desktop-provided configuration.
type Command struct {
	Version              int
	ID                   uint64
	Type                 CommandType
	Mode                 string
	TargetPort           int
	Generation           uint32
	Profile              profile.Profile
	SessionReference     Reference
	InviteReference      Reference
	EnrollmentCapability Capability
	ExpiresAtUnix        uint64
	HostSPKISHA256       PublicPin
	HasHostSPKI          bool
	SessionID            SessionID
}

func (Command) String() string               { return "ipc.Command{redacted}" }
func (Command) GoString() string             { return "ipc.Command{redacted}" }
func (Command) MarshalJSON() ([]byte, error) { return nil, ErrProtocol }

func (c *Command) ClearSensitive() {
	if c == nil {
		return
	}
	clear(c.SessionReference[:])
	clear(c.InviteReference[:])
	clear(c.EnrollmentCapability[:])
	clear(c.HostSPKISHA256[:])
	clear(c.SessionID[:])
	c.ExpiresAtUnix = 0
	c.HasHostSPKI = false
}

type wireCommand struct {
	Version              int           `json:"version"`
	ID                   uint64        `json:"id"`
	Type                 strictString  `json:"type"`
	Mode                 *strictString `json:"mode,omitempty"`
	TargetPort           *int          `json:"target_port,omitempty"`
	Generation           *uint32       `json:"generation,omitempty"`
	ProfileID            *strictString `json:"profile_id,omitempty"`
	SessionReference     *strictString `json:"session_reference,omitempty"`
	InviteReference      *strictString `json:"invite_reference,omitempty"`
	EnrollmentCapability *strictString `json:"enrollment_capability,omitempty"`
	ExpiresAtUnix        *uint64       `json:"expires_at_unix,omitempty"`
	HostSPKISHA256       *strictString `json:"host_spki_sha256,omitempty"`
}

func (w *wireCommand) clearStrings() {
	w.Type = ""
	for _, value := range []*strictString{
		w.Mode, w.ProfileID, w.SessionReference, w.InviteReference, w.EnrollmentCapability, w.HostSPKISHA256,
	} {
		if value != nil {
			*value = ""
		}
	}
	w.Mode = nil
	w.ProfileID = nil
	w.SessionReference = nil
	w.InviteReference = nil
	w.EnrollmentCapability = nil
	w.HostSPKISHA256 = nil
}

// strictString rejects JSON escapes and non-ASCII bytes. This makes each
// security-bearing spelling unique before semantic base64/profile parsing.
type strictString string

func (s *strictString) UnmarshalJSON(encoded []byte) error {
	if len(encoded) < 2 || encoded[0] != '"' || encoded[len(encoded)-1] != '"' {
		return ErrProtocol
	}
	for _, character := range encoded[1 : len(encoded)-1] {
		if character < 0x20 || character > 0x7e || character == '\\' || character == '"' {
			return ErrProtocol
		}
	}
	*s = strictString(encoded[1 : len(encoded)-1])
	return nil
}

type Event struct {
	Version        int    `json:"version"`
	ID             uint64 `json:"id"`
	Type           string `json:"type"`
	Code           string `json:"code,omitempty"`
	State          string `json:"state,omitempty"`
	Mode           string `json:"mode,omitempty"`
	ProfileID      string `json:"profile_id,omitempty"`
	Generation     uint32 `json:"generation,omitempty"`
	LoopbackPort   int    `json:"loopback_port,omitempty"`
	HostSPKISHA256 string `json:"host_spki_sha256,omitempty"`
	Build          string `json:"build,omitempty"`
}

func ParseCommand(line []byte) (Command, error) {
	return ParseCommandAt(line, time.Now())
}

func ParseCommandAt(line []byte, now time.Time) (Command, error) {
	if len(line) == 0 || len(line) > limits.MaxIPCLineBytes || !canonicalIPCBytes(line) {
		return Command{}, ErrProtocol
	}
	if err := rejectDuplicateFields(line); err != nil {
		return Command{}, ErrProtocol
	}
	decoder := json.NewDecoder(bytes.NewReader(line))
	decoder.DisallowUnknownFields()
	decoder.UseNumber()
	var wire wireCommand
	defer wire.clearStrings()
	if err := decoder.Decode(&wire); err != nil {
		return Command{}, ErrProtocol
	}
	if err := requireEOF(decoder); err != nil {
		return Command{}, ErrProtocol
	}
	if wire.Version != limits.IPCVersion || wire.ID == 0 {
		return Command{}, ErrProtocol
	}
	command := Command{Version: wire.Version, ID: wire.ID, Type: CommandType(wire.Type)}
	switch command.Type {
	case CommandHello, CommandPrepareHost, CommandClosePeer, CommandShutdown:
		if !wire.noPeerFields() {
			return command, ErrProtocol
		}
		return command, nil
	case CommandOpenPeer:
		return parseOpenCommand(command, wire, now)
	default:
		return command, ErrProtocol
	}
}

func canonicalIPCBytes(encoded []byte) bool {
	for _, character := range encoded {
		if character == '\\' || character >= 0x7f ||
			(character < 0x20 && character != '\t' && character != '\r' && character != '\n') {
			return false
		}
	}
	return true
}

func (w wireCommand) noPeerFields() bool {
	return w.Mode == nil && w.TargetPort == nil && w.Generation == nil && w.ProfileID == nil &&
		w.SessionReference == nil && w.InviteReference == nil && w.EnrollmentCapability == nil &&
		w.ExpiresAtUnix == nil && w.HostSPKISHA256 == nil
}

func parseOpenCommand(command Command, wire wireCommand, now time.Time) (Command, error) {
	if wire.Mode == nil || wire.Generation == nil || *wire.Generation == 0 || wire.ProfileID == nil ||
		wire.ExpiresAtUnix == nil || *wire.ExpiresAtUnix == 0 || wire.SessionReference == nil ||
		wire.InviteReference == nil || wire.EnrollmentCapability == nil || *wire.SessionReference == "" ||
		*wire.InviteReference == "" || *wire.EnrollmentCapability == "" {
		return command, ErrEnrollmentInvalid
	}
	profileID := string(*wire.ProfileID)
	if !profilePattern.MatchString(profileID) {
		return command, ErrEnrollmentInvalid
	}
	compiledProfile, ok := profile.Lookup(profileID)
	if !ok {
		return command, ErrUnsupportedProfile
	}
	mode := string(*wire.Mode)
	switch mode {
	case "host":
		if wire.TargetPort == nil || *wire.TargetPort < 1 || *wire.TargetPort > 65_535 || wire.HostSPKISHA256 != nil {
			return command, ErrEnrollmentInvalid
		}
	case "guest":
		if wire.TargetPort != nil || wire.HostSPKISHA256 == nil || *wire.HostSPKISHA256 == "" {
			return command, ErrEnrollmentInvalid
		}
	default:
		return command, ErrEnrollmentInvalid
	}
	if *wire.ExpiresAtUnix > math.MaxInt64 {
		return command, ErrEnrollmentInvalid
	}
	expiry := time.Unix(int64(*wire.ExpiresAtUnix), 0)
	now = time.Unix(now.Unix(), 0)
	if expiry.Before(now.Add(-limits.EnrollmentClockSkew)) ||
		expiry.After(now.Add(limits.MaxEnrollmentLifetime+limits.EnrollmentClockSkew)) {
		return command, ErrEnrollmentInvalid
	}
	command.Mode = mode
	if wire.TargetPort != nil {
		command.TargetPort = *wire.TargetPort
	}
	command.Generation = *wire.Generation
	command.Profile = compiledProfile
	command.ExpiresAtUnix = *wire.ExpiresAtUnix
	if err := decodeFixed(string(*wire.SessionReference), command.SessionReference[:]); err != nil {
		command.ClearSensitive()
		return command, ErrEnrollmentInvalid
	}
	if err := decodeFixed(string(*wire.InviteReference), command.InviteReference[:]); err != nil {
		command.ClearSensitive()
		return command, ErrEnrollmentInvalid
	}
	if err := decodeFixed(string(*wire.EnrollmentCapability), command.EnrollmentCapability[:]); err != nil {
		command.ClearSensitive()
		return command, ErrEnrollmentInvalid
	}
	if mode == "guest" {
		if err := decodeFixed(string(*wire.HostSPKISHA256), command.HostSPKISHA256[:]); err != nil {
			command.ClearSensitive()
			return command, ErrEnrollmentInvalid
		}
		command.HasHostSPKI = true
	}
	command.SessionID = deriveSessionID(command.SessionReference, command.InviteReference)
	return command, nil
}

func decodeFixed(encoded string, destination []byte) error {
	if len(destination) == 0 || len(encoded) != base64.RawURLEncoding.EncodedLen(len(destination)) {
		return ErrEnrollmentInvalid
	}
	for index := 0; index < len(encoded); index++ {
		character := encoded[index]
		if !((character >= 'A' && character <= 'Z') || (character >= 'a' && character <= 'z') ||
			(character >= '0' && character <= '9') || character == '-' || character == '_') {
			return ErrEnrollmentInvalid
		}
	}
	decoded, err := base64.RawURLEncoding.Strict().DecodeString(encoded)
	if err != nil || len(decoded) != len(destination) || base64.RawURLEncoding.EncodeToString(decoded) != encoded {
		clear(decoded)
		return ErrEnrollmentInvalid
	}
	nonzero := byte(0)
	for _, value := range decoded {
		nonzero |= value
	}
	if nonzero == 0 {
		clear(decoded)
		return ErrEnrollmentInvalid
	}
	copy(destination, decoded)
	clear(decoded)
	return nil
}

// deriveSessionID keeps the stable application session reference private while
// giving every rotated invitation a distinct service session.  The invite
// reference is required here: the reference service tombstones a closed
// service session to reject replay, while Reset Invite deliberately preserves
// the application session reference and rotates the invitation reference.
func deriveSessionID(sessionReference, inviteReference Reference) SessionID {
	var preimage [len(sessionIDDomain) + 2*len(Reference{})]byte
	copy(preimage[:], sessionIDDomain)
	copy(preimage[len(sessionIDDomain):], sessionReference[:])
	copy(preimage[len(sessionIDDomain)+len(sessionReference):], inviteReference[:])
	digest := sha256.Sum256(preimage[:])
	clear(preimage[:])
	return SessionID(digest)
}

func encodePublicPin(pin PublicPin) string {
	return base64.RawURLEncoding.EncodeToString(pin[:])
}

func MarshalEvent(event Event) ([]byte, error) {
	event.Version = limits.IPCVersion
	if err := validateEvent(&event); err != nil {
		return nil, err
	}
	encoded, err := json.Marshal(event)
	if err != nil {
		return nil, err
	}
	if len(encoded)+1 > limits.MaxEventLineBytes {
		return nil, ErrEventTooLarge
	}
	return append(encoded, '\n'), nil
}

func validateEvent(event *Event) error {
	if _, ok := allowedEventTypes[event.Type]; !ok {
		return ErrProtocol
	}
	if event.Code != "" {
		if _, ok := allowedEventCodes[event.Code]; !ok {
			return ErrProtocol
		}
	}
	if event.State != "" {
		if _, ok := allowedEventStates[event.State]; !ok {
			return ErrProtocol
		}
	}
	if event.Mode != "" && event.Mode != "host" && event.Mode != "guest" {
		return ErrProtocol
	}
	if event.ProfileID != "" {
		resolved, ok := profile.Lookup(event.ProfileID)
		if !ok {
			return ErrProtocol
		}
		event.ProfileID = resolved.ID
	}
	if event.LoopbackPort < 0 || event.LoopbackPort > 65_535 {
		return ErrProtocol
	}
	if event.HostSPKISHA256 != "" {
		var pin PublicPin
		if err := decodeFixed(event.HostSPKISHA256, pin[:]); err != nil {
			return ErrProtocol
		}
		clear(pin[:])
	}
	if event.Build != "" {
		event.Build = safeBuildID(event.Build)
	}
	peerFieldsEmpty := event.Mode == "" && event.ProfileID == "" && event.Generation == 0 && event.LoopbackPort == 0
	switch event.Type {
	case "ready":
		if event.ID != 0 || event.Code != CodeOK || event.State != "idle" || event.Build == "" ||
			!peerFieldsEmpty || event.HostSPKISHA256 != "" {
			return ErrProtocol
		}
	case "hello":
		if event.ID == 0 || event.Code != CodeOK || event.Build == "" || event.HostSPKISHA256 != "" {
			return ErrProtocol
		}
		if event.State == "connecting" || event.State == "host_waiting" || event.State == "connected" {
			if event.Mode == "" || event.ProfileID == "" || event.Generation == 0 || event.LoopbackPort != 0 {
				return ErrProtocol
			}
		} else if (event.State != "idle" && event.State != "identity_ready" && event.State != "closed") || !peerFieldsEmpty {
			return ErrProtocol
		}
	case "host_prepared":
		if event.ID == 0 || event.Code != CodeOK || event.State != "identity_ready" || event.Build != "" ||
			!peerFieldsEmpty || event.HostSPKISHA256 == "" {
			return ErrProtocol
		}
	case "host_registered":
		if event.ID == 0 || event.Code != CodeOK || event.State != "host_waiting" || event.Build != "" ||
			event.Mode != "host" || event.ProfileID == "" || event.Generation == 0 || event.LoopbackPort == 0 ||
			event.HostSPKISHA256 != "" {
			return ErrProtocol
		}
	case "peer_connected":
		if event.Code != CodeOK || event.State != "connected" || event.Build != "" ||
			event.Mode == "" || event.ProfileID == "" || event.Generation == 0 || event.LoopbackPort == 0 ||
			event.HostSPKISHA256 != "" {
			return ErrProtocol
		}
		// A host connection completes asynchronously after host_registered, so
		// it is the sole success event permitted to use the unsolicited id 0.
		// A guest response remains correlated to its open_peer command.
		if (event.Mode == "host") != (event.ID == 0) {
			return ErrProtocol
		}
	case "peer_closed":
		if event.ID == 0 || event.Code != CodeOK || event.State != "closed" || event.Build != "" ||
			event.Mode == "" || event.ProfileID == "" || event.Generation == 0 || event.LoopbackPort != 0 ||
			event.HostSPKISHA256 != "" {
			return ErrProtocol
		}
	case "stopped":
		if event.Code != CodeOK || event.State != "stopped" || event.Build != "" ||
			!peerFieldsEmpty || event.HostSPKISHA256 != "" {
			return ErrProtocol
		}
	case "error":
		if event.Code == "" || event.Code == CodeOK || event.State == "" || event.State == "stopped" ||
			event.Build != "" || !peerFieldsEmpty || event.HostSPKISHA256 != "" {
			return ErrProtocol
		}
	default:
		return ErrProtocol
	}
	return nil
}

var (
	buildPattern      = regexp.MustCompile(`\A[A-Za-z0-9][A-Za-z0-9._+\-]{0,95}\z`)
	profilePattern    = regexp.MustCompile(`\A[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?\z`)
	allowedEventTypes = map[string]struct{}{
		"ready": {}, "hello": {}, "host_prepared": {}, "host_registered": {},
		"peer_connected": {}, "peer_closed": {}, "stopped": {}, "error": {},
	}
	allowedEventCodes = map[string]struct{}{
		CodeOK: {}, CodeProtocolViolation: {}, CodePeerAlreadyOpen: {}, CodePeerNotOpen: {},
		CodeOpenFailed: {}, CodeIdentityNotPrepared: {}, CodeUnsupportedProfile: {}, CodeEnrollmentInvalid: {},
	}
	allowedEventStates = map[string]struct{}{
		"idle": {}, "identity_ready": {}, "connecting": {}, "host_waiting": {}, "connected": {},
		"closed": {}, "stopped": {}, "failed": {},
	}
)

func safeBuildID(build string) string {
	if len(build) > limits.MaxBuildIDBytes || !buildPattern.MatchString(build) {
		return "invalid-build"
	}
	return build
}

func requireEOF(decoder *json.Decoder) error {
	var extra any
	if err := decoder.Decode(&extra); !errors.Is(err, io.EOF) {
		return ErrProtocol
	}
	return nil
}

func rejectDuplicateFields(encoded []byte) error {
	decoder := json.NewDecoder(bytes.NewReader(encoded))
	decoder.UseNumber()
	if err := consumeValue(decoder); err != nil {
		return err
	}
	if _, err := decoder.Token(); !errors.Is(err, io.EOF) {
		return ErrProtocol
	}
	return nil
}

func consumeValue(decoder *json.Decoder) error {
	token, err := decoder.Token()
	if err != nil {
		return err
	}
	if token == nil {
		return ErrProtocol
	}
	delimiter, isDelimiter := token.(json.Delim)
	if !isDelimiter {
		return nil
	}
	switch delimiter {
	case '{':
		seen := make(map[string]struct{})
		for decoder.More() {
			keyToken, keyErr := decoder.Token()
			if keyErr != nil {
				return keyErr
			}
			key, ok := keyToken.(string)
			if !ok {
				return ErrProtocol
			}
			if _, exists := seen[key]; exists {
				return ErrDuplicateField
			}
			seen[key] = struct{}{}
			if valueErr := consumeValue(decoder); valueErr != nil {
				return valueErr
			}
		}
		end, endErr := decoder.Token()
		if endErr != nil || end != json.Delim('}') {
			return ErrProtocol
		}
	case '[':
		for decoder.More() {
			if valueErr := consumeValue(decoder); valueErr != nil {
				return valueErr
			}
		}
		end, endErr := decoder.Token()
		if endErr != nil || end != json.Delim(']') {
			return ErrProtocol
		}
	default:
		return ErrProtocol
	}
	return nil
}
