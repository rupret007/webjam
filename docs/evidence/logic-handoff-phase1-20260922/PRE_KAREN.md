# PRE_KAREN — Logic handoff Phase 1

Scope: `WEBJAM_LOGIC_HANDOFF_P1_20260922`. Keep OPEN DRAFT for Karen and Jeff feel.
Branch: `codex/logic-handoff-phase1-20260922` → `master`.
Exact base after `git pull --ff-only origin master`:
`a51154e533ce5662ca4b541a866dd9b1954452b1`.

The PR body is the final tip-SHA and hosted-CI ledger. Hosted CI is pending until
that body supplies successful runs for the actual tip; local results cannot
substitute for hosted green.

## What the musician receives

**Jamulus band-together → Play along (Music) → Paint along (Art) → Record along
into Logic** is the product arc. This PR is the Record-along star: **SMF Type 1
MIDI plus aligned stems** for Jeff's Logic / Front Stage direction and compatible
DAWs/apps. No deep Front Stage integration is included.

`python -m tools.logic_handoff --stub` creates a four-second synthetic session and
writes a fresh folder under `~/Music/WebJam/LogicHandoff/<session>-<UTC timestamp>-<unique suffix>/`.
The tested pack contains mono bass and stereo melody WAVs, each **24-bit PCM,
48000 Hz, 192000 frames**; a **Type 1 SMF** with eight supplied pitched notes;
`tempo.json`; and `README.md` with SR, BPM, time signature, marker positions,
length and import steps. All files use the same session origin.

`--session session.json` exports explicit stopped local sources with supplied
frame offsets, notes and markers. Sources must already use one declared Logic
project rate (44100/48000/88200/96000/176400/192000 Hz). Actual headers are checked
before output creation; mixed SR or a project-SR mismatch fails closed. No
resampling or discarded tail is allowed. Output WAVs preserve mono/stereo and
pad to one exact frame length. No source bytes are edited.

MIDI stores one constant tempo (5–990 BPM) as integer microseconds per quarter.
Every track has a common end tick at 32760 PPQ; frame timestamps round to the
nearest MIDI tick. Requested/effective BPM, exact frame endpoint, MIDI endpoint,
and the half-tick timing bound are explicit in `tempo.json`. No note performance
is fabricated when the caller supplies no MIDI: the README says so and the SMF
contains its tempo/markers plus an empty track. Instruments must be assigned in
Logic. BWF/iXML is deferred; tempo.json and README carry the requested notes.

## Why a standalone writer

Master's `export_logic_package` is a compatibility alias for audio-only,
editor-neutral export. The schema-v2 take renderer can resample; the new writer
must reject mixed SR. There is no product musical-MIDI recording model to consume.
This implementation therefore adds an explicit core service and CLI, with the
manifest schema and invocation documented in `docs/LOGIC_HANDOFF_PHASE1.md`.

This is the requested smallest honest writer, not a Studio button integration.
It does not interpret TakeProject completeness/alignment gates, region edits,
latency, or source selection; the caller must supply stopped, aligned sources.
Unknown manifest fields (including unsupported tempo maps) are rejected.
No arrange-toolbar, Art companion, or #149 file is changed. No MATCH/merge of
#145–#148, phone transport, MCU, BlackHole, Logic Remote, or Phase 2 mute work.

## Local proof

- **164 passed**: 76 core writer cases, 48 CLI cases, and 40 existing take-export
  and Studio-tempo regression cases. Retained summary: `local-tests.txt`.
- Independent binary MIDI parser checks actual Type 1 header/chunks, conductor
  tempo/signature/marker events, pitched notes, event ordering, legal long-rest
  VLQ splitting, and identical track endpoints. It does not call writer helpers
  to decode the result.
- Actual PCM24 samples, mono/stereo preservation, positive offsets, block-boundary
  padding, source checksums and exact frame counts verified with audio readers.
- All six supported rates and both BPM boundaries pass. Mixed SR, invalid timing,
  unsupported data, ambiguous same-pitch notes, nonfinite/out-of-range samples,
  modified sources, and failed WAV/metadata writes leave no published pack.
- CLI subprocess proof rejects sockets, child processes and audio/device/UI
  imports while exporting the stub. Temporary source cleanup is verified on
  success and failure. Manifest errors fail closed with an actionable exit status.
- Ruff, compileall, pip dependency consistency, runtime dependency policy, UX
  smoke, and whitespace checks pass. No dependency or workflow changes.
- Real default-path stub receipt, output hashes, metadata and independent native
  `afinfo` / `file` inspection: `stub-pack.json`.
- Independent agent code review found no critical format defect. Its supported
  Logic-rate/tempo-range concern was addressed before this candidate was frozen.
  Initial test-only lint issues were corrected; no failing assertion was waived.

## Logic cold import

Logic Pro **12.3.1**, actual fresh Untitled project: **PASS** for the inspected
four-second stub. Bass and Melody WAVs occupy separate audio tracks; Melody MIDI
occupies a third track. All three regions report **starts at bar 1, ends at bar 3**.
The project reports **48 kHz**, **120 BPM**, **4/4**; Start and Middle markers are
visible. Screenshots and the exact observation receipt are retained alongside
this document. Playback/listening was not performed and is not claimed.

Opening Logic showed a one-session fallback from an unavailable audio interface
to the previously selected Mac mini Speakers; no global settings were edited.
The UI import required creating audio tracks; an initial same-track overlap was
corrected before final acceptance by moving Melody to its own track at bar 1.
No user project was opened or edited.

Logic added LGWV analysis chunks to the imported output WAV containers. A strict
whole-file reproduction comparison exposed that difference; independent PCM
comparison proved the samples, channels, sample width, rate and frame count were
unchanged. Fresh final-source WAVs match the original pre-import hashes exactly,
as do MIDI and tempo.json. Before/after hashes are retained in stub-pack.json.
This is observed importer metadata behavior, not an exporter change to its input
recordings. The generated README was refreshed with Jeff's north star and the
SMF Type 1 MIDI headline.

## Remaining review boundaries

This Phase 1 path is CLI/API only, supports constant tempo, and accepts explicit
note events (no controllers, program changes, SysEx, live capture, transcription,
or tempo automation). It creates interchange files rather than a `.logicx`
project. Atomic staging hides incomplete packs during ordinary write failures;
no power-loss durability or hostile-directory race guarantee is claimed.

Jeff listening/feel and independent Karen review remain separate from synthetic
file checks. No merge, release, tag, deployment or signing was performed. Hosted
CI must pass on the exact final tip before completion can be claimed.
