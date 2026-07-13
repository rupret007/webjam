package reference

import (
	"bytes"
	"errors"
	"testing"
	"time"

	"github.com/rupret007/webjam/transport/internal/icequic"
	"github.com/rupret007/webjam/transport/internal/limits"
)

func TestGuestBootstrapConveysAndAuthenticatesEphemeralGuestSPKI(t *testing.T) {
	t.Parallel()
	now := time.Now().UTC().Truncate(time.Second)
	identity, err := icequic.NewEphemeralIdentity(now, time.Hour)
	if err != nil {
		t.Fatal(err)
	}
	defer identity.Destroy()
	guestNonce, err := NewBootstrapNonce()
	if err != nil {
		t.Fatal(err)
	}
	session := filledSession(1)
	capability := filledCapability(2)
	hostPin := filledPin(3)
	guestPin := PeerPin(identity.SPKIFingerprint)
	bootstrap := GuestBootstrap{
		SessionID: session, Generation: 4, HostPin: hostPin, GuestPin: guestPin,
		Nonce: guestNonce, ExpiresAt: now.Add(time.Minute),
		CertificateDER: append([]byte(nil), identity.Certificate.Certificate[0]...),
	}
	envelope, err := SealGuestBootstrap(capability, bootstrap, now)
	if err != nil {
		t.Fatal(err)
	}
	if bytes.Contains(envelope, bootstrap.CertificateDER) || bytes.Contains(envelope, guestPin[:]) {
		t.Fatal("sealed guest bootstrap exposed certificate or pin plaintext")
	}
	expected := GuestBootstrapExpected{SessionID: session, Generation: 4, HostPin: hostPin}
	replays := NewBootstrapReplayCache()
	opened, err := OpenGuestBootstrap(capability, expected, envelope, now, replays)
	if err != nil {
		t.Fatal(err)
	}
	if opened.GuestPin != guestPin || !bytes.Equal(opened.CertificateDER, bootstrap.CertificateDER) {
		t.Fatal("opened guest identity did not match")
	}
	if _, err = OpenGuestBootstrap(capability, expected, envelope, now, replays); !errors.Is(err, ErrBootstrapReplay) {
		t.Fatalf("replay error = %v", err)
	}
}

func TestGuestBootstrapRejectsWrongContextTamperingExpiryAndPin(t *testing.T) {
	t.Parallel()
	now := time.Now().UTC().Truncate(time.Second)
	identity, err := icequic.NewEphemeralIdentity(now, time.Hour)
	if err != nil {
		t.Fatal(err)
	}
	defer identity.Destroy()
	nonce, _ := NewBootstrapNonce()
	session := filledSession(1)
	capability := filledCapability(2)
	hostPin := filledPin(3)
	guestPin := PeerPin(identity.SPKIFingerprint)
	bootstrap := GuestBootstrap{
		SessionID: session, Generation: 1, HostPin: hostPin, GuestPin: guestPin,
		Nonce: nonce, ExpiresAt: now.Add(time.Minute),
		CertificateDER: append([]byte(nil), identity.Certificate.Certificate[0]...),
	}
	envelope, err := SealGuestBootstrap(capability, bootstrap, now)
	if err != nil {
		t.Fatal(err)
	}
	wrongHost := GuestBootstrapExpected{SessionID: session, Generation: 1, HostPin: filledPin(9)}
	if _, err = OpenGuestBootstrap(
		capability, wrongHost, envelope, now, NewBootstrapReplayCache(),
	); !errors.Is(err, ErrBootstrapContext) {
		t.Fatalf("wrong host error = %v", err)
	}
	wrongSession := GuestBootstrapExpected{
		SessionID: filledSession(9), Generation: 1, HostPin: hostPin,
	}
	if _, err = OpenGuestBootstrap(
		capability, wrongSession, envelope, now, NewBootstrapReplayCache(),
	); !errors.Is(err, ErrBootstrap) {
		t.Fatalf("wrong session error = %v", err)
	}
	tampered := append([]byte(nil), envelope...)
	tampered[len(tampered)-1] ^= 1
	if _, err = OpenGuestBootstrap(
		capability, GuestBootstrapExpected{session, 1, hostPin}, tampered, now,
		NewBootstrapReplayCache(),
	); !errors.Is(err, ErrBootstrap) {
		t.Fatalf("tamper error = %v", err)
	}
	if _, err = OpenGuestBootstrap(
		capability, GuestBootstrapExpected{session, 1, hostPin}, envelope,
		now.Add(2*time.Minute), NewBootstrapReplayCache(),
	); !errors.Is(err, ErrBootstrapExpired) {
		t.Fatalf("expiry error = %v", err)
	}
	bootstrap.GuestPin = filledPin(8)
	if _, err = SealGuestBootstrap(capability, bootstrap, now); !errors.Is(err, ErrBootstrap) {
		t.Fatalf("certificate pin mismatch error = %v", err)
	}
	bootstrap.GuestPin = guestPin
	bootstrap.CertificateDER = bytes.Repeat([]byte{1}, limits.MaxCertificateBytes+1)
	if _, err = SealGuestBootstrap(capability, bootstrap, now); !errors.Is(err, ErrBootstrap) {
		t.Fatalf("oversized certificate error = %v", err)
	}
	bootstrap.CertificateDER = []byte{1, 2, 3}
	malformed, err := sealBootstrap(
		guestEnvelopeMagic, guestKeyDomain, guestAADDomain,
		capability, session, marshalGuestBootstrap(bootstrap),
	)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = OpenGuestBootstrap(
		capability, GuestBootstrapExpected{session, 1, hostPin}, malformed, now,
		NewBootstrapReplayCache(),
	); !errors.Is(err, ErrBootstrap) || errors.Is(err, ErrBootstrapExpired) {
		t.Fatalf("malformed certificate error = %v", err)
	}
}

