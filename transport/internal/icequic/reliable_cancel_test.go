package icequic

import (
	"bytes"
	"context"
	"errors"
	"io"
	"net"
	"testing"
	"time"

	"github.com/quic-go/quic-go"
	"github.com/rupret007/webjam/transport/internal/limits"
	"github.com/rupret007/webjam/transport/internal/sessionauth"
	"github.com/rupret007/webjam/transport/internal/wire"
)

func TestReliableAcceptCancelsStalledFrame(t *testing.T) {
	t.Parallel()
	for _, withholdFIN := range []bool{false, true} {
		name := "partial_payload"
		if withholdFIN {
			name = "missing_fin"
		}
		t.Run(name, func(t *testing.T) {
			host, guest := newReliableCancellationPair(t)
			plane, err := NewReliablePlane(host)
			if err != nil {
				t.Fatal(err)
			}
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			result := make(chan error, 1)
			go func() {
				_, acceptErr := plane.Accept(ctx)
				result <- acceptErr
			}()
			streamCtx, stopStream := context.WithTimeout(context.Background(), 3*time.Second)
			defer stopStream()
			stream, err := guest.OpenStreamSync(streamCtx)
			if err != nil {
				t.Fatal(err)
			}
			defer stream.CancelWrite(0)
			defer stream.CancelRead(0)
			if err = stream.SetWriteDeadline(time.Now().Add(3 * time.Second)); err != nil {
				t.Fatal(err)
			}
			var encoded bytes.Buffer
			if err = wire.WriteStreamFrame(&encoded, wire.StreamFrame{
				Kind: wire.StreamKindControl, Generation: 1, Payload: make([]byte, 32*1024),
			}); err != nil {
				t.Fatal(err)
			}
			payload := encoded.Bytes()
			if !withholdFIN {
				payload = payload[:len(payload)/2]
			}
			// This exceeds the peer's receive window and QUIC's write buffer,
			// so Write completes only after Accept is consuming this stream.
			if _, err = stream.Write(payload); err != nil {
				t.Fatal(err)
			}
			if withholdFIN {
				// A complete frame without FIN must remain pending. Give the
				// receiver time to finish the payload and wait for the FIN.
				select {
				case err = <-result:
					t.Fatalf("accepted a frame without FIN: %v", err)
				case <-time.After(25 * time.Millisecond):
				}
			}
			cancel()
			assertReliableCancellation(t, result)
			if len(host.streamSlots) != 0 {
				t.Fatal("canceled receive retained its stream slot")
			}
			assertReliableConnectionReusable(t, host, guest)
		})
	}
}

func TestReliableSendCancelsBlockedWrite(t *testing.T) {
	t.Parallel()
	host, guest := newReliableCancellationPair(t)
	plane, err := NewReliablePlane(guest)
	if err != nil {
		t.Fatal(err)
	}
	// Repeat beyond the stream ceiling to cover returning stream credit after
	// cancellation, as needed when a connection carries subsequent attempts.
	for range limits.MaxConcurrentStreams + 1 {
		ctx, cancel := context.WithCancel(context.Background())
		result := make(chan error, 1)
		go func() {
			result <- plane.Send(ctx, wire.StreamFrame{
				Kind: wire.StreamKindControl, Generation: 1, Payload: make([]byte, limits.MaxStreamFrameBytes),
			})
		}()
		acceptCtx, stopAccept := context.WithTimeout(context.Background(), 3*time.Second)
		stream, acceptErr := host.AcceptStream(acceptCtx)
		stopAccept()
		if acceptErr != nil {
			cancel()
			t.Fatal(acceptErr)
		}
		// Reading one byte establishes the send is active, but leaves far less
		// receive credit than the frame needs. The write remains blocked.
		if err = stream.SetReadDeadline(time.Now().Add(3 * time.Second)); err != nil {
			cancel()
			t.Fatal(err)
		}
		if _, err = io.ReadFull(stream, make([]byte, 1)); err != nil {
			cancel()
			t.Fatal(err)
		}
		cancel()
		assertReliableCancellation(t, result)
		stream.CancelRead(0)
		stream.CancelWrite(0)
		if len(guest.streamSlots) != 0 {
			t.Fatal("canceled send retained its stream slot")
		}
	}
	assertReliableConnectionReusable(t, host, guest)
}

