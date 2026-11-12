# Web Join (spec A from #385)

Web Join supports one browser guest in a desktop-hosted music session.
While hosting, choose **Copy browser invite** in the Music room. WebJam prepares
the link and copies it. Share it in your private Jam room or a private message
and keep the Music session open. The guest opens the link in a browser,
confirms headphones, and starts audio with a tap.
The connection is **Web Join,
higher delay (about 60–120 ms)**; that is an expectation, not a WAN measurement.
Safari/iOS is labelled best effort. Desktop players continue using the WebJam app.

**Check microphone** and **Join with microphone** both request microphone
access. If the browser blocks sound after joining, choose **Play session
audio** to hear the host on the current connection. After leaving an admitted
session, ask the host for a fresh browser invite to rejoin. Delayed enrollment,
playback, and latency callbacks cannot change a later attempt's controls.
Stats returned after close cannot tune the retired audio receiver.

One web guest is admitted at a time. **Kick web guest**, clearing permission,
resetting the native invitation, stopping the session, replacing the owned
server, or quitting closes its sidecar and bridge. Permission is never saved.
A new invitation requires a new explicit opt-in. The current invitation and
endpoint last at most ten minutes, also bounded by TURN credential expiry.

## Host and browser boundary

`webjam-fabric` includes the Pion endpoint in `transport/internal/webjoin`.
`services/web_join_host.py` owns a separate sidecar and one host-local audio
bridge. The bridge converts browser standard Opus to custom-mode Opus and
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

### Build-bound community destination

`core/web_join_target.py` maps two reviewed identifiers to exact first-party
pages: `public` uses `https://stalemate.band/join/`; `private-beta` uses
`https://stalemate-hangout-beta.jeffstory007.workers.dev/join/`. A private package
can therefore send an already admitted community guest to the protected page
without editing the host's invitation or copying community cookies between sites.

PyInstaller writes a canonical, bounded `webjam-web-join-target.json` containing
the identifier, schema version and exact source build. Frozen apps require that
file, ignore environment/user URL overrides and verify it before allocating the
optional bridge, sidecar, capability or upstream credential. Unknown targets,
extra/duplicate fields, a wrong build, missing data or a final symlink fail closed.
Source checkouts retain the public default. Branch test packaging selects the
target through the existing CI's `web_join_target` input; the
[release runbook](DESKTOP_RELEASE_RUNBOOK.md#private-browser-invitation-target)
records its packaging gate. Private-beta cannot be built from a tag or published
by the release job.

Private-beta admission, approved exact Origin configuration, separate web TURN
credentials, quota and platform-trust checks still apply. Destination validation
proves routing metadata; installation, browser/forced-relay and physical music
need separate evidence. Existing provider and public-release holds remain.

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

Cloudflare's TURN service addresses are `141.101.90.1` and `162.159.207.1`.
The private beta observed relayed transport addresses in `104.30.136.0/24`,
`104.30.137.0/24`, and `104.30.144.0/21`. RIPE RIS corroborated that AS13335
announced those routes. This is a reviewed allowlist for observed allocations,
not a provider guarantee that all allocations use those routes. An unknown
pool requires review in both clients. The browser keeps the legacy answer
allowlist when the authenticated offer contains no allocation from the new
pools. Publish the compatible browser before rolling out a native host that
advertises the reviewed pools.

See [Cloudflare's TURN service address guidance](https://developers.cloudflare.com/realtime/turn/faq/)
and its [description of allocated peer addresses](https://developers.cloudflare.com/realtime/turn/what-is-turn/).
No public gateway is introduced. The bridge listens on `127.0.0.1` and pins the
local source socket after token authentication.

The browser gives ICE services a short gathering grace period, then sends the
vetted candidates it has. It waits up to 15 seconds when none are usable.
Credential HTTP requests have a 12-second deadline and host ICE gathering takes
up to 20 seconds. The host polls for an answer until the published invitation
expires. Connection establishment has 25 seconds, and DTLS
connection-to-enrollment 10 seconds. Browser networking and enrollment also have
finite deadlines. Idle/disconnected media paths close instead of retaining an
unusable guest. Local close has a bounded sidecar shutdown path.

Both clients request `client=web` from the existing `/turn-credentials` endpoint
and mailbox. The shared Worker enforces exact CORS origins, one-hour maximum
web credentials, the existing 800 GB combined own-key UTC-month traffic ceiling,
a 200 GB web sub-cap,
3,000 daily web requests and the existing Durable Object budgets. Unreadable
analytics returns 503; exhausted limits return 429. Host and browser reject
unscoped responses from older Workers. There is no retry into a paid service or
quota bypass into direct mode. Pion counts socket traffic in both directions
(including ICE, DTLS, SRTP, RTCP and TURN transport overhead), closing at a
maximum 256 MiB per guest session; the credential response may lower that cap.

If that service is unavailable, the guest is asked to try later or contact the
host. Moving onto the same Wi-Fi does not replace the required web credentials.
The failure closes the attempted audio session. A reported usage cap retires
the invitation in that browser and asks the guest to get a fresh link once the
service is available again. Temporary rate limiting and outages allow Retry.

After joining, the browser shows **Direct connection** or **Relayed connection**
when it can identify the selected audio route. The label reflects the active
connection, even if another route was available during setup. It is hidden
when the route is unavailable. The indicator uses candidate types only.

Cloudflare currently provides a [shared 1,000 GB monthly SFU/TURN free allowance](https://developers.cloudflare.com/realtime/sfu/platform/pricing/).
The Worker must remain on Workers Free with dedicated web TURN accounting and
no paid upgrades. Account-wide usage outside these keys must also stay within
that allowance. The issuer's ingress-plus-egress counter is not all-account
billed egress and need not cover the subscription's billing period. Verify
actual combined headroom separately; see the [counter scope](../deploy/relay/README.md#cloudflare-issuer-counter-formula-and-scope).
This change does not deploy, configure an account, mint live
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

See `web/join/README.md` and the bridge document for browser and native bridge
lab commands. Cross-network Mac host + Windows Chrome + iPhone Safari remains
manual acceptance work: record selected ICE path, observed audio settings,
RTT/jitter estimates and measured audible latency. Lab measurements do not
establish WAN or physical-device performance.
