package icequic

import (
	"context"
	"errors"
	"net/netip"
	"sync"
	"time"

	"github.com/pion/ice/v4"
	"github.com/pion/logging"
	"github.com/pion/stun/v3"
	"github.com/pion/transport/v4"
	"github.com/rupret007/webjam/transport/internal/limits"
	"github.com/rupret007/webjam/transport/internal/signaling"
)

var (
	ErrICEAgent     = errors.New("ICE agent operation failed")
	ErrICEGather    = errors.New("ICE candidate gathering failed")
	ErrICECandidate = errors.New("authenticated ICE candidate rejected")
	ErrICEConnect   = errors.New("ICE connection failed")
	ErrICEState     = errors.New("invalid ICE agent state")
)

type AgentConfig struct {
	URLs           []*stun.URI
	CandidateTypes []ice.CandidateType
	NetworkTypes   []ice.NetworkType
	// AllowedRelayAddresses is the fixed public relay-IP set shipped by the
	// selected profile. A relay candidate must match it exactly.
	AllowedRelayAddresses []string
	Net                   transport.Net
}

// Agent hides Pion's raw AddRemoteCandidate API. Remote candidates can enter
// the agent only through OpenAuthenticatedRemote after AEAD, context, expiry,
// and replay validation succeeds.
type Agent struct {
	inner               *ice.Agent
	mu                  sync.Mutex
	gathered            bool
	remote              *signaling.Bundle
	closed              bool
	allowedCandidates   map[ice.CandidateType]struct{}
	allowedNetworkTypes map[ice.NetworkType]struct{}
	allowedRelays       map[netip.Addr]struct{}
}

func NewAgent(config AgentConfig) (*Agent, error) {
	if err := validateAgentConfig(config); err != nil {
		return nil, ErrICEAgent
	}
	if len(config.NetworkTypes) == 0 {
		config.NetworkTypes = []ice.NetworkType{ice.NetworkTypeUDP4}
	}
	logger := logging.NewDefaultLoggerFactory()
	logger.DefaultLogLevel = logging.LogLevelDisabled
	inner, err := ice.NewAgent(&ice.AgentConfig{
		Urls: config.URLs, CandidateTypes: config.CandidateTypes,
		NetworkTypes: config.NetworkTypes, MulticastDNSMode: ice.MulticastDNSModeDisabled,
		Net: config.Net, LoggerFactory: logger,
	})
	if err != nil {
		return nil, ErrICEAgent
	}
	allowedCandidates := make(map[ice.CandidateType]struct{}, len(config.CandidateTypes))
	for _, candidateType := range config.CandidateTypes {
		allowedCandidates[candidateType] = struct{}{}
	}
	allowedNetworks := make(map[ice.NetworkType]struct{}, len(config.NetworkTypes))
	for _, networkType := range config.NetworkTypes {
		allowedNetworks[networkType] = struct{}{}
	}
	allowedRelays := make(map[netip.Addr]struct{}, len(config.AllowedRelayAddresses))
	for _, encoded := range config.AllowedRelayAddresses {
		address, _ := netip.ParseAddr(encoded)
		allowedRelays[address.Unmap()] = struct{}{}
	}
	return &Agent{
		inner: inner, allowedCandidates: allowedCandidates, allowedNetworkTypes: allowedNetworks,
		allowedRelays: allowedRelays,
	}, nil
}

