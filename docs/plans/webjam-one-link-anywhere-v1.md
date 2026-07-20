# WebJam one-link-anywhere v1 (historical ledger)

> **Historical record:** This preserves the 2026-07-13 recovery chronology for
> the v0.11.0 remote-session vertical slice. It is not the current branch or
> release-status record. Use
> [`../../README.md`](../../README.md) and
> [`../../TEST_PROCEDURE.md`](../../TEST_PROCEDURE.md) for the current v0.17.0
> source/physical boundary and historical v0.16.3 package evidence.

## Product promise

> Host or Join. Band Check. Play.

WebJam must choose and recover the connection path. A musician must never need
to configure a router, discover an address or port, choose a relay, install a
VPN, configure Jamulus, or understand the transport topology.

## Evidence language

- **Implemented** means the named code exists in the committed source tree.
- **Deterministic pass** means an automated test exercised that code without
  physical audio hardware or public infrastructure.
- **Real-harness pass** means official Jamulus 3.12.2 processes exchanged
  measured PCM at the JACK hardware boundary. It is not an acoustic result.
- **Packaged pass** means the built application, not a source checkout, ran the
  named behavior on the named platform.
- **Physical pass** means the named homes, networks, computers, interfaces,
  headphones, musicians, and applications were actually used.
- **NOT RUN** is a required truthful result, not a prediction.

## Repository and preserved baseline — 2026-07-13

- Canonical repository:
  `/Users/jeffstory/Claude/Projects/WebJam/repo`
- Dedicated branch: `codex/webjam-one-link-anywhere-v1`
- Starting/local/default/remote commit:
  `e3fb7ce1b649c34329fe3c258d951337c747ea14`
- `master` and `origin/master` were fetched and confirmed at that commit with
  divergence `0 0`.
- The starting tree was clean: no staged, unstaged, or untracked files; no
  stash; no branch upstream yet. One stale/prunable detached worktree record
  was observed and left untouched.
- Installed baseline: `/Applications/WebJam.app`, WebJam `0.10.0`, arm64,
  strict and deep code-signature verification passed.
- Installed-source build identifier:
  `8ee89081802fe5998f71299c4755b21ae5218cb9`.
- Preserved v0.10.0 artifact:
  `/Users/jeffstory/Documents/WebJam 2/WebJam-v0.10.0-TEST-NIGHT-macos-arm64.zip`
- Preserved SHA-256:
  `f955419909dc014b7172032b00524417983c09e8586c2217691c19838a0b3411`.
- Baseline CI was green for default-branch run `29281301154` and feature run
  `29281287329`.

The preserved ZIP is immutable baseline evidence. It must not be replaced by a
v0.11.0 candidate.

## Exact source baseline

Before implementation, against `e3fb7ce`:

- Ruff: pass.
- `compileall`: pass.
- `pip check`: pass.
- `git diff --check`: pass.
- offscreen UX smoke: pass.
- focused Host/Join, Band Check, transfer, recording, Studio, invitation, and
  real-Jamulus-selection suite: `315 passed, 14 skipped in 10.70s`.
- full suite: `1350 passed, 17 skipped, 1 warning, 6 subtests passed in
  51.48s`.
- The warning was the pre-existing Starlette/httpx deprecation. Qt WebEngine
  printed harmless profile-shutdown warnings after completion.

## Non-negotiable evidence boundary

- Public rendezvous/relay deployment: **NOT RUN by design**. This goal permits
  only a self-hostable local/CI reference service and documents the external
  infrastructure still required.
- Two independent homes and ordinary residential NATs: **NOT RUN**.
- Two-musician bidirectional acoustic audibility: **NOT RUN**.
- Actual macOS/Windows interface selection and heard route: **NOT RUN**.
- Guest original recorded during a real Internet outage: **NOT RUN**.
- Logic Pro import and playback: **NOT RUN**.
- Packaged VoiceOver and NVDA review: **NOT RUN**.

Simulation, loopback, container, and JACK results must not fill any item above.

## Guardrails

- Preserve the v0.10 launch model, three-part symbol, meeting hierarchy,
  black/white/gray/burnt-orange palette, permanent Band Check, canonical
  participant/take/Studio identity, and role-aware End/Leave behavior.
- Jamulus remains the only live-music engine. Webex remains optional for
  conversation/video.
- Normal UI contains no addresses, ports, NAT/ICE/TURN/QUIC language, relay
  selection, certificates, credentials, or manual network setup.
- Remote Jamulus and control endpoints remain loopback-bound wherever the
  selected transport permits it.
- Invitation material, keys, raw peer addresses, names, and home paths are
  excluded or redacted from logs, exceptions, metrics, support artifacts,
  manifests, filenames, subprocess arguments, and snapshots.
- No public service, paid resource, production credential, kernel extension,
  root requirement, router change, or separate VPN account is authorized.
- Do not claim audibility from a process, socket, meter, packet, or RPC result.

## Architecture investigation ledger

The selected design and rejected alternatives belong in
`docs/adr/0001-remote-session-transport.md`. Prototype figures are evidence for
the decision only; they are not implementation or WAN certification.

