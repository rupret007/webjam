package reference

import (
	"bufio"
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"io"
	"net"
	"sync"
	"time"
)

const controlOperationLimit = 5 * time.Second

type Client struct {
	mu     sync.Mutex
	conn   net.Conn
	reader *bufio.Reader
	closed bool
}

type wireRequest struct {
	Version         int    `json:"v"`
	Operation       string `json:"op"`
	Session         string `json:"session,omitempty"`
	HostToken       string `json:"host_token,omitempty"`
	EnrollmentToken string `json:"enrollment_token,omitempty"`
	GuestToken      string `json:"guest_token,omitempty"`
	Role            string `json:"role,omitempty"`
	Token           string `json:"token,omitempty"`
	Generation      uint32 `json:"generation,omitempty"`
	Sequence        uint64 `json:"sequence,omitempty"`
	TTLSeconds      int    `json:"ttl_seconds,omitempty"`
	SealedPayload   string `json:"sealed_payload,omitempty"`
}

type wireResponse struct {
	Version          int      `json:"v"`
	OK               bool     `json:"ok"`
	Error            string   `json:"error,omitempty"`
	Generation       uint32   `json:"generation,omitempty"`
	ParticipantLimit int      `json:"participant_limit,omitempty"`
	TTLSeconds       int      `json:"ttl_seconds,omitempty"`
	SealedPayloads   []string `json:"sealed_payloads,omitempty"`
}

type responseKind uint8

const (
	responseRegister responseKind = iota
	responseEnroll
	responseSignal
	responsePoll
	responseClose
)

func DialLocal(ctx context.Context) (*Client, error) {
	if ctx == nil {
		return nil, ErrInvalidInput
	}
	dialer := net.Dialer{Timeout: controlOperationLimit, KeepAlive: -1}
	conn, err := dialer.DialContext(ctx, "tcp4", ControlAddress)
	if err != nil {
		if ctx.Err() != nil {
			return nil, ctx.Err()
		}
		return nil, ErrControlUnavailable
	}
	return newClient(conn), nil
}

func newClient(conn net.Conn) *Client {
	return &Client{conn: conn, reader: bufio.NewReaderSize(conn, MaxControlFrameBytes)}
}

func (c *Client) Register(
	ctx context.Context,
	session SessionID,
	hostToken *RoleToken,
	enrollment *EnrollmentToken,
	generation uint32,
	ttl time.Duration,
) error {
	if !nonzero(session) || !hostToken.valid() || !enrollment.validFor(session) || generation == 0 ||
		ttl < 30*time.Second || ttl > 10*time.Minute || ttl%time.Second != 0 ||
		bytes.Equal(hostToken.value[:], enrollment.value[:]) {
		return ErrInvalidInput
	}
	response, err := c.roundTrip(ctx, wireRequest{
		Version: ProtocolVersion, Operation: "register", Session: encode(session[:]),
		HostToken: encode(hostToken.value[:]), EnrollmentToken: encode(enrollment.value[:]),
		Generation: generation, TTLSeconds: int(ttl / time.Second),
	}, responseRegister)
	if err != nil {
		return err
	}
	if response.Generation != generation || response.TTLSeconds != int(ttl/time.Second) {
		return c.protocolFailure()
	}
	return nil
}

func (c *Client) Enroll(
	ctx context.Context,
	session SessionID,
	enrollment *EnrollmentToken,
	guestToken *RoleToken,
) error {
	if !nonzero(session) || !enrollment.validFor(session) || !guestToken.valid() ||
		bytes.Equal(guestToken.value[:], enrollment.value[:]) {
		return ErrInvalidInput
	}
	_, err := c.roundTrip(ctx, wireRequest{
		Version: ProtocolVersion, Operation: "enroll", Session: encode(session[:]),
		EnrollmentToken: encode(enrollment.value[:]), GuestToken: encode(guestToken.value[:]),
	}, responseEnroll)
	return err
}

func (c *Client) Signal(
	ctx context.Context,
	session SessionID,
	role Role,
	token *RoleToken,
	generation uint32,
	sequence uint64,
	sealed []byte,
) error {
	if !validAuthenticated(session, role, token, generation, sequence) ||
		len(sealed) < 16 || len(sealed) > MaxSignalPayloadBytes {
		return ErrInvalidInput
	}
	_, err := c.roundTrip(ctx, wireRequest{
		Version: ProtocolVersion, Operation: "signal", Session: encode(session[:]),
		Role: role.text(), Token: encode(token.value[:]), Generation: generation,
		Sequence: sequence, SealedPayload: encode(sealed),
	}, responseSignal)
	return err
}

