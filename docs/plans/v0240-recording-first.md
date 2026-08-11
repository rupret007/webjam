# v0.24.0 — recording-first workstation plan

> Status: plan for the v0.24.0 source candidate. v0.23.0 is the immutable
> GitHub Latest. Nothing below is implemented unless the changelog's
> Unreleased section says so. Physical gates remain **NOT RUN**.

Product decision: v0.24.0 prioritizes the recording and Studio experience —
Shared Track → rehearse → multitrack Record Session → truthful finalization →
take review → comp, mix, export — over broadening meeting integrations. The
Meeting Companion remains optional and provider-neutral; Jamulus remains the
only music path.

## What v0.23.0 already provides (reuse, do not rebuild)

Shared Track live surface (add/drop, waveform, transport, loop, count-in,
route/dropout truth, stopped-only replacement); Record Session with
preparing/count-in/recording/stopping/finalizing/ready states; the SPSC
project-recording engine (`core/project_recording.py`, punch/count-in/
pre-roll frame binding, writer-owned publication) with manifest journal and
readiness checks; Local Originals; take recovery; non-destructive Studio with
multi-region editing and Overdub in Reference Studio; privacy/redaction and
support-bundle allowlists; the multi-service meeting-link policy and
app-identity registry (carried onto this line under Unreleased).

## Gap analysis against the recording-first goals

1. One authoritative SessionRecordingPlan — PARTIAL. Binding facts exist but
   are spread across recorder config, readiness, and manifest journal; there
   is no single immutable pre-record plan object naming roster, server-stem
   expectations, Shared Track fingerprint/generation, input maps, storage
   budget, and validation requirements under one generation. Highest-value
   consolidation seam; build it as a frozen dataclass assembled from the
   existing sources, not a new subsystem.
2. Truthful Finalizing gate — MOSTLY PRESENT. States exist; audit the gate
   conditions against the full checklist (server stop proof, source
   inventory, Local Original reconciliation commit, validation, cleanup or
   Cleanup-pending, durable manifest) and add regression tests for each
   condition individually.
3. Recording-first live UI — PARTIAL. Record Session and per-phase guidance
   exist; the compact per-musician source-card workspace (armed/waiting/
   recording/reconnecting/missing/finalized, meters, clip/dropout badges,
   compact-size guarantees) is the main UI build. Reuse participant grid and
   existing meter widgets.
4. Configurable input maps — VERIFY THEN EXTEND. Fixed Guitar/Vocal wording
   survives only in setup placeholders; confirm the engine's 1–32 map
   support is exposed end-to-end (names, device/channel, mono/stereo,
   enable, Local Original opt-in) and surface it in one input-map editor.
5. Completed-take Studio focus, take lanes, comping — PARTIAL. Studio and
   comping exist; unify source presentation (Musician / Shared Track /
   Local Original), reconnect segments as regions on one lane, and enforce
   fingerprint-equality cross-take Shared Track matching (fail closed on
   legacy evidence). Extract mature Reference Studio command logic instead
   of duplicating.
6. Mixer and export — MOSTLY PRESENT. Keep schema-v2 fail-closed export;
   add the compact live-adjacent mixer view only after the source cards.
7. Meeting Companion — FOUNDATION DONE on this branch (recognition,
   validation, labels, redaction, identity registry, platform matrix in
   docs/plans/multi-service-conversation-phase2.md). Remaining: provider
   adapter object boundary, guest visibility, copy-link, recent choices,
   and the explicit decision on generic-HTTPS fallback (today unknown hosts
   fail closed; a generic fallback must be a deliberate, labelled policy
   change, not silent acceptance).
8. Accessibility/responsive QA — apply the Part 9 checklist to the new
   recording workspace as it is built; the existing suites already cover
   several geometry/accessibility contracts.

## Owner directives recorded 2026-08-10

- Studio/live parity: the Studio must offer the same functional depth as
  the live jam surface (transport, Shared Track context, per-musician
  truth), with the clarity and information density of Logic/GarageBand as
  the quality bar — as an original WebJam design, never copied artwork,
  layouts, or trade dress.
- Multitrack recording enhancements remain the top implementation
  priority (plan steps 1–4).
- A YouTube listen-along option was evaluated and declined by the owner:
  routing YouTube audio into the session would violate YouTube's Terms of
  Service and the project's copyright/network boundaries, and an
  external-listening-only version was judged not worth the surface.
  Uploaded local files remain the only Shared Track path.

## Step 2 audit findings (2026-08-10)

