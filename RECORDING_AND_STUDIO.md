# Recording and Studio — v0.26.0

> This document describes immutable v0.26.0, the GitHub **Latest** private test
> release. Use only exact checksum-verified release assets. Physical recording,
> Shared Track audibility/isolation, playback, recovery, long-session, and
> external-editor gates remain **NOT RUN** until separately observed against
> those packages.

Music and Podcast & Voice are GA creator profiles with live recording,
completed-take editing/mixing/export, and standalone local projects. Review &
Rehearsal is Preview: it allows live WebJam-audio Record Session and
playback/read-only review of a completed session take. It blocks standalone
projects, take editing/comp/mix mutation, track export, shared notes, visual
sync, and media timecode. No profile directly or automatically taps a meeting
app, browser, or system output. Scratchpads are profile-scoped
on one computer and are never shared, session-synchronized, or media-timecoded.

WebJam never directly or automatically taps a meeting app, browser, or system
output. Local Originals capture only the input devices a user explicitly
selects, so do not route meeting or system audio into those inputs.

## Recording is separate from live audio

Jamulus owns the live interface and mix. WebJam’s optional Local Originals are
a separate capture path for named mono/stereo tracks totaling up to 32 enabled
input channels. Recording Setup allocates enabled Local-Original tracks to
device channels sequentially; an empty configuration preserves the compatible
two-mono-input default. One mono row creates one mono PCM-24 WAV; one stereo row
binds adjacent channels into one true two-channel PCM-24 WAV. An all-opted-out
map creates no host Local Original. These logical identities survive recovery,
Studio, gaps, and export. They are
not a prerequisite for a live session and they do not change Jamulus settings.

When the host presses **Record Session** for the first time, WebJam asks:

- Use the profile's shared-take action — **Record Shared Jam Only**, **Record
  Shared Voice Take Only**, or **Record Shared WebJam Audio Only** — to start
  the synchronized host take without Local Originals.
- **Also Keep This Mac’s Inputs** — open Recording Setup, choose a valid input,
  and review or edit the named Local Original tracks before recording.

The host keeps shared recording authority. Guests can opt into Local Originals
only when the active private session supports them; they are never interrupted
while joining live audio.

## Recording readiness

WebJam checks takes storage, recorder control, the known roster, and any
explicit local-capture setting when Record Session is requested. It does not
perform those checks during Host or Join. A failed record preflight preserves
the live session and explains the next safe action.

Before capture begins, one durable take-scoped recording plan binds the exact
roster/server stems, Shared Track source fingerprint and playback generation,
host logical mono/stereo topology, guest Local Original count/map obligations
and presence generations, count-in/pre-roll, storage verdict, and expected
source count. Finalization rechecks those exact facts. A reconnect, changed
topology, missing/extra source, or substituted Shared Track cannot be accepted
as the planned take.

WebJam v0.26.0 shows this frozen plan in one accessible,
path-free **Record Session Readiness** sheet before any recorder, local input
stream, or Shared Track playback is armed. Each exact server, Local Original,
and Shared Track row identifies the participant/source, mono or stereo format,
required/optional obligation, readiness, and a bounded meter when available.
Separate cards report storage and Shared Track readiness; explicit blockers
disable **Start Recording**. Accepting the sheet causes a second private check
of the take/plan generation, roster, input maps, guest obligations, local device
preflight, storage, and Shared Track identity. A changed fact fails closed;
Cancel retires the provisional take without starting capture or creating
media.

An opted-in guest is not treated as armed merely because its recent presence
proof was Ready. After acceptance, the host sends that required participant a
private, take-scoped arm bound to the immutable plan fingerprint and exact
mono/stereo source IDs. The guest opens the frozen input stream first and sends
an authenticated acknowledgement only after the device succeeds. Jamulus
recording cannot start until every required acknowledgement is current; guests
with zero planned tracks do not block. Timeout, disconnect, stale generation,
device failure, or any topology mismatch cancels the arm and starts no server
recorder. After all acknowledgements, the host repeats the full authority
check before opening its own capture and starting Jamulus recording.
An acknowledged guest that cannot yet prove whether the host committed the
take preserves its audio locally as recovery-only media. It is not uploaded on
shutdown uncertainty; only the same authenticated take reaching Recording or a
terminal state can promote it into the transfer queue.

