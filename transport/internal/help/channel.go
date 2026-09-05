// Package help carries bounded, ephemeral troubleshooting text over an
// already-authorized WebJam QUIC connection. It owns no socket, persistence,
// transcript, notification, or offline-delivery path.
package help

import (
	"context"
	"encoding/binary"
	"errors"
	"strings"
	"sync"
	"time"
	"unicode"
	"unicode/utf8"

	"github.com/rupret007/webjam/transport/internal/icequic"
	"github.com/rupret007/webjam/transport/internal/limits"
	"github.com/rupret007/webjam/transport/internal/wire"
	"golang.org/x/text/unicode/norm"
)

const (
	ProtocolVersion = 1
	headerBytes     = 22
)

var frameMagic = [4]byte{'W', 'J', 'H', '1'}

var (
	ErrInvalidMessage  = errors.New("invalid help message")
	ErrWrongPeer       = errors.New("help message has wrong peer role")
	ErrWrongGeneration = errors.New("help message has wrong generation")
	ErrReplay          = errors.New("duplicate or stale help message")
	ErrRateLimited     = errors.New("help message rate exceeded")
	ErrQueueFull       = errors.New("help delivery receipt queue is full")
	ErrUnexpectedAck   = errors.New("unexpected help delivery receipt")
	ErrNotReady        = errors.New("help channel is not ready")
)

type Role uint8

const (
	RoleHost Role = iota + 1
	RoleGuest
)

func (r Role) valid() bool { return r == RoleHost || r == RoleGuest }

func (r Role) opposite() Role {
	if r == RoleHost {
		return RoleGuest
	}
	return RoleHost
}

type FrameKind uint8

const (
	FrameMessage FrameKind = iota + 1
	FrameAck
)

type Frame struct {
	Kind       FrameKind
	Role       Role
	Generation uint32
	RequestID  uint64
	Text       string
}

type EventKind uint8

const (
	EventReceived EventKind = iota + 1
	EventDelivered
)

type Event struct {
	Kind      EventKind
	RequestID uint64
	Text      string
}

type reliablePlane interface {
	Send(context.Context, wire.StreamFrame) error
	Accept(context.Context) (wire.StreamFrame, error)
}

type tokenBucket struct {
	capacity float64
	rate     float64
	tokens   float64
	last     time.Time
}

func newTokenBucket(now time.Time) tokenBucket {
	return tokenBucket{
		capacity: limits.HelpMessageBurst,
		rate:     float64(limits.HelpMessagesPerMinute) / 60,
		tokens:   limits.HelpMessageBurst,
		last:     now,
	}
}

func (b *tokenBucket) allow(now time.Time) bool {
	elapsed := now.Sub(b.last).Seconds()
	if elapsed < 0 {
		elapsed = 0
	}
	b.last = now
	b.tokens += elapsed * b.rate
	if b.tokens > b.capacity {
		b.tokens = b.capacity
	}
	if b.tokens < 1 {
		return false
	}
	b.tokens--
	return true
}

// Channel is one generation-scoped, two-person help path. Closing its parent
// connection or context retires every pending receipt and all message text.
type Channel struct {
	role       Role
	generation uint32
	plane      reliablePlane
	now        func() time.Time

	mu            sync.Mutex
	lastSent      uint64
	received      wire.ReplayWindow
	pending       map[uint64]struct{}
	sendBucket    tokenBucket
	receiveBucket tokenBucket
}

func NewChannel(
	connection *icequic.Connection,
	role Role,
	generation uint32,
) (*Channel, error) {
	plane, err := icequic.NewReliablePlane(connection)
	if err != nil {
		return nil, err
	}
	return newChannel(plane, role, generation, time.Now)
}

// NewWithPlane shares a single authenticated reliable receiver with typed room state.
func NewWithPlane(plane interface {
	Send(context.Context, wire.StreamFrame) error
	Accept(context.Context) (wire.StreamFrame, error)
}, role Role, generation uint32) (*Channel, error) {
	return newChannel(plane, role, generation, time.Now)
}

func newChannel(
	plane reliablePlane,
	role Role,
	generation uint32,
	now func() time.Time,
) (*Channel, error) {
	if plane == nil || !role.valid() || generation == 0 || now == nil {
		return nil, ErrInvalidMessage
	}
	started := now()
	return &Channel{
		role: role, generation: generation, plane: plane, now: now,
		pending:    make(map[uint64]struct{}, limits.MaxHelpPending),
		sendBucket: newTokenBucket(started), receiveBucket: newTokenBucket(started),
	}, nil
}

// NormalizeText returns the sole wire spelling for one plain-text message.
func NormalizeText(raw string) (string, error) {
	if !utf8.ValidString(raw) {
		return "", ErrInvalidMessage
	}
	text := norm.NFC.String(raw)
	if text == "" || strings.TrimSpace(text) == "" || len([]byte(text)) > limits.MaxHelpTextBytes {
		return "", ErrInvalidMessage
	}
	for _, character := range text {
		if character == '<' || character == '>' || character == '\n' || character == '\r' || character == '\t' ||
			unicode.IsControl(character) || unicode.In(character, unicode.Cf, unicode.Co, unicode.Cs) {
			return "", ErrInvalidMessage
		}
	}
	return text, nil
}

