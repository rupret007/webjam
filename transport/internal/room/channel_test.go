package room

import (
	"context"
	"encoding/json"
	"errors"
	"testing"
	"time"

	"github.com/rupret007/webjam/transport/internal/help"
	"github.com/rupret007/webjam/transport/internal/wire"
)

type memoryPlane struct{ incoming, outgoing chan wire.StreamFrame }

func (p *memoryPlane) Send(ctx context.Context, frame wire.StreamFrame) error {
	frame.Payload = append([]byte(nil), frame.Payload...)
	select {
	case p.outgoing <- frame:
		return nil
	case <-ctx.Done():
		return ctx.Err()
	}
}
func (p *memoryPlane) Accept(ctx context.Context) (wire.StreamFrame, error) {
	select {
	case frame := <-p.incoming:
		return frame, nil
	case <-ctx.Done():
		return wire.StreamFrame{}, ctx.Err()
	}
}
func channels(t *testing.T, generation uint32) (*Channel, *Channel) {
	t.Helper()
	a, b := make(chan wire.StreamFrame, 32), make(chan wire.StreamFrame, 32)
	host, e := NewChannel(&memoryPlane{a, b}, help.RoleHost, generation)
	if e != nil {
		t.Fatal(e)
	}
	guest, e := NewChannel(&memoryPlane{b, a}, help.RoleGuest, generation)
	if e != nil {
		t.Fatal(e)
	}
	return host, guest
}
func connect(t *testing.T, host, guest *Channel) {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	results := make(chan error, 2)
	go func() { results <- host.Handshake(ctx) }()
	go func() { results <- guest.Handshake(ctx) }()
	for range 2 {
		if err := <-results; err != nil {
			t.Fatal(err)
		}
	}
}
func TestTypedRoomStateAndHelpUseOneReceiverAndIndependentRateDomains(t *testing.T) {
	host, guest := channels(t, 7)
	connect(t, host, guest)
	ctx := context.Background()
	state := testState()
	// The very first update contains already-shared media. No help command or
	// audio proof is needed to transfer that initial snapshot.
	for revision := uint64(1); revision <= 8; revision++ {
		state.Revision = revision
		if err := host.Publish(ctx, &state); err != nil {
			t.Fatal(err)
		}
		event, err := guest.Receive(ctx)
		if err != nil || event.State == nil || event.State.Revision != revision || event.State.SharedCanvas.JoinURL != state.SharedCanvas.JoinURL || event.Help != nil {
			t.Fatal("room state not delivered", err)
		}
	}
	state.Revision = 9
	if err := host.Publish(ctx, &state); !errors.Is(err, ErrRateLimited) {
		t.Fatal("room burst limit", err)
	}
	if err := host.SendHelp(ctx, 15, "Separate help"); err != nil {
		t.Fatal("room rate consumed help allowance", err)
	}
	received, err := guest.Receive(ctx)
	if err != nil || received.Help == nil || received.Help.Text != "Separate help" || received.State != nil {
		t.Fatal("help dispatch", err)
	}
	delivered, err := host.Receive(ctx)
	if err != nil || delivered.Help == nil || delivered.Help.Kind != help.EventDelivered || delivered.Help.RequestID != 15 {
		t.Fatal("help receipt", err)
	}
	clock := time.Now().Add(time.Second)
	host.now = func() time.Time { return clock }
	guest.now = func() time.Time { return clock }
	for revision := uint64(9); revision < 39; revision++ {
		clock = clock.Add(500 * time.Millisecond)
		state.Revision = revision
		if err := host.Publish(ctx, &state); err != nil {
			t.Fatal("500ms host refresh rejected", err)
		}
		if _, err := guest.Receive(ctx); err != nil {
			t.Fatal("500ms follow refresh rejected", err)
		}
	}
	if err := guest.Publish(ctx, &state); !errors.Is(err, ErrWrongPeer) {
		t.Fatal("guest acquired host authority", err)
	}
	if err := host.Publish(ctx, &state); !errors.Is(err, ErrReplay) {
		t.Fatal("duplicate revision accepted", err)
	}
}
func TestRoomReplayRoleGenerationAndResetFences(t *testing.T) {
	for _, kind := range []string{"replay", "outer_generation", "inner_generation", "role", "second_hello", "unknown", "media"} {
		t.Run(kind, func(t *testing.T) {
			host, guest := channels(t, 7)
			connect(t, host, guest)
			state := testState()
			ctx := context.Background()
			if err := host.Publish(ctx, &state); err != nil {
				t.Fatal(err)
			}
			if _, err := guest.Receive(ctx); err != nil {
				t.Fatal(err)
			}
			data, _ := json.Marshal(state)
			frame := host.frame(frameState, data)
			switch kind {
			case "outer_generation":
				frame.Generation = 8
			case "inner_generation":
				frame.Payload[11] = 8
			case "role":
				frame.Payload[6] = byte(help.RoleGuest)
			case "second_hello":
				frame = host.frame(frameHello, nil)
			case "unknown":
				frame.Payload[0] = 'X'
			case "media":
				frame.Kind = wire.StreamKindMedia
			}
			if err := host.plane.Send(ctx, frame); err != nil {
				t.Fatal(err)
			}
			if _, err := guest.Receive(ctx); err == nil {
				t.Fatal("invalid frame accepted")
			}
		})
	}
	host, guest := channels(t, 8)
	connect(t, host, guest)
	state := testState()
	if err := host.Publish(context.Background(), &state); err != nil {
		t.Fatal("fresh generation retained old revision", err)
	}
	if _, err := guest.Receive(context.Background()); err != nil {
		t.Fatal(err)
	}
	oldHost, _ := channels(t, 7)
	payload, _ := json.Marshal(state)
	frame := oldHost.frame(frameState, payload)
	if err := host.plane.Send(context.Background(), frame); err != nil {
		t.Fatal(err)
	}
	if _, err := guest.Receive(context.Background()); !errors.Is(err, ErrWrongGeneration) {
		t.Fatal("old generation resurrected", err)
	}
}
func TestOlderSilentOrHelpOnlyPeerFailsBeforeConnected(t *testing.T) {
	host, guest := channels(t, 7)
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Millisecond)
	defer cancel()
	started := time.Now()
	if err := host.Handshake(ctx); !errors.Is(err, ErrUnsupported) {
		t.Fatal(err)
	}
	if time.Since(started) > time.Second {
		t.Fatal("old-peer timeout unbounded")
	}
	if host.ready {
		t.Fatal("silent old peer became ready")
	}
	host, guest = channels(t, 7)
	payload, _ := help.EncodeFrame(help.Frame{Kind: help.FrameMessage, Role: help.RoleGuest, Generation: 7, RequestID: 1, Text: "Legacy help"})
	if err := guest.plane.Send(context.Background(), wire.StreamFrame{Kind: wire.StreamKindControl, Generation: 7, Payload: payload}); err != nil {
		t.Fatal(err)
	}
	if err := host.Handshake(context.Background()); !errors.Is(err, ErrUnsupported) {
		t.Fatal("help text substituted for room protocol", err)
	}
}

func TestSilentOlderPeerHonorsDefaultRoomHandshakeDeadline(t *testing.T) {
	host, _ := channels(t, 7)
	started := time.Now()
	if err := host.Handshake(context.Background()); !errors.Is(err, ErrUnsupported) {
		t.Fatal("silent older peer did not produce the update/rejoin boundary", err)
	}
	elapsed := time.Since(started)
	if elapsed < handshakeTimeout || elapsed > handshakeTimeout+2*time.Second {
		t.Fatalf("default handshake deadline was not bounded: %s", elapsed)
	}
	if host.ready {
		t.Fatal("an older silent peer was reported connected")
	}
}
