package directstun

import (
	"reflect"
	"testing"
)

func TestCredentialJSONChoosesSupportedTURNAddressFamily(t *testing.T) {
	t.Parallel()
	for _, test := range []struct {
		name string
		urls string
		want string
	}{
		{name: "ipv6_only", urls: `["turn:[2606:4700:4700::1111]:3478?transport=udp"]`},
		{name: "dual_stack_ipv6_first", urls: `["turn:[2606:4700:4700::1111]:3478?transport=udp","turn:203.0.113.1:3478?transport=udp"]`, want: "203.0.113.1:3478"},
		{name: "dual_stack_ipv4_first", urls: `["turn:203.0.113.1:3478?transport=udp","turn:[2606:4700:4700::1111]:3478?transport=udp"]`, want: "203.0.113.1:3478"},
		{name: "unsupported_tcp_before_ipv4", urls: `["turn:[2606:4700:4700::1111]:3478?transport=tcp","turn:203.0.113.1:3478?transport=udp"]`, want: "203.0.113.1:3478"},
	} {
		t.Run(test.name, func(t *testing.T) {
			document := []byte(`{"urls":` + test.urls + `,"username":"test-user","credential":"test-password"}`)
			credentials, err := parseCredentialJSON(document)
			if test.want == "" {
				if err != errNoTURN || !reflect.DeepEqual(credentials, turnCredentials{}) {
					t.Fatalf("IPv4 transport accepted unsupported IPv6-only credentials: %v", err)
				}
				return
			}
			if err != nil || credentials.server != test.want || credentials.serverIP.String() != "203.0.113.1" {
				t.Fatalf("selected TURN server=%q, address=%v, error=%v; want %q", credentials.server, credentials.serverIP, err, test.want)
			}
		})
	}
}
