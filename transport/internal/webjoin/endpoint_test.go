package webjoin

import (
	"context"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"net"
	"net/http"
	"net/http/httptest"
	"net/netip"
	"os"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/pion/ice/v4"
	"github.com/pion/rtp"
	"github.com/pion/webrtc/v4"
)

type memoryBridge struct {
	received chan Packet
	mix      chan Packet
	closed   chan struct{}
	once     sync.Once
}

func newMemoryBridge() *memoryBridge {
	return &memoryBridge{received: make(chan Packet, 16), mix: make(chan Packet, 16), closed: make(chan struct{})}
}
func (b *memoryBridge) WriteOpus(ctx context.Context, p Packet) error {
	p.Payload = append([]byte(nil), p.Payload...)
	select {
	case b.received <- p:
		return nil
	case <-ctx.Done():
		return ctx.Err()
	case <-b.closed:
		return ErrClosed
	}
}
func (b *memoryBridge) ReadOpus(ctx context.Context) (Packet, error) {
	select {
	case p := <-b.mix:
		return p, nil
	case <-ctx.Done():
		return Packet{}, ctx.Err()
	case <-b.closed:
		return Packet{}, ErrClosed
	}
}
func (b *memoryBridge) Close() error { b.once.Do(func() { close(b.closed) }); return nil }
func testConfig() Config {
	return Config{Enabled: true, SessionID: [32]byte{1}, Capability: [32]byte{2}, Generation: 7, ExpiresAt: time.Now().Add(time.Minute), Bridge: newMemoryBridge()}
}

type labPeer struct {
	host    *Endpoint
	guest   *webrtc.PeerConnection
	track   *webrtc.TrackLocalStaticRTP
	channel chan *webrtc.DataChannel
	audio   chan Packet
	answer  Signal
	cfg     Config
}

