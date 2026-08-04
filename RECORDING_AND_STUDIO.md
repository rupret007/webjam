# Recording and Studio — v0.22.3

> **Published private test candidate:** this guide describes immutable v0.22.3,
> now GitHub Latest. Physical
> recording, playback, recovery, long-session, and external-editor gates remain
> **NOT RUN** until exact package evidence is recorded.

## Recording is separate from live music

Jamulus owns the live interface and mix. WebJam’s optional Local Originals are
a separate capture path for this Mac’s first two interface inputs. They are not
a prerequisite for a jam and they do not change Jamulus settings.

When the host presses **Record** for the first time, WebJam asks:

- **Record Shared Jam Only** — start the synchronized host take now.
- **Also Keep This Mac’s Inputs** — open Recording Setup and explicitly choose
  a valid two-channel Local Originals input before recording.

The host keeps shared recording authority. Guests can opt into Local Originals
only when the active private session supports them; they are never interrupted
while joining music.

## Recording readiness

WebJam checks takes storage, recorder control, the known roster, and any
explicit local-capture setting when Record is requested. It does not perform
those checks during Host or Join. A failed record preflight preserves the live
jam and explains the next safe action.

## Guest Local Originals

A guest's Local Original is preserved as soon as WebJam has verified its
transfer. That confirms the file arrived intact; it does not by itself put the
recording on the host take's timeline.

WebJam marks a guest original ready for an aligned export only after it finds
that same musician's verified Jamulus server reference and the recordings pass
a strict timing check. WebJam records which reference it used and checks that
reference is still intact when exporting. If the reference is missing or
changed, the capture has gaps, or the timing evidence is inconclusive, the
original stays available in Studio with waiting or unverified timing evidence.
It is kept for listening and manual review, but a selected aligned export waits
until it is verified or deliberately deselected rather than guessing where it
belongs. A manual nudge alone cannot turn an uncertain guest original into an
export-ready one.

This is software evidence, not a claim that two physical systems were sample
synchronized or that an external editor has been tested.

## Studio

Studio opens from the direct **Studio** action or Cmd/Ctrl+3; it is intentionally
absent from More so there is one obvious route rather than a duplicate entry. It
is a Logic-like review workspace, not a Logic integration. Open a completed or
explicitly recovered schema-v2 take to use its multitrack review and Arrange
workspace.

### Arrange and mix

Studio keeps edits in project frames and durable take, track, and segment IDs.
It does not store source file paths in the arrangement. The timeline supports:

- moving and edge-trimming regions by drag;
- split, duplicate, disable/enable, and delete actions for a selected region;
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

The familiar Undo/Redo shortcuts restore exact immutable snapshots, including
durable IDs. Adjacent continuous-control changes are coalesced into a useful
single undo step, and history is bounded by both entry count and serialized
size. A new edit after Undo clears the abandoned redo branch.

Arrange is operable without a mouse: Arrow keys move through track/take rows
and regions, Alt+Left/Right nudges the selected region, and Ctrl+Left/Right
Bracket trims its start/end to the playhead. Ctrl+Alt+A auditions the selected
take lane, Ctrl+Alt+C comps its selected region, and Ctrl+Alt+Left/Right moves
the named section at the playhead. The accessible timeline description reports
the current track, lane, region, frame range, snap state, and audition state.

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
unambiguous matching musician at the same project sample rate. Each source is
bound by its complete take/track/segment identity; a similarly named or reused
segment ID from another take is not interchangeable.

Double-click a take-lane name, or use **Audition**, to hear that lane where it
has recorded media without changing the saved comp. Option/Alt-drag a lane to
select a comp range. A newer range cleanly splits prior overlapping selections
and uses short equal-power boundaries. Removing a lane removes only its Studio
inventory and comp choices; the repeated take remains unchanged in Takes.

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

## v0.22.3 evidence boundary

Automated source tests cover the arrangement model, persistence/recovery,
history/controller behavior, renderer, comping, source catalog, waveform
pipeline, export transaction, and headless Qt interactions. Those checks do not
prove that musicians heard the result through physical interfaces.

For the published v0.22.3 private test candidate, real two-Mac listening,
interface disconnect/reconnect, sleep/wake, interruption and long-recording
recovery, Studio playback through physical outputs, external-editor import of
an exported package, signed clean installation, and platform
trust/notarization remain **NOT RUN**. Record any later result against the exact
asset name, build ID, and SHA-256; publishing the candidate did not convert
those physical or credentialed gates into PASS.