The project engine is already the 1-32 track engine (one wide input
stream, one ring+writer per armed track, per-track names driving
filenames); only Reference Studio uses it. The live path's
`LocalInputCapture` is hard-wired to exactly two mono stems
(host-guitar/host-vocal, device channels 0/1, 48 kHz). The plan now
states that fixed map truthfully. Remaining step 2 order: (1)
`AppSettings.input_maps` structured field with strict coercion and the
compat rule that `local_capture_enabled` + empty maps equals today's two
fixed tracks; (2) derive `required_local_stems` and the storage budget
from the map instead of the hardcoded 2; (3) generalize
`LocalInputCapture` to a track list while KEEPING its single stream,
checkpoint journal, and recovery path (do not switch the live path to
the project engine — that loses crash recovery); (4) the editor UI,
reusing Reference Studio's mapping pattern keyed on stable device_key,
never a PortAudio index. Known risks: ring-memory budget and per-stem
fsync cadence at high track counts, storage preflight under-reserve, and
device-index churn across reboots.

## Step 3 audit findings (2026-08-10)

The live surface today shows one session-wide phase string, an elapsed
clock, Shared Track truth, and a REC chip; participant cards carry zero
recording awareness, and guests receive one authenticated monotonic
RecordingSignal plus Shared Track state. The per-source truth projection
now exists (`core/recording_sources.py` + the coordinator's
`recording_source_presentations()` accessor, snapshotting under the
receipt lock) with the conservative rules: receipts are identity
evidence, not liveness — unproven participants WAIT during recording and
go MISSING only after the take's receipt set freezes; only explicit
conflict keys render CONFLICTED; the UI-synthesized `count_in` phase is
handled explicitly; no fingerprints, digests, channels ids, or paths
leave the projection. Remaining step 3 in order: (1) render the
projection as one bounded status line per ParticipantCard via a
defaulted `recording_state` field on ParticipantPresentation and one
accessibility clause — strictly inside the pinned 260×228 card envelope
and the exact-geometry grid tests; (2) refresh cards from the
coordinator on phase changes and roster observations through the UI
invoker; (3) guest-side derivation from the existing session-wide signal
only (self from local capture truth, others host-attributed) — no
per-participant fields in SessionStateSnapshot, which would be a schema,
generation, and privacy change WebJam currently refuses. Guards: the
1100px no-overlap strip test, the once-only set_recording_phase guest
assertion (use a separate setter), and the fingerprint-absence tests.

## Step 3 completion record (2026-08-10)

Host side: per-source projection + coordinator accessor + card badges,
all landed with geometry and truth guards. Guest side is complete by
deliberate refusal: guest cards render no per-musician badges, because a
guest holds no per-musician recording proof — the authenticated
session-wide signal in the strip is the guest truth. Structurally
guaranteed (guest coordinators never enter active phases, so the
projection is empty) and locked by regression. Any future guest badge
requires per-participant wire attribution: a schema, generation, and
privacy change to SessionStateSnapshot that must be its own reviewed
step.

## Step 4 findings and completion record (2026-08-10)

The cross-take Shared Track fingerprint gate already shipped in v0.23
(`core/studio_comping.py`): equal, nonempty digests or no match, with
musician matching correctly participant-id-based. This step promoted the
gate to a public reason-carrying predicate (`shared_track_sources_match`)
and made `add_take_lane` raise those honest reasons — a swapped song now
says so instead of "no unambiguous matching track", and legacy takes
without source evidence say exactly what is missing. Studio vocabulary
unified to Musician / Shared Track / Local Original across badges,
descriptions, and inspector. Deliberately NOT done here, recorded as
future steps with reasons: merging reconnect groups for local/unproven
sources (group keys derive durable track_ids — changing them invalidates
StudioDocument references and clone/reconcile lineage); tightening host
local-original admission (today confidence > 0 alone — changing it
alters export eligibility of existing takes); wiring SharedTrackBinding
into the plan at record start (fingerprint accessor exists only after
playback starts).

## Step 5 findings and completion record (2026-08-10)

The completed-take Studio mixer already satisfies most of the spec:
per-track trim/gain/pan/mute/solo, latched clip indication inside each
meter (spoken to screen readers), source badges, master gain, master
meter, and limiter. This step added the missing reset/default action:
one undoable "Reset Mix" beside the master row that returns every track
and master control to defaults while deliberately preserving export
inclusion (a reset must never silently re-include an excluded track),
no-ops when the mix is already default, and syncs widgets and the
player. Export invariants re-run green: studio export (schema-v2
fail-closed, shared authoritative mix), take export (gain/pan/mute/solo
honored, durable mix ids), studio mixer parity, comping, and the
Reference Studio mixer dialog. Deferred with reasons: a sticky per-take
overload latch (the live clip bool is recomputed per block and cleared
each tick; a latch must be epoch-cleared inside the existing level lock
or it reintroduces a pinned race) and Reference Studio mixer meters
(the dialog is deliberately state-free; a live level feed needs the
take-Studio epoch pattern, not a new one).

## Step 6 completion record (2026-08-11)

