package webjoin

import (
	"bytes"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"log"
	"os"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/pion/webrtc/v4"
)

func TestGameOfferPolicyRejectsMalformedAndOversize(t *testing.T) {
	for _, name := range []string{"", " ", strings.Repeat("A", 41), "Ada\n", "Ada&Co", "Ada/../x"} {
		if _, _, _, err := buildGameOffer("chess", name); !errors.Is(err, ErrGameOffer) {
			t.Fatalf("host name %q: %v", name, err)
		}
	}
	if _, _, _, err := buildGameOffer("rummy", "Ada"); !errors.Is(err, ErrGameOffer) {
		t.Fatal("non-chess offer built")
	}
	if _, err := buildGameTableState("Ada", "join"); !errors.Is(err, ErrGameOffer) {
		t.Fatal("unknown state built")
	}
	full, err := buildGameTableState("Ada", "full")
	if err != nil {
		t.Fatal(err)
	}
	if bytes.Contains(full, []byte(`"value"`)) {
		t.Fatal("table-full message carried a value")
	}
	expired, err := buildGameTableState("Ada", "expired")
	if err != nil || bytes.Contains(expired, []byte(`"value"`)) {
		t.Fatal("expired message carried a value")
	}
	oversized := bytes.Repeat([]byte("x"), maxGameOfferBytes+1)
	if !gameOfferTooBig(oversized) || gameOfferTooBig([]byte(`{"game":"chess"}`)) {
		t.Fatal("oversize payload accepted")
	}
	if !validGameOfferToken(base64.RawURLEncoding.EncodeToString(bytes.Repeat([]byte{9}, 16))) {
		t.Fatal("128-bit token rejected")
	}
	if validGameOfferToken(base64.RawURLEncoding.EncodeToString(bytes.Repeat([]byte{9}, 15))) {
		t.Fatal("short token accepted")
	}
}

func TestGameOfferSourceDoesNotPersistOrLogTheValue(t *testing.T) {
	source, err := os.ReadFile("game_offer.go")
	if err != nil {
		t.Fatal(err)
	}
	text := string(source)
	if strings.Contains(text, "os.Write") || strings.Contains(text, "os.Create") || strings.Contains(text, "ioutil.") {
		t.Fatal("game offer can persist the value")
	}
	for _, line := range strings.Split(text, "\n") {
		if !strings.Contains(line, "log.") {
			continue
		}
		for _, leak := range []string{"payload", "secret", "token", "Value", "hash"} {
			if strings.Contains(line, leak) {
				t.Fatalf("log line can carry offer material: %s", line)
			}
		}
	}
}

func TestGameOfferValueAndDigestNeverLoggedOrRetained(t *testing.T) {
	peer, channels := offerLab(t)
	authorizeLab(t, peer, channels["webjoin-enrollment"])
	waitHostOfferOpen(t, peer.host)
	logs := captureLog(t)
	incoming := watchText(channels[gameOfferChannelLabel])

	hash, err := peer.host.SendGameOffer("Ada")
	if err != nil {
		t.Fatal(err)
	}
	body := takeOffer(t, incoming)
	if body.Game != "chess" || body.HostName != "Ada" || body.Value == "" || body.State != "" {
		t.Fatal("offer shape")
	}
	raw, err := base64.RawURLEncoding.DecodeString(body.Value)
	if err != nil || len(raw) < 16 {
		t.Fatal("value entropy")
	}
	sum := sha256.Sum256([]byte(body.Value))
	if hex.EncodeToString(sum[:]) != hash || hash == body.Value {
		t.Fatal("hash mismatch")
	}
	if strings.Contains(logs.String(), body.Value) || strings.Contains(logs.String(), hash) {
		t.Fatal("one-time value was logged")
	}
	if !strings.Contains(logs.String(), "webjoin game offer sent") {
		t.Fatal("expected offer log")
	}
	retained := reflect.ValueOf(peer.host.gameOffer).Elem()
	for i := 0; i < retained.NumField(); i++ {
		field := retained.Field(i)
		if field.Kind() == reflect.String && (strings.Contains(field.String(), body.Value) || strings.Contains(field.String(), hash)) {
			t.Fatal("offer material retained on the endpoint")
		}
	}
	clear(raw)
	if _, err = peer.host.SendGameOffer("Ada"); !errors.Is(err, ErrGameOffer) {
		t.Fatal("second offer accepted")
	}
	expiredWatch := watchText(channels[gameOfferChannelLabel])
	if err = peer.host.SendGameTableState("Ada", "expired"); err != nil {
		t.Fatal(err)
	}
	expired := takeOffer(t, expiredWatch)
	if expired.State != "expired" || expired.Value != "" {
		t.Fatal("expired state carried a value")
	}
	if strings.Contains(logs.String(), body.Value) {
		t.Fatal("one-time value was logged")
	}
}

func TestExpiredGameOfferCanBeReplacedOnSameConnection(t *testing.T) {
	peer, channels := offerLab(t)
	authorizeLab(t, peer, channels["webjoin-enrollment"])
	waitHostOfferOpen(t, peer.host)
	incoming := watchText(channels[gameOfferChannelLabel])

	firstDigest, err := peer.host.SendGameOffer("Ada")
	if err != nil {
		t.Fatal("first offer failed", err)
	}
	first := takeOffer(t, incoming)
	peer.host.gameOffer.mu.Lock()
	peer.host.gameOffer.offeredUntil = time.Now().Add(-time.Second)
	peer.host.gameOffer.mu.Unlock()
	secondDigest, err := peer.host.SendGameOffer("Ada")
	if err != nil {
		t.Fatal("replacement offer failed", err)
	}
	second := takeOffer(t, incoming)
	if firstDigest == secondDigest || first.Value == second.Value {
		t.Fatal("the replacement reused the prior offer")
	}
	if !peer.host.Authorized() {
		t.Fatal("the enrolled audio connection was lost")
	}
}

