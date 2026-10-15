// Package profile contains the only service endpoints the transport may use.
// Desktop IPC selects an exact identifier; it can never supply or override an
// address, URL, credential, redirect policy, or certificate policy.
package profile

import "github.com/rupret007/webjam/transport/internal/reference"

type Profile struct {
	ID             string
	ControlAddress string
	RelayAddress   string
	LabOnly        bool
	control        reference.ControlConnector
}

const (
	ReferenceLocalID = "reference-local"
	DirectStunID     = "direct-stun"
)

var referenceLocal = Profile{
	ID:             ReferenceLocalID,
	ControlAddress: reference.ControlAddress,
	RelayAddress:   reference.RelayAddress,
	LabOnly:        true,
	control:        reference.LocalControlConnector(),
}

// directStun is the ordinary Internet Host/Join profile: public STUN plus
// clipboard-sealed candidates. It has no rendezvous, relay, or control
// connector. Musicians do not port-forward.
var directStun = Profile{
	ID:      DirectStunID,
	LabOnly: false,
}

// ControlConnector resolves only an unmodified, shipped profile value. The
// caller cannot turn an ID into authority for a substituted address, TLS
// policy, or relay by editing the descriptive exported fields.
func (p Profile) ControlConnector() (reference.ControlConnector, bool) {
	canonical, ok := Lookup(p.ID)
	if !ok || p != canonical || p.ID != ReferenceLocalID {
		return reference.ControlConnector{}, false
	}
	return p.control, true
}

func Lookup(id string) (Profile, bool) {
	switch id {
	case ReferenceLocalID:
		return referenceLocal, true
	case DirectStunID:
		return directStun, true
	default:
		return Profile{}, false
	}
}
