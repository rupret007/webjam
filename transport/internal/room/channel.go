package room

import (
	"context"
	"encoding/binary"
	"encoding/json"
	"errors"
	"sync"
	"time"

	"github.com/rupret007/webjam/transport/internal/help"
	"github.com/rupret007/webjam/transport/internal/limits"
	"github.com/rupret007/webjam/transport/internal/wire"
)

var (
	ErrUnsupported     = errors.New("peer room protocol unsupported")
	ErrNotReady        = errors.New("room state channel is not ready")
	ErrReplay          = errors.New("stale room state")
	ErrWrongPeer       = errors.New("room state has wrong peer role")
	ErrWrongGeneration = errors.New("room state has wrong generation")
	ErrRateLimited     = errors.New("room state rate exceeded")
)

const handshakeTimeout = 3 * time.Second
const headerBytes = 12
const frameHello byte = 1
const frameState byte = 2

var magic = [4]byte{'W', 'J', 'R', '1'}

type Plane interface {
	Send(context.Context, wire.StreamFrame) error
	Accept(context.Context) (wire.StreamFrame, error)
}
type Event struct {
	Help  *help.Event
	State *State
}

// Channel is the sole Accept owner. Help and typed room state have independent
// rate/replay domains; receiving either never grants authority over the other.
type Channel struct {
	plane         Plane
	role          help.Role
	generation    uint32
	help          *help.Channel
	mu            sync.Mutex
	ready         bool
	lastSent      uint64
	lastReceived  uint64
	sendBucket    bucket
	receiveBucket bucket
	now           func() time.Time
}
type bucket struct {
	tokens float64
	last   time.Time
}

func (b *bucket) allow(now time.Time) bool {
	elapsed := now.Sub(b.last).Seconds()
	if elapsed < 0 {
		elapsed = 0
	}
	b.last = now
	b.tokens += elapsed * 4
	if b.tokens > 8 {
		b.tokens = 8
	}
	if b.tokens < 1 {
		return false
	}
	b.tokens--
	return true
}
func NewChannel(plane Plane, role help.Role, generation uint32) (*Channel, error) {
	if plane == nil || (role != help.RoleHost && role != help.RoleGuest) || generation == 0 {
		return nil, ErrInvalid
	}
	h, err := help.NewWithPlane(plane, role, generation)
	if err != nil {
		return nil, err
	}
	now := time.Now()
	return &Channel{plane: plane, role: role, generation: generation, help: h, sendBucket: bucket{8, now}, receiveBucket: bucket{8, now}, now: time.Now}, nil
}
func (c *Channel) frame(kind byte, payload []byte) wire.StreamFrame {
	encoded := make([]byte, headerBytes+len(payload))
	copy(encoded, magic[:])
	encoded[4] = 1
	encoded[5] = kind
	encoded[6] = byte(c.role)
	binary.BigEndian.PutUint32(encoded[8:12], c.generation)
	copy(encoded[headerBytes:], payload)
	return wire.StreamFrame{Kind: wire.StreamKindControl, Generation: c.generation, Payload: encoded}
}
func (c *Channel) decode(stream wire.StreamFrame) (byte, []byte, error) {
	data := stream.Payload
	if stream.Kind != wire.StreamKindControl || len(data) < headerBytes || len(data) > headerBytes+limits.MaxRoomStateBytes || string(data[:4]) != string(magic[:]) || data[4] != 1 || data[7] != 0 {
		return 0, nil, ErrInvalid
	}
	if stream.Generation != c.generation || binary.BigEndian.Uint32(data[8:12]) != c.generation {
		return 0, nil, ErrWrongGeneration
	}
	expected := help.RoleHost
	if c.role == help.RoleHost {
		expected = help.RoleGuest
	}
	if help.Role(data[6]) != expected {
		return 0, nil, ErrWrongPeer
	}
	return data[5], data[headerBytes:], nil
}

// Handshake proves this authorized peer understands the typed room channel.
// It finishes before peer_connected; older peers receive a bounded update/rejoin
// failure, never a false room connection or a hidden help-message workaround.
func (c *Channel) Handshake(ctx context.Context) error {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.ready {
		return ErrInvalid
	}
	handshakeCtx, cancel := context.WithTimeout(ctx, handshakeTimeout)
	defer cancel()
	if err := c.plane.Send(handshakeCtx, c.frame(frameHello, nil)); err != nil {
		return ErrUnsupported
	}
	stream, err := c.plane.Accept(handshakeCtx)
	if err != nil {
		return ErrUnsupported
	}
	kind, payload, err := c.decode(stream)
	clear(stream.Payload)
	if err != nil || kind != frameHello || len(payload) != 0 {
		return ErrUnsupported
	}
	c.ready = true
	return nil
}
func (c *Channel) SendHelp(ctx context.Context, requestID uint64, text string) error {
	c.mu.Lock()
	ready := c.ready
	c.mu.Unlock()
	if !ready {
		return help.ErrNotReady
	}
	return c.help.Send(ctx, requestID, text)
}
func (c *Channel) Publish(ctx context.Context, state *State) error {
	if state == nil || state.Validate() != nil {
		return ErrInvalid
	}
	payload, err := json.Marshal(state)
	if err != nil || len(payload) > limits.MaxRoomStateBytes {
		return ErrInvalid
	}
	defer clear(payload)
	// Serialize publishers through stream creation so strict revisions cannot
	// overtake each other. There is no unbounded outbound queue.
	c.mu.Lock()
	defer c.mu.Unlock()
	if !c.ready {
		return ErrNotReady
	}
	if c.role != help.RoleHost {
		return ErrWrongPeer
	}
	if state.Revision <= c.lastSent {
		return ErrReplay
	}
	if !c.sendBucket.allow(c.now()) {
		return ErrRateLimited
	}
	frame := c.frame(frameState, payload)
	defer clear(frame.Payload)
	if err := c.plane.Send(ctx, frame); err != nil {
		return err
	}
	c.lastSent = state.Revision
	return nil
}
func (c *Channel) Receive(ctx context.Context) (Event, error) {
	c.mu.Lock()
	ready := c.ready
	c.mu.Unlock()
	if !ready {
		return Event{}, ErrNotReady
	}
	stream, err := c.plane.Accept(ctx)
	if err != nil {
		return Event{}, err
	}
	defer clear(stream.Payload)
	if len(stream.Payload) >= 4 && string(stream.Payload[:4]) == "WJH1" {
		event, err := c.help.HandleFrame(ctx, stream)
		if err != nil {
			return Event{}, err
		}
		return Event{Help: &event}, nil
	}
	kind, payload, err := c.decode(stream)
	if err != nil {
		return Event{}, err
	}
	if kind != frameState {
		return Event{}, ErrInvalid
	}
	if c.role != help.RoleGuest {
		return Event{}, ErrWrongPeer
	}
	state, err := Decode(payload)
	if err != nil {
		return Event{}, err
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	if state.Revision <= c.lastReceived {
		return Event{}, ErrReplay
	}
	if !c.receiveBucket.allow(c.now()) {
		return Event{}, ErrRateLimited
	}
	c.lastReceived = state.Revision
	return Event{State: state}, nil
}
