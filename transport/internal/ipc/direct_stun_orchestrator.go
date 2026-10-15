package ipc

import (
	"context"
	"crypto/hkdf"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"io"
	"net"
	"sync"
	"time"

	"github.com/rupret007/webjam/transport/internal/directstun"
	"github.com/rupret007/webjam/transport/internal/help"
	"github.com/rupret007/webjam/transport/internal/icequic"
	"github.com/rupret007/webjam/transport/internal/limits"
	"github.com/rupret007/webjam/transport/internal/loopback"
	"github.com/rupret007/webjam/transport/internal/peer"
	"github.com/rupret007/webjam/transport/internal/profile"
	"github.com/rupret007/webjam/transport/internal/room"
	"github.com/rupret007/webjam/transport/internal/sessionauth"
	"github.com/rupret007/webjam/transport/internal/signaling"
)

const (
	directStunProofDomain = "webjam/v3/direct-stun/proof-nonce"
	directStunDialAttempt = 4 * time.Second
)

var directPathTimeout = 8 * time.Second

var (
	gatherListenCandidates = directstun.Gather
	parseDialTarget        = directstun.ParseDialTarget
	tryAllocateRelay       = directstun.TryAllocate
)

type directStunOrchestrator struct {
	now func() time.Time
}

func newDirectStunOrchestrator(now func() time.Time) fabricOrchestrator {
	return &directStunOrchestrator{now: now}
}

func (o *directStunOrchestrator) Start(
	ctx context.Context,
	configuration *enrollmentConfig,
	identity *icequic.Identity,
	endpoint loopback.Endpoint,
) (fabricOperation, error) {
	if ctx == nil || o == nil || o.now == nil || configuration == nil || identity == nil || endpoint == nil ||
		configuration.Profile.ID != profile.DirectStunID || configuration.Generation == 0 ||
		configuration.SessionID == (SessionID{}) || configuration.EnrollmentCapability == (Capability{}) ||
		configuration.HostSPKISHA256 == (PublicPin{}) || configuration.ExpiresAtUnix == 0 ||
		len(identity.Certificate.Certificate) != 1 || identity.SPKIFingerprint == ([32]byte{}) {
		return nil, ErrEnrollmentInvalid
	}
	if configuration.Mode != "host" && configuration.Mode != "guest" {
		return nil, ErrEnrollmentInvalid
	}
	if configuration.Mode == "host" {
		if PublicPin(identity.SPKIFingerprint) != configuration.HostSPKISHA256 || len(configuration.HostSignal) != 0 {
			return nil, ErrEnrollmentInvalid
		}
	} else if len(configuration.HostSignal) == 0 {
		return nil, ErrEnrollmentInvalid
	}
	operationDeadline, err := sessionOperationDeadline(o.now(), identity)
	if err != nil {
		return nil, err
	}
	operationCtx, cancel := context.WithDeadline(ctx, operationDeadline)
	enrollmentCtx, cancelEnrollment := context.WithDeadline(
		operationCtx, time.Unix(int64(configuration.ExpiresAtUnix), 0),
	)
	if enrollmentCtx.Err() != nil {
		cancelEnrollment()
		cancel()
		return nil, ErrEnrollmentInvalid
	}
	operation := &directStunOperation{
		ctx: operationCtx, cancel: cancel, now: o.now, configuration: configuration,
		enrollmentCtx: enrollmentCtx, cancelEnrollment: cancelEnrollment,
		identity: identity, endpoint: endpoint,
		updates: make(chan fabricUpdate, limits.MaxHelpEventQueueDepth), done: make(chan struct{}),
	}
	go operation.run()
	return operation, nil
}

