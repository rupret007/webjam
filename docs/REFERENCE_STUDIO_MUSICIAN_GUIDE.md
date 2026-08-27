# Reference Studio creator guide

Reference Studio is WebJam's standalone space for writing and rehearsing with
a backing track. It can open without a WebJam session, Webex, or Jamulus. Its
local playback and recording choices do not change the device, buffer, mix, or
connection owned by Jamulus.

> **v0.27.1 source candidate guide:** this document describes unpublished
> source. Immutable GitHub **Latest** remains v0.27.0. Always verify an exact
> downloaded asset against its attached checksum manifest before use.

Windows packages are unsigned. Mac packages are ad-hoc signed and unnotarized.
Automated checks do not prove that a particular interface, driver, speaker, or
headphone path is
audible; the physical hardware gates remain **NOT RUN** until recorded against
the exact downloaded package hash.

Music and Podcast & Voice are the GA standalone-project profiles. Podcast &
Voice starts with its Host + Guest or Solo Voice mic preset, 48 kHz, time
ruler, and count-in/metronome off. Review & Rehearsal is Preview and refuses
standalone project create/open; its completed session takes can be played for
read-only review, but not edited, comped, mix-mutated, or exported.

For the v0.26 Podcast & Voice journey, the Host + Guest preset means one mono
Host track and one true-stereo Guest track. Record the first pass, add named
chapter markers, set a cycle for loop overdub, and stop after the alternate
pass. Save/reopen must preserve the chapter, 48 kHz project rate, exact channel
topology, recording evidence, and take lanes. **Bounce Episode** publishes a
verified stereo PCM-24 WAV with its checksum. Review Preview blocks the same
local create/open, edit, mixer/automation, save, bounce, and export entry points
even if a caller bypasses a disabled visible action.

## Start a project

Open **Reference Studio** from WebJam's main rail.

- Choose **Play Along / Record** to name a project and collect a local backing
  track in one flow.
- Choose **New Project** to begin without backing audio.
- Choose **Open Project…** or a recent project to continue saved work.

A project is a folder rather than one opaque audio file. Keep the whole folder
together when copying it to another computer. The visible source manifest and
the hidden arrangement sidecar refer to immutable files inside `Media/`; they
do not store the original file's absolute path.

Importing a backing track or other media makes a checksummed project-owned
copy. WebJam reads the file you selected but never edits it. Do not delete or
replace files manually inside `Media/`. Use **Import Media…**, **Import Backing
Track…**, or **Relink Missing Media…** so WebJam can validate and collect the
bytes.

## Play along

After media preparation finishes, use the transport at the top of the
workspace:

- **Play / Pause**, **Stop**, and **Return to Start** control only local
  Reference Studio playback.
- **Click** follows the project tempo and meter.
- **Cycle** loops the enabled cycle range.
- The snap selector aligns eligible Arrange edits to bars, beats, eighths, or
  sixteenths; choose Off for frame-domain placement.
- Tempo and time signature control the grid and click. They do not stretch,
  resample, or rewrite an imported performance.

Waveforms arrive progressively. A temporarily blank waveform is not evidence
that media was lost; wait for the status to finish preparing the collected
audio. Playback will refuse media whose current size, checksum, format, sample
rate, channels, or frame count no longer matches the project catalog.

Use **Project → Analyze Backing Tempo…** for a starting estimate. The bounded
offline analyzer reports confidence and opens a review screen. Listen, watch
for half-time or double-time results, correct BPM or meter if needed, and only
then apply it. Cancelling leaves the project unchanged.

## Record an idea

1. Create or select an audio track.
2. Choose **Track → Input Mapping…** and select the intended input/channels.
3. Choose **Track → Arm Selected Track**.
4. Set **Count-in**, punch range, cycle range/pass count, and latency
   compensation deliberately.
5. Press **Record**, perform, then stop.

Reference Studio captures through its own local recording backend. It does not
record the Jamulus receive mix and does not send its backing track into
Jamulus. Use headphones and verify your interface routing outside the app
before a consequential take; software tests cannot establish real audibility
or rule out an analog/direct-monitor feedback path.