Every new source has one stable logical-source ID across the frozen plan,
capture track, guest transfer receipt, take manifest, recovery catalog, and
Studio. Server, host, and guest widths remain ordered and exact—one mono
channel or one stereo pair. A missing or duplicate ID, absent width, changed
map, or extra/missing delivery cannot fall back to a display name, track order,
or invented identity.

The live surface presents one authoritative progression:

| State | Creator meaning |
| --- | --- |
| Idle | No recording generation owns the session |
| Preparing | Storage, recorder, roster, and optional local input are being checked |
| Count-in | The confirmed generation is counting the session in before captured performance |
| Recording | The shared take is actively being captured |
| Stopping | New capture is blocked while active owners stop |
| Finalizing | Stems, timing, manifests, checksums, and publication are being verified |
| Ready | The immutable take is available to Studio |
| Needs attention | Media was preserved, but the shown recovery must complete before success |
| Cleanup pending | An owned process or route could not yet be proved retired; retry Stop |

A duplicate Record request cannot replace a generation that is preparing,
recording, stopping, or finalizing. **Stop Recording** is one host action,
but recorder and Shared Track teardown remain independently proved owners; a
clean result for one does not manufacture success for the other.

## Shared Track in a recorded take

When a Shared Track is loaded and route-ready, confirmed recorder start owns
its count-in/play transition. The route still enters Jamulus through the
separately owned `WebJam Track` participant; WebJam does not mix a direct local
copy into each participant's output. The server recorder's authoritative track is
classified as **Shared Track** in Studio and keeps a stable source identity
across takes. It is not labeled as a participant or Local Original.

The imported song remains immutable. Reconstructing the Studio source from it
is allowed only through the recorded timing contract; if the authoritative
stem, generation, boundaries, or alignment evidence are missing or ambiguous,
WebJam preserves what it has and reports the problem instead of creating a
plausible-looking duplicate. A roster row or waveform is never audibility
proof.

## Guest Local Originals

A guest's Local Original is preserved as soon as WebJam has verified its
transfer. That confirms the file arrived intact; it does not by itself put the
recording on the host take's timeline.

WebJam marks a guest original ready for an aligned export only after it finds
that same participant's verified Jamulus server reference and the recordings pass
a strict timing check. WebJam records which reference it used and checks that
reference is still intact when exporting. If the reference is missing or
changed, the capture has gaps, or the timing evidence is inconclusive, the
original stays available in Studio with waiting or unverified timing evidence.
It is kept for listening and manual review, but a selected aligned export waits
until it is verified or deliberately deselected rather than guessing where it
belongs. A manual nudge alone cannot turn an uncertain guest original into an
export-ready one.

After capture stops, authenticated guest state moves to **Finalizing** rather
than treating recorder stop as publication. Only the later host **Ready** state
establishes a completed take, and Ready does not turn an outstanding guest
transfer into aligned media. Studio and export continue to show that original
as waiting or unverified until its own receipt and timing contract pass.

This is software evidence, not a claim that two physical systems were sample
synchronized or that an external editor has been tested.

## Studio

Studio opens from the direct **Studio** action or Cmd/Ctrl+3; it is intentionally
absent from More so there is one obvious route rather than a duplicate entry.
It uses familiar professional DAW interactions without copying Apple artwork,
icons, exact layouts, or trade dress, and it is not a Logic integration. Open a
completed or explicitly recovered schema-v2 take to use its multitrack review
and Arrange workspace. The track list distinguishes participant, Shared Track,
and Local Original sources before mixing or editing begins.

While an exact recording plan owns the live take, Studio can also show one
plan-bound lane per Jamulus server source, Local Original, and Shared Track.
Rows carry source state, current level when available, reported dropouts, and
overload warning. Participant cards are derived only from real server sources;
Local Originals and Shared Track never masquerade as people. Any malformed,
legacy, or duplicate logical-source projection clears the complete live source
set instead of displaying ambiguous recorder truth.

The Arrange, comp, mix-mutation, sidecar, and export behavior below applies only
to Music and Podcast & Voice. Review & Rehearsal Preview retains playback,
scrubbing, and source inspection, but disables those mutation/export paths and
does not create or load a Studio sidecar.

### Arrange and mix

Studio keeps edits in project frames and durable take, track, and segment IDs.
It does not store source file paths in the arrangement. The timeline supports:

