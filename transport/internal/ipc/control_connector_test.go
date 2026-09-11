package ipc

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/rupret007/webjam/transport/internal/icequic"
	"github.com/rupret007/webjam/transport/internal/limits"
	"github.com/rupret007/webjam/transport/internal/profile"
)

func TestReferenceOrchestratorRejectsModifiedProfileBeforeOperation(t *testing.T) {
	t.Parallel()
	now := time.Now().UTC().Truncate(time.Second)
	identity, err := icequic.NewEphemeralIdentity(now, limits.HostIdentityLifetime)
	if err != nil {
		t.Fatal("test identity generation failed")
	}
	defer identity.Destroy()
	original, _ := profile.Lookup(profile.ReferenceLocalID)
	for _, mutate := range []func(*profile.Profile){
		func(p *profile.Profile) { p.ID = "private-internet" },
		func(p *profile.Profile) { p.ControlAddress = "control.example.test:443" },
		func(p *profile.Profile) { p.RelayAddress = "relay.example.test:443" },
		func(p *profile.Profile) { p.LabOnly = false },
	} {
		altered := original
		mutate(&altered)
		configuration := &enrollmentConfig{
			Profile: altered, Mode: "host", Generation: 1, SessionID: SessionID{1},
			EnrollmentCapability: Capability{2}, HostSPKISHA256: PublicPin(identity.SPKIFingerprint),
			ExpiresAtUnix: uint64(now.Add(time.Minute).Unix()),
		}
		endpoint := &recordingEndpoint{}
		operation, err := newReferenceFabricOrchestrator(func() time.Time { return now }).Start(
			context.Background(), configuration, &identity, endpoint,
		)
		if operation != nil || !errors.Is(err, ErrEnrollmentInvalid) || endpoint.closed {
			t.Fatal("substituted profile started an operation or stole caller endpoint ownership")
		}
	}
}

func TestTLSFoundationDoesNotEnableIPCProfileOrEndpointOverrides(t *testing.T) {
	t.Parallel()
	now := time.Now().UTC().Truncate(time.Second)
	for _, mode := range []string{"host", "guest"} {
		port, pin := 0, testPin()
		if mode == "host" {
			port, pin = 22124, ""
		}
		fields := openFields(2, mode, "private-internet", now.Add(time.Minute), port, pin)
		if _, err := ParseCommandAt(mustJSON(t, fields), now); !errors.Is(err, ErrUnsupportedProfile) {
			t.Fatal("TLS foundation enabled an unprovisioned public profile")
		}
		for _, key := range []string{"control_address", "control_host", "tls_roots", "insecure_skip_verify", "tls_server_name"} {
			fields = openFields(2, mode, profile.ReferenceLocalID, now.Add(time.Minute), port, pin)
			fields[key] = "not-an-authority"
			if _, err := ParseCommandAt(mustJSON(t, fields), now); err == nil {
				t.Fatal("desktop IPC accepted a caller endpoint or trust override")
			}
		}
	}
}
