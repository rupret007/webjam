package ipc

import (
	"bufio"
	"context"
	"errors"
	"io"
	"net"
	"sync"
	"time"

	"github.com/rupret007/webjam/transport/internal/help"
	"github.com/rupret007/webjam/transport/internal/icequic"
	"github.com/rupret007/webjam/transport/internal/limits"
	"github.com/rupret007/webjam/transport/internal/loopback"
	"github.com/rupret007/webjam/transport/internal/profile"
	"github.com/rupret007/webjam/transport/internal/room"
)

const (
	CodeOK                      = "ok"
	CodeProtocolViolation       = "protocol_violation"
	CodePeerAlreadyOpen         = "peer_already_open"
	CodePeerNotOpen             = "peer_not_open"
	CodeOpenFailed              = "open_failed"
	CodeIdentityNotPrepared     = "identity_not_prepared"
	CodeUnsupportedProfile      = "unsupported_profile"
	CodeEnrollmentInvalid       = "enrollment_invalid"
	CodeHelpNotReady            = "help_not_ready"
	CodeRoomNotReady            = "room_state_not_ready"
	CodeRoomInvalid             = "room_state_invalid"
	CodeRoomRateLimited         = "room_state_rate_limited"
	CodePeerProtocolUnsupported = "peer_protocol_unsupported"
	CodeHelpInvalid             = "help_invalid"
	CodeHelpRateLimited         = "help_rate_limited"
	CodeHelpQueueFull           = "help_queue_full"
)

type emitter struct {
	writer io.Writer
	mu     sync.Mutex
}

func (e *emitter) emit(event Event) error {
	encoded, err := MarshalEvent(event)
	if err != nil {
		return err
	}
	e.mu.Lock()
	defer e.mu.Unlock()
	for len(encoded) > 0 {
		written, writeErr := e.writer.Write(encoded)
		if writeErr != nil {
			return writeErr
		}
		if written <= 0 || written > len(encoded) {
			return io.ErrShortWrite
		}
		encoded = encoded[written:]
	}
	return nil
}

type endpointFactory interface {
	Open(mode string, targetPort int) (loopback.Endpoint, error)
}

type systemEndpointFactory struct{}

func (systemEndpointFactory) Open(mode string, targetPort int) (loopback.Endpoint, error) {
	switch mode {
	case "guest":
		return loopback.NewGuestProxy()
	case "host":
		return loopback.NewHostProxy(&net.UDPAddr{IP: net.IPv4(127, 0, 0, 1), Port: targetPort})
	default:
		return nil, ErrProtocol
	}
}

// A fabric operation owns the enrollment configuration and loopback endpoint
// after Start succeeds. Updates are bounded lifecycle facts, never underlying
// errors or network values. updatePeerConnected may be sent only after mutual
// TLS, exact pin validation, bidirectional exporter proof, and peer pumps start.
type fabricUpdateKind uint8

const (
	updateHostRegistered fabricUpdateKind = iota + 1
	updatePeerConnected
	updateHelpReceived
	updateHelpDelivered
	updateRoomStateReceived
	updateFabricFailed
)

type fabricUpdate struct {
	kind          fabricUpdateKind
	err           error
	helpRequestID uint64
	helpText      []byte
	roomState     *room.State
}

type fabricOperation interface {
	Updates() <-chan fabricUpdate
	SendHelp(context.Context, uint64, string) error
	PublishRoomState(context.Context, *room.State) error
	Close(context.Context) error
}

type fabricOrchestrator interface {
	Start(
		context.Context,
		*enrollmentConfig,
		*icequic.Identity,
		loopback.Endpoint,
	) (fabricOperation, error)
}

type lineResult struct {
	line []byte
	err  error
}

func scanLines(ctx context.Context, input io.Reader) <-chan lineResult {
	results := make(chan lineResult)
	go func() {
		defer close(results)
		scanner := bufio.NewScanner(input)
		scanner.Buffer(make([]byte, 4096), limits.MaxIPCLineBytes)
		for scanner.Scan() {
			token := scanner.Bytes()
			line := append([]byte(nil), token...)
			clear(token)
			select {
			case results <- lineResult{line: line}:
			case <-ctx.Done():
				clear(line)
				return
			}
		}
		if err := scanner.Err(); err != nil {
			select {
			case results <- lineResult{err: ErrProtocol}:
			case <-ctx.Done():
			}
		}
	}()
	if closer, ok := input.(io.ReadCloser); ok {
		go func() {
			<-ctx.Done()
			_ = closer.Close()
		}()
	}
	return results
}

