package directstun

import (
	"context"
	"encoding/json"
	"net"
	"net/http"
	"net/http/httptest"
	"net/netip"
	"testing"
	"time"

	"github.com/pion/turn/v5"
	"github.com/rupret007/webjam/transport/internal/limits"
)

func TestParseRelayTargetPinsCredentialedServer(t *testing.T) {
	t.Parallel()
	allowed := netip.MustParseAddr("203.0.113.1")
	encoded, err := relayCandidate(netip.MustParseAddrPort("203.0.113.1:49152"), 4000)
	if err != nil {
		t.Fatal(err)
	}
	target, err := ParseRelayTarget(encoded, allowed)
	if err != nil || target.IP.String() != "203.0.113.1" || target.Port != 49152 {
		t.Fatalf("pinned relay = %v, %v", target, err)
	}
	if _, err = ParseRelayTarget(encoded, netip.MustParseAddr("203.0.113.9")); err == nil {
		t.Fatal("relay candidate for a different TURN IP must be rejected")
	}
	host, err := hostCandidate(netip.MustParseAddr("192.168.1.20"), 22124)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = ParseRelayTarget(host, allowed); err == nil {
		t.Fatal("host candidate must not parse as a relay target")
	}
}

func TestCredentialJSONAcceptsCoturnAndCloudflareShapes(t *testing.T) {
	t.Parallel()
	coturn, err := parseCredentialJSON([]byte(`{
		"urls": ["turn:203.0.113.1:3478?transport=udp"],
		"username": "1700000000:webjam",
		"credential": "short-lived",
		"ttl": 600,
		"quota_bytes": 1048576
	}`))
	if err != nil || coturn.server != "203.0.113.1:3478" || coturn.quota != 1048576 {
		t.Fatalf("coturn document = %+v, %v", coturn, err)
	}

	cloudflare, err := parseCredentialJSON([]byte(`{
		"iceServers": [
			{"urls": ["stun:stun.cloudflare.com:3478"]},
			{
				"urls": ["turn:203.0.113.20:3478?transport=udp", "turns:203.0.113.20:5349?transport=tcp"],
				"username": "cf-user",
				"credential": "cf-pass"
			}
		],
		"quota_bytes": 9999999999,
		"allowed_relay_ips": ["203.0.113.21"]
	}`))
	if err != nil || cloudflare.server != "203.0.113.20:3478" {
		t.Fatalf("cloudflare document = %+v, %v", cloudflare, err)
	}
	if cloudflare.quota != uint64(limits.MaxRelaySessionBytes) {
		t.Fatalf("credential JSON must not raise the compiled quota, got %d", cloudflare.quota)
	}
	if _, ok := cloudflare.allowed[netip.MustParseAddr("203.0.113.21")]; !ok {
		t.Fatal("allowed_relay_ips must be retained")
	}
}

func TestQuotaConnStopsAtCompiledCap(t *testing.T) {
	t.Parallel()
	inner := &discardPacket{local: &net.UDPAddr{IP: net.IPv4(203, 0, 113, 1), Port: 9}}
	wrapped := newQuotaConn(inner, 8)
	peer := &net.UDPAddr{IP: net.IPv4(203, 0, 113, 2), Port: 9}
	if _, err := wrapped.WriteTo([]byte("1234"), peer); err != nil {
		t.Fatal(err)
	}
	if _, err := wrapped.WriteTo([]byte("1234"), peer); err != nil {
		t.Fatal(err)
	}
	if _, err := wrapped.WriteTo([]byte("x"), peer); err != ErrRelayQuota {
		t.Fatalf("quota error = %v", err)
	}
	if wrapped.bytes.Load() <= 8 {
		t.Fatalf("byte counter = %d", wrapped.bytes.Load())
	}
}

func TestHarnessMintAndCredentialURLNeverUseCompiledSecret(t *testing.T) {
	if turnCredentialURL != "" {
		t.Fatal("the shipped binary must not compile a credential URL by default")
	}
	t.Setenv(testHarnessEnv, "1")
	t.Setenv(testTURNServerEnv, "203.0.113.1:3478")
	t.Setenv(testTURNSecretEnv, "harness-only-secret")
	creds, err := mintHarnessCredentials()
	if err != nil || creds.username == "" || creds.password == "" {
		t.Fatalf("harness mint = %+v, %v", creds, err)
	}
	if creds.serverIP.String() != "203.0.113.1" {
		t.Fatalf("server IP = %s", creds.serverIP)
	}

	payload, _ := json.Marshal(map[string]any{
		"urls":        []string{"turn:203.0.113.1:3478?transport=udp"},
		"username":    "rest:webjam",
		"credential":  "from-endpoint",
		"quota_bytes": 4096,
	})
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		if request.Method != http.MethodPost {
			writer.WriteHeader(http.StatusMethodNotAllowed)
			return
		}
		var body struct {
			SessionID   string `json:"session_id"`
			InviteToken string `json:"invite_token"`
		}
		if err := json.NewDecoder(request.Body).Decode(&body); err != nil ||
			body.SessionID == "" || len(body.InviteToken) < 32 {
			writer.WriteHeader(http.StatusForbidden)
			return
		}
		_, _ = writer.Write(payload)
	}))
	t.Cleanup(server.Close)
	t.Setenv(testTURNURLEnv, server.URL)
	fetched, err := fetchCredentials(context.Background(), server.URL)
	if err != nil || fetched.password != "from-endpoint" || fetched.quota != 4096 {
		t.Fatalf("fetched = %+v, %v", fetched, err)
	}
}

