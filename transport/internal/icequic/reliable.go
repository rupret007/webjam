package icequic

import (
	"context"
	"errors"
	"io"
	"time"

	"github.com/rupret007/webjam/transport/internal/limits"
	"github.com/rupret007/webjam/transport/internal/wire"
)

var ErrTrailingStreamData = errors.New("reliable stream contains trailing data")

// ReliablePlane bounds concurrent streams and requires one bounded wire frame
// followed by FIN per stream. Bulk pacing remains an application policy above
// this primitive; callers must not send media while the live path is degraded.
type ReliablePlane struct {
	connection *Connection
}

func NewReliablePlane(connection *Connection) (*ReliablePlane, error) {
	if connection == nil {
		return nil, errors.New("reliable plane requires connection")
	}
	return &ReliablePlane{
		connection: connection,
	}, nil
}

func (p *ReliablePlane) Send(ctx context.Context, frame wire.StreamFrame) error {
	if err := p.connection.RequireAuthorized(); err != nil {
		return err
	}
	if err := p.acquire(ctx); err != nil {
		return err
	}
	defer p.release()
	stream, err := p.connection.OpenStreamSync(ctx)
	if err != nil {
		return err
	}
	// Each frame uses only the sending direction of a bidirectional QUIC
	// stream. Retire the unused receive direction so repeated control updates
	// return stream credit instead of stalling at the concurrency ceiling.
	defer stream.CancelRead(0)
	if err = stream.SetDeadline(operationDeadline(ctx)); err != nil {
		stream.CancelWrite(1)
		return err
	}
	if err = wire.WriteStreamFrame(stream, frame); err != nil {
		stream.CancelWrite(1)
		return err
	}
	return stream.Close()
}

func (p *ReliablePlane) Accept(ctx context.Context) (wire.StreamFrame, error) {
	if err := p.connection.RequireAuthorized(); err != nil {
		return wire.StreamFrame{}, err
	}
	if err := p.acquire(ctx); err != nil {
		return wire.StreamFrame{}, err
	}
	defer p.release()
	stream, err := p.connection.AcceptStream(ctx)
	if err != nil {
		return wire.StreamFrame{}, err
	}
	// No reply travels on this stream; acknowledgments use separate frames.
	// Finish the unused write side even when the inbound frame is invalid.
	defer stream.Close()
	if err = stream.SetDeadline(operationDeadline(ctx)); err != nil {
		stream.CancelRead(1)
		return wire.StreamFrame{}, err
	}
	frame, err := wire.ReadStreamFrame(stream)
	if err != nil {
		stream.CancelRead(1)
		return wire.StreamFrame{}, err
	}
	var trailing [1]byte
	n, trailingErr := stream.Read(trailing[:])
	if n != 0 || !errors.Is(trailingErr, io.EOF) {
		stream.CancelRead(1)
		return wire.StreamFrame{}, ErrTrailingStreamData
	}
	return frame, nil
}

func (p *ReliablePlane) acquire(ctx context.Context) error {
	select {
	case <-ctx.Done():
		return ctx.Err()
	case p.connection.streamSlots <- struct{}{}:
		return nil
	}
}

func (p *ReliablePlane) release() { <-p.connection.streamSlots }

func operationDeadline(ctx context.Context) time.Time {
	deadline := time.Now().Add(limits.StreamOperationLimit)
	if contextDeadline, ok := ctx.Deadline(); ok && contextDeadline.Before(deadline) {
		return contextDeadline
	}
	return deadline
}