type enrollmentConfig struct {
	Profile              profile.Profile
	Mode                 string
	TargetPort           int
	Generation           uint32
	SessionReference     Reference
	InviteReference      Reference
	EnrollmentCapability Capability
	ExpiresAtUnix        uint64
	HostSPKISHA256       PublicPin
	SessionID            SessionID
}

func enrollmentFromCommand(command *Command, hostPin PublicPin) *enrollmentConfig {
	configuration := &enrollmentConfig{
		Profile: command.Profile, Mode: command.Mode, TargetPort: command.TargetPort,
		Generation: command.Generation, SessionReference: command.SessionReference,
		InviteReference: command.InviteReference, EnrollmentCapability: command.EnrollmentCapability,
		ExpiresAtUnix: command.ExpiresAtUnix, HostSPKISHA256: command.HostSPKISHA256,
		SessionID: command.SessionID,
	}
	if command.Mode == "host" {
		configuration.HostSPKISHA256 = hostPin
	}
	return configuration
}

func (c *enrollmentConfig) clear() {
	if c == nil {
		return
	}
	clear(c.SessionReference[:])
	clear(c.InviteReference[:])
	clear(c.EnrollmentCapability[:])
	clear(c.HostSPKISHA256[:])
	clear(c.SessionID[:])
	c.Profile = profile.Profile{}
	c.Mode = ""
	c.TargetPort = 0
	c.Generation = 0
	c.ExpiresAtUnix = 0
}

type peerMetadata struct {
	commandID    uint64
	mode         string
	profileID    string
	generation   uint32
	loopbackPort int
}

type runnerState struct {
	active         *peerMetadata
	recentlyClosed *peerMetadata
	hostRegistered bool
	connected      bool
	operation      fabricOperation
	updates        <-chan fabricUpdate
	openCancel     context.CancelFunc
	localIdentity  *icequic.Identity
	openConsumed   bool
	factory        endpointFactory
	orchestrator   fabricOrchestrator
}

func (s *runnerState) stateName() string {
	switch {
	case s.connected:
		return "connected"
	case s.hostRegistered:
		return "host_waiting"
	case s.operation != nil:
		return "connecting"
	case s.openConsumed:
		return "closed"
	case s.localIdentity != nil:
		return "identity_ready"
	default:
		return "idle"
	}
}

func (s *runnerState) metadataEvent(id uint64, eventType, state string) Event {
	event := Event{ID: id, Type: eventType, Code: CodeOK, State: state}
	if s.active != nil {
		event.Mode = s.active.mode
		event.ProfileID = s.active.profileID
		event.Generation = s.active.generation
		event.LoopbackPort = s.active.loopbackPort
	}
	return event
}

func (s *runnerState) helpEvent(
	id uint64,
	eventType string,
	requestID uint64,
	text string,
) Event {
	event := Event{
		ID: id, Type: eventType, Code: CodeOK, State: "connected",
		RequestID: requestID, Text: text,
	}
	if s.active != nil {
		event.Mode = s.active.mode
		event.ProfileID = s.active.profileID
		event.Generation = s.active.generation
	}
	return event
}

func (s *runnerState) closeActive(preserveHostIdentity bool) peerMetadata {
	var metadata peerMetadata
	if s.active != nil {
		metadata = *s.active
	}
	if s.openCancel != nil {
		s.openCancel()
		s.openCancel = nil
	}
	operation := s.operation
	s.operation = nil
	s.updates = nil
	s.active = nil
	s.hostRegistered = false
	s.connected = false
	if operation != nil {
		closeCtx, cancel := context.WithTimeout(context.Background(), limits.ShutdownLimit)
		_ = operation.Close(closeCtx)
		cancel()
	}
	if !preserveHostIdentity || metadata.mode != "host" {
		s.destroyIdentity()
	}
	return metadata
}

func (s *runnerState) destroyIdentity() {
	if s.localIdentity != nil {
		s.localIdentity.Destroy()
		s.localIdentity = nil
	}
}

func (s *runnerState) destroy() {
	s.closeActive(false)
	s.recentlyClosed = nil
	s.destroyIdentity()
}

func Run(ctx context.Context, input io.Reader, output io.Writer, build string) error {
	return runWithFactoryAndClock(
		ctx, input, output, build, systemEndpointFactory{}, newReferenceFabricOrchestrator(time.Now), time.Now,
	)
}