func (a *Agent) GatherSealed(
	ctx context.Context,
	capability signaling.Capability,
	template signaling.Bundle,
) ([]byte, error) {
	a.mu.Lock()
	if a.closed || a.gathered || a.remote != nil || len(template.Candidates) != 0 || template.ICEUfrag != "" || template.ICEPassword != "" {
		a.mu.Unlock()
		return nil, ErrICEState
	}
	a.gathered = true
	a.mu.Unlock()

	done := make(chan struct{})
	var once sync.Once
	if err := a.inner.OnCandidate(func(candidate ice.Candidate) {
		if candidate == nil {
			once.Do(func() { close(done) })
		}
	}); err != nil {
		return nil, ErrICEGather
	}
	if err := a.inner.GatherCandidates(); err != nil {
		return nil, ErrICEGather
	}
	select {
	case <-ctx.Done():
		return nil, ErrICEGather
	case <-done:
	}
	ufrag, password, err := a.inner.GetLocalUserCredentials()
	if err != nil {
		return nil, ErrICEGather
	}
	candidates, err := a.inner.GetLocalCandidates()
	if err != nil || len(candidates) == 0 {
		return nil, ErrICEGather
	}
	template.ICEUfrag = ufrag
	template.ICEPassword = password
	template.Candidates = make([]string, 0, len(candidates))
	for _, candidate := range candidates {
		template.Candidates = append(template.Candidates, candidate.Marshal())
	}
	envelope, err := signaling.Seal(capability, template)
	if err != nil {
		return nil, ErrICEGather
	}
	return envelope, nil
}

func (a *Agent) OpenAuthenticatedRemote(
	capability signaling.Capability,
	expected signaling.Expected,
	envelope []byte,
	now time.Time,
	replays *signaling.ReplayCache,
) (signaling.Bundle, error) {
	a.mu.Lock()
	defer a.mu.Unlock()
	if a.closed || !a.gathered || a.remote != nil {
		return signaling.Bundle{}, ErrICEState
	}
	bundle, err := signaling.Open(capability, expected, envelope, now, replays)
	if err != nil {
		return signaling.Bundle{}, err
	}
	parsed := make([]ice.Candidate, 0, len(bundle.Candidates))
	for _, encoded := range bundle.Candidates {
		candidate, parseErr := ice.UnmarshalCandidate(encoded)
		if parseErr != nil || !a.candidateAllowed(candidate) {
			return signaling.Bundle{}, ErrICECandidate
		}
		parsed = append(parsed, candidate)
	}
	for _, candidate := range parsed {
		if addErr := a.inner.AddRemoteCandidate(candidate); addErr != nil {
			a.closed = true
			a.remote = nil
			_ = a.inner.Close()
			return signaling.Bundle{}, ErrICECandidate
		}
	}
	remoteCopy := bundle
	a.remote = &remoteCopy
	return bundle, nil
}

func validateAgentConfig(config AgentConfig) error {
	if len(config.CandidateTypes) == 0 || len(config.URLs) == 0 || len(config.URLs) > limits.MaxICEServerCount {
		return ErrICEAgent
	}
	networkTypes := config.NetworkTypes
	if len(networkTypes) == 0 {
		networkTypes = []ice.NetworkType{ice.NetworkTypeUDP4}
	}
	for _, networkType := range networkTypes {
		if !networkType.IsUDP() {
			return ErrICEAgent
		}
	}
	hasSTUN := false
	hasTURN := false
	for _, uri := range config.URLs {
		if uri == nil || uri.Host == "" || uri.Port < 1 || uri.Port > 65_535 || uri.Proto != stun.ProtoTypeUDP {
			return ErrICEAgent
		}
		switch uri.Scheme {
		case stun.SchemeTypeSTUN:
			hasSTUN = true
		case stun.SchemeTypeTURN:
			hasTURN = true
		default:
			return ErrICEAgent
		}
	}
	for _, candidateType := range config.CandidateTypes {
		switch candidateType {
		case ice.CandidateTypeServerReflexive:
			if !hasSTUN {
				return ErrICEAgent
			}
		case ice.CandidateTypeRelay:
			if !hasTURN {
				return ErrICEAgent
			}
		default:
			return ErrICEAgent
		}
	}
	hasRelayCandidate := false
	for _, candidateType := range config.CandidateTypes {
		if candidateType == ice.CandidateTypeRelay {
			hasRelayCandidate = true
		}
	}
	if hasRelayCandidate != (len(config.AllowedRelayAddresses) > 0) || len(config.AllowedRelayAddresses) > limits.MaxICEServerCount {
		return ErrICEAgent
	}
	seenRelays := make(map[netip.Addr]struct{}, len(config.AllowedRelayAddresses))
	for _, encoded := range config.AllowedRelayAddresses {
		address, err := netip.ParseAddr(encoded)
		if err != nil {
			return ErrICEAgent
		}
		address = address.Unmap()
		if !publicUnicastAddress(address) {
			return ErrICEAgent
		}
		if _, duplicate := seenRelays[address]; duplicate {
			return ErrICEAgent
		}
		seenRelays[address] = struct{}{}
	}
	return nil
}

