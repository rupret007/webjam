package help

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/rupret007/webjam/transport/internal/limits"
	"github.com/rupret007/webjam/transport/internal/wire"
)

type clock struct{ now time.Time }

func (c *clock) read() time.Time { return c.now }

type fakePlane struct {
	sent      []wire.StreamFrame
	accept    []wire.StreamFrame
	sendError error
}

func (p *fakePlane) Send(_ context.Context, frame wire.StreamFrame) error {
	if p.sendError != nil {
		return p.sendError
	}
	p.sent = append(p.sent, frame)
	return nil
}

func (p *fakePlane) Accept(context.Context) (wire.StreamFrame, error) {
	if len(p.accept) == 0 {
		return wire.StreamFrame{}, errors.New("no frame")
	}
	frame := p.accept[0]
	p.accept = p.accept[1:]
	return frame, nil
}

func testChannel(t *testing.T, plane *fakePlane, role Role, generation uint32, c *clock) *Channel {
	t.Helper()
	target, err := newChannel(plane, role, generation, c.read)
	if err != nil {
		t.Fatal(err)
	}
	return target
}

func streamFrame(t *testing.T, frame Frame, outerGeneration uint32) wire.StreamFrame {
	t.Helper()
	payload, err := EncodeFrame(frame)
	if err != nil {
		t.Fatal(err)
	}
	return wire.StreamFrame{Kind: wire.StreamKindControl, Generation: outerGeneration, Payload: payload}
}

func TestFrameRoundTripRequiresCanonicalPlainTextAndContext(t *testing.T) {
	t.Parallel()
	want := Frame{Kind: FrameMessage, Role: RoleGuest, Generation: 7, RequestID: 9, Text: "Need audio help — café"}
	encoded, err := EncodeFrame(want)
	if err != nil {
		t.Fatal(err)
	}
	got, err := DecodeFrame(encoded)
	if err != nil || got != want {
		t.Fatalf("round trip = %+v, %v", got, err)
	}

	decomposed := "cafe\u0301"
	if _, err = EncodeFrame(Frame{Kind: FrameMessage, Role: RoleGuest, Generation: 7, RequestID: 1, Text: decomposed}); !errors.Is(err, ErrInvalidMessage) {
		t.Fatalf("non-NFC error = %v", err)
	}
	for _, text := range []string{"", "   ", "<b>help</b>", "line\nbreak", strings.Repeat("é", 251)} {
		if _, err = EncodeFrame(Frame{Kind: FrameMessage, Role: RoleGuest, Generation: 7, RequestID: 1, Text: text}); !errors.Is(err, ErrInvalidMessage) {
			t.Fatalf("text %q error = %v", text, err)
		}
	}
	ack, err := EncodeFrame(Frame{Kind: FrameAck, Role: RoleHost, Generation: 7, RequestID: 9})
	if err != nil {
		t.Fatal(err)
	}
	if got, err = DecodeFrame(ack); err != nil || got.Kind != FrameAck || got.Text != "" {
		t.Fatalf("ack = %+v, %v", got, err)
	}
}

