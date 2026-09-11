package reference

import (
	"bufio"
	"context"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/json"
	"errors"
	"fmt"
	"math/big"
	"net"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func TestTLSControlConnectorValidatesEndpointBeforeIO(t *testing.T) {
	t.Parallel()
	for _, host := range []string{
		"", "localhost", "service.localhost", "service.local", "service.internal", "home.arpa", "service.home.arpa",
		"127.0.0.1", "192.0.2.1", "::1", "[::1]", "https://control.example.test", "user@control.example.test",
		"control.example.test/path", "control.example.test:443", " control.example.test", "control.example.test\n",
		"control.example.test.", "control..example.test", "-control.example.test", "control-.example.test",
		"control_example.test", "contról.example.test", "Kontrol.example.test", strings.Repeat("a", 64) + ".test",
		strings.Repeat("a.", 127) + "test",
	} {
		if _, err := NewTLSControlConnector(host, 443); !errors.Is(err, ErrInvalidInput) {
			t.Fatal("invalid endpoint was accepted")
		}
	}
	if _, err := NewTLSControlConnector("control.example.test", 0); !errors.Is(err, ErrInvalidInput) {
		t.Fatal("zero port was accepted")
	}
	valid, err := NewTLSControlConnector("CONTROL.Example.test", 443)
	if err != nil || valid.address != "control.example.test:443" || valid.serverName != "control.example.test" || !valid.tls {
		t.Fatal("valid DNS endpoint was not canonically bound")
	}
	for _, invalid := range []ControlConnector{
		{}, {address: "127.0.0.2:47131"}, {address: ControlAddress, serverName: "control.example.test"},
		{address: "control.example.test:0443", serverName: "control.example.test", tls: true},
		{address: "control.example.test:443", serverName: "other.example.test", tls: true},
	} {
		client, err := invalid.dial(context.Background(), func(context.Context, string, string) (net.Conn, error) {
			t.Fatal("invalid connector attempted I/O")
			return nil, nil
		}, nil)
		if client != nil || !errors.Is(err, ErrInvalidInput) {
			t.Fatal("invalid connector did not fail closed")
		}
		if err = invalid.CloseSession(context.Background(), filledSession(1), fixedToken(2), 1, 1); !errors.Is(err, ErrInvalidInput) {
			t.Fatal("invalid close connector did not fail closed")
		}
	}
}

func TestControlConnectorRejectsCanceledOrInvalidContextBeforeDial(t *testing.T) {
	t.Parallel()
	connector, _ := NewTLSControlConnector("control.example.test", 443)
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	for _, current := range []context.Context{nil, ctx} {
		client, err := connector.dial(current, func(context.Context, string, string) (net.Conn, error) {
			t.Fatal("invalid or canceled context dialed")
			return nil, nil
		}, nil)
		want := ErrInvalidInput
		if current != nil {
			want = context.Canceled
		}
		if client != nil || !errors.Is(err, want) {
			t.Fatal("invalid context error classification changed")
		}
	}
	if _, err := connector.dial(context.Background(), nil, nil); !errors.Is(err, ErrInvalidInput) {
		t.Fatal("nil dial function was accepted")
	}
}

func TestControlConnectorSharesBoundedDialAndHandshakeContext(t *testing.T) {
	t.Parallel()
	connector, _ := NewTLSControlConnector("control.example.test", 443)
	before := time.Now()
	_, err := connector.dial(context.Background(), func(ctx context.Context, network, address string) (net.Conn, error) {
		deadline, ok := ctx.Deadline()
		if !ok || deadline.Before(before) || deadline.After(before.Add(controlOperationLimit+time.Second)) ||
			network != "tcp" || address != "control.example.test:443" {
			t.Fatal("dial was not bound to its endpoint and total deadline")
		}
		return nil, errors.New("PRIVATE_DNS_ENDPOINT_CERTIFICATE")
	}, nil)
	if err != ErrControlUnavailable || strings.Contains(fmt.Sprint(err), "PRIVATE") {
		t.Fatal("dial exposed an implementation error")
	}

	ctx, cancel := context.WithCancel(context.Background())
	left, right := net.Pipe()
	tracked := &connectorTrackedConn{Conn: left}
	defer right.Close()
	started := make(chan context.Context, 1)
	finished := make(chan error, 1)
	go func() {
		_, dialErr := connector.dial(ctx, func(bound context.Context, _, _ string) (net.Conn, error) {
			started <- bound
			return tracked, nil
		}, nil)
		finished <- dialErr
	}()
	bound := <-started
	cancel()
	select {
	case err = <-finished:
		if !errors.Is(err, context.Canceled) || bound.Err() != context.Canceled || !tracked.closed.Load() {
			t.Fatal("handshake cancellation did not retire the raw connection")
		}
	case <-time.After(time.Second):
		t.Fatal("TLS handshake ignored cancellation")
	}
}

func TestControlConnectorShortCallerDeadlineAndLateDialAreHonest(t *testing.T) {
	t.Parallel()
	connector, _ := NewTLSControlConnector("control.example.test", 443)
	deadline := time.Now().Add(100 * time.Millisecond)
	ctx, cancel := context.WithDeadline(context.Background(), deadline)
	defer cancel()
	_, err := connector.dial(ctx, func(bound context.Context, _, _ string) (net.Conn, error) {
		if actual, ok := bound.Deadline(); !ok || !actual.Equal(deadline) {
			t.Fatal("short caller deadline was extended")
		}
		<-bound.Done()
		return nil, errors.New("PRIVATE_DNS_TIMEOUT")
	}, nil)
	if !errors.Is(err, context.DeadlineExceeded) {
		t.Fatal("resolution/connect timeout was not safely classified")
	}
	for _, current := range []ControlConnector{LocalControlConnector(), connector} {
		ctx, cancel := context.WithCancel(context.Background())
		left, right := net.Pipe()
		tracked := &connectorTrackedConn{Conn: left}
		client, err := current.dial(ctx, func(context.Context, string, string) (net.Conn, error) {
			cancel()
			return tracked, nil
		}, nil)
		_ = right.Close()
		if client != nil || !errors.Is(err, context.Canceled) || !tracked.closed.Load() {
			t.Fatal("a late connection escaped cancellation")
		}
	}
}

func TestTLSControlConnectorVerifiesCertificateBeforeAuthority(t *testing.T) {
	t.Parallel()
	for _, tc := range []struct {
		name, certificateName string
		from, until           time.Duration
		trust                 bool
		maxVersion            uint16
		accepted              bool
	}{
		{"valid", "control.example.test", -time.Hour, time.Hour, true, tls.VersionTLS13, true},
		{"wrong_name", "other.example.test", -time.Hour, time.Hour, true, tls.VersionTLS13, false},
		{"untrusted", "control.example.test", -time.Hour, time.Hour, false, tls.VersionTLS13, false},
		{"expired", "control.example.test", -2 * time.Hour, -time.Hour, true, tls.VersionTLS13, false},
		{"not_yet_valid", "control.example.test", time.Hour, 2 * time.Hour, true, tls.VersionTLS13, false},
		{"tls12", "control.example.test", -time.Hour, time.Hour, true, tls.VersionTLS12, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			certificate, roots := connectorTestCertificate(t, tc.certificateName, tc.from, tc.until)
			if !tc.trust {
				roots = nil // The production roots policy must reject our private test signer.
			}
			left, right := net.Pipe()
			tracked := &connectorTrackedConn{Conn: left}
			defer tracked.Close()
			var applicationRead atomic.Bool
			serverResult := make(chan error, 1)
			var serverName string
			go func() {
				secured := tls.Server(right, &tls.Config{
					Certificates: []tls.Certificate{certificate}, MinVersion: tls.VersionTLS12, MaxVersion: tc.maxVersion,
					GetConfigForClient: func(hello *tls.ClientHelloInfo) (*tls.Config, error) {
						serverName = hello.ServerName
						return nil, nil
					},
				})
				defer secured.Close()
				_ = secured.SetDeadline(time.Now().Add(2 * time.Second))
				if err := secured.Handshake(); err != nil {
					serverResult <- err
					return
				}
				line, err := bufio.NewReader(secured).ReadBytes('\n')
				applicationRead.Store(len(line) > 0)
				if err == nil {
					_, err = secured.Write([]byte("{\"v\":3,\"ok\":true}\n"))
				}
				serverResult <- err
			}()
			connector, _ := NewTLSControlConnector("control.example.test", 443)
			client, err := connector.dial(context.Background(), func(context.Context, string, string) (net.Conn, error) {
				return tracked, nil
			}, roots)
			if tc.accepted {
				if err != nil || client == nil {
					t.Fatal("normally verified TLS endpoint was rejected")
				}
				if err = client.CloseSession(context.Background(), filledSession(1), RoleHost, fixedToken(2), 1, 1); err != nil {
					t.Fatal("verified control connection could not send authenticated close")
				}
				_ = client.Close()
			} else if client != nil || err != ErrControlUnavailable || !tracked.closed.Load() {
				t.Fatal("TLS verification failure exposed authority or leaked its connection")
			}
			select {
			case serverErr := <-serverResult:
				if tc.accepted && serverErr != nil {
					t.Fatal("verified TLS protocol exchange failed")
				}
			case <-time.After(3 * time.Second):
				t.Fatal("TLS server was not retired")
			}
			if applicationRead.Load() != tc.accepted || serverName != "control.example.test" {
				t.Fatal("unverified server received application authority, or endpoint identity changed")
			}
		})
	}
}