type directStunOperation struct {
	ctx              context.Context
	cancel           context.CancelFunc
	enrollmentCtx    context.Context
	cancelEnrollment context.CancelFunc
	now              func() time.Time
	configuration    *enrollmentConfig
	identity         *icequic.Identity
	endpoint         loopback.Endpoint
	updates          chan fabricUpdate
	done             chan struct{}

	resourceMu   sync.Mutex
	packetConn   *net.UDPConn
	mux          *directstun.Mux
	listener     *icequic.Listener
	turnListener *icequic.Listener
	allocation   *directstun.Allocation
	connection   *icequic.Connection
	peer         *peer.Peer
	control      *room.Channel
	guestSignal  chan []byte
	icePassword  string
	bundleNonce  signaling.BundleNonce
	sessionPath  string

	cleanupOnce sync.Once
}

func (o *directStunOperation) Updates() <-chan fabricUpdate { return o.updates }

func (o *directStunOperation) Close(ctx context.Context) error {
	if o == nil || ctx == nil {
		return ErrProtocol
	}
	o.cancel()
	o.interruptDataPlane()
	select {
	case <-o.done:
		return nil
	case <-ctx.Done():
		return ErrProtocol
	}
}

func (o *directStunOperation) run() {
	defer close(o.done)
	defer close(o.updates)
	defer o.cleanup()
	defer o.cancelEnrollment()
	defer o.cancel()
	err := o.runFabric()
	if err != nil && o.ctx.Err() == nil {
		o.send(fabricUpdate{kind: updateFabricFailed, err: safeDirectStunFailure(err)})
	}
}

func (o *directStunOperation) runFabric() error {
	if o.configuration.Mode == "host" {
		return o.runHost()
	}
	return o.runGuest()
}

func (o *directStunOperation) runHost() error {
	expiresAt := time.Unix(int64(o.configuration.ExpiresAtUnix), 0)
	conn, err := net.ListenUDP("udp4", &net.UDPAddr{IP: net.IPv4zero, Port: 0})
	if err != nil {
		return err
	}
	o.resourceMu.Lock()
	o.packetConn = conn
	o.resourceMu.Unlock()

	candidates, public, allocation, err := o.gatherAndMaybeRelay(conn, true)
	if err != nil {
		return err
	}
	if allocation != nil {
		o.resourceMu.Lock()
		o.allocation = allocation
		o.resourceMu.Unlock()
	}
	envelope, err := o.sealHostBundle(candidates, expiresAt)
	if err != nil {
		return err
	}
	mux := directstun.NewMux(conn, o.icePassword, [16]byte(o.bundleNonce), directstun.RoleHost)
	listener, err := icequic.Listen(mux, *o.identity)
	if err != nil {
		_ = mux.Close()
		return err
	}
	guestSignal := make(chan []byte, 1)
	o.resourceMu.Lock()
	o.mux = mux
	o.listener = listener
	o.guestSignal = guestSignal
	o.resourceMu.Unlock()
	route := "local"
	if public {
		route = "public"
	}
	if !o.sendWithContext(o.enrollmentCtx, fabricUpdate{
		kind: updateHostRegistered, signal: envelope, route: route,
	}) {
		return o.enrollmentCtx.Err()
	}

	// Accept on direct and relay together. Join tries the nominated
	// direct path first and falls back to the host's relay candidate.
	accepted := make(chan acceptResult, 2)
	go acceptOn(o.enrollmentCtx, listener, "direct", accepted)
	if allocation != nil {
		turnListener, listenErr := icequic.Listen(allocation.PacketConn(), *o.identity)
		if listenErr == nil {
			o.resourceMu.Lock()
			o.turnListener = turnListener
			o.resourceMu.Unlock()
			go acceptOn(o.enrollmentCtx, turnListener, "relay", accepted)
		}
	}
	pending := 1
	if o.turnListener != nil {
		pending = 2
	}
	for {
		select {
		case envelope := <-guestSignal:
			guestBundle, parseErr := o.openGuestBundle(envelope)
			if parseErr != nil {
				return parseErr
			}
			mux.SetRemotes(candidateAddrs(guestBundle.Candidates))
			o.permitPeerRelays(guestBundle.Candidates)
		case result := <-accepted:
			if result.err != nil {
				pending--
				if pending == 0 {
					if o.enrollmentCtx.Err() != nil {
						return ErrUnreachable
					}
					return result.err
				}
				continue
			}
			o.nominatePath(result)
			if err = o.exchangeHostProofs(result.connection, expiresAt); err != nil {
				return err
			}
			return o.runPeer(peer.ModeHost)
		case <-o.enrollmentCtx.Done():
			return ErrUnreachable
		}
	}
}

