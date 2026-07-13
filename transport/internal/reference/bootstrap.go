package reference

import (
	"bytes"
	"crypto/aes"
	"crypto/cipher"
	"crypto/ed25519"
	"crypto/hkdf"
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"crypto/x509"
	"encoding/binary"
	"io"
	"sync"
	"time"

	"github.com/rupret007/webjam/transport/internal/limits"
)

const (
	guestPlainMagic    = "WJGB"
	guestEnvelopeMagic = "WJGE"
	ackPlainMagic      = "WJGA"
	ackEnvelopeMagic   = "WJAE"
	bootstrapVersion   = 1
	guestKeyDomain     = "webjam/v3/guest-bootstrap/aead"
	guestAADDomain     = "webjam/v3/guest-bootstrap/aad"
	ackKeyDomain       = "webjam/v3/host-ack/aead"
	ackAADDomain       = "webjam/v3/host-ack/aad"
)

type GuestBootstrap struct {
	SessionID      SessionID
	Generation     uint32
	HostPin        PeerPin
	GuestPin       PeerPin
	Nonce          BootstrapNonce
	ExpiresAt      time.Time
	CertificateDER []byte
}

type GuestBootstrapExpected struct {
	SessionID  SessionID
	Generation uint32
	HostPin    PeerPin
}

type HostAcknowledgment struct {
	SessionID      SessionID
	Generation     uint32
	HostPin        PeerPin
	GuestPin       PeerPin
	GuestNonce     BootstrapNonce
	Acknowledgment BootstrapNonce
	ExpiresAt      time.Time
}

type HostAcknowledgmentExpected struct {
	SessionID  SessionID
	Generation uint32
	HostPin    PeerPin
	GuestPin   PeerPin
	GuestNonce BootstrapNonce
}

type BootstrapReplayCache struct {
	mu      sync.Mutex
	expires map[BootstrapNonce]time.Time
}

func NewBootstrapNonce() (BootstrapNonce, error) {
	var nonce BootstrapNonce
	if _, err := rand.Read(nonce[:]); err != nil || !nonzero(nonce) {
		clear(nonce[:])
		return BootstrapNonce{}, ErrRandom
	}
	return nonce, nil
}

func NewBootstrapReplayCache() *BootstrapReplayCache {
	return &BootstrapReplayCache{expires: make(map[BootstrapNonce]time.Time)}
}

func SealGuestBootstrap(
	capability Capability, bootstrap GuestBootstrap, now time.Time,
) ([]byte, error) {
	if !validGuestBootstrap(bootstrap, now) || !nonzero(capability) {
		return nil, ErrBootstrap
	}
	plaintext := marshalGuestBootstrap(bootstrap)
	return sealBootstrap(
		guestEnvelopeMagic, guestKeyDomain, guestAADDomain,
		capability, bootstrap.SessionID, plaintext,
	)
}

func OpenGuestBootstrap(
	capability Capability,
	expected GuestBootstrapExpected,
	envelope []byte,
	now time.Time,
	replays *BootstrapReplayCache,
) (GuestBootstrap, error) {
	if !nonzero(capability) || !validGuestExpected(expected) || replays == nil {
		return GuestBootstrap{}, ErrBootstrap
	}
	plaintext, err := openBootstrap(
		guestEnvelopeMagic, guestKeyDomain, guestAADDomain,
		capability, expected.SessionID, envelope,
	)
	if err != nil {
		return GuestBootstrap{}, err
	}
	bootstrap, err := unmarshalGuestBootstrap(plaintext)
	if err != nil {
		return GuestBootstrap{}, err
	}
	if bootstrap.SessionID != expected.SessionID || bootstrap.Generation != expected.Generation ||
		bootstrap.HostPin != expected.HostPin {
		return GuestBootstrap{}, ErrBootstrapContext
	}
	if !validExpiry(bootstrap.ExpiresAt, now) {
		return GuestBootstrap{}, ErrBootstrapExpired
	}
	if !validGuestBootstrap(bootstrap, now) {
		return GuestBootstrap{}, ErrBootstrap
	}
	if err = replays.accept(bootstrap.Nonce, bootstrap.ExpiresAt, now); err != nil {
		return GuestBootstrap{}, err
	}
	return bootstrap, nil
}

