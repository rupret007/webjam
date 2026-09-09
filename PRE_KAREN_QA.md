# PRE_KAREN: approved host allocation and bounded service cleanup

OPEN DRAFT only. Declared dependent base: #113
`68deee7eb21c584866f894fb2401da2b3dc569b0`. Internet implementation/isolated tests
are authorized. Physical Art/Music acceptance and independent Karen review remain
separate, incomplete requirements.

## Security and ownership self-QA

- Exposing control OR relay requires built-in TLS plus paired dedicated client CA
  and manifest. The legacy insecure flag cannot bypass that boundary. All-loopback
  lab behavior remains available; no compiled public profile is added.
- TLS 1.3 verifies any presented host client certificate. Registration additionally
  requires an exact approved leaf fingerprint and current validity. Identity comes
  from the completed server TLS connection, never wire fields or display names.
- Strict manifest: at most 64 KiB and 128 unique opaque principals/fingerprints,
  regular stable file, no symlink/duplicate/unknown fields; immutable until restart.
  Startup/extraction errors remain categorical. No client private keys in policy.
- Wall-clock certificate expiry is rechecked for every registration. Room TTL,
  idle and fixed rate buckets retain monotonic time. Certificate authority grants
  allocation only; invitation enrollment and room role tokens remain independent.
- Guests and fresh authenticated Close require no client certificate. Invalid
  certificates presented anyway fail TLS: future clients must omit expired host
  credentials on these connections. Expiry does not itself revoke existing rooms.
- Fixed approved-principal buckets permit one attempt/second, burst four, before
  the global bucket. Waiting/enrolled rooms share a per-host cap (default four).
  Unknown principals cannot grow accounting; removal decrements exactly once
  before wiping the identity, signaling, keys and endpoints. Diagnostics stay
  aggregate and omit identities, fingerprints, tokens and paths.
- Response writes and retirement are bounded, including overload/error/HTTP.
  Shutdown marks closed, stops accepting, wipes rooms, aborts tracked writers,
  cancels/joins handlers and rejects late callbacks. One overall default eight
  seconds includes pending five-second handshakes; join failure is not success.
- Temporary certificate fixtures use test-only cryptography; production remains
  standard-library-only. Real tests use owned loopback endpoints, no live issuer,
  media, trust-store mutation or deployment.

## Verification and retained failures

Focused results before freeze: 166 policy/config/protocol tests; 64 state/lifetime
tests; 22 server/connection tests; 15 actual TLS integration scenarios. Ruff passed.
Read-only internal reviews found no actionable issue; this is not Karen PASS.

Retain the seven-failure configuration baseline, ten behavioral bounds failures,
and actual plain-connection shutdown baseline under `out/internet-host-admission`.
Early import/fixture mismatches and sandbox bind denials are separate setup
failures. The first root TLS command failed all 15 scenarios at sandbox loopback
bind; the same tests with authorized local sockets passed in 0.50 seconds. No
TLS behavioral failure was observed in that first denied run. All evidence is
preserved rather than relabeled green.

The frozen tip needs its own complete required local bar, full reference-service
suite, native race and real sidecar tests, full application suite including Art
start UX, and hosted CI including four desktop builds. Exact counts, tip/tree,
workflow results and any recovery history belong in the PR body and AFTER.

## Ten-second UX and remaining acceptance

Guests gain no account, credential setup, extra decision or public discovery
screen in this slice. Existing invitation, room truth and Art/Music controls are
unchanged. No claim that an invitation copy or successful TLS connection means a
person joined, sees/hears the lesson, controls the mix or has acceptable latency.

An inherited API-only lifecycle edge remains: concurrently calling start/close
while listener creation awaits can escape the close snapshot. The CLI awaits
startup sequentially. This review did not exercise or fix concurrent startup;
that lifecycle case remains follow-on work before exposure.

Ordinary host provisioning, a public profile, pending TLS concurrency bounds and
UDP return-path proof are unfinished. A first authenticated UDP BIND does not
prove its source is reachable. Public exposure, real two-home joining, Art faces/
voices/narration/guest pause/listening levels and Music routing/mix/latency remain
unverified. Deployment and physical acceptance are not replaced by CI.

No merge/squash/tag/sign/release/Pages/Publish/deploy/spend/live Cisco, automatic
capture or unsolicited send. Unsigned 0.27.2 remains Jeff-only. No short codes,
public discovery, second media engine, other repository or second goal. Parked
#37/#49 and prior drafts remain untouched. Karen remains pending.
