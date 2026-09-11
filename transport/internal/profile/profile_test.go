package profile

import (
	"github.com/rupret007/webjam/transport/internal/reference"
	"testing"
)

func TestLookupIsExactAndCompiled(t *testing.T) {
	t.Parallel()
	resolved, ok := Lookup(ReferenceLocalID)
	if !ok || resolved.ID != ReferenceLocalID || !resolved.LabOnly ||
		resolved.ControlAddress != "127.0.0.1:47131" ||
		resolved.RelayAddress != "127.0.0.1:47132" {
		t.Fatalf("profile = %+v", resolved)
	}
	for _, rejected := range []string{"", "REFERENCE-LOCAL", "reference-local ", "rеference-local", "http://127.0.0.1"} {
		if _, accepted := Lookup(rejected); accepted {
			t.Fatalf("accepted profile %q", rejected)
		}
	}
}

func TestControlConnectorRejectsSubstitutedProfileFields(t *testing.T) {
	t.Parallel()
	original, _ := Lookup(ReferenceLocalID)
	connector, ok := original.ControlConnector()
	if !ok || connector != reference.LocalControlConnector() {
		t.Fatal("shipped local profile lost its fixed control connector")
	}
	for _, mutate := range []func(*Profile){
		func(p *Profile) { p.ID = "private-internet" },
		func(p *Profile) { p.ControlAddress = "control.example.test:443" },
		func(p *Profile) { p.RelayAddress = "relay.example.test:443" },
		func(p *Profile) { p.LabOnly = false },
		func(p *Profile) { p.control, _ = reference.NewTLSControlConnector("control.example.test", 443) },
	} {
		altered := original
		mutate(&altered)
		if _, accepted := altered.ControlConnector(); accepted {
			t.Fatal("modified profile retained control authority")
		}
	}
	if _, accepted := (Profile{ID: ReferenceLocalID, ControlAddress: original.ControlAddress,
		RelayAddress: original.RelayAddress, LabOnly: true}).ControlConnector(); accepted {
		t.Fatal("descriptive fields manufactured a bound profile")
	}
	if _, accepted := (Profile{}).ControlConnector(); accepted {
		t.Fatal("empty profile retained control authority")
	}
	resolved, _ := Lookup(ReferenceLocalID)
	if resolved != original {
		t.Fatal("editing one profile value changed the shipped registry")
	}
	if _, accepted := Lookup("private-internet"); accepted {
		t.Fatal("TLS foundation enabled a public profile without admission")
	}
}