func TestTLSConnectorFreshCloseKeepsEndpointAndSequence(t *testing.T) {
	t.Parallel()
	certificate, roots := connectorTestCertificate(t, "control.example.test", -time.Hour, time.Hour)
	connector, _ := NewTLSControlConnector("control.example.test", 7443)
	bound := connector
	connector, _ = NewTLSControlConnector("replacement.example.test", 8443)
	_ = connector
	session, token := filledSession(4), fixedToken(5)
	defer token.Destroy()
	for _, sequence := range []uint64{31, 32} {
		requests := make(chan map[string]any, 1)
		serverDone := make(chan error, 1)
		var tracked *connectorTrackedConn
		err := closeLocalSession(context.Background(), session, token, 6, sequence, func(ctx context.Context) (*Client, error) {
			return bound.dial(ctx, func(_ context.Context, network, address string) (net.Conn, error) {
				if network != "tcp" || address != "control.example.test:7443" {
					t.Fatal("fresh close changed its admitted endpoint")
				}
				left, right := net.Pipe()
				tracked = &connectorTrackedConn{Conn: left}
				go func() {
					secured := tls.Server(right, &tls.Config{Certificates: []tls.Certificate{certificate}, MinVersion: tls.VersionTLS13})
					defer secured.Close()
					_ = secured.SetDeadline(time.Now().Add(2 * time.Second))
					var request map[string]any
					if err := json.NewDecoder(secured).Decode(&request); err != nil {
						serverDone <- err
						return
					}
					requests <- request
					_, err := secured.Write([]byte("{\"v\":3,\"ok\":true}\n"))
					serverDone <- err
				}()
				return tracked, nil
			}, roots)
		})
		if err != nil || tracked == nil || !tracked.closed.Load() {
			t.Fatal("fresh TLS close failed or retained its connection")
		}
		if err = <-serverDone; err != nil {
			t.Fatal("fresh TLS close protocol exchange failed")
		}
		request := <-requests
		if len(request) != 7 || request["op"] != "close" || request["role"] != "host" ||
			request["session"] != encode(session[:]) || request["token"] != encode(token.value[:]) ||
			request["generation"] != float64(6) || request["sequence"] != float64(sequence) {
			t.Fatal("fresh TLS close changed authority, sequence, or operation")
		}
	}
}

