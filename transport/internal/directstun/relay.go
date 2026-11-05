package directstun

import (
	"bytes"
	"context"
	"crypto/sha256"
	"crypto/tls"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"io"
	"net"
	"net/http"
	"net/netip"
	"net/url"
	"os"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/pion/logging"
	"github.com/pion/turn/v5"
	"github.com/rupret007/webjam/transport/internal/limits"
)

const (
	testTURNServerEnv = "WEBJAM_TEST_TURN_SERVER"
	testTURNSecretEnv = "WEBJAM_TEST_TURN_SECRET"
	testTURNURLEnv    = "WEBJAM_TEST_TURN_CREDENTIAL_URL"
	turnRESTUser      = "webjam"
	turnProofDomain   = "webjam/v1/turn-credential\x00"
	credentialTTL     = 10 * time.Minute
	// Worker worst case is two sequential 5s upstreams. The client keeps
	// headroom so a slow body after those upstreams still fails closed here.
	workerWorstCase    = 10 * time.Second
	credentialHeadroom = 2 * time.Second
	credentialFetch    = workerWorstCase + credentialHeadroom
	maxCredentialBody  = 4096
	allocateLimit      = 4 * time.Second
)

type credentialProof struct {
	sessionID  [32]byte
	capability [32]byte
}

type proofContextKey struct{}

// WithCredentialProof attaches the session/invite token shapes the credential
// Worker requires. The Cloudflare key and API token never enter this process.
func WithCredentialProof(ctx context.Context, sessionID, capability [32]byte) context.Context {
	if ctx == nil || sessionID == ([32]byte{}) || capability == ([32]byte{}) {
		return ctx
	}
	return context.WithValue(ctx, proofContextKey{}, credentialProof{
		sessionID: sessionID, capability: capability,
	})
}

// Compiled public credential URL. Release builds set this with -ldflags -X.
// The long-lived coturn/Cloudflare secret never belongs in the binary.
var turnCredentialURL = ""

var (
	ErrRelayQuota      = errString("relay session quota reached")
	errNoTURN          = errString("turn credentials unavailable")
	errRelayPermission = errString("turn permission rejected")
)

// Allocation is one short-lived TURN relay used only after direct ICE fails.
type Allocation struct {
	quota     *quotaConn
	client    *turn.Client
	udp       *net.UDPConn
	relayed   *net.UDPAddr
	serverIP  netip.Addr
	allowed   map[netip.Addr]struct{}
	closeOnce sync.Once
}

func (a *Allocation) PacketConn() net.PacketConn {
	if a == nil {
		return nil
	}
	return a.quota
}

func (a *Allocation) Relayed() *net.UDPAddr {
	if a == nil {
		return nil
	}
	return cloneUDPAddr(a.relayed)
}

func (a *Allocation) ServerIP() netip.Addr {
	if a == nil {
		return netip.Addr{}
	}
	return a.serverIP
}

func (a *Allocation) Bytes() uint64 {
	if a == nil || a.quota == nil {
		return 0
	}
	return a.quota.bytes.Load()
}

func (a *Allocation) Candidate() (string, error) {
	if a == nil || a.relayed == nil || a.udp == nil {
		return "", errNoTURN
	}
	local, ok := a.udp.LocalAddr().(*net.UDPAddr)
	if !ok {
		return "", errNoTURN
	}
	address, ok := netip.AddrFromSlice(a.relayed.IP)
	if !ok {
		return "", errNoTURN
	}
	return relayCandidate(netip.AddrPortFrom(address.Unmap(), uint16(a.relayed.Port)), local.Port)
}

func (a *Allocation) Permit(addrs ...*net.UDPAddr) error {
	if a == nil || a.client == nil {
		return errNoTURN
	}
	peers := make([]net.Addr, 0, len(addrs))
	for _, addr := range addrs {
		if addr == nil || addr.Port < 1 {
			continue
		}
		ip, ok := netip.AddrFromSlice(addr.IP)
		if !ok {
			return errRelayPermission
		}
		ip = ip.Unmap()
		if !a.allows(ip) {
			return errRelayPermission
		}
		peers = append(peers, cloneUDPAddr(addr))
	}
	if len(peers) == 0 {
		return errRelayPermission
	}
	return a.client.CreatePermission(peers...)
}

