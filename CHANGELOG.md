# WebJam Changelog

All notable improvements and features for the WebJam music collaboration platform.

---

## [0.16.3] — Unreleased cross-platform release candidate

### Native desktop packages

- Added native Windows x64, Intel macOS x64, and Ubuntu 22.04 x64 build gates
  from one source commit. Every deliverable is freshly installed, mounted, or
  extracted and checked for application/transport architecture, exact build
  provenance, transport hash and protocol lifecycle, required data, and a clean
  frozen UI launch.
- Added direct GitHub-ready desktop installers: a per-user Windows Setup `.exe`
  with Start-menu and optional desktop shortcuts plus clean uninstall, and
  drag-to-Applications `.dmg` files for Intel and Apple Silicon Macs. The
  portable ZIPs remain available as fallbacks.
- Added the first Linux client package. It carries the checksum-pinned official
  Jamulus 3.12.2 Ubuntu `.deb`, visible install instructions, lowercase binary
  discovery, x86-64 ELF validation, and a packaged-app smoke against a private
  JACK graph with authenticated Jamulus RPC and clean process shutdown. Linux
  and Windows remain truthfully Join-only; managed hosting remains macOS-only.
- Fixed fresh Windows installs: the Host/Join dialog now exposes the packaged
  Jamulus installer from PyInstaller's real `_internal` data root. WebJam
  requires the exact 3.12.2 filename and pinned SHA-256 both at discovery and
  immediately before launch.
- Tagged Windows builds now require valid Authenticode credentials and verify
  both payload executables, Setup, and the embedded uninstaller after a real
  fresh-install cycle. Branch builds remain usable for legacy v1/v2 testing but
  state that secure packaged v3 fails closed when unsigned. The upstream
  Jamulus installer has its own unsigned-publisher UAC limitation.
- Test Night evidence now records the actual desktop target, including Intel
  macOS, Windows x64, and Linux x64. Release automation refuses to mutate a
  previously published tag and creates new tag releases as drafts for exact
  hardware certification.

### Reliability

- Replaced an immediate JACK graph assertion with a bounded convergence check
  that retains process-health checks and reports every missing route on timeout.
  This removes an observed CI race without retrying or weakening the real
  Jamulus integration gate.

### Distribution boundary

- Intel/Apple Silicon macOS apps and DMGs remain ad-hoc signed and non-notarized
  private test builds. Windows publisher signing, physical interface audio,
  audible two-musician proof, and Ubuntu hardware audio remain release gates;
  automation does not claim human audibility.

### Source verification

- The clean source gate reports **1,866 passed**, 19 environment-bound skips,
  and one dependency deprecation warning. Ruff, Go tests/vet, workflow YAML,
  and Actionlint pass; native archive evidence is recorded only after the
  matrix builds finish from the committed candidate.

---

## [0.16.2] — 2026-07-16 test-build release candidate

### Simple session flow and take safety

- Normal Host and Join now move to the session automatically after fresh,
  authenticated Jamulus connection and local-identity evidence. Musicians no
  longer have to finish a WebJam sound wizard, confirm a startup Webex choice,
  or press an extra Enter Jam action. Jamulus remains visible for its own audio
  setup, and WebJam never treats connection evidence as proof of audibility.
- Startup, retry, reconnect, invite, recording, and shutdown events now use a
  generation-guarded session snapshot. A late callback from a replaced or
  failed attempt cannot redraw or cancel the current session.
- Incoming Local Originals remain preserved first. Timing alignment is allowed
  only against the matching verified server reference with sufficient anchors,
  confidence, and residual evidence; otherwise Studio keeps the media visible
  but blocks it from a misleading aligned export.
- Recording maintenance now runs outside the Qt completion path and project
  manifests use a short per-take lock plus exact-revision replacement. Late or
  competing work retries safely rather than overwriting newer take truth.

### Release boundary

- The GitHub Latest release contains the macOS Apple-Silicon test build from
  `c4bc5e8fd40f54efc85d0a4af504cf627ec44106`:
  `WebJam-v0.16.2-TEST-NIGHT-macos-arm64.zip`, SHA-256
  `5855af408c5182408d091c9029bdfa61d8f9abf96801822df319d55f649e688d`.
  A fresh extraction passed deep signature, bundled-engine executable, and
  arm64 transport build-ID/checksum verification.
- The build is ad-hoc signed and not notarized. Physical two-Mac audio,
  hardware change/recovery, long-recording recovery, and external-editor
  import remain **NOT RUN** until musicians perform and record those checks.

---

## [0.16.1] — 2026-07-15 private stabilization candidate

### Stability and truthful recovery

- Added the isolated Dual-Musician Rehearsal Lab. It exercises real WebJam
  host/guest peer sessions, loopback HTTP transfer, durable identities,
  deterministic capture fixtures, Studio state, Track Export, stale-invite
  rejection, and cleanup. Its optional Linux/JACK companion exercises real
  JamulusServer and two Jamulus clients without claiming human audibility or
  physical hardware proof.
- Private v2/v3 bearer invitations are no longer accepted from process
  arguments, and WebJam URLs are removed before Qt retains argv. Pasted and
  FileOpen invitations retain their typed ingress path.
- Fixed a peer-media collision where two musicians could reuse a local segment
  UUID and one verified original would be omitted from the host project.
- Transfer descriptors now preserve exact structured capture gaps, reject
  metadata changes after both partial and completed upload, and fail closed if
  a crash-orphaned published WAV has no authoritative descriptor sidecar.
- Host shutdown now cancels stale maintenance lifecycle work: an old worker
  cannot write a manifest or notify the UI after Leave/End or rapid restart.
  Incomplete peer HTTP uploads are also released during shutdown.
- Replaced the previous trinity glyph with the supplied three-loop, three-ring
  WebJam mark. It remains a simple native vector/icon in black, white, and
  burnt orange only.

### Verification

- Source gate: **1,798 passed**, 19 environment-bound skips, 1 known
  dependency warning, and 6 subtests with zero failures/errors.
- Built the private Apple-Silicon archive from
  `7c6e7e2533facdb6162d180d57256a5a101faad8`:
  `WebJam-v0.16.1-TEST-NIGHT-macos-arm64.zip`, SHA-256
  `a983b06781a6af9a9fb3ddff7a2f3852192fa044fdb116a7a73357c4f3546fdd`.
  The checksummed official Jamulus 3.12.2 DMG was freshly staged; a fresh
  extraction passed strict/deep outer and nested signature checks, arm64
  fabric build-ID/checksum verification, and a bounded frozen Host lifecycle
  smoke with no leftover owned process. The archive is ad-hoc signed for
  private testing, not notarized. Physical rehearsal evidence remains
  **NOT RUN**.

---

## [0.16.0] — 2026-07-15 test-night package

### Jamulus-first startup

- Replaced the startup device wizard with one simple choice: **Host a Jam** or
  **Join a Jam**. The private server starts before the visible Jamulus client
  for a host; a guest starts one visible Jamulus client from one parsed invite.
- Jamulus now owns its interface, input/output channels, buffer, jitter, and
  musician mix. WebJam launches the supported filename-only profile
  `WebJam-native-v0.16.ini`, never writes its contents, and leaves the normal
  `Jamulus.ini` untouched.
- Connection proof requires an owned process, authenticated Jamulus RPC, the
  expected connection, and exactly one local musician. Audibility remains an
  explicit human confirmation; Webex is optional only after music is ready.
- Startup recovery persists only allowlisted phase/profile facts and fails
  closed when that profile truth no longer matches.

### Recording, Studio, and identity

- The first host recording choice is now clear: record the shared Jamulus take
  only, or explicitly open Recording Setup to keep this Mac's Local Originals.
  This does not alter Jamulus audio settings.
- Studio remains a Logic-like multitrack review surface—not a Logic
  integration—and offers playback-output selection only while reviewing a take.
- Replaced the old WJ monogram with WebJam's native three-loop trinity mark and
  standardized the interface on black, white, neutral gray, and burnt orange.

### Verification

- Built the final private Apple-Silicon package from
  `a36789978efbaac5e85fbc5c6ef55abae4ed42e3`:
  `WebJam-v0.16.0-TEST-NIGHT-macos-arm64.zip`, SHA-256
  `3ad2da6eccd99eb3965cc0e637ff147198e19446b3d878e4631a689cd5c9bf7b`.
- The final source gate reported **1,783 passed**, 18 environment-bound skips,
  and 6 subtests with zero failures/errors. The ad-hoc-signed,
  non-notarized archive passed fresh-extraction strict/deep outer and nested
  Jamulus/JamulusServer 3.12.2 signature checks, transport verification, and a
  frozen Host smoke. v0.15.0 is preserved as the rollback ZIP and app.
- Physical two-Mac audio, hardware change/recovery, recording, and external
  editor import remain **NOT RUN** until musicians perform those checks.

---

## [0.15.0] — 2026-07-14 private test-night candidate

### Release verification

- Built the exact Apple-Silicon package from
  `30ece85eb6a555dbcb2ef35753e4c6c9e8679770`:
  `WebJam-v0.15.0-TEST-NIGHT-macos-arm64.zip`, SHA-256
  `58ff7a6071d319a11119547028f454b579fd149912d17dfc0fc20ef3cef10152`.
  The v0.14.0 ZIP remains the rollback package.
- The ad-hoc-signed, non-notarized archive passed fresh-extraction strict/deep
  signature checks, nested Jamulus/JamulusServer 3.12.2 checks, arm64 native
  fabric checksum/build-ID verification, and two isolated six-second launch
  and ordinary-cleanup cycles. The isolated launch machine had no default band
  input, which was truthfully reported as a route-setup block rather than an
  audio pass.
- The full source gate passed **1,752 tests**, with 18 environment-bound skips,
  1 known warning, and 6 subtests. `transport` `make check`, `go test ./...`,
  `go vet ./...`, `go mod verify`, and `go mod tidy -diff` passed on the
  release Mac.
- Physical CoreAudio, two-Mac audio, recording/recovery, outage/reconnect, and
  import in an external editor remain **NOT RUN**. No source or package check
  is presented as evidence of those observations.

### Simpler session and Studio

- Added the pure Session Conductor: one fact-derived musician-facing phase and
  one dominant action across host readiness, joining, reconnecting, recording,
  take validation, Studio review, Track Export, and cleanup. It rejects stale
  callbacks and never promotes a process, meter, button press, or file into
  false connection, audibility, saved-media, or import proof.
- Rebuilt the session shell around a quiet meeting layout: original three-path
  WebJam mark, restrained header, focused status HUD, responsive band tiles,
  one bottom control bar, and progressive **More** controls. Runtime color is
  black, white/neutral gray, and Longhorn burnt orange only.
- Renamed the Studio handoff to **Track Export**. It keeps familiar multitrack
  review cues—transport, elapsed-seconds ruler, track headers, mute/solo,
  gain, pan, and inspector—without adding DAW editing or any Logic integration.
  It produces a portable atomic WAV package, source reports, and checksums.

### Closed pilot evidence

- Added explicit `--test-night` operator mode. Normal musicians never see it;
  the hidden dialog owns no persistence and merely asks the controller to
  record safe observations.
- Added a private, bounded, hash-linked local ledger and sanitized report.
  Automatic facts and explicit human observations are separate, and evidence
  cannot include audio, invitations, credentials, addresses, device IDs,
  paths, names, or free-form notes. Interrupted runs restore paused.

---

## [0.14.0] — 2026-07-14 private test-night candidate

### Candidate verification

- Built the exact Apple-Silicon package from
  `045c5acb01687a4088b0bd618dab4d0ab6200804`:
  `WebJam-v0.14.0-TEST-NIGHT-macos-arm64.zip`, SHA-256
  `cbcbdc038ac3d663e15870990ae5fea2a09819cdd55adbaa7463a64405ef8321`.
- The candidate is arm64 and bundles official Jamulus/JamulusServer 3.12.2.
  Fresh extraction passed strict/deep signature checks, nested-app inspection,
  exact native-fabric build-ID verification, and two isolated six-second
  offscreen launch/TERM cycles. It is ad-hoc signed, not notarized.
- The source gate recorded 1,719 passed, 18 skipped, one known warning, and
  6 subtests. Native transport `go test ./...` and `go vet ./...` passed.
- Physical CoreAudio, two-Mac audio, roster, reconnect, recording/recovery,
  and Logic Pro import remain **NOT RUN**. The v0.13.0 ZIP is now retained only
  as a rollback artifact; its record below is historical.

### Studio take review

- Reworked Studio into a focused take-review workspace: a shared elapsed-time
  timeline, track lanes, selected-track inspector, compact level meter, and
  non-destructive gain, pan, mute, solo, and Logic-export controls. It does not
  claim tempo, bars, beats, beat editing, or a completed DAW import.
- Added an atomic per-take Studio sidecar for schema-v2 projects. It stores
  local mix and export choices separately from WAVs and `webjam-take.json`,
  rejects mismatched or unsafe state, and reconciles tracks by durable ID.
- Logic export now applies saved mix/export choices by durable schema-v2 track
  ID, so reordering or selecting a subset of tracks cannot remap those choices
  by position. Legacy projects retain positional compatibility only where no
  durable ID exists.