func SealHostAcknowledgment(
	capability Capability, acknowledgment HostAcknowledgment, now time.Time,
) ([]byte, error) {
	if !nonzero(capability) || !validAcknowledgment(acknowledgment, now) {
		return nil, ErrBootstrap
	}
	return sealBootstrap(
		ackEnvelopeMagic, ackKeyDomain, ackAADDomain,
		capability, acknowledgment.SessionID, marshalAcknowledgment(acknowledgment),
	)
}

func OpenHostAcknowledgment(
	capability Capability,
	expected HostAcknowledgmentExpected,
	envelope []byte,
	now time.Time,
	replays *BootstrapReplayCache,
) (HostAcknowledgment, error) {
	if !nonzero(capability) || !validAcknowledgmentExpected(expected) || replays == nil {
		return HostAcknowledgment{}, ErrBootstrap
	}
	plaintext, err := openBootstrap(
		ackEnvelopeMagic, ackKeyDomain, ackAADDomain,
		capability, expected.SessionID, envelope,
	)
	if err != nil {
		return HostAcknowledgment{}, err
	}
	acknowledgment, err := unmarshalAcknowledgment(plaintext)
	if err != nil {
		return HostAcknowledgment{}, err
	}
	if acknowledgment.SessionID != expected.SessionID ||
		acknowledgment.Generation != expected.Generation ||
		acknowledgment.HostPin != expected.HostPin ||
		acknowledgment.GuestPin != expected.GuestPin ||
		acknowledgment.GuestNonce != expected.GuestNonce {
		return HostAcknowledgment{}, ErrBootstrapContext
	}
	if !validExpiry(acknowledgment.ExpiresAt, now) {
		return HostAcknowledgment{}, ErrBootstrapExpired
	}
	if !validAcknowledgment(acknowledgment, now) {
		return HostAcknowledgment{}, ErrBootstrap
	}
	if err = replays.accept(acknowledgment.Acknowledgment, acknowledgment.ExpiresAt, now); err != nil {
		return HostAcknowledgment{}, err
	}
	return acknowledgment, nil
}