func TestGameOfferRejectedBeforeAuthorized(t *testing.T) {
	peer, channels := offerLab(t)
	waitHostOfferOpen(t, peer.host)
	logs := captureLog(t)
	incoming := watchText(channels[gameOfferChannelLabel])
	if _, err := peer.host.SendGameOffer("Ada"); !errors.Is(err, ErrGameOffer) {
		t.Fatal(err)
	}
	if err := peer.host.SendGameTableState("Ada", "full"); !errors.Is(err, ErrGameOffer) {
		t.Fatal(err)
	}
	select {
	case <-incoming:
		t.Fatal("offer delivered before authorization")
	case <-peer.host.Done():
		t.Fatal("early offer closed the session")
	case <-time.After(300 * time.Millisecond):
	}
	if peer.host.Authorized() {
		t.Fatal("guest became authorized")
	}
	if strings.Contains(logs.String(), "webjoin game offer sent") {
		t.Fatal("early offer was logged as sent")
	}
}

func TestGameOfferRejectedOnEnrollmentChannel(t *testing.T) {
	peer, channels := offerLab(t)
	authorizeLab(t, peer, channels["webjoin-enrollment"])
	// A game offer must not ride the enrollment channel, even after authorized.
	if err := channels["webjoin-enrollment"].SendText(`{"game":"chess","hostName":"Ada","value":"aaaaaaaaaaaaaaaaaaaaaa"}`); err != nil {
		t.Fatal(err)
	}
	waitClosed(t, peer.host, ErrEnrollment)
}

func TestGuestGameOfferRejected(t *testing.T) {
	peer, channels := offerLab(t)
	authorizeLab(t, peer, channels["webjoin-enrollment"])
	if err := channels[gameOfferChannelLabel].SendText(`{"game":"chess","hostName":"Ada","value":"bbbbbbbbbbbbbbbbbbbbbb"}`); err != nil {
		t.Fatal(err)
	}
	waitClosed(t, peer.host, ErrGameOffer)
}

func offerLab(t *testing.T) (*labPeer, map[string]*webrtc.DataChannel) {
	t.Helper()
	peer := newLabPeer(t, nil, true)
	incoming := make(chan *webrtc.DataChannel, 4)
	peer.guest.OnDataChannel(func(dc *webrtc.DataChannel) {
		dc.OnOpen(func() { incoming <- dc })
	})
	if err := peer.apply(t); err != nil {
		t.Fatal(err)
	}
	found := map[string]*webrtc.DataChannel{}
	deadline := time.After(5 * time.Second)
	for len(found) < 2 {
		select {
		case dc := <-incoming:
			found[dc.Label()] = dc
		case <-peer.host.Done():
			t.Fatalf("host closed before the game offer channel: %v", peer.host.Err())
		case <-deadline:
			t.Fatal("game offer channel did not open")
		}
	}
	if found["webjoin-enrollment"] == nil || found[gameOfferChannelLabel] == nil {
		t.Fatal("missing channel")
	}
	return peer, found
}

func authorizeLab(t *testing.T, peer *labPeer, enrollment *webrtc.DataChannel) {
	t.Helper()
	proof, err := EnrollmentProof(peer.cfg.Capability, peer.cfg.SessionID, peer.answer)
	if err != nil {
		t.Fatal(err)
	}
	if err = enrollment.Send(proof); err != nil {
		t.Fatal(err)
	}
	select {
	case <-peer.host.Enrolled():
	case <-peer.host.Done():
		t.Fatalf("enrollment failed: %v", peer.host.Err())
	case <-time.After(3 * time.Second):
		t.Fatal("enrollment timed out")
	}
}

func waitHostOfferOpen(t *testing.T, host *Endpoint) {
	t.Helper()
	deadline := time.Now().Add(3 * time.Second)
	for time.Now().Before(deadline) {
		if host.gameOffer != nil && host.gameOffer.dc.ReadyState() == webrtc.DataChannelStateOpen {
			return
		}
		time.Sleep(10 * time.Millisecond)
	}
	t.Fatal("game offer channel not open")
}

func watchText(dc *webrtc.DataChannel) <-chan []byte {
	got := make(chan []byte, 4)
	dc.OnMessage(func(message webrtc.DataChannelMessage) {
		got <- append([]byte(nil), message.Data...)
	})
	return got
}

func takeOffer(t *testing.T, incoming <-chan []byte) gameOfferMessage {
	t.Helper()
	select {
	case payload := <-incoming:
		var body gameOfferMessage
		if json.Unmarshal(payload, &body) != nil {
			t.Fatal("malformed offer")
		}
		clear(payload)
		return body
	case <-time.After(3 * time.Second):
		t.Fatal("offer timed out")
	}
	return gameOfferMessage{}
}

func captureLog(t *testing.T) *bytes.Buffer {
	t.Helper()
	var buf bytes.Buffer
	previous := log.Writer()
	flags := log.Flags()
	log.SetOutput(&buf)
	log.SetFlags(0)
	t.Cleanup(func() {
		log.SetOutput(previous)
		log.SetFlags(flags)
	})
	return &buf
}
