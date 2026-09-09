package reference

import (
	"bufio"
	"bytes"
	"context"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/json"
	"encoding/pem"
	"errors"
	"io"
	"math/big"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"sync"
	"testing"
	"time"
)

// The DNS route and temporary CA are package-private test inputs. The actual
// connector performs TLS verification and the independent Python process owns
// the real protocol/registry. No system trust, DNS, public service or user
// credentials are modified; this is control-plane proof, not Internet joining.
func TestTLSControlAgainstIndependentReferenceService(t *testing.T) {
	service := startTLSControlService(t)
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	connector, err := NewTLSControlConnector("control.webjam.test", service.controlPort)
	if err != nil {
		t.Fatal(err)
	}
	dial := func(ctx context.Context) (*Client, error) {
		return connector.dial(ctx, service.dial("control.webjam.test"), service.roots)
	}
	host, err := dial(ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer host.Close()
	guest, err := dial(ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer guest.Close()
	for _, client := range []*Client{host, guest} {
		secured, ok := client.conn.(*tls.Conn)
		if !ok || secured.ConnectionState().Version != tls.VersionTLS13 ||
			len(secured.ConnectionState().VerifiedChains) == 0 {
			t.Fatal("control connection lacks verified TLS 1.3")
		}
	}
	session := filledSession(121)
	enrollment, err := DeriveEnrollmentToken(filledCapability(122), session)
	if err != nil {
		t.Fatal(err)
	}
	defer enrollment.Destroy()
	hostToken, err := NewRoleToken()
	if err != nil {
		t.Fatal(err)
	}
	defer hostToken.Destroy()
	guestToken, err := NewRoleToken()
	if err != nil {
		t.Fatal(err)
	}
	defer guestToken.Destroy()
	if err = host.Register(ctx, session, hostToken, enrollment, 9, time.Minute); err != nil {
		t.Fatal(err)
	}
	if err = guest.Enroll(ctx, session, enrollment, guestToken); err != nil {
		t.Fatal(err)
	}
	service.assertCounts(t, 1, 1, 1, 0)
	// TLS does not replace the existing role-token authorization.
	if err = guest.Signal(ctx, session, RoleHost, guestToken, 9, 1, bytes.Repeat([]byte{7}, 32)); !errors.Is(err, ErrUnauthorized) {
		t.Fatal("TLS connection bypassed session role authorization")
	}
	for index, pair := range []struct {
		sender, recipient *Client
		role              Role
		token, other      *RoleToken
	}{
		{host, guest, RoleHost, hostToken, guestToken},
		{guest, host, RoleGuest, guestToken, hostToken},
	} {
		sequence := uint64(index + 1)
		payload := bytes.Repeat([]byte{byte(index + 11)}, 32)
		if err = pair.sender.Signal(ctx, session, pair.role, pair.token, 9, sequence, payload); err != nil {
			t.Fatal(err)
		}
		received, present, pollErr := pair.recipient.Poll(ctx, session, pair.role.opposite(), pair.other, 9, sequence)
		if pollErr != nil || !present || !bytes.Equal(received, payload) {
			t.Fatal("verified control did not preserve opaque bidirectional signaling")
		}
	}
	if err = host.Close(); err != nil {
		t.Fatal(err)
	}
	// The original control socket is gone. A failed new TLS handshake cannot
	// claim removal or silently fall back to the plaintext local service.
	wrong, err := NewTLSControlConnector("other.webjam.test", service.controlPort)
	if err != nil {
		t.Fatal(err)
	}
	err = closeLocalSession(ctx, session, hostToken, 9, 3, func(ctx context.Context) (*Client, error) {
		return wrong.dial(ctx, service.dial("other.webjam.test"), service.roots)
	})
	if !errors.Is(err, ErrControlUnavailable) {
		t.Fatal("unverified close did not fail closed")
	}
	service.assertCounts(t, 1, 1, 1, 0)
	if err = closeLocalSession(ctx, session, hostToken, 9, 3, dial); err != nil {
		t.Fatal(err)
	}
	service.assertCounts(t, 0, 0, 1, 1)
	if _, _, err = guest.Poll(ctx, session, RoleGuest, guestToken, 9, 4); !errors.Is(err, ErrUnauthorized) {
		t.Fatal("fresh verified host close left guest authority live")
	}
}

func TestTLSControlRejectedBeforeServiceRegistration(t *testing.T) {
	service := startTLSControlService(t)
	for _, test := range []struct {
		name, host string
		roots      *x509.CertPool
	}{
		{"wrong-server-name", "wrong.webjam.test", service.roots},
		{"untrusted-server", "control.webjam.test", x509.NewCertPool()},
	} {
		t.Run(test.name, func(t *testing.T) {
			connector, err := NewTLSControlConnector(test.host, service.controlPort)
			if err != nil {
				t.Fatal(err)
			}
			ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
			defer cancel()
			client, err := connector.dial(ctx, service.dial(test.host), test.roots)
			if client != nil {
				_ = client.Close()
				t.Fatal("certificate rejection exposed a protocol client")
			}
			if !errors.Is(err, ErrControlUnavailable) {
				t.Fatal("certificate rejection did not return the safe control error")
			}
			service.assertCounts(t, 0, 0, 0, 0)
		})
	}
}

type tlsControlService struct {
	controlPort uint16
	httpPort    uint16
	roots       *x509.CertPool
}

func (s tlsControlService) dial(host string) func(context.Context, string, string) (net.Conn, error) {
	return func(ctx context.Context, network, address string) (net.Conn, error) {
		if network != "tcp" || address != net.JoinHostPort(host, strconv.Itoa(int(s.controlPort))) {
			return nil, errors.New("unexpected test control route")
		}
		return (&net.Dialer{KeepAlive: -1}).DialContext(ctx, "tcp4",
			net.JoinHostPort("127.0.0.1", strconv.Itoa(int(s.controlPort))))
	}
}

func (s tlsControlService) assertCounts(t *testing.T, active, enrolled, registered, closed int) {
	t.Helper()
	transport := &http.Transport{DisableKeepAlives: true}
	defer transport.CloseIdleConnections()
	client := &http.Client{Transport: transport, Timeout: 2 * time.Second}
	response, err := client.Get("http://127.0.0.1:" + strconv.Itoa(int(s.httpPort)) + "/diagnostics")
	if err != nil {
		t.Fatal("owned service diagnostics unavailable")
	}
	defer response.Body.Close()
	var result struct {
		Sessions struct{ Active, Enrolled int }
		Totals   struct {
			Registered int `json:"sessions_registered"`
			Closed     int `json:"sessions_closed"`
		}
	}
	if response.StatusCode != http.StatusOK || json.NewDecoder(io.LimitReader(response.Body, 16_384)).Decode(&result) != nil {
		t.Fatal("owned service diagnostics invalid")
	}
	if result.Sessions.Active != active || result.Sessions.Enrolled != enrolled ||
		result.Totals.Registered != registered || result.Totals.Closed != closed {
		t.Fatalf("service counts active/enrolled/registered/closed = %d/%d/%d/%d; want %d/%d/%d/%d",
			result.Sessions.Active, result.Sessions.Enrolled, result.Totals.Registered, result.Totals.Closed,
			active, enrolled, registered, closed)
	}
}

func startTLSControlService(t *testing.T) tlsControlService {
	t.Helper()
	root, err := filepath.Abs(filepath.Join("..", "..", ".."))
	if err != nil {
		t.Fatal("reference service source unavailable")
	}
	python := findTLSIntegrationPython(root)
	if python == "" {
		if os.Getenv("WEBJAM_REQUIRE_TLS_INTEGRATION") == "1" {
			t.Fatal("Python 3.10+ is required for this TLS integration run")
		}
		t.Skip("Python 3.10+ unavailable for real TLS service integration")
	}
	certPath, keyPath, roots := tlsControlServiceCertificates(t)
	const script = `
import asyncio, json, sys
from pathlib import Path
from webjam_reference.config import ServiceConfig
from webjam_reference.server import ReferenceService

async def main():
    config = ServiceConfig(control_port=0, relay_port=0, http_port=0,
        tls_cert_path=Path(sys.argv[1]), tls_key_path=Path(sys.argv[2]))
    async with ReferenceService(config) as service:
        print(json.dumps({"control": service.control_port, "http": service.http_port}), flush=True)
        await asyncio.to_thread(sys.stdin.buffer.read, 1)

asyncio.run(main())
`
	command := exec.Command(python, "-u", "-c", script, certPath, keyPath)
	command.WaitDelay = 2 * time.Second
	command.Dir = filepath.Join(root, "reference_service")
	logs := &boundedProcessLog{}
	command.Stderr = logs
	stdout, err := command.StdoutPipe()
	if err != nil {
		t.Fatal("reference readiness pipe unavailable")
	}
	stdin, err := command.StdinPipe()
	if err != nil {
		t.Fatal("reference stop pipe unavailable")
	}
	if err = command.Start(); err != nil {
		_ = stdin.Close()
		_ = stdout.Close()
		t.Fatal("isolated TLS service could not start")
	}
	ready := make(chan []byte, 1)
	done := make(chan error, 1)
	go func() {
		scanner := bufio.NewScanner(stdout)
		if scanner.Scan() {
			ready <- append([]byte(nil), scanner.Bytes()...)
		}
		for scanner.Scan() {
			_, _ = logs.Write(scanner.Bytes())
		}
		done <- command.Wait()
	}()
	var once sync.Once
	t.Cleanup(func() {
		once.Do(func() {
			_ = stdin.Close()
			select {
			case err := <-done:
				if err != nil {
					t.Error("isolated TLS service did not exit cleanly")
				}
			case <-time.After(5 * time.Second):
				_ = command.Process.Kill()
				_ = stdout.Close()
				select {
				case <-done:
				case <-time.After(2 * time.Second):
					t.Error("isolated TLS service could not be reaped within cleanup budget")
				}
				t.Error("isolated TLS service required forced cleanup")
			}
		})
	})
	select {
	case line := <-ready:
		var ports struct{ Control, HTTP uint16 }
		if json.Unmarshal(line, &ports) != nil || ports.Control == 0 || ports.HTTP == 0 {
			t.Fatal("isolated TLS service returned invalid readiness")
		}
		return tlsControlService{ports.Control, ports.HTTP, roots}
	case <-time.After(5 * time.Second):
		t.Fatalf("isolated TLS service did not become ready: %s", logs.String())
	}
	return tlsControlService{}
}

func findTLSIntegrationPython(root string) string {
	candidates := []string{filepath.Join(root, ".venv", "bin", "python"), "python3", "python"}
	if runtime.GOOS == "windows" {
		candidates = append([]string{filepath.Join(root, ".venv", "Scripts", "python.exe")}, candidates...)
	}
	for _, candidate := range candidates {
		path, err := exec.LookPath(candidate)
		if err != nil {
			continue
		}
		ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
		probe := exec.CommandContext(ctx, path, "-c", "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)")
		probe.WaitDelay = 500 * time.Millisecond
		err = probe.Run()
		cancel()
		if err == nil {
			return path
		}
	}
	return ""
}

func tlsControlServiceCertificates(t *testing.T) (string, string, *x509.CertPool) {
	t.Helper()
	caKey, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatal("test CA generation failed")
	}
	leafKey, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatal("test leaf generation failed")
	}
	now := time.Now()
	ca := &x509.Certificate{SerialNumber: big.NewInt(1), Subject: pkix.Name{CommonName: "Temporary WebJam TLS test CA"},
		NotBefore: now.Add(-time.Hour), NotAfter: now.Add(time.Hour), IsCA: true,
		BasicConstraintsValid: true, KeyUsage: x509.KeyUsageCertSign}
	caDER, err := x509.CreateCertificate(rand.Reader, ca, ca, &caKey.PublicKey, caKey)
	if err != nil {
		t.Fatal("test CA signing failed")
	}
	leaf := &x509.Certificate{SerialNumber: big.NewInt(2), DNSNames: []string{"control.webjam.test"},
		NotBefore: now.Add(-time.Hour), NotAfter: now.Add(time.Hour),
		KeyUsage: x509.KeyUsageDigitalSignature, ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth}}
	leafDER, err := x509.CreateCertificate(rand.Reader, leaf, ca, &leafKey.PublicKey, caKey)
	if err != nil {
		t.Fatal("test leaf signing failed")
	}
	keyDER, err := x509.MarshalPKCS8PrivateKey(leafKey)
	if err != nil {
		t.Fatal("test leaf key encoding failed")
	}
	defer clear(keyDER)
	keyPEM := pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: keyDER})
	defer clear(keyPEM)
	dir := t.TempDir()
	certPath, keyPath := filepath.Join(dir, "server.pem"), filepath.Join(dir, "server-key.pem")
	if os.WriteFile(certPath, pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: leafDER}), 0o600) != nil ||
		os.WriteFile(keyPath, keyPEM, 0o600) != nil {
		t.Fatal("temporary test certificate files unavailable")
	}
	pool := x509.NewCertPool()
	if !pool.AppendCertsFromPEM(pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: caDER})) {
		t.Fatal("temporary test trust unavailable")
	}
	return certPath, keyPath, pool
}