func EncodeFrame(frame Frame) ([]byte, error) {
	if !frame.Role.valid() || frame.Generation == 0 || frame.RequestID == 0 {
		return nil, ErrInvalidMessage
	}
	text := ""
	switch frame.Kind {
	case FrameMessage:
		var err error
		text, err = NormalizeText(frame.Text)
		if err != nil || text != frame.Text {
			return nil, ErrInvalidMessage
		}
	case FrameAck:
		if frame.Text != "" {
			return nil, ErrInvalidMessage
		}
	default:
		return nil, ErrInvalidMessage
	}
	payload := []byte(text)
	encoded := make([]byte, headerBytes+len(payload))
	copy(encoded[:4], frameMagic[:])
	encoded[4] = ProtocolVersion
	encoded[5] = byte(frame.Kind)
	encoded[6] = byte(frame.Role)
	encoded[7] = 0
	binary.BigEndian.PutUint32(encoded[8:12], frame.Generation)
	binary.BigEndian.PutUint64(encoded[12:20], frame.RequestID)
	binary.BigEndian.PutUint16(encoded[20:22], uint16(len(payload)))
	copy(encoded[headerBytes:], payload)
	return encoded, nil
}

func DecodeFrame(encoded []byte) (Frame, error) {
	if len(encoded) < headerBytes || len(encoded) > headerBytes+limits.MaxHelpTextBytes ||
		string(encoded[:4]) != string(frameMagic[:]) || encoded[4] != ProtocolVersion || encoded[7] != 0 {
		return Frame{}, ErrInvalidMessage
	}
	kind := FrameKind(encoded[5])
	role := Role(encoded[6])
	generation := binary.BigEndian.Uint32(encoded[8:12])
	requestID := binary.BigEndian.Uint64(encoded[12:20])
	textLength := int(binary.BigEndian.Uint16(encoded[20:22]))
	if !role.valid() || generation == 0 || requestID == 0 || textLength != len(encoded)-headerBytes {
		return Frame{}, ErrInvalidMessage
	}
	text := string(encoded[headerBytes:])
	switch kind {
	case FrameMessage:
		normalized, err := NormalizeText(text)
		if err != nil || normalized != text {
			return Frame{}, ErrInvalidMessage
		}
	case FrameAck:
		if text != "" {
			return Frame{}, ErrInvalidMessage
		}
	default:
		return Frame{}, ErrInvalidMessage
	}
	return Frame{Kind: kind, Role: role, Generation: generation, RequestID: requestID, Text: text}, nil
}

// Send queues one delivery receipt before opening the authenticated stream.
// A transport failure removes the receipt so callers may show honest failure.
func (c *Channel) Send(ctx context.Context, requestID uint64, raw string) error {
	text, err := NormalizeText(raw)
	if err != nil {
		return err
	}
	c.mu.Lock()
	if requestID == 0 || requestID <= c.lastSent {
		c.mu.Unlock()
		return ErrReplay
	}
	if len(c.pending) >= limits.MaxHelpPending {
		c.mu.Unlock()
		return ErrQueueFull
	}
	if !c.sendBucket.allow(c.now()) {
		c.mu.Unlock()
		return ErrRateLimited
	}
	c.lastSent = requestID
	c.pending[requestID] = struct{}{}
	c.mu.Unlock()

	payload, err := EncodeFrame(Frame{
		Kind: FrameMessage, Role: c.role, Generation: c.generation,
		RequestID: requestID, Text: text,
	})
	if err == nil {
		err = c.plane.Send(ctx, wire.StreamFrame{
			Kind: wire.StreamKindControl, Generation: c.generation, Payload: payload,
		})
	}
	if err != nil {
		c.mu.Lock()
		delete(c.pending, requestID)
		c.mu.Unlock()
	}
	return err
}

// Receive accepts one authenticated control stream. Messages are acknowledged
// at the peer transport boundary; acknowledgements never claim the UI showed
// or a person read the text.
func (c *Channel) Receive(ctx context.Context) (Event, error) {
	stream, err := c.plane.Accept(ctx)
	if err != nil {
		return Event{}, err
	}
	return c.HandleFrame(ctx, stream)
}

// HandleFrame validates a frame supplied by the sole reliable dispatcher.
func (c *Channel) HandleFrame(ctx context.Context, stream wire.StreamFrame) (Event, error) {
	var err error
	if stream.Kind != wire.StreamKindControl {
		return Event{}, ErrInvalidMessage
	}
	if stream.Generation != c.generation {
		return Event{}, ErrWrongGeneration
	}
	frame, err := DecodeFrame(stream.Payload)
	if err != nil {
		return Event{}, err
	}
	if frame.Generation != c.generation {
		return Event{}, ErrWrongGeneration
	}
	if frame.Role != c.role.opposite() {
		return Event{}, ErrWrongPeer
	}

	switch frame.Kind {
	case FrameMessage:
		c.mu.Lock()
		if !c.receiveBucket.allow(c.now()) {
			c.mu.Unlock()
			return Event{}, ErrRateLimited
		}
		if err = c.received.Accept(frame.RequestID); err != nil {
			c.mu.Unlock()
			return Event{}, ErrReplay
		}
		c.mu.Unlock()
		ack, encodeErr := EncodeFrame(Frame{
			Kind: FrameAck, Role: c.role, Generation: c.generation,
			RequestID: frame.RequestID,
		})
		if encodeErr != nil {
			return Event{}, encodeErr
		}
		if err = c.plane.Send(ctx, wire.StreamFrame{
			Kind: wire.StreamKindControl, Generation: c.generation, Payload: ack,
		}); err != nil {
			return Event{}, err
		}
		return Event{Kind: EventReceived, RequestID: frame.RequestID, Text: frame.Text}, nil
	case FrameAck:
		c.mu.Lock()
		_, waiting := c.pending[frame.RequestID]
		if waiting {
			delete(c.pending, frame.RequestID)
		}
		c.mu.Unlock()
		if !waiting {
			return Event{}, ErrUnexpectedAck
		}
		return Event{Kind: EventDelivered, RequestID: frame.RequestID}, nil
	default:
		return Event{}, ErrInvalidMessage
	}
}
