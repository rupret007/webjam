# WebJam, simply

> **Private candidate guide:** use GitHub
> [Latest](https://github.com/rupret007/webjam/releases/latest) for the current
> checksum-verified test package. Windows is unsigned; macOS is ad-hoc signed
> and unnotarized.

> **Source note:** use the exact release tag and attached checksum manifest as
> download evidence; an untagged checkout is not a substitute. v0.23.0 adds
> Shared Track, Record Session, and Studio improvements. Its publication state
> is authoritative on GitHub.

WebJam helps a band start playing together. It keeps the session, invite, and
recordings organized. Jamulus handles the music. Webex is optional for talking
or video.

Current downloadable candidate: verify the exact version shown by GitHub
**Latest**. Its
four-platform release covers Windows, Ubuntu 22.04, Intel Mac, and
Apple-silicon Mac packages. Windows is unsigned; Mac packages are ad-hoc
signed and not notarized. The current line uses `cryptography` 50.0.0 for three
audited CVE fixes. Intel macOS uses the one documented, hash-locked native
x86_64 source-build exception because upstream no longer publishes that wheel;
the other targets remain hash-locked to upstream wheels.
The Mac downloads use drag-to-Applications as the primary path and include
optional verified Terminal helpers, including a separately labeled advanced
helper that removes quarantine from WebJam only.

## Start playing

1. Choose **Host a Jam** if this Mac is starting the rehearsal, or **Join a
   Jam** if a bandmate sent a link.
2. When Jamulus opens, choose your interface, input channels, headphones, and
   buffer there.
3. WebJam moves into the session automatically when it sees the authenticated
   Jamulus connection.
4. The host copies the invite. Play a note and make sure bandmates hear each
   other.

That is the whole live-music path.

WebJam keeps a known-good Jamulus copy for offline use. **More → Jamulus
Updates** checks only WebJam-approved, signed update information. Downloads do
not interrupt a jam, and installation waits until music, recording, practice,
reconnection, and Shared Track are stopped. The operating system still asks
before installation. If an update fails, WebJam keeps the current and previous
managed copies available on macOS. On Windows and Linux, the operating system
owns installation and WebJam retains its embedded 3.12.2 fallback instead of
claiming it can roll back the system package.

Jamulus displays a name on a second line after eight characters and accepts no
more than 16 UTF-16 units. WebJam shows that preview anywhere you enter your
musician name, so it will not be silently shortened later.

## Write or rehearse a song locally

Choose **Reference Studio** when you want to play with a backing track, record
ideas, rearrange named sections, mix, or bounce a WAV/FLAC demo without joining
a live jam. Reference Studio owns a separate local project and audio path; it
does not start, stop, configure, or feed Jamulus.

## If you need help

- **Sound needs attention:** choose **More → Audio Settings in Jamulus**.
- **Talking/video:** choose the direct **Webex Controls** action (or **More →
  Webex Controls**). It only shows Conversation. On macOS, **Show Webex App**
  re-verifies and activates the exact Cisco process when running. If stopped,
  it launches the verified app itself with no URL or browser; Webex chooses its
  own screen. Only **Join / Open Meeting** performs the one explicit
  meeting-link handoff. **Open Webex to Mute** shows the verified app so you can use
  its own Mute control; WebJam never claims it changed Webex or Jamulus.
  Windows and Linux use **Join / Open Meeting** because their current packages
  do not verify the native app publisher. If the app is missing, WebJam can
  open Cisco's official installer page after you confirm; it does not save a
  Webex password or install silently.
- **Something failed:** use **More → Band Check / Verify Sound** and the
  support/diagnostics action. The report includes bounded Jamulus updater and
  Webex app state without local paths, meeting links, names, or credentials.
- **Recording:** the host chooses **Record Session**. The first time, choose
  shared recording only or also keep this Mac’s separate Local Originals.
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
  uncertainty. In v0.23.0 source, the live deck also shows a progressive
  waveform, count-in, dropouts, and cleanup; Replace/Remove stay stopped-only.
  Guests receive bounded, path-free host state without transport authority;
  older peer state may show only the dedicated channel. Neither is physical
  synchronization or audibility proof.
- **Review a take:** choose the direct **Studio** action. Choose a playback output only
  while reviewing a take. Move or trim regions on the Arrange timeline, use
  Undo/Redo, or add a safely matched repeated recording as a take lane and
  Option/Alt-drag the parts you want in the comp.
- **Export for another editor:** Track Export makes edited and original 24-bit
  stems, a rough mix, markers, instructions, provenance, and checksums. The
  recording manifest and WAVs are never rewritten. Edited Studio packages use
  the secure macOS/Linux path; Windows clearly offers aligned originals and a
  reference mix without region edits, fades, comps, sections, master
  processing, or attached/repeated take lanes.
- **Verify an already-live jam:** choose **More → Band Check / Verify Sound**.
  It observes the session and does not replace Jamulus setup.

WebJam uses black, white, neutral gray, and burnt orange. The three-loop mark
means musicians playing together; it does not represent a Logic integration.

For the technical overview, evidence boundary, architecture, and roadmap, see
the [project brief](docs/PROJECT_BRIEF.md) and [documentation index](docs/README.md).

Waveforms load in the background and recorded gaps remain silence. Studio
autosaves choices to a separate file; a failed save keeps the edit pending and
the recorded take safe. Real two-Mac output, hardware interruption/recovery,
external-editor import, physical Reference Studio audio, and signed-install
gates are **NOT RUN** for v0.23.0. Publishing a private candidate does not
convert them to PASS. Use the
[v0.23 physical checklist](V023_SHARED_TRACK_RECORDING_PHYSICAL_TEST_CHECKLIST.md)
for the exact multi-machine evidence to collect.
