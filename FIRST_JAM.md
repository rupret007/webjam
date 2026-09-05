# First Session — WebJam v0.27.2 source

> This guide describes current unsigned v0.27.2 source. GitHub **Latest** is
> immutable unsigned/ad-hoc private test release `379360694`, published from
> lightweight tag `v0.27.2` at exact commit
> `9c6ca3de96aa7eb261c65b7dee768ab48144169c`, with seven packages plus
> `WebJam-v0.27.2-SHA256SUMS.txt`. A checkout or branch artifact is not a
> substitute for one of those exact checksum-verified packages. Record every
> v0.27 physical gate as **NOT RUN** until it is observed against exact release
> bytes.
> Host/Join is source-eligible through the exact baked Jamulus 3.12.2 and 3.12.3
> records, but do not treat this checkout as a package or physical-test result.

Before Host or Join, choose **Art** or **Music**. Those are the equal first
clicks. Art then offers **Make together** or **Paint along**, then **Host** or
**Join**. **Make together** means “Talk, make, or draw together in one room”; a
host may open one shared canvas from inside the room. Music is **Host** or
**Join** only. Profiles that support recording keep the same evidence
rules for their recorders. The launch **File** menu contains **New Music Project…**,
**Podcast & Voice…**, and **Review & Rehearsal…**. No profile directly or
automatically taps a meeting app, browser, or system output.

WebJam welcomes artists in any medium who want to make things together. Art
is a newer Preview; the aim is to build it out to the same depth as Music.
Bring your own tools—paint, clay, paper, a printer, or your usual app.
A shared canvas is optional. Inside the room, **Conversation** lets you open
a meeting and share a demonstration in Webex or your chosen service. Everyone
can follow there; **Paint along** is the separate silent local-video option.

## Start an Art room

1. Choose **Art**, then **Make together** or **Paint along**.
2. Choose **Host**. When **Your room is open** appears, choose **Copy Invite**
   and send the complete invitation to your collaborator.
3. To join, choose **Join** and paste that full invitation once. Wait for the
   host's room to respond. **You’re in** means the Art room is connected.
   Art does not launch Jamulus or ask you to prove Music audio.
4. Work with your own tools, open **Conversation** for an optional meeting or
   shared demonstration, or follow the host's silent local video in **Paint
   along**. A shared canvas opens only when you choose it.
5. If the room loses contact, follow its reconnecting or recovery guidance.
   Use **Paste New Invite** with a fresh invitation when the old one may have
   been used. Older peers must update WebJam before using a fresh invitation.
6. Choose **End Room** as host or **Leave Room** as guest. If cleanup needs
   another attempt, use **Try End Room** or **Try Leave Room**. Your external
   meeting and drawing app remain open.

## Host with live audio

These steps apply to Music, Podcast & Voice, and Review & Rehearsal.

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
   surface. Loading does not start playback. If Play is not ready, the strip
   says **Set up the audio device** and opens Shared Track so you can choose
   **Set Up Shared Track…** and **Recheck Route**. That step does not need a
   signed catalog. When the isolated route is already on this Mac, Play sends
   the song to the room. Guests never receive transport authority.
10. Choose **Record Session** when the session is ready. Review every exact
    planned server track, Local Original, and Shared Track in the readiness
    sheet. Confirm its mono/stereo format, required/optional status, storage,
    and blockers; **Start Recording** stays disabled until required facts are
    ready. WebJam rechecks that exact plan privately before arming. Every
    opted-in guest must open the frozen Local Original stream and acknowledge
    that exact take before Jamulus recording can start; WebJam rechecks
    authority again after all required ACKs. A ready
    Shared Track uses the same visible count-in/start transition. Press **Stop
    Recording** once, wait through **Stopping** and **Finalizing**, then use
    **Studio** only after the take reads **Ready**. Each planned mono Local
    Original is one mono file; each planned stereo Local Original is one true
    two-channel file.

## Join with live audio

These steps apply when the host is using Music, Podcast & Voice, or Review &
Rehearsal. An Art invitation follows the room steps above.

1. Open the invite or choose the profile's **Join**, **Join Recording**, or
   **Join Review** action and paste the complete invitation once. The field is
   masked and the invitation is not saved.
2. Follow **Checking invite**, **Contacting host**, **Securing connection**, and
   **Opening Jamulus**. WebJam stops at **Needs attention** rather than waiting
   forever. Use the one shown action: **Try Again** or **Paste New Invite**.
3. Set sound in Jamulus if this Mac needs it. First-time native audio setup is
   a separate human step and may remain open while you choose the interface.
4. WebJam moves into the live session automatically only when the authenticated
   connection is ready. Play a note and make sure you can hear each other.

## Important boundaries

- For live audio, select music devices in Jamulus. Art needs no Jamulus setup.
- Music uses Jamulus for music. An optional meeting supports talking, video,
  and demonstrations in Art or Music.
- Any provider's link must be public HTTPS with a DNS hostname. Local/private
  destinations, IP-literal hosts, embedded credentials, and custom ports are
  refused.
- **Show Webex App** activates or launches the verified app itself without a
  meeting link or browser; Webex chooses its own screen. Use the meeting app's
  own microphone and sharing controls. Art has no Music mute control. Native
  app focus is currently macOS-only; Windows and
  Linux use **Join / Open Meeting**.
- Do not configure Local Originals before joining. The host’s first **Record
  Session** click is when that choice matters.
- Do not use a moving meter as proof that the returned mix sounds right. The
  participants still need to listen and verify each other.

Use the all-**NOT RUN**
[v0.26 physical checklist](V026_CREATOR_MULTITRACK_PHYSICAL_TEST_CHECKLIST.md)
only with an exact checksum-verified release asset; never record this checkout
as physical package evidence. The immutable
[v0.25 physical checklist](V025_CREATOR_MULTITRACK_PHYSICAL_TEST_CHECKLIST.md)
remains historical evidence. The
[v0.24 checklist](V024_RECORDING_FIRST_PHYSICAL_TEST_CHECKLIST.md) remains
immutable historical evidence for the prior release.

If Jamulus needs another change, choose **Bring Jamulus Forward** in the setup
surface or **More → Audio Settings in Jamulus** after entering the session.
