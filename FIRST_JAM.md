# FIRST_JAM.md — the runbook for WebJam's first real session

This is the script for going from "never tested live" to "the band jammed."
Three stages, each with a clear pass/fail. Don't skip stage 1 — every
failure you find solo is one you don't debug live with four people.

---

## Stage 0 — Band admin prep (once, ~1 hour)

**A. Pick a Jamulus server.** Three options:

- *Weekend pilot / best all-in-one path:* on the designated macOS host,
  install the official dedicated `JamulusServer.app` 3.12.2, enable **This Mac
  hosts the band server** in WebJam Setup, and use **Host & Start Audio**.
  WebJam provisions recorder control, supervises the server, and connects the
  host client over loopback. Follow [`server/README.md`](server/README.md).
- *Fastest without recording acceptance:* pick a public server near you from
  [explorer.jamulus.io](https://explorer.jamulus.io) (choose one with low
  ping and few people). Fine for a first test; anyone can join public
  servers, so don't discuss secrets over it. A public server cannot prove the
  host recording gate.
- *Remote/private alternative:* self-host with the ready-made recipe in
  [`server/`](server/README.md) — one `docker compose up -d` on any $5/mo
  VPS gives you a private server **with multitrack recording armed**
  (every take = one WAV per musician + a ready-to-open Reaper project).
  Open UDP 22124 in the firewall; your server address is the VPS IP.

**B. Create the Webex meeting link.** Use your personal room
(`https://<site>.webex.com/meet/<you>`) or schedule a recurring meeting.
Any always-on URL works.

**C. Share with the band:** server host, port (`22124`), and the Webex link.

### v0.8.1 weekend topology: Mac mini host + Apple Silicon Mac drummer

For this pilot, enable WebJam's in-app macOS hosting described in
[`server/README.md`](server/README.md), not the legacy Docker image. The manual
Terminal launcher is a fallback. The Mac mini uses `127.0.0.1:22124`; the
drummer tests both a direct Tailscale address and the home's public address on
UDP 22124. Keep WebJam's client-control RPC on 22222 and server recorder RPC on
loopback-only 22240. Disable the Mac's VPN, reserve its LAN address, allow
Jamulus through the firewall, and forward **UDP 22124 only**.

Prove the public route from an external network and compare it with direct
Tailscale for ten minutes. Use the lower-delay stable route. If direct UDP is
blocked by router/CGNAT restrictions, retain direct Tailscale or approve a VPS;
do not substitute a public server and claim the local Record gate passed.

Both musicians use **Musician with talkback**. Jamulus carries music through
their wired interfaces; native Webex carries muted-by-default speech through a
dedicated talkback mic when possible. Hold Space in Webex only between takes.
BlackHole and a Multi-Output Device are not needed for this pilot. The separate
advanced audience-bridge topology is documented in
[`WEBEX_AUDIO_MODES.md`](WEBEX_AUDIO_MODES.md).

The exact host, TD-27, network, acceptance, and fallback steps are in
[`SUNDAY_TWO_MAC_PILOT.md`](SUNDAY_TWO_MAC_PILOT.md).

---

## Stage 1 — Solo smoke test (you alone, ~30 min) ← DO THIS FIRST

