package icequic_test

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"net"
	"sync"
	"testing"
	"time"

	"github.com/pion/ice/v4"
	"github.com/pion/logging"
	"github.com/pion/stun/v3"
	"github.com/pion/transport/v4/vnet"
	"github.com/pion/turn/v5"
	"github.com/rupret007/webjam/transport/internal/icequic"
	"github.com/rupret007/webjam/transport/internal/loopback"
	"github.com/rupret007/webjam/transport/internal/peer"
	"github.com/rupret007/webjam/transport/internal/sessionauth"
	"github.com/rupret007/webjam/transport/internal/signaling"
	"github.com/rupret007/webjam/transport/internal/wire"
)

const (
	labWANIP    = "1.2.3.4"
	labTURNPort = 3478
	labTURNUser = "webjam-lab"
	labTURNPass = "bounded-lab-only"
	labRealm    = "lab.webjam.invalid"
)

type labMode string

const (
	labDirect labMode = "direct"
	labRelay  labMode = "relay"
)

func TestAuthenticatedFabricDirectAndTURNRelay(t *testing.T) {
	for _, mode := range []labMode{labDirect, labRelay} {
		mode := mode
		t.Run(string(mode), func(t *testing.T) {
			path := newSecureLabPath(t, mode)
			defer path.Close()
			runLoopbackAndReliableRoundTrip(t, path)
		})
	}
}

type secureLabPath struct {
	hostConn   *icequic.Connection
	guestConn  *icequic.Connection
	listener   *icequic.Listener
	hostAgent  *icequic.Agent
	guestAgent *icequic.Agent
	network    *labNetwork
	closeOnce  sync.Once
}

func (p *secureLabPath) Close() {
	p.closeOnce.Do(func() {
		_ = p.guestConn.CloseWithError(0, "lab complete")
		_ = p.hostConn.CloseWithError(0, "lab complete")
		_ = p.listener.Close()
		_ = p.guestAgent.Close()
		_ = p.hostAgent.Close()
		p.network.Close()
	})
}