### In-process Python QUIC prototype

An isolated `/tmp` prototype used aioquic 1.3.0 and cryptography 49.0.0 without
changing the repository:

- dependency target: 22 MiB and 466 files;
- direct loopback handshake: 10.801 ms;
- 500 200-byte datagram echoes: zero loss, 0.141 ms p50, 0.394 ms p95;
- opaque forwarding-relay handshake: 6.915 ms;
- 500 relayed echoes: zero loss, 0.168 ms p50, 0.226 ms p95;
- the relay forwarded 1,131 ciphertext datagrams and did not observe the
  plaintext sentinel;
- an 8 MiB reliable-stream echo sustained 8.13 MiB/s through the same relay;
- a ten-second 2,000-datagram run delivered zero loss at 198.2 round trips/s,
  with 1.076 ms p50, 1.601 ms p95, about 15.45% of one CPU core for both peers
  and relay in one Python process, and about 40.1 MiB maximum RSS.

This proved the loopback-proxy/encrypted-datagram/stream shape, but not Internet
path discovery or NAT traversal.

### Native sidecar prototype

An isolated `/tmp` prototype pinned Pion ICE v4.3.0, Pion TURN v5.0.12,
Pion transport v4.0.2, and quic-go v0.60.0. Initial successful results:

- fixed-peer `net.PacketConn` adapter over Pion ICE carried QUIC TLS 1.3,
  QUIC DATAGRAM, and a concurrent reliable stream;
- stripped static macOS arm64 binary: 8,113,522 bytes (3,205,025 bytes
  gzip-compressed);
- direct ICE: 602.4 ms connect, 1.46 ms QUIC handshake, 500 alternating
  440/660-byte datagrams with zero loss and 0.081 ms p50 / 0.255 ms p95;
- TURN relay: 0.278 ms connect, 0.891 ms QUIC handshake, 500 datagrams with
  zero loss and 0.072 ms p50 / 0.441 ms p95;
- direct and relay paths each SHA-verified a concurrent 16 MiB stream;
- under deterministic 5% packet loss plus 5 ms delay and 2 ms jitter, the
  datagram plane truthfully delivered 475/500 probes while the reliable stream
  recovered and SHA-verified. The slowed bulk stream demonstrates that media
  transfer needs explicit pacing/backpressure during live audio.
- five repeat runs had zero datagram loss on both paths; median direct/relay
  p50 RTT was 0.048/0.078 ms and p95 was 0.130/0.243 ms;
- under the same impairment through TURN, 284/300 live probes arrived while a
  4 MiB stream SHA-verified in 25.57 seconds; an 8 MiB stream exceeded the
  bounded 45-second test window;
- static stripped builds succeeded for macOS arm64 (8,113,522 bytes), macOS
  x64 (8,751,312), Windows x64 (8,760,320), and Linux x64 (8,614,072);
- representative 500-probe plus 16 MiB runs used about 19.3 MiB direct and
  20.1 MiB relayed maximum RSS for both peers, TURN, and vnet in one process.

The feasibility gate passed and ADR 0001 selects the static Go sidecar. Real
NATs, kernel UDP buffers, IPv6/TURN MTU, ICE restart, address migration, and
relay failover remain implementation/integration gates.

## Known baseline truth defect

Pinned Jamulus 3.12.2 does not expose `jamulusclient/setMuted`, despite the
v0.10 Talk Break implementation and tests claiming it does. Official source
and the bundled binary expose `setFaderLevel`, `setMidiSettings`, `setName`, and
`setSkillLevel`, but not `setMuted`. At baseline, the fake RPC server accepted
unknown `jamulusclient/set*` methods and hid this defect.

Completed release correction:

- represent `live_send_mute=false` as a capability;
- remove or disable Talk Break rather than optimistically changing UI state;
- make the fake RPC reject unknown methods;
- add a pinned method-contract test and real 3.12.2 negative evidence;
- never reapply a nonexistent mute during reconnect;
- explain that the musician should use the interface mute or Stop Audio.

No client-mode packaged probe was possible on this Mac at baseline because no
usable two-input/two-output CoreAudio interface was attached. That physical
result remains **NOT RUN**.

## Exact v0.11.0 candidate evidence — 2026-07-13

- Source/build commit:
  `1a03927e3ea8eb76557617aa59e985a551c35e0b`.
- Artifact:
  `/Users/jeffstory/Documents/WebJam 2/WebJam-v0.11.0-TEST-NIGHT-macos-arm64.zip`.
- Artifact SHA-256:
  `11bc573a28c9804163d34deb5fbf3779dd6aaa2338f3a25e6e70819776b41e4f`.
- The exact local build environment and resolved Python package inventory are
  preserved beside the ZIP as `WebJam-v0.11.0-build-environment.txt` and
  `WebJam-v0.11.0-build-dependencies.txt` (Python 3.12.13, PyInstaller 6.21.0,
  PySide6 6.11.1, Go 1.25.12).
- Installed candidate: `/Applications/WebJam.app`, version `0.11.0`, arm64.
- Rollback app:
  `/Applications/WebJam-v0.10.0-before-v0.11.0-TEST-NIGHT.app`.