func runWithFactoryAndClock(
	ctx context.Context,
	input io.Reader,
	output io.Writer,
	build string,
	factory endpointFactory,
	orchestrator fabricOrchestrator,
	now func() time.Time,
) error {
	if factory == nil || orchestrator == nil || now == nil {
		return ErrProtocol
	}
	events := &emitter{writer: output}
	build = safeBuildID(build)
	if err := events.emit(Event{ID: 0, Type: "ready", Code: CodeOK, State: "idle", Build: build}); err != nil {
		return err
	}

	state := &runnerState{factory: factory, orchestrator: orchestrator}
	defer state.destroy()
	scanCtx, cancelScan := context.WithCancel(ctx)
	defer cancelScan()
	lines := scanLines(scanCtx, input)
	for {
		// Preserve causal event order when an operation has already completed a
		// lifecycle boundary: publish that boundary before accepting a later
		// desktop command that happened to be buffered on stdin.
		if state.updates != nil {
			select {
			case update, ok := <-state.updates:
				if !ok {
					update = fabricUpdate{kind: updateFabricFailed, err: ErrProtocol}
				}
				if err := state.handleFabricUpdate(update, events); err != nil {
					return err
				}
				continue
			default:
			}
		}
		select {
		case <-ctx.Done():
			state.destroy()
			_ = events.emit(Event{ID: 0, Type: "stopped", Code: CodeOK, State: "stopped"})
			return nil
		case update, ok := <-state.updates:
			if !ok {
				if state.operation == nil {
					continue
				}
				update = fabricUpdate{kind: updateFabricFailed, err: ErrProtocol}
			}
			if err := state.handleFabricUpdate(update, events); err != nil {
				return err
			}
		case result, ok := <-lines:
			if !ok {
				return nil
			}
			if result.err != nil {
				_ = events.emit(Event{ID: 0, Type: "error", Code: CodeProtocolViolation, State: "failed"})
				return ErrProtocol
			}
			command, parseErr := ParseCommandAt(result.line, now())
			clear(result.line)
			if parseErr != nil {
				code := parseErrorCode(parseErr)
				commandID := command.ID
				command.ClearSensitive()
				_ = events.emit(Event{ID: commandID, Type: "error", Code: code, State: "failed"})
				return parseErr
			}
			stop, handleErr := state.handle(ctx, &command, events, build, now)
			command.ClearSensitive()
			if handleErr != nil {
				return handleErr
			}
			if stop {
				return nil
			}
		}
	}
}

func parseErrorCode(err error) string {
	switch {
	case errors.Is(err, ErrUnsupportedProfile):
		return CodeUnsupportedProfile
	case errors.Is(err, ErrEnrollmentInvalid):
		return CodeEnrollmentInvalid
	default:
		return CodeProtocolViolation
	}
}

