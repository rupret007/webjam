# FIRST_JAM.md — the runbook for WebJam's first real session

This is the script for going from "never tested live" to "the band jammed."
Three stages, each with a clear pass/fail. Don't skip stage 1 — every
failure you find solo is one you don't debug live with four people.

---

## Stage 0 — Band admin prep (once, ~1 hour)

**A. Pick a Jamulus server.** Two options:

- *Fastest:* pick a public server near you from
  [explorer.jamulus.io](https://explorer.jamulus.io) (choose one with low
  ping and few people). Fine for a first test; anyone can join public
  servers, so don't discuss secrets over it.
- *Best for the band:* self-host with the ready-made recipe in
  [`server/`](server/README.md) — one `docker compose up -d` on any $5/mo
  VPS gives you a private server **with multitrack recording armed**
  (every take = one WAV per musician + a ready-to-open Reaper project).
  Open UDP 22124 in the firewall; your server address is the VPS IP.

**B. Create the Webex meeting link.** Use your personal room
(`https://<site>.webex.com/meet/<you>`) or schedule a recurring meeting.
Any always-on URL works.

**C. Share with the band:** server host, port (`22124`), and the Webex link.

### v0.8.1 weekend topology: Mac mini host + Apple Silicon Mac drummer

For this pilot, use the native macOS server command in
[`server/README.md`](server/README.md), not the legacy Docker image. The Mac
mini uses `127.0.0.1:22124`; the drummer tests both a direct Tailscale address
and the home's public address on UDP 22124. Keep WebJam's client-control RPC on
22222 and server recorder RPC on loopback-only 22240. Disable the Mac's VPN,
reserve its LAN address, allow Jamulus through the firewall, and forward
**UDP 22124 only**. Require Tailscale to report a direct peer path, not DERP.

Prove the public route from an external network and compare it with direct
Tailscale for ten minutes. Use the lower-delay stable route. If direct UDP is
blocked by router/CGNAT restrictions, retain direct Tailscale or approve a VPS;
do not substitute a public server and claim the local Record gate passed.

The Mac mini is the only Webex audio bridge: Jamulus outputs to a macOS
Multi-Output Device containing the physical output and BlackHole; Webex uses
BlackHole as microphone and the physical device as speaker. The drummer uses
Jamulus through the TD-27 for all audio and keeps Webex microphone/speaker
muted. Device
detection in Setup/Ready Check does not configure this routing.

The exact host, TD-27, network, acceptance, and fallback steps are in
[`SUNDAY_TWO_MAC_PILOT.md`](SUNDAY_TWO_MAC_PILOT.md).

---

## Stage 1 — Solo smoke test (you alone, ~30 min) ← DO THIS FIRST

