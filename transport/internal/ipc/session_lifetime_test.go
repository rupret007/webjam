package ipc

import (
	"bytes"
	"context"
	"errors"
	"testing"
	"time"

	"github.com/rupret007/webjam/transport/internal/icequic"
	"github.com/rupret007/webjam/transport/internal/limits"
	"github.com/rupret007/webjam/transport/internal/loopback"
	"github.com/rupret007/webjam/transport/internal/profile"
)

func TestSessionOperationDeadlineUsesActualCertificate(t *testing.T) {
	now := time.Now().UTC().Truncate(time.Second)
	for _, tc := range []struct {
		name          string
		age, lifetime time.Duration
	}{
		{"fresh", 0, limits.HostIdentityLifetime},
		{"older_host", 6 * time.Hour, limits.HostIdentityLifetime},
		{"long_identity", 0, limits.MaxIdentityLifetime},
	} {
		t.Run(tc.name, func(t *testing.T) {
			identity, err := icequic.NewEphemeralIdentity(now.Add(-tc.age), tc.lifetime)
			if err != nil {
				t.Fatal(err)
			}
			defer identity.Destroy()
			expected := identity.Certificate.Leaf.NotAfter
			if max := now.Add(limits.MaxActiveSessionLifetime); max.Before(expected) {
				expected = max
			}
			// The actual DER certificate, not an altered cached Leaf, owns expiry.
			identity.Certificate.Leaf.NotAfter = now.Add(24 * time.Hour)
			deadline, err := sessionOperationDeadline(now, &identity)
			if err != nil || !deadline.Equal(expected) {
				t.Fatalf("deadline %v, expected %v, error %v", deadline, expected, err)
			}
		})
	}
	identity, err := icequic.NewEphemeralIdentity(now.Add(-9*time.Hour), limits.HostIdentityLifetime)
	if err != nil {
		t.Fatal(err)
	}
	defer identity.Destroy()
	for _, candidate := range []*icequic.Identity{nil, {}, &identity} {
		if _, err := sessionOperationDeadline(now, candidate); !errors.Is(err, ErrEnrollmentInvalid) {
			t.Fatal("missing/expired identity must fail closed")
		}
	}
}

func TestEnrollmentCompletionKeepsSeparateOperationContext(t *testing.T) {
	now := time.Now().UTC().Truncate(time.Second)
	expiry := now.Add(time.Minute)
	for _, tc := range []struct {
		name                      string
		offset                    time.Duration
		cancelSetup, cancelParent bool
	}{
		{"before", -time.Nanosecond, false, false},
		{"exact", 0, false, false},
		{"after", time.Nanosecond, false, false},
		{"setup_canceled", -time.Second, true, false},
		{"parent_canceled", -time.Second, false, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			parent, cancelParent := context.WithCancel(context.Background())
			defer cancelParent()
			setup, cancelSetup := context.WithCancel(parent)
			defer cancelSetup()
			op := &referenceFabricOperation{
				ctx: parent, enrollmentCtx: setup, cancelEnrollment: cancelSetup,
				configuration: &enrollmentConfig{ExpiresAtUnix: uint64(expiry.Unix())},
				now:           func() time.Time { return expiry.Add(tc.offset) },
			}
			if tc.cancelSetup {
				cancelSetup()
			}
			if tc.cancelParent {
				cancelParent()
			}
			err := op.completeEnrollment()
			if tc.name == "before" {
				if err != nil || setup.Err() != context.Canceled || parent.Err() != nil {
					t.Fatal("successful admission must cancel only its setup child")
				}
				cancelParent()
				if op.ctx.Err() != context.Canceled {
					t.Fatal("parent cancellation must still own the active operation")
				}
			} else if err == nil {
				t.Fatal("late or canceled admission was promoted")
			}
		})
	}
}

type lifetimeOrchestrator struct {
	operation *lifetimeOperation
}

type lifetimeOperation struct {
	*recordingOperation
	ctx       context.Context
	failClose bool
}

