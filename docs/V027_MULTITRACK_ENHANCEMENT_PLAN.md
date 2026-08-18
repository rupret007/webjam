# v0.27 Multitrack Enhancement Plan

Phase 1 deliverable: gap verification, candidate features, sequencing, explicit
non-goals, and one recommended first slice. No production code accompanies this
document. A human selects the Phase 2 slice; nothing below begins on its own.

## Baseline statement

This plan was verified against **`8b52bbf` ("docs: record immutable v0.26
release"), the current `master` tip**, not the brief's stated baseline
`076c2f9` (`codex/v027-multitrack-proof-lab`). That commit does not exist on
`https://github.com/rupret007/webjam.git` (no such branch; `master` tips at its
stated parent), in either local clone, or in any of the 21 bundles on disk.
Per the brief's own instruction the base was not guessed; the human directed
planning to proceed against `8b52bbf`.

Consequences, stated so nothing below silently assumes the missing delta:

- `tools/run_multitrack_proof_lab.py`, `tests/support/multitrack_proof_lab.py`,
  and `capture_finalization_needs_attention` (the "v0.27 finalization fix")
  exist **only in the missing commit**. Zero occurrences at `8b52bbf`. The
  proof-lab gate, its matrix, and the 262-file / 4,720-pass baseline numbers do
  not apply here.
- Local baseline at `8b52bbf` on this machine (macOS 15.8, Python 3.11.16,
  umask 022, unprivileged user, `QT_QPA_PLATFORM=offscreen`): **260 test
  files, 259 pass, 1 pre-existing failure** —
  `tests/test_jamulus_update_service.py::test_check_download_and_platform_handoff_are_truthful_and_redacted`
  fails because its fixture's signed approval (`NOW = 2026-07-28` +
  `timedelta(days=20)`, `tests/test_jamulus_update_service.py:74,230`) expired
  against real wall-clock on 2026-08-17. The refusal is correct product
  behavior; the test's clock injection is incomplete. Flagged for a separate
  fix; not touched by this work.
- If `076c2f9` (or its patch/bundle) arrives, Phase 2 re-bases onto it and
  re-runs this verification pass before writing code. Every item below that
  the missing delta could plausibly touch is marked **[recheck at 076c2f9]**.

## 1. Verification pass

Statuses: **Confirmed** (gap exists as described), **Already closed** (the
brief's claim is not a gap at this base), **Partly closed** (real gap, but
narrower or different than described).

### 1a. Capture fidelity and sync

**48 kHz session pin — Confirmed, with one correction.**
`core/take_library.py:2212` pins `project_sample_rate = 48_000`;
`core/take_library.py:1251,1283` (`require_48k=True` default) fails take
validation for any non-48k track; `core/local_capture.py:448–449` and `:586`
refuse a device that cannot open at 48 kHz with `unsupported_sample_rate`.
**Correction:** the brief says `core/take_export.py` "rejects any source whose
rate differs." It does not — `_render_segment_block`
(`core/take_export.py:296–357`) converts any source rate onto the project
timeline deterministically and records `"resampling":
"deterministic-linear-affine-v1"` in per-track evidence
(`core/take_export.py:845–853`). The render/export math is already
multi-rate; the pin lives at capture preflight and take validation. And the
brief's "no policy for an interface that cannot do 48 kHz" is inverted: the
policy exists and is fail-closed refusal. The genuine remaining gap is a
*deliberate, plan-bound* alternate-rate capture path (candidate F10).

**PCM-24 pin — Confirmed.** `subtype="PCM_24"` at `core/take_export.py:118,
189, 281, 443`, `core/song_bounce.py:628` (and `:682` verification),
`core/studio_export.py:2027`; contract language in
`core/take_export.py:1034,1239` and `core/studio_export.py:2194`.

