package webjoin

import (
	"bytes"
	"context"
	"net"
	"strings"
	"testing"
	"time"

	"github.com/pion/ice/v4"
	"github.com/pion/logging"
	"github.com/pion/rtp"
	"github.com/pion/turn/v5"
	"github.com/pion/webrtc/v4"
)

// Real local TURN allocations exercise Pion's UDP and TCP server transports.
// Both peers are relay-only, so a successful DTLS/media roundtrip cannot hide
// a broken relay behind a working direct host pair. No live credentials/cost.
func TestWebJoinForcedTURNMedia(t *testing.T) {
	for _, transport := range []string{"udp", "tcp"} {
		t.Run(transport, func(t *testing.T) {
			realm, username, password := "webjoin.invalid", "local-lab", "ephemeral-lab-only"
			factory := logging.NewDefaultLoggerFactory()
			factory.DefaultLogLevel = logging.LogLevelDisabled
			generator := &turn.RelayAddressGeneratorStatic{RelayAddress: net.ParseIP("127.0.0.1"), Address: "127.0.0.1"}
			serverConfig := turn.ServerConfig{Realm: realm, LoggerFactory: factory,
				AuthHandler: func(attributes *turn.RequestAttributes) (string, []byte, bool) {
					return username, turn.GenerateAuthKey(username, realm, password), attributes.Username == username && attributes.Realm == realm
				}}
			var address string
			if transport == "udp" {
				socket, err := net.ListenPacket("udp4", "127.0.0.1:0")
				if err != nil {
					t.Fatal(err)
				}
				t.Cleanup(func() { _ = socket.Close() })
				address = socket.LocalAddr().String()
				serverConfig.PacketConnConfigs = []turn.PacketConnConfig{{PacketConn: socket, RelayAddressGenerator: generator}}
			} else {
				listener, err := net.Listen("tcp4", "127.0.0.1:0")
				if err != nil {
					t.Fatal(err)
				}
				t.Cleanup(func() { _ = listener.Close() })
				address = listener.Addr().String()
				serverConfig.ListenerConfigs = []turn.ListenerConfig{{Listener: listener, RelayAddressGenerator: generator}}
			}
			server, err := turn.NewServer(serverConfig)
			if err != nil {
				t.Fatal(err)
			}
			t.Cleanup(func() { _ = server.Close() })
			iceServers := []webrtc.ICEServer{{URLs: []string{"turn:" + address + "?transport=" + transport}, Username: username, Credential: password}}
			cfg := testConfig()
			cfg.ICE = ICEConfig{Servers: iceServers, AllowedRelayAddresses: []string{"127.0.0.1"}, QuotaBytes: MaxSessionBytes}
			host, err := newEndpoint(context.Background(), cfg, true)
			if err != nil {
				t.Fatal(err)
			}
			t.Cleanup(func() { _ = host.Close() })
			pcConfig := host.pc.GetConfiguration()
			pcConfig.ICETransportPolicy = webrtc.ICETransportPolicyRelay
			if err = host.pc.SetConfiguration(pcConfig); err != nil {
				t.Fatal(err)
			}
			sealed, err := host.Offer(context.Background())
			if err != nil {
				t.Fatal(err)
			}
			offer, err := OpenSignal(cfg.Capability, cfg.SessionID, sealed)
			if err != nil {
				t.Fatal(err)
			}
			if strings.Contains(offer.SDP, " typ host") {
				t.Fatal("direct host candidate escaped relay-only offer")
			}

			settings := webrtc.SettingEngine{}
			settings.SetNetworkTypes([]webrtc.NetworkType{webrtc.NetworkTypeUDP4})
			settings.SetICEMulticastDNSMode(ice.MulticastDNSModeDisabled)
			settings.SetIncludeLoopbackCandidate(true)
			settings.SetIPFilter(func(ip net.IP) bool { return ip.IsLoopback() })
			settings.LoggerFactory = factory
			guest, err := webrtc.NewAPI(webrtc.WithSettingEngine(settings)).NewPeerConnection(webrtc.Configuration{ICEServers: iceServers, ICETransportPolicy: webrtc.ICETransportPolicyRelay})
			if err != nil {
				t.Fatal(err)
			}
			t.Cleanup(func() { _ = guest.Close() })
			peer := &labPeer{host: host, guest: guest, cfg: cfg, channel: make(chan *webrtc.DataChannel, 1), audio: make(chan Packet, 1)}
			guest.OnDataChannel(func(dc *webrtc.DataChannel) { dc.OnOpen(func() { peer.channel <- dc }) })
			guest.OnTrack(func(track *webrtc.TrackRemote, _ *webrtc.RTPReceiver) {
				go func() {
					for {
						p, _, readErr := track.ReadRTP()
						if readErr != nil {
							return
						}
						select {
						case peer.audio <- Packet{Payload: append([]byte(nil), p.Payload...)}:
						default:
						}
					}
				}()
			})
			peer.track, err = webrtc.NewTrackLocalStaticRTP(webrtc.RTPCodecCapability{MimeType: webrtc.MimeTypeOpus, ClockRate: 48000, Channels: 2}, "browser", "webjoin")
			if err != nil {
				t.Fatal(err)
			}
			sender, err := guest.AddTrack(peer.track)
			if err != nil {
				t.Fatal(err)
			}
			go func() {
				buffer := make([]byte, 1500)
				for {
					if _, _, readErr := sender.Read(buffer); readErr != nil {
						return
					}
				}
			}()
			if err = guest.SetRemoteDescription(webrtc.SessionDescription{Type: webrtc.SDPTypeOffer, SDP: offer.SDP}); err != nil {
				t.Fatal(err)
			}
			gathered := webrtc.GatheringCompletePromise(guest)
			answer, err := guest.CreateAnswer(nil)
			if err != nil {
				t.Fatal(err)
			}
			if err = guest.SetLocalDescription(answer); err != nil {
				t.Fatal(err)
			}
			select {
			case <-gathered:
			case <-time.After(5 * time.Second):
				t.Fatal("TURN guest gather timed out")
			}
			peer.answer = offer
			peer.answer.Kind = "answer"
			peer.answer.SDP = guest.LocalDescription().SDP
			for _, line := range strings.Split(peer.answer.SDP, "\n") {
				if strings.HasPrefix(line, "a=fingerprint:") {
					peer.answer.GuestPin, err = parseFingerprint(strings.TrimSpace(strings.TrimPrefix(line, "a=fingerprint:")))
					if err != nil {
						t.Fatal(err)
					}
					break
				}
			}
			peer.answer.SDP, err = validateSDP(peer.answer.SDP, peer.answer.GuestPin, host.relays, true, true)
			if err != nil {
				t.Fatal(err)
			}
			if err = peer.apply(t); err != nil {
				t.Fatal(err)
			}
			peer.enroll(t)
			pair, err := host.pc.SCTP().Transport().ICETransport().GetSelectedCandidatePair()
			if err != nil || pair == nil || pair.Local.Typ != webrtc.ICECandidateTypeRelay || pair.Remote.Typ != webrtc.ICECandidateTypeRelay {
				t.Fatalf("relay pair required: %v %v", pair, err)
			}
			payload := []byte{0xf8, 0xff, 0xfe}
			bridge := cfg.Bridge.(*memoryBridge)
			if err = peer.track.WriteRTP(&rtp.Packet{Header: rtp.Header{Version: 2, PayloadType: 111, SequenceNumber: 1, Timestamp: 960, SSRC: 42}, Payload: payload}); err != nil {
				t.Fatal(err)
			}
			select {
			case packet := <-bridge.received:
				if !bytes.Equal(packet.Payload, payload) {
					t.Fatal("TURN input corrupted")
				}
			case <-host.Done():
				t.Fatal(host.Err())
			case <-time.After(3 * time.Second):
				t.Fatal("TURN input timed out")
			}
			bridge.mix <- Packet{Payload: payload, SequenceNumber: 1, Timestamp: 960}
			select {
			case packet := <-peer.audio:
				if !bytes.Equal(packet.Payload, payload) {
					t.Fatal("TURN return corrupted")
				}
			case <-host.Done():
				t.Fatal(host.Err())
			case <-time.After(3 * time.Second):
				t.Fatal("TURN return timed out")
			}
		})
	}
}
