package webjoin

// OfferExpiresAt returns the exact Unix deadline sealed into the offer, or zero
// before an offer exists. ICE credentials may shorten the requested lifetime;
// invite fragments must carry this value for the browser's exact binding check.
func (e *Endpoint) OfferExpiresAt() int64 {
	e.mu.Lock()
	defer e.mu.Unlock()
	if e.offer == nil {
		return 0
	}
	return e.offer.ExpiresAt
}
