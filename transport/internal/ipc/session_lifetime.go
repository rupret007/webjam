package ipc

import (
	"crypto/x509"
	"time"

	"github.com/rupret007/webjam/transport/internal/icequic"
	"github.com/rupret007/webjam/transport/internal/limits"
)

// sessionOperationDeadline bounds an owned operation independently of the
// invitation's admission window. A prepared host identity can predate Open, so
// its actual certificate expiry, not a fresh identity lifetime, is the ceiling.
func sessionOperationDeadline(now time.Time, identity *icequic.Identity) (time.Time, error) {
	if identity == nil || len(identity.Certificate.Certificate) != 1 {
		return time.Time{}, ErrEnrollmentInvalid
	}
	certificate, err := x509.ParseCertificate(identity.Certificate.Certificate[0])
	if err != nil || !certificate.NotAfter.After(now) {
		return time.Time{}, ErrEnrollmentInvalid
	}
	deadline := now.Add(limits.MaxActiveSessionLifetime)
	if certificate.NotAfter.Before(deadline) {
		deadline = certificate.NotAfter
	}
	return deadline, nil
}
