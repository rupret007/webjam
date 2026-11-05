// Package directstun gathers host listen addresses for the ordinary Internet
// Host/Join profile. Candidates are sealed by signaling.Bundle; this package
// never puts an address in a v3 URL.
package directstun

import (
	"context"
	"net"
	"net/netip"
	"os"
	"strings"
	"time"

	"github.com/pion/ice/v4"
	"github.com/pion/stun/v3"
	"github.com/rupret007/webjam/transport/internal/limits"
)

const (
	stunAttemptLimit = 2 * time.Second
)

// Compiled public STUN servers. No account, no WebJam VM. Packaged Host/Join
// always uses this list. A test-only override is read only when
// WEBJAM_TEST_HARNESS=1 (never in frozen builds; see stunServerList).
var stunServers = []string{
	"stun.cloudflare.com:3478",
	"stun.l.google.com:19302",
}

const (
	testHarnessEnv = "WEBJAM_TEST_HARNESS"
	testSTUNEnv    = "WEBJAM_TEST_STUN_SERVER"
)

var nonPublicAddressPrefixes = []netip.Prefix{
	netip.MustParsePrefix("0.0.0.0/8"),
	netip.MustParsePrefix("100.64.0.0/10"),
	netip.MustParsePrefix("192.0.0.0/24"),
	netip.MustParsePrefix("192.0.2.0/24"),
	netip.MustParsePrefix("192.88.99.0/24"),
	netip.MustParsePrefix("198.18.0.0/15"),
	netip.MustParsePrefix("198.51.100.0/24"),
	netip.MustParsePrefix("203.0.113.0/24"),
	netip.MustParsePrefix("240.0.0.0/4"),
}

// Gather returns ICE-encoded UDP candidates for conn's local port: RFC1918
// host candidates and, when STUN succeeds, one server-reflexive mapping.
func Gather(ctx context.Context, conn *net.UDPConn) (candidates []string, public bool, err error) {
	if ctx == nil || conn == nil {
		return nil, false, errNoAddress
	}
	local, ok := conn.LocalAddr().(*net.UDPAddr)
	if !ok || local.Port < 1 {
		return nil, false, errNoAddress
	}
	seen := make(map[string]struct{})
	for _, address := range privateIPv4s() {
		encoded, encodeErr := hostCandidate(address, local.Port)
		if encodeErr != nil {
			continue
		}
		if _, exists := seen[encoded]; exists {
			continue
		}
		seen[encoded] = struct{}{}
		candidates = append(candidates, encoded)
	}
	mapped, stunErr := serverReflexive(ctx, conn)
	if stunErr == nil && stunMappedIPv4(mapped.Addr()) {
		encoded, encodeErr := srflxCandidate(mapped, local.Port)
		if encodeErr == nil {
			if _, exists := seen[encoded]; !exists {
				seen[encoded] = struct{}{}
				candidates = append(candidates, encoded)
				// public means STUN returned a mapping, including lab TEST-NET
				// ranges. Do not classify srflx by reserved address prefixes.
				public = true
			}
		}
	}
	if len(candidates) == 0 || len(candidates) > limits.MaxCandidateCount {
		return nil, false, errNoAddress
	}
	return candidates, public, nil
}

// ParseDialTarget accepts one sealed ICE candidate string and returns the
// UDP address a peer may probe. Host candidates must be RFC1918 IPv4. Server-
// reflexive candidates are accepted because STUN returned them, including
// documentation/test ranges used by the two-NAT harness.
func ParseDialTarget(encoded string) (*net.UDPAddr, error) {
	candidate, err := ice.UnmarshalCandidate(encoded)
	if err != nil || candidate == nil || candidate.Component() != 1 {
		return nil, errRejectedCandidate
	}
	address, err := netip.ParseAddr(candidate.Address())
	if err != nil {
		return nil, errRejectedCandidate
	}
	address = address.Unmap()
	if !address.Is4() || candidate.Port() < 1 || candidate.Port() > 65_535 {
		return nil, errRejectedCandidate
	}
	switch candidate.Type() {
	case ice.CandidateTypeHost:
		if !address.IsPrivate() || address.IsLoopback() {
			return nil, errRejectedCandidate
		}
	case ice.CandidateTypeServerReflexive:
		if !stunMappedIPv4(address) {
			return nil, errRejectedCandidate
		}
	default:
		return nil, errRejectedCandidate
	}
	return &net.UDPAddr{IP: net.IP(address.AsSlice()), Port: candidate.Port()}, nil
}