func TestControlConnectorClosesConnectionReturnedWithDialError(t *testing.T) {
	t.Parallel()
	left, right := net.Pipe()
	defer right.Close()
	tracked := &connectorTrackedConn{Conn: left}
	connector, _ := NewTLSControlConnector("control.example.test", 443)
	client, err := connector.dial(context.Background(), func(context.Context, string, string) (net.Conn, error) {
		return tracked, errors.New("PRIVATE_PARTIAL_CONNECTION")
	}, nil)
	if client != nil || err != ErrControlUnavailable || !tracked.closed.Load() {
		t.Fatal("partial failed dial leaked a connection or error detail")
	}
}

type connectorTrackedConn struct {
	net.Conn
	closed atomic.Bool
}

func (c *connectorTrackedConn) Close() error {
	c.closed.Store(true)
	return c.Conn.Close()
}

func connectorTestCertificate(t *testing.T, host string, from, until time.Duration) (tls.Certificate, *x509.CertPool) {
	t.Helper()
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatal("test key generation failed")
	}
	template := &x509.Certificate{
		SerialNumber: big.NewInt(1), Subject: pkix.Name{CommonName: "isolated connector test"}, DNSNames: []string{host},
		NotBefore: time.Now().Add(from), NotAfter: time.Now().Add(until),
		KeyUsage: x509.KeyUsageDigitalSignature, ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth},
	}
	encoded, err := x509.CreateCertificate(rand.Reader, template, template, key.Public(), key)
	if err != nil {
		t.Fatal("test certificate creation failed")
	}
	certificate, err := x509.ParseCertificate(encoded)
	if err != nil {
		t.Fatal("test certificate parsing failed")
	}
	roots := x509.NewCertPool()
	roots.AddCert(certificate)
	return tls.Certificate{Certificate: [][]byte{encoded}, PrivateKey: key}, roots
}