func (o *lifetimeOrchestrator) Start(ctx context.Context, config *enrollmentConfig, _ *icequic.Identity, endpoint loopback.Endpoint) (fabricOperation, error) {
	o.operation = &lifetimeOperation{
		recordingOperation: &recordingOperation{updates: make(chan fabricUpdate, 2), configuration: config, endpoint: endpoint},
		ctx:                ctx,
	}
	return o.operation, nil
}

func (o *lifetimeOperation) Close(ctx context.Context) error {
	if o.failClose {
		return context.DeadlineExceeded
	}
	return o.recordingOperation.Close(ctx)
}

// This fixture exercises actual runner ownership with a controlled endpoint and
// explicitly simulated authenticated receipt. Real proof/data/control remain
// covered by the separate reference-service integration test.
func lifetimeRunner(t *testing.T, mode string) (*runnerState, *lifetimeOperation, *recordingFactory, *bytes.Buffer, time.Time) {
	t.Helper()
	now := time.Now().UTC().Truncate(time.Second)
	expiry := now.Add(2 * time.Minute)
	factory, orchestrator := &recordingFactory{port: 41000}, &lifetimeOrchestrator{}
	state := &runnerState{factory: factory, orchestrator: orchestrator}
	parent, cancel := context.WithCancel(context.Background())
	var output bytes.Buffer
	events := &emitter{writer: &output}
	if mode == "host" {
		if _, err := state.handle(parent, &Command{ID: 1, Type: CommandPrepareHost}, events, "test", func() time.Time { return now }); err != nil {
			t.Fatal(err)
		}
	}
	port, pin := 0, testPin()
	if mode == "host" {
		port, pin = 22124, ""
	}
	command, err := ParseCommandAt(mustJSON(t, openFields(2, mode, profile.ReferenceLocalID, expiry, port, pin)), now)
	if err != nil {
		t.Fatal(err)
	}
	defer command.ClearSensitive()
	if err = state.openPeer(parent, &command, events, func() time.Time { return now }); err != nil {
		t.Fatal(err)
	}
	if state.operation == nil || orchestrator.operation == nil {
		t.Fatal("controlled operation was not opened")
	}
	if mode == "host" {
		if err = state.handleFabricUpdate(fabricUpdate{kind: updateHostRegistered}, events); err != nil {
			t.Fatal(err)
		}
	}
	if err = state.handleFabricUpdate(fabricUpdate{kind: updatePeerConnected}, events); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		cancel()
		orchestrator.operation.failClose = false
		if err := state.destroy(); err != nil {
			t.Error(err)
		}
	})
	return state, orchestrator.operation, factory, &output, expiry
}

func TestRunnerConnectedLifetimeIsIndependentOfInvitation(t *testing.T) {
	for _, mode := range []string{"host", "guest"} {
		t.Run(mode, func(t *testing.T) {
			state, op, factory, _, expiry := lifetimeRunner(t, mode)
			deadline, bounded := op.ctx.Deadline()
			if !state.connected || !bounded || !deadline.After(expiry) ||
				!deadline.Equal(state.localIdentity.Certificate.Leaf.NotAfter) || op.ctx.Err() != nil {
				t.Fatal("connected operation must retain the finite identity lifetime, not invitation expiry")
			}
			if _, err := state.closeActive(true); err != nil {
				t.Fatal(err)
			}
			if op.ctx.Err() != context.Canceled || !factory.endpoint.closed || !op.closed {
				t.Fatal("local Close must still cancel and retire the owned operation")
			}
		})
	}
}

