# WebJam, simply

WebJam helps a band start playing together. It keeps the session, invite, and
recordings organized. Jamulus handles the music. Webex is optional for talking
or video.

Current source candidate: **v0.21.0 unsigned private test candidate**. Its
four-platform workflow covers Windows, Ubuntu 22.04, Intel Mac, and
Apple-silicon Mac packages. Windows is unsigned; Mac packages are ad-hoc signed
and not notarized.
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

## Write or rehearse a song locally

Choose **Reference Studio** when you want to play with a backing track, record
ideas, rearrange named sections, mix, or bounce a WAV/FLAC demo without joining
a live jam. Reference Studio owns a separate local project and audio path; it
does not start, stop, configure, or feed Jamulus.

## If you need help

- **Sound needs attention:** choose **More → Audio Settings in Jamulus**.
- **Talking/video:** choose **More → Webex / Conversation**. Jamulus still
  carries music; WebJam never says that Webex joined or muted itself.
- **Recording:** the host chooses **Record**. The first time, choose shared
  recording only or also keep this Mac’s separate Local Originals.
- **Review a take:** choose **More → Studio**. Choose a playback output only
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

Waveforms load in the background and recorded gaps remain silence. Studio
autosaves choices to a separate file; a failed save keeps the edit pending and
the recorded take safe. Real two-Mac output, hardware interruption/recovery,
external-editor import, physical Reference Studio audio, and signed-install
gates are **NOT RUN** for v0.21.0.