func (o *directStunOperation) runGuest() error {
	expiresAt := time.Unix(int64(o.configuration.ExpiresAtUnix), 0)
	bundle, err := o.openHostBundle(o.configuration.HostSignal)
	if err != nil {
		return err
	}
	conn, err := net.ListenUDP("udp4", &net.UDPAddr{IP: net.IPv4zero, Port: 0})
	if err != nil {
		return err
	}
	o.resourceMu.Lock()
	o.packetConn = conn
	o.resourceMu.Unlock()
	localCandidates, _, allocation, err := o.gatherAndMaybeRelay(conn, false)
	if err != nil {
		return err
	}
	if allocation != nil {
		o.resourceMu.Lock()
		o.allocation = allocation
		o.resourceMu.Unlock()
	}
	reply, err := o.sealGuestBundle(localCandidates, expiresAt)
	if err != nil {
		return err
	}
	targets := make([]*net.UDPAddr, 0, len(bundle.Candidates))
	for _, encoded := range bundle.Candidates {
		target, parseErr := parseDialTarget(encoded)
		if parseErr != nil {
			continue
		}
		targets = append(targets, target)
	}
	if len(targets) == 0 && allocation == nil {
		return ErrEnrollmentInvalid
	}
	mux := directstun.NewMux(conn, bundle.ICEPassword, [16]byte(bundle.Nonce), directstun.RoleGuest)
	mux.SetRemotes(targets)
	o.resourceMu.Lock()
	o.mux = mux
	o.resourceMu.Unlock()
	if !o.sendWithContext(o.enrollmentCtx, fabricUpdate{kind: updateGuestSignal, signal: reply}) {
		return o.enrollmentCtx.Err()
	}
	directCtx, cancelDirect := context.WithTimeout(o.enrollmentCtx, directPathTimeout)
	nominated, err := mux.WaitNominated(directCtx)
	cancelDirect()
	if err == nil {
		dialCtx, cancel := context.WithTimeout(o.enrollmentCtx, directStunDialAttempt)
		defer cancel()
		connection, dialErr := icequic.Dial(
			dialCtx, mux, nominated, *o.identity,
			hex.EncodeToString(o.configuration.HostSPKISHA256[:]),
		)
		if dialErr == nil {
			o.resourceMu.Lock()
			o.connection = connection
			o.sessionPath = "direct"
			o.resourceMu.Unlock()
			if err = o.exchangeGuestProofs(connection, expiresAt); err != nil {
				return err
			}
			return o.runPeer(peer.ModeGuest)
		}
		if o.enrollmentCtx.Err() != nil {
			return ErrUnreachable
		}
	}
	connection, err := o.dialRelay(bundle.Candidates)
	if err != nil {
		return ErrUnreachable
	}
	o.resourceMu.Lock()
	o.connection = connection
	o.sessionPath = "relay"
	o.resourceMu.Unlock()
	if err = o.exchangeGuestProofs(connection, expiresAt); err != nil {
		return err
	}
	return o.runPeer(peer.ModeGuest)
}

