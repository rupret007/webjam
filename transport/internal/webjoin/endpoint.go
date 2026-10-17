package webjoin

import (
	"context"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"net"
	"net/netip"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/pion/ice/v4"
	"github.com/pion/logging"
	"github.com/pion/rtp"
	"github.com/pion/transport/v4/stdnet"
	"github.com/pion/webrtc/v4"
)

const (
	GatherTimeout     = 20 * time.Second
	ConnectTimeout    = 25 * time.Second
	EnrollmentTimeout = 10 * time.Second
	IdleTimeout       = 30 * time.Second
)

type Packet struct {
	Payload        []byte
	SequenceNumber uint16
	Timestamp      uint32
}
type Bridge interface {
	WriteOpus(context.Context, Packet) error
	ReadOpus(context.Context) (Packet, error)
	Close() error
}

// Config is host-only. Browser input cannot set ICE services, relay allowlists,
// bridge ports, timeouts, quota, or the session's explicit permission bit.
type Config struct {
	Enabled    bool
	SessionID  [32]byte
	Capability [32]byte
	Generation uint32
	ExpiresAt  time.Time
	ICE        ICEConfig
	Bridge     Bridge
}
type Endpoint struct {
	cfg                      Config
	pc                       *webrtc.PeerConnection
	track                    *webrtc.TrackLocalStaticRTP
	enrollment               *webrtc.DataChannel
	fingerprint              [32]byte
	relays                   map[netip.Addr]bool
	lab                      bool
	ctx                      context.Context
	cancel                   context.CancelFunc
	done, finished, enrolled chan struct{}
	stopOnce                 sync.Once
	mu                       sync.Mutex
	offer, answer            *Signal
	offerStarted             bool
	err                      error
	authorized               atomic.Bool
	trackStarted             atomic.Bool
	connected                atomic.Int64
	lastMedia                atomic.Int64
	answerAt                 atomic.Int64
	quota                    *byteQuota
}