func TestTLSClientRetiresRawConnectionWithoutCloseNotifyBudget(t *testing.T) {
	t.Parallel()
	certificate, roots := connectorTestCertificate(t, "control.example.test", -time.Hour, time.Hour)
	for _, mode := range []string{"explicit_close", "acknowledged_close", "malformed_receipt", "canceled_receipt", "lost_receipt"} {
		t.Run(mode, func(t *testing.T) {
			left, right := net.Pipe()
			tracked := &connectorTrackedConn{Conn: left}
			release := make(chan struct{})
			received := make(chan struct{})
			serverDone := make(chan struct{})
			defer func() {
				close(release)
				_ = right.Close()
				select {
				case <-serverDone:
				case <-time.After(time.Second):
					t.Error("held TLS peer did not retire")
				}
			}()
			go func() {
				defer close(serverDone)
				defer right.Close()
				secured := tls.Server(right, &tls.Config{Certificates: []tls.Certificate{certificate}, MinVersion: tls.VersionTLS13})
				if err := secured.Handshake(); err != nil {
					return
				}
				if mode == "explicit_close" {
					<-release // Deliberately never consume the peer's close_notify.
					return
				}
				if _, err := bufio.NewReader(secured).ReadBytes('\n'); err != nil {
					return
				}
				close(received)
				switch mode {
				case "acknowledged_close":
					_, _ = secured.Write([]byte("{\"v\":3,\"ok\":true}\n"))
				case "malformed_receipt":
					_, _ = secured.Write([]byte("{\"v\":2,\"ok\":true}\n"))
				case "lost_receipt":
					return
				}
				<-release
			}()
			connector, _ := NewTLSControlConnector("control.example.test", 443)
			dial := func(ctx context.Context) (*Client, error) {
				return connector.dial(ctx, func(context.Context, string, string) (net.Conn, error) {
					return tracked, nil
				}, roots)
			}
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			result := make(chan error, 1)
			go func() {
				if mode == "explicit_close" {
					client, err := dial(ctx)
					if err == nil {
						err = client.Close()
					}
					result <- err
					return
				}
				result <- closeLocalSession(ctx, filledSession(1), fixedToken(2), 1, 1, dial)
			}()
			if mode == "canceled_receipt" {
				select {
				case <-received:
					cancel()
				case <-time.After(time.Second):
					t.Fatal("held receipt was never reached")
				}
			}
			select {
			case err := <-result:
				var want error
				switch mode {
				case "malformed_receipt":
					want = ErrControlProtocol
				case "canceled_receipt":
					want = context.Canceled
				case "lost_receipt":
					want = ErrControlUnavailable
				}
				if !errors.Is(err, want) || !tracked.closed.Load() {
					t.Fatal("TLS close lost its safe receipt classification or raw transport ownership")
				}
			case <-time.After(time.Second):
				t.Fatal("TLS close started an independent close_notify write budget")
			}
		})
	}
}
