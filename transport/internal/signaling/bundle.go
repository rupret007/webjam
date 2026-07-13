// Package signaling seals canonical ICE candidate bundles before they leave a
// peer. The rendezvous service sees only an opaque AEAD envelope. Authentication
// and context checks happen before any remote candidate reaches Pion ICE.
package signaling

import (
	"bytes"
	"crypto/aes"
	"crypto/cipher"
	"crypto/hkdf"
	"crypto/rand"
	"crypto/sha256"
	"encoding/binary"
	"errors"
	"io"
	"sort"
	"sync"
	"time"
	"unicode/utf8"

	"github.com/rupret007/webjam/transport/internal/limits"
)

const (
	bundleVersion = 1
	bundleMagic   = "WJCB"
	envelopeMagic = "WJSE"
	keyDomain     = "webjam/v3/candidate-bundle/aead"
	aadDomain     = "webjam/v3/candidate-bundle/aad"
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
type BundleNonce [16]byte

type Bundle struct {
	SessionID     SessionID
	SenderRole    Role
	HostPin       PeerPin
	GuestPin      PeerPin
	HostPinKnown  bool
	GuestPinKnown bool
	Nonce         BundleNonce
	Generation    uint32
	ExpiresAt     time.Time
	ICEUfrag      string
	ICEPassword   string
	Candidates    []string
}

type Expected struct {
	SessionID     SessionID
	SenderRole    Role
	HostPin       PeerPin
	GuestPin      PeerPin
	HostPinKnown  bool
	GuestPinKnown bool
	Generation    uint32
}

var (
	ErrInvalidBundle  = errors.New("invalid candidate bundle")
	ErrAuthentication = errors.New("candidate bundle authentication failed")
	ErrWrongContext   = errors.New("candidate bundle context mismatch")
	ErrExpired        = errors.New("candidate bundle expired")
	ErrReplay         = errors.New("candidate bundle replayed")
	ErrReplayCapacity = errors.New("candidate replay cache full")
)

func NewBundleNonce() (BundleNonce, error) {
	var nonce BundleNonce
	_, err := io.ReadFull(rand.Reader, nonce[:])
	return nonce, err
}

func Seal(capability Capability, bundle Bundle) ([]byte, error) {
	plaintext, err := marshalCanonical(bundle)
	if err != nil {
		return nil, err
	}
	aead, err := candidateAEAD(capability, bundle.SessionID)
	if err != nil {
		return nil, err
	}
	nonce := make([]byte, aead.NonceSize())
	if _, err = io.ReadFull(rand.Reader, nonce); err != nil {
		return nil, err
	}
	envelope := make([]byte, 0, len(envelopeMagic)+1+len(nonce)+len(plaintext)+aead.Overhead())
	envelope = append(envelope, envelopeMagic...)
	envelope = append(envelope, bundleVersion)
	envelope = append(envelope, nonce...)
	envelope = aead.Seal(envelope, nonce, plaintext, candidateAAD(bundle.SessionID))
	if len(envelope) > limits.MaxSignalEnvelopeBytes {
		return nil, ErrInvalidBundle
	}
	return envelope, nil
}

func Open(
	capability Capability,
	expected Expected,
	envelope []byte,
	now time.Time,
	replays *ReplayCache,
) (Bundle, error) {
	if len(envelope) < len(envelopeMagic)+1+12+16 || len(envelope) > limits.MaxSignalEnvelopeBytes {
		return Bundle{}, ErrInvalidBundle
	}
	if string(envelope[:len(envelopeMagic)]) != envelopeMagic || envelope[len(envelopeMagic)] != bundleVersion {
		return Bundle{}, ErrInvalidBundle
	}
	aead, err := candidateAEAD(capability, expected.SessionID)
	if err != nil {
		return Bundle{}, err
	}
	nonceOffset := len(envelopeMagic) + 1
	nonceEnd := nonceOffset + aead.NonceSize()
	if nonceEnd >= len(envelope) {
		return Bundle{}, ErrInvalidBundle
	}
	plaintext, err := aead.Open(nil, envelope[nonceOffset:nonceEnd], envelope[nonceEnd:], candidateAAD(expected.SessionID))
	if err != nil {
		return Bundle{}, ErrAuthentication
	}
	bundle, err := unmarshalCanonical(plaintext)
	if err != nil {
		return Bundle{}, err
	}
	if err = validateExpected(bundle, expected); err != nil {
		return Bundle{}, err
	}
	if !bundle.ExpiresAt.After(now) || bundle.ExpiresAt.After(now.Add(limits.MaxSignalLifetime)) {
		return Bundle{}, ErrExpired
	}
	if replays == nil {
		return Bundle{}, ErrInvalidBundle
	}
	if err = replays.Accept(bundle.Nonce, bundle.ExpiresAt, now); err != nil {
		return Bundle{}, err
	}
	return bundle, nil
}

func candidateAEAD(capability Capability, session SessionID) (cipher.AEAD, error) {
	if capability == (Capability{}) || session == (SessionID{}) {
		return nil, ErrInvalidBundle
	}
	key, err := hkdf.Key(sha256.New, capability[:], session[:], keyDomain, 32)
	if err != nil {
		return nil, err
	}
	block, err := aes.NewCipher(key)
	if err != nil {
		return nil, err
	}
	return cipher.NewGCM(block)
}

func candidateAAD(session SessionID) []byte {
	aad := make([]byte, 0, len(aadDomain)+len(session))
	aad = append(aad, aadDomain...)
	return append(aad, session[:]...)
}

func marshalCanonical(bundle Bundle) ([]byte, error) {
	if err := validateBundle(bundle); err != nil {
		return nil, err
	}
	candidates := append([]string(nil), bundle.Candidates...)
	sort.Strings(candidates)
	for index := 1; index < len(candidates); index++ {
		if candidates[index] == candidates[index-1] {
			return nil, ErrInvalidBundle
		}
	}
	var encoded bytes.Buffer
	encoded.Grow(256)
	encoded.WriteString(bundleMagic)
	encoded.WriteByte(bundleVersion)
	encoded.Write(bundle.SessionID[:])
	encoded.WriteByte(byte(bundle.SenderRole))
	var flags byte
	if bundle.HostPinKnown {
		flags |= 1
	}
	if bundle.GuestPinKnown {
		flags |= 2
	}
	encoded.WriteByte(flags)
	encoded.Write(bundle.HostPin[:])
	encoded.Write(bundle.GuestPin[:])
	encoded.Write(bundle.Nonce[:])
	writeUint32(&encoded, bundle.Generation)
	writeUint64(&encoded, uint64(bundle.ExpiresAt.Unix()))
	writeString(&encoded, bundle.ICEUfrag)
	writeString(&encoded, bundle.ICEPassword)
	writeUint16(&encoded, uint16(len(candidates)))
	for _, candidate := range candidates {
		writeString(&encoded, candidate)
	}
	if encoded.Len() > limits.MaxSignalEnvelopeBytes/2 {
		return nil, ErrInvalidBundle
	}
	return encoded.Bytes(), nil
}

func unmarshalCanonical(encoded []byte) (Bundle, error) {
	reader := bytes.NewReader(encoded)
	magicBytes := make([]byte, len(bundleMagic))
	if _, err := io.ReadFull(reader, magicBytes); err != nil || string(magicBytes) != bundleMagic {
		return Bundle{}, ErrInvalidBundle
	}
	version, err := reader.ReadByte()
	if err != nil || version != bundleVersion {
		return Bundle{}, ErrInvalidBundle
	}
	var bundle Bundle
	if _, err = io.ReadFull(reader, bundle.SessionID[:]); err != nil {
		return Bundle{}, ErrInvalidBundle
	}
	role, err := reader.ReadByte()
	if err != nil {
		return Bundle{}, ErrInvalidBundle
	}
	bundle.SenderRole = Role(role)
	flags, err := reader.ReadByte()
	if err != nil || flags&^byte(3) != 0 {
		return Bundle{}, ErrInvalidBundle
	}
	bundle.HostPinKnown = flags&1 != 0
	bundle.GuestPinKnown = flags&2 != 0
	if _, err = io.ReadFull(reader, bundle.HostPin[:]); err != nil {
		return Bundle{}, ErrInvalidBundle
	}
	if _, err = io.ReadFull(reader, bundle.GuestPin[:]); err != nil {
		return Bundle{}, ErrInvalidBundle
	}
	if _, err = io.ReadFull(reader, bundle.Nonce[:]); err != nil {
		return Bundle{}, ErrInvalidBundle
	}
	if bundle.Generation, err = readUint32(reader); err != nil {
		return Bundle{}, ErrInvalidBundle
	}
	expires, err := readUint64(reader)
	if err != nil || expires > uint64(^uint64(0)>>1) {
		return Bundle{}, ErrInvalidBundle
	}
	bundle.ExpiresAt = time.Unix(int64(expires), 0).UTC()
	if bundle.ICEUfrag, err = readString(reader, limits.MaxICECredentialBytes); err != nil {
		return Bundle{}, err
	}
	if bundle.ICEPassword, err = readString(reader, limits.MaxICECredentialBytes); err != nil {
		return Bundle{}, err
	}
	count, err := readUint16(reader)
	if err != nil || count > limits.MaxCandidateCount {
		return Bundle{}, ErrInvalidBundle
	}
	bundle.Candidates = make([]string, 0, count)
	for range count {
		candidate, candidateErr := readString(reader, limits.MaxCandidateBytes)
		if candidateErr != nil {
			return Bundle{}, candidateErr
		}
		if len(bundle.Candidates) > 0 && candidate <= bundle.Candidates[len(bundle.Candidates)-1] {
			return Bundle{}, ErrInvalidBundle
		}
		bundle.Candidates = append(bundle.Candidates, candidate)
	}
	if reader.Len() != 0 || validateBundle(bundle) != nil {
		return Bundle{}, ErrInvalidBundle
	}
	return bundle, nil
}

func validateBundle(bundle Bundle) error {
	if bundle.SessionID == (SessionID{}) || bundle.Nonce == (BundleNonce{}) ||
		!bundle.SenderRole.valid() || bundle.Generation == 0 || bundle.ExpiresAt.Unix() <= 0 {
		return ErrInvalidBundle
	}
	if bundle.HostPinKnown && bundle.HostPin == (PeerPin{}) {
		return ErrInvalidBundle
	}
	if bundle.GuestPinKnown && bundle.GuestPin == (PeerPin{}) {
		return ErrInvalidBundle
	}
	if !bundle.HostPinKnown && bundle.HostPin != (PeerPin{}) {
		return ErrInvalidBundle
	}
	if !bundle.GuestPinKnown && bundle.GuestPin != (PeerPin{}) {
		return ErrInvalidBundle
	}
	if len(bundle.ICEUfrag) < 1 || len(bundle.ICEUfrag) > limits.MaxICECredentialBytes || !safeASCII(bundle.ICEUfrag) {
		return ErrInvalidBundle
	}
	if len(bundle.ICEPassword) < 1 || len(bundle.ICEPassword) > limits.MaxICECredentialBytes || !safeASCII(bundle.ICEPassword) {
		return ErrInvalidBundle
	}
	if len(bundle.Candidates) < 1 || len(bundle.Candidates) > limits.MaxCandidateCount {
		return ErrInvalidBundle
	}
	for _, candidate := range bundle.Candidates {
		if len(candidate) < 1 || len(candidate) > limits.MaxCandidateBytes || !safeASCII(candidate) {
			return ErrInvalidBundle
		}
	}
	return nil
}

func validateExpected(bundle Bundle, expected Expected) error {
	if bundle.SessionID != expected.SessionID || bundle.SenderRole != expected.SenderRole || bundle.Generation != expected.Generation {
		return ErrWrongContext
	}
	if bundle.HostPinKnown != expected.HostPinKnown || bundle.GuestPinKnown != expected.GuestPinKnown {
		return ErrWrongContext
	}
	if expected.HostPinKnown && bundle.HostPin != expected.HostPin {
		return ErrWrongContext
	}
	if expected.GuestPinKnown && bundle.GuestPin != expected.GuestPin {
		return ErrWrongContext
	}
	return nil
}

func safeASCII(value string) bool {
	if !utf8.ValidString(value) {
		return false
	}
	for _, character := range value {
		if character < 0x20 || character > 0x7e {
			return false
		}
	}
	return true
}

func writeString(writer *bytes.Buffer, value string) {
	writeUint16(writer, uint16(len(value)))
	writer.WriteString(value)
}

func readString(reader *bytes.Reader, maximum int) (string, error) {
	length, err := readUint16(reader)
	if err != nil || int(length) > maximum || int(length) > reader.Len() {
		return "", ErrInvalidBundle
	}
	payload := make([]byte, length)
	if _, err = io.ReadFull(reader, payload); err != nil {
		return "", ErrInvalidBundle
	}
	value := string(payload)
	if !safeASCII(value) {
		return "", ErrInvalidBundle
	}
	return value, nil
}

func writeUint16(writer *bytes.Buffer, value uint16) {
	var encoded [2]byte
	binary.BigEndian.PutUint16(encoded[:], value)
	writer.Write(encoded[:])
}

func writeUint32(writer *bytes.Buffer, value uint32) {
	var encoded [4]byte
	binary.BigEndian.PutUint32(encoded[:], value)
	writer.Write(encoded[:])
}

func writeUint64(writer *bytes.Buffer, value uint64) {
	var encoded [8]byte
	binary.BigEndian.PutUint64(encoded[:], value)
	writer.Write(encoded[:])
}

func readUint16(reader *bytes.Reader) (uint16, error) {
	var encoded [2]byte
	if _, err := io.ReadFull(reader, encoded[:]); err != nil {
		return 0, err
	}
	return binary.BigEndian.Uint16(encoded[:]), nil
}

func readUint32(reader *bytes.Reader) (uint32, error) {
	var encoded [4]byte
	if _, err := io.ReadFull(reader, encoded[:]); err != nil {
		return 0, err
	}
	return binary.BigEndian.Uint32(encoded[:]), nil
}

func readUint64(reader *bytes.Reader) (uint64, error) {
	var encoded [8]byte
	if _, err := io.ReadFull(reader, encoded[:]); err != nil {
		return 0, err
	}
	return binary.BigEndian.Uint64(encoded[:]), nil
}

type ReplayCache struct {
	mu      sync.Mutex
	expires map[BundleNonce]time.Time
}

func NewReplayCache() *ReplayCache {
	return &ReplayCache{expires: make(map[BundleNonce]time.Time)}
}

func (c *ReplayCache) Accept(nonce BundleNonce, expiresAt, now time.Time) error {
	c.mu.Lock()
	defer c.mu.Unlock()
	for knownNonce, expiry := range c.expires {
		if !expiry.After(now) {
			delete(c.expires, knownNonce)
		}
	}
	if _, exists := c.expires[nonce]; exists {
		return ErrReplay
	}
	if len(c.expires) >= limits.MaxReplayNonces {
		return ErrReplayCapacity
	}
	c.expires[nonce] = expiresAt
	return nil
}