func newLabPeer(t *testing.T, mutate func(*Signal)) *labPeer {
	t.Helper()
	cfg := testConfig()
	host, err := newEndpoint(context.Background(), cfg, true)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = host.Close() })
	sealed, err := host.Offer(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	offer, err := OpenSignal(cfg.Capability, cfg.SessionID, sealed)
	if err != nil {
		t.Fatal(err)
	}
	engine := webrtc.SettingEngine{}
	engine.SetIncludeLoopbackCandidate(true)
	engine.SetIPFilter(func(ip net.IP) bool { return ip.IsLoopback() })
	engine.SetNetworkTypes([]webrtc.NetworkType{webrtc.NetworkTypeUDP4})
	engine.SetICEMulticastDNSMode(ice.MulticastDNSModeDisabled)
	guest, err := webrtc.NewAPI(webrtc.WithSettingEngine(engine)).NewPeerConnection(webrtc.Configuration{})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = guest.Close() })
	peer := &labPeer{host: host, guest: guest, channel: make(chan *webrtc.DataChannel, 1), audio: make(chan Packet, 8), cfg: cfg}
	guest.OnDataChannel(func(dc *webrtc.DataChannel) { dc.OnOpen(func() { peer.channel <- dc }) })
	guest.OnTrack(func(track *webrtc.TrackRemote, _ *webrtc.RTPReceiver) {
		go func() {
			for {
				p, _, e := track.ReadRTP()
				if e != nil {
					return
				}
				select {
				case peer.audio <- Packet{Payload: p.Payload, SequenceNumber: p.SequenceNumber, Timestamp: p.Timestamp}:
				default:
				}
			}
		}()
	})
	peer.track, err = webrtc.NewTrackLocalStaticRTP(webrtc.RTPCodecCapability{MimeType: webrtc.MimeTypeOpus, ClockRate: 48000, Channels: 2}, "browser", "webjoin")
	if err != nil {
		t.Fatal(err)
	}
	sender, err := guest.AddTrack(peer.track)
	if err != nil {
		t.Fatal(err)
	}
	go func() {
		buffer := make([]byte, 1500)
		for {
			if _, _, e := sender.Read(buffer); e != nil {
				return
			}
		}
	}()
	if err = guest.SetRemoteDescription(webrtc.SessionDescription{Type: webrtc.SDPTypeOffer, SDP: offer.SDP}); err != nil {
		t.Fatal(err)
	}
	complete := webrtc.GatheringCompletePromise(guest)
	answer, err := guest.CreateAnswer(nil)
	if err != nil {
		t.Fatal(err)
	}
	if err = guest.SetLocalDescription(answer); err != nil {
		t.Fatal(err)
	}
	select {
	case <-complete:
	case <-time.After(3 * time.Second):
		t.Fatal("guest gather timed out")
	}
	peer.answer = offer
	peer.answer.Kind = "answer"
	peer.answer.SDP = guest.LocalDescription().SDP
	for _, line := range strings.Split(peer.answer.SDP, "\n") {
		if strings.HasPrefix(line, "a=fingerprint:") {
			peer.answer.GuestPin, err = parseFingerprint(strings.TrimSpace(strings.TrimPrefix(line, "a=fingerprint:")))
			if err != nil {
				t.Fatal(err)
			}
			break
		}
	}
	peer.answer.SDP, err = validateSDP(peer.answer.SDP, peer.answer.GuestPin, host.relays, true, true)
	if err != nil {
		t.Fatal(err)
	}
	if mutate != nil {
		mutate(&peer.answer)
	}
	return peer
}
func (p *labPeer) apply(t *testing.T) error {
	t.Helper()
	envelope, err := SealSignal(p.cfg.Capability, p.cfg.SessionID, p.answer)
	if err != nil {
		t.Fatal(err)
	}
	err = p.host.ApplyAnswer(context.Background(), envelope)
	return err
}
func (p *labPeer) dataChannel(t *testing.T) *webrtc.DataChannel {
	t.Helper()
	select {
	case dc := <-p.channel:
		return dc
	case <-p.host.Done():
		t.Fatalf("host closed: %v", p.host.Err())
	case <-time.After(5 * time.Second):
		t.Fatal("data channel timed out")
	}
	return nil
}
func (p *labPeer) enroll(t *testing.T) *webrtc.DataChannel {
	t.Helper()
	dc := p.dataChannel(t)
	proof, err := EnrollmentProof(p.cfg.Capability, p.cfg.SessionID, p.answer)
	if err != nil {
		t.Fatal(err)
	}
	if err = dc.Send(proof); err != nil {
		t.Fatal(err)
	}
	select {
	case <-p.host.Enrolled():
	case <-p.host.Done():
		t.Fatalf("enrollment failed: %v", p.host.Err())
	case <-time.After(3 * time.Second):
		t.Fatal("enrollment timed out")
	}
	return dc
}
func waitClosed(t *testing.T, endpoint *Endpoint, expected error) {
	t.Helper()
	select {
	case <-endpoint.Done():
		if !errors.Is(endpoint.Err(), expected) {
			t.Fatalf("closed with %v, want %v", endpoint.Err(), expected)
		}
	case <-time.After(3 * time.Second):
		t.Fatal("endpoint did not close")
	}
}

