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

var (
	gatherListenCandidates = directstun.Gather
	parseDialTarget        = directstun.ParseDialTarget
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

	resourceMu  sync.Mutex
	packetConn  *net.UDPConn
	mux         *directstun.Mux
	listener    *icequic.Listener
	connection  *icequic.Connection
	peer        *peer.Peer
	control     *room.Channel
	guestSignal chan []byte
	icePassword string
	bundleNonce signaling.BundleNonce

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

	candidates, public, err := gatherListenCandidates(o.enrollmentCtx, conn)
	if err != nil || len(candidates) == 0 {
		return ErrOpenFailedWrap(err)
	}
	drainUDP(conn)
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

	accepted := make(chan acceptResult, 1)
	go func() {
		connection, acceptErr := listener.Accept(o.enrollmentCtx)
		accepted <- acceptResult{connection: connection, err: acceptErr}
	}()
	for {
		select {
		case envelope := <-guestSignal:
			guestBundle, parseErr := o.openGuestBundle(envelope)
			if parseErr != nil {
				return parseErr
			}
			mux.SetRemotes(candidateAddrs(guestBundle.Candidates))
		case result := <-accepted:
			if result.err != nil {
				if o.enrollmentCtx.Err() != nil {
					return ErrUnreachable
				}
				return result.err
			}
			o.resourceMu.Lock()
			o.connection = result.connection
			o.resourceMu.Unlock()
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
	localCandidates, _, err := gatherListenCandidates(o.enrollmentCtx, conn)
	if err != nil || len(localCandidates) == 0 {
		return ErrOpenFailedWrap(err)
	}
	drainUDP(conn)
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
	if len(targets) == 0 {
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
	nominated, err := mux.WaitNominated(o.enrollmentCtx)
	if err != nil {
		return ErrUnreachable
	}
	dialCtx, cancel := context.WithTimeout(o.enrollmentCtx, directStunDialAttempt)
	defer cancel()
	connection, err := icequic.Dial(
		dialCtx, mux, nominated, *o.identity,
		hex.EncodeToString(o.configuration.HostSPKISHA256[:]),
	)
	if err != nil {
		if o.enrollmentCtx.Err() != nil || errors.Is(err, context.DeadlineExceeded) {
			return ErrUnreachable
		}
		return err
	}
	o.resourceMu.Lock()
	o.connection = connection
	o.resourceMu.Unlock()
	if err = o.exchangeGuestProofs(connection, expiresAt); err != nil {
		return err
	}
	return o.runPeer(peer.ModeGuest)
}

type acceptResult struct {
	connection *icequic.Connection
	err        error
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
		if !o.sendWithContext(pumpCtx, fabricUpdate{kind: updatePeerConnected}) {
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
	livePeer, connection, listener, mux, packetConn := o.peer, o.connection, o.listener, o.mux, o.packetConn
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
	if mux != nil {
		_ = mux.Close()
	} else if packetConn != nil {
		_ = packetConn.Close()
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
