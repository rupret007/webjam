package sessionauth

import (
	"crypto/sha256"
	"errors"
	"testing"
	"time"
)

func TestProofBindsExporterContextAndReplay(t *testing.T) {
	t.Parallel()
	now := time.Date(2026, 7, 13, 12, 0, 0, 0, time.UTC)
	binding := Binding{
		SessionID: SessionID{1}, SenderRole: RoleGuest, Generation: 4,
		HostPin: PeerPin{2}, GuestPin: PeerPin{3}, Nonce: Nonce{4},
		ExpiresAt: now.Add(time.Minute),
	}
	capability := Capability{5}
	exporterA := deterministicExporter([]byte("tls-connection-a"))
	proof, err := createProof(exporterA, capability, binding, now)
	if err != nil {
		t.Fatal(err)
	}
	cache := NewReplayCache()
	if err = verifyProof(exporterA, capability, binding, proof, now, cache); err != nil {
		t.Fatal(err)
	}
	if err = verifyProof(exporterA, capability, binding, proof, now, cache); !errors.Is(err, ErrProofReplay) {
		t.Fatalf("replay error = %v", err)
	}
	if err = verifyProof(deterministicExporter([]byte("tls-connection-b")), capability, binding, proof, now, NewReplayCache()); !errors.Is(err, ErrInvalidProof) {
		t.Fatalf("other TLS connection error = %v", err)
	}
	wrongGeneration := binding
	wrongGeneration.Generation++
	if err = verifyProof(exporterA, capability, wrongGeneration, proof, now, NewReplayCache()); !errors.Is(err, ErrProofMismatch) {
		t.Fatalf("generation error = %v", err)
	}
	wrongExpiry := binding
	wrongExpiry.ExpiresAt = wrongExpiry.ExpiresAt.Add(time.Second)
	if err = verifyProof(exporterA, capability, wrongExpiry, proof, now, NewReplayCache()); !errors.Is(err, ErrProofMismatch) {
		t.Fatalf("expiry error = %v", err)
	}
}

func TestGateStartsQuarantined(t *testing.T) {
	t.Parallel()
	gate := NewGate()
	if err := gate.Require(); !errors.Is(err, ErrQuarantined) {
		t.Fatalf("gate error = %v", err)
	}
}

func deterministicExporter(secret []byte) exporter {
	return func(label string, context []byte, length int) ([]byte, error) {
		hash := sha256.New()
		_, _ = hash.Write([]byte(label))
		_, _ = hash.Write(context)
		_, _ = hash.Write(secret)
		seed := hash.Sum(nil)
		output := make([]byte, 0, length)
		for len(output) < length {
			output = append(output, seed...)
		}
		return append([]byte(nil), output[:length]...), nil
	}
}