func TestEndpointRequiresExplicitHostPermission(t *testing.T) {
	cfg := testConfig()
	cfg.Enabled = false
	if _, err := New(context.Background(), cfg); err != ErrDisabled {
		t.Fatalf("got %v", err)
	}
	cfg.Enabled = true
	if _, err := New(context.Background(), cfg); err != ErrICE {
		t.Fatalf("production accepts missing ICE credentials: %v", err)
	}
}
func TestPionEnrollmentQuarantineAndBidirectionalOpus(t *testing.T) {
	p := newLabPeer(t, nil)
	if err := p.apply(t); err != nil {
		t.Fatal(err)
	}
	dc := p.dataChannel(t)
	bridge := p.cfg.Bridge.(*memoryBridge)
	for n := 0; n < 4; n++ {
		_ = p.track.WriteRTP(&rtp.Packet{Header: rtp.Header{Version: 2, SequenceNumber: uint16(n), Timestamp: uint32(n * 960)}, Payload: []byte{0xf8, 0xff, 0xfe}})
		time.Sleep(10 * time.Millisecond)
	}
	select {
	case <-bridge.received:
		t.Fatal("quarantined audio reached Jamulus")
	default:
	}
	bridge.mix <- Packet{Payload: []byte{0xf8, 0xff, 0xfe}, SequenceNumber: 1, Timestamp: 960}
	select {
	case <-p.audio:
		t.Fatal("quarantined host mix leaked")
	case <-time.After(30 * time.Millisecond):
	}
	proof, _ := EnrollmentProof(p.cfg.Capability, p.cfg.SessionID, p.answer)
	if err := dc.Send(proof); err != nil {
		t.Fatal(err)
	}
	select {
	case <-p.host.Enrolled():
	case <-time.After(3 * time.Second):
		t.Fatal("enrollment failed")
	}
	if !p.host.Authorized() {
		t.Fatal("gate closed after accepted proof")
	}
	// Browsers send empty RTP probing packets after enrollment. These are
	// valid transport traffic, not malformed Opus frames.
	_ = p.track.WriteRTP(&rtp.Packet{Header: rtp.Header{Version: 2, SequenceNumber: 9, Timestamp: 8640}})
	if err := p.track.WriteRTP(&rtp.Packet{Header: rtp.Header{Version: 2, SequenceNumber: 10, Timestamp: 9600}, Payload: []byte{0xf8, 0xff, 0xfe}}); err != nil {
		t.Fatal(err)
	}
	select {
	case got := <-bridge.received:
		if string(got.Payload) != string([]byte{0xf8, 0xff, 0xfe}) {
			t.Fatal("payload changed")
		}
	case <-time.After(3 * time.Second):
		t.Fatal("browser audio did not reach Jamulus")
	}
	select {
	case got := <-p.audio:
		if got.Timestamp != 960 {
			t.Fatal("RTP timestamp changed")
		}
	case <-time.After(3 * time.Second):
		t.Fatal("host mix did not reach browser")
	}
	if p.host.Bytes() == 0 {
		t.Fatal("network usage not metered")
	}
	if err := dc.Send(proof); err != nil {
		t.Fatal(err)
	}
	waitClosed(t, p.host, ErrReplay)
	select {
	case <-bridge.closed:
	case <-time.After(time.Second):
		t.Fatal("bridge survived endpoint close")
	}
}
func TestPionRejectsEnrollmentTampering(t *testing.T) {
	for _, name := range []string{"bad_pin", "wrong_generation", "expired", "wrong_nonce"} {
		t.Run(name, func(t *testing.T) {
			p := newLabPeer(t, func(a *Signal) {
				switch name {
				case "bad_pin":
					a.GuestPin = strings.Repeat("33", 32)
				case "wrong_generation":
					a.Generation++
				case "expired":
					a.ExpiresAt = time.Now().Add(-time.Second).Unix()
				case "wrong_nonce":
					a.Nonce = strings.Repeat("44", 16)
				}
			})
			err := p.apply(t)
			if err == nil {
				t.Fatal("invalid answer accepted")
			}
			if p.host.Authorized() {
				t.Fatal("invalid answer authorized")
			}
		})
	}
	t.Run("bad_proof", func(t *testing.T) {
		p := newLabPeer(t, nil)
		if err := p.apply(t); err != nil {
			t.Fatal(err)
		}
		dc := p.dataChannel(t)
		if err := dc.Send(make([]byte, 32)); err != nil {
			t.Fatal(err)
		}
		waitClosed(t, p.host, ErrEnrollment)
	})
	t.Run("expired_proof", func(t *testing.T) {
		p := newLabPeer(t, nil)
		if err := p.apply(t); err != nil {
			t.Fatal(err)
		}
		_ = p.dataChannel(t)
		proof, _ := EnrollmentProof(p.cfg.Capability, p.cfg.SessionID, p.answer)
		if err := p.host.authorize(proof, time.Unix(p.answer.ExpiresAt+1, 0)); err != ErrExpired {
			t.Fatalf("got %v", err)
		}
	})
}
func TestPionKickGenerationExpiryAndQuotaClose(t *testing.T) {
	for _, name := range []string{"kick", "generation", "quota", "expiry"} {
		t.Run(name, func(t *testing.T) {
			cfg := testConfig()
			if name == "expiry" {
				cfg.ExpiresAt = time.Now().Add(150 * time.Millisecond)
			}
			e, err := newEndpoint(context.Background(), cfg, true)
			if err != nil {
				t.Fatal(err)
			}
			defer e.Close()
			want := ErrClosed
			switch name {
			case "kick":
				e.Kick()
			case "generation":
				e.SetGeneration(cfg.Generation + 1)
				want = ErrExpired
			case "quota":
				e.quota.take(int(MaxSessionBytes))
				want = ErrQuota
			case "expiry":
				want = ErrExpired
			}
			waitClosed(t, e, want)
		})
	}
}
func TestCandidatePolicy(t *testing.T) {
	relay := map[netip.Addr]bool{netip.MustParseAddr("141.101.90.1"): true}
	tests := []struct {
		candidate string
		allow     bool
	}{
		{"1 1 udp 1 8.8.8.8 4000 typ srflx raddr 0.0.0.0 rport 0", true},
		{"1 1 udp 1 141.101.90.1 4000 typ relay raddr 0.0.0.0 rport 0", true},
		{"1 1 udp 1 8.8.8.8 4000 typ relay", false}, {"1 1 udp 1 8.8.8.8 4000 typ host", false},
		{"1 1 udp 1 8.8.8.8 4000 typ prflx", false}, {"1 1 udp 1 192.168.1.2 4000 typ srflx", false},
		{"1 1 udp 1 100.64.1.2 4000 typ srflx", false}, {"1 1 udp 1 127.0.0.1 4000 typ host", false},
		{"1 1 udp 1 192.0.2.1 4000 typ srflx", false}, {"1 1 tcp 1 8.8.8.8 4000 typ srflx tcptype active", false},
		{"1 2 udp 1 8.8.8.8 4000 typ srflx", false}, {"1 1 udp 1 fe80::1 4000 typ srflx", false},
	}
	for _, tt := range tests {
		candidate, err := ice.UnmarshalCandidate(tt.candidate)
		if err != nil {
			t.Fatal(err)
		}
		if got := candidateAllowed(candidate, relay, false); got != tt.allow {
			t.Errorf("%s: %v", tt.candidate, got)
		}
	}
}
func TestQuotaConcurrentReservationsFailClosed(t *testing.T) {
	var hit atomic.Bool
	q := byteQuota{limit: 1024, onLimit: func() { hit.Store(true) }}
	var wg sync.WaitGroup
	for n := 0; n < 32; n++ {
		wg.Add(1)
		go func() { defer wg.Done(); q.take(64) }()
	}
	wg.Wait()
	if q.used.Load() > 1024 || !hit.Load() {
		t.Fatal("quota failed")
	}
}
func TestFetchICEWebScopeAndCaps(t *testing.T) {
	for _, mode := range []string{"valid", "429", "503", "unscoped", "overlong_ttl", "oversized_quota", "evil_turn", "private_relay"} {
		t.Run(mode, func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				var request map[string]string
				if json.NewDecoder(r.Body).Decode(&request) != nil || request["client"] != "web" || len(request["session_id"]) != 64 || len(request["invite_token"]) != 43 {
					t.Error("missing scoped proof")
				}
				if mode == "429" {
					w.WriteHeader(429)
					return
				}
				if mode == "503" {
					w.WriteHeader(503)
					return
				}
				response := map[string]any{"client": "web", "ttl": 300, "quota_bytes": MaxSessionBytes, "iceServers": []map[string]any{{"urls": []string{"stun:stun.cloudflare.com:3478", "turn:turn.cloudflare.com:3478?transport=udp", "turns:turn.cloudflare.com:5349?transport=tcp"}, "username": "temporary", "credential": "ephemeral"}}}
				switch mode {
				case "unscoped":
					delete(response, "client")
				case "overlong_ttl":
					response["ttl"] = 3601
				case "oversized_quota":
					response["quota_bytes"] = MaxSessionBytes + 1
				case "evil_turn":
					response["iceServers"] = []map[string]any{{"urls": []string{"turn:evil.example:3478"}, "username": "user", "credential": "secret"}}
				case "private_relay":
					response["allowed_relay_ips"] = []string{"127.0.0.1"}
				}
				_ = json.NewEncoder(w).Encode(response)
			}))
			defer server.Close()
			cfg, err := fetchICE(context.Background(), server.Client(), server.URL, [32]byte{1}, [32]byte{2})
			if (err == nil) != (mode == "valid") {
				t.Fatalf("got %v", err)
			}
			if err == nil && len(cfg.Servers[0].URLs) != 3 {
				t.Fatal("TCP/TLS fallback dropped")
			}
		})
	}
}
func TestBrowserProtocolVector(t *testing.T) {
	raw, err := os.ReadFile("../../../web/join/test/vectors.json")
	if err != nil {
		t.Fatal(err)
	}
	var vector map[string]json.RawMessage
	if json.Unmarshal(raw, &vector) != nil {
		t.Fatal("fixture JSON")
	}
	// Fixture names are defined by the browser implementation and deliberately
	// consumed as-is so either side's wire-format drift fails this cross-check.
	str := func(name string) string {
		var s string
		if json.Unmarshal(vector[name], &s) != nil {
			t.Fatalf("missing fixture %s", name)
		}
		return s
	}
	capability := pinBytes(str("capability_hex"))
	session := pinBytes(str("session"))
	envelope, err := base64.RawURLEncoding.DecodeString(str("envelope_b64url"))
	if err != nil {
		t.Fatal(err)
	}
	answer, err := OpenSignal(capability, session, envelope)
	if err != nil {
		t.Fatal(err)
	}
	proof, err := EnrollmentProof(capability, session, answer)
	if err != nil {
		t.Fatal(err)
	}
	if hex.EncodeToString(proof) != str("proof_hex") {
		t.Fatal("browser/Go enrollment proof mismatch")
	}
	envelope[len(envelope)-1] ^= 1
	if _, err = OpenSignal(capability, session, envelope); err != ErrSignal {
		t.Fatal("tampered ciphertext accepted")
	}
}