Recording publication is transactional. Each completed track file is
validated, collected as immutable project media, and represented by
frame-aligned regions and take-lane/pass evidence. Recorded dropout intervals,
input channels, latency compensation, punch bounds, and cycle passes remain
evidence associated with the commit. If capture completed but the project
could not finish publication, WebJam presents an explicit recovery candidate
on the next open. Recover or discard it deliberately; do not assume an
interrupted commit is already part of the arrangement.

Count-in is pre-roll and is not represented as recorded program material.
Punch limits the kept performance range. Cycle recording creates bounded
passes that can be auditioned and comped; it does not destructively overwrite
the prior pass.

## Arrange the song

The center timeline is non-destructive. Regions point to immutable project
media, so move, trim, split, duplicate, fade, disable, or delete operations
change arrangement state rather than source bytes.

- Add markers for navigation.
- Add named sections such as Intro, Verse, Chorus, and Bridge.
- Drag a section bar to move that whole block across tracks as one undoable
  ripple edit. An unsafe boundary is rejected with its reason.
- Use take lanes to audition alternate performances.
- Use quick-swipe comp ranges to build a performance from alternate lanes.
- Use cycle and snap while editing, and Undo/Redo for bounded arrangement
  history.

Save remains important even with autosave. Autosave is recovery evidence, not
a replacement for a confirmed project save. If WebJam finds a newer valid
autosave or a last-known-good backup, it asks which state to use. A save
conflict or failed final save stays visible and can block close rather than
silently discarding the current arrangement.

**File → Save As…** creates a new project identity and copies the exact
collected media, project state, and non-destructive arrangement through a
staged transaction. The original stays unchanged. WebJam refuses a destination
that would alias, nest inside, overwrite, or partially publish another
project.

## Mix and automate

Choose **Mix → Show Mixer** for track fader, pan, mute, solo, shared-reverb
send, master gain, and the safety limiter. The small built-in effect set is
high-pass filter, parametric EQ, compressor, gate, and one shared reverb bus.
The controls are bounded and stored as non-destructive project choices.

Choose **Mix → Show Automation** to add or replace a volume, pan, or mute point
at the current playhead. Volume and pan interpolate between points; mute holds
until its next point. Playback and bounce use the same validated routing,
automation, and built-in DSP graph, so a mix should not silently change merely
because it is being exported.

The built-in effects are practical demo tools, not third-party plug-in hosting.
Reference Studio does not scan or load Audio Units, VST, or AAX plug-ins in
this candidate.

## Bounce a demo

Choose **File → Bounce…**, then select:

- 24-bit WAV or 24-bit FLAC;
- whether the Reference / Backing track is included;
- whether to add one processed stem per track;
- the entire project, enabled cycle range, or selected audio track when
  available.

WebJam first requires the project to save successfully. Bounce runs outside the
UI thread, can be cancelled, reads only through the verified media catalog,
and publishes files outside the project bundle only after verification. A
cancelled or failed bounce removes unpublished staging files.

The completion report includes each filename, SHA-256 checksum, peak dBFS,
clipped-sample count, and deterministic RMS dBFS. RMS is not an integrated-LUFS
mastering measurement. Treat clipping as a reason to lower track, effect, send,
or master gain and bounce again.

MP3 bounce is intentionally absent from the unpublished v0.27.1 source. It appears only
if a separate encoder adapter has passed the product's identity,
output-decoding,
and license-policy self-tests. Use WAV or FLAC for a lossless handoff.

## Reference Studio versus other WebJam audio

| Workflow | Audio owner | Project/recording meaning |
| --- | --- | --- |
| Host/Join | Jamulus | Live low-latency rehearsal; WebJam observes and conducts the private session |
| Session recording and Studio | WebJam recorder plus immutable completed-take evidence | Music and Podcast can review, arrange, comp, and export; Review Preview is limited to playback, scrubbing, and source inspection |
| Standalone Reference Studio | Local Reference Studio playback/recording backend | Write, play along, record local ideas, arrange, mix, and bounce without joining |
| Shared Track live route | Separate `WebJam Track` Jamulus participant | Capability-gated host source; packaged Play stays fail-closed until the Mac proves the isolated route, and physical audibility remains **NOT RUN** |

Do not route Reference Studio into Jamulus by assumption. A future explicit
feature would need to prove ownership, feedback isolation, return-fader state,
and teardown. The standalone workflow makes no such claim.