func (a *Allocation) allows(address netip.Addr) bool {
	if a == nil || !address.IsValid() {
		return false
	}
	address = address.Unmap()
	if a.serverIP.IsValid() && address == a.serverIP {
		return true
	}
	if a.relayed != nil {
		if relayed, ok := netip.AddrFromSlice(a.relayed.IP); ok && relayed.Unmap() == address {
			return true
		}
	}
	_, ok := a.allowed[address]
	return ok
}

func (a *Allocation) ParsePeerRelay(encoded string) (*net.UDPAddr, error) {
	if a == nil {
		return nil, errRejectedCandidate
	}
	tried := make(map[netip.Addr]struct{})
	try := func(ip netip.Addr) (*net.UDPAddr, error) {
		ip = ip.Unmap()
		if !ip.IsValid() {
			return nil, errRejectedCandidate
		}
		if _, seen := tried[ip]; seen {
			return nil, errRejectedCandidate
		}
		tried[ip] = struct{}{}
		return ParseRelayTarget(encoded, ip)
	}
	if target, err := try(a.serverIP); err == nil {
		return target, nil
	}
	if a.relayed != nil {
		if ip, ok := netip.AddrFromSlice(a.relayed.IP); ok {
			if target, err := try(ip); err == nil {
				return target, nil
			}
		}
	}
	for ip := range a.allowed {
		if target, err := try(ip); err == nil {
			return target, nil
		}
	}
	return nil, errRejectedCandidate
}

func (a *Allocation) Close() error {
	if a == nil {
		return nil
	}
	a.closeOnce.Do(func() {
		if a.quota != nil {
			_ = a.quota.inner.Close()
		}
		if a.client != nil {
			a.client.Close()
		}
		if a.udp != nil {
			_ = a.udp.Close()
		}
	})
	return nil
}

// TryAllocate gathers a TURN allocation when credentials exist. Failure is
// silent: Host/Join continues on the direct path.
func TryAllocate(ctx context.Context) *Allocation {
	allocation, err := Allocate(ctx)
	if err != nil {
		return nil
	}
	return allocation
}

func Allocate(ctx context.Context) (*Allocation, error) {
	if ctx == nil {
		return nil, errNoTURN
	}
	creds, err := resolveCredentials(ctx)
	if err != nil {
		return nil, err
	}
	udp, err := net.ListenUDP("udp4", &net.UDPAddr{IP: net.IPv4zero, Port: 0})
	if err != nil {
		return nil, err
	}
	factory := logging.NewDefaultLoggerFactory()
	factory.DefaultLogLevel = logging.LogLevelDisabled
	client, err := turn.NewClient(&turn.ClientConfig{
		STUNServerAddr:         creds.server,
		TURNServerAddr:         creds.server,
		Username:               creds.username,
		Password:               creds.password,
		Conn:                   udp,
		LoggerFactory:          factory,
		RequestedAddressFamily: turn.RequestedAddressFamilyIPv4,
	})
	if err != nil {
		_ = udp.Close()
		return nil, err
	}
	if err = client.Listen(); err != nil {
		client.Close()
		_ = udp.Close()
		return nil, err
	}
	done := make(chan struct{})
	var relayed net.PacketConn
	var allocateErr error
	go func() {
		relayed, allocateErr = client.Allocate()
		close(done)
	}()
	timer := time.NewTimer(allocateLimit)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		_ = udp.Close()
		client.Close()
		<-done
		if relayed != nil {
			_ = relayed.Close()
		}
		return nil, errNoTURN
	case <-timer.C:
		_ = udp.Close()
		client.Close()
		<-done
		if relayed != nil {
			_ = relayed.Close()
		}
		return nil, errNoTURN
	case <-done:
	}
	if allocateErr != nil || relayed == nil {
		client.Close()
		_ = udp.Close()
		return nil, errNoTURN
	}
	relayedAddr, ok := relayed.LocalAddr().(*net.UDPAddr)
	if !ok || relayedAddr == nil || relayedAddr.Port < 1 {
		_ = relayed.Close()
		client.Close()
		_ = udp.Close()
		return nil, errNoTURN
	}
	address, ok := netip.AddrFromSlice(relayedAddr.IP)
	if !ok {
		_ = relayed.Close()
		client.Close()
		_ = udp.Close()
		return nil, errNoTURN
	}
	address = address.Unmap()
	if !usableRelayedIPv4(address, creds.serverIP) {
		_ = relayed.Close()
		client.Close()
		_ = udp.Close()
		return nil, errNoTURN
	}
	if creds.serverIP.IsValid() && address != creds.serverIP && !creds.allows(address) {
		_ = relayed.Close()
		client.Close()
		_ = udp.Close()
		return nil, errNoTURN
	}
	serverIP := creds.serverIP
	if !serverIP.IsValid() {
		serverIP = address
	}
	if creds.allowed == nil {
		creds.allowed = map[netip.Addr]struct{}{}
	}
	creds.allowed[address] = struct{}{}
	return &Allocation{
		quota:    newQuotaConn(relayed, creds.quota),
		client:   client,
		udp:      udp,
		relayed:  cloneUDPAddr(relayedAddr),
		serverIP: serverIP,
		allowed:  creds.allowed,
	}, nil
}

