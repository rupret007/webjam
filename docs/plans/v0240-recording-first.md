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
