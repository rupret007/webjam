# Web Join (spec A from #385)

This draft implements a browser guest in a desktop-hosted Jamulus session.
It starts disabled. While hosting an owned Jamulus server, choose **Web Join →
Allow a web guest**, then **Copy web invite**. A guest opens the link, confirms
headphones, and starts audio with a tap. The UI labels the connection **Web Join,
higher delay (about 60–120 ms)**; that is an expectation, not a WAN measurement.
Safari/iOS is labelled best effort. Desktop players continue using Jamulus.

One web guest is admitted at a time. **Kick web guest**, clearing permission,
resetting the native invitation, stopping the session, replacing the owned
server, or quitting closes its sidecar and bridge. Permission is never saved.
A new invitation requires a new explicit opt-in. The current invitation and
endpoint last at most ten minutes, also bounded by TURN credential expiry.

## Host and browser boundary

`webjam-fabric` includes the Pion endpoint in `transport/internal/webjoin`.
`services/web_join_host.py` owns a separate sidecar and one host-local Jamulus
bridge. The bridge converts browser standard Opus to Jamulus Opus Custom and
returns that client's server mix; see [native bridge setup](web-join-bridge.md).
Missing codec support fails closed before the host can issue a usable invite.

The sidecar's existing JSON-lines IPC adds three exact command shapes:

* `web_join_open`: version, id, type, session_id (32-byte base64url), capability
  (32-byte base64url), bridge_port (loopback only), bridge_token (32-byte
  base64url), generation, expires_at_unix.
* `web_join_answer`: version, id, type, generation, answer (sealed base64url envelope).
* `web_join_close`: version, id, type, generation.

No command accepts a browser URL, ICE server, TURN credential, address or raw
SDP. ICE configuration comes from a compiled HTTPS credential endpoint with
redirects disabled. Error events contain fixed codes; diagnostic event history
omits sealed signaling. A process admits one capability for its entire lifetime.
Generation mismatch closes the endpoint instead of resetting replay state.
The `web_join_offer` event returns the sealed offer's effective expiry. The host
uses that value in the invite fragment after checking that it is still live and
does not exceed the requested lifetime, including when TURN credentials expire
sooner than ten minutes.

The browser is static under `web/join/`, without build secrets or third-party
scripts. Its only application network origin is the existing relay Worker.
The invite's `#k=…&s=…&g=…&e=…` fragment contains the random capability, random
session ID, generation and expiry; none enters the HTTP page request.

## Authentication and quarantine

Go and WebCrypto share deterministic vectors in `web/join/test/vectors.json`.
HKDF-SHA256 uses the `webjam/web-join/1` domain plus the existing mailbox and
enrollment domain labels. AES-GCM seals both SDP directions and the session,
generation, expiry, challenge, and certificate fingerprints. A bounded binary
`WJWS1` envelope is split across at most three existing mailbox slots (4,000
base64url characters each); chunk zero is published last. Chunk IDs include the
role and index. Reassembly is bounded and the complete envelope must authenticate
before SDP reaches Pion or the browser.

The host generates an ephemeral DTLS certificate and carries its SHA-256
fingerprint inside the sealed offer. The browser checks every SDP fingerprint
against that pin before applying it. The browser generates its own ephemeral
ECDSA certificate; its pin is carried in the sealed answer. Pion additionally
checks the actual remote DTLS certificate and selected ICE pair.

A dedicated reliable data channel carries a one-use HMAC proof bound to the
session, guest role, challenge nonce, generation, expiry and both certificate
pins. Browsers do not expose a DTLS exporter: this proof is explicitly a
capability HMAC sent over the pinned DTLS connection. Media stays quarantined
until verification; browser playback/microphone forwarding and the local bridge
handshake only start after authorization. Extra data channels, replayed answers
or proofs, bad pins, wrong generations and expiry close the endpoint.

## Networks, deadlines and free limits

ICE gathers public IPv4 server-reflexive and allowlisted TURN candidates together;
ICE priority prefers a direct pair and can fall back to TURN within the same
negotiation. TURN over UDP, TCP, and TLS service URLs are supported. Host,
private, multicast, reserved and non-allowlisted relay candidates are rejected.
There is no production loopback policy override; loopback/private hosts are
available only to the Go test harness. These restrictions can reject same-LAN
networks without a permitted public or relay path.
IPv6 candidates are filtered from host offers and rejected in browser answers,
matching the browser's IPv4-only policy without discarding usable IPv4 fallback.

Cloudflare's reviewed relay IPv4 addresses are `141.101.90.1` and
`162.159.207.1`; a provider change fails closed until the allowlists in both
clients are reviewed. Maintain them against [Cloudflare's TURN FAQ](https://developers.cloudflare.com/realtime/turn/faq/).
No public gateway is introduced. The bridge listens on `127.0.0.1` and pins the
local source socket after token authentication.

Credential HTTP requests have a 12-second deadline, ICE gathering 20 seconds,
host answer polling 90 seconds, connection establishment 25 seconds, and DTLS
connection-to-enrollment 10 seconds. Browser networking and enrollment also have
finite deadlines. Idle/disconnected media paths close instead of retaining an
unusable guest. Local close has a bounded sidecar shutdown path.

Both clients request `client=web` from the existing `/turn-credentials` endpoint
and mailbox. The shared Worker enforces exact CORS origins, one-hour maximum
web credentials, the existing 800 GB monthly TURN cap, a 200 GB web sub-cap,
3,000 daily web requests and the existing Durable Object budgets. Unreadable
analytics returns 503; exhausted limits return 429. Host and browser reject
unscoped responses from older Workers. There is no retry into a paid service or
quota bypass into direct mode. Pion counts socket traffic in both directions
(including ICE, DTLS, SRTP, RTCP and TURN transport overhead), closing at a
maximum 256 MiB per guest session; the credential response may lower that cap.

Cloudflare currently provides a [shared 1,000 GB monthly SFU/TURN free allowance](https://developers.cloudflare.com/realtime/sfu/pricing/).
The Worker must remain on Workers Free with dedicated web TURN accounting and
no paid upgrades. Account-wide usage outside these keys must also stay within
that allowance. This change does not deploy, configure an account, mint live
credentials during tests, or install a native artifact.

## Validation and local setup

```sh
cd transport
go test ./...
go test -race ./internal/webjoin ./internal/ipc
cd ..
pytest tests/test_web_join_bridge.py tests/test_web_join_host.py \
  tests/test_web_join_controls.py tests/test_transport_runtime.py
cd deploy/relay/cloudflare-worker && npm test
cd ../../../web/join && npm test
```

Build the Go sidecar using the existing `transport/Makefile` and the custom-mode
codec using the bridge build helper. Static hosting and Worker configuration
are reviewable sources only; live use requires a separately authorized release
that includes this native dependency, static page and Worker web quota scope.
Tests use local fixtures, never the live TURN credential service.

See `web/join/README.md` and the bridge document for browser and real Jamulus
lab commands. Cross-network Mac host + Windows Chrome + iPhone Safari remains
manual acceptance work: record selected ICE path, observed audio settings,
RTT/jitter estimates and measured audible latency. Lab measurements do not
establish WAN or physical-device performance.
