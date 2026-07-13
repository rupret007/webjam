package reference

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"strings"
	"testing"
	"time"
)

func TestRoleTokenIsRandomRedactedAndDestroyable(t *testing.T) {
	t.Parallel()
	first, err := NewRoleToken()
	if err != nil {
		t.Fatal(err)
	}
	second, err := NewRoleToken()
	if err != nil {
		t.Fatal(err)
	}
	if first.value == second.value || first.value == ([32]byte{}) {
		t.Fatal("role tokens were zero or repeated")
	}
	if got := fmt.Sprintf("%v %#v", first, first); strings.Contains(got, encode(first.value[:])) ||
		!strings.Contains(got, "redacted") {
		t.Fatalf("role token formatting was not redacted: %q", got)
	}
	first.Destroy()
	second.Destroy()
	if first.valid() || second.valid() {
		t.Fatal("destroy retained role token bytes")
	}
}

func TestControlClientRunsStrictRegistrationEnrollmentSignalPollAndClose(t *testing.T) {
	t.Parallel()
	client, requests := scriptedClient(t, []string{
		`{"generation":7,"ok":true,"participant_limit":1,"ttl_seconds":60,"v":3}`,
		`{"ok":true,"participant_limit":1,"ttl_seconds":59,"v":3}`,
		`{"ok":true,"v":3}`,
		`{"ok":true,"sealed_payloads":["b3BhcXVlLXNpZ25hbC10YWc"],"v":3}`,
		`{"ok":true,"v":3}`,
	})
	defer client.Close()
	host := fixedToken(1)
	guest := fixedToken(2)
	session := filledSession(3)
	capability := filledCapability(4)
	enrollment := mustEnrollmentToken(t, capability, session)
	defer enrollment.Destroy()
	ctx := context.Background()
	if err := client.Register(ctx, session, host, enrollment, 7, time.Minute); err != nil {
		t.Fatal(err)
	}
	if err := client.Enroll(ctx, session, enrollment, guest); err != nil {
		t.Fatal(err)
	}
	sealed := []byte("opaque-signal-tag")
	if err := client.Signal(ctx, session, RoleGuest, guest, 7, 1, sealed); err != nil {
		t.Fatal(err)
	}
	polled, ok, err := client.Poll(ctx, session, RoleHost, host, 7, 1)
	if err != nil || !ok || string(polled) != string(sealed) {
		t.Fatalf("poll = %q, %v, %v", polled, ok, err)
	}
	if err = client.CloseSession(ctx, session, RoleHost, host, 7, 2); err != nil {
		t.Fatal(err)
	}

	operations := []string{"register", "enroll", "signal", "poll", "close"}
	for _, operation := range operations {
		request := <-requests
		if request["v"] != float64(3) || request["op"] != operation {
			t.Fatalf("request = %#v", request)
		}
		if encoded, exists := request["enrollment_token"]; exists && encoded == encode(capability[:]) {
			t.Fatal("control request exposed raw invitation capability")
		}
		for _, forbidden := range []string{"name", "address", "path", "certificate", "private_key"} {
			if _, exists := request[forbidden]; exists {
				t.Fatalf("request exposed forbidden field %q", forbidden)
			}
		}
	}
}

func TestControlClientMapsBoundedErrorsWithoutEchoingServerText(t *testing.T) {
	t.Parallel()
	client, _ := scriptedClient(t, []string{
		`{"error":"enrollment_used","ok":false,"v":3}`,
		`{"error":"overloaded","ok":false,"v":3}`,
	})
	defer client.Close()
	session := filledSession(1)
	capability := filledCapability(2)
	enrollment := mustEnrollmentToken(t, capability, session)
	defer enrollment.Destroy()
	token := fixedToken(3)
	if err := client.Enroll(context.Background(), session, enrollment, token); !errors.Is(err, ErrEnrollmentUsed) {
		t.Fatalf("enroll error = %v", err)
	}
	if err := client.Signal(
		context.Background(), session, RoleGuest, token, 1, 1, []byte("opaque-signal-tag"),
	); !errors.Is(err, ErrOverloaded) {
		t.Fatalf("signal error = %v", err)
	}
}

func TestControlClientRejectsDuplicateUnknownAndOversizedResponses(t *testing.T) {
	t.Parallel()
	for _, response := range []string{
		`{"ok":true,"ok":true,"v":3}`,
		`{"ok":true,"v":3,"raw_endpoint":"203.0.113.5:9999"}`,
		`{"ok":true,"v":2}`,
		`{"ok":true,"participant_limit":2,"ttl_seconds":60,"v":3}`,
		`{"ok":true,"sealed_payloads":null,"v":3}`,
	} {
		client, _ := scriptedClient(t, []string{response})
		session := filledSession(1)
		enrollment := mustEnrollmentToken(t, filledCapability(2), session)
		defer enrollment.Destroy()
		err := client.Enroll(
			context.Background(), session, enrollment, fixedToken(3),
		)
		if !errors.Is(err, ErrControlProtocol) {
			t.Fatalf("response %q error = %v", response, err)
		}
		if err = client.Enroll(
			context.Background(), session, enrollment, fixedToken(3),
		); !errors.Is(err, ErrClosed) {
			t.Fatalf("protocol failure left client reusable: %v", err)
		}
	}
	left, right := net.Pipe()
	client := newClient(left)
	session := filledSession(1)
	enrollment := mustEnrollmentToken(t, filledCapability(2), session)
	defer enrollment.Destroy()
	go func() {
		reader := bufio.NewReader(right)
		_, _ = reader.ReadString('\n')
		_, _ = right.Write(append([]byte(`{"ok":true,"v":3,"padding":"`),
			append([]byte(strings.Repeat("x", MaxControlFrameBytes)), []byte(`"}\n`)...)...))
	}()
	defer right.Close()
	if err := client.Enroll(
		context.Background(), session, enrollment, fixedToken(3),
	); !errors.Is(err, ErrControlProtocol) {
		t.Fatalf("oversize error = %v", err)
	}
}