// gatherAndMaybeRelay starts TURN in parallel with STUN gather. A LAN host
// (no public mapping) does not wait for credentials — that fetch is discarded.
// Join always waits: the host may have published a relay candidate.
func (o *directStunOperation) gatherAndMaybeRelay(
	conn *net.UDPConn,
	discardOnLAN bool,
) ([]string, bool, *directstun.Allocation, error) {
	allocCh := make(chan *directstun.Allocation, 1)
	go func() {
		allocCh <- tryAllocateRelay(directstun.WithCredentialProof(
			o.enrollmentCtx,
			[32]byte(o.configuration.SessionID),
			[32]byte(o.configuration.EnrollmentCapability),
		))
	}()
	candidates, public, err := gatherListenCandidates(o.enrollmentCtx, conn)
	if err != nil || len(candidates) == 0 {
		go discardRelayAllocation(allocCh)
		return nil, false, nil, ErrOpenFailedWrap(err)
	}
	drainUDP(conn)
	waitForRelay := public || !discardOnLAN
	var allocation *directstun.Allocation
	if waitForRelay {
		select {
		case allocation = <-allocCh:
		case <-o.enrollmentCtx.Done():
			go discardRelayAllocation(allocCh)
			return nil, public, nil, ErrUnreachable
		}
	} else {
		go discardRelayAllocation(allocCh)
	}
	if allocation != nil {
		if encoded, candErr := allocation.Candidate(); candErr == nil && encoded != "" &&
			len(candidates) < limits.MaxCandidateCount {
			candidates = append(candidates, encoded)
		}
	}
	return candidates, public, allocation, nil
}

func discardRelayAllocation(allocCh <-chan *directstun.Allocation) {
	if allocation := <-allocCh; allocation != nil {
		_ = allocation.Close()
	}
}

func acceptOn(ctx context.Context, listener *icequic.Listener, path string, accepted chan<- acceptResult) {
	if listener == nil {
		return
	}
	connection, err := listener.Accept(ctx)
	select {
	case accepted <- acceptResult{connection: connection, err: err, path: path}:
	case <-ctx.Done():
		if connection != nil {
			_ = connection.CloseWithError(0, "unused path")
		}
	}
}

func (o *directStunOperation) permitPeerRelays(encoded []string) {
	o.resourceMu.Lock()
	allocation := o.allocation
	o.resourceMu.Unlock()
	if allocation == nil {
		return
	}
	for _, item := range encoded {
		target, err := allocation.ParsePeerRelay(item)
		if err != nil {
			continue
		}
		_ = allocation.Permit(target)
	}
}

func (o *directStunOperation) nominatePath(result acceptResult) {
	o.resourceMu.Lock()
	o.connection = result.connection
	o.sessionPath = result.path
	unused := o.turnListener
	if result.path == "relay" {
		unused = o.listener
	}
	o.resourceMu.Unlock()
	if unused != nil && result.path == "relay" {
		_ = unused.Close()
	} else if unused != nil && result.path == "direct" {
		_ = unused.Close()
	}
}

func (o *directStunOperation) dialRelay(hostCandidates []string) (*icequic.Connection, error) {
	o.resourceMu.Lock()
	allocation := o.allocation
	o.resourceMu.Unlock()
	if allocation == nil {
		return nil, ErrUnreachable
	}
	var target *net.UDPAddr
	for _, encoded := range hostCandidates {
		parsed, err := allocation.ParsePeerRelay(encoded)
		if err != nil {
			continue
		}
		target = parsed
		break
	}
	if target == nil {
		return nil, ErrUnreachable
	}
	if err := allocation.Permit(target); err != nil {
		return nil, ErrUnreachable
	}
	dialCtx, cancel := context.WithTimeout(o.enrollmentCtx, directStunDialAttempt)
	defer cancel()
	return icequic.Dial(
		dialCtx, allocation.PacketConn(), target, *o.identity,
		hex.EncodeToString(o.configuration.HostSPKISHA256[:]),
	)
}

func (o *directStunOperation) SessionPath() string {
	if o == nil {
		return ""
	}
	o.resourceMu.Lock()
	defer o.resourceMu.Unlock()
	return o.sessionPath
}

