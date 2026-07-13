// Package wire defines the authenticated payload framing carried inside QUIC.
// QUIC authenticates these bytes; the explicit generation and sequence fields
// let WebJam reject stale paths and duplicate live packets deterministically.
package wire

import (
	"encoding/binary"
	"errors"
	"fmt"
	"io"

	"github.com/rupret007/webjam/transport/internal/limits"
)

const (
	datagramHeaderBytes = 18
	streamHeaderBytes   = 12
	datagramKindLive    = 1
)

var magic = [2]byte{'W', 'J'}

var (
	ErrBadMagic        = errors.New("invalid frame magic")
	ErrBadVersion      = errors.New("unsupported frame version")
	ErrBadKind         = errors.New("unsupported frame kind")
	ErrBadLength       = errors.New("invalid frame length")
	ErrReplay          = errors.New("duplicate or stale datagram")
	ErrStreamExhausted = errors.New("stream budget exhausted")
)

type DatagramFrame struct {
	Generation uint32
	Sequence   uint64
	Payload    []byte
}

func EncodeDatagram(generation uint32, sequence uint64, payload []byte) ([]byte, error) {
	if generation == 0 || len(payload) == 0 || len(payload) > limits.MaxLivePayloadBytes {
		return nil, ErrBadLength
	}
	encoded := make([]byte, datagramHeaderBytes+len(payload))
	copy(encoded[:2], magic[:])
	encoded[2] = limits.WireVersion
	encoded[3] = datagramKindLive
	binary.BigEndian.PutUint32(encoded[4:8], generation)
	binary.BigEndian.PutUint64(encoded[8:16], sequence)
	binary.BigEndian.PutUint16(encoded[16:18], uint16(len(payload)))
	copy(encoded[datagramHeaderBytes:], payload)
	return encoded, nil
}

func DecodeDatagram(encoded []byte) (DatagramFrame, error) {
	if len(encoded) < datagramHeaderBytes {
		return DatagramFrame{}, ErrBadLength
	}
	if encoded[0] != magic[0] || encoded[1] != magic[1] {
		return DatagramFrame{}, ErrBadMagic
	}
	if encoded[2] != limits.WireVersion {
		return DatagramFrame{}, ErrBadVersion
	}
	if encoded[3] != datagramKindLive {
		return DatagramFrame{}, ErrBadKind
	}
	payloadLength := int(binary.BigEndian.Uint16(encoded[16:18]))
	if payloadLength < 1 || payloadLength > limits.MaxLivePayloadBytes || len(encoded) != datagramHeaderBytes+payloadLength {
		return DatagramFrame{}, ErrBadLength
	}
	generation := binary.BigEndian.Uint32(encoded[4:8])
	if generation == 0 {
		return DatagramFrame{}, ErrBadLength
	}
	return DatagramFrame{
		Generation: generation,
		Sequence:   binary.BigEndian.Uint64(encoded[8:16]),
		Payload:    append([]byte(nil), encoded[datagramHeaderBytes:]...),
	}, nil
}

type StreamKind uint8

const (
	StreamKindControl StreamKind = 1
	StreamKindMedia   StreamKind = 2
)

func (k StreamKind) valid() bool {
	return k == StreamKindControl || k == StreamKindMedia
}

type StreamFrame struct {
	Kind       StreamKind
	Generation uint32
	Payload    []byte
}

func WriteStreamFrame(w io.Writer, frame StreamFrame) error {
	if !frame.Kind.valid() {
		return ErrBadKind
	}
	if frame.Generation == 0 || len(frame.Payload) > limits.MaxStreamFrameBytes {
		return ErrBadLength
	}
	header := make([]byte, streamHeaderBytes)
	copy(header[:2], magic[:])
	header[2] = limits.WireVersion
	header[3] = byte(frame.Kind)
	binary.BigEndian.PutUint32(header[4:8], frame.Generation)
	binary.BigEndian.PutUint32(header[8:12], uint32(len(frame.Payload)))
	if err := writeAll(w, header); err != nil {
		return err
	}
	return writeAll(w, frame.Payload)
}

func ReadStreamFrame(r io.Reader) (StreamFrame, error) {
	header := make([]byte, streamHeaderBytes)
	if _, err := io.ReadFull(r, header); err != nil {
		return StreamFrame{}, err
	}
	if header[0] != magic[0] || header[1] != magic[1] {
		return StreamFrame{}, ErrBadMagic
	}
	if header[2] != limits.WireVersion {
		return StreamFrame{}, ErrBadVersion
	}
	kind := StreamKind(header[3])
	if !kind.valid() {
		return StreamFrame{}, ErrBadKind
	}
	generation := binary.BigEndian.Uint32(header[4:8])
	length := binary.BigEndian.Uint32(header[8:12])
	if generation == 0 || length > limits.MaxStreamFrameBytes {
		return StreamFrame{}, ErrBadLength
	}
	payload := make([]byte, int(length))
	if _, err := io.ReadFull(r, payload); err != nil {
		return StreamFrame{}, err
	}
	return StreamFrame{Kind: kind, Generation: generation, Payload: payload}, nil
}

func writeAll(w io.Writer, payload []byte) error {
	for len(payload) > 0 {
		n, err := w.Write(payload)
		if err != nil {
			return err
		}
		if n <= 0 || n > len(payload) {
			return io.ErrShortWrite
		}
		payload = payload[n:]
	}
	return nil
}

// StreamReader applies a per-stream frame-count and total-byte budget.
type StreamReader struct {
	r          io.Reader
	framesRead int
	bytesRead  int64
}

func NewStreamReader(r io.Reader) *StreamReader { return &StreamReader{r: r} }

func (r *StreamReader) Next() (StreamFrame, error) {
	if r.framesRead >= limits.MaxStreamFrames || r.bytesRead >= limits.MaxStreamBytes {
		return StreamFrame{}, ErrStreamExhausted
	}
	frame, err := ReadStreamFrame(r.r)
	if err != nil {
		return StreamFrame{}, err
	}
	if r.bytesRead+int64(len(frame.Payload)) > limits.MaxStreamBytes {
		return StreamFrame{}, ErrStreamExhausted
	}
	r.framesRead++
	r.bytesRead += int64(len(frame.Payload))
	return frame, nil
}

func (r *StreamReader) String() string {
	return fmt.Sprintf("frames=%d bytes=%d", r.framesRead, r.bytesRead)
}

// ReplayWindow accepts each sequence once within a 64-packet reorder window.
// It is intentionally per generation; callers must construct/reset it when a
// new authenticated transport generation becomes active.
type ReplayWindow struct {
	initialized bool
	highest     uint64
	seen        uint64
}

func (w *ReplayWindow) Accept(sequence uint64) error {
	if !w.initialized {
		w.initialized = true
		w.highest = sequence
		w.seen = 1
		return nil
	}
	if sequence > w.highest {
		shift := sequence - w.highest
		if shift >= 64 {
			w.seen = 0
		} else {
			w.seen <<= shift
		}
		w.highest = sequence
		w.seen |= 1
		return nil
	}
	delta := w.highest - sequence
	if delta >= 64 {
		return ErrReplay
	}
	mask := uint64(1) << delta
	if w.seen&mask != 0 {
		return ErrReplay
	}
	w.seen |= mask
	return nil
}