func TestControlClientValidatesInputsBeforeWriting(t *testing.T) {
	t.Parallel()
	left, right := net.Pipe()
	defer left.Close()
	defer right.Close()
	client := newClient(left)
	session := filledSession(1)
	enrollment := mustEnrollmentToken(t, filledCapability(2), session)
	defer enrollment.Destroy()
	if err := client.Register(
		context.Background(), SessionID{}, fixedToken(1), enrollment, 1, time.Minute,
	); !errors.Is(err, ErrInvalidInput) {
		t.Fatalf("zero session error = %v", err)
	}
	if err := client.Signal(
		context.Background(), filledSession(1), RoleHost, fixedToken(2), 1, 0,
		[]byte("opaque-signal-tag"),
	); !errors.Is(err, ErrInvalidInput) {
		t.Fatalf("zero sequence error = %v", err)
	}
	if _, _, err := client.Poll(
		context.Background(), filledSession(1), Role(99), fixedToken(2), 1, 1,
	); !errors.Is(err, ErrInvalidInput) {
		t.Fatalf("bad role error = %v", err)
	}
	if err := client.Enroll(
		context.Background(), filledSession(9), enrollment, fixedToken(2),
	); !errors.Is(err, ErrInvalidInput) {
		t.Fatalf("cross-session enrollment error = %v", err)
	}
}

func TestControlClientCancellationIsBoundedAndClosesConnection(t *testing.T) {
	t.Parallel()
	left, right := net.Pipe()
	client := newClient(left)
	t.Cleanup(func() {
		_ = client.Close()
		_ = right.Close()
	})
	requestRead := make(chan struct{})
	go func() {
		_, _ = bufio.NewReader(right).ReadBytes('\n')
		close(requestRead)
		_, _ = bufio.NewReader(right).ReadBytes('\n')
	}()
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Millisecond)
	defer cancel()
	session := filledSession(1)
	enrollment := mustEnrollmentToken(t, filledCapability(2), session)
	defer enrollment.Destroy()
	err := client.Enroll(ctx, session, enrollment, fixedToken(3))
	if !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("cancellation error = %v", err)
	}
	<-requestRead
	if err = client.Enroll(
		context.Background(), session, enrollment, fixedToken(3),
	); !errors.Is(err, ErrClosed) {
		t.Fatalf("cancelled client remained reusable: %v", err)
	}
}

func TestEnrollmentTokenDerivationIsSessionBoundSeparatedAndRedacted(t *testing.T) {
	t.Parallel()
	capability := filledCapability(2)
	session := filledSession(1)
	token := mustEnrollmentToken(t, capability, session)
	if token.value == [32]byte(capability) {
		t.Fatal("enrollment token reused raw invitation capability")
	}
	const golden = "36cbc39154f7b7774c6dce8430af5b00e2ef078b8b620a315313adb194f43e2b"
	if got := fmt.Sprintf("%x", token.value); got != golden {
		t.Fatalf("enrollment token = %s", got)
	}
	other := mustEnrollmentToken(t, capability, filledSession(9))
	if token.value == other.value || token.validFor(filledSession(9)) {
		t.Fatal("enrollment derivation was not session bound")
	}
	if got := fmt.Sprintf("%v %#v", token, token); strings.Contains(got, encode(token.value[:])) ||
		!strings.Contains(got, "redacted") {
		t.Fatalf("enrollment token formatting was not redacted: %q", got)
	}
	token.Destroy()
	other.Destroy()
	if token.validFor(session) {
		t.Fatal("destroy retained enrollment token")
	}
}

func scriptedClient(t *testing.T, responses []string) (*Client, <-chan map[string]any) {
	t.Helper()
	left, right := net.Pipe()
	requests := make(chan map[string]any, len(responses))
	go func() {
		defer right.Close()
		reader := bufio.NewReader(right)
		for _, response := range responses {
			line, err := reader.ReadBytes('\n')
			if err != nil {
				return
			}
			var request map[string]any
			if json.Unmarshal(line, &request) != nil {
				return
			}
			requests <- request
			_, _ = right.Write(append([]byte(response), '\n'))
		}
	}()
	t.Cleanup(func() {
		_ = left.Close()
		_ = right.Close()
	})
	return newClient(left), requests
}

func fixedToken(value byte) *RoleToken {
	token := &RoleToken{}
	for index := range token.value {
		token.value[index] = value
	}
	return token
}

func filledSession(value byte) SessionID {
	var session SessionID
	for index := range session {
		session[index] = value
	}
	return session
}

func filledCapability(value byte) Capability {
	var capability Capability
	for index := range capability {
		capability[index] = value
	}
	return capability
}

func filledPin(value byte) PeerPin {
	var pin PeerPin
	for index := range pin {
		pin[index] = value
	}
	return pin
}

func mustEnrollmentToken(
	t *testing.T, capability Capability, session SessionID,
) *EnrollmentToken {
	t.Helper()
	token, err := DeriveEnrollmentToken(capability, session)
	if err != nil {
		t.Fatal(err)
	}
	return token
}