func (o *directStunOperation) RelayBytes() uint64 {
	if o == nil {
		return 0
	}
	o.resourceMu.Lock()
	allocation := o.allocation
	o.resourceMu.Unlock()
	if allocation == nil {
		return 0
	}
	return allocation.Bytes()
}

type acceptResult struct {
	connection *icequic.Connection
	err        error
	path       string
}

func (o *directStunOperation) ApplyGuestSignal(ctx context.Context, envelope []byte) error {
	if o == nil || ctx == nil || len(envelope) == 0 {
		return ErrProtocol
	}
	o.resourceMu.Lock()
	signal := o.guestSignal
	o.resourceMu.Unlock()
	if signal == nil {
		return ErrProtocol
	}
	copied := append([]byte(nil), envelope...)
	select {
	case signal <- copied:
		return nil
	case <-ctx.Done():
		clear(copied)
		return ctx.Err()
	case <-o.ctx.Done():
		clear(copied)
		return ErrProtocol
	}
}

func (o *directStunOperation) sealHostBundle(candidates []string, expiresAt time.Time) ([]byte, error) {
	ufrag, password, err := randomICECredentials()
	if err != nil {
		return nil, err
	}
	nonce, err := signaling.NewBundleNonce()
	if err != nil {
		return nil, err
	}
	o.icePassword = password
	o.bundleNonce = nonce
	return signaling.Seal(signaling.Capability(o.configuration.EnrollmentCapability), signaling.Bundle{
		SessionID:    signaling.SessionID(o.configuration.SessionID),
		SenderRole:   signaling.RoleHost,
		HostPin:      signaling.PeerPin(o.configuration.HostSPKISHA256),
		HostPinKnown: true,
		Nonce:        nonce,
		Generation:   referenceWireGeneration,
		ExpiresAt:    expiresAt,
		ICEUfrag:     ufrag,
		ICEPassword:  password,
		Candidates:   candidates,
	})
}

func (o *directStunOperation) sealGuestBundle(candidates []string, expiresAt time.Time) ([]byte, error) {
	ufrag, password, err := randomICECredentials()
	if err != nil {
		return nil, err
	}
	nonce, err := signaling.NewBundleNonce()
	if err != nil {
		return nil, err
	}
	return signaling.Seal(signaling.Capability(o.configuration.EnrollmentCapability), signaling.Bundle{
		SessionID:    signaling.SessionID(o.configuration.SessionID),
		SenderRole:   signaling.RoleGuest,
		HostPin:      signaling.PeerPin(o.configuration.HostSPKISHA256),
		HostPinKnown: true,
		Nonce:        nonce,
		Generation:   referenceWireGeneration,
		ExpiresAt:    expiresAt,
		ICEUfrag:     ufrag,
		ICEPassword:  password,
		Candidates:   candidates,
	})
}

func (o *directStunOperation) openHostBundle(envelope []byte) (signaling.Bundle, error) {
	return signaling.Open(
		signaling.Capability(o.configuration.EnrollmentCapability),
		signaling.Expected{
			SessionID:    signaling.SessionID(o.configuration.SessionID),
			SenderRole:   signaling.RoleHost,
			HostPin:      signaling.PeerPin(o.configuration.HostSPKISHA256),
			HostPinKnown: true,
			Generation:   referenceWireGeneration,
		},
		envelope,
		o.now(),
		signaling.NewReplayCache(),
	)
}

func (o *directStunOperation) openGuestBundle(envelope []byte) (signaling.Bundle, error) {
	return signaling.Open(
		signaling.Capability(o.configuration.EnrollmentCapability),
		signaling.Expected{
			SessionID:    signaling.SessionID(o.configuration.SessionID),
			SenderRole:   signaling.RoleGuest,
			HostPin:      signaling.PeerPin(o.configuration.HostSPKISHA256),
			HostPinKnown: true,
			Generation:   referenceWireGeneration,
		},
		envelope,
		o.now(),
		signaling.NewReplayCache(),
	)
}