func (c *Client) Poll(
	ctx context.Context,
	session SessionID,
	role Role,
	token *RoleToken,
	generation uint32,
	sequence uint64,
) ([]byte, bool, error) {
	if !validAuthenticated(session, role, token, generation, sequence) {
		return nil, false, ErrInvalidInput
	}
	response, err := c.roundTrip(ctx, wireRequest{
		Version: ProtocolVersion, Operation: "poll", Session: encode(session[:]),
		Role: role.text(), Token: encode(token.value[:]), Generation: generation, Sequence: sequence,
	}, responsePoll)
	if err != nil {
		return nil, false, err
	}
	if len(response.SealedPayloads) == 0 {
		return nil, false, nil
	}
	decoded, err := decodeOpaque(response.SealedPayloads[0])
	if err != nil {
		return nil, false, c.protocolFailure()
	}
	return decoded, true, nil
}

func (c *Client) CloseSession(
	ctx context.Context,
	session SessionID,
	role Role,
	token *RoleToken,
	generation uint32,
	sequence uint64,
) error {
	if !validAuthenticated(session, role, token, generation, sequence) || role != RoleHost {
		return ErrInvalidInput
	}
	_, err := c.roundTrip(ctx, wireRequest{
		Version: ProtocolVersion, Operation: "close", Session: encode(session[:]),
		Role: role.text(), Token: encode(token.value[:]), Generation: generation, Sequence: sequence,
	}, responseClose)
	return err
}