func newSecureLabPath(t *testing.T, mode labMode) *secureLabPath { //nolint:gocyclo
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	t.Cleanup(cancel)
	network := newLabNetwork(t, mode)
	hostAgent := newLabAgent(t, mode, network.hostNet)
	guestAgent := newLabAgent(t, mode, network.guestNet)

	now := time.Now().UTC().Truncate(time.Second)
	hostIdentity, err := icequic.NewEphemeralIdentity(now, time.Hour)
	if err != nil {
		t.Fatal(err)
	}
	guestIdentity, err := icequic.NewEphemeralIdentity(now, time.Hour)
	if err != nil {
		t.Fatal(err)
	}
	capability := signaling.Capability{1, 9, 8, 4}
	sessionID := signaling.SessionID{7, 6, 5, 4}
	hostPin := signaling.PeerPin(hostIdentity.SPKIFingerprint)
	guestPin := signaling.PeerPin(guestIdentity.SPKIFingerprint)

	hostBundle, err := hostAgent.GatherSealed(ctx, capability, signaling.Bundle{
		SessionID: sessionID, SenderRole: signaling.RoleHost, HostPin: hostPin, GuestPin: guestPin,
		HostPinKnown: true, GuestPinKnown: true, Nonce: signaling.BundleNonce{1},
		Generation: 1, ExpiresAt: now.Add(time.Minute),
	})
	if err != nil {
		t.Fatalf("seal host candidates: %v", err)
	}
	guestBundle, err := guestAgent.GatherSealed(ctx, capability, signaling.Bundle{
		SessionID: sessionID, SenderRole: signaling.RoleGuest, HostPin: hostPin, GuestPin: guestPin,
		HostPinKnown: true, GuestPinKnown: true, Nonce: signaling.BundleNonce{2},
		Generation: 1, ExpiresAt: now.Add(time.Minute),
	})
	if err != nil {
		t.Fatalf("seal guest candidates: %v", err)
	}

	_, err = guestAgent.OpenAuthenticatedRemote(capability, signaling.Expected{
		SessionID: sessionID, SenderRole: signaling.RoleHost, HostPin: hostPin, GuestPin: guestPin,
		HostPinKnown: true, GuestPinKnown: true, Generation: 1,
	}, hostBundle, now, signaling.NewReplayCache())
	if err != nil {
		t.Fatalf("authenticate host signaling: %v", err)
	}
	_, err = hostAgent.OpenAuthenticatedRemote(capability, signaling.Expected{
		SessionID: sessionID, SenderRole: signaling.RoleGuest, HostPin: hostPin, GuestPin: guestPin,
		HostPinKnown: true, GuestPinKnown: true, Generation: 1,
	}, guestBundle, now, signaling.NewReplayCache())
	if err != nil {
		t.Fatalf("authenticate guest signaling: %v", err)
	}
	hostICE, guestICE := connectLabICE(t, ctx, hostAgent, guestAgent)
	pathEvidence, err := hostAgent.SelectedPath()
	if err != nil {
		t.Fatalf("selected pair: %v", err)
	}
	wantType := ice.CandidateTypeServerReflexive
	if mode == labRelay {
		wantType = ice.CandidateTypeRelay
	}
	if pathEvidence.LocalType != wantType.String() || pathEvidence.RemoteType != wantType.String() {
		t.Fatalf("selected pair = %s/%s, want %s", pathEvidence.LocalType, pathEvidence.RemoteType, wantType)
	}

	hostAddress := &net.UDPAddr{IP: net.IPv4(127, 0, 0, 1), Port: 41000}
	guestAddress := &net.UDPAddr{IP: net.IPv4(127, 0, 0, 1), Port: 41001}
	hostPacketConn, err := icequic.NewFixedPeerPacketConn(hostICE, hostAddress, guestAddress)
	if err != nil {
		t.Fatal(err)
	}
	guestPacketConn, err := icequic.NewFixedPeerPacketConn(guestICE, guestAddress, hostAddress)
	if err != nil {
		t.Fatal(err)
	}
	listener, err := icequic.Listen(hostPacketConn, hostIdentity)
	if err != nil {
		t.Fatalf("listen QUIC over ICE: %v", err)
	}
	type accepted struct {
		connection *icequic.Connection
		err        error
	}
	hostAccepted := make(chan accepted, 1)
	go func() {
		connection, acceptErr := listener.Accept(ctx)
		hostAccepted <- accepted{connection: connection, err: acceptErr}
	}()
	guestQUIC, err := icequic.Dial(ctx, guestPacketConn, hostAddress, guestIdentity, hostIdentity.FingerprintHex())
	if err != nil {
		t.Fatalf("dial QUIC over ICE: %v", err)
	}
	hostResult := <-hostAccepted
	if hostResult.err != nil {
		t.Fatalf("accept QUIC over ICE: %v", hostResult.err)
	}
	if err = guestQUIC.SendDatagram([]byte("must remain quarantined")); !errors.Is(err, sessionauth.ErrQuarantined) {
		t.Fatalf("pre-enrollment datagram error = %v", err)
	}
	if _, err = guestQUIC.OpenStreamSync(ctx); !errors.Is(err, sessionauth.ErrQuarantined) {
		t.Fatalf("pre-enrollment stream error = %v", err)
	}

	authCapability := sessionauth.Capability(capability)
	authSession := sessionauth.SessionID(sessionID)
	authHostPin := sessionauth.PeerPin(hostPin)
	authGuestPin := sessionauth.PeerPin(guestPin)
	guestProofBinding := sessionauth.Binding{
		SessionID: authSession, SenderRole: sessionauth.RoleGuest, Generation: 1,
		HostPin: authHostPin, GuestPin: authGuestPin, Nonce: sessionauth.Nonce{3},
		ExpiresAt: now.Add(time.Minute),
	}
	guestProof, err := sessionauth.CreateProof(guestQUIC, authCapability, guestProofBinding, now)
	if err != nil {
		t.Fatalf("create guest enrollment proof: %v", err)
	}
	wrongGuestBinding := guestProofBinding
	wrongGuestBinding.GuestPin = sessionauth.PeerPin{99}
	wrongGuestProof, proofErr := sessionauth.CreateProof(guestQUIC, authCapability, wrongGuestBinding, now)
	if proofErr != nil {
		t.Fatalf("create wrong-identity proof: %v", proofErr)
	}
	if err = hostResult.connection.VerifyAndAuthorize(
		authCapability, wrongGuestBinding, wrongGuestProof, now, sessionauth.NewReplayCache(),
	); !errors.Is(err, icequic.ErrPeerIdentityMismatch) {
		t.Fatalf("wrong guest certificate error = %v", err)
	}
	if err = hostResult.connection.SendDatagram([]byte("must still remain quarantined")); !errors.Is(err, sessionauth.ErrQuarantined) {
		t.Fatalf("post-failed-enrollment datagram error = %v", err)
	}
	if err = hostResult.connection.VerifyAndAuthorize(
		authCapability, guestProofBinding, guestProof, now, sessionauth.NewReplayCache(),
	); err != nil {
		t.Fatalf("verify guest enrollment proof: %v", err)
	}
	hostProofBinding := sessionauth.Binding{
		SessionID: authSession, SenderRole: sessionauth.RoleHost, Generation: 1,
		HostPin: authHostPin, GuestPin: authGuestPin, Nonce: sessionauth.Nonce{4},
		ExpiresAt: now.Add(time.Minute),
	}
	hostProof, err := sessionauth.CreateProof(hostResult.connection, authCapability, hostProofBinding, now)
	if err != nil {
		t.Fatalf("create host enrollment proof: %v", err)
	}
	if err = guestQUIC.VerifyAndAuthorize(
		authCapability, hostProofBinding, hostProof, now, sessionauth.NewReplayCache(),
	); err != nil {
		t.Fatalf("verify host enrollment proof: %v", err)
	}
	return &secureLabPath{
		hostConn: hostResult.connection, guestConn: guestQUIC,
		listener:  listener,
		hostAgent: hostAgent, guestAgent: guestAgent, network: network,
	}
}