- moving and edge-trimming regions by drag;
- split, duplicate, disable/enable, and delete actions for a selected region;
- visible multi-region selection with Shift-click or Ctrl/Cmd-click; completed-
  take Studio keeps edit actions single-region, while Reference Studio song
  projects add **Select All** and one-step Cut/Copy/Paste;
- per-region fades, validated same-track crossfades, markers/sections,
  cycle/loop playback ranges, and time/marker snapping;
- track trim, fader, pan, mute, solo, and export-inclusion choices;
- master gain and deterministic limiter delivery behavior;
- zoom, scroll, scrub, and a shared playhead across the arrangement.

Select a region and choose **＋ Section** to name a Verse, Chorus, or other song
block. Drag that named section bar in the ruler to move the whole block earlier
or later across every track. Studio performs one undoable ripple edit, splits
regions at the move seams without changing their source mapping, moves contained
arrangement choices with the block, reloads playback, and refuses the move
atomically when an interval cannot cross a seam safely. Source recordings and
existing tombstones remain unchanged.

While dragging, the section bar snaps to the other blocks' edges — its start to
another section's start to land in front of that block, or its end to that
block's end to land right behind it — so a drop between blocks needs no
precision; away from every edge, free placement and the general snap mode still
apply. A snap target the reorder itself would refuse is never offered.

Right-click a section bar, or press Ctrl+Alt+D or Ctrl+Alt+Backspace with the
playhead inside a section, to **Duplicate** or **Remove** that block across
every track as one undoable edit. Duplicate inserts a copy immediately after
the original and copies everything provably inside the block — region
fragments, point markers, nested sections, comp choices, and crossfades — under
new durable IDs. Remove closes the gap and records the block's contents as
tombstones at their original position; a cycle range inside a removed block is
cleared. Both refuse atomically when an active interval straddles a section
edge, and neither ever changes a source recording. Ctrl+Alt+Right lands the
playhead section cleanly behind a longer following block by aligning the two
blocks' ends.

In a **Reference Studio song project**, Shift-click or Ctrl/Cmd-click regions to
build a selection, or press **Select All** (⌘/Ctrl+A) to select every active
region. Cut, Copy, Paste, and Delete then act on the whole selection as one
undoable edit: Paste lands the earliest copied region at the playhead and
preserves every other copy's exact relative offset, so a multi-track phrase
pastes as one phrase. A copied region whose destination track no longer exists
fails the paste closed rather than inventing a track. Copies are new durable
IDs; source recordings and tombstones are never rewritten. Completed-take
Studio does not expose these batch clipboard commands.

The familiar Undo/Redo shortcuts restore exact immutable snapshots, including
durable IDs. Adjacent continuous-control changes are coalesced into a useful
single undo step, and history is bounded by both entry count and serialized
size. A new edit after Undo clears the abandoned redo branch.

Arrange is operable without a mouse: Arrow keys move through track/take rows
and regions, Alt+Left/Right nudges the selected region, and Ctrl+Left/Right
Bracket trims its start/end to the playhead. Ctrl+Alt+A auditions the selected
take lane, Ctrl+Alt+C comps its selected region, Ctrl+Alt+Left/Right moves the
named section at the playhead, and Ctrl+Alt+D / Ctrl+Alt+Backspace duplicate
or remove it. The accessible timeline description reports the current track,
lane, region, frame range, snap state, and audition state.

**Cycle Region** loops the selected range on exact project frames, including
when a loop boundary falls inside an output-device block. For cycles of four
frames or more, playback applies a deterministic short seam fade, including
for blocks spanning multiple wraps, without changing the exact transport frame
count. One- through three-frame pathological cycles stay sample-exact and
non-silent instead of being faded into silence, so their raw seam is not
de-clicked. Click-free playback through a named physical interface remains a
separate **NOT RUN** observation.

### Repeated takes and comping

Select a destination track and choose **＋ Add Take** to use another complete
or explicitly recovered recording from the same session. Studio offers only an
unambiguous matching participant at the same project sample rate. Each source is
bound by its complete take/track/segment identity; a similarly named or reused
segment ID from another take is not interchangeable.

