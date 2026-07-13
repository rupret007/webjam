package wire

import (
	"bytes"
	"errors"
	"testing"

	"github.com/rupret007/webjam/transport/internal/limits"
)

func TestDatagramRoundTripAndBounds(t *testing.T) {
	t.Parallel()
	payload := bytes.Repeat([]byte{0x5a}, 660)
	encoded, err := EncodeDatagram(7, 42, payload)
	if err != nil {
		t.Fatal(err)
	}
	frame, err := DecodeDatagram(encoded)
	if err != nil {
		t.Fatal(err)
	}
	if frame.Generation != 7 || frame.Sequence != 42 || !bytes.Equal(frame.Payload, payload) {
		t.Fatalf("unexpected frame: %+v", frame)
	}
	encoded = append(encoded, 0)
	if _, err = DecodeDatagram(encoded); !errors.Is(err, ErrBadLength) {
		t.Fatalf("trailing byte error = %v", err)
	}
	if _, err = EncodeDatagram(1, 0, make([]byte, limits.MaxLivePayloadBytes+1)); !errors.Is(err, ErrBadLength) {
		t.Fatalf("oversize error = %v", err)
	}
}

func TestStreamFrameRoundTripAndPartialWrites(t *testing.T) {
	t.Parallel()
	var raw bytes.Buffer
	w := &oneByteWriter{w: &raw}
	want := StreamFrame{Kind: StreamKindMedia, Generation: 9, Payload: []byte("bounded media chunk")}
	if err := WriteStreamFrame(w, want); err != nil {
		t.Fatal(err)
	}
	got, err := NewStreamReader(&raw).Next()
	if err != nil {
		t.Fatal(err)
	}
	if got.Kind != want.Kind || got.Generation != want.Generation || !bytes.Equal(got.Payload, want.Payload) {
		t.Fatalf("got %+v, want %+v", got, want)
	}
}

func TestReplayWindow(t *testing.T) {
	t.Parallel()
	var window ReplayWindow
	for _, sequence := range []uint64{10, 12, 11, 75} {
		if err := window.Accept(sequence); err != nil {
			t.Fatalf("sequence %d: %v", sequence, err)
		}
	}
	if err := window.Accept(75); !errors.Is(err, ErrReplay) {
		t.Fatalf("duplicate error = %v", err)
	}
	if err := window.Accept(10); !errors.Is(err, ErrReplay) {
		t.Fatalf("stale error = %v", err)
	}
}

type oneByteWriter struct{ w *bytes.Buffer }

func (w *oneByteWriter) Write(payload []byte) (int, error) {
	if len(payload) == 0 {
		return 0, nil
	}
	return w.w.Write(payload[:1])
}