Setup: one pilot machine for each release artifact you intend to validate
(Windows x64, macOS ARM64, macOS Intel x64), with wired headphones and the
latest WebJam build from
[Releases](https://github.com/rupret007/webjam/releases)
(right-click → Open the first time on macOS — see README_SIMPLE for the
security-warning walkthrough). Downloadable builds bundle Jamulus (macOS:
zero-install; Windows: install it via the Setup Wizard's "Install Jamulus
now" button) — no separate download needed unless you're testing an
unbundled/source build.

Work through this in order; note anything that deviates.

1. **First-run wizard** appears (fresh installs are unconfigured on
   purpose). The designated macOS host selects **This Mac hosts the band
   server** (which fixes its client host to `127.0.0.1`); other musicians
   enter the shared external host/port. Confirm the Jamulus
   executable path (pre-filled on bundled builds — on Windows use "Install
   Jamulus now" if it's blank), paste the Webex link, choose **Musician with
   talkback**, and select supplemental local recording only when needed.
2. **Checks → Ready Check / F2.** Automated items should pass. Manually confirm
   each Webex `VERIFY` row; WebJam cannot inspect native Webex device or mute
   selections. Talkback and video-only modes must not ask for BlackHole.
3. **Practice smoke (no band server needed).** Press **Ctrl+P** (or the
   **Practice** button). WebJam starts a private Jamulus server *on your
   machine* and connects to it — you should see your own card, hear
   yourself, and be able to move your fader and use Talk Break. If practice
   works, your whole local audio path is proven before any network enters
   the picture. Stop Audio ends it.
4. **Start Audio** (or **Host & Start Audio** on the host). The status bar
   should read `Server: Hosting :22124` on the host and `Connecting` first;
   within
   ~10 s it should change to `Connected (yourserver:22124)` and the truthful
   connecting state should be replaced by a single real card with **your
   name** ("1 participant · waiting for
   others"). This step is the big one — it proves the JSON-RPC control
   channel works against real Jamulus.
   - Also confirm: a second window (Jamulus's own GUI) opened. Ignore it;
     WebJam is the controller.
5. **Mixer sanity.** Drag your own fader — no errors. Click **Talk Break**:
   the Jamulus GUI's mute indicator should light up. Confirm Webex does not
   change. Release Webex Space/mute it, choose **Resume Music**, and confirm the
   Jamulus send returns.
6. **Chat.** Type a message in the canvas chat box. It should echo as
   `You: …` and appear in the Jamulus GUI chat window too.
7. **Open Webex.** WebJam opens native Webex or the default browser and reports
   `Opened externally`. Finish joining there. This status does not claim that
   WebJam can see meeting membership or control Webex.
8. **Stop Audio → Start Audio again.** On the designated host, Stop Audio must
   leave `Server: Hosting :22124` visible so the other musician stays
   connected. Reconnect should work; no zombie client processes remain.
9. **Quit WebJam.** Its Jamulus client should quit. A server WebJam owns also
   stops; an authenticated external/manual server that WebJam merely adopted
   must remain running.

**If anything fails:** hit `Ctrl+Shift+D` (copies a diagnostics summary to
the clipboard) and paste it into a GitHub issue — or into a Claude session,
and I'll debug from there. Logs live at `~/.webjam.log` (WebJam) and
`~/.webjam_jamulus.log` (Jamulus output).

---

## Stage 2 — Two-person test (you + one patient bandmate, ~30 min)

1. Bandmate installs per README_SIMPLE (WebJam + virtual cable; Jamulus is
   bundled in the release build — macOS zero-install, Windows "Install
   Jamulus now" in the wizard) and enters the wizard values you shared.
2. Both **Start Audio**. Each should see the other's named card appear.
3. **Play something.** Adjust each other's faders — confirm your fader
   moves only change *your* monitor mix, not theirs.
4. One person selects **Talk Break** — the other should stop hearing them in
   Jamulus, while Webex remains untouched. Resume only after Webex is muted.
5. Both **Open Webex** and join muted with computer audio. Webex speakers feed
   the wired interfaces. Hold Space only to speak between takes; require clear
   speech and no delayed duplicate music during ten minutes of playing.
6. Chat from both sides; both canvases should show the conversation.
7. In the Canvas, enter one `Decision:`, `Action:`, `Blocker:`, and
   `Question:` line. Confirm Pulse updates, then use **Export… → Session
   brief…** and verify the exported Markdown also contains the raw notes.
8. Start and stop **Record** from the Mac. Require Preflight and Validating to
   finish without a **Needs Attention** result. Confirm the take contains one
   non-empty server WAV per musician plus `host-guitar.wav`,
   `host-vocal.wav`, and `webjam-take.json`. Play it in Take Deck and import
   the aligned stems into Logic. The `.rpp` file is for Reaper and is not a
   Logic project. If isolated capture cannot share the SSL with Jamulus, keep
   the server take but treat the recording gate as failed; do not change the
   live audio path during the session.
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
- If Webex does not open, use **Copy meeting link** and open it directly in
  native Webex or a supported browser. WebJam does not host the meeting.

---

## Failure playbook

| Symptom | Likely cause | Fix |
|---|---|---|
| "No Jamulus Server Configured" | wizard skipped | Settings (Ctrl+,) → enter server |
| Stage stays on "Connecting" after Start Audio | RPC handshake failed | check `~/.webjam_jamulus.log`; confirm Jamulus ≥ 3.9; port 22222 free |
| "Jamulus Not Found" | not bundled/installed, or custom path | Windows: click "Install Jamulus now" in Settings → Jamulus; otherwise install from jamulus.io, or set the path in Settings |
| "Port in use" | old Jamulus still running | quit it (Activity Monitor / Task Manager), retry |
| I hear delayed duplicate music | Webex microphone is open or a bridge feed reaches a musician | mute Webex; use talkback mode and keep all music in Jamulus |
| Nobody hears Webex speech | joined without computer audio or wrong talkback mic | join with computer audio, choose the speech mic and wired interface speaker, then test Spacebar |
| Remote drummer cannot reach Mac server | VPN, missing UDP forward, firewall, CGNAT | disable VPN; verify DHCP reservation and UDP 22124 forward; do not expose RPC ports |
| Record button conflicts with client RPC | server and client both using 22222 | keep client RPC on 22222 and same-Mac server recorder RPC on 22240 |
| I cannot hear my own Jamulus return | interface/mixer routing or local channel problem | check the Jamulus personal mix; Jamulus normally returns the local musician with the server mix |
| Constant crackling in Jamulus | buffer too small / Wi-Fi | wired Ethernet; raise buffer in Jamulus settings |