func usableRelayedIPv4(address, expected netip.Addr) bool {
	if stunMappedIPv4(address) {
		return true
	}
	return harnessTURN() && address.Is4() && address.IsLoopback() && address == expected.Unmap()
}

type turnCredentials struct {
	server   string
	username string
	password string
	quota    uint64
	serverIP netip.Addr
	allowed  map[netip.Addr]struct{}
}

func (c turnCredentials) allows(address netip.Addr) bool {
	if c.serverIP.IsValid() && address == c.serverIP {
		return true
	}
	if _, ok := c.allowed[address]; ok {
		return true
	}
	return false
}

func resolveCredentials(ctx context.Context) (turnCredentials, error) {
	if harnessTURN() {
		if minted, err := mintHarnessCredentials(); err == nil {
			return minted, nil
		}
	}
	rawURL := strings.TrimSpace(turnCredentialURL)
	if harnessTURN() {
		if override := strings.TrimSpace(os.Getenv(testTURNURLEnv)); override != "" {
			rawURL = override
		}
	}
	if rawURL == "" {
		return turnCredentials{}, errNoTURN
	}
	if _, ok := proofFromContext(ctx); !ok && !harnessTURN() {
		return turnCredentials{}, errNoTURN
	}
	return fetchCredentials(ctx, rawURL)
}

func proofFromContext(ctx context.Context) (credentialProof, bool) {
	if ctx == nil {
		return credentialProof{}, false
	}
	proof, ok := ctx.Value(proofContextKey{}).(credentialProof)
	if !ok || proof.sessionID == ([32]byte{}) || proof.capability == ([32]byte{}) {
		return credentialProof{}, false
	}
	return proof, true
}

func credentialRequestBody(ctx context.Context) []byte {
	if proof, ok := proofFromContext(ctx); ok {
		digest := sha256.Sum256(append([]byte(turnProofDomain), proof.capability[:]...))
		body, err := json.Marshal(map[string]string{
			"session_id":   hex.EncodeToString(proof.sessionID[:]),
			"invite_token": base64.RawURLEncoding.EncodeToString(digest[:]),
		})
		if err == nil {
			return body
		}
	}
	if harnessTURN() {
		body, err := json.Marshal(map[string]string{
			"session_id":   "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
			"invite_token": strings.Repeat("A", 43),
		})
		if err == nil {
			return body
		}
	}
	return nil
}

func harnessTURN() bool {
	return strings.TrimSpace(os.Getenv(testHarnessEnv)) == "1"
}

func mintHarnessCredentials() (turnCredentials, error) {
	server := strings.TrimSpace(os.Getenv(testTURNServerEnv))
	secret := strings.TrimSpace(os.Getenv(testTURNSecretEnv))
	if server == "" || secret == "" {
		return turnCredentials{}, errNoTURN
	}
	if _, _, err := net.SplitHostPort(server); err != nil {
		return turnCredentials{}, errNoTURN
	}
	username, password, err := turn.GenerateLongTermTURNRESTCredentials(secret, turnRESTUser, credentialTTL)
	if err != nil {
		return turnCredentials{}, err
	}
	ip, err := resolveTURNIP(server)
	if err != nil {
		return turnCredentials{}, err
	}
	return turnCredentials{
		server: server, username: username, password: password,
		quota: uint64(limits.MaxRelaySessionBytes), serverIP: ip,
		allowed: map[netip.Addr]struct{}{ip: {}},
	}, nil
}

