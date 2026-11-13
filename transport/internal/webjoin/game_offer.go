package webjoin

import (
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"log"
	"sync"
	"time"

	"github.com/pion/webrtc/v4"
)

const (
	gameOfferChannelLabel = "webjoin-game-offer"
	gameOfferBytes        = 32
	maxGameOfferBytes     = 128
	gameOfferLifetime     = 2 * time.Minute
)

// ErrGameOffer rejects a game offer. The error text never includes the one-time value.
var ErrGameOffer = errors.New("web join game offer rejected")

// gameOfferChannel is the host-created sibling of webjoin-enrollment.
// The expiry prevents overlapping offers on the same guest connection.
type gameOfferChannel struct {
	dc           *webrtc.DataChannel
	mu           sync.Mutex
	offeredUntil time.Time
}

type gameOfferMessage struct {
	Game     string `json:"game"`
	HostName string `json:"hostName"`
	Value    string `json:"value,omitempty"`
	State    string `json:"state,omitempty"`
}

func openGameOfferChannel(e *Endpoint, pc *webrtc.PeerConnection) error {
	dc, err := pc.CreateDataChannel(gameOfferChannelLabel, nil)
	if err != nil {
		return err
	}
	slot := &gameOfferChannel{dc: dc}
	// Messages from the guest are never offers. Drop them without logging the body.
	dc.OnMessage(func(webrtc.DataChannelMessage) {
		e.stop(ErrGameOffer)
	})
	e.gameOffer = slot
	return nil
}

func validHostName(name string) bool {
	if len(name) == 0 || len(name) > 40 {
		return false
	}
	trimmed := false
	for i := 0; i < len(name); i++ {
		c := name[i]
		switch {
		case c >= 'a' && c <= 'z', c >= 'A' && c <= 'Z', c >= '0' && c <= '9':
			trimmed = true
		case c == ' ' || c == '.' || c == '-' || c == '\'':
		default:
			return false
		}
	}
	return trimmed
}

func validGameOfferToken(token string) bool {
	if len(token) < 22 || len(token) > 43 {
		return false
	}
	raw, err := base64.RawURLEncoding.DecodeString(token)
	if err != nil {
		return false
	}
	defer clear(raw)
	if len(raw) < 16 || len(raw) > gameOfferBytes {
		return false
	}
	return base64.RawURLEncoding.EncodeToString(raw) == token
}

// The Worker hashes the encoded invitation value and stores a lowercase hex digest.
// The host page fragment carries this hash, never the value.
func hashGameOfferValue(value string) string {
	sum := sha256.Sum256([]byte(value))
	return hex.EncodeToString(sum[:])
}

func buildGameOffer(game, hostName string) (payload []byte, hash string, secret []byte, err error) {
	if game != "chess" || !validHostName(hostName) {
		return nil, "", nil, ErrGameOffer
	}
	secret = make([]byte, gameOfferBytes)
	if _, err = rand.Read(secret); err != nil {
		clear(secret)
		return nil, "", nil, ErrGameOffer
	}
	token := base64.RawURLEncoding.EncodeToString(secret)
	if !validGameOfferToken(token) {
		clear(secret)
		return nil, "", nil, ErrGameOffer
	}
	hash = hashGameOfferValue(token)
	payload, err = json.Marshal(gameOfferMessage{Game: game, HostName: hostName, Value: token})
	if err != nil || len(payload) == 0 || len(payload) > maxGameOfferBytes {
		clear(secret)
		clear(payload)
		return nil, "", nil, ErrGameOffer
	}
	return payload, hash, secret, nil
}

func buildGameTableState(hostName, state string) ([]byte, error) {
	if !validHostName(hostName) || (state != "full" && state != "expired") {
		return nil, ErrGameOffer
	}
	payload, err := json.Marshal(gameOfferMessage{Game: "chess", HostName: hostName, State: state})
	if err != nil || len(payload) == 0 || len(payload) > maxGameOfferBytes || containsOfferValue(payload) {
		clear(payload)
		return nil, ErrGameOffer
	}
	return payload, nil
}

func containsOfferValue(payload []byte) bool {
	var body gameOfferMessage
	if json.Unmarshal(payload, &body) != nil || body.Value != "" {
		return true
	}
	return false
}

func gameOfferTooBig(payload []byte) bool {
	return len(payload) == 0 || len(payload) > maxGameOfferBytes
}

func (e *Endpoint) sendGameOfferText(payload []byte) error {
	if e == nil || !e.authorized.Load() || e.gameOffer == nil || e.gameOffer.dc == nil {
		return ErrGameOffer
	}
	if e.gameOffer.dc.ReadyState() != webrtc.DataChannelStateOpen {
		return ErrGameOffer
	}
	if gameOfferTooBig(payload) {
		return ErrGameOffer
	}
	if err := e.gameOffer.dc.SendText(string(payload)); err != nil {
		return ErrGameOffer
	}
	return nil
}

// SendGameOffer delivers one live Chess offer at a time to this enrolled guest.
// It returns hash(value) for the host page fragment. The value is not retained.
func (e *Endpoint) SendGameOffer(hostName string) (string, error) {
	if e == nil || e.gameOffer == nil || !e.authorized.Load() {
		return "", ErrGameOffer
	}
	e.gameOffer.mu.Lock()
	defer e.gameOffer.mu.Unlock()
	if time.Now().Before(e.gameOffer.offeredUntil) || !e.authorized.Load() {
		return "", ErrGameOffer
	}
	payload, hash, secret, err := buildGameOffer("chess", hostName)
	if err != nil {
		return "", ErrGameOffer
	}
	defer clear(secret)
	defer clear(payload)
	if err = e.sendGameOfferText(payload); err != nil {
		return "", ErrGameOffer
	}
	e.gameOffer.offeredUntil = time.Now().Add(gameOfferLifetime)
	// The log line is the event only. The value and the hash stay out of it.
	log.Printf("webjoin game offer sent")
	return hash, nil
}

// SendGameTableState tells an enrolled guest the table is full or the offer expired.
// The message has no one-time value.
func (e *Endpoint) SendGameTableState(hostName, state string) error {
	if e == nil || e.gameOffer == nil || !e.authorized.Load() {
		return ErrGameOffer
	}
	payload, err := buildGameTableState(hostName, state)
	if err != nil {
		return ErrGameOffer
	}
	defer clear(payload)
	e.gameOffer.mu.Lock()
	defer e.gameOffer.mu.Unlock()
	if err = e.sendGameOfferText(payload); err != nil {
		return ErrGameOffer
	}
	log.Printf("webjoin game offer %s", state)
	return nil
}
