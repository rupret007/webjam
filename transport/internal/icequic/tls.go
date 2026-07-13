package icequic

import (
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/hex"
	"errors"
	"math/big"
	"net"
	"time"

	"github.com/quic-go/quic-go"
	"github.com/rupret007/webjam/transport/internal/limits"
)

const ALPN = "webjam/3"

var (
	ErrIdentityLifetime = errors.New("identity lifetime outside bounds")
	ErrInvalidIdentity  = errors.New("invalid local identity")
	ErrInvalidPin       = errors.New("invalid certificate pin")
	ErrPinMismatch      = errors.New("certificate pin mismatch")
	ErrPeerCertificate  = errors.New("invalid peer certificate")
	ErrQUICListen       = errors.New("QUIC listener failed")
	ErrQUICHandshake    = errors.New("QUIC handshake failed")
)

type Identity struct {
	Certificate     tls.Certificate
	SPKIFingerprint [sha256.Size]byte
}

func NewEphemeralIdentity(now time.Time, lifetime time.Duration) (Identity, error) {
	if lifetime < limits.MinIdentityLifetime || lifetime > limits.MaxIdentityLifetime {
		return Identity{}, ErrIdentityLifetime
	}
	publicKey, privateKey, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		return Identity{}, err
	}
	serial, err := rand.Int(rand.Reader, new(big.Int).Lsh(big.NewInt(1), 120))
	if err != nil {
		return Identity{}, err
	}
	serial.Add(serial, big.NewInt(1))
	validFrom := now.Add(-time.Minute)
	template := &x509.Certificate{
		SerialNumber:          serial,
		Subject:               pkix.Name{CommonName: "WebJam ephemeral session"},
		NotBefore:             validFrom,
		NotAfter:              validFrom.Add(lifetime),
		KeyUsage:              x509.KeyUsageDigitalSignature,
		ExtKeyUsage:           []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth, x509.ExtKeyUsageClientAuth},
		BasicConstraintsValid: true,
		DNSNames:              []string{"session.webjam.invalid"},
	}
	der, err := x509.CreateCertificate(rand.Reader, template, template, publicKey, privateKey)
	if err != nil {
		return Identity{}, err
	}
	leaf, err := x509.ParseCertificate(der)
	if err != nil {
		return Identity{}, err
	}
	return Identity{
		Certificate: tls.Certificate{
			Certificate: [][]byte{der},
			PrivateKey:  privateKey,
			Leaf:        leaf,
		},
		SPKIFingerprint: sha256.Sum256(leaf.RawSubjectPublicKeyInfo),
	}, nil
}

func (i Identity) FingerprintHex() string { return hex.EncodeToString(i.SPKIFingerprint[:]) }

// Destroy removes the retained private-key material from this identity on a
// best-effort basis. Callers must ensure no live TLS config still uses it.
func (i *Identity) Destroy() {
	if i == nil {
		return
	}
	if privateKey, ok := i.Certificate.PrivateKey.(ed25519.PrivateKey); ok {
		clear(privateKey)
	}
	i.Certificate = tls.Certificate{}
	clear(i.SPKIFingerprint[:])
}

func ServerTLSConfig(identity Identity) *tls.Config {
	return &tls.Config{
		MinVersion:   tls.VersionTLS13,
		MaxVersion:   tls.VersionTLS13,
		Certificates: []tls.Certificate{identity.Certificate},
		NextProtos:   []string{ALPN},
		ClientAuth:   tls.RequireAnyClientCert,
		VerifyPeerCertificate: func(rawCerts [][]byte, _ [][]*x509.Certificate) error {
			_, err := validateEphemeralCertificate(rawCerts, time.Now(), x509.ExtKeyUsageClientAuth)
			return err
		},
	}
}

func ClientTLSConfig(identity Identity, pinHex string, now func() time.Time) (*tls.Config, error) {
	pinBytes, err := hex.DecodeString(pinHex)
	if err != nil || len(pinBytes) != sha256.Size {
		return nil, ErrInvalidPin
	}
	if now == nil {
		now = time.Now
	}
	if err = validateLocalIdentity(identity, now(), x509.ExtKeyUsageClientAuth); err != nil {
		return nil, err
	}
	pin := append([]byte(nil), pinBytes...)
	return &tls.Config{
		MinVersion:         tls.VersionTLS13,
		MaxVersion:         tls.VersionTLS13,
		ServerName:         "session.webjam.invalid",
		NextProtos:         []string{ALPN},
		Certificates:       []tls.Certificate{identity.Certificate},
		InsecureSkipVerify: true, // Exact session pin verification is implemented below.
		VerifyPeerCertificate: func(rawCerts [][]byte, _ [][]*x509.Certificate) error {
			certificate, validationErr := validateEphemeralCertificate(rawCerts, now(), x509.ExtKeyUsageServerAuth)
			if validationErr != nil {
				return validationErr
			}
			fingerprint := sha256.Sum256(certificate.RawSubjectPublicKeyInfo)
			if subtle.ConstantTimeCompare(fingerprint[:], pin) != 1 {
				return ErrPinMismatch
			}
			return nil
		},
	}, nil
}

