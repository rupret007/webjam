package directstun

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"reflect"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

const credentialFixture = `{"urls":["turn:203.0.113.1:3478?transport=udp"],"username":"test-user","credential":"test-password"}`

func TestCredentialFetchFailuresReturnNoCredentialsOrRetries(t *testing.T) {
	t.Setenv(testHarnessEnv, "1")
	for _, test := range []struct {
		name      string
		status    int
		body      string
		truncated bool
	}{
		{name: "unauthorized", status: http.StatusUnauthorized, body: credentialFixture},
		{name: "forbidden", status: http.StatusForbidden, body: credentialFixture},
		{name: "rate_limited", status: http.StatusTooManyRequests, body: credentialFixture},
		{name: "internal_error", status: http.StatusInternalServerError, body: credentialFixture},
		{name: "bad_gateway", status: http.StatusBadGateway, body: credentialFixture},
		{name: "unavailable", status: http.StatusServiceUnavailable, body: credentialFixture},
		{name: "empty", status: http.StatusOK},
		{name: "invalid_json", status: http.StatusOK, body: `{"urls":`},
		{name: "missing_password", status: http.StatusOK, body: `{"urls":"turn:203.0.113.1:3478","username":"test-user"}`},
		{name: "oversized", status: http.StatusOK, body: credentialFixture + strings.Repeat(" ", maxCredentialBody)},
		{name: "truncated", status: http.StatusOK, body: credentialFixture, truncated: true},
	} {
		t.Run(test.name, func(t *testing.T) {
			var requests atomic.Int32
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				requests.Add(1)
				w.Header().Set("Retry-After", "3600")
				if test.truncated {
					w.Header().Set("Content-Length", fmt.Sprint(len(test.body)+10))
				}
				w.WriteHeader(test.status)
				_, _ = w.Write([]byte(test.body))
			}))
			t.Cleanup(server.Close)
			ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
			defer cancel()
			credentials, err := fetchCredentials(ctx, server.URL)
			if err != errNoTURN || !reflect.DeepEqual(credentials, turnCredentials{}) {
				t.Fatalf("failed fetch returned credentials=%t, error=%v", !reflect.DeepEqual(credentials, turnCredentials{}), err)
			}
			if got := requests.Load(); got != 1 {
				t.Fatalf("failed credential request count = %d, want 1", got)
			}
		})
	}
}

func TestCredentialFetchCancellationAtEveryResponseStage(t *testing.T) {
	t.Setenv(testHarnessEnv, "1")
	for _, stage := range []string{"before_headers", "after_headers", "partial_body"} {
		t.Run(stage, func(t *testing.T) {
			started := make(chan struct{})
			canceled := make(chan struct{})
			release := make(chan struct{})
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				_, _ = io.Copy(io.Discard, r.Body)
				if stage != "before_headers" {
					w.WriteHeader(http.StatusOK)
					if stage == "partial_body" {
						_, _ = w.Write([]byte(`{"urls":`))
					}
					w.(http.Flusher).Flush()
				}
				close(started)
				select {
				case <-r.Context().Done():
					close(canceled)
				case <-release:
				}
			}))
			t.Cleanup(server.Close)
			t.Cleanup(func() { close(release) })
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			result := make(chan error, 1)
			go func() {
				credentials, err := fetchCredentials(ctx, server.URL)
				if !reflect.DeepEqual(credentials, turnCredentials{}) {
					result <- fmt.Errorf("canceled fetch returned credentials")
					return
				}
				result <- err
			}()
			select {
			case <-started:
			case <-time.After(2 * time.Second):
				t.Fatal("credential request did not reach the server")
			}
			cancel()
			select {
			case err := <-result:
				if err != errNoTURN {
					t.Fatalf("canceled fetch error = %v", err)
				}
			case <-time.After(2 * time.Second):
				t.Fatal("cancellation did not interrupt credential fetch")
			}
			select {
			case <-canceled:
			case <-time.After(2 * time.Second):
				t.Fatal("credential fetch left the request open after cancellation")
			}
		})
	}
}

func TestCredentialFetchDeadlineBeforeHeaders(t *testing.T) {
	t.Setenv(testHarnessEnv, "1")
	started := make(chan struct{})
	release := make(chan struct{})
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = io.Copy(io.Discard, r.Body)
		close(started)
		select {
		case <-r.Context().Done():
		case <-release:
		}
	}))
	t.Cleanup(server.Close)
	t.Cleanup(func() { close(release) })
	ctx, cancel := context.WithTimeout(context.Background(), 200*time.Millisecond)
	defer cancel()
	result := make(chan error, 1)
	go func() {
		_, err := fetchCredentials(ctx, server.URL)
		result <- err
	}()
	select {
	case <-started:
	case <-time.After(2 * time.Second):
		t.Fatal("credential request did not reach the server")
	}
	select {
	case err := <-result:
		if err != errNoTURN || ctx.Err() != context.DeadlineExceeded {
			t.Fatalf("fetch error=%v, context error=%v", err, ctx.Err())
		}
	case <-time.After(2 * time.Second):
		t.Fatal("credential fetch ignored the parent deadline before headers")
	}
}

func TestCredentialFetchOwnDeadlineWithoutCallerTimeout(t *testing.T) {
	t.Setenv(testHarnessEnv, "1")
	release := make(chan struct{})
	canceled := make(chan struct{})
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = io.Copy(io.Discard, r.Body)
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(`{"urls":`))
		w.(http.Flusher).Flush()
		select {
		case <-r.Context().Done():
			close(canceled)
		case <-release:
		}
	}))
	t.Cleanup(server.Close)
	t.Cleanup(func() { close(release) })
	result := make(chan error, 1)
	start := time.Now()
	go func() {
		_, err := fetchCredentials(context.Background(), server.URL)
		result <- err
	}()
	select {
	case err := <-result:
		if err != errNoTURN {
			t.Fatalf("stalled body error = %v", err)
		}
		if elapsed := time.Since(start); elapsed < workerWorstCase {
			t.Fatalf("fetch ended after %s, before the Worker's %s upstream budget", elapsed, workerWorstCase)
		}
	case <-time.After(credentialFetch + 3*time.Second):
		t.Fatal("credential fetch has no effective deadline without a caller timeout")
	}
	select {
	case <-canceled:
	case <-time.After(2 * time.Second):
		t.Fatal("credential deadline left the HTTP request open")
	}
}

func TestAllocateCredentialFailureAllowsSubsequentRetry(t *testing.T) {
	t.Setenv(testHarnessEnv, "1")
	t.Setenv(testTURNServerEnv, "")
	t.Setenv(testTURNSecretEnv, "")
	var requests atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if requests.Add(1) == 1 {
			w.WriteHeader(http.StatusServiceUnavailable)
			return
		}
		_, _ = w.Write([]byte(credentialFixture))
	}))
	t.Cleanup(server.Close)
	t.Setenv(testTURNURLEnv, server.URL)
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	if allocation := TryAllocate(ctx); allocation != nil {
		_ = allocation.Close()
		t.Fatal("unavailable credential service returned an allocation")
	}
	credentials, err := resolveCredentials(ctx)
	if err != nil || credentials.server != "203.0.113.1:3478" {
		t.Fatalf("fresh credential attempt did not recover: %v", err)
	}
	if got := requests.Load(); got != 2 {
		t.Fatalf("credential attempts = %d, want one failed allocation and one fresh fetch", got)
	}
}
