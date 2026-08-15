# First Session — WebJam v0.25.0

> This guide describes the immutable, checksum-verified v0.25.0 GitHub
> **Latest** private test release. Do not treat a checkout or branch artifact as
> a package, and record every v0.25 physical gate as **NOT RUN** until it is
> observed against exact published release bytes.

Before Host or Join, choose what you are creating: **Music** (GA), **Podcast &
Voice** (GA), or **Review & Rehearsal** (Preview). The chosen profile changes
language and safe defaults, not the recorder's evidence rules. Review &
Rehearsal supports live WebJam-audio Host/Join, Record Session, local notes,
and playback/read-only review of completed session takes. It blocks standalone
projects, take editing/comp/mix mutation, track export, shared notes, visual
sync, and media timecode. No profile directly or automatically taps a meeting
app, browser, or system output.

## Host

1. Open WebJam, choose a creator profile, then choose **Host**, **Host Remote
   Recording**, or **Host Review** as shown for that profile.
2. WebJam starts the private JamulusServer and opens Jamulus.
3. In Jamulus, set your interface, channels, headphones, and buffer. Use
   **Settings → Audio/Network Settings** if needed.
4. WebJam moves into the session automatically when it sees the authenticated
   Jamulus connection. If Jamulus is behind another window, choose **Bring
   Jamulus Forward**.
5. When **Copy Invite** appears, send the complete link to the intended
   collaborator.
6. Make sound and verify you can hear each other. Use the profile's **Band
   Check**, **Sound Check**, or **Session Check** if you need help.
7. Add conversation/video only if the group wants it: choose **Conversation**
   on the main session rail, save a public HTTPS meeting link, then use **Join
   / Open Meeting**. Known Webex, Zoom, Microsoft Teams, Google Meet, and
   FaceTime destinations receive friendly labels; another accepted provider
   stays neutrally labeled. Merely showing Conversation does not open or
   rejoin a meeting. On macOS, the separate Webex-only **Show Webex App** re-verifies
   and activates the exact Cisco process when running; if stopped, it launches
   the verified app itself without a URL. Only **Join / Open Meeting** hands off
   the meeting link. Jamulus remains the WebJam-audio path. WebJam never
   directly or automatically taps a meeting app, browser, or system output.
   Local Originals record only explicitly selected input devices, so do not
   route meeting or system-output audio into those inputs.
8. Follow the single next action in the Session HUD. Open **Notes** when you
   want the same status plus output results, recent events, and your Creative
   Pulse in one session record.
9. Hosts choose **Add Shared Track** or drop one supported file on the live
   surface. Loading does not start playback. Use **Shared Track** for the full
   waveform and transport; Play stays locked until its separate Jamulus route
   is proven. Guests never receive transport authority.
10. Choose **Record Session** when the session is ready. A ready Shared Track uses
    the same visible count-in/start transition. Press **Stop Recording** once,
    wait through **Stopping** and **Finalizing**, then use **Studio** only after
    the take reads **Ready**. Each planned mono Local Original is one mono file;
    each planned stereo Local Original is one true two-channel file.

## Join

1. Open the invite or choose the profile's **Join**, **Join Recording**, or
   **Join Review** action and paste it once.
2. Let WebJam open Jamulus and connect to the invited session.
3. Set sound in Jamulus if this Mac needs it.
4. WebJam moves into the live session automatically when the authenticated
   connection is ready. Play a note and make sure you can hear each other.

## Important boundaries

- Do not select Jamulus music devices in WebJam; Jamulus owns them.
- Do not expect a meeting service to carry the music; it is optional
  conversation/video.
- Any provider's link must be public HTTPS with a DNS hostname. Local/private
  destinations, IP-literal hosts, embedded credentials, and custom ports are
  refused.
- **Show Webex App** activates or launches the verified app itself without a
  meeting link or browser; Webex chooses its own screen. **Open Webex to Mute** shows
  the verified app for its own Mute control; WebJam does not claim to change or
  verify Webex mute. Those focus actions are currently macOS-only; Windows and
  Linux use **Join / Open Meeting**.
- Do not configure Local Originals before joining. The host’s first **Record
  Session** click is when that choice matters.
- Do not use a moving meter as proof that the returned mix sounds right. The
  participants still need to listen and verify each other.

Use the
[v0.25 physical checklist](V025_CREATOR_MULTITRACK_PHYSICAL_TEST_CHECKLIST.md)
to capture creator-profile, machine, interface, route, exact stem, recovery,
and Studio evidence. The
[v0.24 checklist](V024_RECORDING_FIRST_PHYSICAL_TEST_CHECKLIST.md) remains
immutable historical evidence for the prior release.

If Jamulus needs another change, choose **Bring Jamulus Forward** in the setup
surface or **More → Audio Settings in Jamulus** after entering the session.
