// Package webjoin implements the host-local, explicitly enabled Web Join endpoint.
// Signaling is opaque to the mailbox; only the holder of the invite capability
// can supply a peer certificate or ICE candidates.
package webjoin

import (
	"bytes"
	"crypto/aes"
	"crypto/cipher"
	"crypto/hkdf"
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"time"
)

const (
	Domain                  = "webjam/web-join/1"
	MaxEnvelopeBytes        = 8192
	MaxSessionBytes  uint64 = 256 * 1024 * 1024
	MaxOpusBytes            = 1275
)

var (
	ErrDisabled   = errors.New("web join requires explicit session permission")
	ErrConfig     = errors.New("invalid web join configuration")
	ErrSignal     = errors.New("invalid sealed web join signaling")
	ErrPin        = errors.New("web join certificate pin mismatch")
	ErrEnrollment = errors.New("web join enrollment rejected")
	ErrReplay     = errors.New("web join enrollment replayed")
	ErrExpired    = errors.New("web join session or enrollment expired")
	ErrCandidate  = errors.New("web join candidate rejected")
	ErrQuota      = errors.New("web join free session quota exhausted")
	ErrTimeout    = errors.New("web join operation timed out")
	ErrClosed     = errors.New("web join closed")
	ErrBridge     = errors.New("web join audio bridge failed")
	ErrICE        = errors.New("web join relay unavailable or free quota exhausted")
)

// Signal is the versioned plaintext inside the invite-capability AEAD envelope.
// The answer echoes the fresh host challenge, expiry, generation and host pin.
type Signal struct {
	Version    int    `json:"v"`
	Kind       string `json:"kind"`
	Session    string `json:"session"`
	Generation uint32 `json:"generation"`
	ExpiresAt  int64  `json:"expires_at"`
	Nonce      string `json:"nonce"`
	HostPin    string `json:"host_pin"`
	GuestPin   string `json:"guest_pin"`
	SDP        string `json:"sdp"`
}

func signalAEAD(capability, session [32]byte) (cipher.AEAD, error) {
	if capability == ([32]byte{}) || session == ([32]byte{}) {
		return nil, ErrSignal
	}
	key, err := hkdf.Key(sha256.New, capability[:], session[:], Domain+"/aead", 32)
	if err != nil {
		return nil, ErrSignal
	}
	defer clear(key)
	block, err := aes.NewCipher(key)
	if err != nil {
		return nil, ErrSignal
	}
	return cipher.NewGCM(block)
}
func signalAAD(session [32]byte) []byte { return append([]byte(Domain+"/aad"), session[:]...) }
func validHex(s string, n int) bool {
	b, e := hex.DecodeString(s)
	return e == nil && len(b) == n && hex.EncodeToString(b) == s && !allZero(b)
}
func allZero(b []byte) bool {
	for _, v := range b {
		if v != 0 {
			return false
		}
	}
	return true
}
func validSignal(s Signal) bool {
	return s.Version == 1 && (s.Kind == "offer" || s.Kind == "answer") && validHex(s.Session, 32) && s.Generation != 0 && s.ExpiresAt > 0 && validHex(s.Nonce, 16) && validHex(s.HostPin, 32) && ((s.Kind == "offer" && s.GuestPin == "") || (s.Kind == "answer" && validHex(s.GuestPin, 32))) && len(s.SDP) > 0 && len(s.SDP) < MaxEnvelopeBytes
}

// SealSignal is shared by native lab peers and the browser protocol fixtures.
func SealSignal(capability, session [32]byte, signal Signal) ([]byte, error) {
	if !validSignal(signal) || signal.Session != hex.EncodeToString(session[:]) {
		return nil, ErrSignal
	}
	plaintext, err := json.Marshal(signal)
	if err != nil {
		return nil, ErrSignal
	}
	aead, err := signalAEAD(capability, session)
	if err != nil {
		return nil, err
	}
	nonce := make([]byte, aead.NonceSize())
	if _, err = io.ReadFull(rand.Reader, nonce); err != nil {
		return nil, err
	}
	envelope := append([]byte("WJWS1"), nonce...)
	envelope = aead.Seal(envelope, nonce, plaintext, signalAAD(session))
	if len(envelope) > MaxEnvelopeBytes {
		return nil, ErrSignal
	}
	return envelope, nil
}

// OpenSignal authenticates before JSON, SDP, or any remote address is used.
func OpenSignal(capability, session [32]byte, envelope []byte) (Signal, error) {
	if len(envelope) < 33 || len(envelope) > MaxEnvelopeBytes || string(envelope[:5]) != "WJWS1" {
		return Signal{}, ErrSignal
	}
	aead, err := signalAEAD(capability, session)
	if err != nil {
		return Signal{}, err
	}
	plain, err := aead.Open(nil, envelope[5:17], envelope[17:], signalAAD(session))
	if err != nil {
		return Signal{}, ErrSignal
	}
	decoder := json.NewDecoder(bytes.NewReader(plain))
	decoder.DisallowUnknownFields()
	var signal Signal
	if decoder.Decode(&signal) != nil || decoder.Decode(new(any)) != io.EOF || !validSignal(signal) || signal.Session != hex.EncodeToString(session[:]) {
		return Signal{}, ErrSignal
	}
	return signal, nil
}

// EnrollmentProof is HMAC over the one-use challenge and both certificate pins.
// Browsers cannot export DTLS keying material. Instead, the proof MUST arrive on
// the DTLS data channel whose actual certificate matches the sealed guest pin;
// it is never accepted over IPC or the mailbox as authorization to send media.
func EnrollmentProof(capability, session [32]byte, answer Signal) ([]byte, error) {
	if !validSignal(answer) || answer.Kind != "answer" || answer.Session != hex.EncodeToString(session[:]) || capability == ([32]byte{}) {
		return nil, ErrEnrollment
	}
	key, err := hkdf.Key(sha256.New, capability[:], session[:], Domain+"/enrollment", 32)
	if err != nil {
		return nil, ErrEnrollment
	}
	defer clear(key)
	body := fmt.Sprintf("%s/enrollment\n%s\nguest\n%d\n%d\n%s\n%s\n%s", Domain, answer.Session, answer.Generation, answer.ExpiresAt, answer.Nonce, answer.HostPin, answer.GuestPin)
	mac := hmac.New(sha256.New, key)
	_, _ = mac.Write([]byte(body))
	return mac.Sum(nil), nil
}
func sameBinding(offer, answer Signal, now time.Time) error {
	if !time.Unix(answer.ExpiresAt, 0).After(now) {
		return ErrExpired
	}
	if answer.Kind != "answer" || answer.Session != offer.Session || answer.Generation != offer.Generation || answer.Nonce != offer.Nonce || answer.ExpiresAt != offer.ExpiresAt || answer.HostPin != offer.HostPin {
		return ErrEnrollment
	}
	return nil
}
