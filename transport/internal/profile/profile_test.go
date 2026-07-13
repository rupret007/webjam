package profile

import "testing"

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
