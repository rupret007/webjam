package webjoin

import (
	"bytes"
	"context"
	"crypto/hkdf"
	"crypto/sha256"
	"crypto/tls"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"io"
	"net/http"
	"net/netip"
	"net/url"
	"strings"
	"time"

	"github.com/pion/stun/v3"
	"github.com/pion/webrtc/v4"
)

// This build-time endpoint is never accepted from IPC, a browser, environment,
// or an invite. Credentials exist only in memory. No redirects carry the proof.
var credentialURL = "https://webjam-turn-credentials.jeffstory007.workers.dev/turn-credentials"
var providerRelayIPs = []string{"141.101.90.1", "162.159.207.1"}

type ICEConfig struct {
	Servers               []webrtc.ICEServer
	AllowedRelayAddresses []string
	ExpiresAt             time.Time
	QuotaBytes            uint64
}

func (c ICEConfig) validate(now time.Time) error {
	if len(c.Servers) == 0 || len(c.Servers) > 8 || !c.ExpiresAt.After(now) || c.ExpiresAt.After(now.Add(time.Hour)) || c.QuotaBytes == 0 || c.QuotaBytes > MaxSessionBytes {
		return ErrICE
	}
	if len(c.AllowedRelayAddresses) == 0 || len(c.AllowedRelayAddresses) > 2 {
		return ErrICE
	}
	seen := map[string]bool{}
	for _, raw := range c.AllowedRelayAddresses {
		if seen[raw] || (raw != providerRelayIPs[0] && raw != providerRelayIPs[1]) {
			return ErrICE
		}
		seen[raw] = true
		ip, err := netip.ParseAddr(raw)
		if err != nil || !publicIP(ip) {
			return ErrICE
		}
	}
	turn := false
	urls := 0
	for _, server := range c.Servers {
		if len(server.URLs) == 0 || len(server.URLs) > 8 {
			return ErrICE
		}
		for _, raw := range server.URLs {
			urls++
			if urls > 12 {
				return ErrICE
			}
			uri, err := stun.ParseURI(raw)
			if err != nil || uri.Port < 1 || uri.Port > 65535 {
				return ErrICE
			}
			switch uri.Scheme {
			case stun.SchemeTypeSTUN:
				if (uri.Host != "stun.cloudflare.com" && uri.Host != "turn.cloudflare.com") || uri.Proto != stun.ProtoTypeUDP {
					return ErrICE
				}
			case stun.SchemeTypeTURN, stun.SchemeTypeTURNS:
				if uri.Host != "turn.cloudflare.com" {
					return ErrICE
				}
				credential, ok := server.Credential.(string)
				if !ok || len(credential) == 0 || len(credential) > 1024 || len(server.Username) == 0 || len(server.Username) > 512 {
					return ErrICE
				}
				if uri.Proto != stun.ProtoTypeUDP && uri.Proto != stun.ProtoTypeTCP {
					return ErrICE
				}
				turn = true
			default:
				return ErrICE
			}
		}
	}
	if !turn {
		return ErrICE
	}
	return nil
}

// FetchICE always checks the Worker's free web quota, including for direct ICE.
// Any timeout, unreadable usage, cap response, invalid service or old unscoped
// Worker response fails closed. There is no direct-mode quota bypass.
func FetchICE(ctx context.Context, session, capability [32]byte) (ICEConfig, error) {
	parsed, err := url.Parse(credentialURL)
	if err != nil || parsed.Scheme != "https" || parsed.Host == "" || parsed.User != nil || parsed.RawQuery != "" || parsed.Fragment != "" || parsed.Path != "/turn-credentials" || session == ([32]byte{}) || capability == ([32]byte{}) {
		return ICEConfig{}, ErrICE
	}
	client := &http.Client{Timeout: 12 * time.Second, Transport: &http.Transport{TLSClientConfig: &tls.Config{MinVersion: tls.VersionTLS12}}, CheckRedirect: func(*http.Request, []*http.Request) error { return ErrICE }}
	defer client.CloseIdleConnections()
	return fetchICE(ctx, client, parsed.String(), session, capability)
}
func fetchICE(ctx context.Context, client *http.Client, endpoint string, session, capability [32]byte) (ICEConfig, error) {
	ctx, cancel := context.WithTimeout(ctx, 12*time.Second)
	defer cancel()
	token, keyErr := hkdf.Key(sha256.New, capability[:], session[:], Domain+"/turn-token", 32)
	if keyErr != nil {
		return ICEConfig{}, ErrICE
	}
	defer clear(token)
	body, _ := json.Marshal(map[string]string{"session_id": hex.EncodeToString(session[:]), "invite_token": base64.RawURLEncoding.EncodeToString(token), "client": "web"})
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, endpoint, bytes.NewReader(body))
	if err != nil {
		return ICEConfig{}, ErrICE
	}
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("Accept", "application/json")
	response, err := client.Do(request)
	if err != nil {
		return ICEConfig{}, ErrICE
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return ICEConfig{}, ErrICE
	}
	raw, err := io.ReadAll(io.LimitReader(response.Body, 16385))
	if err != nil || len(raw) > 16384 {
		return ICEConfig{}, ErrICE
	}
	defer clear(raw)
	var document struct {
		Client  string             `json:"client"`
		Servers []webrtc.ICEServer `json:"iceServers"`
		TTL     int                `json:"ttl"`
		Quota   uint64             `json:"quota_bytes"`
		Relays  []string           `json:"allowed_relay_ips"`
	}
	if json.Unmarshal(raw, &document) != nil || document.Client != "web" || document.TTL <= 0 || document.TTL > 3600 {
		return ICEConfig{}, ErrICE
	}
	if len(document.Relays) == 0 {
		document.Relays = append([]string(nil), providerRelayIPs...)
	}
	for _, s := range document.Servers {
		for _, u := range s.URLs {
			if strings.TrimSpace(u) != u {
				return ICEConfig{}, ErrICE
			}
		}
	}
	result := ICEConfig{Servers: document.Servers, AllowedRelayAddresses: document.Relays, ExpiresAt: time.Now().Add(time.Duration(document.TTL) * time.Second), QuotaBytes: document.Quota}
	if err = result.validate(time.Now()); err != nil {
		return ICEConfig{}, err
	}
	return result, nil
}
