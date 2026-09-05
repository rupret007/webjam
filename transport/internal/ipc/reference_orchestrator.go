package ipc

import (
	"context"
	"encoding/hex"
	"errors"
	"sync"
	"time"

	"github.com/rupret007/webjam/transport/internal/help"
	"github.com/rupret007/webjam/transport/internal/icequic"
	"github.com/rupret007/webjam/transport/internal/limits"
	"github.com/rupret007/webjam/transport/internal/loopback"
	"github.com/rupret007/webjam/transport/internal/peer"
	"github.com/rupret007/webjam/transport/internal/profile"
	"github.com/rupret007/webjam/transport/internal/reference"
	"github.com/rupret007/webjam/transport/internal/room"
	"github.com/rupret007/webjam/transport/internal/sessionauth"
)

const (
	// The backoff keeps a ten-minute host wait below the reference service's
	// fixed 1,024-operation connection ceiling while retaining fast local
	// pairing when the guest is already enrolling.
	referencePollInitial = 20 * time.Millisecond
	referencePollMaximum = time.Second

	// IPC generations are each desktop's local callback/command fence, not a
	// shared value carried in a v3 invitation. A replacement host can be at
	// local generation 8 while a fresh guest starts at 1. Each invitation has
	// exactly one authenticated peer connection, so its wire epoch is 1.
	// Reset rotates InviteReference and the capability, producing a distinct
	// derived SessionID, enrollment token, relay domain and proof key. Old
	// packets/proofs therefore cannot enter the replacement invitation even
	// though its wire epoch is also 1. The runner keeps original local IPC
	// generations on every command check, connection fact and callback.
	referenceWireGeneration uint32 = 1
)

type referenceFabricOrchestrator struct {
	now     func() time.Time
	observe func(string, error)
}

func newReferenceFabricOrchestrator(now func() time.Time) fabricOrchestrator {
	return &referenceFabricOrchestrator{now: now}
}

func (o *referenceFabricOrchestrator) Start(
	ctx context.Context,
	configuration *enrollmentConfig,
	identity *icequic.Identity,
	endpoint loopback.Endpoint,
) (fabricOperation, error) {
	if ctx == nil || o == nil || o.now == nil || configuration == nil || identity == nil || endpoint == nil ||
		configuration.Profile.ID != profile.ReferenceLocalID || configuration.Generation == 0 ||
		configuration.SessionID == (SessionID{}) || configuration.EnrollmentCapability == (Capability{}) ||
		configuration.HostSPKISHA256 == (PublicPin{}) || configuration.ExpiresAtUnix == 0 ||
		len(identity.Certificate.Certificate) != 1 || identity.SPKIFingerprint == ([32]byte{}) {
		return nil, ErrEnrollmentInvalid
	}
	if configuration.Mode != "host" && configuration.Mode != "guest" {
		return nil, ErrEnrollmentInvalid
	}
	if configuration.Mode == "host" && PublicPin(identity.SPKIFingerprint) != configuration.HostSPKISHA256 {
		return nil, ErrEnrollmentInvalid
	}
	operationCtx, cancel := context.WithCancel(ctx)
	operation := &referenceFabricOperation{
		ctx: operationCtx, cancel: cancel, now: o.now, configuration: configuration,
		identity: identity, endpoint: endpoint,
		updates: make(chan fabricUpdate, limits.MaxHelpEventQueueDepth), done: make(chan struct{}),
		observe: o.observe,
	}
	go operation.run()
	return operation, nil
}

type referenceFabricOperation struct {
	ctx           context.Context
	cancel        context.CancelFunc
	now           func() time.Time
	configuration *enrollmentConfig
	identity      *icequic.Identity
	endpoint      loopback.Endpoint
	updates       chan fabricUpdate
	done          chan struct{}
	observe       func(string, error)
	stage         string

	resourceMu sync.Mutex
	client     *reference.Client
	token      *reference.RoleToken
	enrollment *reference.EnrollmentToken
	relay      *reference.RelayPacketConn
	listener   *icequic.Listener
	connection *icequic.Connection
	peer       *peer.Peer
	control    *room.Channel
	registered bool
	sequence   uint64

	cleanupOnce sync.Once
}