func runLoopbackAndReliableRoundTrip(t *testing.T, path *secureLabPath) {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	jamulus, err := net.ListenUDP("udp4", &net.UDPAddr{IP: net.IPv4(127, 0, 0, 1), Port: 0})
	if err != nil {
		t.Fatal(err)
	}
	defer jamulus.Close()
	hostProxy, err := loopback.NewHostProxy(jamulus.LocalAddr().(*net.UDPAddr))
	if err != nil {
		t.Fatal(err)
	}
	guestProxy, err := loopback.NewGuestProxy()
	if err != nil {
		t.Fatal(err)
	}
	hostPeer, err := peer.New(peer.ModeHost, 1, hostProxy, path.hostConn)
	if err != nil {
		t.Fatal(err)
	}
	guestPeer, err := peer.New(peer.ModeGuest, 1, guestProxy, path.guestConn)
	if err != nil {
		t.Fatal(err)
	}
	peerErrors := make(chan error, 2)
	go func() { peerErrors <- hostPeer.Run(ctx) }()
	go func() { peerErrors <- guestPeer.Run(ctx) }()

	jamulusDone := make(chan error, 1)
	go func() {
		buffer := make([]byte, 2048)
		for index := 0; index < 32; index++ {
			if err := jamulus.SetReadDeadline(time.Now().Add(2 * time.Second)); err != nil {
				jamulusDone <- err
				return
			}
			n, remote, readErr := jamulus.ReadFromUDP(buffer)
			if readErr != nil {
				jamulusDone <- readErr
				return
			}
			if _, writeErr := jamulus.WriteToUDP(buffer[:n], remote); writeErr != nil {
				jamulusDone <- writeErr
				return
			}
		}
		jamulusDone <- nil
	}()

	localJamulus, err := net.DialUDP("udp4", nil, &net.UDPAddr{IP: net.IPv4(127, 0, 0, 1), Port: guestProxy.LocalPort()})
	if err != nil {
		t.Fatal(err)
	}
	defer localJamulus.Close()

	hostReliable, err := icequic.NewReliablePlane(path.hostConn)
	if err != nil {
		t.Fatal(err)
	}
	guestReliable, err := icequic.NewReliablePlane(path.guestConn)
	if err != nil {
		t.Fatal(err)
	}
	type receivedFrame struct {
		frame wire.StreamFrame
		err   error
	}
	reliableReceived := make(chan receivedFrame, 1)
	reliableSent := make(chan error, 1)
	go func() {
		frame, acceptErr := hostReliable.Accept(ctx)
		reliableReceived <- receivedFrame{frame: frame, err: acceptErr}
	}()
	reliablePayload := bytes.Repeat([]byte{0x6d}, 32*1024)
	go func() {
		reliableSent <- guestReliable.Send(ctx, wire.StreamFrame{
			Kind: wire.StreamKindControl, Generation: 1, Payload: reliablePayload,
		})
	}()

	buffer := make([]byte, 1024)
	for index := 0; index < 32; index++ {
		size := 440
		if index%2 == 1 {
			size = 660
		}
		payload := bytes.Repeat([]byte{byte(index + 1)}, size)
		if _, err = localJamulus.Write(payload); err != nil {
			t.Fatal(err)
		}
		if err = localJamulus.SetReadDeadline(time.Now().Add(2 * time.Second)); err != nil {
			t.Fatal(err)
		}
		n, readErr := localJamulus.Read(buffer)
		if readErr != nil {
			t.Fatal(readErr)
		}
		if !bytes.Equal(buffer[:n], payload) {
			t.Fatalf("datagram %d boundary/content changed: got=%d want=%d", index, n, len(payload))
		}
	}
	if err = <-jamulusDone; err != nil {
		t.Fatal(err)
	}
	select {
	case received := <-reliableReceived:
		if received.err != nil {
			t.Fatal(received.err)
		}
		if received.frame.Kind != wire.StreamKindControl || received.frame.Generation != 1 || !bytes.Equal(received.frame.Payload, reliablePayload) {
			t.Fatal("reliable frame changed")
		}
	case <-ctx.Done():
		t.Fatal(ctx.Err())
	}
	select {
	case err = <-reliableSent:
		if err != nil {
			t.Fatal(err)
		}
	case <-ctx.Done():
		t.Fatal(ctx.Err())
	}

	cancel()
	_ = guestPeer.Close()
	_ = hostPeer.Close()
	for range 2 {
		if peerErr := <-peerErrors; peerErr != nil {
			t.Fatalf("peer cleanup: %v", peerErr)
		}
	}
	if guestPeer.Metrics().Sent != 32 || guestPeer.Metrics().Received != 32 {
		t.Fatalf("guest metrics = %+v", guestPeer.Metrics())
	}
	if hostPeer.Metrics().Sent != 32 || hostPeer.Metrics().Received != 32 {
		t.Fatalf("host metrics = %+v", hostPeer.Metrics())
	}
}

