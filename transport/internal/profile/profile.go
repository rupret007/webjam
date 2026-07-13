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
}

const ReferenceLocalID = "reference-local"

var referenceLocal = Profile{
	ID:             ReferenceLocalID,
	ControlAddress: reference.ControlAddress,
	RelayAddress:   reference.RelayAddress,
	LabOnly:        true,
}

func Lookup(id string) (Profile, bool) {
	if id != ReferenceLocalID {
		return Profile{}, false
	}
	return referenceLocal, true
}