func (a *Agent) candidateAllowed(candidate ice.Candidate) bool {
	if candidate == nil || candidate.Port() < 1 || candidate.Port() > 65_535 {
		return false
	}
	if _, allowed := a.allowedCandidates[candidate.Type()]; !allowed {
		return false
	}
	if _, allowed := a.allowedNetworkTypes[candidate.NetworkType()]; !allowed {
		return false
	}
	if candidate.Component() != 1 {
		return false
	}
	address, err := netip.ParseAddr(candidate.Address())
	if err != nil {
		return false
	}
	address = address.Unmap()
	if candidate.NetworkType().IsIPv4() {
		if !address.Is4() {
			return false
		}
	} else if !candidate.NetworkType().IsIPv6() || !address.Is6() {
		return false
	}
	if !publicUnicastAddress(address) {
		return false
	}
	if candidate.Type() == ice.CandidateTypeRelay {
		_, allowed := a.allowedRelays[address]
		return allowed
	}
	return candidate.Type() == ice.CandidateTypeServerReflexive
}

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
	netip.MustParsePrefix("2001:2::/48"),
	netip.MustParsePrefix("2001:db8::/32"),
	netip.MustParsePrefix("3fff::/20"),
}

func publicUnicastAddress(address netip.Addr) bool {
	if !address.IsValid() || !address.IsGlobalUnicast() || address.IsPrivate() ||
		address.IsLoopback() || address.IsLinkLocalUnicast() || address.IsMulticast() || address.IsUnspecified() {
		return false
	}
	for _, prefix := range nonPublicAddressPrefixes {
		if prefix.Contains(address) {
			return false
		}
	}
	return true
}

func (a *Agent) Accept(ctx context.Context) (*ice.Conn, error) {
	remote, err := a.remoteCredentials()
	if err != nil {
		return nil, err
	}
	connection, err := a.inner.Accept(ctx, remote.ICEUfrag, remote.ICEPassword)
	if err != nil {
		return nil, ErrICEConnect
	}
	return connection, nil
}

func (a *Agent) Dial(ctx context.Context) (*ice.Conn, error) {
	remote, err := a.remoteCredentials()
	if err != nil {
		return nil, err
	}
	connection, err := a.inner.Dial(ctx, remote.ICEUfrag, remote.ICEPassword)
	if err != nil {
		return nil, ErrICEConnect
	}
	return connection, nil
}

type PathEvidence struct {
	LocalType  string
	RemoteType string
}

func (a *Agent) SelectedPath() (PathEvidence, error) {
	pair, err := a.inner.GetSelectedCandidatePair()
	if err != nil || pair == nil {
		return PathEvidence{}, ErrICEState
	}
	return PathEvidence{LocalType: pair.Local.Type().String(), RemoteType: pair.Remote.Type().String()}, nil
}

func (a *Agent) remoteCredentials() (signaling.Bundle, error) {
	a.mu.Lock()
	defer a.mu.Unlock()
	if a.closed || !a.gathered || a.remote == nil {
		return signaling.Bundle{}, ErrICEState
	}
	return *a.remote, nil
}

func (a *Agent) Close() error {
	a.mu.Lock()
	if a.closed {
		a.mu.Unlock()
		return nil
	}
	a.closed = true
	a.remote = nil
	a.mu.Unlock()
	if err := a.inner.Close(); err != nil {
		return ErrICEAgent
	}
	return nil
}