**Clock drift "corrected by repositioning, not resampling" — Already closed.**
This is the largest factual correction. Default Studio regions carry
drift-scaled timeline extents: `_default_region`
(`core/studio_project.py:3104–3117,3137`) computes
`timeline_frame_count = round(frame_count / sample_rate × drift_scale ×
project_rate)` while `source_frame_count` stays unscaled, so the region's
affine map ratio ≠ 1 whenever `drift_ppm ≠ 0`. The Studio renderer resolves
fractional source positions through exactly that ratio with linear
interpolation (`core/studio_renderer.py:2341–2360`), and the aligned-originals
export path applies the same transform (`core/take_export.py:296–357`,
docstring: "applies the same affine drift transform used by Studio without
rewriting the immutable source"). A guest's drifting clock does **not** walk
away inside a long region on any render path; disclosed capture gaps stay
silent after conversion (`core/take_export.py:339–353`). At the certified
±15 ppm ceiling (`core/take_alignment.py:47`), linear interpolation error is
far below the 24-bit noise floor. Certified tolerances confirmed as stated:
offset 1.5 ms (docstring `:14`), drift 15 ppm, anchor RMS 2 ms, confidence
floor 0.72 (`core/take_alignment.py:47–50`). No source file is ever rewritten
(`core/take_alignment.py:6–8`).

**Alignment confidence "computed but not operable" — Partly closed; the
operability gap is real.** Display exists:
`webjam_qt/widgets/studio_arrangement_workflow.py:961–970` renders
"Evidence {confidence:.2f} · {method}". The model is further along than the
brief says: `TimeTransform.manual_nudge_s` exists
(`core/take_alignment.py:128`), folds into `effective_offset_s` (`:147`), has
a guarded constructor `AlignmentState.with_manual_nudge`
(`core/take_project.py:456–459`), serializes into the take-project manifest
(`:468`), and is already reported in export evidence
(`core/take_export.py:840`). The behavioral contract already draws the safety
line: "A manual nudge alone cannot turn an uncertain guest original into an
export-ready one" (`RECORDING_AND_STUDIO.md:144–145`). What does not exist:
**any UI or controller writes `manual_nudge_s`** (zero `webjam_qt` hits; the
only caller is a unit test, `tests/test_take_alignment.py:317`), no
user-facing explanation of *why* an alignment was refused, and no manual
anchor path. Candidate F1. **[recheck at 076c2f9]** — the missing commit's
proof lab may exercise alignment surfaces.

**Punch/replace only in Reference Studio song projects — Confirmed.**
`RECORDING_AND_STUDIO.md:276–292` scopes Overdub loop recording to song
projects explicitly ("completed-take Studio remains a review, arrangement,
mix, and export surface"); recorder lives in `core/project_recording.py` /
`core/project_recording_commit.py`; completed-take Studio has no punch path.

### 1b. Studio editing depth

**Batch clipboard is song-project-only — Confirmed, and contractual.**
`RECORDING_AND_STUDIO.md:186–188` ("completed-take Studio keeps edit actions
single-region") and `:203–211` ("Completed-take Studio does not expose these
batch clipboard commands"). Introduced by `bc5f2f7`. The reason it was
restricted, reconstructed from the identity model: schema-v3 song regions
carry one project-level `source_media_id`, while schema-v2 completed-take
regions carry a `(source_take_id, source_track_id, source_segment_id)`
triplet bound to their track's source inventory
(`core/studio_project.py:1276–1283`); cross-track paste is only trivially safe
under the song catalog model. Lifting this is a **contract change plus an
identity-gated paste design**, not a port. Candidate F6.

**No time-stretch / elastic audio — Confirmed as a user feature; the
enforcement question is answered.** Nothing enforces
`mapping_source_frame_count == mapping_timeline_frame_count`. They default
equal (`core/studio_project.py:1073–1090`), but drift already makes them
unequal in ordinary use, the renderer already interpolates through any ratio
(`core/studio_renderer.py:2341–2360` — with the comment explaining why splits
preserve the parent affine map bit-identically), and validation only requires
the map to stay inside the cataloged segment
(`core/studio_renderer.py:1137–1145`). What is missing for *musical*
stretching is a quality resampler; linear interpolation is certified for
±15 ppm, not for ±10% tempo pulls. Recommended non-goal (§4).

**No per-region gain — Confirmed.** `StudioRegion` carries fades and curves
only (`core/studio_project.py:940–1110`); the `gain` at `:492` belongs to
`StudioSend`. Level control today is track trim/fader, sends, and mixer
automation. Candidate F2.

**Comp boundary fixed at 5 ms equal-power — Confirmed.**
`DEFAULT_COMP_BOUNDARY_MS = 5.0` (`core/studio_comping.py:35`), threaded as a
parameter at `:752` but never varied per boundary; no lane priority.
Candidate F4.

**Waveform amplitude-only — Confirmed.** Min/max envelope tiles
(`core/studio_waveform.py:561–569`), bounded cache, dropout/overload flags via
declared gaps; no spectral view, no clip/phase visualization. Recommended
non-goal for v0.27/v0.28 (§4).

### 1c. Mixing and export interchange

**Effect set — Confirmed.** High-pass (`core/studio_mixer.py:218`), EQ
(`:246`), compressor (`:277`), gate (`:310`), reverb (`:333`). No delay,
de-esser, or per-track limiter (the deterministic limiter at
`RECORDING_AND_STUDIO.md:192` is master-bus delivery). Buses and pre/post
sends already exist (`StudioSend`, `core/studio_project.py:483–520`) —
confirmed present, nothing to add there.

**"Export encoding is PCM-24 WAV only" — Partly closed; the brief overstates
it.** `core/song_bounce.py` already delivers WAV **and FLAC**, plus
capability-gated MP3 through a self-testing adapter protocol with an
AGPL/GPL/SSPL license denylist (`BounceFormat`, `core/song_bounce.py:81`;
`:9–10`, `:57–64`, `Mp3EncoderCapability`/`Mp3EncoderAdapter` `:95–110`). The
real gap: **completed-take Studio export** (`core/studio_export.py:2026–2027`)
and take stems (`core/take_export.py`) are PCM-24 WAV only — and the delivery
machinery to reuse already exists in-house. Candidate F5. No AAC anywhere
(non-goal, §4).

**No standardized loudness — Confirmed.** `core/song_bounce.py:252–255`:
`loudness_method = "ungated stereo RMS of delivered PCM"`. Zero repository
hits for LUFS / BS.1770 / R128 / true-peak. Candidate F3.

**DAW interchange is CSV + instructions text — Confirmed.** `MARKERS.csv`
(`core/take_export.py:908,1043`), sections/markers CSV + import instructions
in Studio export (`core/studio_export.py:115,2154`). No .RPP, AAF/OMF, MIDI
tempo track, or OTIO. Candidate F8; AAF/OMF rejected (§4).

**Edited export POSIX-only — Confirmed.**
`core/studio_export.py:70–76` (`_SECURE_EXPORT_PLATFORM_SUPPORTED`
= posix ∧ (darwin ∨ linux), plus `os.supports_dir_fd` checks),
`studio_export_supported()` `:103–106`; Windows fallback behavior documented
at `RECORDING_AND_STUDIO.md:358–368`. Candidate F9.

## 2. Candidate features

Each: user outcome · modules · invariants stressed and how held · schema
impact/migration · new bounded limits · test shape · size · risk.

---

### F1 — Operable alignment: "why it refused" + manual nudge

**Outcome:** A musician whose take shows waiting/unverified timing can read a
plain-language, path-free reason and nudge that track into place by ear
without leaving Studio — while export verification stays exactly as strict as
it is today.

**Modules:** `webjam_qt/widgets/studio_arrangement_workflow.py` (reason
panel + nudge control), `webjam_qt/controllers/application_controller.py`
(profile-gated command boundary), `core/take_library.py` (surface the refusal
facts it already computes), `core/take_project.py` (existing
`with_manual_nudge` + guarded manifest CAS).

**Invariants:** The nudge is take-project alignment state, not a Studio
sidecar edit. The write path is the existing guarded atomic
compare-and-swap (`replace_take_project_manifest_if_unchanged`,
`core/take_project.py:70–93`) used today by the recording pipeline
(`core/session_transfer_runtime.py:2555`,
`webjam_qt/controllers/recording_coordinator.py:6248`) — it never rewrites
source WAVs and refuses on concurrent change. The contract's existing line
holds unchanged: a nudge cannot promote an uncertain original to
export-ready (`RECORDING_AND_STUDIO.md:144–145`); evidence already reports
`manual_nudge_s` (`core/take_export.py:840`), so exports stay truthful for
free. Review & Rehearsal refuses at the controller boundary like every other
mutation. Key Phase 2 design decision to nail first: confirm manifest
alignment-state updates are recording-pipeline-owned mutations (the CAS
function exists precisely for this) and specify which frozen facts are
rechecked before the swap.

**Schema:** none — `manual_nudge_s` already serializes
(`core/take_project.py:468`).

**Limits:** clamp nudge to an explicit ceiling (proposal: ±2.000 s, stepped;
constant with a test), bounded reason-text length.

**Tests:** nudge persists across take reload; CAS refuses when the manifest
changed underneath; nudged-but-unverified original still refuses aligned
export (name it like
`test_manual_nudge_cannot_promote_unverified_original_to_aligned_export`);
reason text contains no path for every refusal class; R&R profile refuses the
command; out-of-range nudge refuses.

**Size/risk:** **S/M, low.** Every model hook, persistence path, evidence
field, and contract sentence already exists; this is operability plumbing.

---

### F2 — Per-region gain

**Outcome:** Turn down one hot chorus region (or lift one quiet fill) without
automating the whole track.

**Modules:** `core/studio_project.py` (`StudioRegion.gain`, strict schema),
`core/studio_renderer.py` (apply per-region scalar in the region plan),
`webjam_qt/widgets/studio_arrangement_workflow.py` (inspector control),
`core/studio_history.py` (coalescing continuous changes — pattern exists).

**Invariants:** render-time scalar only; sources untouched; export provenance
gains a per-region field (additive keys in evidence JSON, within report
ceilings).

**Schema:** **this is the schema-bump feature.** Region parsers are strict
both ways (`_strict_keys` with `required=expected`,
`core/studio_project.py:1280–1286`), so an additive key cannot ride
schema-v2 silently. Follow the existing versioned-field pattern
(identity/song fields gated on schema version,
`core/studio_project.py:851–900`) and the schema-v1→v2 migration precedent:
older documents load with `gain=1.0` in memory, original bytes preserved
until the first explicit save (`RECORDING_AND_STUDIO.md:326–328`). Older app
builds reading a newer sidecar refuse — fail-closed, consistent with house
rules, but it must be stated in RECORDING_AND_STUDIO.md. Coordinate with F4
so completed-take documents bump **once**, not twice.

**Limits:** `0.0 ≤ gain ≤ MAX_GAIN` (4.0, `core/studio_project.py:39`).

**Tests:** renderer applies gain deterministically (hash-stable render);
old sidecar loads with default and untouched bytes; round-trip after explicit
save; malformed/out-of-range gain refuses; undo restores exact snapshot.

**Size/risk:** **M, medium** (all of the risk is the schema evolution).

---

### F3 — BS.1770 loudness + true peak in bounce/export evidence

**Outcome:** Every bounce and Studio export reports LUFS-I (gated, per
ITU-R BS.1770-4) and true-peak dBTP next to the existing RMS figure, so a
podcaster can hit −16 LUFS and a band can check −14 LUFS without opening
another tool.

**Modules:** new `core/loudness.py` (pure NumPy: K-weighting biquads,
400 ms/75% blocks, absolute −70 LUFS + relative −10 LU gating; 4× oversampled
true peak); `core/song_bounce.py` (add fields beside
`loudness_dbfs`/`loudness_method`, `:252–255`); `core/studio_export.py`
provenance.

**Invariants:** measurement only — delivery bytes unchanged; the existing
ungated-RMS field is kept (additive, never replaced); evidence keys are
path-free. **No new dependency** — deliberately avoids the
`requirements-lock/` native-packaging trap.

**Schema:** evidence/provenance JSON gains additive keys
(`integrated_lufs`, `true_peak_dbtp`, `loudness_standard`); no sidecar or
manifest impact.

**Limits:** chunked measurement bounded by existing
`MAX_BOUNCE_DURATION_SECONDS` (`core/song_bounce.py:55`) and the export
chunk-frame pattern; measurement state is O(blocks) with an explicit cap.

**Tests:** −18 dBFS 997 Hz sine → −18.0 LUFS within published tolerance;
gating ignores long silence; inter-sample peak on a 0 dBFS 11.025 kHz-style
worst case reads > 0 dBTP; evidence keys present in bounce and Studio export;
mono/stereo channel weighting; duration-cap refusal.

**Size/risk:** **M, low-medium** (well-specified DSP, additive surface).

---

### F4 — Per-boundary comp crossfade shaping

**Outcome:** Fix one audible comp seam by widening or reshaping just that
boundary, instead of living with the global 5 ms equal-power default.

**Modules:** `core/studio_comping.py` (per-boundary width/curve override on
`StudioCompRange` boundaries; default stays
`DEFAULT_COMP_BOUNDARY_MS`, `:35`), renderer boundary application, inspector
UI.

**Invariants:** comp selection stays sidecar-only; equal-power law preserved
as default curve; sources untouched.

**Schema:** additive fields on comp-range entries — same strict-parser
consequence as F2; **must share F2's single version bump**.

**Limits:** boundary width clamped (proposal 1–200 ms, constant + test).

**Tests:** custom width renders deterministic equal-power seam; out-of-range
refuses; legacy documents default to 5 ms with bytes preserved; overlapping
boundary widths that would cross a neighboring seam refuse.

**Size/risk:** **S/M, low** (piggybacks F2's schema work).

---

### F5 — FLAC (and gated MP3) delivery for completed-take Studio export

**Outcome:** An edited-session export can hand the band FLAC masters — and
MP3 review copies where a certified encoder adapter exists — instead of
WAV-only.

**Modules:** `core/studio_export.py` (delivery stage after the WAV masters),
reusing `core/song_bounce.py`'s `BounceFormat`, FLAC writing, MP3 adapter
protocol + license denylist (`:57–110`); provenance + `SHA256SUMS.txt`.

**Invariants:** PCM-24 WAV stems remain the evidence artifacts; FLAC/MP3 are
*additional* deliverables, never replacements; a failed encode fails the
delivery closed rather than shipping a partial set; all new files hashed and
listed; no best-effort fallback.

**Schema:** provenance JSON additive keys only.

**Limits:** existing export disk-reserve and package-size ceilings extended
to count the added artifacts (explicit constants + tests).

**Tests:** FLAC decodes bit-identical to its WAV master; missing FLAC support
in the runtime refuses with path-free guidance; MP3 only via self-tested
adapter (`Mp3EncoderCapability.self_tested`); license-denylisted adapter
refuses; SHA256SUMS covers every delivered file.

**Size/risk:** **M, medium** (mature in-house pattern; the care is in the
fail-closed delivery set).

---

### F6 — Batch clipboard in completed-take Studio

**Outcome:** Select several regions in a recorded-session take and
cut/copy/paste/delete them as one undoable edit, like Reference Studio song
projects already allow.

**Modules:** `webjam_qt/widgets/studio_arrangement_workflow.py`,
`webjam_qt/controllers/application_controller.py` (boundary), batch edit ops
in `core/studio_project.py` (reuse the song-project batch model from
`bc5f2f7`), `core/studio_history.py`.

**Invariants stressed:** the schema-v2 region identity triplet binds a region
to its track's source inventory — paste must be same-track, or gated on
identical source identity availability on the destination track; a paste that
cannot prove identity fails closed (mirror the song rule "a copied region
whose destination track no longer exists fails the paste closed",
`RECORDING_AND_STUDIO.md:208–209`). R&R Preview keeps refusing at the
controller boundary. **Contract change:** `RECORDING_AND_STUDIO.md:186–188,
203–211` currently promise the restriction; the doc edit is part of the
feature, not an afterthought.

**Schema:** none (copies are new durable IDs over existing fields).

**Limits:** bounded selection size (history is already bounded by
`DEFAULT_MAX_ENTRIES = 128` / `DEFAULT_MAX_BYTES = 8 MiB`,
`core/studio_history.py:28–29`; one batch = one entry, with a serialized-size
test at the cap).

**Tests:** cross-track paste onto identity-incompatible track refuses;
relative offsets preserved; one undo restores the exact pre-batch snapshot;
paste at history-size ceiling refuses cleanly; R&R refuses.

**Size/risk:** **M/L, medium-high** (identity-model reasoning, contract
change).

---

### F7 — Tempo-grid snapping for region edits

**Outcome:** Region moves and trims can snap to bars/beats of the existing
tempo map, so arranging to the song's meter stops being pixel-guesswork.

**Modules:** `core/studio_tempo.py` (grid queries exist at 960 TPQ),
snapping layer beside the existing time/marker snapping
(`RECORDING_AND_STUDIO.md:189–190`), UI toggle.

**Invariants:** placement-only — no audio mapping change, no schema; snap
never fires without the toggle.

**Schema:** none (tempo map already persisted; snap mode can stay a
non-persisted view preference, or ride F2's bump if persisted).

**Limits:** grid query bounded by existing tempo-map entry ceilings.

**Tests:** nearest-gridline determinism at boundaries; disabled snap is
byte-identical behavior to today; degenerate tempo maps refuse gracefully.

**Size/risk:** **M, low-medium.**

---

### F8 — Reaper .RPP session + MIDI tempo track in export packages

**Outcome:** The export folder opens directly as a Reaper session with tracks
laid out and stems placed on the timeline, and carries a standard MIDI file
whose tempo track any DAW can import.

**Modules:** new `core/daw_interchange.py` (RPP text emitter; SMF type-0
tempo/meter emitter — both plain formats, zero dependencies);
`core/studio_export.py` / `core/take_export.py` package assembly; import
instructions text.

**Invariants:** additive artifacts only; RPP references package-relative
filenames only (no path leaks — same rule as `core/redaction.py` enforces
elsewhere); deterministic bytes (hash-stable across runs); listed in
provenance + SHA256SUMS.

**Schema:** provenance additive keys.

**Limits:** emitter output size bounded by existing track/region/section
ceilings; explicit byte cap with refusal test.

**Tests:** RPP contains only relative names; byte-determinism for identical
input; MIDI tempo events match `core/studio_tempo.py`'s map exactly (960 TPQ
conversion proven); malformed/empty tempo map refuses; package hash list
covers the new files.

**Size/risk:** **M, low-medium.**

---

### F9 — Windows edited Studio export

**Outcome:** Windows users get the full edited export — arrangement, fades,
comps, sections, master processing — instead of the aligned-originals
fallback.

**Modules:** `core/studio_export.py` secure-directory layer (`:70–106` and
the descriptor-relative operations behind `:1017,1766`); a Windows-safe
equivalent of the dir-fd strategy; platform gate rework; docs.

**Invariants stressed:** the entire point of the gate is anti-substitution
(descriptor-relative operations defeat directory swaps mid-export). A Windows
design must prove equivalent protection (handle-based `os.open` +
`O_OBSCURE`d rename semantics differ meaningfully); "no loss on
finalization" and atomic single-folder publication must survive. The
brief rates this high care — agreed.

**Schema:** none.

**Limits:** unchanged export ceilings.

**Tests:** unit-testable seams for the directory strategy; fault-injected
substitution attempts refuse; the fallback path remains fully intact for
still-unsupported runtimes. **Physical Windows observation: NOT RUN** (no
Windows host in this environment) — ships with that row explicit.

**Size/risk:** **L, high.** High user value for Windows bands; this machine
is macOS-first, and it cannot be physically observed here.

---

### F10 — Deliberate alternate-rate capture policy (44.1/96 kHz)

**Outcome:** An interface that cannot open at 48 kHz can still record Local
Originals — captured at its native rate, declared in the frozen recording
plan, and rendered onto the 48 kHz timeline through the already-certified
deterministic linear-affine conversion.

**Modules:** `core/local_capture.py` (preflight `:440–449`, capture class
`:586`), `core/session_recording_plan.py` (the plan freezes the rate; the
finalization recheck refuses a mid-take change),
`core/take_library.py` (`require_48k` relaxation to "48k timeline, declared
per-segment rates"), readiness sheet UI.

**Invariants stressed:** fail-closed/never-guess is the whole feature — the
alternate rate must be an explicit, plan-bound declaration, never a silent
fallback when 48 kHz fails; manifests already record per-segment
`sample_rate`, and the export path already handles conversion with evidence
(`"deterministic-linear-affine-v1"`). Alignment tolerance certification must
be re-argued at 44.1 kHz anchors.

**Schema:** recording-plan payload gains the declared rate; manifest schema
unchanged (per-segment rate already exists).

**Limits:** allowed-rate allowlist (44_100, 48_000, 88_200, 96_000), not
free-form.

**Tests:** plan freeze/recheck refuses rate deltas; non-allowlisted rate
refuses; 44.1 kHz capture → aligned export evidence marks resampling;
timeline math property tests across rates.

**Size/risk:** **L, high** (touches the recording path's most defended
invariants). **[recheck at 076c2f9]** — the missing proof lab certainly
covers this path, and its matrix would need extension.

## 3. Sequencing — v0.27 → v0.28

**v0.27 wave 1 — no schema changes, independent, land in any order:**
- **F1 Operable alignment** (recommended first slice, §5)
- **F3 LUFS/true-peak evidence**

**v0.27 wave 2 — the one completed-take sidecar schema evolution:**
- **F2 Per-region gain** and **F4 Comp boundary shaping** ride a **single
  coordinated schema-version bump** for completed-take documents (strict
  parsers force it; two separate bumps would be gratuitous churn). F2 lands
  first (establishes the versioned-field + migrate-in-memory pattern for v2
  documents), F4 immediately after. **Schema-bump callout:** this is the only
  v0.27 item that touches `.webjam-studio-state.json` compatibility; the
  migration story (old documents load with defaults, bytes preserved until
  explicit save; old builds refuse new documents) must be written into
  RECORDING_AND_STUDIO.md in the same commit.

**v0.27 wave 3 — after wave 1:**
- **F5 FLAC/MP3 Studio delivery** — after F3, so loudness evidence covers the
  new deliverables from day one (soft dependency).

**v0.28:**
- **F7 Tempo snapping** (independent; earliest v0.28 or late v0.27 stretch)
- **F8 RPP + MIDI tempo** — after F5 settles the package layout it must
  describe (hard-ish dependency on final artifact naming)
- **F6 Batch clipboard in completed-take Studio** — contract change; benefits
  from the F2/F4-era regression suite around region identity
- **F9 Windows edited export** — independent, large, needs a physical Windows
  observation plan
- **F10 Alternate-rate capture** — last; it touches the recording plan and
  must be re-verified against the missing v0.27 proof-lab delta if that
  arrives (its matrix and `matrix_sha256` would change)

Nothing in v0.27 forces a take-manifest, provenance, or recording-plan schema
change. The single sidecar bump is confined to wave 2.

## 4. Explicit non-goals (recommend rejecting)

- **Time-stretch / pitch shift / elastic audio.** The affine-map hook renders
  drift-magnitude ratios through linear interpolation; musical ratios need a
  windowed-sinc or phase-vocoder resampler — a native dependency
  (`requirements-lock/` hashed per-target locks make that a packaging event,
  not a pip install) and a quality bar unmeetable this cycle. The model hook
  stays reserved for drift. Revisit only with a concrete resampler decision.
- **AAF/OMF interchange.** Binary formats with poor open tooling and heavy
  dependencies; F8's RPP + MIDI tempo + existing CSV serves the actual
  workflow (get stems into a DAW with placement and tempo intact). Rejected,
  not deferred.
- **Spectral waveform / clip-phase visualization.** Real diagnostic value,
  but dropout/overload flags already cover the evidence need; heavy
  DSP + tile-cache work for a review surface. Defer beyond v0.28.
- **Loudness normalization at export** (auto-gain to −14/−16 LUFS). Ship
  measurement (F3) first; normalization mutates delivery and needs a
  true-peak-limited gain stage designed against "export never rewrites the
  original take". Deliberately sequenced behind F3's field experience.
- **AAC delivery.** Patent/licensing overhead the MP3 adapter protocol was
  designed to avoid absorbing; FLAC + gated MP3 covers delivery. Rejected.
- **Tapping meeting apps, browsers, or system output.** Contractually
  excluded (`RECORDING_AND_STUDIO.md:14–20`). Permanent non-goal; restated
  here so no capture feature drifts toward it.
- **Changing Jamulus device/rate/buffer behavior in any candidate.** F10
  explicitly scopes to WebJam's separate Local Originals path only.

## 5. Recommended first slice: F1 — Operable alignment

The argument, without hedging:

1. **It repairs the worst failure mode a band actually hits.** A hot region
   (F2) is an annoyance with existing workarounds (fader, automation). A take
   whose guest original sits "unverified — waiting" with no explanation and
   no recourse is a **lost session** — the product's core promise broken at
   the exact moment it mattered. Converting that dead end into "here is why,
   in plain words; nudge it by ear if you know better; export still tells the
   truth" is the highest-leverage single change available.
2. **The codebase already agreed to it.** The field exists, the guarded
   constructor exists, the atomic manifest CAS exists, evidence already
   reports `manual_nudge_s`, and the behavioral contract already specifies
   the safety boundary (`RECORDING_AND_STUDIO.md:144–145`). No schema bump,
   no new dependency, no new ceiling category. This is the rare feature where
   the invariant-heavy work is already done and tested at the core layer.
3. **Risk is genuinely low, not rated-down.** The only novel surface is a
   controller command + inspector UI + refusal-reason text, each testable
   fail-closed in the repo's existing style. The blast radius of a bug is a
   wrong *displayed* number — never wrong audio, never wrong evidence,
   because the export gate is untouched by design.
4. **It fits a distracted musician mid-rehearsal.** One visible reason
   sentence, one nudge control, no manual. That is the product bar the brief
   sets.

Phase 2 for F1 must open by pinning the one real design decision: the write
path for a Studio-surfaced nudge is the recording-pipeline-owned manifest CAS
(`replace_take_project_manifest_if_unchanged`) with its recheck set specified
— never a Studio-sidecar shadow copy, which would fork alignment truth
between the manifest and the arrangement.