---

## [0.13.0] — 2026-07-14 historical rollback candidate

### Candidate verification

- Built the exact Apple-Silicon package from
  `4d09810d7fb3c7f7355ca1d88e8218bb8ea784dd`:
  `WebJam-v0.13.0-TEST-NIGHT-macos-arm64.zip`, SHA-256
  `6b32a1d85cb64eb0bc97fecb7dadcd527159420a675358176cd75745d6565b3b`.
- The candidate is arm64 and bundles official Jamulus/JamulusServer 3.12.2.
  Fresh extraction passed strict/deep signature checks, nested-app inspection,
  exact sidecar build/hash/IPC validation, and two isolated six-second
  offscreen launch/TERM cycles. It is ad-hoc signed, not notarized.
- The final source gate recorded 1,706 passed, 18 skipped, one known
  Starlette/httpx warning, and 6 subtests. Physical CoreAudio, two-Mac audio,
  roster, reconnect, recording recovery, and Logic import remain **NOT RUN**.

### Durable recovery and truthful takes

- Local isolated capture now checkpoints about once per second: it flushes the
  writer, synchronizes the audio file, and records opaque take/session IDs,
  durable frame count, gaps, and capture facts. Parent-directory synchronization
  closes the atomic-publication durability gap on supported POSIX filesystems.
- Startup recovery safely promotes abandoned hidden captures to visible recovery
  folders without following symlinks or adopting a live writer. A recovered
  project is **Needs Attention**, never a completed take. Audio beyond the last
  confirmed durable frame is disclosed as an unverified crash gap and blocks a
  false-complete export.
- A recovered guest original is preserved on that guest Mac for review. It is
  not silently re-uploaded or represented as having reached the host.

#### Conservative Logic handoff

- Studio's Logic export now refuses a selected track explicitly marked silent,
  and refuses an unaligned guest/local original. The musician can intentionally
  deselect a track, or retain the aligned Jamulus server track, rather than
  producing an apparently complete but misleading package.
- Per-track Logic-export selection is local Studio state; it does not mutate the
  take manifest. The UI gives a short safe explanation instead of exposing a
  path, credential, or other internal failure detail.

#### One-use remote invitation truth

- A v3 remote invitation may be retried only when the sidecar fails before
  `open_guest` begins enrollment. Once enrollment was attempted, WebJam clears
  the invitation and requires a fresh one; it does not fall through to a legacy
  LAN launch or imply that a consumed credential remains usable. The v3 profile
  remains a loopback/CI laboratory boundary, not a deployed remote service.

---

## [0.12.0] — 2026-07-14 private test-night candidate

### Candidate verification

- Built the exact Apple-Silicon package from
  `796e9a4ddebe79f430b0ded8cf8034bc27836dd0`:
  `WebJam-v0.12.0-TEST-NIGHT-macos-arm64.zip`, SHA-256
  `01427316820b884d61546d40a9327a49cedf43d6a60a4d88b5b29ab4c693a24c`.
- The candidate is arm64 and bundles official Jamulus/JamulusServer 3.12.2.
  Fresh extraction passed strict/deep signature checks, nested-app inspection,
  exact sidecar build/hash/IPC validation, and two isolated six-second
  offscreen launch/TERM cycles. It is ad-hoc signed, not notarized.
- The final source gate recorded 1,687 passed, 18 skipped, one known
  Starlette/httpx warning, and 6 subtests. Physical CoreAudio, two-Mac audio,
  roster, reconnect, recording recovery, and Logic import remain **NOT RUN**.

### Last-mile session trust

- Added one privacy-safe authoritative session lifecycle record for Host/Join,
  Band Check, launch, roster-confirmed connection, recovery, recording
  finalization, and shutdown. The support bundle now includes only its
  allowlisted/redacted transition timeline.
- Added fail-closed private-LAN pre-share readiness. A legacy v1/v2 host does
  not enable **Copy Invite** until WebJam observes its authenticated local
  server, expected UDP listener, and a private LAN address. This is not a
  public-Internet, NAT, or remote-home reachability claim.
- Recorded the current v1 last-mile acceptance boundary and manual gates in
  `docs/WEBJAM_V1_LAST_MILE_PLAN.md`.

### Recording safety

- Added a fail-closed recording-storage guard. Band Check checks the selected
  folder before the session starts, and **Record** rechecks free space using the
  actual roster before opening local capture or arming the server recorder.
  An unsafe result starts no take and gives one recovery path; low storage is a
  warning to make room before a long rehearsal, not a guarantee of one. This
  behavior is included in v0.12.0; its physical drive-full result remains a
  separate **NOT RUN** gate.

### Recording evidence and recovery

- The v0.12.0 schema-v2 take manifests retain optional recording-session
  evidence: start/end timestamps only after recorder-server confirmation, host
  identity and protocol label, plus a bounded redacted lifecycle/recovery
  timeline. Invitations, network addresses, credentials, and raw device
  identifiers are excluded.
- While a take is live, v0.12.0 writes that evidence to a private,
  crash-safe checkpoint below the chosen Takes folder. An untrusted or
  unfinished checkpoint is recovery-needed truth, never a completed-take
  claim; it is removed only after final manifest publication. The final Logic
  export copies nonempty evidence into `webjam-logic-export.json`. Physical
  recovery and Logic-import results remain **NOT RUN**.

### Simpler musician setup

- Added a one-screen v0.12.0 confirmation after Host/Join: musician name plus
  Band input and Band output & review are saved before Band Check.
- Reworked in-session **Settings** into a short musician-first page: name,
  Band input, Band output & review, and a collapsed optional conversation link.
  On macOS, a complete pair persists as CoreAudio UIDs and is staged for the
  next Jamulus session.
- Removed Band Check's empty technical-details disclosure. Private diagnostics
  remain available only through the quieter **Save Support Bundle** action;
  **Audio Settings** is now the obvious correction path.

### macOS Jamulus route ownership

- Added read-only native CoreAudio discovery without PyObjC or a helper binary.
  WebJam resolves persistent UIDs, rejects duplicate Jamulus selector names,
  missing channels, and non-48-kHz devices before launch.
- Added a protected WebJam-owned `WebJam-route-v1.ini` in Jamulus's allowed
  container. The macOS client receives only the filename with that directory as
  its working directory; WebJam never overwrites a musician's `Jamulus.ini`.
- Route configuration is deliberately not audibility proof. A frozen route plan
  is revalidated on reconnect instead of silently switching defaults; a local
  PortAudio meter is skipped while Jamulus owns the live pair.

---

## [0.11.0] — 2026-07-13 private test-night candidate

### Remote-session foundation — local and CI evidence only

- Added a strict v3 invitation boundary with opaque, expiring, revocable,
  one-use enrollment material. Remote invitations use a compiled profile ID,
  never a caller-supplied endpoint, and secret-bearing values have constant
  string representations and are excluded from ordinary diagnostics.
- Added the statically compiled `webjam-fabric` process boundary, bounded
  JSON-lines IPC, loopback Jamulus proxy primitives, mutually authenticated
  QUIC session core, and deterministic direct/relay laboratory coverage. CI
  now builds the sidecar for macOS arm64, macOS x64, and Windows x64 and stages
  it beside the packaged desktop executable.
- Frozen builds ignore environment path/build-ID overrides and require the
  packaged sidecar, a canonical package-generated SHA-256 manifest, the
  expected architecture, safe owner/mode, its native platform signature, and
  the exact embedded build ID before accepting it. On macOS the manifest is
  sealed as data under `Contents/Resources`; placing text in `Contents/MacOS`
  would make strict code-signature verification treat it as unsigned code.
- Remote Jamulus host and guest launches omit the musician name from process
  arguments. The name is applied only after authenticated loopback JSON-RPC is
  available; legacy v1/v2 launch behavior remains unchanged.
- Added a dependency-free, containerizable reference service with bounded
  in-memory registration, one-use enrollment, opaque signaling, and an
  authenticated exact-pair UDP relay. The service is a native WebJam protocol,
  not an HTTP/WebSocket signaling server or a stock TURN server.
- The native reference integration now distinguishes host registration from
  peer connection and proves sealed bootstrap/acknowledgment, mutual TLS with
  exact pins, bidirectional exporter proofs, pre-proof quarantine, peer pumps,
  live payloads through the real relay/loopback-proxy seam, reset, and bounded
  close against an independently spawned service process. Its endpoints are
  controlled UDP sockets, not real Jamulus processes or physical musicians.
- Band Check can retain explicit local, transport, remote-signal, decoded-
  fixture, and musician-confirmed evidence without treating a socket, packet,
  process, meter, or fixture as proof that a person heard the live route.
- Added deterministic impairment coverage for latency, jitter, loss, reorder,
  duplication, bandwidth limits, blackholes, path changes, relay failure,
  restart, and cleanup, while keeping physical hardware and public-network
  results separate.

### Release boundary

- No public rendezvous or relay is deployed or bundled. `reference-local` is a
  loopback-only lab profile; it is not an “anywhere” service and cannot be
  redirected through desktop input or IPC.
- The existing v1/v2 same-private-LAN path remains the ordinary musician flow.
  Public Internet deployment, two independent homes/NATs, two-musician
  acoustic audibility, physical interface routing, Logic Pro import, and
  packaged VoiceOver/NVDA review remain **NOT RUN**.
- The private Apple Silicon candidate is
  `WebJam-v0.11.0-TEST-NIGHT-macos-arm64.zip`, SHA-256
  `11bc573a28c9804163d34deb5fbf3779dd6aaa2338f3a25e6e70819776b41e4f`,
  built from `1a03927e3ea8eb76557617aa59e985a551c35e0b`. Fresh-extraction,
  installed-copy, strict/deep signature, sidecar integrity/IPC, and two
  no-input normal-close cleanup cycles pass. This Mac has no CoreAudio input,
  so packaged live-client/roster audio remains **NOT RUN**. The candidate is
  ad-hoc signed, not Developer ID signed or notarized.

---

## [0.10.0] — 2026-07-13 certification candidate

### Band Check before guesswork

- **Band Check is now the permanent readiness path.** It guides each musician
  through the music engine, owned host service, selected local input, headphone
  left/right output, a five-second PCM24 recording, explicit playback
  confirmation, Studio transport, and a plain-language result: **Ready to
  Jam**, **Ready with a Warning**, or **Action Needed**.
- Live Band Check never opens a second device or restarts a running music
  service. Its copy now distinguishes WebJam's separate PortAudio input from
  Jamulus observations, so a moving local meter is not presented as proof of
  what a bandmate hears.
- **Save Support Bundle** previews the same immutable allowlisted artifact that
  is saved as a ZIP. The separate diagnostics shortcut creates its own short,
  sanitized clipboard summary. The private archive excludes audio, notes,
  transcripts, Webex content, meeting/invite links, settings/environment dumps,
  secrets, home paths, and arbitrary personal files by default; bounded log
  excerpts are recursively redacted.

### Originals survive reconnects

- A schema-v2 take now uses durable session, take, participant, track, source,
  and segment IDs. Explicit project placement, device/rate/channel/format
  facts, SHA-256, media status, reconnect segments, and gap intervals replace
  filename/name inference.
- The host, and a guest connected through an active v2 private invite, can
  explicitly keep interface inputs 1 and 2 as separate local PCM24/48-kHz
  originals. A v1 guest still joins/plays and receives a server track, but has
  no WebJam-orchestrated local capture or delivery. Queue or write loss
  preserves absolute frame time by inserting disclosed silence instead of
  shortening the recording. Writer
  timeout, attach failure, crash, and shutdown preserve visible recoverable
  media and never steal a still-live writer's file handles.
- Each installation that uses a v2 private invite receives a stable
  session-participant identity. The invite is a reusable session-scoped bearer
  credential, not a one-use or one-guest token; anyone who has it on the trusted
  LAN can enroll until the host peer service restarts. Guest capture begins only
  after authenticated host recording state, continues while the peer control
  plane is unavailable, and uploads immutable segments in restartable chunks.
  Size, SHA-256, and PCM facts must agree before the host atomically attaches a
  copy; the guest original is never moved or deleted.
- End Session is blocked while a host take is recording or validating; the host
  presses **Stop Rec** and waits for **Take saved** first. **Leave Jam** finalizes
  active opted-in guest capture, persists the resumable queue, and attempts one
  final upload before disconnecting.
- The peer plane is intentionally limited to authenticated plain HTTP on the
  same RFC1918 IPv4 LAN. It does not claim TLS, IPv6, Internet, VPN, NAT
  traversal, or safe public exposure. Invite links now contain a private
  enrollment credential and should be shared only with the intended bandmate.

### Studio and Logic evidence

- Studio retains missing, partial, damaged, transferring, and failed-transfer
  truth. Playback and exact asynchronous waveforms support multi-segment,
  mixed-rate, reconnect-gap, and drift-adjusted projects; active seek reopens
  every reader and leaving Studio releases its output.
- Non-destructive alignment now measures repeated transients, signed start
  offset, long-take drift, mixed rates, gaps, residuals, and confidence. Manual
  nudge remains separate and can be restored to the automatic evidence.
