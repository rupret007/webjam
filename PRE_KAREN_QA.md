# PRE_KAREN: control setup limits and startup ownership

OPEN DRAFT only. Declared dependent base: #114
`89274f756567ed6422b93e193560800ef126667f`. Internet implementation and isolated
tests are authorized. Physical Art/Music acceptance and independent Karen review
remain incomplete.

## Security and ownership self-QA

- Control admission reserves pending capacity and rate before creating TLS work.
  Defaults: 64 pending setups, 32 starts/second, burst 64. Settings are bounded
  positive integers and reject booleans. Plain loopback lab shares these limits.
  HTTP uses a separate bucket/cap with those same settings, so health traffic
  cannot consume control tokens. One 10 ms timer per listener admits at most
  16 nonblocking accepts per callback, with address-family rotation. This is
  connection setup only, outside the media and UDP paths.
- Completed control capacity remains 512 and is reserved synchronously before
  handler scheduling. HTTP capacity remains 64. Rejected transports close without
  a protocol response or application task; admitted protocol errors remain.
- A plain underlying transport is owned and paused before server-side TLS starts.
  TLS-configured control has no plaintext application parsing, reply or protocol
  negotiation: only verified TLS reaches its handler. Early decrypted input stays
  bounded until promotion. This is direct TLS, not a wire-level STARTTLS mode;
  explicitly configured plain-loopback lab compatibility remains.
- Raw acceptance is synchronous, nonblocking and bounded per timer callback.
  Each accepted socket is owned before asynchronous setup begins. Control and
  HTTP use this path; it avoids both cancellation of a hidden accept result and
  Python's delayed acceptance before transport attachment. Kernel/network floods
  still require upstream protection; no volumetric protection claim is made.
- Startup and Close belong to the service instance. Closing prevents publication
  as running, cancels/joins startup, retires late resources and joins UDP closure,
  control acceptance/setup, handlers and cleanup within one absolute deadline.
  Caller cancellation cannot abandon owned teardown. Timeout reports failure and
  retains ownership; sets are not cleared to manufacture success. An unpublished
  plain transport attachment remains owned until publication or failed completion;
  it is not canceled and forgotten before its transport can be retired.
- Bind settings are validated before I/O: numeric unscoped IPv4/IPv6 or exact
  case-insensitive localhost only. Control localhost shares one chosen port across
  available loopback families; explicit IPv6 fails honestly. HTTP/UDP localhost
  explicitly use IPv4. No resolver/executor task or new desktop endpoint field.
- TLS 1.3, dedicated host CA, approved immutable leaf policy, live registration
  expiry, allocation quotas and role/enrollment authority remain inherited.
  Guests and fresh role-token Close remain certificate-free. No public discovery,
  short codes, credential issuer, native profile or UDP framing changes.
- Policy-file loading handles CPython 3.12 Windows path/descriptor ctime semantics:
  cross-API comparisons retain device, file ID, size and modification time, while
  full before/after comparisons within each API retain ctime mutation detection.
  POSIX retains its cross-API ctime check. File type, size, symlink refusal, bounded
  reads, descriptor cleanup and categorical errors remain enforced.
- Two bounded reads through the same descriptor must also agree on policy bytes.
  This detects the observed same-size rewrite when filesystem timestamps remain
  unchanged. Both metadata checks repeat after verification. This finite check
  is not an atomic snapshot against a writer deliberately changing both reads.
- Documented transient accept errors retain the listener and its ordinary bounded
  poll; invalid-state, resource and unknown errors still fail closed. The Linux
  pending-network allowlist is platform-specific; ambiguous EOPNOTSUPP stays fatal.
  A refused unauthenticated peer must not permanently stop subsequent joining.
- Diagnostics and errors are categorical. No address-keyed accounting, identity,
  token, certificate or media logging. Test certificates are disposable; runtime
  remains Python standard-library-only.

## Verification and retained failures

Configuration tests reproduced the missing settings and acceptance of arbitrary
bind hostnames. Lifecycle tests reproduced ten behavioral failures before the
fix. Preserve original failures, fixture mismatches and any sandbox socket denial
as distinct evidence under `out/service-connection-ownership`.

The abandoned manual-accept helper had two actual ownership failures: a completed
accept could lose its socket on cancellation, and a setup failure before transport
creation could retain a socket. Both motivated the public transport gate. Its
initial eleven bind failures were sandbox denials; the same initial source passed
18 tests with authorized sockets, before those two deeper defects were exposed.
The failing source/tests and subsequent public-gate results remain separate.

A broader default-event-loop probe then exposed Python's pre-transport acceptance
race in the public-server design: 30 Close calls appeared successful despite 40
runtime assertions and 80 unraisable resource warnings. This was a real normal
scheduler failure. A custom-factory restriction did not fix it. Preserve that
failed source/probe and require the corrected owned-acceptance regression to pass;
focused TLS tests alone are insufficient.

The same raw-client regression against preserved public-gate source records 16
assertions, 32 ResourceWarnings and two EOF timeouts. The owned-polling version
passes its 24-service burst/Close journey. Current focused results are 27 helper
tests, 40 lifecycle/server tests and three actual local TLS integration tests.
An initial polling failure was also retained: completed-task callbacks had not
retired bookkeeping before join returned. Explicit idempotent retirement after
joining corrected it; an extra scheduling delay is not the remedy.

