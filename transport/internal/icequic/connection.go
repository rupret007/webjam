package icequic

import (
	"context"
	"crypto/sha256"
	"crypto/subtle"
	"errors"
	"time"

	"github.com/quic-go/quic-go"
	"github.com/rupret007/webjam/transport/internal/limits"
	"github.com/rupret007/webjam/transport/internal/sessionauth"
)

var ErrPeerIdentityMismatch = errors.New("TLS peer identity does not match enrollment proof")

// Connection is deliberately narrower than quic.Conn. Live datagrams and
// application streams cannot be opened or received until the connection's
// enrollment gate is authorized by a TLS-exporter-bound proof.
type Connection struct {
	inner       *quic.Conn
	gate        *sessionauth.Gate
	streamSlots chan struct{}
}

func newConnection(inner *quic.Conn) (*Connection, error) {
	if inner == nil {
		return nil, errors.New("nil QUIC connection")
	}
	return &Connection{
		inner: inner, gate: sessionauth.NewGate(),
		streamSlots: make(chan struct{}, limits.MaxConcurrentStreams),
	}, nil
}

func (c *Connection) RequireAuthorized() error { return c.gate.Require() }

func (c *Connection) ExportKeyingMaterial(label string, context []byte, length int) ([]byte, error) {
	state := c.inner.ConnectionState().TLS
	return state.ExportKeyingMaterial(label, context, length)
}

// VerifyAndAuthorize is the only transition that can open this connection's
// data planes. In addition to the exporter-bound proof, it requires the pin
// named for the sender role to match the certificate on this TLS connection.
func (c *Connection) VerifyAndAuthorize(
	capability sessionauth.Capability,
	expected sessionauth.Binding,
	proof []byte,
	now time.Time,
	replays *sessionauth.ReplayCache,
) error {
	peerCertificates := c.inner.ConnectionState().TLS.PeerCertificates
	if len(peerCertificates) != 1 {
		return ErrPeerIdentityMismatch
	}
	actual := sha256.Sum256(peerCertificates[0].RawSubjectPublicKeyInfo)
	var expectedPin sessionauth.PeerPin
	switch expected.SenderRole {
	case sessionauth.RoleHost:
		expectedPin = expected.HostPin
	case sessionauth.RoleGuest:
		expectedPin = expected.GuestPin
	default:
		return ErrPeerIdentityMismatch
	}
	if subtle.ConstantTimeCompare(actual[:], expectedPin[:]) != 1 {
		return ErrPeerIdentityMismatch
	}
	return sessionauth.VerifyAndAuthorize(c, capability, expected, proof, now, replays, c.gate)
}

func (c *Connection) SendDatagram(payload []byte) error {
	if err := c.RequireAuthorized(); err != nil {
		return err
	}
	return c.inner.SendDatagram(payload)
}

func (c *Connection) ReceiveDatagram(ctx context.Context) ([]byte, error) {
	if err := c.RequireAuthorized(); err != nil {
		return nil, err
	}
	return c.inner.ReceiveDatagram(ctx)
}

func (c *Connection) OpenStreamSync(ctx context.Context) (*quic.Stream, error) {
	if err := c.RequireAuthorized(); err != nil {
		return nil, err
	}
	return c.inner.OpenStreamSync(ctx)
}

func (c *Connection) AcceptStream(ctx context.Context) (*quic.Stream, error) {
	if err := c.RequireAuthorized(); err != nil {
		return nil, err
	}
	return c.inner.AcceptStream(ctx)
}

func (c *Connection) CloseWithError(code quic.ApplicationErrorCode, reason string) error {
	return c.inner.CloseWithError(code, reason)
}

type Listener struct{ inner *quic.Listener }

func (l *Listener) Accept(ctx context.Context) (*Connection, error) {
	inner, err := l.inner.Accept(ctx)
	if err != nil {
		return nil, err
	}
	return newConnection(inner)
}

func (l *Listener) Close() error { return l.inner.Close() }