Double-click a take-lane name, or use **Audition**, to hear that lane where it
has recorded media without changing the saved comp. Option/Alt-drag a lane to
select a comp range. A newer range cleanly splits prior overlapping selections
and uses short equal-power boundaries. Removing a lane removes only its Studio
inventory and comp choices; the repeated take remains unchanged in Takes.

For a newly completed editable Music or Podcast & Voice take, v0.26.0 also
stacks every provably safe earlier counterpart automatically. The gate requires
the same session and project sample rate, a different complete or explicitly
recovered take, one unique stable logical-source ID on each side, matching
participant and source kind, identical mono/stereo topology, verified timing,
and the same Shared Track fingerprint where applicable. Lane IDs are
deterministic, so repeating the operation is idempotent. A legacy identity,
duplicate ID, uncertain timing, topology mismatch, or ambiguous match produces
no automatic edit; the conservative manual browser remains available where its
own evidence gate permits. Review & Rehearsal Preview never runs automatic
stacking or creates the arrangement sidecar it would require.

### Podcast & Voice episode journey

Podcast & Voice's standalone Reference Studio path defaults to 48 kHz with a
mono Host track and a true-stereo Guest track. The v0.26 journey preserves
those channel maps through an initial recording and a cycle/loop overdub,
stores chapter markers as named section markers, and restores the same chapter,
track topology, recording ledger, and arrangement after save/reopen. **Bounce
Episode** publishes a verified two-channel PCM-24 WAV and reports its checksum.
It does not flatten the Guest capture into a mono source or rewrite either
recording pass.

Review & Rehearsal remains a completed-session-take Preview, not a local
episode editor. It refuses local project create/open and blocks arrangement
edits, comping, mixer/automation mutation, save, bounce, and export at the
controller boundary even if a caller bypasses disabled visible actions.

### Reference Studio song-project overdub loop recording

This flow is available only in a Reference Studio song project; completed-take
Studio remains a review, arrangement, mix, and export surface. Turn on
**Overdub** in the Transport menu (shortcut **O**) or the transport bar to
loop-record over a chosen range. First set a loop: select a region and use
**Region ▸ Loop Selected Region**, or drag a cycle range. With Overdub on,
**Record** then loops that range and lands each complete pass in its own new
take lane, with no pass-count dialog — press **Stop** when you have enough. Comp
the result exactly like any repeated take: Option/Alt-drag a lane to pick the
keeper range, or use **Region ▸ Quick-Swipe Comp**. Overdub uses the same
sample-accurate, crash-safe project recorder and take-lane commit as a manual
cycle take, so every pass carries the same durable evidence; nothing about the
underlying recording identity or non-destructive boundary changes. With Overdub
on but no loop set, Record explains how to set one instead of recording a
straight punch. Two-endpoint physical overdub monitoring remains a **NOT RUN**
observation.

### Waveforms

Arrange waveforms are overview evidence, not rewritten audio. Studio requests
only fixed-grid tiles intersecting the visible viewport, publishes them
progressively, and keeps a bounded entry/byte cache. Declared capture gaps and
missing timeline spans draw as silence. Mix-only edits reuse valid peaks;
changing source identity invalidates them. Switching takes or shutting down
cancels stale work so a late worker cannot paint into the current take.

Waveform reads recheck regular-file identity and declared checksum and reject
symbolic-link substitution. A waveform failure leaves the recording untouched
and does not turn uncertain media into trusted export input.

A deterministic sparse 12-track/60-minute workspace stress gate passes
load/edit/save/reopen, compact Arrange zoom/viewport, bounded waveform
scheduling, bounded-block export cancellation/cleanup, and unchanged source
hashes. It does not play a real hour-long session; physical long-session
operation remains **NOT RUN**.

### Autosave and recovery

Edits autosave to `.webjam-studio-state.json`, a separate schema-v2 sidecar.
The save uses an exact-byte token, process and cross-process locking, an atomic
replacement, and a last-known-good backup. Multiple quick edits coalesce onto
one pending save. Undo and Redo are saved just like any other arrangement
change.

A disk error or competing writer keeps the in-memory edit dirty and retryable;
Studio will not silently overwrite the other writer or discard unsaved work
when switching takes or quitting WebJam. If the final synchronous save retry
fails, close is refused with path-free guidance while the document remains
dirty and usable. A damaged primary can be opened from its valid backup with a
visible recovery notice. Older schema-v1 mixer choices migrate in memory only,
and their exact original bytes are preserved on the first explicit schema-v2
save.

