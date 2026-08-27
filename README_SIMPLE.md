# WebJam, simply

> **Private test release:** GitHub
> [Latest](https://github.com/rupret007/webjam/releases/latest) remains the
> immutable, checksum-verified v0.27.0 package set until unique successful tag
> CI publishes this unpublished v0.27.1 feel build. That download does not
> include #47. This checkout is not that download. Windows is unsigned; macOS
> is ad-hoc signed and unnotarized.

> **Source note:** use the exact release tag and attached checksum manifest as
> download evidence; an untagged checkout is not a substitute. The current
> v0.27.1 source candidate has not been published as a release.

> **What this checkout is:** unpublished v0.27.1 source. The exact published
> v0.27.0 release assets—not this checkout or a branch artifact—are package
> evidence. No physical PASS result is claimed.

WebJam helps creators start a live audio session and keep its separate tracks
organized. Jamulus handles low-latency audio. Any meeting platform can be
optional for talking or video when it provides a public HTTPS meeting link
that passes WebJam's safety checks. WebJam never directly or automatically taps
the meeting app, browser, or system output. Local Originals record only the
input devices you explicitly select, so do not route meeting or system audio
into those inputs.

The unpublished v0.27.1 source keeps Music and Podcast & Voice as GA
creator profiles. Art and Review & Rehearsal are visibly Preview. Art offers a
live room for **Talk & make**, Drawpile-backed **Paint together**, or
host-clocked **Paint along**, but no recording or standalone project. Review
& Rehearsal supports live WebJam-audio
Host/Join, Record Session, local notes, and playback/read-only review of a
completed session take. It blocks standalone projects, take editing/comp/mix
mutation, track export, shared notes, visual sync, and media timecode. No
profile directly or automatically taps a meeting app, browser, or system
output.

Current private test release: **v0.27.0**. Use only the exact assets attached to
the immutable v0.27.0 GitHub release and verify them with its checksum manifest.
The four-platform release covers
Windows, Ubuntu 22.04, Intel Mac, and
Apple-silicon Mac packages. Windows is unsigned; Mac packages are ad-hoc
signed and not notarized. The current line uses `cryptography` 50.0.0 for three
audited CVE fixes. Intel macOS uses the one documented, hash-locked native
x86_64 source-build exception because upstream no longer publishes that wheel;
the other targets remain hash-locked to upstream wheels.
The Mac downloads use drag-to-Applications as the primary path and include
optional verified Terminal helpers, including a separately labeled advanced
helper that removes quarantine from WebJam only.

## Start playing

1. Choose **Art** or **Music**. Choose **Podcast or review** only when you need
   one of those rooms.
2. In Art, choose **Talk & make**, **Paint together**, or **Paint along**, then
   Host/Join. Music uses Host/Join; Podcast uses Host Remote Recording/Join
   Recording; Review uses Host Review/Join Review.
3. When Jamulus opens, choose your interface, input channels, headphones, and
   buffer there.
4. WebJam moves into the session automatically when it sees the authenticated
   Jamulus connection.
5. The host copies the invite. Make sound and verify participants hear each
   other.

That is the whole live-session path.

In **Paint along**, WebJam turns its existing window into the video workspace
once the room exists—there is no third preview window beside WebJam and the
optional meeting app. The host chooses **Share…** and controls play/pause; a
guest chooses **Open my copy…** for the same local file. The video is always
silent. **Back to room** or Escape returns to the conductor without ending the
room.

WebJam keeps a known-good Jamulus copy for offline use. **More → Jamulus
Updates** checks only WebJam-approved, signed update information. Downloads do
not interrupt a live session, and installation waits until audio, recording, practice,
reconnection, and Shared Track are stopped. The operating system still asks
before installation. If an update fails, WebJam keeps the current and previous
managed copies available on macOS. On Windows and Linux, the operating system
owns installation and WebJam retains its embedded 3.12.2 fallback instead of
claiming it can roll back the system package.

Jamulus displays a name on a second line after eight characters and accepts no
more than 16 UTF-16 units. WebJam shows that preview anywhere you enter your
Jamulus display name, so it will not be silently shortened later.

## Create locally

Music and Podcast & Voice can use their local-project actions to play with
reference audio, record ideas, rearrange sections/chapters, mix, or bounce a
WAV/FLAC result without joining
a live session. Reference Studio owns a separate local project and audio path; it
does not start, stop, configure, or feed Jamulus. Review & Rehearsal keeps this
action unavailable in Preview.

## If you need help

- **Sound needs attention:** choose **More → Audio Settings in Jamulus**.
- **Talking/video:** choose the direct **Conversation** action (or **More →
  Conversation**). It does not open a meeting. Save a public HTTPS meeting link,
  then use **Join / Open Meeting** for the explicit handoff. Webex, Zoom,
  Microsoft Teams, Google Meet, and FaceTime receive friendly labels; any
  other accepted provider stays neutrally labeled. On macOS, the separate
  Webex-only **Show Webex App** action
  re-verifies and activates the exact Cisco process when running. If stopped,
  it launches the verified app itself with no URL or browser; Webex chooses its
  own screen. Only **Join / Open Meeting** performs the one explicit
  meeting-link handoff. **Open Webex to Mute** shows the verified app so you can use
  its own Mute control; WebJam never claims it changed Webex or Jamulus. Ending
  or leaving WebJam leaves that meeting open, and leaving or closing the
  meeting does not end the WebJam session; each app ends only itself.
  FaceTime links are Mac-only. Windows and Linux use **Join / Open Meeting**
  because their current packages
  do not verify the native app publisher. If the app is missing, WebJam can
  open Cisco's official installer page after you confirm; it does not save a
  Webex password or install silently.
- **Something failed:** use the profile's **Band Check**, **Sound Check**, or
  **Session Check** and the
  support/diagnostics action. The report includes bounded Jamulus updater and
  Webex app state without local paths, meeting links, provider hostnames,
  names, or credentials.
- **Recording:** the host chooses **Record Session**. The first time, choose
  shared recording only or also keep this Mac’s separate Local Originals.
  WebJam then shows one readiness sheet with every exact server, Local
  Original, and Shared Track source; mono/stereo format; required/optional
  status; storage; Shared Track status; and any blocker. Start stays disabled
  until required facts are ready, and WebJam privately rechecks the same plan
  before it arms capture. Each required guest then opens the exact planned
  Local Original stream and returns an authenticated, take-scoped
  acknowledgement; Jamulus recording stays off until every ACK and a final
  authority check succeed. Zero-track guest opt-outs do not block, and a
  missing or stale ACK starts no recorder. Each mono input row becomes one mono file;
  each stereo row becomes one true two-channel file. One stable source ID keeps
  that identity through capture, transfer, recovery, Studio, and repeated
  takes. WebJam freezes the exact server stems, Shared Track, host input
  topology, and guest Local Original obligations before capture and rechecks
  them before Ready. WebJam does not directly tap meeting-app,
  browser, or system-output audio. Local Originals record the selected input
  devices, so do not route meeting or system audio into those inputs.
  Wait through Preparing, Count-in, Recording, Stopping, and Finalizing; only
  Ready means the take finished safely.
- **Shared Track:** during a hosted session choose **Shared Track**, select
  **Add Track…**, or drop a supported file onto the live surface. You can load
  and inspect WAV/WAVE, AIFF, or FLAC even when the playback route is not
  ready; MP3 is shown
  only when this package proves decoder support. **Recheck
  Route** starts no playback. Downloaded v0.22.2 remains locked before route
  scanning. In published v0.22.5 on Mac, an official 48-kHz BlackHole 16ch/64ch
  route may make Play available only after machine certification; choosing
  Play still performs exact live isolation checks and fails closed on
  uncertainty. The live deck also shows a progressive
  waveform, count-in, dropouts, and cleanup; Replace/Remove stay stopped-only.
  Guests receive bounded, path-free host state without transport authority;
  older peer state may show only the dedicated channel. Neither is physical
  synchronization or audibility proof.
- **Review a take:** choose the direct **Studio** action and select a playback
  output there. Review & Rehearsal Preview permits playback, scrubbing, and
  source inspection only. In Music or Podcast & Voice, you can also move or
  trim regions, use Undo/Redo, add a safely matched take lane, and comp a range.
  A new completed take automatically stacks only earlier lanes with the exact
  same session, stable source identity, sample rate, source kind, mono/stereo
  format, and verified timing. Anything uncertain is skipped.
- **Make a voice episode:** Podcast & Voice starts with a 48 kHz Host-mono +
  Guest-stereo layout. Record, loop-overdub, add chapter markers, save/reopen,
  then use **Bounce Episode** for a verified stereo 24-bit WAV. Review Preview
  cannot create/open that local project or reach an edit, mix, save, bounce, or
  export action.
- **Export for another editor:** in Music or Podcast & Voice, Track Export makes
  edited and original 24-bit stems, a rough mix, markers, instructions,
  provenance, and checksums. Review & Rehearsal Preview has no Track Export.
  The recording manifest and WAVs are never rewritten. Edited Studio packages
  use the secure macOS/Linux path; Windows clearly offers aligned originals and
  a reference mix without region edits, fades, comps, sections, master
  processing, or attached/repeated take lanes.
- **Verify an already-live session:** choose the profile's **Band Check**,
  **Sound Check**, or **Session Check**.
  It observes the session and does not replace Jamulus setup.

WebJam uses black, white, neutral gray, and burnt orange. The three-loop mark
means creators collaborating; it does not represent a Logic integration.

For the technical overview, evidence boundary, architecture, and roadmap, see
the [project brief](docs/PROJECT_BRIEF.md) and [documentation index](docs/README.md).

Waveforms load in the background and recorded gaps remain silence. Studio
autosaves choices to a separate file; a failed save keeps the edit pending and
the recorded take safe. Real two-Mac output, hardware interruption/recovery,
external-editor import, physical Reference Studio audio, and signed-install
gates are **NOT RUN** for the v0.27.0 private test release and remain unclaimed
for unpublished v0.27.1. Publishing a private candidate does not convert them to PASS. The
[v0.26 physical checklist](V026_CREATOR_MULTITRACK_PHYSICAL_TEST_CHECKLIST.md)
is the all-NOT-RUN physical ledger for that earlier published package; do not
execute it against this checkout. The
[v0.25 physical checklist](V025_CREATOR_MULTITRACK_PHYSICAL_TEST_CHECKLIST.md)
remains immutable historical evidence. The
[v0.24 checklist](V024_RECORDING_FIRST_PHYSICAL_TEST_CHECKLIST.md) remains
immutable historical evidence for the prior package.
