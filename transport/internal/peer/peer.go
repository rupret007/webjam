// Package peer owns one live peer data plane. It connects a loopback Jamulus
// endpoint to one authenticated QUIC connection with bounded queues, explicit
// generation checks, replay rejection, and deterministic cancellation.
package peer

import (
	"context"
	"errors"
	"sync"
	"sync/atomic"

	"github.com/rupret007/webjam/transport/internal/icequic"
	"github.com/rupret007/webjam/transport/internal/limits"
	"github.com/rupret007/webjam/transport/internal/loopback"
	"github.com/rupret007/webjam/transport/internal/queue"
	"github.com/rupret007/webjam/transport/internal/wire"
)

type Mode string

const (
	ModeHost  Mode = "host"
	ModeGuest Mode = "guest"
)

var ErrAlreadyRun = errors.New("peer already run")

type Metrics struct {
	Sent            uint64
	Received        uint64
	QueueDrops      uint64
	MalformedDrops  uint64
	GenerationDrops uint64
	ReplayDrops     uint64
}

type Peer struct {
	mode       Mode
	generation uint32
	endpoint   loopback.Endpoint
	connection *icequic.Connection
	outbound   *queue.DatagramQueue
	ready      chan struct{}

	started         atomic.Bool
	stopping        atomic.Bool
	sent            atomic.Uint64
	received        atomic.Uint64
	queueDrops      atomic.Uint64
	malformedDrops  atomic.Uint64
	generationDrops atomic.Uint64
	replayDrops     atomic.Uint64

	cancelMu  sync.Mutex
	cancel    context.CancelFunc
	closeOnce sync.Once
}

func New(mode Mode, generation uint32, endpoint loopback.Endpoint, connection *icequic.Connection) (*Peer, error) {
	if mode != ModeHost && mode != ModeGuest {
		return nil, errors.New("invalid peer mode")
	}
	if generation == 0 || endpoint == nil || connection == nil {
		return nil, errors.New("peer requires generation, endpoint, and connection")
	}
	outbound, err := queue.NewDatagramQueue(limits.MaxDatagramQueueDepth, limits.MaxLivePayloadBytes)
	if err != nil {
		return nil, err
	}
	return &Peer{
		mode: mode, generation: generation, endpoint: endpoint,
		connection: connection, outbound: outbound, ready: make(chan struct{}),
	}, nil
}

// Ready closes only after all live-data pumps have been launched. Callers must
// still watch Run's result: a pump can fail immediately after becoming ready.
func (p *Peer) Ready() <-chan struct{} { return p.ready }

func (p *Peer) Run(ctx context.Context) error {
	if err := p.connection.RequireAuthorized(); err != nil {
		return err
	}
	if !p.started.CompareAndSwap(false, true) {
		return ErrAlreadyRun
	}
	runCtx, cancel := context.WithCancel(ctx)
	p.cancelMu.Lock()
	p.cancel = cancel
	p.cancelMu.Unlock()
	defer cancel()

	errorsCh := make(chan error, 3)
	var workers sync.WaitGroup
	workers.Add(3)
	go p.readLoopback(runCtx, &workers, errorsCh)
	go p.sendQUIC(runCtx, &workers, errorsCh)
	go p.receiveQUIC(runCtx, &workers, errorsCh)
	close(p.ready)

	var runErr error
	select {
	case <-ctx.Done():
		runErr = ctx.Err()
	case runErr = <-errorsCh:
	}
	cancel()
	p.closeOwned()
	workers.Wait()
	if ctx.Err() != nil || p.stopping.Load() || errors.Is(runErr, context.Canceled) {
		return nil
	}
	return runErr
}

func (p *Peer) readLoopback(ctx context.Context, workers *sync.WaitGroup, errorsCh chan<- error) {
	defer workers.Done()
	for {
		payload, err := p.endpoint.ReadDatagram(ctx)
		if err != nil {
			errorsCh <- err
			return
		}
		if err = p.outbound.Push(payload); errors.Is(err, queue.ErrFull) {
			p.queueDrops.Add(1)
			continue
		} else if err != nil {
			errorsCh <- err
			return
		}
	}
}

func (p *Peer) sendQUIC(ctx context.Context, workers *sync.WaitGroup, errorsCh chan<- error) {
	defer workers.Done()
	var sequence uint64
	for {
		datagram, err := p.outbound.Pop(ctx)
		if err != nil {
			errorsCh <- err
			return
		}
		encoded, err := wire.EncodeDatagram(p.generation, sequence, datagram.Payload)
		if err != nil {
			errorsCh <- err
			return
		}
		sequence++
		if err = p.connection.SendDatagram(encoded); err != nil {
			errorsCh <- err
			return
		}
		p.sent.Add(1)
	}
}

func (p *Peer) receiveQUIC(ctx context.Context, workers *sync.WaitGroup, errorsCh chan<- error) {
	defer workers.Done()
	var replay wire.ReplayWindow
	for {
		encoded, err := p.connection.ReceiveDatagram(ctx)
		if err != nil {
			errorsCh <- err
			return
		}
		frame, err := wire.DecodeDatagram(encoded)
		if err != nil {
			p.malformedDrops.Add(1)
			continue
		}
		if frame.Generation != p.generation {
			p.generationDrops.Add(1)
			continue
		}
		if err = replay.Accept(frame.Sequence); err != nil {
			p.replayDrops.Add(1)
			continue
		}
		if err = p.endpoint.WriteDatagram(ctx, frame.Payload); err != nil {
			errorsCh <- err
			return
		}
		p.received.Add(1)
	}
}

func (p *Peer) Close() error {
	p.stopping.Store(true)
	p.cancelMu.Lock()
	if p.cancel != nil {
		p.cancel()
	}
	p.cancelMu.Unlock()
	p.closeOwned()
	return nil
}

func (p *Peer) closeOwned() {
	p.closeOnce.Do(func() {
		p.outbound.Close()
		_ = p.endpoint.Close()
		_ = p.connection.CloseWithError(0, "peer stopped")
	})
}

func (p *Peer) Metrics() Metrics {
	return Metrics{
		Sent: p.sent.Load(), Received: p.received.Load(), QueueDrops: p.queueDrops.Load(),
		MalformedDrops: p.malformedDrops.Load(), GenerationDrops: p.generationDrops.Load(),
		ReplayDrops: p.replayDrops.Load(),
	}
}
