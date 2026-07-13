// Package sessionauth keeps the QUIC data planes quarantined until both peers
// verify an enrollment proof bound to this TLS connection, session, generation,
// and peer pins. The TLS exporter prevents a proof captured on one QUIC
// connection from authorizing another.
package sessionauth

import (
	"bytes"
	"context"
	"crypto/hkdf"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/binary"
	"errors"
	"io"
	"sync"
	"sync/atomic"
	"time"

	"github.com/rupret007/webjam/transport/internal/limits"
)

const (
	proofMagic     = "WJEP"
	proofVersion   = 1
	exporterLabel  = "EXPORTER-WebJam-v3-enrollment"
	proofKeyDomain = "webjam/v3/enrollment-proof/hmac"
	proofBytes     = 162
)

type Role uint8

const (
	RoleHost  Role = 1
	RoleGuest Role = 2
)

func (r Role) valid() bool { return r == RoleHost || r == RoleGuest }

type Capability [32]byte
type SessionID [32]byte
type PeerPin [32]byte
type Nonce [16]byte

type Binding struct {
	SessionID  SessionID
	SenderRole Role
	Generation uint32
	HostPin    PeerPin
	GuestPin   PeerPin
	Nonce      Nonce
	ExpiresAt  time.Time
}

var (
	ErrQuarantined   = errors.New("session data plane remains quarantined")
	ErrInvalidProof  = errors.New("invalid enrollment proof")
	ErrProofMismatch = errors.New("enrollment proof context mismatch")
	ErrProofExpired  = errors.New("enrollment proof expired")
	ErrProofReplay   = errors.New("enrollment proof replayed")
)

type Gate struct{ authorized atomic.Bool }

func NewGate() *Gate { return &Gate{} }

func (g *Gate) Authorized() bool { return g != nil && g.authorized.Load() }

func (g *Gate) Require() error {
	if !g.Authorized() {
		return ErrQuarantined
	}
	return nil
}

type exporter func(label string, context []byte, length int) ([]byte, error)

type Exporter interface {
	ExportKeyingMaterial(label string, context []byte, length int) ([]byte, error)
}

func CreateProof(connection Exporter, capability Capability, binding Binding, now time.Time) ([]byte, error) {
	if connection == nil {
		return nil, ErrInvalidProof
	}
	return createProof(connection.ExportKeyingMaterial, capability, binding, now)
}

func VerifyAndAuthorize(
	connection Exporter,
	capability Capability,
	expected Binding,
	proof []byte,
	now time.Time,
	replays *ReplayCache,
	gate *Gate,
) error {
	if connection == nil || gate == nil || replays == nil {
		return ErrInvalidProof
	}
	if err := verifyProof(connection.ExportKeyingMaterial, capability, expected, proof, now, replays); err != nil {
		return err
	}
	gate.authorized.Store(true)
	return nil
}

func createProof(export exporter, capability Capability, binding Binding, now time.Time) ([]byte, error) {
	if capability == (Capability{}) {
		return nil, ErrInvalidProof
	}
	if err := validateBinding(binding, now); err != nil {
		return nil, err
	}
	body := marshalBinding(binding)
	key, err := proofKey(export, capability, body)
	if err != nil {
		return nil, err
	}
	mac := hmac.New(sha256.New, key)
	_, _ = mac.Write(body)
	return append(body, mac.Sum(nil)...), nil
}

func verifyProof(
	export exporter,
	capability Capability,
	expected Binding,
	proof []byte,
	now time.Time,
	replays *ReplayCache,
) error {
	if capability == (Capability{}) || len(proof) != proofBytes {
		return ErrInvalidProof
	}
	body := proof[:proofBytes-sha256.Size]
	binding, err := unmarshalBinding(body)
	if err != nil {
		return err
	}
	if binding.SessionID != expected.SessionID || binding.SenderRole != expected.SenderRole ||
		binding.Generation != expected.Generation || binding.HostPin != expected.HostPin ||
		binding.GuestPin != expected.GuestPin || binding.Nonce != expected.Nonce ||
		binding.ExpiresAt.Unix() != expected.ExpiresAt.Unix() {
		return ErrProofMismatch
	}
	if err = validateBinding(binding, now); err != nil {
		return err
	}
	key, err := proofKey(export, capability, body)
	if err != nil {
		return err
	}
	mac := hmac.New(sha256.New, key)
	_, _ = mac.Write(body)
	if !hmac.Equal(mac.Sum(nil), proof[len(body):]) {
		return ErrInvalidProof
	}
	return replays.Accept(binding.Nonce, binding.ExpiresAt, now)
}