func candidateAddrs(encoded []string) []*net.UDPAddr {
	addrs := make([]*net.UDPAddr, 0, len(encoded))
	for _, item := range encoded {
		target, err := parseDialTarget(item)
		if err != nil {
			continue
		}
		addrs = append(addrs, target)
	}
	return addrs
}

func (o *directStunOperation) exchangeHostProofs(connection *icequic.Connection, expiresAt time.Time) error {
	guestPin, err := connection.PeerSPKIFingerprint()
	if err != nil {
		return err
	}
	stream, err := connection.AcceptEnrollmentStream(o.enrollmentCtx)
	if err != nil {
		return err
	}
	defer stream.Close()
	guestProof := make([]byte, sessionauthProofBytes)
	if _, err = io.ReadFull(stream, guestProof); err != nil {
		return err
	}
	guestBinding := o.proofBinding(sessionauth.RoleGuest, guestPin, expiresAt)
	if err = connection.VerifyAndAuthorize(
		sessionauth.Capability(o.configuration.EnrollmentCapability),
		guestBinding, guestProof, o.now(), sessionauth.NewReplayCache(),
	); err != nil {
		return err
	}
	hostBinding := o.proofBinding(sessionauth.RoleHost, guestPin, expiresAt)
	hostProof, err := sessionauth.CreateProof(
		connection, sessionauth.Capability(o.configuration.EnrollmentCapability), hostBinding, o.now(),
	)
	if err != nil {
		return err
	}
	_, err = stream.Write(hostProof)
	clear(hostProof)
	return err
}

func (o *directStunOperation) exchangeGuestProofs(connection *icequic.Connection, expiresAt time.Time) error {
	guestPin := sessionauth.PeerPin(o.identity.SPKIFingerprint)
	stream, err := connection.OpenEnrollmentStream(o.enrollmentCtx)
	if err != nil {
		return err
	}
	defer stream.Close()
	guestBinding := o.proofBinding(sessionauth.RoleGuest, [32]byte(guestPin), expiresAt)
	guestProof, err := sessionauth.CreateProof(
		connection, sessionauth.Capability(o.configuration.EnrollmentCapability), guestBinding, o.now(),
	)
	if err != nil {
		return err
	}
	if _, err = stream.Write(guestProof); err != nil {
		clear(guestProof)
		return err
	}
	clear(guestProof)
	hostProof := make([]byte, sessionauthProofBytes)
	if _, err = io.ReadFull(stream, hostProof); err != nil {
		return err
	}
	hostBinding := o.proofBinding(sessionauth.RoleHost, [32]byte(guestPin), expiresAt)
	return connection.VerifyAndAuthorize(
		sessionauth.Capability(o.configuration.EnrollmentCapability),
		hostBinding, hostProof, o.now(), sessionauth.NewReplayCache(),
	)
}

func (o *directStunOperation) proofBinding(
	role sessionauth.Role,
	guestPin [32]byte,
	expiresAt time.Time,
) sessionauth.Binding {
	return sessionauth.Binding{
		SessionID:  sessionauth.SessionID(o.configuration.SessionID),
		SenderRole: role,
		Generation: referenceWireGeneration,
		HostPin:    sessionauth.PeerPin(o.configuration.HostSPKISHA256),
		GuestPin:   sessionauth.PeerPin(guestPin),
		Nonce:      derivedDirectStunNonce(o.configuration.EnrollmentCapability, o.configuration.SessionID, role),
		ExpiresAt:  expiresAt,
	}
}

