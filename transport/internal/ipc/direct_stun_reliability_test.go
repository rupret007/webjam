package ipc

import (
	"context"
	"errors"
	"net"
	"testing"
	"time"

	"github.com/rupret007/webjam/transport/internal/directstun"
)

func TestDirectStunBlockedProbeWithoutRelay(t *testing.T) {
	for _, cancelProbe := range []bool{false, true} {
		name := "bounded_unreachable_failure"
		if cancelProbe {
			name = "cancel_pending_probe"
		}
		t.Run(name, func(t *testing.T) {
			// Keep every probe on loopback and provide no TURN allocation, as
			// when direct UDP is blocked and the credential service is offline.
			blackhole, err := net.ListenUDP("udp4", &net.UDPAddr{IP: net.IPv4(127, 0, 0, 1)})
			if err != nil {
				t.Fatal(err)
			}
			t.Cleanup(func() { _ = blackhole.Close() })
			previousParse, previousAllocate, previousTimeout := parseDialTarget, tryAllocateRelay, directPathTimeout
			t.Cleanup(func() {
				parseDialTarget, tryAllocateRelay, directPathTimeout = previousParse, previousAllocate, previousTimeout
			})
			installUnreachableDirectGather(t)
			parseDialTarget = func(string) (*net.UDPAddr, error) {
				return blackhole.LocalAddr().(*net.UDPAddr), nil
			}
			tryAllocateRelay = func(context.Context) *directstun.Allocation { return nil }
			directPathTimeout = 100 * time.Millisecond
			if cancelProbe {
				directPathTimeout = 10 * time.Second
			}

			now := time.Now().UTC().Truncate(time.Second)
			hostIdentity, guestIdentity := mustIdentities(t, now)
			hostOp, cancelHost := startDirectStunHost(t, now, hostIdentity)
			defer cancelHost()
			defer hostOp.Close(context.Background())
			envelope := waitHostRegistered(t, hostOp)
			guestOp, cancelGuest := startDirectStunGuest(t, now, hostIdentity, guestIdentity, envelope)
			defer cancelGuest()
			defer guestOp.Close(context.Background())

			// The mux can emit its first punch before the reply is published.
			// Wait for both boundaries before canceling the pending attempt.
			select {
			case update, open := <-guestOp.Updates():
				if !open || update.kind != updateGuestSignal || len(update.signal) == 0 {
					t.Fatal("guest did not publish its reply before probing")
				}
			case <-time.After(2 * time.Second):
				t.Fatal("guest did not publish its reply within the deadline")
			}

			// Receiving an actual punch packet proves the guest reached the
			// network attempt rather than rejecting the invitation up front.
			if err = blackhole.SetReadDeadline(time.Now().Add(2 * time.Second)); err != nil {
				t.Fatal(err)
			}
			if _, _, err = blackhole.ReadFromUDP(make([]byte, 2048)); err != nil {
				t.Fatalf("guest did not probe the direct path: %v", err)
			}
			if cancelProbe {
				cancelGuest()
			}
			gotFailure := false
			deadline := time.After(2 * time.Second)
			for {
				select {
				case update, open := <-guestOp.Updates():
					if !open {
						if gotFailure == cancelProbe {
							t.Fatalf("failure=%v canceled=%v", gotFailure, cancelProbe)
						}
						return
					}
					switch update.kind {
					case updateGuestSignal:
						t.Fatal("guest published more than one reply")
					case updatePeerConnected:
						t.Fatal("blocked paths must never report a connected peer")
					case updateFabricFailed:
						gotFailure = true
						if !errors.Is(update.err, ErrUnreachable) || update.sessionPath != "" {
							t.Fatalf("unexpected failure: error=%v path=%q", update.err, update.sessionPath)
						}
					}
				case <-deadline:
					t.Fatal("blocked direct probe did not settle within its deadline")
				}
			}
		})
	}
}