- The Logic handoff now publishes common-origin numbered PCM24 stems, a server
  reference, Studio reference, marker/tempo/signature guidance, source
  manifest, alignment and recording reports, independent WAV analysis, and
  checksums. Missing or changed selected media blocks publication. The
  deterministic affine resampler is disclosed and is not claimed to be
  sample-perfect or mastering grade.

### Identity and certification boundary

- The placeholder **WJ** header has been replaced by an original three-part
  WebJam mark representing conversation, live music, and production. SVG, ICO,
  and ICNS assets use only black/white/neutral and Longhorn burnt orange; no
  purple or teal is part of the identity.
- A real Jamulus 3.12.2/JACK harness now measures two independently named
  clients at their hardware-boundary ports, checks cross-contamination,
  dropouts, server stems, Studio/export traversal, reconnect, resources, and
  owned-process cleanup. A separate longevity test refuses to count runs below
  3,600 seconds.
- Private peer-server startup now binds directly to the selected numeric LAN
  address instead of blocking the Qt thread on reverse DNS. A frozen-package
  regression and full Host lifecycle prove client/server/RPC startup, normal
  close, process cleanup, and port release.
- Automated evidence does not replace the final two-Mac musician and Logic Pro
  gates. At this changelog entry, bidirectional acoustic audibility and Logic
  import remain **NOT RUN**. The fresh private Apple Silicon ZIP is
  `WebJam-v0.10.0-TEST-NIGHT-macos-arm64.zip`, SHA-256
  `f955419909dc014b7172032b00524417983c09e8586c2217691c19838a0b3411`,
  built from `8ee89081802fe5998f71299c4755b21ae5218cb9`. Its fresh-extracted
  Host lifecycle passes twice. GitHub Actions run `29269188463` passed the
  exact-source 3,600-second native Jamulus/JACK certification with reconnect,
  recording cycles, bounded resources/xruns, and zero cleanup errors.

---

## [0.9.0] — 2026-07-13 test-night candidate

### A simpler first five seconds

- **Open WebJam. Choose Host or Join. Start playing.** The launch window is now
  one calm, responsive decision instead of a configuration surface. **Host a
  Jam** is the unmistakable primary action; **Join a Jam** opens one paste-ready
  invitation field with one Join action. Duplicate clicks are ignored while an
  operation is being submitted.
- An original, lightweight shared-signal graphic gives the launch screen a
  recognizable WebJam identity without delaying access, faking progress, or
  introducing motion that must be disabled.
- The normal path still starts the bundled server and music client
  automatically. Ports, process paths, recorder credentials, and routing
  internals remain outside the musician experience.

### Black, white, and burnt orange

- The entire Qt interface now uses a restrained near-black and white system
  with burnt orange (`#BF5700`) reserved for primary actions, focus, and
  meaningful emphasis. Purple, teal, neon glow, busy gradients, and the old
  color-coded control clutter are gone.
- Reusable tokens now govern surfaces, text hierarchy, borders, focus,
  semantic states, meters, buttons, inputs, dialogs, menus, tooltips, empty
  states, and recording surfaces. State meaning is always expressed in words
  or control labels, never by color alone.
- The live window adopts a familiar meeting hierarchy without copying Webex
  assets: a restrained header, a dominant musician stage, responsive tiles,
  one status surface, and one bottom control bar.

### Truthful live-session controls and recovery

- The bottom bar keeps only **Copy Invite**, **Record**, **More**, and the
  role-aware session action. A host sees **End Session** because it ends the jam
  for everyone; a bandmate sees **Leave Jam** because it disconnects only that
  Mac. **Ending…** and **Leaving…** remain visible until owned-process cleanup
  actually finishes.
- Connection recovery no longer treats a running process as proof of a live
  session. An interruption clears stale participant/audio truth, announces the
  recovery state, and returns to connected only after real local session
  evidence. A timed-out attempt presents one recovery action instead of
  competing Retry buttons.
- Invalid invitations, unavailable sessions, offline networks, microphone
  permission requirements and denials, recoverable failures, and fatal startup
  failures use plain-language states with a next action. Technical detail stays
  in logs or **More → Troubleshooting**.
- Ending a hosted jam and leaving a joined jam have distinct confirmations.
  Active recording is stopped and saved first; cleanup failure is reported
  instead of being replaced by a false success state.

### Responsive and accessible by construction

- The main session remains usable at 760×600. Participant tiles reflow from a
  focus tile to balanced multi-column layouts based on the actual viewport,
  and the four essential bottom controls remain visible in a narrow window.
- Keyboard order follows the task: title, participant mix controls, Copy
  Invite, Record, More, then End/Leave. Focus is visibly distinct, interactive
  targets are larger, controls have accessible names and descriptions, and
  changing connection/participant states are announced to assistive
  technology.
- Local mute is now described as **Mute Monitor** so it cannot be mistaken for
  muting the musician's outgoing audio. Permission and validation recovery do
  not rely on color.

### Multitrack Studio and Logic handoff

- **Recording is a musician-facing Studio, not a toolbar switch.** More →
  Multitrack Studio shows one lane per participant, a single Record action,
  live recording state, a take library, waveforms, transport/scrub, selectable
  stereo output, gain, pan, mute, and solo. Recording starts without pulling
  the host away from the simple live room.
- A hosted take keeps the server's isolated WAV for each musician and maps
  channel filenames to participant names plus the session title in the take
  manifest.
- **Export for Logic is aligned, atomic, and non-destructive.** It creates one
  numbered, musician-named 24-bit PCM WAV per track, padding or trimming every
  signed source offset onto a shared zero-based timeline. All stems have the
  same length, so they can be dragged into Logic together at `0:00` without
  manual offset math. A stereo rough mix reflects the current gain/pan/mute/
  solo state, while instructions and `webjam-logic-export.json` preserve the
  handoff evidence. Original recorder files are never modified and repeated
  exports never overwrite an earlier package. Unverified audio cannot be
  presented or exported as Logic-ready.
- **Recording Setup lives in Studio.** The first-run Host/Join experience stays
  focused, while the host can choose Studio's wired playback output and
  optionally capture interface inputs 1 and 2 as separate 24-bit/48 kHz stems.
  Explicit capture settings persist across host launches. Joining musicians
  cannot arm host-only local capture.
- Recording has explicit starting, recording, stopping, validating, complete,
  and needs-attention states. Partial recordings are preserved on attach or
  shutdown failure instead of being silently deleted.

### Test-night boundary

- v0.9.0 is a new private test artifact and must not overwrite or be confused
  with the earlier v0.8.2 ZIP. The exact packaged app still must pass the
  source, frozen-runtime, two-Mac audio, reconnect, multitrack, and cleanup
  gates in [`TEST_PROCEDURE.md`](TEST_PROCEDURE.md) and
  [`SUNDAY_TWO_MAC_PILOT.md`](SUNDAY_TWO_MAC_PILOT.md).
- The same-LAN invitation boundary remains intentional for tonight. Internet,
  VPN, NAT traversal, Windows, and Intel macOS are not part of the v0.9.0
  private-pilot claim.

---

## [0.8.2] — 2026-07-12 test-night candidate

### Host → Share → Join → Play

- **Every launch starts with two choices: Host a Jam or Join a Jam.** There is
  no setup wizard, Ready Check, server-address form, port picker, device-path
  field, or routing decision in the normal path.
- **Hosting is one click on the macOS test build.** WebJam selects safe
  defaults, starts its bundled dedicated server and background music client,
  and publishes the invitation only after the hosted service is actually
  alive.
- **Invitations are links, not network configuration.** Copy Invite produces a
  versioned `webjam://join?...` link containing only the host, port, and session
  name. A bandmate can open that link or paste it into the single Join field.
  Cold-start and already-running deep links use the same strict parser; malformed
  or ambiguous links, unsafe addresses, credentials, fragments, and unexpected
  parameters are rejected.
- **The session HUD says what WebJam knows.** It distinguishes starting,
  ready-to-share, connected, timed-out, and ended states. A local input meter
  means WebJam observed signal on this Mac; a remote meter means band audio was
  observed. Neither meter is presented as proof that the other musician heard
  the signal. A 30-second join timeout ends the unproductive attempt and offers
  one clear Try Again action.
- **End Session owns cleanup.** The host path stops and saves an active take,
  then stops the local client and the server WebJam started. A joined Mac stops
  its client. Shutdown follows the same ownership-aware order.

### Progressive session workspace

- The connected workspace keeps the invitation, readiness, participant cards,
  and **End Session** visible. Notes, Studio, optional video/conversation,
  Talk Break, Settings, and Troubleshooting live under one **More**
  menu instead of competing with the core path.
- Settings is now a small preferences dialog for the musician name and optional
  conversation link. It does not duplicate host/join or expose internal ports,
  secrets, executable paths, or recording folders.
- Webex/video is optional. When used, WebJam only launches the external
  conversation and reports that action; it does not claim meeting membership
  or control native Webex devices.

### Familiar meeting stage and packaged-runtime reliability

- The live session now follows the familiar Webex meeting hierarchy: a light
  header, dominant neutral stage, large automatically centered musician tiles,
  one compact readiness line, and a persistent bottom bar for Copy Invite,
  Record, More, and the red End Session action. One musician gets a large
  focus tile; two to six musicians form a balanced equal-view grid.
- Raw network links no longer occupy the live stage, and legacy hosted-server
  text can no longer become a stray top-level macOS window. Inter 4.1 remains
  bundled under the SIL Open Font License, with a platform-font fallback.
- The packaged app keeps private control secrets and multitrack takes in
  writable Application Support storage. Its official Jamulus client now runs
  headlessly, so musicians do not have to operate or dismiss a second audio
  application window.
- The bundled server and client were exercised together on isolated test ports:
  the server reported a real connected client, the client accepted a musician
  name over authenticated control, both stopped cleanly, and the test ports
  were released. Physical two-Mac audio, reconnect, and recording remain the
  release-candidate acceptance gate described in
  [`SUNDAY_TWO_MAC_PILOT.md`](SUNDAY_TWO_MAC_PILOT.md).
- The legacy UDP monitor is dormant in the product build. Enabling it made the
  Jamulus server count WebJam's monitor socket as another musician; the bundled
  3.12.2 client's authenticated interface already provides the authoritative
  roster, levels, mixer controls, chat, and mute state without that phantom
  connection.
- Local identity now follows the real Jamulus 3.12.2 control response. That
  response describes the local profile without returning a channel id, so
  WebJam reconciles it with the roster instead of mislabeling the host as a
  bandmate and timing out a healthy session. A remote-only roster can no
  longer produce a misleading “Bandmate connected” banner while this Mac is
  still reconnecting.
- The private macOS test artifact is ad-hoc signed and intentionally not
  notarized. The first launch may require Control-click → Open. The pilot is
  limited to two Macs on the same local network; it does not claim internet or
  NAT traversal.

### Recording integrity hardening

- Stem alignment offsets are now signed end-to-end. Local capture arms before
  the server recorder starts, so isolated stems normally *lead* the server
  take; the previous clamp forced those negative offsets to zero and every
  supplemental stem played late by the recorder-start latency. Take Deck now
  plays negative-offset stems sample-aligned and labels the trimmed lead-in.
- Alignment correlation uses an alias-free 100 Hz block-mean envelope plus a
  bounded full-rate refinement pass, replacing raw stride decimation. The
  reported confidence is the refined normalized correlation (≈1.0 for a
  genuine match, ≈0 for unrelated audio), making the 0.15 acceptance floor
  meaningful. Manifests record `alignment_method: envelope+refine-v2`.
- The supplemental-capture audio callback is real-time safe: it only copies
  blocks into a bounded queue and a dedicated writer thread does all disk
  writes. Device status flags and write errors are deduplicated, counted, and
  capped, so a sustained fault can no longer grow an unbounded error list into
  the manifest.
- Partial recordings are always preserved: a failed stem attach moves the
  audio to a visible `Recovered-local-…` folder instead of deleting it,
  attaching never overwrites an existing take file (collisions get a
  `-local` suffix), and quitting mid-recording salvages the capture into a
  `Recovered-…` take instead of discarding it. Capture hand-off between the
  validation worker, stop-failure handling, and shutdown is now atomic and
  idempotent.
- Take Deck reuses recorded manifest findings when reviewing a finished take
  instead of re-probing every WAV, and shows transient `validating` manifests
  as "Checking…" rather than "Unchecked".
- Ending a hosted session while recording now stops and saves the take before
  the client and owned server are shut down.
- Participant names and roles from the Jamulus roster render as plain text,
  so markup in a remote musician's name can no longer be interpreted as rich
  text in the mixer.
- Reconnect guidance now stays in the session HUD and offers one clear
  **Try Again** action after a timed-out attempt.

---

> Entries below v0.8.2 preserve earlier implementation history. References to
> Setup, Ready Check, raw endpoints, Start/Stop Audio, or a visible Jamulus
> window are not instructions for the current build.

## [0.8.0] — 2026-07-08

### Bundle Jamulus with downloadable builds (both platforms)