func assertReliableCancellation(t *testing.T, result <-chan error) {
	t.Helper()
	select {
	case err := <-result:
		if !errors.Is(err, context.Canceled) {
			t.Fatalf("canceled operation error = %v, want context.Canceled", err)
		}
	case <-time.After(time.Second):
		t.Fatal("canceled operation remained blocked on its stream")
	}
}

func assertReliableConnectionReusable(t *testing.T, host, guest *Connection) {
	t.Helper()
	hostPlane, err := NewReliablePlane(host)
	if err != nil {
		t.Fatal(err)
	}
	guestPlane, err := NewReliablePlane(guest)
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	sent := make(chan error, 1)
	want := wire.StreamFrame{Kind: wire.StreamKindControl, Generation: 1, Payload: []byte("join retry")}
	go func() { sent <- guestPlane.Send(ctx, want) }()
	got, err := hostPlane.Accept(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if got.Kind != want.Kind || got.Generation != want.Generation || !bytes.Equal(got.Payload, want.Payload) {
		t.Fatalf("frame after cancellation = %+v", got)
	}
	if err = <-sent; err != nil {
		t.Fatal(err)
	}
}

func newReliableCancellationPair(t *testing.T) (*Connection, *Connection) {
	t.Helper()
	now := time.Now()
	hostIdentity, err := NewEphemeralIdentity(now, time.Hour)
	if err != nil {
		t.Fatal(err)
	}
	guestIdentity, err := NewEphemeralIdentity(now, time.Hour)
	if err != nil {
		t.Fatal(err)
	}
	listen := func() *net.UDPConn {
		t.Helper()
		conn, listenErr := net.ListenUDP("udp4", &net.UDPAddr{IP: net.IPv4(127, 0, 0, 1)})
		if listenErr != nil {
			t.Fatal(listenErr)
		}
		t.Cleanup(func() { _ = conn.Close() })
		return conn
	}
	hostSocket, guestSocket := listen(), listen()
	config := QUICConfig()
	config.InitialStreamReceiveWindow = 1024
	config.MaxStreamReceiveWindow = 1024
	listener, err := quic.Listen(hostSocket, ServerTLSConfig(hostIdentity), config)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = listener.Close() })
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	guest, err := Dial(ctx, guestSocket, hostSocket.LocalAddr(), guestIdentity, hostIdentity.FingerprintHex())
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = guest.CloseWithError(0, "test complete") })
	hostInner, err := listener.Accept(ctx)
	if err != nil {
		t.Fatal(err)
	}
	host, err := newConnection(hostInner)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = host.CloseWithError(0, "test complete") })
	capability := sessionauth.Capability{1}
	for _, role := range []sessionauth.Role{sessionauth.RoleHost, sessionauth.RoleGuest} {
		sender, receiver := host, guest
		if role == sessionauth.RoleGuest {
			sender, receiver = guest, host
		}
		binding := sessionauth.Binding{
			SessionID: sessionauth.SessionID{2}, SenderRole: role, Generation: 1,
			HostPin: sessionauth.PeerPin(hostIdentity.SPKIFingerprint), GuestPin: sessionauth.PeerPin(guestIdentity.SPKIFingerprint),
			Nonce: sessionauth.Nonce{byte(role)}, ExpiresAt: now.Add(time.Minute),
		}
		proof, proofErr := sessionauth.CreateProof(sender, capability, binding, now)
		if proofErr != nil {
			t.Fatal(proofErr)
		}
		if err = receiver.VerifyAndAuthorize(capability, binding, proof, now, sessionauth.NewReplayCache()); err != nil {
			t.Fatal(err)
		}
	}
	return host, guest
}
