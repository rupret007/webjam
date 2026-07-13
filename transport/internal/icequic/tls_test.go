package icequic

import (
	"crypto/ed25519"
	"crypto/rand"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"errors"
	"math/big"
	"net"
	"testing"
	"time"

	"github.com/rupret007/webjam/transport/internal/limits"
)

func TestExactCertificatePinAndLifetime(t *testing.T) {
	t.Parallel()
	now := time.Date(2026, 7, 13, 12, 0, 0, 0, time.UTC)
	identity, err := NewEphemeralIdentity(now, time.Hour)
	if err != nil {
		t.Fatal(err)
	}
	config, err := ClientTLSConfig(identity, identity.FingerprintHex(), func() time.Time { return now })
	if err != nil {
		t.Fatal(err)
	}
	if err = config.VerifyPeerCertificate(identity.Certificate.Certificate, nil); err != nil {
		t.Fatalf("valid pin: %v", err)
	}
	wrongPin := sha256.Sum256([]byte("wrong session"))
	wrongConfig, err := ClientTLSConfig(identity, stringHex(wrongPin[:]), func() time.Time { return now })
	if err != nil {
		t.Fatal(err)
	}
	if err = wrongConfig.VerifyPeerCertificate(identity.Certificate.Certificate, nil); !errors.Is(err, ErrPinMismatch) {
		t.Fatalf("wrong pin error = %v", err)
	}
	currentTime := now
	expiredConfig, err := ClientTLSConfig(identity, identity.FingerprintHex(), func() time.Time { return currentTime })
	if err != nil {
		t.Fatal(err)
	}
	currentTime = now.Add(2 * time.Hour)
	if err = expiredConfig.VerifyPeerCertificate(identity.Certificate.Certificate, nil); !errors.Is(err, ErrPeerCertificate) {
		t.Fatalf("expired error = %v", err)
	}
	if got := identity.Certificate.Leaf.NotAfter.Sub(identity.Certificate.Leaf.NotBefore); got != time.Hour {
		t.Fatalf("certificate lifetime = %v", got)
	}
}

func TestServerRequiresBoundedEphemeralClientCertificate(t *testing.T) {
	t.Parallel()
	now := time.Now().UTC().Truncate(time.Second)
	identity, err := NewEphemeralIdentity(now, time.Hour)
	if err != nil {
		t.Fatal(err)
	}
	config := ServerTLSConfig(identity)
	if config.ClientAuth != tls.RequireAnyClientCert {
		t.Fatalf("client auth mode = %v", config.ClientAuth)
	}
	if err = config.VerifyPeerCertificate(nil, nil); !errors.Is(err, ErrPeerCertificate) {
		t.Fatalf("missing client certificate error = %v", err)
	}
	oversized := [][]byte{make([]byte, limits.MaxCertificateBytes+1)}
	if err = config.VerifyPeerCertificate(oversized, nil); !errors.Is(err, ErrPeerCertificate) {
		t.Fatalf("oversized client certificate error = %v", err)
	}
	longLived := reissueCertificate(t, identity, now, limits.MaxIdentityLifetime+time.Minute)
	if err = config.VerifyPeerCertificate([][]byte{longLived}, nil); !errors.Is(err, ErrPeerCertificate) {
		t.Fatalf("long-lived client certificate error = %v", err)
	}
}

func TestTLSHandshakeRejectsMissingClientCertificate(t *testing.T) {
	t.Parallel()
	now := time.Now().UTC().Truncate(time.Second)
	hostIdentity, err := NewEphemeralIdentity(now, time.Hour)
	if err != nil {
		t.Fatal(err)
	}
	guestIdentity, err := NewEphemeralIdentity(now, time.Hour)
	if err != nil {
		t.Fatal(err)
	}
	clientConfig, err := ClientTLSConfig(guestIdentity, hostIdentity.FingerprintHex(), time.Now)
	if err != nil {
		t.Fatal(err)
	}
	clientConfig.Certificates = nil
	serverSocket, clientSocket := net.Pipe()
	defer serverSocket.Close()
	defer clientSocket.Close()
	deadline := time.Now().Add(2 * time.Second)
	_ = serverSocket.SetDeadline(deadline)
	_ = clientSocket.SetDeadline(deadline)
	server := tls.Server(serverSocket, ServerTLSConfig(hostIdentity))
	client := tls.Client(clientSocket, clientConfig)
	serverResult := make(chan error, 1)
	go func() { serverResult <- server.Handshake() }()
	clientErr := client.Handshake()
	serverErr := <-serverResult
	if clientErr == nil && serverErr == nil {
		t.Fatal("TLS handshake accepted a guest without a client certificate")
	}
}

func TestIdentityDestroyClearsPrivateKey(t *testing.T) {
	t.Parallel()
	identity, err := NewEphemeralIdentity(time.Now(), time.Hour)
	if err != nil {
		t.Fatal(err)
	}
	privateKey, ok := identity.Certificate.PrivateKey.(ed25519.PrivateKey)
	if !ok {
		t.Fatal("identity private key is not Ed25519")
	}
	identity.Destroy()
	for index, value := range privateKey {
		if value != 0 {
			t.Fatalf("private key byte %d was not cleared", index)
		}
	}
	if identity.Certificate.PrivateKey != nil || identity.SPKIFingerprint != ([sha256.Size]byte{}) {
		t.Fatal("destroyed identity retained material")
	}
	identity.Destroy()
}

func TestQUICDisablesZeroRTTAndBoundsStreams(t *testing.T) {
	t.Parallel()
	if ALPN != "webjam/3" {
		t.Fatalf("ALPN = %q", ALPN)
	}
	config := QUICConfig()
	if config.Allow0RTT {
		t.Fatal("0-RTT unexpectedly enabled")
	}
	if config.InitialPacketSize != 1200 || !config.DisablePathMTUDiscovery {
		t.Fatalf("relay packet bound = %d / PMTU disabled %t", config.InitialPacketSize, config.DisablePathMTUDiscovery)
	}
	if config.MaxIncomingStreams != 4 || config.MaxIncomingUniStreams != -1 {
		t.Fatalf("stream bounds = %d/%d", config.MaxIncomingStreams, config.MaxIncomingUniStreams)
	}
}

func stringHex(payload []byte) string {
	const digits = "0123456789abcdef"
	encoded := make([]byte, len(payload)*2)
	for i, value := range payload {
		encoded[i*2] = digits[value>>4]
		encoded[i*2+1] = digits[value&0x0f]
	}
	return string(encoded)
}

func reissueCertificate(t *testing.T, identity Identity, now time.Time, lifetime time.Duration) []byte {
	t.Helper()
	privateKey, ok := identity.Certificate.PrivateKey.(ed25519.PrivateKey)
	if !ok {
		t.Fatal("identity private key is not Ed25519")
	}
	template := &x509.Certificate{
		SerialNumber: big.NewInt(2), Subject: pkix.Name{CommonName: "WebJam test identity"},
		NotBefore: now, NotAfter: now.Add(lifetime),
		KeyUsage:              x509.KeyUsageDigitalSignature,
		ExtKeyUsage:           []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth, x509.ExtKeyUsageClientAuth},
		BasicConstraintsValid: true,
	}
	der, err := x509.CreateCertificate(rand.Reader, template, template, privateKey.Public(), privateKey)
	if err != nil {
		t.Fatal(err)
	}
	return der
}