func TestMessageReceiptIsBoundToRoleGenerationAndPendingRequest(t *testing.T) {
	t.Parallel()
	c := &clock{now: time.Unix(100, 0)}
	hostPlane := &fakePlane{}
	host := testChannel(t, hostPlane, RoleHost, 7, c)
	if err := host.Send(context.Background(), 11, "Check your headphones"); err != nil {
		t.Fatal(err)
	}
	if len(hostPlane.sent) != 1 {
		t.Fatal("message was not sent")
	}
	message, err := DecodeFrame(hostPlane.sent[0].Payload)
	if err != nil || message.Role != RoleHost || message.RequestID != 11 {
		t.Fatalf("message = %+v, %v", message, err)
	}

	guestPlane := &fakePlane{accept: []wire.StreamFrame{hostPlane.sent[0]}}
	guest := testChannel(t, guestPlane, RoleGuest, 7, c)
	received, err := guest.Receive(context.Background())
	if err != nil || received.Kind != EventReceived || received.Text != "Check your headphones" {
		t.Fatalf("received = %+v, %v", received, err)
	}
	if len(guestPlane.sent) != 1 {
		t.Fatal("receipt was not sent")
	}
	hostPlane.accept = append(hostPlane.accept, guestPlane.sent[0])
	delivered, err := host.Receive(context.Background())
	if err != nil || delivered.Kind != EventDelivered || delivered.RequestID != 11 || delivered.Text != "" {
		t.Fatalf("delivered = %+v, %v", delivered, err)
	}
	hostPlane.accept = append(hostPlane.accept, guestPlane.sent[0])
	if _, err = host.Receive(context.Background()); !errors.Is(err, ErrUnexpectedAck) {
		t.Fatalf("duplicate receipt error = %v", err)
	}

	wrongRole := streamFrame(t, Frame{Kind: FrameMessage, Role: RoleGuest, Generation: 7, RequestID: 12, Text: "wrong"}, 7)
	guestPlane.accept = append(guestPlane.accept, wrongRole)
	if _, err = guest.Receive(context.Background()); !errors.Is(err, ErrWrongPeer) {
		t.Fatalf("wrong role error = %v", err)
	}
	wrongGeneration := streamFrame(t, Frame{Kind: FrameMessage, Role: RoleHost, Generation: 8, RequestID: 13, Text: "stale"}, 8)
	guestPlane.accept = append(guestPlane.accept, wrongGeneration)
	if _, err = guest.Receive(context.Background()); !errors.Is(err, ErrWrongGeneration) {
		t.Fatalf("wrong generation error = %v", err)
	}
}

func TestReplayRateAndPendingQueuesFailClosed(t *testing.T) {
	t.Parallel()
	c := &clock{now: time.Unix(100, 0)}
	plane := &fakePlane{}
	sender := testChannel(t, plane, RoleHost, 3, c)
	for requestID := uint64(1); requestID <= limits.MaxHelpPending; requestID++ {
		if requestID > limits.HelpMessageBurst {
			c.now = c.now.Add(2 * time.Second)
		}
		if err := sender.Send(context.Background(), requestID, "help"); err != nil {
			t.Fatalf("send %d: %v", requestID, err)
		}
	}
	if err := sender.Send(context.Background(), limits.MaxHelpPending+1, "full"); !errors.Is(err, ErrQueueFull) {
		t.Fatalf("queue error = %v", err)
	}
	if err := sender.Send(context.Background(), limits.MaxHelpPending, "replay"); !errors.Is(err, ErrReplay) {
		t.Fatalf("replay error = %v", err)
	}

	receiverPlane := &fakePlane{}
	receiver := testChannel(t, receiverPlane, RoleGuest, 3, c)
	for requestID := uint64(1); requestID <= limits.HelpMessageBurst; requestID++ {
		receiverPlane.accept = append(receiverPlane.accept, streamFrame(t, Frame{
			Kind: FrameMessage, Role: RoleHost, Generation: 3, RequestID: requestID, Text: "help",
		}, 3))
		if _, err := receiver.Receive(context.Background()); err != nil {
			t.Fatalf("receive %d: %v", requestID, err)
		}
	}
	receiverPlane.accept = append(receiverPlane.accept, streamFrame(t, Frame{
		Kind: FrameMessage, Role: RoleHost, Generation: 3, RequestID: 7, Text: "flood",
	}, 3))
	if _, err := receiver.Receive(context.Background()); !errors.Is(err, ErrRateLimited) {
		t.Fatalf("rate error = %v", err)
	}
}

func TestFailedSendDoesNotRetainPendingReceipt(t *testing.T) {
	t.Parallel()
	c := &clock{now: time.Unix(100, 0)}
	plane := &fakePlane{sendError: errors.New("closed")}
	target := testChannel(t, plane, RoleHost, 2, c)
	if err := target.Send(context.Background(), 1, "hello"); err == nil {
		t.Fatal("failed transport send succeeded")
	}
	if len(target.pending) != 0 {
		t.Fatal("failed send retained a pending receipt")
	}
}
