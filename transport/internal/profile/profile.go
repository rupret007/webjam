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

const ReferenceLocalID = "reference-local"

var referenceLocal = Profile{
	ID:             ReferenceLocalID,
	ControlAddress: reference.ControlAddress,
	RelayAddress:   reference.RelayAddress,
	LabOnly:        true,
	control:        reference.LocalControlConnector(),
}

// ControlConnector resolves only an unmodified, shipped profile value. The
// caller cannot turn an ID into authority for a substituted address, TLS
// policy, or relay by editing the descriptive exported fields.
func (p Profile) ControlConnector() (reference.ControlConnector, bool) {
	canonical, ok := Lookup(p.ID)
	if !ok || p != canonical {
		return reference.ControlConnector{}, false
	}
	return p.control, true
}

func Lookup(id string) (Profile, bool) {
	if id != ReferenceLocalID {
		return Profile{}, false
	}
	return referenceLocal, true
}