The Meeting Companion adapter boundary now exists as
`MeetingProvider` / `meeting_provider_for_link()` in core/meeting_link.py:
recognition facts only (key, label, hostname, platform gate,
native-detection support — true solely for Webex, the one app WebJam
verifies). Authenticated integrations extend this object later; nothing
claims them today. The conversation card follows the saved link's
service in its title ("Zoom conversation"), falls back to Webex, and
gains a Copy Link action enabled only with a validated saved link,
copying the normalized URL with a flash confirmation. Remaining
companion items, deliberately out: guest-visible provider surface (needs
the same wire-schema review as guest badges), recent-choice memory
(privacy review first), and the generic-HTTPS fallback decision, which
stays fail-closed until explicitly made as a labelled policy change.

## Step 7 closing verification record (2026-08-11)

Full repository sweep: all 249 test files pass under CI-style per-file
isolation, offscreen Qt, non-root. CI-pinned ruff clean on every linted
tree including reference_service; git diff --check clean; release
metadata and workflow-limit contracts green. One real fix emerged: the
new participant-card test file needed deterministic widget disposal
(interpreter-exit collection of unparented QWidgets crashes Qt offscreen
teardown) — landed with the sweep. Physical gates remain NOT RUN, as
always, until humans run exact packages.

v0.24.0 readiness: the branch carries the plan, four completed steps
(1, 3, 4, 5, 6), step 2's engine+schema halves, and the meeting-platform
foundation. READY FOR HANDS-ON SOURCE TESTING of the landed behavior.
Not yet a release candidate: step 2's classification/budget coherence
pass, the deferred mixer latches, and the editor UI remain, each with
seams documented above.

## Phase 8 — step 2 coherence pass completed (2026-08-11)

Configured input maps now drive recording end to end through one
resolver, `resolve_capture_tracks(settings)`: enabled Local-Original
entries map onto sequential device channels in list order (stereo
entries split into L/R mono stems), names sanitize deterministically
into `local-` prefixed filesystem-safe stems, disabled and
non-Local-Original entries consume nothing, and the legacy fixed pair
remains the exact behavior for an enabled capture with no valid
configuration. The same resolver feeds capture (`LocalInputCapture`
tracks), the per-take SessionRecordingPlan (capture truth, not intent),
required local stems at finalization (take-scoped count captured at
capture start), the storage budget (per-track reserve instead of the
hardcoded 2), and diagnostics. Take classification recognizes
`local-` stems alongside the legacy pair, and local channel numbers
enumerate stably by sorted filename (legacy order preserved). Still
deferred to the editor-UI phase: explicit device-channel selection
(sequential allocation is the documented default) and non-Local-Original
input tracks, which are reserved and skipped.

## Phase 9 — input-track editor (2026-08-11, unattended)

The musician-facing front end for the phase-8 resolver:
`webjam_qt/windows/input_map_editor.py` (self-contained, headlessly
tested) edits `AppSettings.input_maps` as add/name/remove rows with
mono/stereo, enable, and Local Original, validating exactly like the
settings loader. Recording Setup opens it via "Edit Input Tracks…",
shows a configuration summary, and persists the maps with the capture
flag. Reserved for later: explicit device-channel selection (sequential
allocation is the documented default) and a device picker keyed on
stable device IDs.

## Phase 10 — sticky overload latch (2026-08-11, unattended)

The first of the two deferred mixer items. The completed-take Studio now
latches clip state per playback epoch: once a lane or the master clips,
its meter stays lit until transport restarts or seeks (epoch change),
reusing the existing meter rendering (feed `sticky OR current` clipped)
so no widget or geometry changes and the realtime path is untouched.
`overloaded_sources()` exposes it. One existing ruler/meter test pinned
the old clear-next-tick behavior and was updated to the sticky semantics
(the epoch-reset clear is covered by the dedicated latch test). Still
deferred: Reference Studio mixer meters (that dialog is state-free; a
live feed needs the take-Studio epoch pattern) and a visible per-lane
overload badge (geometry-pinned; `overloaded_sources()` is the seam).

## Phase 11 — auto-open completed take: already satisfied (2026-08-11)

Verified rather than rebuilt: successful finalization already surfaces
the finished take. on_take_completed reloads the take list and selects
the completed path, the selection handler loads it into Studio, and the
controller switches the live surface to the takes view (gated on
durable_shutdown_publication, which is true for a normal completion). A
focused guard test now pins the studio-side selection+load so the
behavior cannot silently regress. No redundant code was added. Remaining
larger phase-11 ambitions (a dedicated compact recording-workspace
layout distinct from the live grid, and extracting Reference Studio
editing-command logic to share with the take Studio) are genuine
follow-ups, not started here.

## Sequencing (each step lands with focused regression tests)

1. SessionRecordingPlan consolidation + Finalizing-gate condition tests.
2. Input-map editor exposure and end-to-end plumbing audit.
3. Recording workspace UI (source cards, compact guarantees, Stop always
   reachable), host-authority/guest-projection tests.
4. Studio source-identity unification + comp fingerprint gate.
5. Compact mixer polish; export invariants re-run.
6. Meeting Companion adapter boundary; provider docs.
7. Full-suite, packaging, accessibility, and geometry passes; update docs
   and Unreleased changelog per step.

Do not tag, release, or push without explicit review and authorization.
