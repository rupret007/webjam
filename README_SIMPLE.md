# WebJam, simply

WebJam is a desktop app for people who make music or art together.

Follow the network named in the invitation. A same-network invitation needs
the host's Wi-Fi or local network. For friends in another house, ask the test
organizer for a compatible, qualified build. The current unsigned Windows app
cannot join remote Music. The [community beta setup guide](docs/COMMUNITY_MUSIC_BETA.md)
explains what is ready to test on each computer. Keep the host's window open.

The [creator guide](USER_GUIDE.md) is the one complete how-to. This page is
the short version.

## Three steps

1. **Get the app** from [GitHub Latest](https://github.com/rupret007/webjam/releases/latest).
   Pick the file for your computer (Windows, Mac, or Ubuntu).
   For the Stalemate private beta, use the qualified package from its organizer;
   the public download may not accept the beta's invitation.
2. **Open it.**
   - **Windows:** Windows may say it protected your PC. Choose **More info**,
     then **Run anyway**. A work PC may still block it; ask IT. WebJam does
     not bypass that.
   - **Mac:** drag WebJam into Applications, then open it from there. If
     macOS blocks it, open **System Settings → Privacy & Security**, click
     **Open Anyway** for WebJam, and confirm **Open**.
   - **Ubuntu:** unzip the folder and start `WebJam` from inside it. Keep the
     whole folder together.
3. **Start or join.** Choose **Art** or **Music**, then **Host** or **Join**.
   The host uses **Copy Invite** and sends the **whole message**. The friend
   opens WebJam, chooses **Join**, and pastes that whole message.

## Music and Art

- **Music:** play live together. **A Mac starts the band.** Friends on Mac,
  Windows, or Linux choose **Join**. Windows and Linux cannot Host a Music
  jam. Music uses Host/Join. When Jamulus opens, pick your interface,
  headphones, and buffer. Play a note. If you hear each other, you are in.
- **Art (Preview):** open a room and make with your own tools, or follow a
  silent lesson. Choose **Make together** or **Paint along**, then **Host**
  or **Join**. Any computer can Host or Join. You do not need an art app.
  A shared canvas is optional and needs [Drawpile](https://drawpile.net/download/)
  only if you want that canvas.

Talking and video use your own meeting app (Webex, Zoom, Teams, Meet, or
FaceTime). Open **Conversation**, add a public meeting link, then **Join /
Open Meeting**. Opening Conversation does not start a meeting. Ending WebJam
leaves the meeting open.

## Invite, record, leave

- **Invite:** send the whole copied message, not just a short code. If
  WebJam shows a short reply, send that back; the host chooses **Paste
  Reply**. If Join says the invite was already used, ask the host for a
  new one and choose **Paste New Invite**.
- **Record (Music):** the Mac host chooses **Record Session**. When the take
  says **Ready**, it is saved. Use
  **More → Recording Setup… → Show Folder** to find the saved files.
  Recording locations depend on the profile and its settings.
- **Leave:** the host chooses **End Session** (Music) or **End Room** (Art).
  A guest chooses **Leave Jam** or **Leave Room**. The others stay. Your
  meeting and drawing app stay open. Notes in **Session library** stay on
  this computer.

Podcast & Voice and Review & Rehearsal are not extra buttons on the Art/Music
door. The door **File** menu opens **Session library…**.

## Create locally

From the launch screen, **File → Session library…** opens saved notes and
plans without starting a room. If a live session is open, end or leave it
first. Published v0.29.0 then needs you to quit and relaunch. A later
test build can use **File → Return to launch** instead. Details for a
separate local Music project stay in the [creator guide](USER_GUIDE.md).

## If you need help

For the next click, use the [creator guide](USER_GUIDE.md) or press **F1**
in the app. Common fixes:

- **Music sound:** **More → Audio Settings in Jamulus**.
- **Talking or video:** **Conversation**, then **Join / Open Meeting**.
- **Art room:** follow the recovery button on the room. Your meeting and
  drawing app stay open.
- **Recording:** wait until the take says **Ready**. Only the host starts
  and stops **Record Session**.

WebJam uses black, white, gray, and burnt orange. The three-loop mark means
people making together. It is not a Logic logo.

Developers who want the source app can run `webjam_qt_main.py` after
installing the listed Python packages. See [Development](DEVELOPMENT.md).

> **Source identity:** v0.29.0 unsigned testing candidate.
> **Current download and publication status:** [GitHub Latest](https://github.com/rupret007/webjam/releases/latest)
> identifies the published tag, packages, and checksum manifest.
> **Historical checkpoint, checked 2026-09-29 before the v0.29.0 release round:**
> [v0.28.4](https://github.com/rupret007/webjam/releases/tag/v0.28.4), immutable
> release `396603181`, published `2026-09-25T12:57:13Z`. Annotated tag object
> `dc494f0450ccc6f690ba7c9768ff4b575fe2da6c` peels to
> `ce52e9c9302cb3f28510a3e3b0e9edfff8b31111`. Its seven packages plus
> `WebJam-v0.28.4-SHA256SUMS.txt` are the download evidence. Windows is unsigned;
> macOS is ad-hoc signed and unnotarized. Physical, signing, and platform-trust
> gates remain **NOT RUN**. A source checkout is not a published package.

## Historical release evidence

The superseded v0.28.1 download record is release `393030220`, published
`2026-09-21T14:30:22Z`: annotated tag object
`db44247a3ceefb97f4cac6e623deef1bd32648a8` peels to
`200cac9eb04d01611696cdc147957b36daef257f`. Its seven packages and
`WebJam-v0.28.1-SHA256SUMS.txt` remain evidence for that historical release.
Older release entries (v0.28.0, v0.27.2, Jamulus catalog v1–v3) were deleted by
owner; git tags remain. Those records do not verify the current show candidate.
Physical observations remain **NOT RUN** until recorded against exact bytes.

The
[v0.26 physical checklist](V026_CREATOR_MULTITRACK_PHYSICAL_TEST_CHECKLIST.md)
is the all-NOT-RUN physical ledger for that earlier published package; do not
execute it against this checkout. The
[v0.25 physical checklist](V025_CREATOR_MULTITRACK_PHYSICAL_TEST_CHECKLIST.md)
remains immutable historical evidence. The
[v0.24 checklist](V024_RECORDING_FIRST_PHYSICAL_TEST_CHECKLIST.md) remains
immutable historical evidence for the prior package.