func validateLocalIdentity(identity Identity, now time.Time, usage x509.ExtKeyUsage) error {
	certificate, err := validateEphemeralCertificate(identity.Certificate.Certificate, now, usage)
	if err != nil || identity.Certificate.PrivateKey == nil {
		return ErrInvalidIdentity
	}
	fingerprint := sha256.Sum256(certificate.RawSubjectPublicKeyInfo)
	if subtle.ConstantTimeCompare(fingerprint[:], identity.SPKIFingerprint[:]) != 1 {
		return ErrInvalidIdentity
	}
	privateKey, ok := identity.Certificate.PrivateKey.(ed25519.PrivateKey)
	if !ok {
		return ErrInvalidIdentity
	}
	publicKey, ok := certificate.PublicKey.(ed25519.PublicKey)
	if !ok || subtle.ConstantTimeCompare(privateKey.Public().(ed25519.PublicKey), publicKey) != 1 {
		return ErrInvalidIdentity
	}
	return nil
}

func validateEphemeralCertificate(
	rawCerts [][]byte,
	now time.Time,
	usage x509.ExtKeyUsage,
) (*x509.Certificate, error) {
	if len(rawCerts) != 1 || len(rawCerts[0]) == 0 || len(rawCerts[0]) > limits.MaxCertificateBytes {
		return nil, ErrPeerCertificate
	}
	certificate, err := x509.ParseCertificate(rawCerts[0])
	if err != nil || certificate.SerialNumber == nil || certificate.SerialNumber.Sign() <= 0 ||
		certificate.PublicKeyAlgorithm != x509.Ed25519 || !certificate.BasicConstraintsValid || certificate.IsCA {
		return nil, ErrPeerCertificate
	}
	if subtle.ConstantTimeCompare(certificate.RawIssuer, certificate.RawSubject) != 1 {
		return nil, ErrPeerCertificate
	}
	if now.Before(certificate.NotBefore) || now.After(certificate.NotAfter) {
		return nil, ErrPeerCertificate
	}
	lifetime := certificate.NotAfter.Sub(certificate.NotBefore)
	if lifetime < limits.MinIdentityLifetime || lifetime > limits.MaxIdentityLifetime {
		return nil, ErrPeerCertificate
	}
	if err = certificate.CheckSignature(
		certificate.SignatureAlgorithm,
		certificate.RawTBSCertificate,
		certificate.Signature,
	); err != nil {
		return nil, ErrPeerCertificate
	}
	if certificate.KeyUsage&x509.KeyUsageDigitalSignature == 0 || !allowsUsage(certificate.ExtKeyUsage, usage) {
		return nil, ErrPeerCertificate
	}
	return certificate, nil
}

func allowsUsage(usages []x509.ExtKeyUsage, required x509.ExtKeyUsage) bool {
	for _, usage := range usages {
		if usage == required {
			return true
		}
	}
	return false
}

func QUICConfig() *quic.Config {
	return &quic.Config{
		EnableDatagrams: true,
		Allow0RTT:       false,
		// The native exact-pair relay admits at most 1,350 inner bytes. Fixing
		// QUIC at its RFC minimum avoids PMTU probes or application packets
		// that the relay would have to reject after authentication.
		InitialPacketSize:       1200,
		DisablePathMTUDiscovery: true,
		HandshakeIdleTimeout:    limits.HandshakeLimit,
		MaxIdleTimeout:          30 * time.Second,
		KeepAlivePeriod:         2 * time.Second,
		MaxIncomingStreams:      limits.MaxConcurrentStreams,
		MaxIncomingUniStreams:   -1,
	}
}

func Listen(packetConn net.PacketConn, identity Identity) (*Listener, error) {
	if packetConn == nil {
		return nil, errors.New("nil QUIC PacketConn")
	}
	if err := validateLocalIdentity(identity, time.Now(), x509.ExtKeyUsageServerAuth); err != nil {
		return nil, ErrQUICListen
	}
	inner, err := quic.Listen(packetConn, ServerTLSConfig(identity), QUICConfig())
	if err != nil {
		return nil, ErrQUICListen
	}
	return &Listener{inner: inner}, nil
}

func Dial(
	ctx context.Context,
	packetConn net.PacketConn,
	peer net.Addr,
	identity Identity,
	pinHex string,
) (*Connection, error) {
	if packetConn == nil || peer == nil {
		return nil, errors.New("nil QUIC dial endpoint")
	}
	tlsConfig, err := ClientTLSConfig(identity, pinHex, time.Now)
	if err != nil {
		return nil, err
	}
	handshakeCtx, cancel := context.WithTimeout(ctx, limits.HandshakeLimit)
	defer cancel()
	connection, err := quic.Dial(handshakeCtx, packetConn, peer, tlsConfig, QUICConfig())
	if err != nil {
		return nil, ErrQUICHandshake
	}
	return newConnection(connection)
}
