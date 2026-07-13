// Package queue implements bounded ownership queues for latency-sensitive
// datagrams. A full live queue drops the new packet; it never grows memory or
// blocks the loopback reader behind bulk work.
package queue

import (
	"context"
	"errors"
	"sync"
)

var (
	ErrClosed = errors.New("datagram queue closed")
	ErrFull   = errors.New("datagram queue full")
	ErrTooBig = errors.New("datagram exceeds queue limit")
)

type Datagram struct {
	Payload []byte
}

type DatagramQueue struct {
	items      chan Datagram
	closed     chan struct{}
	closeOnce  sync.Once
	maxPayload int
}

func NewDatagramQueue(capacity, maxPayload int) (*DatagramQueue, error) {
	if capacity < 1 || maxPayload < 1 {
		return nil, errors.New("invalid datagram queue bounds")
	}
	return &DatagramQueue{
		items:      make(chan Datagram, capacity),
		closed:     make(chan struct{}),
		maxPayload: maxPayload,
	}, nil
}

func (q *DatagramQueue) Push(payload []byte) error {
	select {
	case <-q.closed:
		return ErrClosed
	default:
	}
	if len(payload) == 0 || len(payload) > q.maxPayload {
		return ErrTooBig
	}
	copyPayload := append([]byte(nil), payload...)
	select {
	case <-q.closed:
		return ErrClosed
	case q.items <- Datagram{Payload: copyPayload}:
		return nil
	default:
		return ErrFull
	}
}

func (q *DatagramQueue) Pop(ctx context.Context) (Datagram, error) {
	select {
	case <-ctx.Done():
		return Datagram{}, ctx.Err()
	case <-q.closed:
		return Datagram{}, ErrClosed
	case item := <-q.items:
		return item, nil
	}
}

func (q *DatagramQueue) Len() int { return len(q.items) }

func (q *DatagramQueue) Close() {
	q.closeOnce.Do(func() { close(q.closed) })
}