func TestRunnerFailedLocalCloseRetainsOwnershipForRetry(t *testing.T) {
	for _, mode := range []string{"host", "guest"} {
		t.Run(mode, func(t *testing.T) {
			state, op, factory, output, _ := lifetimeRunner(t, mode)
			op.failClose = true
			events := &emitter{writer: output}
			stop, err := state.handle(context.Background(), &Command{ID: 3, Type: CommandClosePeer}, events, "test", time.Now)
			if err != nil || stop {
				t.Fatal("failed Close should remain available for cleanup retry")
			}
			got := decodeEvents(t, output.String())
			last := got[len(got)-1]
			if last.Type != "error" || last.Code != CodeProtocolViolation || last.State != "failed" {
				t.Fatal("failed local Close emitted a successful receipt")
			}
			if state.operation != op || state.active == nil || state.localIdentity == nil || state.updates != nil ||
				state.connected || !state.closePending || !state.openConsumed || factory.endpoint.closed {
				t.Fatal("unclosed local ownership was lost or retained application authority")
			}
			calls := factory.calls
			if err := state.openPeer(context.Background(), &Command{ID: 4}, events, time.Now); err != nil {
				t.Fatal(err)
			}
			if factory.calls != calls {
				t.Fatal("failed cleanup allowed a second endpoint")
			}
			if err := state.handleFabricUpdate(fabricUpdate{kind: updatePeerConnected}, events); !errors.Is(err, ErrProtocol) {
				t.Fatal("retired operation accepted a late connected receipt")
			}
			op.failClose = false
			if _, err := state.handle(context.Background(), &Command{ID: 5, Type: CommandClosePeer}, events, "test", time.Now); err != nil {
				t.Fatal(err)
			}
			got = decodeEvents(t, output.String())
			last = got[len(got)-1]
			if last.Type != "peer_closed" || state.operation != nil || state.closePending || !factory.endpoint.closed || !op.closed {
				t.Fatal("successful local cleanup retry did not release ownership")
			}
			if mode == "host" && (state.openConsumed || state.localIdentity == nil) {
				t.Fatal("successful host reset did not preserve only its prepared identity")
			}
		})
	}
}

func TestRunnerShutdownAndFabricFailureDoNotHideLocalCloseFailure(t *testing.T) {
	for _, shutdown := range []bool{false, true} {
		t.Run(map[bool]string{false: "fabric_failure", true: "shutdown"}[shutdown], func(t *testing.T) {
			state, op, _, output, _ := lifetimeRunner(t, "host")
			op.failClose = true
			events := &emitter{writer: output}
			if shutdown {
				stop, err := state.handle(context.Background(), &Command{ID: 3, Type: CommandShutdown}, events, "test", time.Now)
				if err != nil || stop {
					t.Fatal("failed shutdown must not emit stopped")
				}
			} else if err := state.failFabric(events, ErrProtocol); err != nil {
				t.Fatal(err)
			}
			got := decodeEvents(t, output.String())
			if got[len(got)-1].Type != "error" || state.recentlyClosed != nil || !state.closePending || state.operation != op || !state.openConsumed {
				t.Fatal("unconfirmed cleanup was treated as a completed close")
			}
		})
	}
}

func TestFullUpdateQueueHonorsPumpCancellation(t *testing.T) {
	parent, cancelParent := context.WithCancel(context.Background())
	defer cancelParent()
	pump, cancelPump := context.WithCancel(parent)
	defer cancelPump()
	op := &referenceFabricOperation{ctx: parent, updates: make(chan fabricUpdate, 1)}
	op.updates <- fabricUpdate{kind: updateHostRegistered}
	result := make(chan bool, 1)
	go func() { result <- op.sendWithContext(pump, fabricUpdate{kind: updateHelpReceived}) }()
	cancelPump()
	select {
	case sent := <-result:
		if sent || parent.Err() != nil {
			t.Fatal("canceled pump must unblock without canceling its parent")
		}
	case <-time.After(time.Second):
		t.Fatal("full update queue prevented pump teardown")
	}
}

func TestCanceledPumpCannotPublishIntoAvailableUpdateQueue(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	op := &referenceFabricOperation{updates: make(chan fabricUpdate, 1)}
	if op.sendWithContext(ctx, fabricUpdate{kind: updatePeerConnected}) || len(op.updates) != 0 {
		t.Fatal("a retired pump published a stale connected receipt")
	}
}