Windows policy-stat tests first reproduced three failures caused by rejecting
different path/descriptor ctime receipts before reading. Eight new cases cover
normal loading, separate same-API metadata changes, stable-identity mismatches
and preserved POSIX checks; the focused policy module passes 83 tests. Those
receipts are synthetic around real bounded file I/O, not a Windows execution
claim. The actual Windows Python 3.12 service suite remains required.

The first full pre-freeze service run recorded **1 failed / 380 passed** in
`out/service-connection-ownership/service-prefreeze-attempt-1.log`. The inherited
unfinished-handshake test closed before polling had proved socket ownership and
assumed EOF alone; its read/teardown path raised ConnectionResetError. The corrected
test proves actual pending ownership and accepts only EOF/reset retirement, while
timeouts and protocol bytes still fail. The original result remains retained;
the corrected full pre-freeze rerun passed **381 tests in 3.52 seconds** under
Python development mode with ResourceWarning and unraisable warnings as errors.
Frozen-tip verification is still required.

The frozen tip requires its own full local bar, complete reference-service suite,
native race and real sidecar tests, full application suite including Art start UX,
and both hosted workflows including four desktop builds. Each desktop now runs
the service suite using real Python 3.12 before returning to its existing packaging
Python. Record exact results, tip/tree and any failure/recovery history in the PR
body and AFTER; do not infer hosted success from local tests or workflow text.

Intel Mac's Python 3.12 service-test setup builds the hash-verified cryptography
50.0.0 source using pinned Rust 1.88.0 and explicitly selected Homebrew OpenSSL,
recording that OpenSSL version. It is test-only dependency setup; it neither calls
the Python 3.11 application installer nor claims its separate private OpenSSL
3.5.7 packaging provenance. All four real desktop service runs remain required.

The initial published tip `847d22063c75b416026f5089728915f555c516a0` passed all
14 local checks (9,571 application tests, 381 service tests). Its own hosted
Linux service run then failed the real same-size policy rewrite test; Windows
also exposed socket-class fixture patching, a timing-sensitive ordering fixture,
forced-process termination assumptions, and a pytest environment limit caused by
an oversized parameter ID. These failures remain preserved, not retried away.

The correction retains the original mutation test and adds deterministic stable-
metadata/content-change cases. The oversized certificate remains 65,537 bytes;
only its test ID is shortened. Listener fixtures replace only their module's
socket namespace, preserving the runtime's socket class and exception trap.
The ordering test directly checks all writer aborts before listener join, with
the same 25 ms budget and separate negative timeout coverage. Windows's process
fixture translates a scoped CTRL_BREAK to SIGINT around the unchanged real CLI
and Runner; it requires actual stopped output and Ctrl-C exit, without claiming
native CLI support for CTRL_BREAK. POSIX retains actual CLI/SIGTERM behavior.

Separate review reproduced the transient-accept availability defect before its
correction. Twenty added helper cases cover real local recovery after an injected
peer error, bounded address-family progress and preserved fatal failures. The
corrected pre-freeze service suite passes **407 tests in 3.66 seconds**, with strict
resource/unraisable warning checks. The revised source still needs its own frozen-
tip full local and hosted evidence; the original tip's results cannot substitute.

The next source `ae97273fcbe2e7e8ee1cd882a60468fdf11ceca5` passed all 14 local
checks and its PR's Windows desktop job. Its own push then recorded **1 failed /
406 passed** on Windows: the real control/partial-HTTP shutdown test exceeded its
configured 25 ms service deadline. That failure remains under
`out/service-connection-ownership/revision-2-ae97273`; the passing PR job does not
erase it. The log does not identify a permanent leaked owner or measure the
runner's clock resolution. Python's [Windows platform notes](https://docs.python.org/3.12/library/asyncio-platforms.html#windows)
describe platform-dependent timer resolution; this is context, not a diagnosis
of that runner.

This real-socket success case now uses the existing production configuration
with ephemeral ports, including its 3 s plain shutdown budget. It observes the
real HTTP handler entering its second, incomplete header read before Close,
then checks both accepted descriptors, handlers, setup tasks and listener polls
are retired, with no HTTP reply and an erased registry. Aggregate failure
diagnostics distinguish attachment, open descriptors and unfinished join work.
The deliberately short held-peer, pending-attachment and failed-retirement tests
remain required and unchanged. No production timeout or ownership code changes.

A retained local diagnostic deliberately delayed real abort/poll scheduling:
25 ms failed while owned cleanup subsequently completed; the existing 3 s
budget succeeded. Its first probe's wrapper-lifetime warnings and corrected
clean run are both retained. This controlled model is not Windows emulation or
a reproduction of the hosted cause. The final corrected source still requires
fresh full local verification and both exact-tip hosted workflows, including
actual Windows execution; neither prior tip supplies that proof.

## Ten-second UX and remaining acceptance

No new guest decisions or credentials. The private invitation remains the join
mechanism; this slice does not activate an Internet profile or prove two-home
reachability. Art faces/voices/narration, either-person pause, independent listening
levels and Music reference/no-reference routing, mix and latency remain physical
acceptance requirements. CI cannot establish these outcomes.

Remaining implementation includes authenticated UDP return-path proof, ordinary
host credential issuance/storage/recovery, a compiled Internet profile, and native
Art names/lesson requests. Deployment and physical media checks need their own
existing authorization boundaries; Internet implementation is already authorized.

No merge/squash/tag/sign/release/Pages/Publish/deploy/spend/live Cisco, automatic
capture or unsolicited send. Unsigned 0.27.2 remains Jeff-only. No short codes,
public discovery, second media engine, other repository or second goal. Parked
#37/#49 and all prior drafts remain untouched. Karen remains pending.