### Playback and evidence-rich export

Playback and export use the same arrangement renderer for frame conversion,
regions, fades/crossfades, comp selections, recorded gaps, mix state, and master
delivery. Playback output remains a Studio-review choice and never changes the
Jamulus live-music configuration.

Source checksum validation and descriptor preparation run on a cancellable
background worker. The audio callback uses only preopened readers and bounded
DSP—no pathname open/stat/fstat work—and a source read failure latches one safe
terminal error for the UI thread to drain. Switching takes or editing while
preparation is pending retires the stale generation before it can start output.

Export publishes one complete folder atomically. Selected tracks receive
equal-length 24-bit edited stems and aligned-unity original stems, plus a rough
mix. The package also contains markers/sections CSV, import instructions, the
exact Studio document, the source take manifest for every rendered take,
`provenance.json`, and `SHA256SUMS.txt`. Provenance identifies selected tracks,
source keys and hashes, timeline rate/length, output hashes and clipping counts,
and records external-editor validation as `NOT RUN` until it is separately
performed.

Export rechecks the source media, take manifests, source catalog, and saved
Studio state before publication. A changed, missing, ambiguous, disabled, or
unverified source fails closed. Cancellation and other pre-publication failures
remove the temporary package; they do not leave a partial export presented as
complete.

Edited Studio-package publication is supported on macOS and Linux only when
the runtime provides the required descriptor-relative directory APIs. On
Windows or another unsupported runtime, Studio instead labels the action
**Export Aligned Originals** and asks for confirmation when the saved Studio
document differs from its default. That path exports unity aligned originals
and a reference rough mix using current trim, fader, pan, mute, and solo
choices. It explicitly excludes arrangement edits, region fades, comp choices,
song sections, master gain/limiter processing, and recordings attached as
repeated take lanes; export those recordings from their own takes. A failure
after an edited Studio export begins never silently falls back to aligned
originals.

## Non-destructive boundary

The take manifest and every source WAV are recording evidence. Studio never
rewrites them during load, editing, comping, undo/redo, autosave, waveform
generation, playback, or export. Arrangement deletes are tombstones in the
sidecar, not media deletion. Local Originals remain separate preserved media
and appear only when real files are available and their evidence permits the
requested operation.

Export never rewrites the original take.

## v0.26.0 private-test-release evidence boundary

Automated source tests can establish state-machine, identity, source
validation, timing-model, persistence/recovery, rendering, waveform, export,
privacy, and headless UI behavior. They cannot establish acoustic audibility,
latency, direct-monitor isolation, interface recovery, or how a packaged build
feels to creators.

For v0.26.0, two-machine audio, Shared Track audibility and independent mix,
count-in/record alignment, authoritative server stems, Local Original transfer,
hardware interruption, long recording, Studio playback, external-editor
import, packaged accessibility, SmartScreen, Gatekeeper, signing, and
notarization remain **NOT RUN**. The
[v0.26 physical checklist](V026_CREATOR_MULTITRACK_PHYSICAL_TEST_CHECKLIST.md)
must be executed only against its named exact package set; this checkout is not
physical evidence.

Immutable v0.25.0 remains historical evidence and keeps its separate
[v0.25 physical checklist](V025_CREATOR_MULTITRACK_PHYSICAL_TEST_CHECKLIST.md).
No v0.26 result may be written into that historical ledger.

The [v0.24 checklist](V024_RECORDING_FIRST_PHYSICAL_TEST_CHECKLIST.md) remains
immutable historical evidence for that earlier release.

## Historical v0.22.4 evidence boundary

Automated source tests cover the arrangement model, persistence/recovery,
history/controller behavior, renderer, comping, source catalog, waveform
pipeline, export transaction, and headless Qt interactions. Those checks do not
prove that participants heard the result through physical interfaces.

For the published v0.22.4 private test candidate, real two-Mac listening,
interface disconnect/reconnect, sleep/wake, interruption and long-recording
recovery, Studio playback through physical outputs, external-editor import of
an exported package, signed clean installation, and platform
trust/notarization remain **NOT RUN**. Record any later result against the exact
asset name, build ID, and SHA-256; publishing the candidate did not convert
those physical or credentialed gates into PASS.
