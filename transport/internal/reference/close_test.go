package reference

import (
	"bufio"
	"context"
	"errors"
	"fmt"
	"net"
	"strings"
	"testing"
	"time"

	"github.com/rupret007/webjam/transport/internal/limits"
)

func TestCloseLocalSessionFreshConnectionUsesExistingHostAuthorityOnce(t *testing.T) {
	t.Parallel()
	session, token := filledSession(7), fixedToken(8)
	defer token.Destroy()
	original := token.value
	var clients []*Client
	var requests []<-chan map[string]any
	var deadlines []time.Time
	before := time.Now()
	dial := func(ctx context.Context) (*Client, error) {
		deadline, ok := ctx.Deadline()
		if !ok {
			t.Fatal("close dial has no total deadline")
		}
		deadlines = append(deadlines, deadline)
		client, request := scriptedClient(t, []string{`{"v":3,"ok":true}`})
		clients = append(clients, client)
		requests = append(requests, request)
		return client, nil
	}
	for _, sequence := range []uint64{41, 42} {
		if err := closeLocalSession(context.Background(), session, token, 9, sequence, dial); err != nil {
			t.Fatal(err)
		}
	}
	if len(clients) != 2 || clients[0] == clients[1] {
		t.Fatal("separate calls reused a control client")
	}
	for index, received := range requests {
		if !clients[index].closed {
			t.Fatal("close left its control connection owned")
		}
		if deadlines[index].Before(before) || deadlines[index].After(time.Now().Add(limits.ShutdownLimit)) {
			t.Fatal("close exceeded its fixed total budget")
		}
		select {
		case request := <-received:
			if len(request) != 7 || request["v"] != float64(3) || request["op"] != "close" ||
				request["role"] != "host" || request["session"] != encode(session[:]) ||
				request["token"] != encode(token.value[:]) || request["generation"] != float64(9) ||
				request["sequence"] != float64(41+index) {
				t.Fatal("close changed the supplied session authority, sequence, or operation")
			}
		default:
			t.Fatal("close returned without sending its one request")
		}
		select {
		case <-received:
			t.Fatal("close sent another request")
		default:
		}
	}
	if token.value != original {
		t.Fatal("close consumed the caller's host token")
	}
}

func TestCloseLocalSessionInvalidArgumentsNeverDial(t *testing.T) {
	t.Parallel()
	valid := fixedToken(2)
	defer valid.Destroy()
	destroyed := fixedToken(3)
	destroyed.Destroy()
	tests := []struct {
		name       string
		ctx        context.Context
		session    SessionID
		token      *RoleToken
		generation uint32
		sequence   uint64
	}{
		{"nil-context", nil, filledSession(1), valid, 1, 1},
		{"zero-session", context.Background(), SessionID{}, valid, 1, 1},
		{"nil-token", context.Background(), filledSession(1), nil, 1, 1},
		{"zero-token", context.Background(), filledSession(1), &RoleToken{}, 1, 1},
		{"destroyed-token", context.Background(), filledSession(1), destroyed, 1, 1},
		{"zero-generation", context.Background(), filledSession(1), valid, 0, 1},
		{"zero-sequence", context.Background(), filledSession(1), valid, 1, 0},
		{"exhausted-sequence", context.Background(), filledSession(1), valid, 1, uint64(1) << 63},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			err := closeLocalSession(test.ctx, test.session, test.token, test.generation, test.sequence,
				func(context.Context) (*Client, error) { t.Fatal("invalid close reached dialing"); return nil, nil })
			if !errors.Is(err, ErrInvalidInput) {
				t.Fatalf("invalid close error = %v", err)
			}
		})
	}
	if err := closeLocalSession(context.Background(), filledSession(1), valid, 1, 1, nil); !errors.Is(err, ErrInvalidInput) {
		t.Fatalf("nil dial error = %v", err)
	}
	// Exercise the exported wrapper's validation too; no valid address reaches I/O.
	if err := CloseLocalSession(context.Background(), SessionID{}, valid, 1, 1); !errors.Is(err, ErrInvalidInput) {
		t.Fatalf("exported invalid close error = %v", err)
	}
}

func TestCloseLocalSessionCanceledBeforeDial(t *testing.T) {
	t.Parallel()
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	err := closeLocalSession(ctx, filledSession(1), fixedToken(2), 1, 1,
		func(context.Context) (*Client, error) { t.Fatal("canceled close reached dialing"); return nil, nil })
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("canceled close error = %v", err)
	}
}