func proofKey(export exporter, capability Capability, body []byte) ([]byte, error) {
	contextHash := sha256.Sum256(body)
	exported, err := export(exporterLabel, contextHash[:], 32)
	if err != nil {
		return nil, err
	}
	return hkdf.Key(sha256.New, capability[:], exported, proofKeyDomain, 32)
}

func marshalBinding(binding Binding) []byte {
	body := make([]byte, 0, proofBytes-sha256.Size)
	body = append(body, proofMagic...)
	body = append(body, proofVersion)
	body = append(body, binding.SessionID[:]...)
	body = append(body, byte(binding.SenderRole))
	var encoded [8]byte
	binary.BigEndian.PutUint32(encoded[:4], binding.Generation)
	body = append(body, encoded[:4]...)
	body = append(body, binding.HostPin[:]...)
	body = append(body, binding.GuestPin[:]...)
	body = append(body, binding.Nonce[:]...)
	binary.BigEndian.PutUint64(encoded[:], uint64(binding.ExpiresAt.Unix()))
	body = append(body, encoded[:]...)
	return body
}

func unmarshalBinding(body []byte) (Binding, error) {
	if len(body) != proofBytes-sha256.Size {
		return Binding{}, ErrInvalidProof
	}
	reader := bytes.NewReader(body)
	magic := make([]byte, len(proofMagic))
	if _, err := io.ReadFull(reader, magic); err != nil || string(magic) != proofMagic {
		return Binding{}, ErrInvalidProof
	}
	version, err := reader.ReadByte()
	if err != nil || version != proofVersion {
		return Binding{}, ErrInvalidProof
	}
	var binding Binding
	if _, err = io.ReadFull(reader, binding.SessionID[:]); err != nil {
		return Binding{}, ErrInvalidProof
	}
	role, err := reader.ReadByte()
	if err != nil {
		return Binding{}, ErrInvalidProof
	}
	binding.SenderRole = Role(role)
	var encoded [8]byte
	if _, err = io.ReadFull(reader, encoded[:4]); err != nil {
		return Binding{}, ErrInvalidProof
	}
	binding.Generation = binary.BigEndian.Uint32(encoded[:4])
	if _, err = io.ReadFull(reader, binding.HostPin[:]); err != nil {
		return Binding{}, ErrInvalidProof
	}
	if _, err = io.ReadFull(reader, binding.GuestPin[:]); err != nil {
		return Binding{}, ErrInvalidProof
	}
	if _, err = io.ReadFull(reader, binding.Nonce[:]); err != nil {
		return Binding{}, ErrInvalidProof
	}
	if _, err = io.ReadFull(reader, encoded[:]); err != nil || reader.Len() != 0 {
		return Binding{}, ErrInvalidProof
	}
	expires := binary.BigEndian.Uint64(encoded[:])
	if expires > uint64(^uint64(0)>>1) {
		return Binding{}, ErrInvalidProof
	}
	binding.ExpiresAt = time.Unix(int64(expires), 0).UTC()
	return binding, nil
}

func validateBinding(binding Binding, now time.Time) error {
	if !binding.SenderRole.valid() || binding.Generation == 0 || binding.SessionID == (SessionID{}) ||
		binding.HostPin == (PeerPin{}) || binding.GuestPin == (PeerPin{}) || binding.Nonce == (Nonce{}) {
		return ErrInvalidProof
	}
	if !binding.ExpiresAt.After(now) || binding.ExpiresAt.After(now.Add(limits.MaxEnrollmentLifetime)) {
		return ErrProofExpired
	}
	return nil
}

type ReplayCache struct {
	mu      sync.Mutex
	expires map[Nonce]time.Time
}

func NewReplayCache() *ReplayCache { return &ReplayCache{expires: make(map[Nonce]time.Time)} }

func (c *ReplayCache) Accept(nonce Nonce, expiresAt, now time.Time) error {
	c.mu.Lock()
	defer c.mu.Unlock()
	for knownNonce, expiry := range c.expires {
		if !expiry.After(now) {
			delete(c.expires, knownNonce)
		}
	}
	if _, exists := c.expires[nonce]; exists {
		return ErrProofReplay
	}
	if len(c.expires) >= limits.MaxReplayNonces {
		return ErrInvalidProof
	}
	c.expires[nonce] = expiresAt
	return nil
}

// WaitAuthorized is useful to an owner that completes proof exchange on a
// dedicated enrollment stream before starting the live or reliable planes.
func (g *Gate) WaitAuthorized(ctx context.Context) error {
	ticker := time.NewTicker(5 * time.Millisecond)
	defer ticker.Stop()
	for {
		if g.Authorized() {
			return nil
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-ticker.C:
		}
	}
}