Removes the "leave WebJam, find jamulus.io, download, install, come back"
detour for most users. Both platforms bundle the same pinned Jamulus
version (`3.12.2` / tag `r3_12_2`) already used by the `integration-jamulus`
CI job, under GPL/AGPL "mere aggregation" terms — see the new
`THIRD_PARTY_NOTICES.md` for the full licensing rationale. Current macOS
packaging prepares and re-signs its nested copies ad hoc; it does not preserve a
notarized nested-app signature.

> Packaging note: 0.8.1 supersedes the original macOS signature-preservation
> approach below. The current test build prepares the same upstream app
> contents with ad-hoc, non-sandboxed signatures as documented above and in
> `THIRD_PARTY_NOTICES.md`.

- **macOS: zero-install.** The original 0.8.0 plan downloaded and
  checksum-verified the official Apple-signed/notarized
  `jamulus_3.12.2_mac.dmg`. Current private-candidate packaging extracts that
  release, prepares the nested client/server copies for WebJam's loopback-only
  orchestration, and re-signs them ad hoc. The nested copies and WebJam artifact
  are therefore not notarized. A fresh install still finds the pinned bundled
  client automatically with zero configuration.
- **Windows: bundled installer.** Jamulus only ships an NSIS installer on
  Windows (no portable binary), so CI downloads and checksum-verifies
  `jamulus_3.12.2_win.exe` and `webjam.spec`'s new `Jamulus/` datas block
  (mirroring the existing `VB/` block) ships it inside the WebJam install
  directory. The Setup Wizard's Jamulus page now shows an **"Install
  Jamulus now"** button when no install is found — it launches the bundled
  installer and polls (non-blocking, via `QTimer`) for completion, filling
  in the executable path automatically once it lands.
- Added `services.bridge_service._bundled_jamulus_candidate()` (macOS) and
  `_bundled_jamulus_installer()` (Windows) — both frozen-build-aware and
  no-ops in dev checkouts. `find_jamulus()` now falls back to the bundled
  macOS candidate as a last resort after all configured/default candidates
  are exhausted.
- The manual override (Browse button, `WEBJAM_JAMULUS_CANDIDATES` env var)
  is unchanged and remains the escape hatch for anyone who needs a
  different Jamulus install than the bundled one.
- Added `licenses/JAMULUS_COPYING.txt` (the exact GPL text from the pinned
  Jamulus release tag) and `THIRD_PARTY_NOTICES.md`; CI places a copy
  alongside the bundled Jamulus in every build (macOS:
  `WebJam.app/Contents/Resources/THIRD_PARTY_LICENSES/`; Windows:
  `Jamulus/` next to the installer).
- Updated the Setup Wizard's Welcome-page notice (no longer an "install
  this yourself" warning) and the Jamulus page (pre-fills + notes the
  bundled macOS copy; shows the install button on Windows).
