// Package reference implements the native client for the isolated WebJam v3
// reference rendezvous and exact-pair relay. It is deliberately separate from
// Pion's direct / STUN / TURN path; the reference relay is not a TURN server.
package reference

import (
	"crypto/hkdf"
	"crypto/rand"
	"crypto/sha256"
	"errors"
)

const (
	ProtocolVersion       = 3
	ControlAddress        = "127.0.0.1:47131"
	RelayAddress          = "127.0.0.1:47132"
	HealthAddress         = "127.0.0.1:47133"
	MaxControlFrameBytes  = 16_384
	MaxSignalPayloadBytes = 8_192
	MaxRelayDatagramBytes = 1_420
	MaxRelayPayloadBytes  = 1_350
)

type SessionID [32]byte
type Capability [32]byte
type PeerPin [32]byte
type BootstrapNonce [16]byte

const enrollmentTokenDomain = "webjam/v3/reference-service/enrollment-token"

type Role uint8

const (
	RoleHost Role = iota
	RoleGuest
)

func (r Role) valid() bool { return r == RoleHost || r == RoleGuest }

func (r Role) text() string {
	if r == RoleHost {
		return "host"
	}
	if r == RoleGuest {
		return "guest"
	}
	return ""
}

func (r Role) opposite() Role {
	if r == RoleHost {
		return RoleGuest
	}
	return RoleHost
}

// RoleToken is an unexported-byte, redacted credential. Call Destroy after the
// control client and relay PacketConn no longer need it.
type RoleToken struct{ value [32]byte }

// EnrollmentToken is domain-separated from the invitation capability and
// bound to one derived session ID. The raw invitation capability never crosses
// the reference-service control connection.
type EnrollmentToken struct {
	value   [32]byte
	session SessionID
}

func NewRoleToken() (*RoleToken, error) {
	token := &RoleToken{}
	if _, err := rand.Read(token.value[:]); err != nil || token.value == ([32]byte{}) {
		token.Destroy()
		return nil, ErrRandom
	}
	return token, nil
}

func (t *RoleToken) Destroy() {
	if t != nil {
		clear(t.value[:])
	}
}

func (t *RoleToken) String() string   { return "<redacted>" }
func (t *RoleToken) GoString() string { return "reference.RoleToken{<redacted>}" }

func (t *RoleToken) valid() bool { return t != nil && t.value != ([32]byte{}) }

func DeriveEnrollmentToken(
	capability Capability, session SessionID,
) (*EnrollmentToken, error) {
	if !nonzero(capability) || !nonzero(session) {
		return nil, ErrInvalidInput
	}
	derived, err := hkdf.Key(
		sha256.New, capability[:], session[:], enrollmentTokenDomain, 32,
	)
	if err != nil || len(derived) != 32 {
		clear(derived)
		return nil, ErrCredentialDerivation
	}
	token := &EnrollmentToken{session: session}
	copy(token.value[:], derived)
	clear(derived)
	if token.value == ([32]byte{}) {
		token.Destroy()
		return nil, ErrCredentialDerivation
	}
	return token, nil
}

func (t *EnrollmentToken) Destroy() {
	if t != nil {
		clear(t.value[:])
		clear(t.session[:])
	}
}

func (t *EnrollmentToken) String() string   { return "<redacted>" }
func (t *EnrollmentToken) GoString() string { return "reference.EnrollmentToken{<redacted>}" }

func (t *EnrollmentToken) validFor(session SessionID) bool {
	return t != nil && t.value != ([32]byte{}) && t.session == session
}

var (
	ErrRandom               = errors.New("reference credential generation failed")
	ErrCredentialDerivation = errors.New("reference credential derivation failed")
	ErrInvalidInput         = errors.New("invalid reference request")
	ErrControlUnavailable   = errors.New("reference control unavailable")
	ErrControlProtocol      = errors.New("reference control protocol violation")
	ErrUnauthorized         = errors.New("reference request unauthorized")
	ErrSessionConflict      = errors.New("reference session already registered")
	ErrEnrollmentUsed       = errors.New("reference enrollment already used")
	ErrReplay               = errors.New("reference message replayed")
	ErrOverloaded           = errors.New("reference service overloaded")
	ErrQueueFull            = errors.New("reference signaling queue full")
	ErrClosed               = errors.New("reference client closed")
	ErrBootstrap            = errors.New("invalid guest bootstrap")
	ErrBootstrapContext     = errors.New("guest bootstrap context mismatch")
	ErrBootstrapExpired     = errors.New("guest bootstrap expired")
	ErrBootstrapReplay      = errors.New("guest bootstrap replayed")
	ErrUnexpectedPeer       = errors.New("reference packet addressed to unexpected peer")
	ErrRelayFrame           = errors.New("invalid reference relay frame")
	ErrRelayUnavailable     = errors.New("reference relay unavailable")
)

func nonzero[T ~[16]byte | ~[32]byte](value T) bool {
	var zero T
	return value != zero
}
