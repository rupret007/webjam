package webjoin

import (
	"context"
	"testing"
	"time"
)

func TestOfferExpiryMatchesCredentialClampedSealedOffer(t *testing.T) {
	cfg := testConfig()
	cfg.ExpiresAt = time.Now().Add(10 * time.Minute)
	cfg.ICE.ExpiresAt = time.Now().Add(90 * time.Second)
	endpoint, err := newEndpoint(context.Background(), cfg, true)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = endpoint.Close() })
	if endpoint.OfferExpiresAt() != 0 {
		t.Fatal("metadata exists before the sealed offer")
	}
	envelope, err := endpoint.Offer(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	offer, err := OpenSignal(cfg.Capability, cfg.SessionID, envelope)
	if err != nil {
		t.Fatal(err)
	}
	if offer.ExpiresAt != cfg.ICE.ExpiresAt.Unix() || endpoint.OfferExpiresAt() != offer.ExpiresAt {
		t.Fatalf("offer expiry %d and metadata %d must match the credential deadline %d", offer.ExpiresAt, endpoint.OfferExpiresAt(), cfg.ICE.ExpiresAt.Unix())
	}
}