func (s *runnerState) handle(
	ctx context.Context,
	command *Command,
	events *emitter,
	build string,
	now func() time.Time,
) (bool, error) {
	switch command.Type {
	case CommandHello:
		event := Event{ID: command.ID, Type: "hello", Code: CodeOK, State: s.stateName(), Build: build}
		if s.active != nil {
			event.Mode = s.active.mode
			event.ProfileID = s.active.profileID
			event.Generation = s.active.generation
		}
		return false, events.emit(event)
	case CommandPrepareHost:
		if s.operation != nil || s.localIdentity != nil || s.openConsumed {
			return false, events.emit(Event{ID: command.ID, Type: "error", Code: CodeEnrollmentInvalid, State: s.stateName()})
		}
		identity, err := icequic.NewEphemeralIdentity(now(), limits.HostIdentityLifetime)
		if err != nil {
			return false, events.emit(Event{ID: command.ID, Type: "error", Code: CodeOpenFailed, State: "idle"})
		}
		s.localIdentity = &identity
		pin := PublicPin(identity.SPKIFingerprint)
		encodedPin := encodePublicPin(pin)
		clear(pin[:])
		return false, events.emit(Event{
			ID: command.ID, Type: "host_prepared", Code: CodeOK, State: "identity_ready",
			HostSPKISHA256: encodedPin,
		})
	case CommandOpenPeer:
		return false, s.openPeer(ctx, command, events, now)
	case CommandSendHelp:
		if s.operation == nil || s.active == nil || !s.connected ||
			command.Generation != s.active.generation {
			return false, events.emit(Event{
				ID: command.ID, Type: "error", Code: CodeHelpNotReady,
				State: s.stateName(),
			})
		}
		helpCtx, cancel := context.WithTimeout(ctx, limits.HelpOperationLimit)
		err := s.operation.SendHelp(
			helpCtx,
			command.ID,
			string(command.HelpText),
		)
		cancel()
		if err != nil {
			return false, events.emit(Event{
				ID: command.ID, Type: "error", Code: helpErrorCode(err),
				State: s.stateName(),
			})
		}
		return false, events.emit(
			s.helpEvent(command.ID, "help_accepted", command.ID, ""),
		)
	case CommandPublishRoomState:
		if s.operation == nil || s.active == nil || !s.connected || command.Generation != s.active.generation {
			return false, events.emit(Event{ID: command.ID, Type: "error", Code: CodeRoomNotReady, State: s.stateName()})
		}
		if s.active.mode != "host" {
			return false, events.emit(Event{ID: command.ID, Type: "error", Code: CodeRoomInvalid, State: s.stateName()})
		}
		publishCtx, cancel := context.WithTimeout(ctx, limits.HelpOperationLimit)
		err := s.operation.PublishRoomState(publishCtx, command.RoomState)
		cancel()
		if err != nil {
			return false, events.emit(Event{ID: command.ID, Type: "error", Code: roomErrorCode(err), State: s.stateName()})
		}
		return false, events.emit(s.helpEvent(command.ID, "room_state_accepted", command.ID, ""))
	case CommandClosePeer:
		if s.operation == nil {
			if s.recentlyClosed != nil {
				// A remote close can finish the bounded teardown immediately
				// before the desktop's explicit close command arrives. Acknowledge
				// that already-completed boundary once, with the original public
				// metadata, instead of turning a successful cleanup race into an
				// application error.
				metadata := *s.recentlyClosed
				s.recentlyClosed = nil
				return false, events.emit(Event{
					ID: command.ID, Type: "peer_closed", Code: CodeOK, State: "closed",
					Mode: metadata.mode, ProfileID: metadata.profileID, Generation: metadata.generation,
				})
			}
			return false, events.emit(Event{ID: command.ID, Type: "error", Code: CodePeerNotOpen, State: s.stateName()})
		}
		metadata := s.closeActive(true)
		if metadata.mode == "host" {
			// close_peer is the explicit host invitation reset boundary. The
			// current service session is revoked, while the prepared identity
			// and public pin remain stable for the next invitation.
			s.openConsumed = false
		}
		return false, events.emit(Event{
			ID: command.ID, Type: "peer_closed", Code: CodeOK, State: "closed",
			Mode: metadata.mode, ProfileID: metadata.profileID, Generation: metadata.generation,
		})
	case CommandShutdown:
		s.destroy()
		return true, events.emit(Event{ID: command.ID, Type: "stopped", Code: CodeOK, State: "stopped"})
	default:
		return false, ErrProtocol
	}
}

func (s *runnerState) openPeer(
	ctx context.Context,
	command *Command,
	events *emitter,
	now func() time.Time,
) error {
	if s.operation != nil {
		return events.emit(Event{ID: command.ID, Type: "error", Code: CodePeerAlreadyOpen, State: s.stateName()})
	}
	if s.openConsumed {
		return events.emit(Event{ID: command.ID, Type: "error", Code: CodeEnrollmentInvalid, State: "closed"})
	}
	startTime := now()
	expiresAt := time.Unix(int64(command.ExpiresAtUnix), 0)
	if !expiresAt.After(startTime) {
		return events.emit(Event{ID: command.ID, Type: "error", Code: CodeEnrollmentInvalid, State: s.stateName()})
	}

	var hostPin PublicPin
	guestIdentity := false
	switch command.Mode {
	case "host":
		if s.localIdentity == nil {
			return events.emit(Event{ID: command.ID, Type: "error", Code: CodeIdentityNotPrepared, State: "idle"})
		}
		hostPin = PublicPin(s.localIdentity.SPKIFingerprint)
	case "guest":
		if s.localIdentity != nil {
			return events.emit(Event{ID: command.ID, Type: "error", Code: CodeEnrollmentInvalid, State: "identity_ready"})
		}
		identity, err := icequic.NewEphemeralIdentity(startTime, limits.HostIdentityLifetime)
		if err != nil {
			return events.emit(Event{ID: command.ID, Type: "error", Code: CodeOpenFailed, State: "idle"})
		}
		s.localIdentity = &identity
		guestIdentity = true
	default:
		return ErrProtocol
	}

	configuration := enrollmentFromCommand(command, hostPin)
	clear(hostPin[:])
	endpoint, err := s.factory.Open(configuration.Mode, configuration.TargetPort)
	if err != nil {
		configuration.clear()
		if guestIdentity {
			s.destroyIdentity()
		}
		return events.emit(Event{ID: command.ID, Type: "error", Code: CodeOpenFailed, State: s.stateName()})
	}
	operationCtx, cancel := context.WithDeadline(ctx, expiresAt)
	operation, err := s.orchestrator.Start(operationCtx, configuration, s.localIdentity, endpoint)
	if err != nil || operation == nil || operation.Updates() == nil {
		cancel()
		_ = endpoint.Close()
		configuration.clear()
		if guestIdentity {
			s.destroyIdentity()
		}
		code := CodeOpenFailed
		if errors.Is(err, ErrEnrollmentInvalid) {
			code = CodeEnrollmentInvalid
		}
		return events.emit(Event{ID: command.ID, Type: "error", Code: code, State: s.stateName()})
	}

	s.operation = operation
	s.updates = operation.Updates()
	s.openCancel = cancel
	s.openConsumed = true
	s.recentlyClosed = nil
	s.active = &peerMetadata{
		commandID: command.ID, mode: command.Mode, profileID: command.Profile.ID,
		generation: command.Generation, loopbackPort: endpoint.LocalPort(),
	}
	return nil
}