- The preserved v0.10.0 ZIP and its earlier SHA remain unchanged.
- The pinned official Jamulus 3.12.2 DMG matched SHA-256
  `adf185aaf78e27d9f603daa6895e7698b4bdffee18fe29ad789cd7c1021d6bd0`.
- The fresh extraction and installed copy pass strict and deep-strict outer
  verification, strict nested Jamulus/client/server/sidecar verification,
  canonical signed-sidecar hash validation, exact arm64/build-ID validation,
  and bounded ready/hello/shutdown IPC. The sidecar SHA-256 is
  `4ab81da324d01c6fb62ad4bb664ee6a220bd421d4ad7939df2eac8aa26a16e3f`.
- Final source validation is `1613 passed, 18 skipped, 1 warning, 6 subtests
  passed`; Ruff, compileall, pip check, UX smoke, workflow YAML, and diff checks
  pass. The warning is the existing Starlette/httpx deprecation.
- The exact installed app completed two isolated-home, 20-second normal Qt
  close cycles. In both cycles the bundled server/RPC initialized, recording
  stayed off, mode-0600 secrets were enforced, cleanup released all child
  processes and ports, relaunch was clean, and no audio files were created.
- This Mac exposes output-only devices and no CoreAudio input. The bundled
  Jamulus client therefore truthfully stopped with “couldn't find a usable
  CoreAudio audio device”; the two-cycle check observed a zero-client roster.
  A packaged live-client/roster/audio pass remains **NOT RUN**, not failed or
  inferred. Attach the test interface and complete the two-Mac worksheet.
- The app is ad-hoc signed for private testing, not Developer ID signed or
  notarized. No tag or public release was created.

## Implementation ledger

- [x] Establish repository, artifact, installed-app, CI, and source-test truth.
- [x] Accept the architecture decision and threat model.
- [x] Add the typed versioned transport seam, deterministic path policy, and
  native `reference-local` sidecar/runtime integration outside
  `ApplicationController`.
- [x] Add a bounded, self-hostable rendezvous/relay reference and abuse tests.
- [x] Add the strict, opaque, expiring, revocable, one-use v3 invitation codec
  and service-backed enrollment lifecycle.
- [x] Add authenticated peer-to-peer encryption and generation/replay rules to
  the secure-session core, deterministic labs, and native reference runner.
- [x] Add deterministic direct/relay scoring, fallback, hysteresis, and stop to
  the transport policy and impairment lab; live runtime observation remains
  open.
- [ ] Route one real host and one real guest Jamulus process through the fabric.
- [ ] Move control and resumable originals onto the secure session plane.
- [x] Add canonical audio-route profiles and honest confirmation levels.
- [x] Extend Band Check with explicit transport, remote-signal, decoded-fixture,
  and musician-confirmed evidence; real runtime population remains open.
- [x] Remove the false Talk Break capability.
- [x] Add the isolated impairment lab and remote failure matrix.
- [ ] Complete privacy, dependency, license, accessibility, and resource audits.
  Python and Go dependency audits plus the transport license inventory pass;
  final accessibility and resource audits remain open.
- [x] Pass focused/full source, independent native-sidecar, dependency,
  reference-container, and exact-package integrity gates. Prior exact-engine
  Jamulus/JACK longevity evidence remains preserved; current two-Mac acoustic,
  Logic, accessibility, and physical-resource gates remain open.
- [x] Build/install/exercise a fresh v0.11.0 candidate and record exact SHA and
  signature evidence. Package integrity and no-input cleanup pass; live client
  audio remains **NOT RUN** because no input device is attached.
- [x] Historical branch work was later incorporated into `master`; the
  `1438a73` master baseline is recorded in the active last-mile record. That
  integration did not certify the still-open two-Mac or Logic physical gates.

### Current native reference proof — 2026-07-13

The committed Go integration packages pass against an independently spawned
Python reference-service process. Two runner instances prove the distinction
between `host_registered` and `peer_connected`, one-use enrollment, sealed
guest bootstrap and host acknowledgment, mutual TLS with exact pins,
bidirectional exporter proofs, quarantine before both proofs, running peer
pumps, and bidirectional live payload delivery through the real exact-pair UDP
relay and loopback Jamulus proxy seam. A fresh host invitation can register
after bounded close while its prepared ephemeral identity remains inside the
sidecar; shutdown destroys it.

This is real native protocol/QUIC/UDP service-process-boundary evidence. The runner
instances execute the production orchestration in the Go test process; they
are not packaged sidecar executables, and the Jamulus endpoint is a controlled
UDP socket rather than two real Jamulus processes. It is therefore **not** a
packaged pass, public deployment, ordinary-home NAT result, musician acoustic
result, or secure original-media transfer result.

## Rollback boundary

The same-LAN v1/v2 implementation remains an isolated compatibility transport.
The remote v3 path must be disableable without changing recording, participant,
take, Studio, or Logic schemas. If the encrypted fabric cannot establish a
bounded safe path, WebJam stops and gives one useful action; it never exposes
the Jamulus server publicly or silently falls back to plaintext Internet
control/media.
