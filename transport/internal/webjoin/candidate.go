package webjoin

import (
	"encoding/hex"
	"net/netip"
	"strings"

	"github.com/pion/ice/v4"
	"github.com/pion/sdp/v3"
)

var forbiddenPrefixes = []netip.Prefix{
	netip.MustParsePrefix("0.0.0.0/8"), netip.MustParsePrefix("100.64.0.0/10"), netip.MustParsePrefix("192.0.0.0/24"), netip.MustParsePrefix("192.0.2.0/24"), netip.MustParsePrefix("192.88.99.0/24"), netip.MustParsePrefix("198.18.0.0/15"), netip.MustParsePrefix("198.51.100.0/24"), netip.MustParsePrefix("203.0.113.0/24"), netip.MustParsePrefix("240.0.0.0/4"), netip.MustParsePrefix("2001:2::/48"), netip.MustParsePrefix("2001:db8::/32"), netip.MustParsePrefix("3fff::/20"),
}

func publicIP(ip netip.Addr) bool {
	ip = ip.Unmap()
	if !ip.IsValid() || ip.Zone() != "" || !ip.IsGlobalUnicast() || ip.IsPrivate() || ip.IsLoopback() || ip.IsLinkLocalUnicast() {
		return false
	}
	for _, prefix := range forbiddenPrefixes {
		if prefix.Contains(ip) {
			return false
		}
	}
	return true
}
func candidateAllowed(candidate ice.Candidate, relays map[netip.Addr]bool, lab bool) bool {
	if candidate == nil || candidate.Component() != 1 || candidate.Port() < 1 || candidate.Port() > 65535 || !candidate.NetworkType().IsUDP() {
		return false
	}
	ip, err := netip.ParseAddr(candidate.Address())
	if err != nil {
		return false
	}
	ip = ip.Unmap()
	if lab && (ip.IsLoopback() || ip.IsPrivate()) && (candidate.Type() == ice.CandidateTypeHost || candidate.Type() == ice.CandidateTypePeerReflexive || (candidate.Type() == ice.CandidateTypeRelay && relays[ip])) {
		return true
	}
	if !publicIP(ip) {
		return false
	}
	if candidate.Type() == ice.CandidateTypeRelay {
		return relays[ip]
	}
	return candidate.Type() == ice.CandidateTypeServerReflexive
}
func parseFingerprint(value string) (string, error) {
	parts := strings.Fields(value)
	if len(parts) != 2 || strings.ToLower(parts[0]) != "sha-256" {
		return "", ErrPin
	}
	pin := strings.ToLower(strings.ReplaceAll(parts[1], ":", ""))
	if !validHex(pin, 32) {
		return "", ErrPin
	}
	return pin, nil
}

// validateSDP checks every fingerprint, media section and candidate before Pion
// sees the SDP. Local filtering strips ineligible candidates (including private
// srflx returned by a broken STUN server); remote disallowed candidates reject
// the entire authenticated answer.
func validateSDP(raw, pin string, relays map[netip.Addr]bool, local, lab bool) (string, error) {
	var parsed sdp.SessionDescription
	if len(raw) > MaxEnvelopeBytes || parsed.Unmarshal([]byte(raw)) != nil {
		return "", ErrSignal
	}
	seenPin := false
	count := 0
	filter := func(attrs []sdp.Attribute) ([]sdp.Attribute, error) {
		result := make([]sdp.Attribute, 0, len(attrs))
		for _, attr := range attrs {
			switch attr.Key {
			case "fingerprint":
				actual, err := parseFingerprint(attr.Value)
				if err != nil || actual != pin {
					return nil, ErrPin
				}
				seenPin = true
			case "candidate":
				candidate, err := ice.UnmarshalCandidate(attr.Value)
				if err != nil || !candidateAllowed(candidate, relays, lab) {
					if local {
						continue
					}
					return nil, ErrCandidate
				}
				if local && !lab {
					fields := strings.Fields(attr.Value)
					for i := 8; i+1 < len(fields); i += 2 {
						if fields[i] == "raddr" {
							fields[i+1] = "0.0.0.0"
						}
						if fields[i] == "rport" {
							fields[i+1] = "0"
						}
					}
					attr.Value = strings.Join(fields, " ")
				}
				count++
				if count > 32 {
					return nil, ErrCandidate
				}
			case "ice-options":
				// One sealed offer/answer contains complete ICE gathering. Trickle and
				// renegotiation are deliberately unavailable across this IPC boundary.
				continue
			}
			result = append(result, attr)
		}
		return result, nil
	}
	var err error
	parsed.Attributes, err = filter(parsed.Attributes)
	if err != nil {
		return "", err
	}
	if local && !lab {
		parsed.Origin.UnicastAddress = "0.0.0.0"
		if parsed.ConnectionInformation != nil {
			parsed.ConnectionInformation.AddressType = "IP4"
			parsed.ConnectionInformation.Address = &sdp.Address{Address: "0.0.0.0"}
		}
	}
	audio, application := 0, 0
	for _, media := range parsed.MediaDescriptions {
		if local && !lab && media.ConnectionInformation != nil {
			media.ConnectionInformation.AddressType = "IP4"
			media.ConnectionInformation.Address = &sdp.Address{Address: "0.0.0.0"}
		}
		switch media.MediaName.Media {
		case "audio":
			audio++
		case "application":
			application++
		default:
			return "", ErrSignal
		}
		media.Attributes, err = filter(media.Attributes)
		if err != nil {
			return "", err
		}
	}
	if !seenPin || count == 0 || audio != 1 || application != 1 {
		return "", ErrSignal
	}
	encoded, err := parsed.Marshal()
	if err != nil {
		return "", ErrSignal
	}
	return string(encoded), nil
}
func pinBytes(pin string) [32]byte {
	var out [32]byte
	raw, _ := hex.DecodeString(pin)
	copy(out[:], raw)
	return out
}