func connectLabICE(
	t *testing.T,
	ctx context.Context,
	hostAgent, guestAgent *icequic.Agent,
) (*ice.Conn, *ice.Conn) {
	t.Helper()
	type result struct {
		connection *ice.Conn
		err        error
	}
	hostAccepted := make(chan result, 1)
	go func() {
		connection, err := hostAgent.Accept(ctx)
		hostAccepted <- result{connection: connection, err: err}
	}()
	guestConnection, err := guestAgent.Dial(ctx)
	if err != nil {
		t.Fatal(err)
	}
	hostResult := <-hostAccepted
	if hostResult.err != nil {
		t.Fatal(hostResult.err)
	}
	return hostResult.connection, guestConnection
}

type labNetwork struct {
	wan        *vnet.Router
	hostNet    *vnet.Net
	guestNet   *vnet.Net
	turnServer *turn.Server
	closeOnce  sync.Once
}

func (n *labNetwork) Close() {
	n.closeOnce.Do(func() {
		_ = n.turnServer.Close()
		_ = n.wan.Stop()
	})
}

func newLabNetwork(t *testing.T, mode labMode) *labNetwork { //nolint:gocyclo
	t.Helper()
	logger := quietLabLogger()
	wan, err := vnet.NewRouter(&vnet.RouterConfig{
		CIDR: "0.0.0.0/0", QueueSize: 8192, LoggerFactory: logger,
	})
	if err != nil {
		t.Fatal(err)
	}
	wanNet, err := vnet.NewNet(&vnet.NetConfig{StaticIPs: []string{labWANIP}})
	if err != nil {
		t.Fatal(err)
	}
	if err = wan.AddNet(wanNet); err != nil {
		t.Fatal(err)
	}
	nat := &vnet.NATType{
		MappingBehavior: vnet.EndpointIndependent, FilteringBehavior: vnet.EndpointIndependent,
	}
	if mode == labRelay {
		nat = &vnet.NATType{
			MappingBehavior:   vnet.EndpointAddrPortDependent,
			FilteringBehavior: vnet.EndpointAddrPortDependent,
		}
	}
	hostNet := addLabLAN(t, wan, logger, "27.1.1.1", "192.168.10.0/24", "192.168.10.2", nat)
	guestNet := addLabLAN(t, wan, logger, "28.1.1.1", "10.20.30.0/24", "10.20.30.2", nat)
	if err = wan.Start(); err != nil {
		t.Fatal(err)
	}
	turnConn, err := wanNet.ListenPacket("udp", fmt.Sprintf("%s:%d", labWANIP, labTURNPort))
	if err != nil {
		t.Fatal(err)
	}
	turnServer, err := turn.NewServer(turn.ServerConfig{
		Realm: labRealm,
		AuthHandler: func(attributes *turn.RequestAttributes) (string, []byte, bool) {
			if attributes.Username != labTURNUser {
				return "", nil, false
			}
			return labTURNUser, turn.GenerateAuthKey(labTURNUser, attributes.Realm, labTURNPass), true
		},
		PacketConnConfigs: []turn.PacketConnConfig{{
			PacketConn: turnConn,
			RelayAddressGenerator: &turn.RelayAddressGeneratorStatic{
				RelayAddress: net.ParseIP(labWANIP), Address: "0.0.0.0", Net: wanNet,
			},
			PermissionHandler: turn.DefaultPermissionHandler,
		}},
		LoggerFactory: logger,
		InboundMTU:    1600,
	})
	if err != nil {
		t.Fatal(err)
	}
	return &labNetwork{wan: wan, hostNet: hostNet, guestNet: guestNet, turnServer: turnServer}
}