func (s *runnerState) handleFabricUpdate(update fabricUpdate, events *emitter) error {
	if s.operation == nil || s.active == nil {
		return ErrProtocol
	}
	switch update.kind {
	case updateHostRegistered:
		if update.err != nil || s.active.mode != "host" || s.hostRegistered || s.connected {
			return s.failFabric(events, ErrProtocol)
		}
		s.hostRegistered = true
		return events.emit(s.metadataEvent(s.active.commandID, "host_registered", "host_waiting"))
	case updatePeerConnected:
		if update.err != nil || s.connected || (s.active.mode == "host" && !s.hostRegistered) {
			return s.failFabric(events, ErrProtocol)
		}
		s.connected = true
		id := s.active.commandID
		if s.active.mode == "host" {
			id = 0
		}
		return events.emit(s.metadataEvent(id, "peer_connected", "connected"))
	case updateHelpReceived:
		if update.err != nil || !s.connected || update.helpRequestID == 0 ||
			len(update.helpText) == 0 {
			clear(update.helpText)
			return s.failFabric(events, ErrProtocol)
		}
		text := string(update.helpText)
		clear(update.helpText)
		return events.emit(
			s.helpEvent(0, "help_received", update.helpRequestID, text),
		)
	case updateHelpDelivered:
		if update.err != nil || !s.connected || update.helpRequestID == 0 ||
			len(update.helpText) != 0 {
			clear(update.helpText)
			return s.failFabric(events, ErrProtocol)
		}
		return events.emit(
			s.helpEvent(0, "help_delivered", update.helpRequestID, ""),
		)
	case updateRoomStateReceived:
		if update.err != nil || !s.connected || s.active.mode != "guest" || update.roomState == nil || update.roomState.Validate() != nil {
			return s.failFabric(events, ErrProtocol)
		}
		event := s.helpEvent(0, "room_state_received", 0, "")
		event.RoomState = update.roomState
		return events.emit(event)
	case updateFabricFailed:
		return s.failFabric(events, update.err)
	default:
		return s.failFabric(events, ErrProtocol)
	}
}

func roomErrorCode(err error) string {
	switch {
	case errors.Is(err, room.ErrInvalid), errors.Is(err, room.ErrReplay), errors.Is(err, room.ErrWrongPeer):
		return CodeRoomInvalid
	case errors.Is(err, room.ErrRateLimited):
		return CodeRoomRateLimited
	default:
		return CodeRoomNotReady
	}
}

func helpErrorCode(err error) string {
	switch {
	case errors.Is(err, help.ErrInvalidMessage), errors.Is(err, help.ErrReplay):
		return CodeHelpInvalid
	case errors.Is(err, help.ErrRateLimited):
		return CodeHelpRateLimited
	case errors.Is(err, help.ErrQueueFull):
		return CodeHelpQueueFull
	default:
		return CodeHelpNotReady
	}
}

func (s *runnerState) failFabric(events *emitter, failure error) error {
	if failure == nil {
		failure = ErrProtocol
	}
	id := s.active.commandID
	if s.connected || (s.active.mode == "host" && s.hostRegistered) {
		id = 0
	}
	code := CodeOpenFailed
	if errors.Is(failure, room.ErrUnsupported) {
		code = CodePeerProtocolUnsupported
	}
	if errors.Is(failure, ErrEnrollmentInvalid) {
		code = CodeEnrollmentInvalid
	}
	metadata := s.closeActive(true)
	mode := metadata.mode
	s.recentlyClosed = &metadata
	if mode == "host" {
		s.openConsumed = false
	}
	return events.emit(Event{ID: id, Type: "error", Code: code, State: "failed"})
}
