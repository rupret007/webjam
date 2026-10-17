package webjoin

// This harness is compiled only into the test binary. It is never reachable
// through IPC or the shipping endpoint constructor. Browser engines can emit
// private-interface host candidates even when both peers run on this machine.
import (
	"context"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net"
	"net/http"
	"os"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

type echoBridge struct {
	*memoryBridge
	received, sent atomic.Uint64
}

func (b *echoBridge) WriteOpus(ctx context.Context, p Packet) error {
	b.received.Add(1)
	select {
	case b.mix <- p:
		return nil
	case <-ctx.Done():
		return ctx.Err()
	default:
		return nil
	}
}
func (b *echoBridge) ReadOpus(ctx context.Context) (Packet, error) {
	p, e := b.memoryBridge.ReadOpus(ctx)
	if e == nil {
		b.sent.Add(1)
	}
	return p, e
}
func TestMain(m *testing.M) {
	if os.Getenv("WEBJAM_BROWSER_LAB") != "1" {
		os.Exit(m.Run())
	}
	var mu sync.Mutex
	var endpoint *Endpoint
	var bridge *echoBridge
	mux := http.NewServeMux()
	server := &http.Server{Addr: "127.0.0.1:4175", Handler: mux, ReadHeaderTimeout: 2 * time.Second, ReadTimeout: 5 * time.Second, WriteTimeout: 25 * time.Second}
	mux.HandleFunc("/offer", func(w http.ResponseWriter, r *http.Request) {
		mu.Lock()
		defer mu.Unlock()
		if endpoint != nil {
			_ = endpoint.Close()
		}
		cfg := testConfig()
		bridge = &echoBridge{memoryBridge: newMemoryBridge()}
		cfg.Bridge = bridge
		var err error
		endpoint, err = newEndpoint(context.Background(), cfg, true)
		if err != nil {
			http.Error(w, err.Error(), 500)
			return
		}
		offer, err := endpoint.Offer(r.Context())
		if err != nil {
			http.Error(w, err.Error(), 500)
			return
		}
		signal, _ := OpenSignal(cfg.Capability, cfg.SessionID, offer)
		_ = json.NewEncoder(w).Encode(map[string]any{"capability_hex": hex.EncodeToString(cfg.Capability[:]), "session": hex.EncodeToString(cfg.SessionID[:]), "generation": cfg.Generation, "expires_at": signal.ExpiresAt, "offer_b64url": base64.RawURLEncoding.EncodeToString(offer)})
	})
	mux.HandleFunc("/answer", func(w http.ResponseWriter, r *http.Request) {
		mu.Lock()
		defer mu.Unlock()
		var body struct {
			Envelope string `json:"envelope_b64url"`
		}
		if json.NewDecoder(http.MaxBytesReader(w, r.Body, 12000)).Decode(&body) != nil || endpoint == nil {
			http.Error(w, "answer", 400)
			return
		}
		raw, err := base64.RawURLEncoding.DecodeString(body.Envelope)
		if err == nil {
			err = endpoint.ApplyAnswer(r.Context(), raw)
		}
		if err != nil {
			http.Error(w, err.Error(), 400)
			return
		}
		w.WriteHeader(204)
	})
	mux.HandleFunc("/status", func(w http.ResponseWriter, r *http.Request) {
		mu.Lock()
		defer mu.Unlock()
		if endpoint == nil {
			_ = json.NewEncoder(w).Encode(map[string]any{"authorized": false, "received": 0, "sent": 0, "error": ""})
			return
		}
		reason := ""
		if err := endpoint.Err(); err != nil {
			reason = err.Error()
		}
		_ = json.NewEncoder(w).Encode(map[string]any{"authorized": endpoint.Authorized(), "received": bridge.received.Load(), "sent": bridge.sent.Load(), "bytes": endpoint.Bytes(), "error": reason})
	})
	mux.HandleFunc("/stop", func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(204)
		go func() {
			mu.Lock()
			if endpoint != nil {
				_ = endpoint.Close()
			}
			mu.Unlock()
			_ = server.Shutdown(context.Background())
		}()
	})
	listener, err := net.Listen("tcp", server.Addr)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	fmt.Println("WEBJAM_BROWSER_LAB_READY 127.0.0.1:4175")
	if err = server.Serve(listener); err != nil && err != http.ErrServerClosed {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	os.Exit(0)
}