func (c *Client) Close() error {
	if c == nil {
		return nil
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.closed {
		return nil
	}
	c.closed = true
	if c.conn != nil {
		if err := c.conn.Close(); err != nil {
			return ErrControlUnavailable
		}
	}
	return nil
}

func validAuthenticated(
	session SessionID, role Role, token *RoleToken, generation uint32, sequence uint64,
) bool {
	return nonzero(session) && role.valid() && token.valid() && generation != 0 &&
		sequence > 0 && sequence <= uint64(^uint64(0)>>1)
}

func (c *Client) roundTrip(
	ctx context.Context, request wireRequest, kind responseKind,
) (wireResponse, error) {
	if c == nil || ctx == nil {
		return wireResponse{}, ErrInvalidInput
	}
	encoded, err := json.Marshal(request)
	request = wireRequest{}
	if err != nil || len(encoded)+1 > MaxControlFrameBytes {
		return wireResponse{}, ErrInvalidInput
	}
	encoded = append(encoded, '\n')
	defer clear(encoded)
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.closed || c.conn == nil {
		return wireResponse{}, ErrClosed
	}
	if err = ctx.Err(); err != nil {
		return wireResponse{}, err
	}
	deadline := time.Now().Add(controlOperationLimit)
	if contextDeadline, ok := ctx.Deadline(); ok && contextDeadline.Before(deadline) {
		deadline = contextDeadline
	}
	if err = c.conn.SetDeadline(deadline); err != nil {
		return wireResponse{}, c.unavailableFailure()
	}
	stop := context.AfterFunc(ctx, func() { _ = c.conn.SetDeadline(time.Now()) })
	defer func() {
		stopped := stop()
		if stopped && !c.closed {
			_ = c.conn.SetDeadline(time.Time{})
		} else if !stopped && !c.closed {
			c.closed = true
			_ = c.conn.Close()
		}
	}()
	if err = writeAll(c.conn, encoded); err != nil {
		return wireResponse{}, c.networkFailure(ctx)
	}
	line, err := c.reader.ReadSlice('\n')
	if errors.Is(err, bufio.ErrBufferFull) || len(line) > MaxControlFrameBytes {
		return wireResponse{}, c.protocolFailure()
	}
	if err != nil || len(line) == 0 {
		return wireResponse{}, c.networkFailure(ctx)
	}
	defer clear(line)
	response, err := decodeResponse(line, kind)
	if err != nil {
		return wireResponse{}, c.protocolFailure()
	}
	if !response.OK {
		mapped := mapServiceError(response.Error)
		if errors.Is(mapped, ErrControlProtocol) {
			return wireResponse{}, c.protocolFailure()
		}
		return wireResponse{}, mapped
	}
	return response, nil
}

func decodeResponse(line []byte, kind responseKind) (wireResponse, error) {
	if len(line) == 0 || line[len(line)-1] != '\n' || len(line) > MaxControlFrameBytes {
		return wireResponse{}, ErrControlProtocol
	}
	fields, err := responseFields(line)
	if err != nil {
		return wireResponse{}, err
	}
	decoder := json.NewDecoder(bytes.NewReader(line))
	decoder.DisallowUnknownFields()
	var response wireResponse
	if err := decoder.Decode(&response); err != nil {
		return wireResponse{}, ErrControlProtocol
	}
	if token, err := decoder.Token(); !errors.Is(err, io.EOF) || token != nil {
		return wireResponse{}, ErrControlProtocol
	}
	if response.Version != ProtocolVersion {
		return wireResponse{}, ErrControlProtocol
	}
	if !response.OK {
		if response.Error == "" || response.Generation != 0 || response.ParticipantLimit != 0 ||
			response.TTLSeconds != 0 || response.SealedPayloads != nil ||
			!exactFields(fields, "v", "ok", "error") {
			return wireResponse{}, ErrControlProtocol
		}
		return response, nil
	}
	if response.Error != "" {
		return wireResponse{}, ErrControlProtocol
	}
	switch kind {
	case responseRegister:
		if response.Generation == 0 || response.ParticipantLimit != 1 || response.TTLSeconds < 30 ||
			response.TTLSeconds > 600 || response.SealedPayloads != nil ||
			!exactFields(fields, "v", "ok", "generation", "participant_limit", "ttl_seconds") {
			return wireResponse{}, ErrControlProtocol
		}
	case responseEnroll:
		if response.Generation != 0 || response.ParticipantLimit != 1 || response.TTLSeconds < 0 ||
			response.TTLSeconds > 600 || response.SealedPayloads != nil ||
			!exactFields(fields, "v", "ok", "participant_limit", "ttl_seconds") {
			return wireResponse{}, ErrControlProtocol
		}
	case responseSignal, responseClose:
		if response.Generation != 0 || response.ParticipantLimit != 0 || response.TTLSeconds != 0 ||
			response.SealedPayloads != nil || !exactFields(fields, "v", "ok") {
			return wireResponse{}, ErrControlProtocol
		}
	case responsePoll:
		if response.Generation != 0 || response.ParticipantLimit != 0 || response.TTLSeconds != 0 ||
			response.SealedPayloads == nil || len(response.SealedPayloads) > 1 ||
			!exactFields(fields, "v", "ok", "sealed_payloads") {
			return wireResponse{}, ErrControlProtocol
		}
	default:
		return wireResponse{}, ErrControlProtocol
	}
	return response, nil
}

func responseFields(line []byte) (map[string]struct{}, error) {
	decoder := json.NewDecoder(bytes.NewReader(line))
	opening, err := decoder.Token()
	if err != nil || opening != json.Delim('{') {
		return nil, ErrControlProtocol
	}
	seen := make(map[string]struct{}, 8)
	for decoder.More() {
		field, err := decoder.Token()
		name, ok := field.(string)
		if err != nil || !ok {
			return nil, ErrControlProtocol
		}
		if _, exists := seen[name]; exists {
			return nil, ErrControlProtocol
		}
		seen[name] = struct{}{}
		var raw json.RawMessage
		if err = decoder.Decode(&raw); err != nil {
			return nil, ErrControlProtocol
		}
	}
	closing, err := decoder.Token()
	if err != nil || closing != json.Delim('}') {
		return nil, ErrControlProtocol
	}
	return seen, nil
}

func exactFields(fields map[string]struct{}, expected ...string) bool {
	if len(fields) != len(expected) {
		return false
	}
	for _, field := range expected {
		if _, exists := fields[field]; !exists {
			return false
		}
	}
	return true
}

func writeAll(writer io.Writer, payload []byte) error {
	for len(payload) > 0 {
		n, err := writer.Write(payload)
		if err != nil || n <= 0 {
			return ErrControlUnavailable
		}
		payload = payload[n:]
	}
	return nil
}

func encode(value []byte) string { return base64.RawURLEncoding.EncodeToString(value) }

func decodeOpaque(value string) ([]byte, error) {
	if value == "" || len(value) > base64.RawURLEncoding.EncodedLen(MaxSignalPayloadBytes) {
		return nil, ErrControlProtocol
	}
	decoded, err := base64.RawURLEncoding.DecodeString(value)
	if err != nil || len(decoded) < 16 || len(decoded) > MaxSignalPayloadBytes || encode(decoded) != value {
		return nil, ErrControlProtocol
	}
	return decoded, nil
}

func mapServiceError(code string) error {
	switch code {
	case "invalid_enrollment", "unauthorized":
		return ErrUnauthorized
	case "session_conflict":
		return ErrSessionConflict
	case "enrollment_used":
		return ErrEnrollmentUsed
	case "replay", "session_replayed":
		return ErrReplay
	case "overloaded", "rate_limited":
		return ErrOverloaded
	case "queue_full":
		return ErrQueueFull
	case "malformed", "invalid_ttl":
		return ErrInvalidInput
	default:
		return ErrControlProtocol
	}
}

func (c *Client) protocolFailure() error {
	c.closed = true
	if c.conn != nil {
		_ = c.conn.Close()
	}
	return ErrControlProtocol
}

func (c *Client) unavailableFailure() error {
	c.closed = true
	if c.conn != nil {
		_ = c.conn.Close()
	}
	return ErrControlUnavailable
}

func (c *Client) networkFailure(ctx context.Context) error {
	_ = c.unavailableFailure()
	if ctx != nil {
		if err := ctx.Err(); err != nil {
			return err
		}
		// A socket deadline set from the context can fire in the tiny interval
		// before context's timer goroutine publishes DeadlineExceeded.
		if deadline, ok := ctx.Deadline(); ok && !time.Now().Before(deadline) {
			return context.DeadlineExceeded
		}
	}
	return ErrControlUnavailable
}