func TestFetchCredentialsPostsProofAndSkipsWithoutIt(t *testing.T) {
	t.Setenv(testHarnessEnv, "1")
	var method string
	var sessionID string
	var token string
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		method = request.Method
		var body struct {
			SessionID   string `json:"session_id"`
			InviteToken string `json:"invite_token"`
		}
		_ = json.NewDecoder(request.Body).Decode(&body)
		sessionID = body.SessionID
		token = body.InviteToken
		_, _ = writer.Write([]byte(`{
			"urls": ["turn:203.0.113.1:3478?transport=udp"],
			"username": "proof:webjam",
			"credential": "posted"
		}`))
	}))
	t.Cleanup(server.Close)

	var session, capability [32]byte
	session[0] = 7
	capability[0] = 9
	ctx := WithCredentialProof(context.Background(), session, capability)
	fetched, err := fetchCredentials(ctx, server.URL)
	if err != nil || fetched.password != "posted" {
		t.Fatalf("posted fetch = %+v, %v", fetched, err)
	}
	if method != http.MethodPost || len(sessionID) != 64 || len(token) < 32 {
		t.Fatalf("proof body method=%s session=%q token_len=%d", method, sessionID, len(token))
	}

	previous := turnCredentialURL
	turnCredentialURL = "https://turn-creds.example.test/turn-credentials"
	t.Cleanup(func() { turnCredentialURL = previous })
	t.Setenv(testHarnessEnv, "")
	t.Setenv(testTURNURLEnv, "")
	if _, err = resolveCredentials(context.Background()); err != errNoTURN {
		t.Fatalf("missing proof must not fetch credentials: %v", err)
	}
}

func TestCredentialFetchTimeoutCoversWorkerAndSlowBody(t *testing.T) {
	if credentialFetch != 10*time.Second {
		t.Fatalf("credentialFetch=%s want 10s (two 5s Worker upstreams)", credentialFetch)
	}
	t.Setenv(testHarnessEnv, "1")
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		writer.WriteHeader(http.StatusOK)
		if flusher, ok := writer.(http.Flusher); ok {
			flusher.Flush()
		}
		<-request.Context().Done()
	}))
	t.Cleanup(server.Close)
	var session, capability [32]byte
	session[0], capability[0] = 1, 2
	ctx, cancel := context.WithTimeout(
		WithCredentialProof(context.Background(), session, capability),
		200*time.Millisecond,
	)
	defer cancel()
	start := time.Now()
	if _, err := fetchCredentials(ctx, server.URL); err != errNoTURN {
		t.Fatalf("slow body must fail closed, got %v", err)
	}
	if time.Since(start) > 2*time.Second {
		t.Fatal("timeout must stay in force after headers")
	}
}

func TestAllocateUsesTimeLimitedRESTCredentials(t *testing.T) {
	secret := "relay-test-secret"
	packet, err := net.ListenUDP("udp4", &net.UDPAddr{IP: net.IPv4(127, 0, 0, 1), Port: 0})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = packet.Close() })
	server, err := turn.NewServer(turn.ServerConfig{
		Realm:       "webjam.test",
		AuthHandler: turn.LongTermTURNRESTAuthHandler(secret, nil),
		PacketConnConfigs: []turn.PacketConnConfig{{
			PacketConn: packet,
			RelayAddressGenerator: &turn.RelayAddressGeneratorStatic{
				RelayAddress: net.ParseIP("127.0.0.1"),
				Address:      "127.0.0.1",
			},
		}},
	})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = server.Close() })

	t.Setenv(testHarnessEnv, "1")
	t.Setenv(testTURNSecretEnv, secret)
	t.Setenv(testTURNServerEnv, packet.LocalAddr().String())
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	allocation, err := Allocate(ctx)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = allocation.Close() })
	if allocation.Relayed() == nil || allocation.Relayed().Port < 1 {
		t.Fatal("missing relayed address")
	}
	encoded, err := allocation.Candidate()
	if err != nil || encoded == "" {
		t.Fatalf("candidate = %q, %v", encoded, err)
	}
}

type discardPacket struct {
	local net.Addr
}

func (p *discardPacket) ReadFrom([]byte) (int, net.Addr, error) {
	return 0, p.local, net.ErrClosed
}

func (p *discardPacket) WriteTo(payload []byte, _ net.Addr) (int, error) {
	return len(payload), nil
}

func (p *discardPacket) Close() error                     { return nil }
func (p *discardPacket) LocalAddr() net.Addr              { return p.local }
func (p *discardPacket) SetDeadline(time.Time) error      { return nil }
func (p *discardPacket) SetReadDeadline(time.Time) error  { return nil }
func (p *discardPacket) SetWriteDeadline(time.Time) error { return nil }