// ParseRelayTarget accepts a sealed relay candidate only when its IPv4 equals
// the credentialed TURN server. That is the exact-pair bound: CreatePermission
// and QUIC dial use this address, never an arbitrary peer-supplied relay.
func relayAllowedIPv4(address netip.Addr) bool {
	if stunMappedIPv4(address) {
		return true
	}
	return harnessTURN() && address.Is4() && address.IsLoopback()
}

func ParseRelayTarget(encoded string, allowed netip.Addr) (*net.UDPAddr, error) {
	if !relayAllowedIPv4(allowed) {
		return nil, errRejectedCandidate
	}
	candidate, err := ice.UnmarshalCandidate(encoded)
	if err != nil || candidate == nil || candidate.Component() != 1 {
		return nil, errRejectedCandidate
	}
	if candidate.Type() != ice.CandidateTypeRelay {
		return nil, errRejectedCandidate
	}
	address, err := netip.ParseAddr(candidate.Address())
	if err != nil {
		return nil, errRejectedCandidate
	}
	address = address.Unmap()
	if !relayAllowedIPv4(address) || address != allowed.Unmap() ||
		candidate.Port() < 1 || candidate.Port() > 65_535 {
		return nil, errRejectedCandidate
	}
	return &net.UDPAddr{IP: net.IP(address.AsSlice()), Port: candidate.Port()}, nil
}

func privateIPv4s() []netip.Addr {
	addrs, err := net.InterfaceAddrs()
	if err != nil {
		return nil
	}
	var result []netip.Addr
	for _, network := range addrs {
		ipNet, ok := network.(*net.IPNet)
		if !ok || ipNet.IP == nil {
			continue
		}
		address, ok := netip.AddrFromSlice(ipNet.IP.To4())
		if !ok {
			continue
		}
		address = address.Unmap()
		if address.Is4() && address.IsPrivate() && !address.IsLoopback() {
			result = append(result, address)
		}
	}
	return result
}

func hostCandidate(address netip.Addr, port int) (string, error) {
	candidate, err := ice.NewCandidateHost(&ice.CandidateHostConfig{
		Network:   "udp",
		Address:   address.String(),
		Port:      port,
		Component: 1,
	})
	if err != nil {
		return "", err
	}
	return candidate.Marshal(), nil
}

func relayCandidate(relayed netip.AddrPort, relatedPort int) (string, error) {
	candidate, err := ice.NewCandidateRelay(&ice.CandidateRelayConfig{
		Network:   "udp",
		Address:   relayed.Addr().String(),
		Port:      int(relayed.Port()),
		Component: 1,
		RelAddr:   "0.0.0.0",
		RelPort:   relatedPort,
	})
	if err != nil {
		return "", err
	}
	return candidate.Marshal(), nil
}

func srflxCandidate(mapped netip.AddrPort, relatedPort int) (string, error) {
	candidate, err := ice.NewCandidateServerReflexive(&ice.CandidateServerReflexiveConfig{
		Network:   "udp",
		Address:   mapped.Addr().String(),
		Port:      int(mapped.Port()),
		Component: 1,
		RelAddr:   "0.0.0.0",
		RelPort:   relatedPort,
	})
	if err != nil {
		return "", err
	}
	return candidate.Marshal(), nil
}

func serverReflexive(ctx context.Context, conn *net.UDPConn) (netip.AddrPort, error) {
	for _, server := range stunServerList() {
		mapped, err := stunBinding(ctx, conn, server)
		if err == nil {
			return mapped, nil
		}
	}
	return netip.AddrPort{}, errNoAddress
}

func stunServerList() []string {
	if strings.TrimSpace(os.Getenv(testHarnessEnv)) != "1" {
		return stunServers
	}
	override := strings.TrimSpace(os.Getenv(testSTUNEnv))
	if override == "" {
		return stunServers
	}
	if _, _, err := net.SplitHostPort(override); err != nil {
		return stunServers
	}
	return []string{override}
}