func addLabLAN(
	t *testing.T,
	wan *vnet.Router,
	logger logging.LoggerFactory,
	publicIP, cidr, privateIP string,
	nat *vnet.NATType,
) *vnet.Net {
	t.Helper()
	router, err := vnet.NewRouter(&vnet.RouterConfig{
		StaticIPs: []string{publicIP}, CIDR: cidr, NATType: nat, QueueSize: 8192, LoggerFactory: logger,
	})
	if err != nil {
		t.Fatal(err)
	}
	network, err := vnet.NewNet(&vnet.NetConfig{StaticIPs: []string{privateIP}})
	if err != nil {
		t.Fatal(err)
	}
	if err = router.AddNet(network); err != nil {
		t.Fatal(err)
	}
	if err = wan.AddRouter(router); err != nil {
		t.Fatal(err)
	}
	return network
}

func newLabAgent(t *testing.T, mode labMode, network *vnet.Net) *icequic.Agent {
	t.Helper()
	candidateType := ice.CandidateTypeServerReflexive
	url := &stun.URI{Scheme: stun.SchemeTypeSTUN, Host: labWANIP, Port: labTURNPort, Proto: stun.ProtoTypeUDP}
	if mode == labRelay {
		candidateType = ice.CandidateTypeRelay
		url = &stun.URI{
			Scheme: stun.SchemeTypeTURN, Host: labWANIP, Port: labTURNPort,
			Username: labTURNUser, Password: labTURNPass, Proto: stun.ProtoTypeUDP,
		}
	}
	config := icequic.AgentConfig{
		URLs: []*stun.URI{url}, NetworkTypes: []ice.NetworkType{ice.NetworkTypeUDP4},
		CandidateTypes: []ice.CandidateType{candidateType}, Net: network,
	}
	if mode == labRelay {
		config.AllowedRelayAddresses = []string{labWANIP}
	}
	agent, err := icequic.NewAgent(config)
	if err != nil {
		t.Fatal(err)
	}
	return agent
}

func quietLabLogger() *logging.DefaultLoggerFactory {
	logger := logging.NewDefaultLoggerFactory()
	logger.DefaultLogLevel = logging.LogLevelDisabled
	return logger
}

func TestReliablePlaneRemainsQuarantined(t *testing.T) {
	t.Parallel()
	if err := sessionauth.NewGate().Require(); !errors.Is(err, sessionauth.ErrQuarantined) {
		t.Fatalf("gate error = %v", err)
	}
}