func fetchCredentials(ctx context.Context, rawURL string) (turnCredentials, error) {
	parsed, err := url.Parse(rawURL)
	if err != nil || parsed.Host == "" {
		return turnCredentials{}, errNoTURN
	}
	switch parsed.Scheme {
	case "https":
	case "http":
		if !harnessTURN() {
			return turnCredentials{}, errNoTURN
		}
	default:
		return turnCredentials{}, errNoTURN
	}
	payload := credentialRequestBody(ctx)
	if len(payload) == 0 {
		return turnCredentials{}, errNoTURN
	}
	fetchCtx, cancel := context.WithTimeout(ctx, credentialFetch)
	defer cancel()
	request, err := http.NewRequestWithContext(
		fetchCtx, http.MethodPost, parsed.String(), bytes.NewReader(append([]byte(nil), payload...)),
	)
	clear(payload)
	if err != nil {
		return turnCredentials{}, errNoTURN
	}
	request.Header.Set("Accept", "application/json")
	request.Header.Set("Content-Type", "application/json")
	client := &http.Client{
		Timeout: credentialFetch,
		Transport: &http.Transport{
			TLSClientConfig: &tls.Config{MinVersion: tls.VersionTLS12},
		},
		CheckRedirect: func(req *http.Request, via []*http.Request) error {
			if req.URL.Scheme != "https" && !harnessTURN() {
				return errNoTURN
			}
			if len(via) >= 2 {
				return errNoTURN
			}
			return nil
		},
	}
	response, err := client.Do(request)
	if err != nil {
		return turnCredentials{}, errNoTURN
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return turnCredentials{}, errNoTURN
	}
	limited := io.LimitReader(response.Body, maxCredentialBody+1)
	body, err := io.ReadAll(limited)
	if err != nil || len(body) == 0 || len(body) > maxCredentialBody {
		return turnCredentials{}, errNoTURN
	}
	creds, err := parseCredentialJSON(body)
	clear(body)
	return creds, err
}

type credentialDocument struct {
	URLs         any                  `json:"urls"`
	Username     string               `json:"username"`
	Credential   string               `json:"credential"`
	TTL          int                  `json:"ttl"`
	QuotaBytes   uint64               `json:"quota_bytes"`
	AllowedRelay []string             `json:"allowed_relay_ips"`
	ICEServers   []credentialDocument `json:"iceServers"`
}

func parseCredentialJSON(body []byte) (turnCredentials, error) {
	var document credentialDocument
	if err := json.Unmarshal(body, &document); err != nil {
		return turnCredentials{}, errNoTURN
	}
	if len(document.ICEServers) > 0 {
		for _, server := range document.ICEServers {
			if strings.TrimSpace(server.Username) == "" || strings.TrimSpace(server.Credential) == "" {
				continue
			}
			if document.QuotaBytes > 0 && server.QuotaBytes == 0 {
				server.QuotaBytes = document.QuotaBytes
			}
			if len(document.AllowedRelay) > 0 && len(server.AllowedRelay) == 0 {
				server.AllowedRelay = document.AllowedRelay
			}
			parsed, err := credentialsFromDocument(server)
			if err == nil {
				return parsed, nil
			}
		}
		return turnCredentials{}, errNoTURN
	}
	return credentialsFromDocument(document)
}

func credentialsFromDocument(document credentialDocument) (turnCredentials, error) {
	username := strings.TrimSpace(document.Username)
	password := strings.TrimSpace(document.Credential)
	if username == "" || password == "" {
		return turnCredentials{}, errNoTURN
	}
	server, ip, err := firstTURNServer(document.URLs)
	if err != nil {
		return turnCredentials{}, err
	}
	quota := uint64(limits.MaxRelaySessionBytes)
	if document.QuotaBytes > 0 && document.QuotaBytes < quota {
		quota = document.QuotaBytes
	}
	allowed := map[netip.Addr]struct{}{ip: {}}
	for _, encoded := range document.AllowedRelay {
		address, parseErr := netip.ParseAddr(strings.TrimSpace(encoded))
		if parseErr != nil {
			continue
		}
		address = address.Unmap()
		if stunMappedIPv4(address) {
			allowed[address] = struct{}{}
		}
	}
	return turnCredentials{
		server: server, username: username, password: password,
		quota: quota, serverIP: ip, allowed: allowed,
	}, nil
}