func (c *BootstrapReplayCache) accept(
	nonce BootstrapNonce, expiresAt, now time.Time,
) error {
	if c == nil || !nonzero(nonce) {
		return ErrBootstrap
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	for known, expiry := range c.expires {
		if !expiry.After(now) {
			delete(c.expires, known)
		}
	}
	if _, exists := c.expires[nonce]; exists {
		return ErrBootstrapReplay
	}
	if len(c.expires) >= limits.MaxReplayNonces {
		return ErrBootstrapReplay
	}
	c.expires[nonce] = expiresAt
	return nil
}

func sealBootstrap(
	envelopeMagic, keyDomain, aadDomain string,
	capability Capability,
	session SessionID,
	plaintext []byte,
) ([]byte, error) {
	aead, err := bootstrapAEAD(capability, session, keyDomain)
	if err != nil {
		return nil, err
	}
	nonce := make([]byte, aead.NonceSize())
	if _, err = io.ReadFull(rand.Reader, nonce); err != nil {
		return nil, ErrRandom
	}
	envelope := make([]byte, 0, len(envelopeMagic)+1+len(nonce)+len(plaintext)+aead.Overhead())
	envelope = append(envelope, envelopeMagic...)
	envelope = append(envelope, bootstrapVersion)
	envelope = append(envelope, nonce...)
	envelope = aead.Seal(envelope, nonce, plaintext, bootstrapAAD(aadDomain, session))
	if len(envelope) < 16 || len(envelope) > MaxSignalPayloadBytes {
		clear(envelope)
		return nil, ErrBootstrap
	}
	return envelope, nil
}

func openBootstrap(
	envelopeMagic, keyDomain, aadDomain string,
	capability Capability,
	session SessionID,
	envelope []byte,
) ([]byte, error) {
	if len(envelope) < len(envelopeMagic)+1+12+16 || len(envelope) > MaxSignalPayloadBytes ||
		string(envelope[:len(envelopeMagic)]) != envelopeMagic ||
		envelope[len(envelopeMagic)] != bootstrapVersion {
		return nil, ErrBootstrap
	}
	aead, err := bootstrapAEAD(capability, session, keyDomain)
	if err != nil {
		return nil, err
	}
	nonceStart := len(envelopeMagic) + 1
	nonceEnd := nonceStart + aead.NonceSize()
	if nonceEnd >= len(envelope) {
		return nil, ErrBootstrap
	}
	plaintext, err := aead.Open(
		nil, envelope[nonceStart:nonceEnd], envelope[nonceEnd:], bootstrapAAD(aadDomain, session),
	)
	if err != nil {
		return nil, ErrBootstrap
	}
	return plaintext, nil
}

func bootstrapAEAD(capability Capability, session SessionID, domain string) (cipher.AEAD, error) {
	if !nonzero(capability) || !nonzero(session) {
		return nil, ErrBootstrap
	}
	key, err := hkdf.Key(sha256.New, capability[:], session[:], domain, 32)
	if err != nil {
		return nil, ErrBootstrap
	}
	defer clear(key)
	block, err := aes.NewCipher(key)
	if err != nil {
		return nil, ErrBootstrap
	}
	return cipher.NewGCM(block)
}

func bootstrapAAD(domain string, session SessionID) []byte {
	aad := make([]byte, 0, len(domain)+len(session))
	aad = append(aad, domain...)
	return append(aad, session[:]...)
}

func marshalGuestBootstrap(bootstrap GuestBootstrap) []byte {
	encoded := make([]byte, 0, 131+len(bootstrap.CertificateDER))
	encoded = append(encoded, guestPlainMagic...)
	encoded = append(encoded, bootstrapVersion)
	encoded = append(encoded, bootstrap.SessionID[:]...)
	var integer [8]byte
	binary.BigEndian.PutUint32(integer[:4], bootstrap.Generation)
	encoded = append(encoded, integer[:4]...)
	encoded = append(encoded, bootstrap.HostPin[:]...)
	encoded = append(encoded, bootstrap.GuestPin[:]...)
	encoded = append(encoded, bootstrap.Nonce[:]...)
	binary.BigEndian.PutUint64(integer[:], uint64(bootstrap.ExpiresAt.Unix()))
	encoded = append(encoded, integer[:]...)
	binary.BigEndian.PutUint16(integer[:2], uint16(len(bootstrap.CertificateDER)))
	encoded = append(encoded, integer[:2]...)
	return append(encoded, bootstrap.CertificateDER...)
}

func unmarshalGuestBootstrap(encoded []byte) (GuestBootstrap, error) {
	reader := bytes.NewReader(encoded)
	if !readMagicVersion(reader, guestPlainMagic) {
		return GuestBootstrap{}, ErrBootstrap
	}
	var bootstrap GuestBootstrap
	if _, err := io.ReadFull(reader, bootstrap.SessionID[:]); err != nil {
		return GuestBootstrap{}, ErrBootstrap
	}
	var integer [8]byte
	if _, err := io.ReadFull(reader, integer[:4]); err != nil {
		return GuestBootstrap{}, ErrBootstrap
	}
	bootstrap.Generation = binary.BigEndian.Uint32(integer[:4])
	if _, err := io.ReadFull(reader, bootstrap.HostPin[:]); err != nil {
		return GuestBootstrap{}, ErrBootstrap
	}
	if _, err := io.ReadFull(reader, bootstrap.GuestPin[:]); err != nil {
		return GuestBootstrap{}, ErrBootstrap
	}
	if _, err := io.ReadFull(reader, bootstrap.Nonce[:]); err != nil {
		return GuestBootstrap{}, ErrBootstrap
	}
	if _, err := io.ReadFull(reader, integer[:]); err != nil {
		return GuestBootstrap{}, ErrBootstrap
	}
	expires := binary.BigEndian.Uint64(integer[:])
	if expires > uint64(^uint64(0)>>1) {
		return GuestBootstrap{}, ErrBootstrap
	}
	bootstrap.ExpiresAt = time.Unix(int64(expires), 0).UTC()
	if _, err := io.ReadFull(reader, integer[:2]); err != nil {
		return GuestBootstrap{}, ErrBootstrap
	}
	certificateBytes := int(binary.BigEndian.Uint16(integer[:2]))
	if certificateBytes < 1 || certificateBytes > limits.MaxCertificateBytes || certificateBytes != reader.Len() {
		return GuestBootstrap{}, ErrBootstrap
	}
	bootstrap.CertificateDER = make([]byte, certificateBytes)
	if _, err := io.ReadFull(reader, bootstrap.CertificateDER); err != nil || reader.Len() != 0 {
		return GuestBootstrap{}, ErrBootstrap
	}
	return bootstrap, nil
}

func marshalAcknowledgment(acknowledgment HostAcknowledgment) []byte {
	encoded := make([]byte, 0, 145)
	encoded = append(encoded, ackPlainMagic...)
	encoded = append(encoded, bootstrapVersion)
	encoded = append(encoded, acknowledgment.SessionID[:]...)
	var integer [8]byte
	binary.BigEndian.PutUint32(integer[:4], acknowledgment.Generation)
	encoded = append(encoded, integer[:4]...)
	encoded = append(encoded, acknowledgment.HostPin[:]...)
	encoded = append(encoded, acknowledgment.GuestPin[:]...)
	encoded = append(encoded, acknowledgment.GuestNonce[:]...)
	encoded = append(encoded, acknowledgment.Acknowledgment[:]...)
	binary.BigEndian.PutUint64(integer[:], uint64(acknowledgment.ExpiresAt.Unix()))
	return append(encoded, integer[:]...)
}

func unmarshalAcknowledgment(encoded []byte) (HostAcknowledgment, error) {
	reader := bytes.NewReader(encoded)
	if !readMagicVersion(reader, ackPlainMagic) {
		return HostAcknowledgment{}, ErrBootstrap
	}
	var acknowledgment HostAcknowledgment
	if _, err := io.ReadFull(reader, acknowledgment.SessionID[:]); err != nil {
		return HostAcknowledgment{}, ErrBootstrap
	}
	var integer [8]byte
	if _, err := io.ReadFull(reader, integer[:4]); err != nil {
		return HostAcknowledgment{}, ErrBootstrap
	}
	acknowledgment.Generation = binary.BigEndian.Uint32(integer[:4])
	if _, err := io.ReadFull(reader, acknowledgment.HostPin[:]); err != nil {
		return HostAcknowledgment{}, ErrBootstrap
	}
	if _, err := io.ReadFull(reader, acknowledgment.GuestPin[:]); err != nil {
		return HostAcknowledgment{}, ErrBootstrap
	}
	if _, err := io.ReadFull(reader, acknowledgment.GuestNonce[:]); err != nil {
		return HostAcknowledgment{}, ErrBootstrap
	}
	if _, err := io.ReadFull(reader, acknowledgment.Acknowledgment[:]); err != nil {
		return HostAcknowledgment{}, ErrBootstrap
	}
	if _, err := io.ReadFull(reader, integer[:]); err != nil || reader.Len() != 0 {
		return HostAcknowledgment{}, ErrBootstrap
	}
	expires := binary.BigEndian.Uint64(integer[:])
	if expires > uint64(^uint64(0)>>1) {
		return HostAcknowledgment{}, ErrBootstrap
	}
	acknowledgment.ExpiresAt = time.Unix(int64(expires), 0).UTC()
	return acknowledgment, nil
}

func readMagicVersion(reader *bytes.Reader, magic string) bool {
	prefix := make([]byte, len(magic)+1)
	if _, err := io.ReadFull(reader, prefix); err != nil {
		return false
	}
	return string(prefix[:len(magic)]) == magic && prefix[len(magic)] == bootstrapVersion
}

func validGuestExpected(expected GuestBootstrapExpected) bool {
	return nonzero(expected.SessionID) && expected.Generation != 0 && nonzero(expected.HostPin)
}

func validGuestBootstrap(bootstrap GuestBootstrap, now time.Time) bool {
	if !validGuestExpected(GuestBootstrapExpected{
		SessionID: bootstrap.SessionID, Generation: bootstrap.Generation, HostPin: bootstrap.HostPin,
	}) || !nonzero(bootstrap.GuestPin) || !nonzero(bootstrap.Nonce) ||
		!validExpiry(bootstrap.ExpiresAt, now) ||
		len(bootstrap.CertificateDER) < 1 || len(bootstrap.CertificateDER) > limits.MaxCertificateBytes {
		return false
	}
	return validGuestCertificate(bootstrap.CertificateDER, bootstrap.GuestPin, now)
}

func validAcknowledgmentExpected(expected HostAcknowledgmentExpected) bool {
	return nonzero(expected.SessionID) && expected.Generation != 0 && nonzero(expected.HostPin) &&
		nonzero(expected.GuestPin) && nonzero(expected.GuestNonce)
}

func validAcknowledgment(acknowledgment HostAcknowledgment, now time.Time) bool {
	return validAcknowledgmentExpected(HostAcknowledgmentExpected{
		SessionID: acknowledgment.SessionID, Generation: acknowledgment.Generation,
		HostPin: acknowledgment.HostPin, GuestPin: acknowledgment.GuestPin,
		GuestNonce: acknowledgment.GuestNonce,
	}) && nonzero(acknowledgment.Acknowledgment) &&
		acknowledgment.Acknowledgment != acknowledgment.GuestNonce &&
		validExpiry(acknowledgment.ExpiresAt, now)
}

func validExpiry(expiresAt, now time.Time) bool {
	return expiresAt.Unix() > 0 && expiresAt.After(now) &&
		!expiresAt.After(now.Add(limits.MaxEnrollmentLifetime))
}

func validGuestCertificate(der []byte, expected PeerPin, now time.Time) bool {
	certificate, err := x509.ParseCertificate(der)
	if err != nil || certificate.SerialNumber == nil || certificate.SerialNumber.Sign() <= 0 ||
		certificate.PublicKeyAlgorithm != x509.Ed25519 || !certificate.BasicConstraintsValid ||
		certificate.IsCA || now.Before(certificate.NotBefore) || now.After(certificate.NotAfter) ||
		certificate.NotAfter.Sub(certificate.NotBefore) < limits.MinIdentityLifetime ||
		certificate.NotAfter.Sub(certificate.NotBefore) > limits.MaxIdentityLifetime ||
		certificate.KeyUsage&x509.KeyUsageDigitalSignature == 0 ||
		!allowsClientAuth(certificate.ExtKeyUsage) {
		return false
	}
	if subtle.ConstantTimeCompare(certificate.RawIssuer, certificate.RawSubject) != 1 {
		return false
	}
	if err = certificate.CheckSignature(
		certificate.SignatureAlgorithm, certificate.RawTBSCertificate, certificate.Signature,
	); err != nil {
		return false
	}
	publicKey, ok := certificate.PublicKey.(ed25519.PublicKey)
	if !ok || len(publicKey) != ed25519.PublicKeySize {
		return false
	}
	fingerprint := sha256.Sum256(certificate.RawSubjectPublicKeyInfo)
	return subtle.ConstantTimeCompare(fingerprint[:], expected[:]) == 1
}

func allowsClientAuth(usages []x509.ExtKeyUsage) bool {
	for _, usage := range usages {
		if usage == x509.ExtKeyUsageClientAuth {
			return true
		}
	}
	return false
}