func resolveSTUNServer(ctx context.Context, server string) (*net.UDPAddr, error) {
	if ctx.Err() != nil {
		return nil, ctx.Err()
	}
	host, port, err := net.SplitHostPort(server)
	if err != nil {
		return nil, err
	}
	portNum, err := net.LookupPort("udp", port)
	if err != nil || portNum < 1 {
		return nil, errNoAddress
	}
	if ip := net.ParseIP(host); ip != nil {
		v4 := ip.To4()
		if v4 == nil {
			return nil, errNoAddress
		}
		return &net.UDPAddr{IP: v4, Port: portNum}, nil
	}
	lookupCtx, cancel := context.WithTimeout(ctx, stunAttemptLimit)
	defer cancel()
	records, err := net.DefaultResolver.LookupIPAddr(lookupCtx, host)
	if err != nil {
		return nil, err
	}
	for _, record := range records {
		v4 := record.IP.To4()
		if v4 == nil {
			continue
		}
		return &net.UDPAddr{IP: v4, Port: portNum}, nil
	}
	return nil, errNoAddress
}

func stunBinding(ctx context.Context, conn *net.UDPConn, server string) (netip.AddrPort, error) {
	if ctx.Err() != nil {
		return netip.AddrPort{}, ctx.Err()
	}
	resolved, err := resolveSTUNServer(ctx, server)
	if err != nil {
		return netip.AddrPort{}, err
	}
	message, err := stun.Build(stun.TransactionID, stun.BindingRequest)
	if err != nil {
		return netip.AddrPort{}, err
	}
	if err = conn.SetDeadline(time.Now().Add(stunAttemptLimit)); err != nil {
		return netip.AddrPort{}, err
	}
	defer conn.SetDeadline(time.Time{})
	if _, err = conn.WriteToUDP(message.Raw, resolved); err != nil {
		return netip.AddrPort{}, err
	}
	buffer := make([]byte, 1500)
	for {
		if ctx.Err() != nil {
			return netip.AddrPort{}, ctx.Err()
		}
		n, _, readErr := conn.ReadFromUDP(buffer)
		if readErr != nil {
			return netip.AddrPort{}, readErr
		}
		response := new(stun.Message)
		response.Raw = append([]byte(nil), buffer[:n]...)
		if err = response.Decode(); err != nil {
			continue
		}
		if response.Type != stun.BindingSuccess {
			continue
		}
		var xor stun.XORMappedAddress
		if err = xor.GetFrom(response); err != nil {
			var mapped stun.MappedAddress
			if mappedErr := mapped.GetFrom(response); mappedErr != nil {
				continue
			}
			address, ok := netip.AddrFromSlice(mapped.IP)
			if !ok {
				continue
			}
			return netip.AddrPortFrom(address.Unmap(), uint16(mapped.Port)), nil
		}
		address, ok := netip.AddrFromSlice(xor.IP)
		if !ok {
			continue
		}
		return netip.AddrPortFrom(address.Unmap(), uint16(xor.Port)), nil
	}
}

func stunMappedIPv4(address netip.Addr) bool {
	return address.IsValid() && address.Is4() && !address.IsUnspecified() &&
		!address.IsLoopback() && !address.IsMulticast() && !address.IsLinkLocalUnicast()
}

// publicUnicastIPv4 is the real-world public unicast check (documentation
// prefixes excluded). Gather/ParseDialTarget do not use it to drop a STUN
// mapping; STUN success classifies srflx.
func publicUnicastIPv4(address netip.Addr) bool {
	if !stunMappedIPv4(address) || !address.IsGlobalUnicast() || address.IsPrivate() {
		return false
	}
	for _, prefix := range nonPublicAddressPrefixes {
		if prefix.Contains(address) {
			return false
		}
	}
	return true
}

func LoopbackCandidate(port int) (string, error) {
	return hostCandidate(netip.MustParseAddr("127.0.0.1"), port)
}

func ParseLoopbackDialTarget(encoded string) (*net.UDPAddr, error) {
	candidate, err := ice.UnmarshalCandidate(encoded)
	if err != nil || candidate == nil {
		return nil, errRejectedCandidate
	}
	if candidate.Address() != "127.0.0.1" || candidate.Port() < 1 {
		return nil, errRejectedCandidate
	}
	return &net.UDPAddr{IP: net.IPv4(127, 0, 0, 1), Port: candidate.Port()}, nil
}

var (
	errNoAddress         = errString("direct-stun has no reachable UDP address")
	errRejectedCandidate = errString("direct-stun candidate rejected")
	ErrUnreachable       = errString("direct-stun could not reach the peer")
)

type errString string

func (e errString) Error() string { return string(e) }