Setup: one pilot machine for each release artifact you intend to validate
(Windows x64, macOS ARM64, macOS Intel x64), with VB-CABLE/BlackHole
installed as appropriate, the latest WebJam build from
[Releases](https://github.com/rupret007/webjam/releases)
(right-click → Open the first time on macOS — see README_SIMPLE for the
security-warning walkthrough). Downloadable builds bundle Jamulus (macOS:
zero-install; Windows: install it via the Setup Wizard's "Install Jamulus
now" button) — no separate download needed unless you're testing an
unbundled/source build.

Work through this in order; note anything that deviates.

1. **First-run wizard** appears (fresh installs are unconfigured on
   purpose). Enter the server host/port from Stage 0, confirm the Jamulus
   executable path (pre-filled on bundled builds — on Windows use "Install
   Jamulus now" if it's blank), paste the Webex link, and confirm the
   audio-routing page shows a green check for VB-CABLE/BlackHole.
2. **Test → Ready Check / F2.** All required items should pass. Optional bridge
   warnings are expected on musicians who keep Webex audio muted. Fix required
   failures before continuing.
3. **Practice smoke (no band server needed).** Press **Ctrl+P** (or the
   **Practice** button). WebJam starts a private Jamulus server *on your
   machine* and connects to it — you should see your own card, hear
   yourself, and be able to move your fader and Mute Me. If practice
   works, your whole local audio path is proven before any network enters
   the picture. Stop Audio ends it.
4. **Launch Audio.** The status bar should read `Connecting` first; within
   ~10 s it should change to `Connected (yourserver:22124)` and the demo cards
   should be replaced by a
   single real card with **your name** ("1 participant · waiting for
   others"). This step is the big one — it proves the JSON-RPC control
   channel works against real Jamulus.
   - Also confirm: a second window (Jamulus's own GUI) opened. Ignore it;
     WebJam is the controller.
5. **Mixer sanity.** Drag your own fader — no errors. Click **Mute Me**:
   the Jamulus GUI's mute indicator should light up (that proves *real*
   self-mute via RPC, not just a local fader trick).
6. **Chat.** Type a message in the canvas chat box. It should echo as
   `You: …` and appear in the Jamulus GUI chat window too.
7. **Join Video.** Either the embedded pane loads your meeting, or you
   get the "Open video call in browser" fallback button (also a pass —
   the embed depends on QtWebEngine + your meeting's embed permissions).
8. **Stop Audio → Launch Audio again.** Reconnect should work; no zombie
   Jamulus processes left behind (check Activity Monitor).
9. **Quit WebJam.** Jamulus should quit with it.

**If anything fails:** hit `Ctrl+Shift+D` (copies a diagnostics summary to
the clipboard) and paste it into a GitHub issue — or into a Claude session,
and I'll debug from there. Logs live at `~/.webjam.log` (WebJam) and
`~/.webjam_jamulus.log` (Jamulus output).

---

## Stage 2 — Two-person test (you + one patient bandmate, ~30 min)

1. Bandmate installs per README_SIMPLE (WebJam + virtual cable; Jamulus is
   bundled in the release build — macOS zero-install, Windows "Install
   Jamulus now" in the wizard) and enters the wizard values you shared.
2. Both **Launch Audio**. Each should see the other's named card appear.
3. **Play something.** Adjust each other's faders — confirm your fader
   moves only change *your* monitor mix, not theirs.
4. One person **Mute Me** — the other should stop hearing them (this is
   the real test of self-mute).
5. Both **Join Video**. Confirm the designated Mac bridge carries the band
   mix into Webex without echo. Keep Webex audio muted on the drummer's Mac.
6. Chat from both sides; both canvases should show the conversation.
7. In the Canvas, enter one `Decision:`, `Action:`, `Blocker:`, and
   `Question:` line. Confirm Pulse updates, then use **Export… → Session
   brief…** and verify the exported Markdown also contains the raw notes.
8. Start and stop **Record** from the Mac. Confirm a new take contains one
   non-empty WAV per musician, plays in Take Deck, and imports into Logic.
   The `.rpp` file is for Reaper and is not a Logic project.
9. Briefly interrupt the drummer's network and confirm reconnect, then run
   continuously for 45–60 minutes. Finish by checking diagnostics, logs, and
   orphan Jamulus processes.

Latency check: if the round-trip delay makes tight playing impossible,
try a server geographically closer to both of you, set your audio
interface's buffer lower, and use wired Ethernet — Wi-Fi is the #1
latency killer.

---

## Stage 3 — Full band

Everything from Stage 2, times N. Tips for the first full session:

- Have everyone run **F2 Ready Check** *before* the call.
- The conductor (you) can Ctrl+M mute-all while sorting out someone's
  setup, and `Ctrl+S` saves the mix once levels feel right — it
  auto-restores next session.
- Anyone whose video pane misbehaves should use the browser-fallback
  button and move on; audio is the thing that matters.

---

## Failure playbook

| Symptom | Likely cause | Fix |
|---|---|---|
| "No Jamulus Server Configured" | wizard skipped | Settings (Ctrl+,) → enter server |
| Cards stay "Preview · …" after Launch | RPC handshake failed | check `~/.webjam_jamulus.log`; confirm Jamulus ≥ 3.9; port 22222 free |
| "Jamulus Not Found" | not bundled/installed, or custom path | Windows: click "Install Jamulus now" in Settings → Jamulus; otherwise install from jamulus.io, or set the path in Settings |
| "Port in use" | old Jamulus still running | quit it (Activity Monitor / Task Manager), retry |
| I hear myself echo | Webex mic set to real mic AND Jamulus monitoring | in Webex, set mic to the virtual cable only |
| Others can't hear my instrument on video | Webex mic not set to virtual cable | Webex settings → microphone → BlackHole / CABLE Output |
| Remote drummer cannot reach Mac server | VPN, missing UDP forward, firewall, CGNAT | disable VPN; verify DHCP reservation and UDP 22124 forward; do not expose RPC ports |
| Record button conflicts with client RPC | server and client both using 22222 | keep client RPC on 22222 and same-Mac server recorder RPC on 22240 |
| Music sounds fine in Jamulus, awful in Webex | that's expected — Webex compresses | mix decisions happen in Jamulus; Webex is for faces |
| Constant crackling in Jamulus | buffer too small / Wi-Fi | wired Ethernet; raise buffer in Jamulus settings |