func (o *directStunOperation) runPeer(mode peer.Mode) error {
	livePeer, err := peer.New(mode, referenceWireGeneration, o.endpoint, o.connection)
	if err != nil {
		return err
	}
	helpRole := help.RoleGuest
	if mode == peer.ModeHost {
		helpRole = help.RoleHost
	}
	plane, err := icequic.NewReliablePlane(o.connection)
	if err != nil {
		return err
	}
	liveControl, err := room.NewChannel(plane, helpRole, referenceWireGeneration)
	if err != nil {
		return err
	}
	o.resourceMu.Lock()
	o.peer = livePeer
	o.control = liveControl
	o.resourceMu.Unlock()
	if err := liveControl.Handshake(o.enrollmentCtx); err != nil {
		return err
	}
	if o.ctx.Err() != nil {
		return o.ctx.Err()
	}
	if o.enrollmentCtx.Err() != nil ||
		!time.Unix(int64(o.configuration.ExpiresAtUnix), 0).After(o.now()) {
		return ErrEnrollmentInvalid
	}
	o.cancelEnrollment()
	pumpCtx, cancelPumps := context.WithCancel(o.ctx)
	var workers sync.WaitGroup
	defer func() {
		cancelPumps()
		o.interruptDataPlane()
		workers.Wait()
	}()
	peerResult := make(chan error, 1)
	helpResult := make(chan error, 1)
	workers.Add(1)
	go func() {
		defer workers.Done()
		peerResult <- livePeer.Run(pumpCtx)
	}()
	select {
	case <-livePeer.Ready():
		if !o.sendWithContext(pumpCtx, fabricUpdate{kind: updatePeerConnected, sessionPath: o.SessionPath()}) {
			return pumpCtx.Err()
		}
		workers.Add(1)
		go func() {
			defer workers.Done()
			o.receiveControl(pumpCtx, liveControl, helpResult)
		}()
	case err = <-peerResult:
		if err == nil {
			return ErrProtocol
		}
		return err
	case <-o.ctx.Done():
		return o.ctx.Err()
	}
	select {
	case err = <-peerResult:
		if o.ctx.Err() != nil {
			return o.ctx.Err()
		}
		if err == nil {
			return ErrProtocol
		}
		return err
	case err = <-helpResult:
		if o.ctx.Err() != nil {
			return o.ctx.Err()
		}
		if err == nil {
			return ErrProtocol
		}
		return err
	case <-o.ctx.Done():
		return o.ctx.Err()
	}
}

func (o *directStunOperation) receiveControl(ctx context.Context, channel *room.Channel, result chan<- error) {
	for {
		event, err := channel.Receive(ctx)
		if err != nil {
			result <- err
			return
		}
		update := fabricUpdate{}
		if event.State != nil {
			update.kind = updateRoomStateReceived
			update.roomState = event.State
		} else if event.Help != nil {
			update.helpRequestID = event.Help.RequestID
			switch event.Help.Kind {
			case help.EventReceived:
				update.kind = updateHelpReceived
				update.helpText = []byte(event.Help.Text)
			case help.EventDelivered:
				update.kind = updateHelpDelivered
			default:
				result <- ErrProtocol
				return
			}
		} else {
			result <- ErrProtocol
			return
		}
		if !o.sendWithContext(ctx, update) {
			clear(update.helpText)
			result <- ctx.Err()
			return
		}
	}
}

func (o *directStunOperation) SendHelp(ctx context.Context, requestID uint64, text string) error {
	if ctx == nil {
		return help.ErrNotReady
	}
	o.resourceMu.Lock()
	channel := o.control
	o.resourceMu.Unlock()
	if channel == nil {
		return help.ErrNotReady
	}
	return channel.SendHelp(ctx, requestID, text)
}

func (o *directStunOperation) PublishRoomState(ctx context.Context, state *room.State) error {
	if ctx == nil {
		return room.ErrNotReady
	}
	o.resourceMu.Lock()
	channel := o.control
	o.resourceMu.Unlock()
	if channel == nil {
		return room.ErrNotReady
	}
	return channel.Publish(ctx, state)
}

func (o *directStunOperation) send(update fabricUpdate) bool {
	return o.sendWithContext(o.ctx, update)
}