func TestEndpointConnectEnrollmentAndIdleDeadlines(t *testing.T) {
	for _, phase := range []string{"connect", "enrollment", "idle"} {
		t.Run(phase, func(t *testing.T) {
			endpoint, err := newEndpoint(context.Background(), testConfig(), true)
			if err != nil {
				t.Fatal(err)
			}
			defer endpoint.Close()
			switch phase {
			case "connect":
				endpoint.answerAt.Store(time.Now().Add(-ConnectTimeout - time.Second).UnixNano())
			case "enrollment":
				endpoint.connected.Store(time.Now().Add(-EnrollmentTimeout - time.Second).UnixNano())
			case "idle":
				endpoint.authorized.Store(true)
				endpoint.lastMedia.Store(time.Now().Add(-IdleTimeout - time.Second).UnixNano())
			}
			waitClosed(t, endpoint, ErrTimeout)
		})
	}
}
func TestLocalSDPStripsPrivateRelatedAddresses(t *testing.T) {
	p := newLabPeer(t, nil)
	raw := strings.ReplaceAll(p.answer.SDP, "127.0.0.1", "8.8.8.8")
	raw = strings.ReplaceAll(raw, "typ host", "typ srflx raddr 192.168.42.9 rport 1234")
	raw = strings.ReplaceAll(raw, "IN IP4 0.0.0.0", "IN IP4 192.168.42.9")
	sanitized, err := validateSDP(raw, p.answer.GuestPin, nil, true, false)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(sanitized, "192.168.42.9") || strings.Contains(sanitized, "rport 1234") {
		t.Fatal("private interface leaked through SDP")
	}
	if !strings.Contains(sanitized, "8.8.8.8") || !strings.Contains(sanitized, "raddr 0.0.0.0 rport 0") {
		t.Fatal("valid public route removed")
	}
}
func TestPionRejectsCertificateDifferentFromSealedPin(t *testing.T) {
	p := newLabPeer(t, nil)
	actual := p.answer.GuestPin
	fake := strings.Repeat("55", 32)
	format := func(pin string) string {
		parts := make([]string, 0, 32)
		for n := 0; n < len(pin); n += 2 {
			parts = append(parts, strings.ToUpper(pin[n:n+2]))
		}
		return strings.Join(parts, ":")
	}
	p.answer.GuestPin = fake
	p.answer.SDP = strings.ReplaceAll(p.answer.SDP, format(actual), format(fake))
	if err := p.apply(t); err != nil {
		t.Fatal(err)
	}
	select {
	case <-p.host.Done():
	case <-time.After(3 * time.Second):
		t.Fatal("DTLS pin mismatch failed to close")
	}
	if p.host.Authorized() {
		t.Fatal("wrong actual DTLS certificate enrolled")
	}
}