- Updated README, README_SIMPLE, DEVELOPMENT, ARCHITECTURE, USER_GUIDE,
  FIRST_JAM, COHORT_VALIDATION_PLAYBOOK, TEST_PROCEDURE, and
  VISION_AND_ROADMAP to reflect per-platform bundling instead of a blanket
  "install Jamulus separately" requirement (still true for source
  checkouts, which don't go through the PyInstaller bundling step).
- Added `TestBundledJamulusCandidate`, `TestBundledJamulusInstaller`, and
  `TestJamulusPageBundling` test suites (28 + new wizard cases) covering
  frozen/non-frozen and platform-gating branches, the `find_jamulus()`
  fallback, and the install-button launch/poll/failure paths. Full suite:
  823 tests passing (0 regressions).
- Known trade-off, not blocking: bundling ties the shipped Jamulus version
  to WebJam's own release cadence — see `THIRD_PARTY_NOTICES.md`'s
  "Staying current" note.

---

## [0.7.3] — 2026-07-08

### Test isolation fix and doc cleanup

- Fixed a test-isolation bug in
  `tests/test_application_controller_demo_to_real_transition.py`: the
  audio "stopping" latch set by `AudioCoordinator.stop()` wasn't reset in
  `setUp()`, so a prior test's stop() could leak into the next test and
  make `apply_participants()` silently no-op.
- Fixed `DEVELOPMENT.md`'s "Adding a Jamulus JSON-RPC method call"
  tutorial, which still described the pre-rewrite RPC client (separate
  poll/SSE threads, a synchronous `_call()` helper) and referenced a
  nonexistent `GAIN_RANGE_MAX` attribute in its example code. Rewritten
  to match the current single-thread NDJSON reader and fire-and-forget
  `_send()`.

---

## [0.7.2] — 2026-07-06

### Pilot readiness hardening

- Added a session-health snapshot so the Conductor distinguishes a launched
  Jamulus process from proven RPC/participant/meter truth.
- Made Ready Check visible in the session strip and run it automatically after
  first-run setup completes.
- Hardened first-run setup: Jamulus executable presence is required, Webex
  links must be HTTPS `webex.com`, and setup completion copy no longer implies
  the rig is jam-ready before Ready Check passes.
- Made `Mute Me` truthful: it only changes local UI state after Jamulus RPC
  accepts `setMuted`, and reverts on failure.
- Tightened recorder status parsing, Webex permissions/token injection, log and
  diagnostics redaction, Companion API opt-in behavior, and Jamulus RPC secret
  fail-closed launch.
- CI desktop builds now wait for the real-Jamulus integration job.
- Restored an Intel Mac release artifact (`WebJam-macos-x64.zip`) using
  GitHub's current `macos-15-intel` hosted runner.

---

## [0.7.1] — 2026-07-05

### Deep code + logic review — hardening pass

A four-reviewer deep audit of the audio engine, RPC layer, and controller
state machines. Confirmed the security model is sound (0o600 secrets, no
command injection, loopback-only RPC + SSH tunnel, Host-header guard). Fixes:

- **Take Deck plays at the take's real samplerate.** A 44.1 kHz take no
  longer plays pitch-shifted / misaligned through a fixed 48 kHz device.
  Replaying a finished take rewinds instead of sitting silent, and finishing
  a take now releases the audio stream + file handles.
- **RPC framing is stall-proof.** Both the Record-button transport and the
  live client now frame NDJSON from raw sockets, so a response split across a
  network stall no longer hard-fails a call or drops notifications.
- **No zombie RPC reader** after a fast Stop Audio → Launch Audio; sends are
  serialised; channel meters map by channel id, not list position.
- **Record button polls** the server recorder until it actually arms/disarms
  (Jamulus does it asynchronously), and resets on Stop Audio.
- **Reconnect** shows a clear "couldn't reconnect after 5 tries" instead of
  hanging on "Reconnecting…" forever.
- **Practice mode** cleans up its private server if the client launch fails,
  and never freezes the UI during teardown.
- Webex button can't get stuck lying "Leave Video"; shutdown is re-entrant;
  companion-API reads are race-safe; diagnostics redaction is future-proofed.
- 12 regression tests added; suite at 754.

---

## [0.7.0] — 2026-07-05

### The Take Deck — play back and mix your jams, in-app

- **Take Deck (side-rail "Takes")** — the recordings the ● Record button
  captures are now reviewable *inside WebJam*: pick a take, hit play, and
  mix it with the very same console the live session uses (per-track
  faders, mute, solo, live meters, scrub). Musicians who connect mid-jam
  line up correctly — track start offsets are read from the take's
  Audacity `.lof`. This is the first half of the "Demo Deck": review now,
  overdub next.
- **Multitrack playback engine** (`core/take_player.py`) — streaming
  per-track mixing on a numpy bus with gain/mute/solo/offsets and a
  transport, behind a sink abstraction so the whole engine is unit-tested
  headless (no audio hardware in CI).
- **Take library** (`core/take_library.py`) — discovers take folders and
  parses `.lof` offsets; robust to missing/garbled metadata.
- **Review-only, on purpose** — no editing/plugins here; every take keeps
  its Reaper-project escape hatch for the DAW.
- New dependency: `soundfile`. New setting: `takes_directory`. Suite +34.

---

## [0.6.0] — 2026-07-05

### The Record Button

- **● Record in the Conductor** — one press arms the band server's
  multitrack recorder; one press stops it. Every musician gets their own
  track and every take lands as a ready-to-open Reaper project on the
  server. The whole band sees the red ● REC chip while tape rolls.
- **Band-server RPC transport** (`core/jamulus_server_rpc.py`) — reaches
  the server's loopback-only JSON-RPC through an SSH tunnel; new settings
  `server_rpc_port` (default 22240) and `server_rpc_secret_file` (a local
  copy of the server's jsonrpc.secret). Unconfigured? The button tells you
  exactly how to set it up.
- **Machine-verified against real Jamulus** — the Record cycle (arm →
  new-take → stop), roster query, and wrong-secret rejection all run
  against the shipping jamulus-headless binary in CI on every push.
- Suite at 719 (+16 unit, +3 real-binary integration).

---

## [0.5.0] — 2026-07-04

### The "make it amazing" release — practice mode, recording awareness, band server

- **Practice mode (Ctrl+P / Practice button)** — WebJam starts a private
  Jamulus server on your own machine and connects to it: hear yourself,
  watch your meter, test the mixer — zero internet, zero band-server
  dependency. Works on a fresh unconfigured install. Stop Audio tears the
  local server down with the client.
- **● REC indicator** — when the band server's multitrack recorder is
  rolling, every member sees a red ● REC chip in the status bar (wired to
  Jamulus `recorderState` notifications).
- **Stage cards v2** — cards now show each musician's skill level from
  their Jamulus profile alongside the instrument ("Bass · Intermediate").
- **Band server recipe (`server/`)** — one `docker compose up -d` gives the
  band a private server with multitrack recording armed: every take is one
  WAV per musician plus a ready-to-open Reaper project. JSON-RPC stays on
  loopback (SSH-tunnel only) — the foundation for the upcoming Record
  button.
- **Vision** — see VISION_AND_ROADMAP.md for the roadmap this release starts
  (Session Record concept, server browser, Webex intelligence).

- **Fresh installs start unconfigured** — the dead default Jamulus server
  (a private LAN IP) and sandbox Webex link are gone. The wizard requires
  real values; Launch Audio without a server now shows an actionable error
  instead of spawning `Jamulus --connect :22124`; the empty default no
  longer crashes the app at startup.
- **FIRST_JAM.md** — staged runbook for the band's first session (solo
  smoke test → two-person → full band) with a failure playbook.
- **Download & security-warning docs** — README_SIMPLE now covers grabbing
  release zips and getting past Gatekeeper/SmartScreen (builds are unsigned).
- **Legacy Tkinter app quarantined** — `webjam_app*.py`, the Tkinter `ui/`
  modules, `admin/`, `session_templates`, old installer scripts, and their
  tests moved to `legacy/` (see `legacy/README.md`). CI no longer needs
  tkinter to collect the active suite; `ui/services.py` (live MetricsService)
  stays. Active suite: 674 tests, zero collection errors.

---

## [0.4.10] — 2026-07-04

### First shippable v0.4.x build — release pipeline unblocked

- **CI: release pipeline fixed** — every tag run since v0.4.5 was killed at
  the 24h wall because the build matrix still listed `macos-13` (Intel), a
  runner type GitHub has retired; the release job never fired. The Intel
  entry is removed (Intel Macs: run from source) and jobs now carry real
  timeouts. This is the first v0.4.x tag whose build can actually publish.
- **Fix: routing-scan shutdown race** — the background audio-routing scan no
  longer dies with a `RuntimeError` traceback if the app shuts down while the
  scan is in flight (the status is quietly dropped instead).
- **Tests** — live-session engine coverage push: `application_controller`
  69%→86%, `jamulus_controller` 63%→88%, `bridge_service` →91%. New suites for
  the Join/Leave Video flow, Webex state machine, token refresh, Launch/Stop
  Audio toggle, crash-reconnect banner, settings wizard round-trip,
  diagnostics export, JamulusController lifecycle, and BridgeService launch
  failure paths + Jamulus command-line contract. Suite at 720.

---

## [0.4.9] — 2026-06-29

### Live-session features + build correctness

- **In-session chat both ways** — a chat box in the session canvas sends to the
  band (`jamulusclient/sendChatText`) and echoes locally; incoming chat appends
  to the shared canvas.
- **Name sync** — on connect, WebJam pushes your display name to Jamulus
  (`jamulusclient/setName`) so bandmates see a real name, not a blank.
- **Ready Check (F2)** — `core/preflight.py` reports what's missing before you
  jam (Jamulus installed, server/port set, virtual audio cable detected, Webex
  link), surfaced via an F2 shortcut + F1 help.
- **Build correctness** — macOS bundle version now tracks `__version__` (was
  pinned to 0.3.0); Windows builds bundle the VB-CABLE installers; added
  `api.local_bridge` / `core.file_io` to PyInstaller hiddenimports.
- **Tests** — suite at 620 (fake-Jamulus TCP server, preflight, chat send,
  build data-file guards). `__version__` → 0.4.9.

---

## [0.4.8] — 2026-06-29

### Real-world hardening, correct Jamulus control, and onboarding

The headline: WebJam's Jamulus control was rebuilt against the **actual** current
Jamulus JSON-RPC API, plus a multi-round audit fixed real bugs and the CI/release
pipeline. First release intended for live band use.

#### Jamulus integration (correctness)
- **Rebuilt the JSON-RPC client against shipping Jamulus (3.9–3.12).** The old
  client spoke an experimental HTTP+SSE fork (`jamulus/getChannelClients`,
  gain 0–10000) that never matched released Jamulus. It now uses
  newline-delimited JSON-RPC over **TCP**, the `jamulus/apiAuth` handshake
  (`--jsonrpcsecretfile`, generated at launch), and the real `jamulusclient/*`
  methods (`getClientList`, `setFaderLevel` 0–100, `setMuted`) and notifications
  (`clientListReceived`, `channelLevelListReceived` 0–9, `connected`/`disconnected`).
- **Real "Mute Me"** via `jamulusclient/setMuted` — previously it zeroed your own
  fader, which only muted you in your *own* monitor; the band still heard you.
- **In-session chat** — incoming Jamulus chat (`chatTextReceived`) is appended to
  the shared session canvas; `sendChatText` is wired.

#### Reliability / security fixes (from the audit rounds)
- RPC heartbeat no longer false-fires "Jamulus stopped responding" after a restart.
- Mix auto-save safety net no longer disarmed by a failed save.
- Background audio-routing scan no longer dies silently when PortAudio is missing.
- Companion API: added a loopback-only `Host`-header check (DNS-rebinding defense),
  redacted `sentry_dsn`, and **actually wired it into the app** (it was documented
  as auto-starting but never instantiated).
- Python 3.10 compatibility fix; unknown-msg-id log-flood cap; assorted Lows.

#### Pipeline / docs
- **CI no longer cancels branch/tag runs**, so `master` can go green and produce builds.
- **`README_SIMPLE.md` rewritten** as an accurate band onboarding guide for the Qt app.
- **`WEBJAM_NEXT_LEVEL.md`** added: engine evaluation (stay on Jamulus; SonoBus/JackTrip considered) + roadmap.

#### Tests
- Suite expanded to **600+** (incl. a fake-Jamulus TCP server verifying the real
  wire protocol). `__version__` → 0.4.8.

---

## [0.4.7] — 2026-04-24

### Round 4 deep-dive — controller refactor, telemetry expansion, multi-mix, audio device picker

6 parallel implementation agents in isolated worktrees, plus follow-up wiring and a user-journey audit.

#### Refactor
- **`ParticipantStateManager` extracted** from `JamulusController` (new `jamulus_state_manager.py`, 349 LOC).  Owns `participants`, `_pre_solo_mute`, and `_participants_lock` plus all mutator helpers (`set_fader_level`, `set_mute`, `set_solo`, `serialize_mix`, `apply_mix_data`, `sync_from_protocol`).  `JamulusController` shrinks 803 → 545 LOC and now delegates; backward-compat properties on the controller keep older test fixtures working.
- **`unregister_callback()`** added to `JamulusController`; `stop()` warns if monitor thread didn't exit, then clears the callbacks list to drop dangling references.

#### New features
- **Multi-mix save/load** — `Ctrl+Shift+S` ("Save Mix As…") and `Ctrl+Shift+O` ("Load Mix From…") open `QFileDialog`s so users can keep one mix per song / per band-mate.  New `MixManager.save_to(path)` / `load_from(path)` paired methods.
- **Audio input device picker** in the wizard's Routing page (`AppSettings.audio_input_device_index`).  `core/audio_engine.py::_resolve_device` now prefers an explicit setting over auto-detect, so users with multiple interfaces can pin the right one.

#### Telemetry expansion (7 new metrics)
- `metric_jamulus_hang_detected` — incremented when the RPC heartbeat first crosses the >15s silence threshold.
- `metric_webex_token_refresh_attempt` / `_success` — wired through `WebexEmbed.on_refresh_metric` callback.
- `metric_audio_device_blackhole_found` / `_audio_device_missing` — emitted from the routing-status apply path so we know how often the bundled BlackHole route succeeds.
- `metric_mix_corruption_recovered` — incremented on `JSONDecodeError` in `MixManager.load`.
- `metric_session_started` — first-time-this-session participant arrival, paired with a "Connected to {server}. Waiting for band members…" flash.

#### Memory + concurrency hardening
- **`_unknown_msg_ids_seen` capped** at 256 entries in `core/jamulus_protocol.py` so unknown-message logging can't grow without bound on a misconfigured server.
- **`_request_counter` reset** in `JamulusRpcClient.stop()` — prevents wraparound state leaking across reconnects.
- **47 new tests** across 7 files covering the state-manager extraction, multi-mix round-trip, telemetry expansion, audio device picker validation, and concurrency stress (RPC client + JamulusController under daemon-thread Barrier/Event harness).

#### User-journey polish
- **Jamulus install warning relocated** from the Done page (page 4) to the Welcome page (page 1) of the setup wizard, with an amber notice box — users now discover the prerequisite before configuring anything.

#### Versioning
- `__version__` 0.4.6 → 0.4.7.  Suite total: **647 pass, 12 skipped** (was 611; +36 net; 0 failures).

---

## [0.4.6] — 2026-04-25

### Round 3 deep-dive — refactors, new shortcuts, audit fixes

10 parallel agents (6 implementation, 4 investigative) plus follow-up fixes.

#### New features
- **Ctrl+Shift+R — Reset all faders to 0 dB** (`application_controller.py::_on_reset_all_faders`).  Confirmation dialog; saved mix on disk untouched (Ctrl+O still restores).
- **Ctrl+Shift+D — Copy diagnostics summary** (`webjam_qt/controllers/diagnostics.py`).  New 129-LOC `DiagnosticsExporter` builds a Markdown summary (versions, service state, server config, log paths, last 30 lines of `~/.webjam.log`, sanitised settings — `webex_guest_issuer_secret` redacted) and pastes to clipboard.
- **Auto-save mix on shutdown** when the user touched the mix and Jamulus was connected. `_mix_dirty` flag flips True on any fader/mute/solo change, False after explicit save. Shutdown auto-saves so mid-session tweaks survive even if the user forgets Ctrl+S.

#### Wizard polish
- **Live validation hints** in the Jamulus and Webex pages.  Type-as-you-go feedback ("Host shouldn't contain spaces", "Will auto-prepend https://", "URL needs a domain"), no Next-button bouncing.

#### Refactor
- **`MixManager` extracted** from `ApplicationController` (`webjam_qt/controllers/mix_manager.py`, 124 LOC).  Owns `~/.webjam_mix.json` save/load/auto-restore. `_on_save_mix`/`_on_load_mix`/`_restore_saved_mix` retained as thin delegates.

#### State machine + correctness
- **`JamulusState` str-enum** in `services/bridge_service.py` (8 raw string assignments converted).  `_set_jamulus_state` writes under `_reconnect_lock`; `jamulus_process` writes likewise locked.  Inheritance from `str` keeps existing equality checks working transparently.
- **Memory leak: signal disconnect** in `ParticipantGrid._remove_card`.  Without this, `card.fader_changed.connect(self.fader_changed)` connections from `_add_card` survived `deleteLater()` and accumulated over join/leave churn.
- **Missing METRIC_KEYS added** (`ui/services.py`): `metric_jamulus_stop`, `metric_jamulus_port_conflict`, `metric_webex_leave`, `metric_session_completed` were incremented in code but absent from the canonical list.

#### macOS shortcut consistency
- **Ctrl+Shift+R / Ctrl+Shift+D bind to literal Control on macOS** (Qt.MetaModifier), matching the existing macOS-safe pattern used for Ctrl+M / Ctrl+Shift+M.  Avoids any potential Cmd+key system conflicts.

#### Tests
- **46 new tests** across 11 new files — port conflict detection, log capture, UDP protocol robustness, RPC hang banner, atomic notes export, MixManager round-trip, mix-dirty auto-save, diagnostics summary, wizard live validation.
- Suite total: **611 pass, 12 skipped** (was 565; +46 net; 0 failures).

#### Versioning
- `__version__` 0.4.5 → 0.4.6, surfaced in title bar and F1 help.

---

## [0.4.5] — 2026-04-25

### Deep-dive pass — data integrity, accessibility, performance, robustness

Synthesised from 17 parallel investigative + implementation agents across
two rounds covering architecture, performance, tests, real-world failures,
accessibility, integrations, persistence, docs, state machines, network
protocol robustness, cross-platform pitfalls, and error UX.

#### Data integrity
- **Atomic writes** for all persistent JSON/text via new `core/file_io.py::atomic_write_text` (temp file + fsync + `os.replace`).  Five call sites converted: setup wizard config, mix file, session notes, session metadata, canvas notes export.  8 new tests in `tests/test_file_io.py`.
- **Config file mode `0o600`** for `~/.webjam_config.json` (which can hold the `webex_guest_issuer_secret`).  Was world-readable.

#### Reliability + leak fixes
- **Subprocess log file leak fixed** in `bridge_service.launch_jamulus` — new `_close_jamulus_log_file()` helper called on shutdown-mid-launch and exception paths; idempotent.
- **State-machine bug**: `jamulus_reconnect_inflight` now cleared on the manual-launch failure paths (Not Found, Port In Use), so subsequent reconnect ticks aren't stuck on a stale True flag.
- **Bounded `_levels` dict** in `RealAudioEngine` (cap 1024 entries via LRU-trim); new `clear_level_overrides()` called from `JamulusController.stop()` so stale per-channel meter data doesn't leak between sessions.
- **RPC heartbeat** detects hung Jamulus (process alive but RPC silent for >15s).  Surfaces "Jamulus stopped responding" banner; auto-clears when activity resumes.

#### Real-world failure handling
- **Port conflict detection** before launching Jamulus.  Bind-tests `127.0.0.1:RPC_PORT`; if in use, shows actionable error pointing at `WEBJAM_JAMULUS_RPC_PORT` env var instead of silently leaving an uncontrollable Jamulus running.
- **Mix save/load specificity**: distinguishes OSError ("Permission denied. Check folder permissions and disk space"), JSONDecodeError ("Mix file is corrupted. Save a fresh one with Ctrl+S"), and generic exceptions.  All three flash for 6s and log full traceback.

#### UDP protocol hardening
- **`_parse_level_list`** capped at 500 entries (was unbounded — a hostile/malformed `CLT_CHANNEL_LEVEL_LIST` could allocate tens of thousands of dict entries).
- **Unknown msg_id logs deduped** — each unknown msg_id is logged once per session, preventing log floods from packet storms.

#### Cross-platform fixes
- **Windows `CREATE_NO_WINDOW`** in `subprocess.Popen` so the launched Jamulus doesn't pop up a spurious console alongside its GUI.
- **macOS Cmd+M conflict resolved** — Ctrl+M / Ctrl+Shift+M now bind to literal Control on macOS (via `Qt.MetaModifier`) so they don't collide with Cmd+M = system minimize.  Other platforms unchanged.  F1 help and shortcut labels reflect this.
- **Font fallback chain reordered** — Inter is not bundled, so `-apple-system, 'Segoe UI', 'Helvetica Neue', Helvetica, Arial, Inter, sans-serif` resolves correctly per platform.

#### Accessibility
- **`TEXT_MUTED` `#5F6B85 → #7A8AA0`** (was 2.93:1 contrast on BG_CARD — WCAG AA fail).  `TEXT_SECONDARY` bumped for safety margin.
- **Fader keyboard step**: `setSingleStep(5)` / `setPageStep(15)` (was default 1, made keyboard nav unusable).
- **Participant-context accessible names**: "Volume fader for Alice (decibels)", "Mute Alice", "Solo Alice".  Fader's accessible description includes the current dB value and updates on each change.
- **Side-rail focus border** 1px → 2px for visible keyboard navigation.

#### Performance
- **Single global LevelMeter timer** (was N per-card).  20 participants: 500 events/sec → 25/sec (-95%).  `level_meter.py::external_tick` flag, `participant_grid.tick_all_meters()`, driven by ApplicationController's `_meter_tick_timer`.

#### Webex integration
- **Token refresh on TTL approach** — 5-min safety margin before 1-hour expiry, polled every 60s.  Long rehearsals no longer silently lose Webex auth.
- **`mute_webex_self()` JS bridge** — Mute Me / Ctrl+Shift+M now silences the user in BOTH Jamulus AND Webex (was Jamulus-only).
- **Auto-restore placeholder** when Webex URL fails to load (404/DNS/blocked) — emits `error` state, restores placeholder, shows hint pointing at "Open video call in browser" fallback.

#### Architecture refactor
- **`SessionPersistence` extracted** from `ApplicationController` — `webjam_qt/controllers/session_persistence.py` (111 lines) owns notes + title + mode I/O.  Public methods on ApplicationController retained as thin delegates so existing tests pass unchanged.

#### Developer experience
- **`DEVELOPMENT.md`** +191 lines: 3 contributor tutorials (add a `ParticipantPresentation` field, add a Jamulus JSON-RPC method, wire a new keyboard shortcut) + sections on running tests / ruff / smoke-gate locally.
- **`.github/ISSUE_TEMPLATE/`** — `bug_report.yaml` + `feature_request.yaml` + `config.yml` with structured fields for OS/version/log excerpts.
- **Public docstrings** on `JamulusController.set_fader_level / set_mute`, `BridgeService.launch_jamulus / attempt_auto_reconnects`.
- **Friendly Python version error** in `webjam_qt_main.py` instead of cryptic `SyntaxError` on Python 3.9.
- **Wizard hints at `directory.jamulus.io`** for users without a server.

#### Tests
- **30 new tests** across these new files: `test_file_io`, `test_jamulus_rpc_fallback`, `test_jamulus_concurrent_mixer`, `test_webex_embed_lifecycle`, `test_bridge_reconnect_max_attempts`, `test_repository_mix_migration`, `test_application_controller_demo_to_real_transition`, `test_application_controller_signal_wiring`, `test_settings_corruption_recovery`, `test_audio_engine_levels_bound`, `test_session_persistence`, `test_level_meter_external_tick`, `test_webex_token_refresh`, `test_rpc_heartbeat`.
- **Suite total: 565 pass, 12 skipped** (was 523 at v0.4.4 release; +42 net).

#### Versioning
- **`__version__` → 0.4.5**, surfaced in title bar and F1 help.

---

## [0.4.4] — 2026-04-24

### Fixed — Session-control completeness

#### Toggle launch/stop and join/leave
- **Stop Audio button** (`services/bridge_service.py::stop_jamulus`, `webjam_qt/controllers/application_controller.py::_on_launch_audio`): The "Launch Audio" button now toggles. After Jamulus is running, clicking it prompts to stop; Yes terminates the subprocess (graceful terminate, force-kill at 2s) and stops the RPC/UDP monitoring threads. The auto-reconnect intent is cleared so the next reconnect tick doesn't immediately relaunch. Without this the conductor had to kill the app to end a session.
- **Leave Video button** (`services/bridge_service.py::leave_webex`, `application_controller.py::_on_join_video`): Same toggle treatment for the video button. `WebexEmbed.leave_meeting()` already existed but was never called from the UI; now it is. Bridge state is reset to "Not opened" and reconnect intent cleared.
- **Button labels reflect state**: `_refresh_readiness` now shows "Stop Audio" / "Leave Video" when active, and `_on_webex_state` shows the action-oriented "Leave Video" on the button while keeping the descriptive label ("In Meeting", "Lobby") in the status bar.
- **5 new tests** in `tests/test_reconnect_manager_edge.py` cover the new stop/leave paths: graceful termination, force-kill on timeout, clearing reconnect intent, monitoring stopped, leave_webex state reset.

#### Crash recovery is now visible
- **Reconnect banner** (`application_controller.py::_on_reconnect_tick`): When `jamulus_process.poll() is not None` is detected mid-session (Jamulus crashed), a flash message appears: "Jamulus disconnected — auto-reconnecting (attempt N/5)…". When the connection recovers, "Jamulus reconnected." flashes once. Previously the auto-reconnect machinery was completely silent.

#### App-close cleanup
- **Jamulus subprocess no longer survives app close** (`application_controller.py::shutdown`): The previous shutdown only stopped `JamulusController` monitoring threads — the Jamulus subprocess kept running and the user had to manually quit it. Shutdown now calls `bridge.stop_jamulus()` which terminates the subprocess too.

#### Discoverability
- **Tooltips on Launch Audio / Join Video** (`webjam_qt/widgets/session_strip.py`): Each button now hovers with a one-sentence explanation including the toggle behavior and how to access settings.
- **Log file path in error dialogs** (`application_controller.py::_show_actionable_error`): The actionable-error dialog now appends `For details, see the log file: ~/.webjam.log` so users know where to look when something goes wrong.
- **jamulus.io link in "Jamulus Not Found"** (`bridge_service.py::launch_jamulus`): The next-action text now points new users directly at https://jamulus.io to download Jamulus before falling back to the custom-location instructions.
- **F1 in-app help dialog** (`webjam_qt/windows/conductor_window.py`): F1 now opens a small dialog listing every keyboard shortcut, the colour-coded launch-button semantics, and a 4-step getting-started flow. Useful when users forget shortcuts mid-rehearsal without leaving the app to consult the README.

#### Mid-session settings changes are now context-aware
- **Targeted "leave/relaunch to apply" hints** (`application_controller.py::_open_settings_wizard`): The wizard used to flash a generic "take effect on next Launch Audio / Join Video" message after every save. It now snapshots `webex_url` + `jamulus_server` before the wizard, compares after, and shows specific actions if needed: "Leave Video and re-join to apply the new Webex URL" and/or "Stop Audio and re-launch to connect to the new Jamulus server".

#### Audit-found bugfixes
- **Reconnect-banner latch** (`application_controller.py::_stop_audio`): The `_reconnect_banner_shown` flag was set True on Jamulus crash and reset only when state went back to "Running". If the user clicked "Stop Audio" during reconnect attempts, the latch stayed True and future crash banners were silent. Cleared in `_stop_audio` so subsequent crashes flash again.

#### Tests
- **`tests/test_application_controller_toggle.py`** — 15 tests for `_is_jamulus_running`, `_is_video_active` predicates, button-label transitions, server:port in status bar, and self-mute behaviour.
- **`tests/test_reconnect_manager_edge.py`** — 8 new tests for `stop_jamulus` (terminate, force-kill, idempotency, dead-process), `leave_webex` (state reset, swallow controller errors).
- **`tests/test_qt_setup_wizard.py`** — 3 new tests for forgiving Webex URL validation (auto-prepend, scheme-prefixed bare-word rejection) and skip_welcome.
- **`tests/test_application_controller_toggle.py`** also covers: alone-on-server status, multi-participant counting, muted-card Qt property, session metadata round-trip.
- Suite total: **523 pass, 12 skipped**.

#### More live-session quality-of-life
- **'Mute Me' button + Ctrl+Shift+M** (`webjam_qt/widgets/session_strip.py`, `application_controller.py::_on_mute_self`): A new ghost button between the mode picker and audio button toggles mute on the local user's channel, with a Ctrl+Shift+M keyboard shortcut. Useful when the conductor needs to silence themselves quickly (answering a phone, talking off-mic) without finding their card in the grid. The button syncs in both directions with the local-user card's MUTE button.
- **Restore demos after Stop Audio** (`application_controller.py::_reset_to_demo_state`): When the user clicks Stop Audio, the (now-stale) real-participant cards are replaced with the demo placeholders and the demo-level animation restarts. The status-bar latency label resets to "Not connected". Gives a clear visual signal that audio is off.
- **Forgiving Webex URL validation** (`webjam_qt/windows/setup_wizard.py::_WebexPage.validatePage`): If the user types `org.webex.com/meet/foo` without a scheme, the wizard auto-prepends `https://` rather than silently refusing to advance. Bare words like "not-a-url" still fail (the auto-prepend only triggers on inputs containing a dot before any slash, and a final netloc-dot check rejects scheme-prefixed bare words too).

#### Layout density + session persistence
- **Per-card video tile shrunk to 6px accent bar** (`participant_card.py`, `conductor.qss`): The 'Video arrives when Webex is connected' placeholder used to occupy 120px+ of vertical space on every card, even though per-channel video isn't implemented (Webex video shows in the embedded view at the bottom of the stage). The tile is now a fixed-height 6px accent bar in brand colours (teal for remote, gold for local user). Card minimum height drops from 220px to 150px, fitting roughly 40% more participants on screen.
- **In-session Settings skips Welcome page** (`webjam_qt/windows/setup_wizard.py`): `SetupWizard` accepts a new `skip_welcome=True` keyword arg. When the user reopens Settings via Ctrl+, mid-session, the wizard now starts at the Jamulus page (skipping the welcome) and the title becomes 'WebJam Settings'. First-run flow is unchanged.
- **Session title persists across launches** (`application_controller.py::_load_session_title` / `_save_session_title`): The session title (e.g. 'Tuesday Practice') was lost on every close and reset to 'Band Rehearsal' on next launch. Now persisted to `~/.webjam_session.json` on title change and on shutdown; restored on startup.

#### At-a-glance state visualization
- **Muted participant cards fade visually** (`participant_card.py`, `conductor.qss`): Previously only the MUTE button changed colour when a channel was muted. The card itself now sets a `muted="true"` Qt property when muted, and QSS dims the background to BG_INPUT and the name/role text to TEXT_MUTED — making it easy to scan a busy stage and see who's silent.
- **Friendlier 'alone on server' status** (`application_controller.py::_apply_jamulus_participants`): When the user is the only channel on the server, the Session label now shows "1 participant · waiting for others" instead of the cold "1 participant". 2+ participants show "{N} participants" as before.
- **Last blocking 'Already running' dialog removed** (`services/bridge_service.py::launch_jamulus`): Re-clicking Launch Audio while Jamulus is already running used to throw a modal QMessageBox.information; now flashes a non-blocking status banner.

#### Webex embed resilience
- **Auto-restore placeholder when Webex URL fails to load** (`webjam_qt/widgets/webex_embed.py::_on_view_load_finished`, `application_controller.py::_on_webex_state`): When `QWebEngineView.loadFinished(ok=False)` fires (404, DNS, blocked, network), the embed emits a new "error" state. The controller restores the placeholder, resets the button to "Join Video", and flashes a hint pointing at the 'Open video call in browser' fallback button. Skips false positives from about:blank/data: navigations.

#### Troubleshooting infrastructure
- **Jamulus stdout/stderr captured to `~/.webjam_jamulus.log`** (`services/bridge_service.py::launch_jamulus`): Used to be discarded via `subprocess.DEVNULL`. Now line-buffered, overwritten per launch, closed on `stop_jamulus`. Falls back to DEVNULL if the file can't be opened.
- **Both log paths surfaced in error dialogs** (`application_controller.py::_show_actionable_error`): Lists `~/.webjam.log` (always) and `~/.webjam_jamulus.log` (only when it exists, to avoid confusion in 'Not Found' errors).
- **F1 help dialog mentions log paths** so users can find them without triggering an error first.

#### Versioning + onboarding
- **Bumped `__version__` 0.1.0 → 0.4.4** in `webjam_qt/__init__.py` (was stale across 4 minor releases).
- **Version surfaced in window title** (`WebJam — Conductor (v0.4.4)`) and **F1 help dialog header** (`WebJam — Conductor UI v0.4.4`).
- **Wizard now hints at directory.jamulus.io** for users who don't yet have a Jamulus server.
- **Friendly Python version error** in `webjam_qt_main.py` instead of cryptic `SyntaxError` on Python 3.9.
- **Red 'Unmute Me' button** when self is muted — `QPushButton#GhostButton:checked` paints in danger red (was visually identical to unmuted state).
- **Session mode persists** alongside title in `~/.webjam_session.json`. Bands using the same mode no longer need to re-select it on every launch.

---

## [0.4.3] — 2026-04-24

### Fixed — Critical mixer reliability + 4 UX improvements

#### Critical: mixer commands no longer silently dropped
- **`_check_participants` bypassed when RPC is active** (`jamulus_controller.py`): The UDP monitor loop ran every second and used the protocol adapter's cached participant list, which is always empty when UDP is disabled. This wiped `JamulusController.participants` each second, causing fader/mute/solo commands to hit an empty dict and be silently dropped between 5-second RPC poll cycles. Added an early-return guard matching the existing guard in `_on_udp_participants`.

#### UX
- **Audio button is now gold, video button is teal** (`session_strip.py`, `conductor.qss`): Both action buttons previously used the same teal "PrimaryButton" style. The audio button now uses the `AudioButton` objectName, rendering gold — visually distinguishing "Launch Audio" from "Join Video" at a glance. The `AudioButton` QSS rule is extended with a full set of states (border, padding, focus, pressed, disabled) since QSS has no inheritance within selectors.
- **Embedded Webex join keeps "Video Active" label** (`application_controller.py`): `_refresh_readiness` checked for the bridge-state string `"Opened in browser"` only. After an embedded `QWebEngineView` join, the bridge state becomes `"In Meeting"`, `"Joining…"`, etc. The reconnect timer would then reset the video button to `"Join Video"`. The check now uses a frozen set of all active states.
- **SideRail selection restored after modal actions** (`side_rail.py`, `application_controller.py`): Clicking "Chat", "Roles", or "Settings" in the side rail used to leave that item checked even though the view didn't change, making the nav rail misleading. The controller now tracks the last active content key and restores the rail selection after any modal/placeholder action. `SideRail` gains `current_key()` and `set_active_key(key)` helpers.
- **Setup wizard routing scan uses Signal, not `QMetaObject.invokeMethod`** (`setup_wizard.py`): The background routing scan used `QMetaObject.invokeMethod(self, "_apply_routing", QueuedConnection)` to marshal back to the UI thread, which can silently fail in PySide6 for Python-defined slots. Replaced with a class-level `_scan_complete = Signal()` connected to `_apply_routing` — signal emission across threads is always safe.

---

## [0.4.0] — 2026-04-24

### Fixed — Jamulus mixer RPC signal chain
- **Mute and solo now reach Jamulus via JSON-RPC** (`jamulus_controller.py`): `set_mute()` and `set_solo()` previously only sent UDP; mute/solo state was silently lost when the JSON-RPC server was the primary interface. Both now call a new `_send_rpc_gain()` helper that translates mute/solo state to an effective gain level and forwards it over RPC.
- **All RPC calls moved off the UI thread**: `_send_rpc_gain()` spawns a daemon thread for every `set_channel_gain` call. A slow or unreachable RPC server no longer freezes the UI.

### Fixed — Production bugfixes
- **`WebJamEnhancedApp` constructor ordering**: Property-delegated attributes (`jamulus_state`, `webex_state`, `jamulus_process`, etc.) were assigned before `bridge_service` was created, causing `AttributeError` on startup. Removed the redundant early assignments; `BridgeService.__init__` already sets matching defaults.
- **`_on_theme_changed` callback**: `ThemeManager` registered this callback on `WebJamEnhancedApp` but the method was missing. Added implementation that updates `high_contrast_enabled` and calls `_apply_accessibility_mode()`.
- **`session_controller` initialization**: `SessionController` was referenced (e.g. in `quit_app`) but never instantiated in `__init__`. Added `self.session_controller = SessionController(self)` after `bridge_service` creation.
- **`MixerService._saved_mix_payload_for_load`**: `load_mix()` called this helper but it was not defined. Added implementation that checks signed-in user profile first, then falls back to local mix file.

### Added — Test suite (Part 2 of v0.4 sprint)
- **All 11 previously-ignored edge test files now pass** in CI. Methods that migrated to `MixerService`, `BridgeService`, or `ModeController` during the v0.3 refactor were re-tested against their new homes:
  - `test_listening_profiles_edge.py` → `MixerService` (17 tests)
  - `test_reconnect_manager_edge.py` → `BridgeService` (12 tests)
  - `test_help_and_permissions_edge.py` → `MixerService` + `WebJamEnhancedApp` (4 tests)
  - `test_startup_smoke_edge.py` → `MixerService._restore_startup_mix_default` (2 tests)
  - `test_app_polling_edge.py` → updated stubs for `bridge_service` / `session_controller` delegation (14 tests)
  - `test_jamulus_controller_edge.py` → added `rpc_client` stub (11 tests)
  - `test_mode_layout_edge.py` → rewritten against `ModeController` (8 tests)
  - `test_mode_templates_edge.py`, `test_diagnostics_bundle_export_edge.py`, `test_session_brief_export_edge.py`, `test_docs_parity_edge.py` → updated for `_save_notes` rename and new stubs
  - `test_setup_flow_edge.py` → 3 tests migrated to `MixerService`
- **`README_SIMPLE.md`** added — quick-start guide referenced by `test_docs_parity_edge.py`
- **CI `--ignore` flags removed** from `.github/workflows/ci.yml` — full test suite now runs with no exclusions (493 pass, 12 skip on macOS)

---

## [0.4.2] — 2026-04-24

### Fixed / Added — Qt Conductor usability pass 2

#### Navigation
- **SideRail buttons wired**: clicking "Stage" or "Mixer" expands the participant grid; clicking "Canvas" expands the session notes panel; "Chat" and "Roles" flash a friendly "coming in a future update" message. Previously all four buttons did nothing. `ConductorWindow.center_splitter` is now a named attribute; both panels set collapsible so `setSizes` can resize them.

#### Participant metadata
- **`is_local` from Jamulus RPC**: `JamulusParticipant.is_local` field added and propagated from `ChannelInfo.is_local` (which is resolved via `getClientInfo` RPC). `ApplicationController._apply_jamulus_participants` uses the real flag instead of the `channel_id == 0` heuristic. Existing participants also get `is_local` refreshed on every RPC poll.
- **Role label refreshes for existing participants**: when an existing participant's instrument changes (e.g. mid-session Jamulus settings update), the role label is now updated in `self.participants` before the grid refresh, so the card reflects the new instrument.

#### Session canvas
- **Notes persist across launches**: `_load_notes` runs on startup, reading `~/.webjam_notes.md` into the canvas; `_save_notes` runs in `shutdown()` to write it back. Notes survive app restarts.
- **Timestamp button + Ctrl+T**: inserts the current time as a Markdown heading (`## HH:MM:SS`) at the cursor — useful for logging key moments during a session.
- **Export… button**: opens a Save-file dialog so you can write the session notes as a dated `.md` file (e.g. `webjam_session_2026-04-24.md`).
- **Clear button**: clears all notes after a confirmation prompt.

#### Status bar
- **Participant count replaces "—"**: the Latency status label now shows the live participant count ("3 participants") once Jamulus connects, rather than the static "—". Shows "Not connected" before first Jamulus update.

---

## [0.4.1] — 2026-04-24

### Fixed — Qt Conductor runtime gaps (weekend-usability sprint)

#### Signal wiring
- **Duplicate signal connections eliminated**: `ParticipantGrid` now declares `fader_changed / mute_toggled / solo_toggled` re-emit signals and wires them once per card in `_add_card`. `ApplicationController._wire_signals` connects to the grid once; the per-card loop in `_push_participants_to_grid` is removed. Previously, every participant update stacked new connections → N× callbacks per fader move.

#### Auto-reconnect
- **Auto-reconnect timer wired**: `ApplicationController` now starts a 3-second `QTimer` that calls `BridgeService.attempt_auto_reconnects()` on every tick. Previously, `attempt_auto_reconnects()` existed but was never called — dropped Jamulus processes were never retried.

#### Mix save / restore
- **Saved mix auto-restored on Jamulus connect**: when `JamulusController` fires its first real participant update (`_jamulus_connected` flips `True`), `_restore_saved_mix()` loads `~/.webjam_mix.json` and applies it. Fader layout comes back without manual action.
- **Ctrl+S / Ctrl+O (Save/Load Mix)**: new shortcuts in `ConductorWindow`; `ApplicationController` handlers call `JamulusController.serialize_mix` / `apply_mix_data` and flash a status-bar confirmation.

#### Jamulus path detection
- **macOS + Linux default candidates added** to `AppSettings.jamulus_candidates`: `/Applications/Jamulus.app/Contents/MacOS/Jamulus`, `/usr/bin/Jamulus`, `/usr/local/bin/Jamulus`, `/opt/homebrew/bin/Jamulus` — alongside the existing Windows paths. `find_jamulus()` now resolves on first run on common macOS/Linux installs.
- **Jamulus executable field in setup wizard**: the Jamulus page gains a path text field (pre-populated from first existing candidate) and a Browse button that resolves `.app` bundles to the binary. The chosen path is persisted at the front of `jamulus_candidates` in `~/.webjam_config.json`.

#### Error handling
- **`NameError` in BridgeService error dialogs fixed**: lambdas capturing `exc` from `except` blocks (Python 3 deletes `exc` after the block) caused a `NameError` when the actionable-error dialog was shown after a Jamulus or Webex launch failure. Fixed with `lambda m=str(exc): ...` captures.
- **Video button re-enable**: in direct-URL Webex mode the `meeting_state_changed` signal emits `"joining"` and then nothing (no JS bridge). The "Join Video" button was permanently disabled. A 6-second `QTimer.singleShot` now re-enables it as "Video Active".

#### Participant metadata
- **Instrument pass-through**: `_on_rpc_participants` now builds an `instrument_map` from `ChannelInfo` objects and writes each participant's `instrument` field after `_sync_participants_from_protocol`. Role labels in `ParticipantCard` automatically show the instrument (e.g., "Guitar", "Piano") instead of the generic "Musician" fallback.

#### Code quality
- Removed unused `webbrowser`, `Callable`, `Any` imports from `bridge_service.py`; split two single-line compound statements that ruff flagged as E701.

---

## Historical post-v0.3.0 development notes

### Added — Post-v0.3.0 gap fixes
- **Qt widget test suite** (`tests/test_qt_widgets.py`): 45 headless smoke tests covering `LevelMeter`, `ParticipantCard`, `SessionStrip`, `ParticipantGrid`, `SideRail`, and `ConductorWindow`
- **Qt setup wizard tests** (`tests/test_qt_setup_wizard.py`): 18 tests covering `should_show_on_startup`, Jamulus/Webex page validation, settings save/round-trip
- **Ruff linting gate** added to CI (lint step runs before tests; 8 auto-fixed unused imports)
- **`python3-tk` added to CI** apt-get — unblocks 11 previously-ignored Tkinter edge test files; only `test_elevation_edge.py` remains ignored (Windows ctypes.windll)
- **`test_elevation_edge.py`**: Windows-only skip guard — deferred imports prevent `ImportError` on macOS/Linux
- **`ui/mixer_service.py`**: `MIX_FILE` TODO resolved — path now sourced from `AppSettings.mix_file` via `settings=` constructor param; default is `~/.webjam_mix.json`
- **Setup wizard Done page**: explicit "Jamulus must be installed separately" note with link to jamulus.io
- **README status table**: updated to reflect v0.3.0 shipped Qt UI, correct limitation descriptions, and links to Releases page

---

## [0.3.0] — 2026-04-21

### Added — Phase 6: Onboarding, Shortcuts & Build
- **Setup Wizard** (`webjam_qt/windows/setup_wizard.py`): 5-page first-run wizard (Welcome, Jamulus server, Webex URL, audio routing, Done). Saves to `~/.webjam_config.json`. Auto-shown on first run.
- **Keyboard shortcuts**: Ctrl+L (focus session title), F11 (fullscreen), Escape (leave fullscreen), Ctrl+, (open settings)
- **Accessibility**: `setAccessibleName()` on all major panels, focus rings in QSS, screen-reader-compatible labels
- **PyInstaller spec** (`webjam.spec`): Production macOS/Windows bundle with QSS + HTML assets, Info.plist camera/mic usage strings

### Added — Phase 5: Audio Device Auto-Detection
- **`core/audio_routing.py`**: `scan_loopback_devices()` auto-detects VB-CABLE, BlackHole, Loopback Audio, JACK, Soundflower
- **`AudioRoutingStatus`** / **`LoopbackDevice`** dataclasses with device metadata (name, index, channel counts)
- **Setup wizard routing page**: shows detected device name or install instructions with link
- **`RealAudioEngine._resolve_device()`**: uses loopback scan to prefer virtual cable over system mic

### Added — Phase 3: Embedded Webex Meeting Pane
- **`webjam_qt/widgets/webex_embed.py`**: `QWebEngineView` embedded meeting pane (lazy-init, Chromium only started on first join)
- **`webjam_qt/webex_widget.html`**: Local HTML template loading Webex Meetings Widget from CDN; dark theme; loading spinner
- **`_WebexBridge(QObject)`**: QWebChannel bridge for bidirectional JS↔Qt communication (`on_page_ready`, `on_state`)
- **Guest-widget mode**: generates HS256 JWT, exchanges for access token, loads widget in embedded view
- **Direct-URL mode**: fallback — loads meeting URL directly using Chrome user-agent + persistent `webjam_webex` profile
- **Auto-grants** camera, mic, screen capture, notification permissions
- **`core/webex_guest_token.py`**: `generate_guest_jwt()` (stdlib HMAC-SHA256) + `exchange_guest_jwt()` (httpx POST)

### Added — Phase 2: Jamulus Protocol Integration
- **`core/jamulus_rpc_client.py`**: HTTP JSON-RPC 2.0 client with polling loop + SSE stream; `set_channel_gain()`, `set_channel_mute()`; non-blocking `stop()` via `httpx.Client.close()`
- **`core/jamulus_protocol.py`**: Full binary UDP adapter — CRC-16-CCITT (poly=0x1021), CONN_CLIENTS_LIST parser, CHANNEL_GAIN/CHANNEL_PAN commands, CLT_CHANNEL_LEVEL_LIST
- **JSON-RPC launch flag**: `services/bridge_service.py` adds `--jsonrpcport 22222` to Jamulus startup command
- **`services/bridge_service.py`**: `threading.Lock` guards reconnect-in-flight flags; exponential backoff for Jamulus/Webex reconnection
- **Real fader dB math**: `20*log10(level/100)` for 1..100; `(level-100)/27*6` for 101..127; `−∞ dB` for 0
- **Gain wire range fixed**: UDP gain mapped correctly as `int(fader_level / 127.0 * 32767)` (was /100 causing scale error)

### Fixed
- `@Slot()` missing on `_RoutingPage._apply_routing` — wizard routing scan result was silently dropped
- `QWebEnginePage` parented to profile (not widget) — eliminates "profile requested but page not deleted" warning
- SSE stream `stop()` now calls `httpx.Client.close()` to immediately unblock the reader thread
- `QSS`: added `QLabel#BodyLabel`, `QWidget#WebexPlaceholder`, `:focus` and `:disabled` states for all interactive widgets

### Changed
- `RealAudioEngine.stop()` thread join timeout: 1.5s → 3.0s for cleaner shutdown
- `WebexEmbed.load_meeting_with_guest_token()`: stays on placeholder until token arrives (was racing to show page before token fetch)

---

## Historical reliability and hardening rollup

### Security and Data Integrity
- Added serialized lockout mutation flow in `WebJamRepository.authenticate_with_status()` to avoid race-driven counter drift under concurrent failed authentication attempts.
- Switched password hash comparison to constant-time `hmac.compare_digest()` during authentication checks.

### Stability and Runtime Safety
- Hardened `JamulusController.load_mix()` against malformed files and invalid payload shapes with bounded coercion/clamping.
- Added atomic mix save behavior (`tempfile` + replace) to reduce partial-write corruption risk.
- Added participant-state synchronization (`RLock`) across controller and monitor paths to avoid cross-thread mutation hazards.
- Fixed participant auto-ID allocation after removals to avoid channel ID collisions.
- Added explicit sqlite connection management helper to prevent lingering connection warnings and improve cleanup reliability.
- Added sqlite runtime defaults for local repository usage:
  - `busy_timeout=5000`
  - best-effort `journal_mode=WAL`
- Added bounded retention for cohort telemetry events (latest 1000 kept per cohort key).
- Updated settings increment and cohort event append paths to run atomically under concurrency.

### Local API Bridge Resilience
- Added explicit bridge shutdown signaling and thread join behavior.
- Wrapped `/participants` and `/diagnostics` callback errors into HTTP 500 responses with actionable details.
- Added lightweight app-construction helper used by integration tests.

### Configuration and Operational Updates
- Added admin endpoint validation for empty host and out-of-range/non-numeric port values.
- Added warning logging when settings JSON is malformed and defaults are used.
- Added env bounds validation for `WEBJAM_JAMULUS_PORT` (`1..65535`) and sanity checks for numeric audio env values.
- Added env-gated startup debug logging controls:
  - `WEBJAM_AGENT_DEBUG_LOG`
  - `WEBJAM_AGENT_DEBUG_LOG_PATH`
- Updated diagnostics timestamp generation to timezone-aware UTC.

### Tests and Verification
- Expanded modernization and integration coverage:
  - auth lockout behavior under concurrency
  - bounded cohort event retention
  - API bridge callback error wrapping
  - TestClient endpoint integration checks (`/health`, `/participants`, `/diagnostics`)
  - malformed mix payload resilience and clamping/coercion behavior
- Full regression suites pass:
  - `python -m unittest test_modernization`
  - `python -m unittest test_webjam`

### Legacy Launcher Maintenance
- Extracted low-risk shared installer helpers into `utils/installer_helpers.py`.
- Rewired legacy launcher paths to use shared helper implementations to reduce maintenance drift.

---

## Version 2.0 - Enhanced Edition (Current Release)

### 🎉 Major New Features

#### Virtual Mixing Console
- **Professional mixer interface** with individual channel strips for each musician
- **Vertical faders** with dB scale (-∞ to 0dB) for precise volume control
- **Real-time VU meters** showing audio levels with color-coded indicators (green/yellow/red)
- **Pan controls** for stereo positioning (L-C-R) of each musician
- **Mute/Solo buttons** for quick channel control
- **Channel status indicators** showing connection state

#### Modern GUI Application
- **Complete rewrite** with modern tkinter/customtkinter interface
- **Dark theme** optimized for studio environments
- **Intuitive layout** familiar to musicians and audio engineers
- **Responsive design** that works on various screen sizes
- **Professional typography** and visual hierarchy

#### Session Management
- **Save/Load mix presets** for different songs or configurations
- **Automatic settings persistence** across sessions
- **Mix profiles** stored in user directory
- **Quick reset functions** for faders, pans, and mutes
- **Configuration backup** and restore

#### Jamulus Integration
- **Real-time participant detection** (foundation for future implementation)
- **Per-channel level control** via intuitive faders
- **Audio monitoring system** with simulated levels (ready for actual audio analysis)
- **Automatic channel creation** when musicians join
- **Connection status tracking** with visual indicators

#### Webex Integration
- **Browser-based meeting access** with one-click launch
- **Participant synchronization** framework (ready for SDK integration)
- **Embedded view preparation** for future Webex SDK implementation
- **Configuration management** for meeting preferences

### 🛠️ Technical Improvements

#### Architecture
- **Modular design** with separate controllers for Jamulus and Webex
- **Event-driven updates** using callback system
- **Threading** for non-blocking audio monitoring
- **Clean separation** of UI and business logic
- **Extensible framework** for future enhancements

#### Installation System
- **Enhanced installer** (`webjam_installer.py`) with better error handling
- **Progress indicators** for long-running operations
- **Smart dependency detection** and installation
- **Desktop and Start Menu shortcuts** created automatically
- **Application directory** in LocalAppData for clean installation

#### Build System
- **Automated build script** (`build_webjam.py`) for creating executables
- **PyInstaller integration** with proper bundling
- **Distribution package creation** with all necessary files
- **ZIP archive generation** for easy distribution

### 📚 Documentation

#### New Documentation Files
- **README.md**: Complete project overview and quick start
- **USER_GUIDE.md**: Comprehensive 30+ page user manual
- **CHANGELOG.md**: This file, tracking all changes
- **Code documentation**: Extensive docstrings and comments

#### User Guide Includes
- Installation instructions with screenshots
- Step-by-step first session tutorial
- Mixer control reference
- Troubleshooting section
- Professional mixing tips
- Keyboard shortcuts
- Technical appendix

### 🎨 User Interface Enhancements

#### Visual Design
- **Color-coded controls**: Mute (red), Solo (green), Status (green/gray)
- **Professional meters**: VU meters with proper ballistics
- **Clear typography**: Arial font with appropriate sizing
- **Visual feedback**: Button states, hover effects, active indicators
- **Consistent spacing**: Professional layout with proper padding

#### Usability Features
- **Menu bar** with File, Session, and Help menus
- **Status bar** showing participant count and server info
- **Control bar** with quick-access buttons
- **Tooltips** and labels for all controls
- **Keyboard shortcuts** for common operations
- **Modal dialogs** for confirmations and errors

### 🔧 Developer Experience

#### Code Quality
- **Type hints** throughout codebase
- **Dataclasses** for clean data structures
- **Descriptive naming** following Python conventions
- **Error handling** with try-except blocks
- **Logging and debugging** print statements

#### Project Structure
```
WebJam/
├── webjam_app_enhanced.py      # Main GUI application (New)
├── webjam_app.py               # Basic GUI version
├── jamulus_controller.py       # Jamulus integration module (New)
├── webex_integration.py        # Webex integration module (New)
├── webjam_installer.py         # Enhanced installer (New)
├── build_webjam.py             # Build automation (New)
├── requirements.txt            # Python dependencies
├── README.md                   # Project documentation (Enhanced)
├── USER_GUIDE.md              # Comprehensive user manual (New)
├── CHANGELOG.md               # This file (New)
├── webjam_launch_session.py   # Legacy launcher
├── webjam_win_oneclick.py     # Legacy installer
└── VB/                        # VB-Cable drivers
```

---

## Version 1.0 - Initial Release

### Core Features

#### Basic Functionality
- **One-click installer** for Jamulus and VB-Cable
- **Automatic audio routing** setup
- **Desktop shortcut** creation
- **Simple launcher** script

#### Components
- VB-Cable installation with driver detection
- Jamulus installation with multiple installer support
- Audio device configuration via PowerShell
- Webex meeting launcher

#### Limitations of v1.0
- ❌ No mixer controls (used Jamulus built-in mixer)
- ❌ No GUI application (command-line only)
- ❌ No session management
- ❌ Manual participant management
- ❌ Limited configuration options

---

## Migration Guide: v1.0 → v2.0

### For End Users

#### What Changed
1. **New Application**: Launch "WebJam" instead of old launcher
2. **Mixer Interface**: Control levels in WebJam, not Jamulus window
3. **Better Integration**: Automatic participant detection

#### Migration Steps
1. Uninstall old WebJam (optional - won't conflict)
2. Run new WebJam_Installer.exe
3. Launch from new Desktop shortcut
4. Enjoy enhanced features!

#### Settings Migration
- Old settings are not migrated automatically
- Recreate your mix preferences in new interface
- Save your mix using the new Save Mix feature

### For Developers

#### API Changes
- `JamulusController` class replaces direct subprocess calls
- `WebexController` provides structured meeting access
- Event-driven architecture with callbacks
- Configuration via JSON files instead of constants

#### Code Migration
```python
# Old approach (v1.0)
subprocess.Popen([jamulus_path, "--connect", server])

# New approach (v2.0)
controller = JamulusController(server, port)
controller.start()
controller.add_participant("Musician", channel_id)
controller.set_fader_level(channel_id, 75)
```

---

## Roadmap - Future Versions

### Version 2.1 (Planned)

#### Features
- [ ] **Direct Jamulus Protocol**: Implement full Jamulus UDP protocol
- [ ] **Real audio monitoring**: Use PyAudio to analyze actual audio levels
- [ ] **Participant auto-detection**: Automatically discover musicians from Jamulus
- [ ] **Effects processing**: Per-channel EQ, compression, reverb
- [ ] **Recording**: Multi-track recording directly in WebJam

#### Improvements
- [ ] **Performance optimization**: Reduce CPU usage
- [ ] **Better error messages**: User-friendly error dialogs
- [ ] **Config GUI**: Settings panel for advanced options
- [ ] **Server selection**: Choose from multiple Jamulus servers

### Version 3.0 (Future)

#### Major Features
- [ ] **Webex SDK Integration**: Embedded video within WebJam window
- [ ] **MIDI Control**: Use physical faders/controllers
- [ ] **Mobile Companion**: iOS/Android remote control app
- [ ] **Cloud Sync**: Sync settings across devices
- [ ] **AI-Powered Mixing**: Automatic level balancing

#### Professional Features
- [ ] **VST Plugin Support**: Load audio effects plugins
- [ ] **Multi-server**: Connect to multiple Jamulus servers simultaneously
- [ ] **Advanced Routing**: Custom audio routing matrix
- [ ] **Metering**: Professional audio meters (PPM, RMS, LUFS)
- [ ] **Time Alignment**: Compensate for latency differences

### Community Wishlist

Vote for features you want to see:
- [ ] Linux and macOS support
- [ ] Standalone mode (Jamulus+Webex in one)
- [ ] Practice room scheduling
- [ ] Integrated chat
- [ ] Sheet music viewer
- [ ] Metronome with sync
- [ ] Latency testing tools
- [ ] Performance analytics

---

## Historical v2.0-era notes (archived)

The following notes were preserved from a 2024 planning document. They do not
describe the current v0.12 Host/Join UI, Webex handoff boundary, or packaging
claims; use the Unreleased entry, README, and v1 last-mile readiness record
above for current behavior.

### Current Limitations

#### Jamulus Integration
- ~~**Participant detection** is currently manual~~ — **Resolved** (Phase 2): Full Jamulus UDP protocol + JSON-RPC client auto-detects participants via CONN_CLIENTS_LIST
- ~~**Audio levels** are simulated~~ — **Resolved** (Phase 2): Real fader dB math and UDP gain wiring implemented in `core/jamulus_protocol.py`
- ~~**Mixer commands** don't yet control actual Jamulus mixer~~ — **Resolved** (Phase 2): `set_channel_gain()` and `set_channel_mute()` wired to live Jamulus JSON-RPC endpoint

#### Webex Integration
- ~~**Browser-based** video (not embedded in app)~~ — **Resolved** (Phase 3): `QWebEngineView` embedded meeting pane with `webex_widget.html` template
- ~~**Participant sync** is name-based matching only~~ — **Resolved** (Phase 3): Bidirectional JS↔Qt bridge via `_WebexBridge(QObject)` + QWebChannel
- **No video controls** from within WebJam — still managed via the embedded Webex widget UI

#### Audio Routing
- ~~**VB-Cable required**: No built-in virtual audio device~~ — **Resolved** (Phase 5): `scan_loopback_devices()` auto-detects VB-CABLE, BlackHole, Loopback Audio, JACK, and Soundflower
- ~~**Manual device setup**: May need manual configuration~~ — **Resolved** (Phase 5/6): Setup wizard routing page auto-detects and configures the preferred virtual device
- **Single audio stream**: Can't separate audio and video audio — still a system-level constraint

### Bug Reports

For a current issue, report it at:
1. Go to: https://github.com/rupret007/webjam/issues
2. Click "New Issue"
3. Describe the problem with steps to reproduce
4. Include your system info (Windows version, audio interface, etc.)

---

## Historical credits and acknowledgments

### WebJam Team
- **Development**: [Your Name]
- **UI/UX Design**: [Designer]
- **Testing**: [Testers]
- **Documentation**: [Writers]

### Open Source Projects
- **Jamulus**: Low-latency audio - [jamulus.io](https://jamulus.io)
- **VB-Audio**: Virtual audio cables - [vb-audio.com](https://vb-audio.com)
- **CustomTkinter**: Modern tkinter - [github.com/TomSchimansky/CustomTkinter](https://github.com/TomSchimansky/CustomTkinter)
- **PyInstaller**: Python packaging - [pyinstaller.org](https://pyinstaller.org)

### Special Thanks
- Jamulus community for inspiration
- Beta testers for valuable feedback
- Musicians who tried early versions
- Open source community for tools and libraries

---

## License

WebJam is released under the MIT License.

Copyright (c) 2024 WebJam Contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

---

**Historical metadata**: October 9, 2024 · Version 2.0.0 · Release Candidate

For current updates, visit: **[WebJam on GitHub](https://github.com/rupret007/webjam)**