func TestHostAcknowledgmentBindsGuestBootstrapAndBothPins(t *testing.T) {
	t.Parallel()
	now := time.Now().UTC().Truncate(time.Second)
	guestNonce, _ := NewBootstrapNonce()
	ackNonce, _ := NewBootstrapNonce()
	acknowledgment := HostAcknowledgment{
		SessionID: filledSession(1), Generation: 5,
		HostPin: filledPin(2), GuestPin: filledPin(3),
		GuestNonce: guestNonce, Acknowledgment: ackNonce, ExpiresAt: now.Add(time.Minute),
	}
	capability := filledCapability(4)
	envelope, err := SealHostAcknowledgment(capability, acknowledgment, now)
	if err != nil {
		t.Fatal(err)
	}
	expected := HostAcknowledgmentExpected{
		SessionID: acknowledgment.SessionID, Generation: acknowledgment.Generation,
		HostPin: acknowledgment.HostPin, GuestPin: acknowledgment.GuestPin,
		GuestNonce: guestNonce,
	}
	replays := NewBootstrapReplayCache()
	opened, err := OpenHostAcknowledgment(capability, expected, envelope, now, replays)
	if err != nil {
		t.Fatal(err)
	}
	if opened != acknowledgment {
		t.Fatal("host acknowledgment changed during sealing")
	}
	if _, err = OpenHostAcknowledgment(capability, expected, envelope, now, replays); !errors.Is(err, ErrBootstrapReplay) {
		t.Fatalf("ack replay error = %v", err)
	}
	expected.GuestNonce[0] ^= 1
	if _, err = OpenHostAcknowledgment(
		capability, expected, envelope, now, NewBootstrapReplayCache(),
	); !errors.Is(err, ErrBootstrapContext) {
		t.Fatalf("wrong guest nonce error = %v", err)
	}
}

func TestBootstrapRejectsZeroValuesAndExcessLifetime(t *testing.T) {
	t.Parallel()
	now := time.Now().UTC().Truncate(time.Second)
	if _, err := SealGuestBootstrap(Capability{}, GuestBootstrap{}, now); !errors.Is(err, ErrBootstrap) {
		t.Fatalf("zero guest bootstrap error = %v", err)
	}
	guestNonce, _ := NewBootstrapNonce()
	ackNonce, _ := NewBootstrapNonce()
	ack := HostAcknowledgment{
		SessionID: filledSession(1), Generation: 1, HostPin: filledPin(2), GuestPin: filledPin(3),
		GuestNonce: guestNonce, Acknowledgment: ackNonce,
		ExpiresAt: now.Add(limits.MaxEnrollmentLifetime + time.Second),
	}
	if _, err := SealHostAcknowledgment(filledCapability(4), ack, now); !errors.Is(err, ErrBootstrap) {
		t.Fatalf("excess lifetime error = %v", err)
	}
}
