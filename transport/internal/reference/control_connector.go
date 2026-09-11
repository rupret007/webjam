package reference

import (
	"context"
	"crypto/tls"
	"crypto/x509"
	"net"
	"strconv"
	"strings"
)

// ControlConnector binds the endpoint and certificate policy for both the
// initial control connection and a later fresh authenticated close. Its zero
// value is invalid. Desktop IPC cannot construct or modify these values.
type ControlConnector struct {
	address    string
	serverName string
	tls        bool
}

// LocalControlConnector is the only plaintext connector. Its endpoint is
// fixed to the existing reference-local loopback service.
func LocalControlConnector() ControlConnector {
	return ControlConnector{address: ControlAddress}
}

// NewTLSControlConnector prepares a provisioned DNS endpoint without dialing.
// It does not publish a service profile or grant permission to register rooms.
// Dial always uses normal hostname verification and the operating system's
// trusted roots; callers cannot override the certificate policy.
func NewTLSControlConnector(host string, port uint16) (ControlConnector, error) {
	for index := range host {
		if host[index] >= 128 {
			return ControlConnector{}, ErrInvalidInput
		}
	}
	host = strings.ToLower(host)
	if port == 0 || !validControlDNSName(host) {
		return ControlConnector{}, ErrInvalidInput
	}
	return ControlConnector{
		address: net.JoinHostPort(host, strconv.Itoa(int(port))), serverName: host, tls: true,
	}, nil
}

func validControlDNSName(host string) bool {
	if len(host) < 3 || len(host) > 253 || net.ParseIP(host) != nil ||
		!strings.Contains(host, ".") || strings.HasSuffix(host, ".localhost") ||
		strings.HasSuffix(host, ".local") || strings.HasSuffix(host, ".internal") ||
		host == "home.arpa" || strings.HasSuffix(host, ".home.arpa") {
		return false
	}
	for _, label := range strings.Split(host, ".") {
		if len(label) == 0 || len(label) > 63 || label[0] == '-' || label[len(label)-1] == '-' {
			return false
		}
		for _, character := range label {
			if (character < 'a' || character > 'z') && (character < '0' || character > '9') && character != '-' {
				return false
			}
		}
	}
	return true
}

func (c ControlConnector) valid() bool {
	if !c.tls {
		return c.address == ControlAddress && c.serverName == ""
	}
	host, encodedPort, err := net.SplitHostPort(c.address)
	port, portErr := strconv.ParseUint(encodedPort, 10, 16)
	return err == nil && portErr == nil && port != 0 && host == c.serverName &&
		validControlDNSName(host) && encodedPort == strconv.FormatUint(port, 10)
}

func (c ControlConnector) Dial(ctx context.Context) (*Client, error) {
	dialer := net.Dialer{KeepAlive: -1}
	return c.dial(ctx, dialer.DialContext, nil)
}

// dial keeps deterministic test routing and roots package-private. Production
// Dial never accepts a WebJam-specific trust or routing override from IPC,
// environment variables, or callers. Standard system-root policy still applies.
func (c ControlConnector) dial(
	ctx context.Context,
	dialContext func(context.Context, string, string) (net.Conn, error),
	roots *x509.CertPool,
) (*Client, error) {
	if ctx == nil || dialContext == nil || !c.valid() {
		return nil, ErrInvalidInput
	}
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	// DNS resolution, TCP connection, and TLS handshake share one total
	// budget. A shorter caller deadline or cancellation always wins.
	dialCtx, cancel := context.WithTimeout(ctx, controlOperationLimit)
	defer cancel()
	network := "tcp"
	if !c.tls {
		network = "tcp4"
	}
	connection, err := dialContext(dialCtx, network, c.address)
	if err != nil || connection == nil {
		if connection != nil {
			_ = connection.Close()
		}
		return nil, safeControlDialError(dialCtx)
	}
	if !c.tls {
		if dialCtx.Err() != nil {
			_ = connection.Close()
			return nil, safeControlDialError(dialCtx)
		}
		return newClient(connection), nil
	}
	secured := tls.Client(connection, &tls.Config{
		MinVersion: tls.VersionTLS13, ServerName: c.serverName, RootCAs: roots,
	})
	if err := secured.HandshakeContext(dialCtx); err != nil {
		_ = connection.Close()
		return nil, safeControlDialError(dialCtx)
	}
	if dialCtx.Err() != nil {
		_ = connection.Close()
		return nil, safeControlDialError(dialCtx)
	}
	// No Client exists, and therefore no enrollment or role token can be
	// written, until the standard TLS chain and hostname checks succeed.
	client := newClient(secured)
	client.rawConn = connection
	return client, nil
}

func safeControlDialError(ctx context.Context) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	return ErrControlUnavailable
}

// CloseSession makes one fresh authenticated close at this exact endpoint.
// Its acknowledgment, lifetime, and caller-owned token semantics match
// CloseLocalSession; it does not re-enroll or retry an uncertain operation.
func (c ControlConnector) CloseSession(
	ctx context.Context,
	session SessionID,
	hostToken *RoleToken,
	generation uint32,
	sequence uint64,
) error {
	if !c.valid() {
		return ErrInvalidInput
	}
	return closeLocalSession(ctx, session, hostToken, generation, sequence, c.Dial)
}