func TestCloseLocalSessionUnavailableDialIsSafeAndNotRetried(t *testing.T) {
	t.Parallel()
	for _, returned := range []error{errors.New("PRIVATE_HOST_TOKEN_PRIVATE_ENDPOINT"), nil} {
		calls := 0
		err := closeLocalSession(context.Background(), filledSession(1), fixedToken(2), 1, 1,
			func(context.Context) (*Client, error) { calls++; return nil, returned })
		if calls != 1 || !errors.Is(err, ErrControlUnavailable) || strings.Contains(fmt.Sprintf("%v", err), "PRIVATE") {
			t.Fatal("failed dial was retried, reported success, or exposed private details")
		}
	}
}

func TestCloseLocalSessionCallerBudgetIncludesDial(t *testing.T) {
	t.Parallel()
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Millisecond)
	defer cancel()
	deadline, _ := ctx.Deadline()
	calls := 0
	err := closeLocalSession(ctx, filledSession(1), fixedToken(2), 1, 1,
		func(received context.Context) (*Client, error) {
			calls++
			actual, ok := received.Deadline()
			if !ok || !actual.Equal(deadline) {
				t.Fatal("dial reset the caller's total budget")
			}
			<-received.Done()
			return nil, errors.New("PRIVATE_DIAL_TIMEOUT")
		})
	if calls != 1 || !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("bounded dial error = %v", err)
	}
}

func TestCloseLocalSessionAcknowledgmentErrorsNeverClaimRemoval(t *testing.T) {
	t.Parallel()
	for _, test := range []struct {
		name, response string
		expected       error
	}{
		{"service-rejects-authority", `{"v":3,"ok":false,"error":"unauthorized"}`, ErrUnauthorized},
		{"service-overloaded", `{"v":3,"ok":false,"error":"overloaded"}`, ErrOverloaded},
		{"replayed-sequence", `{"v":3,"ok":false,"error":"replay"}`, ErrReplay},
		{"unknown-private-error", `{"v":3,"ok":false,"error":"PRIVATE_SERVICE_DATA"}`, ErrControlProtocol},
		{"extra-private-field", `{"v":3,"ok":true,"private":"PRIVATE_SERVICE_DATA"}`, ErrControlProtocol},
	} {
		t.Run(test.name, func(t *testing.T) {
			client, _ := scriptedClient(t, []string{test.response})
			calls := 0
			err := closeLocalSession(context.Background(), filledSession(1), fixedToken(2), 1, 1,
				func(context.Context) (*Client, error) { calls++; return client, nil })
			if calls != 1 || !errors.Is(err, test.expected) || !client.closed || strings.Contains(fmt.Sprintf("%v", err), "PRIVATE") {
				t.Fatal("uncertain close acknowledgment was retried, leaked, or reported as removal")
			}
		})
	}
}

func TestCloseLocalSessionClosedClientIsNotReusedOrRetried(t *testing.T) {
	t.Parallel()
	client := &Client{closed: true}
	calls := 0
	err := closeLocalSession(context.Background(), filledSession(1), fixedToken(2), 1, 1,
		func(context.Context) (*Client, error) { calls++; return client, nil })
	if calls != 1 || !errors.Is(err, ErrClosed) {
		t.Fatalf("closed client error = %v", err)
	}
}

func TestCloseLocalSessionMissingAcknowledgmentClosesItsConnection(t *testing.T) {
	t.Parallel()
	for _, outcome := range []string{"lost-reply", "caller-canceled", "caller-timeout"} {
		t.Run(outcome, func(t *testing.T) {
			left, right := net.Pipe()
			client := newClient(left)
			budget := time.Second
			if outcome == "caller-timeout" {
				budget = 25 * time.Millisecond
			}
			ctx, cancel := context.WithTimeout(context.Background(), budget)
			defer cancel()
			defer left.Close()
			defer right.Close()
			done := make(chan struct{})
			go func() {
				defer close(done)
				defer right.Close()
				_, _ = bufio.NewReader(right).ReadBytes('\n')
				if outcome == "caller-canceled" {
					cancel()
				}
				if outcome != "lost-reply" {
					var one [1]byte
					_, _ = right.Read(one[:])
				}
			}()
			calls := 0
			err := closeLocalSession(ctx, filledSession(1), fixedToken(2), 1, 1,
				func(context.Context) (*Client, error) { calls++; return client, nil })
			expected := ErrControlUnavailable
			if outcome == "caller-canceled" {
				expected = context.Canceled
			} else if outcome == "caller-timeout" {
				expected = context.DeadlineExceeded
			}
			if calls != 1 || !errors.Is(err, expected) || !client.closed {
				t.Fatalf("lost acknowledgment error = %v; connection closed = %v", err, client.closed)
			}
			select {
			case <-done:
			case <-time.After(time.Second):
				t.Fatal("cleanup did not release the controlled connection")
			}
		})
	}
}