func (o *referenceFabricOperation) Updates() <-chan fabricUpdate { return o.updates }

func (o *referenceFabricOperation) Close(ctx context.Context) error {
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

func (o *referenceFabricOperation) run() {
	defer close(o.done)
	defer close(o.updates)
	defer o.cleanup()
	err := o.runFabric()
	if err != nil && o.ctx.Err() == nil {
		if o.observe != nil {
			o.observe(o.stage, err)
		}
		o.send(fabricUpdate{kind: updateFabricFailed, err: safeFabricFailure(err)})
	}
}

func (o *referenceFabricOperation) runFabric() error {
	o.stage = "role_token"
	token, err := reference.NewRoleToken()
	if err != nil {
		return err
	}
	o.resourceMu.Lock()
	o.token = token
	o.resourceMu.Unlock()
	o.stage = "enrollment_token"
	enrollment, err := reference.DeriveEnrollmentToken(o.referenceCapability(), o.referenceSession())
	if err != nil {
		return err
	}
	o.resourceMu.Lock()
	o.enrollment = enrollment
	o.resourceMu.Unlock()

	o.stage = "control_dial"
	client, err := reference.DialLocal(o.ctx)
	if err != nil {
		return err
	}
	o.resourceMu.Lock()
	o.client = client
	o.resourceMu.Unlock()

	if o.configuration.Mode == "host" {
		return o.runHost()
	}
	return o.runGuest()
}

func (o *referenceFabricOperation) runHost() error {
	expiresAt := time.Unix(int64(o.configuration.ExpiresAtUnix), 0)
	ttl := expiresAt.Sub(o.now()).Truncate(time.Second)
	if ttl < 30*time.Second || ttl > limits.MaxEnrollmentLifetime {
		return ErrEnrollmentInvalid
	}
	o.stage = "host_register"
	if err := o.client.Register(
		o.ctx, o.referenceSession(), o.token, o.enrollment, referenceWireGeneration, ttl,
	); err != nil {
		return err
	}
	o.resourceMu.Lock()
	o.registered = true
	o.resourceMu.Unlock()

	o.stage = "host_relay"
	relay, err := reference.OpenRelayLocal(
		o.referenceSession(), reference.RoleHost, o.token, referenceWireGeneration,
	)
	if err != nil {
		return err
	}
	o.resourceMu.Lock()
	o.relay = relay
	o.resourceMu.Unlock()
	o.stage = "host_quic_listen"
	listener, err := icequic.Listen(relay, *o.identity)
	if err != nil {
		return err
	}
	o.resourceMu.Lock()
	o.listener = listener
	o.resourceMu.Unlock()
	if !o.send(fabricUpdate{kind: updateHostRegistered}) {
		return o.ctx.Err()
	}

	o.stage = "host_bootstrap_poll"
	guestEnvelope, err := o.pollSignal(reference.RoleHost)
	if err != nil {
		return err
	}
	o.stage = "host_bootstrap_open"
	guestBootstrap, err := reference.OpenGuestBootstrap(
		o.referenceCapability(),
		reference.GuestBootstrapExpected{
			SessionID: o.referenceSession(), Generation: referenceWireGeneration,
			HostPin: reference.PeerPin(o.configuration.HostSPKISHA256),
		},
		guestEnvelope, o.now(), reference.NewBootstrapReplayCache(),
	)
	clear(guestEnvelope)
	if err != nil {
		return err
	}
	clear(guestBootstrap.CertificateDER)

	o.stage = "host_ack_nonce"
	ackNonce, err := reference.NewBootstrapNonce()
	if err != nil {
		return err
	}
	acknowledgment := reference.HostAcknowledgment{
		SessionID: o.referenceSession(), Generation: referenceWireGeneration,
		HostPin: reference.PeerPin(o.configuration.HostSPKISHA256), GuestPin: guestBootstrap.GuestPin,
		GuestNonce: guestBootstrap.Nonce, Acknowledgment: ackNonce, ExpiresAt: expiresAt,
	}
	o.stage = "host_ack_seal"
	ackEnvelope, err := reference.SealHostAcknowledgment(o.referenceCapability(), acknowledgment, o.now())
	if err != nil {
		return err
	}
	o.stage = "host_ack_signal"
	err = o.signal(reference.RoleHost, ackEnvelope)
	clear(ackEnvelope)
	if err != nil {
		return err
	}

	o.stage = "host_quic_accept"
	connection, err := listener.Accept(o.ctx)
	if err != nil {
		return err
	}
	o.resourceMu.Lock()
	o.connection = connection
	o.resourceMu.Unlock()

	hostBinding := sessionauth.Binding{
		SessionID: sessionauth.SessionID(o.configuration.SessionID), SenderRole: sessionauth.RoleHost,
		Generation: referenceWireGeneration, HostPin: sessionauth.PeerPin(o.configuration.HostSPKISHA256),
		GuestPin: sessionauth.PeerPin(guestBootstrap.GuestPin), Nonce: sessionauth.Nonce(ackNonce),
		ExpiresAt: acknowledgment.ExpiresAt,
	}
	o.stage = "host_proof_create"
	hostProof, err := sessionauth.CreateProof(
		connection, sessionauth.Capability(o.configuration.EnrollmentCapability), hostBinding, o.now(),
	)
	if err != nil {
		return err
	}
	o.stage = "host_proof_signal"
	err = o.signal(reference.RoleHost, hostProof)
	clear(hostProof)
	if err != nil {
		return err
	}
	o.stage = "host_proof_poll"
	guestProof, err := o.pollSignal(reference.RoleHost)
	if err != nil {
		return err
	}
	guestBinding := sessionauth.Binding{
		SessionID: sessionauth.SessionID(o.configuration.SessionID), SenderRole: sessionauth.RoleGuest,
		Generation: referenceWireGeneration, HostPin: sessionauth.PeerPin(o.configuration.HostSPKISHA256),
		GuestPin: sessionauth.PeerPin(guestBootstrap.GuestPin), Nonce: sessionauth.Nonce(guestBootstrap.Nonce),
		ExpiresAt: guestBootstrap.ExpiresAt,
	}
	o.stage = "host_proof_verify"
	err = connection.VerifyAndAuthorize(
		sessionauth.Capability(o.configuration.EnrollmentCapability), guestBinding, guestProof,
		o.now(), sessionauth.NewReplayCache(),
	)
	clear(guestProof)
	if err != nil {
		return err
	}
	o.stage = "host_peer"
	return o.runPeer(peer.ModeHost)
}

func (o *referenceFabricOperation) runGuest() error {
	o.stage = "guest_enroll"
	if err := o.client.Enroll(
		o.ctx, o.referenceSession(), o.enrollment, o.token,
	); err != nil {
		return err
	}
	o.stage = "guest_relay"
	relay, err := reference.OpenRelayLocal(
		o.referenceSession(), reference.RoleGuest, o.token, referenceWireGeneration,
	)
	if err != nil {
		return err
	}
	o.resourceMu.Lock()
	o.relay = relay
	o.resourceMu.Unlock()

	o.stage = "guest_bootstrap_nonce"
	guestNonce, err := reference.NewBootstrapNonce()
	if err != nil {
		return err
	}
	guestPin := reference.PeerPin(o.identity.SPKIFingerprint)
	guestBootstrap := reference.GuestBootstrap{
		SessionID: o.referenceSession(), Generation: referenceWireGeneration,
		HostPin: reference.PeerPin(o.configuration.HostSPKISHA256), GuestPin: guestPin,
		Nonce: guestNonce, ExpiresAt: time.Unix(int64(o.configuration.ExpiresAtUnix), 0),
		CertificateDER: append([]byte(nil), o.identity.Certificate.Certificate[0]...),
	}
	o.stage = "guest_bootstrap_seal"
	guestEnvelope, err := reference.SealGuestBootstrap(o.referenceCapability(), guestBootstrap, o.now())
	clear(guestBootstrap.CertificateDER)
	if err != nil {
		return err
	}
	o.stage = "guest_bootstrap_signal"
	err = o.signal(reference.RoleGuest, guestEnvelope)
	clear(guestEnvelope)
	if err != nil {
		return err
	}

	o.stage = "guest_ack_poll"
	ackEnvelope, err := o.pollSignal(reference.RoleGuest)
	if err != nil {
		return err
	}
	o.stage = "guest_ack_open"
	acknowledgment, err := reference.OpenHostAcknowledgment(
		o.referenceCapability(),
		reference.HostAcknowledgmentExpected{
			SessionID: o.referenceSession(), Generation: referenceWireGeneration,
			HostPin: reference.PeerPin(o.configuration.HostSPKISHA256), GuestPin: guestPin,
			GuestNonce: guestNonce,
		},
		ackEnvelope, o.now(), reference.NewBootstrapReplayCache(),
	)
	clear(ackEnvelope)
	if err != nil {
		return err
	}

	o.stage = "guest_quic_dial"
	connection, err := icequic.Dial(
		o.ctx, relay, relay.PeerAddr(), *o.identity,
		hex.EncodeToString(o.configuration.HostSPKISHA256[:]),
	)
	if err != nil {
		return err
	}
	o.resourceMu.Lock()
	o.connection = connection
	o.resourceMu.Unlock()

	guestBinding := sessionauth.Binding{
		SessionID: sessionauth.SessionID(o.configuration.SessionID), SenderRole: sessionauth.RoleGuest,
		Generation: referenceWireGeneration, HostPin: sessionauth.PeerPin(o.configuration.HostSPKISHA256),
		GuestPin: sessionauth.PeerPin(guestPin), Nonce: sessionauth.Nonce(guestNonce),
		ExpiresAt: guestBootstrap.ExpiresAt,
	}
	o.stage = "guest_proof_create"
	guestProof, err := sessionauth.CreateProof(
		connection, sessionauth.Capability(o.configuration.EnrollmentCapability), guestBinding, o.now(),
	)
	if err != nil {
		return err
	}
	o.stage = "guest_proof_signal"
	err = o.signal(reference.RoleGuest, guestProof)
	clear(guestProof)
	if err != nil {
		return err
	}
	o.stage = "guest_proof_poll"
	hostProof, err := o.pollSignal(reference.RoleGuest)
	if err != nil {
		return err
	}
	hostBinding := sessionauth.Binding{
		SessionID: sessionauth.SessionID(o.configuration.SessionID), SenderRole: sessionauth.RoleHost,
		Generation: referenceWireGeneration, HostPin: sessionauth.PeerPin(o.configuration.HostSPKISHA256),
		GuestPin: sessionauth.PeerPin(guestPin), Nonce: sessionauth.Nonce(acknowledgment.Acknowledgment),
		ExpiresAt: acknowledgment.ExpiresAt,
	}
	o.stage = "guest_proof_verify"
	err = connection.VerifyAndAuthorize(
		sessionauth.Capability(o.configuration.EnrollmentCapability), hostBinding, hostProof,
		o.now(), sessionauth.NewReplayCache(),
	)
	clear(hostProof)
	if err != nil {
		return err
	}
	o.stage = "guest_peer"
	return o.runPeer(peer.ModeGuest)
}

func (o *referenceFabricOperation) runPeer(mode peer.Mode) error {
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
	if err := liveControl.Handshake(o.ctx); err != nil {
		return err
	}
	peerResult := make(chan error, 1)
	helpResult := make(chan error, 1)
	go func() { peerResult <- livePeer.Run(o.ctx) }()
	select {
	case <-livePeer.Ready():
		if !o.send(fabricUpdate{kind: updatePeerConnected}) {
			return o.ctx.Err()
		}
		go o.receiveControl(liveControl, helpResult)
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

func (o *referenceFabricOperation) receiveControl(channel *room.Channel, result chan<- error) {
	for {
		event, err := channel.Receive(o.ctx)
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
		if !o.send(update) {
			clear(update.helpText)
			result <- o.ctx.Err()
			return
		}
	}
}
func (o *referenceFabricOperation) SendHelp(ctx context.Context, requestID uint64, text string) error {
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
func (o *referenceFabricOperation) PublishRoomState(ctx context.Context, state *room.State) error {
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

func (o *referenceFabricOperation) signal(role reference.Role, payload []byte) error {
	sequence, err := o.nextSequence()
	if err != nil {
		return err
	}
	return o.client.Signal(
		o.ctx, o.referenceSession(), role, o.token, referenceWireGeneration, sequence, payload,
	)
}

func (o *referenceFabricOperation) pollSignal(role reference.Role) ([]byte, error) {
	timer := time.NewTimer(0)
	defer timer.Stop()
	pollDelay := referencePollInitial
	for {
		select {
		case <-o.ctx.Done():
			return nil, o.ctx.Err()
		case <-timer.C:
		}
		sequence, err := o.nextSequence()
		if err != nil {
			return nil, err
		}
		payload, ok, err := o.client.Poll(
			o.ctx, o.referenceSession(), role, o.token, referenceWireGeneration, sequence,
		)
		if err != nil {
			return nil, err
		}
		if ok {
			return payload, nil
		}
		timer.Reset(pollDelay)
		if pollDelay < referencePollMaximum {
			pollDelay *= 2
			if pollDelay > referencePollMaximum {
				pollDelay = referencePollMaximum
			}
		}
	}
}

func (o *referenceFabricOperation) nextSequence() (uint64, error) {
	if o.sequence >= uint64(^uint64(0)>>1) {
		return 0, ErrProtocol
	}
	o.sequence++
	return o.sequence, nil
}

func (o *referenceFabricOperation) referenceSession() reference.SessionID {
	return reference.SessionID(o.configuration.SessionID)
}

func (o *referenceFabricOperation) referenceCapability() reference.Capability {
	return reference.Capability(o.configuration.EnrollmentCapability)
}

func (o *referenceFabricOperation) send(update fabricUpdate) bool {
	select {
	case o.updates <- update:
		return true
	case <-o.ctx.Done():
		return false
	}
}

func (o *referenceFabricOperation) interruptDataPlane() {
	o.resourceMu.Lock()
	livePeer, connection, listener, relay := o.peer, o.connection, o.listener, o.relay
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
	if relay != nil {
		_ = relay.Close()
	}
	if endpoint != nil {
		_ = endpoint.Close()
	}
}

func (o *referenceFabricOperation) cleanup() {
	o.cleanupOnce.Do(func() {
		o.interruptDataPlane()
		o.resourceMu.Lock()
		client, token, enrollment, registered := o.client, o.token, o.enrollment, o.registered
		o.resourceMu.Unlock()
		if registered && client != nil && token != nil {
			closeCtx, cancel := context.WithTimeout(context.Background(), limits.ShutdownLimit)
			if sequence, err := o.nextSequence(); err == nil {
				_ = client.CloseSession(
					closeCtx, o.referenceSession(), reference.RoleHost, token,
					referenceWireGeneration, sequence,
				)
			}
			cancel()
		}
		if client != nil {
			_ = client.Close()
		}
		if token != nil {
			token.Destroy()
		}
		if enrollment != nil {
			enrollment.Destroy()
		}
		if o.configuration != nil {
			o.configuration.clear()
		}
		o.resourceMu.Lock()
		o.control = nil
		o.resourceMu.Unlock()
	})
}

func safeFabricFailure(err error) error {
	switch {
	case errors.Is(err, room.ErrUnsupported):
		return room.ErrUnsupported
	case errors.Is(err, reference.ErrUnauthorized),
		errors.Is(err, reference.ErrEnrollmentUsed),
		errors.Is(err, reference.ErrReplay),
		errors.Is(err, reference.ErrBootstrap),
		errors.Is(err, reference.ErrBootstrapContext),
		errors.Is(err, reference.ErrBootstrapExpired),
		errors.Is(err, reference.ErrBootstrapReplay),
		errors.Is(err, icequic.ErrPeerIdentityMismatch),
		errors.Is(err, sessionauth.ErrInvalidProof),
		errors.Is(err, sessionauth.ErrProofMismatch),
		errors.Is(err, sessionauth.ErrProofExpired),
		errors.Is(err, sessionauth.ErrProofReplay):
		return ErrEnrollmentInvalid
	default:
		return ErrProtocol
	}
}

var _ fabricOrchestrator = (*referenceFabricOrchestrator)(nil)
var _ fabricOperation = (*referenceFabricOperation)(nil)