// New leaves both media directions quarantined. Only a valid one-use proof on
// the certificate-pinned DTLS enrollment channel opens the gate.
func New(ctx context.Context, cfg Config) (*Endpoint, error) { return newEndpoint(ctx, cfg, false) }
func newEndpoint(ctx context.Context, cfg Config, lab bool) (*Endpoint, error) {
	if !cfg.Enabled {
		return nil, ErrDisabled
	}
	now := time.Now()
	if ctx == nil || cfg.SessionID == ([32]byte{}) || cfg.Capability == ([32]byte{}) || cfg.Generation == 0 || cfg.Bridge == nil || !cfg.ExpiresAt.After(now) || cfg.ExpiresAt.After(now.Add(8*time.Hour)) {
		return nil, ErrConfig
	}
	if !lab {
		if err := cfg.ICE.validate(now); err != nil {
			return nil, err
		}
	}
	if cfg.ICE.QuotaBytes == 0 {
		cfg.ICE.QuotaBytes = MaxSessionBytes
	}
	if cfg.ICE.QuotaBytes > MaxSessionBytes {
		return nil, ErrConfig
	}
	if cfg.ICE.ExpiresAt.Before(cfg.ExpiresAt) && !cfg.ICE.ExpiresAt.IsZero() {
		cfg.ExpiresAt = cfg.ICE.ExpiresAt
	}
	inner, cancel := context.WithCancel(ctx)
	e := &Endpoint{cfg: cfg, lab: lab, ctx: inner, cancel: cancel, done: make(chan struct{}), finished: make(chan struct{}), enrolled: make(chan struct{}), relays: map[netip.Addr]bool{}}
	for _, raw := range cfg.ICE.AllowedRelayAddresses {
		ip, _ := netip.ParseAddr(raw)
		e.relays[ip.Unmap()] = true
	}
	e.quota = &byteQuota{limit: cfg.ICE.QuotaBytes, onLimit: func() { e.stop(ErrQuota) }}
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		cancel()
		return nil, err
	}
	cert, err := webrtc.GenerateCertificate(key)
	if err != nil {
		cancel()
		return nil, err
	}
	fps, err := cert.GetFingerprints()
	if err != nil || len(fps) != 1 {
		cancel()
		return nil, ErrPin
	}
	pin, err := parseFingerprint(fps[0].Algorithm + " " + fps[0].Value)
	if err != nil {
		cancel()
		return nil, err
	}
	e.fingerprint = pinBytes(pin)
	engine := webrtc.SettingEngine{}
	// Match the browser's IPv4 candidate policy before gathering begins.
	engine.SetNetworkTypes([]webrtc.NetworkType{webrtc.NetworkTypeUDP4})
	engine.SetICEMulticastDNSMode(ice.MulticastDNSModeDisabled)
	engine.SetICETimeouts(5*time.Second, 10*time.Second, 2*time.Second)
	engine.SetSTUNGatherTimeout(5 * time.Second)
	engine.SetRelayAcceptanceMinWait(time.Second)
	// The host is ICE-controlling. Do not nominate an unsignaled peer-reflexive
	// route ahead of an authenticated relay fallback.
	if !lab {
		engine.SetPrflxAcceptanceMinWait(24 * time.Hour)
	}
	engine.SetSCTPMaxReceiveBufferSize(4096)
	engine.SetSCTPMaxMessageSize(128)
	engine.SetDTLSConnectContextMaker(func() (context.Context, func()) { return context.WithTimeout(e.ctx, EnrollmentTimeout) })
	engine.SetRemoteIPFilter(func(ip net.IP) bool {
		a, ok := netip.AddrFromSlice(ip)
		return ok && ((lab && (a.IsLoopback() || a.IsPrivate())) || publicIP(a))
	})
	policy := webrtc.ICETransportPolicyNoHost
	if lab {
		policy = webrtc.ICETransportPolicyAll
		engine.SetIncludeLoopbackCandidate(true)
		engine.SetIPFilter(func(ip net.IP) bool { return ip.IsLoopback() })
	}
	network, err := stdnet.NewNet()
	if err != nil {
		cancel()
		return nil, err
	}
	engine.SetNet(&quotaNet{Net: network, quota: e.quota})
	logger := logging.NewDefaultLoggerFactory()
	logger.DefaultLogLevel = logging.LogLevelDisabled
	engine.LoggerFactory = logger
	media := &webrtc.MediaEngine{}
	if err = media.RegisterCodec(webrtc.RTPCodecParameters{RTPCodecCapability: webrtc.RTPCodecCapability{MimeType: webrtc.MimeTypeOpus, ClockRate: 48000, Channels: 2, SDPFmtpLine: "stereo=1;sprop-stereo=1;maxaveragebitrate=128000;usedtx=0;useinbandfec=1;minptime=10"}, PayloadType: 111}, webrtc.RTPCodecTypeAudio); err != nil {
		cancel()
		return nil, err
	}
	api := webrtc.NewAPI(webrtc.WithSettingEngine(engine), webrtc.WithMediaEngine(media))
	pc, err := api.NewPeerConnection(webrtc.Configuration{ICEServers: cfg.ICE.Servers, ICETransportPolicy: policy, Certificates: []webrtc.Certificate{*cert}, BundlePolicy: webrtc.BundlePolicyMaxBundle, RTCPMuxPolicy: webrtc.RTCPMuxPolicyRequire})
	if err != nil {
		cancel()
		return nil, ErrICE
	}
	e.pc = pc
	pc.SCTP().Transport().ICETransport().OnSelectedCandidatePairChange(func(pair *webrtc.ICECandidatePair) {
		if !e.allowedPair(pair) {
			e.stop(ErrCandidate)
		}
	})
	e.track, err = webrtc.NewTrackLocalStaticRTP(webrtc.RTPCodecCapability{MimeType: webrtc.MimeTypeOpus, ClockRate: 48000, Channels: 2}, "host-mix", "webjoin")
	if err != nil {
		e.stop(err)
		return nil, err
	}
	sender, err := pc.AddTrack(e.track)
	if err != nil {
		e.stop(err)
		return nil, err
	}
	// Drain bounded RTCP to keep Pion's sender moving; no application data plane.
	go func() {
		buf := make([]byte, 1500)
		for {
			if _, _, readErr := sender.Read(buf); readErr != nil {
				return
			}
		}
	}()
	e.enrollment, err = pc.CreateDataChannel("webjoin-enrollment", nil)
	if err != nil {
		e.stop(err)
		return nil, err
	}
	e.enrollment.OnMessage(func(message webrtc.DataChannelMessage) {
		if message.IsString || len(message.Data) != sha256.Size {
			e.stop(ErrEnrollment)
			return
		}
		if verifyErr := e.authorize(message.Data, time.Now()); verifyErr != nil {
			e.stop(verifyErr)
			return
		}
		if sendErr := e.enrollment.SendText("authorized"); sendErr != nil {
			e.stop(ErrEnrollment)
			return
		}
		go e.sendMix()
	})
	// Extra channels are never application-authorized, even after enrollment.
	pc.OnDataChannel(func(dc *webrtc.DataChannel) { _ = dc.Close(); e.stop(ErrEnrollment) })
	pc.OnConnectionStateChange(func(state webrtc.PeerConnectionState) {
		switch state {
		case webrtc.PeerConnectionStateConnected:
			e.connected.CompareAndSwap(0, time.Now().UnixNano())
			if err := e.verifyTransport(); err != nil {
				e.stop(err)
			}
		case webrtc.PeerConnectionStateFailed, webrtc.PeerConnectionStateClosed:
			e.stop(ErrClosed)
		}
	})
	pc.OnTrack(func(track *webrtc.TrackRemote, _ *webrtc.RTPReceiver) {
		if !strings.EqualFold(track.Codec().MimeType, webrtc.MimeTypeOpus) || !e.trackStarted.CompareAndSwap(false, true) {
			e.stop(fmt.Errorf("%w: unexpected audio track", ErrBridge))
			return
		}
		go e.receiveAudio(track)
	})
	go e.watch()
	return e, nil
}
func (e *Endpoint) Fingerprint() [32]byte     { return e.fingerprint }
func (e *Endpoint) Done() <-chan struct{}     { return e.done }
func (e *Endpoint) Enrolled() <-chan struct{} { return e.enrolled }
func (e *Endpoint) Err() error                { e.mu.Lock(); defer e.mu.Unlock(); return e.err }
func (e *Endpoint) Authorized() bool          { return e.authorized.Load() }
func (e *Endpoint) Bytes() uint64             { return e.quota.used.Load() }
func (e *Endpoint) SetGeneration(generation uint32) {
	if generation != e.cfg.Generation {
		e.stop(ErrExpired)
	}
}
func (e *Endpoint) Kick() { e.stop(ErrClosed) }
func (e *Endpoint) Close() error {
	e.stop(ErrClosed)
	select {
	case <-e.finished:
		return nil
	case <-time.After(3 * time.Second):
		return ErrTimeout
	}
}
func (e *Endpoint) stop(err error) {
	e.stopOnce.Do(func() {
		e.mu.Lock()
		e.err = err
		e.mu.Unlock()
		e.authorized.Store(false)
		e.cancel()
		close(e.done)
		go func() {
			if e.pc != nil {
				_ = e.pc.Close()
			}
			_ = e.cfg.Bridge.Close()
			close(e.finished)
		}()
	})
}
func (e *Endpoint) Offer(ctx context.Context) (envelope []byte, err error) {
	e.mu.Lock()
	if e.offerStarted || e.err != nil {
		e.mu.Unlock()
		return nil, ErrClosed
	}
	e.offerStarted = true
	e.mu.Unlock()
	defer func() {
		if err != nil {
			e.stop(err)
		}
	}()
	gatherCtx, cancel := context.WithTimeout(ctx, GatherTimeout)
	defer cancel()
	complete := webrtc.GatheringCompletePromise(e.pc)
	description, err := e.pc.CreateOffer(nil)
	if err != nil {
		return nil, ErrSignal
	}
	if err = e.pc.SetLocalDescription(description); err != nil {
		return nil, ErrSignal
	}
	select {
	case <-complete:
	case <-gatherCtx.Done():
		return nil, ErrTimeout
	case <-e.done:
		return nil, e.Err()
	}
	pin := hex.EncodeToString(e.fingerprint[:])
	raw, err := validateSDP(e.pc.LocalDescription().SDP, pin, e.relays, true, e.lab)
	if err != nil {
		return nil, err
	}
	nonce := make([]byte, 16)
	if _, err = rand.Read(nonce); err != nil {
		return nil, err
	}
	expires := time.Now().Add(10 * time.Minute)
	if e.cfg.ExpiresAt.Before(expires) {
		expires = e.cfg.ExpiresAt
	}
	offer := Signal{Version: 1, Kind: "offer", Session: hex.EncodeToString(e.cfg.SessionID[:]), Generation: e.cfg.Generation, ExpiresAt: expires.Unix(), Nonce: hex.EncodeToString(nonce), HostPin: pin, GuestPin: "", SDP: raw}
	e.mu.Lock()
	e.offer = &offer
	e.mu.Unlock()
	return SealSignal(e.cfg.Capability, e.cfg.SessionID, offer)
}
func (e *Endpoint) ApplyAnswer(ctx context.Context, envelope []byte) (err error) {
	defer func() {
		if err != nil {
			e.stop(err)
		}
	}()
	if ctx.Err() != nil {
		return ctx.Err()
	}
	answer, err := OpenSignal(e.cfg.Capability, e.cfg.SessionID, envelope)
	if err != nil {
		return err
	}
	e.mu.Lock()
	if e.err != nil || e.offer == nil || e.answer != nil {
		e.mu.Unlock()
		return ErrReplay
	}
	if err = sameBinding(*e.offer, answer, time.Now()); err != nil {
		e.mu.Unlock()
		return err
	}
	e.answer = &answer
	e.mu.Unlock()
	raw, err := validateSDP(answer.SDP, answer.GuestPin, e.relays, false, e.lab)
	if err != nil {
		return err
	}
	e.answerAt.Store(time.Now().UnixNano())
	if err = e.pc.SetRemoteDescription(webrtc.SessionDescription{Type: webrtc.SDPTypeAnswer, SDP: raw}); err != nil {
		return ErrSignal
	}
	return nil
}
func (e *Endpoint) verifyTransport() error {
	e.mu.Lock()
	answer := e.answer
	e.mu.Unlock()
	if answer == nil || e.pc.SCTP() == nil || e.pc.SCTP().Transport() == nil {
		return ErrPin
	}
	dtls := e.pc.SCTP().Transport()
	cert := dtls.GetRemoteCertificate()
	if len(cert) == 0 {
		return ErrPin
	}
	actual := sha256.Sum256(cert)
	expected := pinBytes(answer.GuestPin)
	if !hmac.Equal(actual[:], expected[:]) {
		return ErrPin
	}
	pair, err := dtls.ICETransport().GetSelectedCandidatePair()
	if err != nil || pair == nil {
		return ErrCandidate
	}
	if !e.allowedPair(pair) {
		return ErrCandidate
	}
	return nil
}
func (e *Endpoint) allowedPair(pair *webrtc.ICECandidatePair) bool {
	if pair == nil {
		return false
	}
	for _, c := range []*webrtc.ICECandidate{pair.Local, pair.Remote} {
		if c == nil {
			return false
		}
		parsed, err := ice.UnmarshalCandidate(strings.TrimPrefix(c.ToJSON().Candidate, "candidate:"))
		if err != nil || !candidateAllowed(parsed, e.relays, e.lab) {
			return false
		}
	}
	return true
}
func (e *Endpoint) authorize(proof []byte, now time.Time) error {
	if e.authorized.Load() {
		return ErrReplay
	}
	if err := e.verifyTransport(); err != nil {
		return err
	}
	e.mu.Lock()
	defer e.mu.Unlock()
	if e.err != nil || e.offer == nil || e.answer == nil {
		return ErrEnrollment
	}
	if err := sameBinding(*e.offer, *e.answer, now); err != nil {
		return err
	}
	expected, err := EnrollmentProof(e.cfg.Capability, e.cfg.SessionID, *e.answer)
	if err != nil || !hmac.Equal(expected, proof) {
		return ErrEnrollment
	}
	if !e.authorized.CompareAndSwap(false, true) {
		return ErrReplay
	}
	e.lastMedia.Store(now.UnixNano())
	close(e.enrolled)
	return nil
}
func (e *Endpoint) receiveAudio(track *webrtc.TrackRemote) {
	for {
		packet, _, err := track.ReadRTP()
		if err != nil {
			if e.ctx.Err() == nil {
				e.stop(fmt.Errorf("%w: RTP receive failed", ErrBridge))
			}
			return
		}
		if !e.authorized.Load() {
			continue
		}
		// Browsers send RTP padding/probing packets with no codec payload.
		// They are metered at the socket, but neither reach Opus nor keep a
		// silent/disconnected guest alive.
		if len(packet.Payload) == 0 {
			continue
		}
		if len(packet.Payload) > MaxOpusBytes {
			e.stop(fmt.Errorf("%w: invalid incoming Opus packet", ErrBridge))
			return
		}
		e.lastMedia.Store(time.Now().UnixNano())
		ctx, cancel := context.WithTimeout(e.ctx, 2*time.Second)
		err = e.cfg.Bridge.WriteOpus(ctx, Packet{Payload: packet.Payload, SequenceNumber: packet.SequenceNumber, Timestamp: packet.Timestamp})
		cancel()
		if err != nil {
			e.stop(fmt.Errorf("%w: bridge input failed", ErrBridge))
			return
		}
	}
}
func (e *Endpoint) sendMix() {
	for e.authorized.Load() {
		packet, err := e.cfg.Bridge.ReadOpus(e.ctx)
		if err != nil {
			if e.ctx.Err() == nil {
				e.stop(fmt.Errorf("%w: bridge output failed", ErrBridge))
			}
			return
		}
		if !e.authorized.Load() {
			return
		}
		if len(packet.Payload) == 0 || len(packet.Payload) > MaxOpusBytes {
			e.stop(fmt.Errorf("%w: invalid outgoing Opus packet", ErrBridge))
			return
		}
		if err = e.track.WriteRTP(&rtp.Packet{Header: rtp.Header{Version: 2, PayloadType: 111, SequenceNumber: packet.SequenceNumber, Timestamp: packet.Timestamp}, Payload: packet.Payload}); err != nil {
			e.stop(fmt.Errorf("%w: RTP send failed", ErrBridge))
			return
		}
	}
}
func (e *Endpoint) watch() {
	ticker := time.NewTicker(100 * time.Millisecond)
	defer ticker.Stop()
	for {
		select {
		case <-e.ctx.Done():
			e.stop(ErrClosed)
			return
		case now := <-ticker.C:
			if !e.cfg.ExpiresAt.After(now) {
				e.stop(ErrExpired)
				return
			}
			answerAt, connected := e.answerAt.Load(), e.connected.Load()
			if !e.authorized.Load() {
				if (answerAt != 0 && now.Sub(time.Unix(0, answerAt)) > ConnectTimeout) || (connected != 0 && now.Sub(time.Unix(0, connected)) > EnrollmentTimeout) {
					e.stop(ErrTimeout)
					return
				}
			} else if now.Sub(time.Unix(0, e.lastMedia.Load())) > IdleTimeout {
				e.stop(ErrTimeout)
				return
			}
		}
	}
}