func firstTURNServer(raw any) (string, netip.Addr, error) {
	var urls []string
	switch value := raw.(type) {
	case string:
		if strings.TrimSpace(value) != "" {
			urls = []string{value}
		}
	case []any:
		for _, item := range value {
			text, ok := item.(string)
			if ok && strings.TrimSpace(text) != "" {
				urls = append(urls, text)
			}
		}
	case []string:
		urls = value
	}
	for _, item := range urls {
		hostPort, ok := turnUDPHostPort(item)
		if ok {
			// The allocator uses udp4. An unsupported address family in a
			// provider's URL list must not hide a later usable TURN endpoint.
			if ip, err := resolveTURNIP(hostPort); err == nil {
				return hostPort, ip, nil
			}
		}
	}
	return "", netip.Addr{}, errNoTURN
}

func turnUDPHostPort(raw string) (string, bool) {
	item := strings.TrimSpace(raw)
	if item == "" {
		return "", false
	}
	lower := strings.ToLower(item)
	switch {
	case strings.HasPrefix(lower, "turns:"):
		return "", false
	case strings.HasPrefix(lower, "turn://"):
		item = item[len("turn://"):]
	case strings.HasPrefix(lower, "turn:"):
		item = item[len("turn:"):]
	}
	host, query, _ := strings.Cut(item, "?")
	if query != "" {
		values, err := url.ParseQuery(query)
		if err != nil {
			return "", false
		}
		if transport := strings.ToLower(values.Get("transport")); transport != "" && transport != "udp" {
			return "", false
		}
	}
	host = strings.TrimSpace(host)
	if host == "" {
		return "", false
	}
	if _, _, err := net.SplitHostPort(host); err != nil {
		host = net.JoinHostPort(host, "3478")
	}
	if _, _, err := net.SplitHostPort(host); err != nil {
		return "", false
	}
	return host, true
}

func resolveTURNIP(server string) (netip.Addr, error) {
	host, _, err := net.SplitHostPort(server)
	if err != nil {
		return netip.Addr{}, errNoTURN
	}
	if ip, err := netip.ParseAddr(host); err == nil {
		ip = ip.Unmap()
		if stunMappedIPv4(ip) || (harnessTURN() && ip.Is4() && ip.IsLoopback()) {
			return ip, nil
		}
		return netip.Addr{}, errNoTURN
	}
	addrs, err := net.LookupIP(host)
	if err != nil {
		return netip.Addr{}, errNoTURN
	}
	for _, ip := range addrs {
		address, ok := netip.AddrFromSlice(ip)
		if !ok {
			continue
		}
		address = address.Unmap()
		if stunMappedIPv4(address) {
			return address, nil
		}
	}
	return netip.Addr{}, errNoTURN
}

type quotaConn struct {
	inner net.PacketConn
	limit uint64
	bytes atomic.Uint64
}

func newQuotaConn(inner net.PacketConn, limit uint64) *quotaConn {
	if limit == 0 || limit > uint64(limits.MaxRelaySessionBytes) {
		limit = uint64(limits.MaxRelaySessionBytes)
	}
	return &quotaConn{inner: inner, limit: limit}
}

func (c *quotaConn) account(n int) error {
	if n <= 0 {
		return nil
	}
	total := c.bytes.Add(uint64(n))
	if total > c.limit {
		return ErrRelayQuota
	}
	return nil
}

func (c *quotaConn) ReadFrom(payload []byte) (int, net.Addr, error) {
	n, addr, err := c.inner.ReadFrom(payload)
	if n > 0 {
		if quotaErr := c.account(n); quotaErr != nil {
			_ = c.inner.Close()
			return n, addr, quotaErr
		}
	}
	return n, addr, err
}

func (c *quotaConn) WriteTo(payload []byte, peer net.Addr) (int, error) {
	if err := c.account(len(payload)); err != nil {
		_ = c.inner.Close()
		return 0, err
	}
	return c.inner.WriteTo(payload, peer)
}

func (c *quotaConn) Close() error                      { return c.inner.Close() }
func (c *quotaConn) LocalAddr() net.Addr               { return c.inner.LocalAddr() }
func (c *quotaConn) SetDeadline(t time.Time) error     { return c.inner.SetDeadline(t) }
func (c *quotaConn) SetReadDeadline(t time.Time) error { return c.inner.SetReadDeadline(t) }
func (c *quotaConn) SetWriteDeadline(t time.Time) error {
	return c.inner.SetWriteDeadline(t)
}

var _ net.PacketConn = (*quotaConn)(nil)