## v0.22.5 published candidate record

The immutable historical v0.22.5 release adds first-demo reliability to
the v0.22.4 Studio foundation, including real-world MP3 acceptance, exact
path-free load errors, drag-and-drop Reference Track loading, and audible
dropout surfacing. Verify the exact downloaded asset against
`WebJam-v0.22.5-SHA256SUMS.txt`; the complete eight-asset inventory and
publication evidence are in the [desktop release runbook](DESKTOP_RELEASE_RUNBOOK.md).
Windows remains unsigned, both Mac architectures remain ad-hoc signed and
unnotarized, and physical Reference Studio input/output, latency, recovery,
external-editor, hardware, SmartScreen, Gatekeeper, signing, and notarization
gates remain **NOT RUN**.

## Historical v0.22.4 published candidate record

The v0.22.4 source, checksums, signed sequence-5 Jamulus catalog, four-platform
frozen-package gates, and verified promotion passed before publication. Its
exact immutable release inventory is:

- `WebJam-v0.22.4-windows-x64-UNSIGNED-TEST-ONLY-setup.exe`
- `WebJam-windows-x64-UNSIGNED-TEST-ONLY.zip`
- `WebJam-v0.22.4-macos-arm64-ADHOC-TEST-ONLY.dmg`
- `WebJam-macos-arm64-ADHOC-TEST-ONLY.zip`
- `WebJam-v0.22.4-macos-x64-ADHOC-TEST-ONLY.dmg`
- `WebJam-macos-x64-ADHOC-TEST-ONLY.zip`
- `WebJam-linux-x64.zip`
- `WebJam-v0.22.4-SHA256SUMS.txt`

The checksum manifest covers the other seven files, not itself. Windows is
unsigned; both Mac architectures are ad-hoc signed and unnotarized. Physical
Reference Studio input/output, latency, recovery, external-editor, hardware,
SmartScreen, Gatekeeper, signing, and notarization gates remain **NOT RUN**.

## Historical v0.22.2 record

The
[v0.22.2 GitHub release](https://github.com/rupret007/webjam/releases/tag/v0.22.2)
was published as a non-prerelease marked **Latest** at that time and is now
superseded by immutable v0.22.5. Its verified inventory is exactly these seven
packages plus the checksum manifest:

- `WebJam-v0.22.2-windows-x64-UNSIGNED-TEST-ONLY-setup.exe`
- `WebJam-windows-x64-UNSIGNED-TEST-ONLY.zip`
- `WebJam-v0.22.2-macos-arm64-ADHOC-TEST-ONLY.dmg`
- `WebJam-macos-arm64-ADHOC-TEST-ONLY.zip`
- `WebJam-v0.22.2-macos-x64-ADHOC-TEST-ONLY.dmg`
- `WebJam-macos-x64-ADHOC-TEST-ONLY.zip`
- `WebJam-linux-x64.zip`
- `WebJam-v0.22.2-SHA256SUMS.txt`

The manifest contains checksums for the seven packages, not for itself. An
Actions artifact or GitHub draft is engineering evidence, not the Latest
release.

Windows is unsigned. The Mac apps are ad-hoc signed and not Apple-notarized.
Use them only within the private-test boundary. Managed computers can still
block installation through organization policy; do not bypass that policy.

## If something goes wrong

- Stop playback or recording before changing an audio device.
- Read the current Reference Studio status; it distinguishes preparation,
  save, recovery, analysis, bounce, and cancellation outcomes.
- Keep the project folder intact and make a copy before manual inspection.
- Do not edit the JSON manifests or files in `Media/`.
- If a save, recovery, record commit, or bounce fails, preserve the project and
  retry from the explicit recovery/action shown by WebJam.
- For a demo or bug report, record the exact WebJam version, target, build ID,
  release asset name, and SHA-256. Do not include private source paths,
  credentials, invitation links, or copyrighted media.

The [desktop release runbook](DESKTOP_RELEASE_RUNBOOK.md) defines the package
and publication gates. The
[architecture decision](adr/0006-standalone-reference-studio-projects.md)
defines the persistence, migration, audio-isolation, and trust boundaries.