func (o *directStunOperation) sendWithContext(ctx context.Context, update fabricUpdate) bool {
	if ctx.Err() != nil {
		return false
	}
	select {
	case o.updates <- update:
		return true
	case <-ctx.Done():
		return false
	}
}

func (o *directStunOperation) interruptDataPlane() {
	o.resourceMu.Lock()
	livePeer, connection, listener, turnListener, mux, packetConn := o.peer, o.connection, o.listener, o.turnListener, o.mux, o.packetConn
	allocation := o.allocation
	endpoint := o.endpoint
	o.resourceMu.Unlock()
	if livePeer != nil {
		_ = livePeer.Close()
	}
	if connection != nil {
		_ = connection.CloseWithError(0, "peer stopped")
	}
	if listener != nil {
		_ = listener.Close()
	}
	if turnListener != nil {
		_ = turnListener.Close()
	}
	if mux != nil {
		_ = mux.Close()
	} else if packetConn != nil {
		_ = packetConn.Close()
	}
	if allocation != nil {
		_ = allocation.Close()
	}
	if endpoint != nil {
		_ = endpoint.Close()
	}
}

func (o *directStunOperation) cleanup() {
	o.cleanupOnce.Do(func() {
		o.interruptDataPlane()
		if o.configuration != nil {
			o.configuration.clear()
		}
		o.resourceMu.Lock()
		o.control = nil
		o.resourceMu.Unlock()
	})
}

func derivedDirectStunNonce(capability Capability, session SessionID, role sessionauth.Role) sessionauth.Nonce {
	info := append([]byte(directStunProofDomain), byte(role))
	key, err := hkdf.Key(sha256.New, capability[:], session[:], string(info), 16)
	var nonce sessionauth.Nonce
	if err != nil {
		return nonce
	}
	copy(nonce[:], key)
	return nonce
}

func drainUDP(conn *net.UDPConn) {
	if conn == nil {
		return
	}
	_ = conn.SetReadDeadline(time.Now().Add(time.Millisecond))
	buffer := make([]byte, 1500)
	for {
		if _, _, err := conn.ReadFromUDP(buffer); err != nil {
			break
		}
	}
	_ = conn.SetReadDeadline(time.Time{})
}

func randomICECredentials() (string, string, error) {
	var raw [32]byte
	if _, err := io.ReadFull(rand.Reader, raw[:]); err != nil {
		return "", "", err
	}
	encoded := hex.EncodeToString(raw[:])
	return encoded[:8], encoded[8:], nil
}

func ErrOpenFailedWrap(err error) error {
	if err == nil {
		return ErrProtocol
	}
	return err
}

func safeDirectStunFailure(err error) error {
	switch {
	case errors.Is(err, room.ErrUnsupported):
		return room.ErrUnsupported
	case errors.Is(err, ErrUnreachable),
		errors.Is(err, directstun.ErrUnreachable),
		errors.Is(err, directstun.ErrRelayQuota),
		errors.Is(err, context.DeadlineExceeded),
		errors.Is(err, icequic.ErrQUICHandshake):
		return ErrUnreachable
	case errors.Is(err, icequic.ErrPeerIdentityMismatch),
		errors.Is(err, sessionauth.ErrInvalidProof),
		errors.Is(err, sessionauth.ErrProofMismatch),
		errors.Is(err, sessionauth.ErrProofExpired),
		errors.Is(err, sessionauth.ErrProofReplay),
		errors.Is(err, signaling.ErrAuthentication),
		errors.Is(err, signaling.ErrWrongContext),
		errors.Is(err, signaling.ErrExpired),
		errors.Is(err, signaling.ErrInvalidBundle):
		return ErrEnrollmentInvalid
	default:
		return ErrProtocol
	}
}

const sessionauthProofBytes = 162

var _ fabricOrchestrator = (*directStunOrchestrator)(nil)
var _ fabricOperation = (*directStunOperation)(nil)
