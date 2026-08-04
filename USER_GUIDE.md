# WebJam musician guide — v0.22.3

> **Pre-publication candidate:** this guide describes v0.22.3. The immutable
> v0.22.2 packages remain GitHub Latest until the exact v0.22.3 draft passes
> verified promotion. Physical and platform-trust gates remain **NOT RUN**.

## Follow the current guide

The always-visible Session HUD is the dominant action surface. **Notes** opens
Session Canvas, where **NOW** repeats the same status, next step, evidence-based
reason, output results, and recent meaningful events. Studio repeats the shared
next step while showing take validation, non-destructive edit/save state, and
export outcome. These are views of one result, not separate checklists.

The **Creative Pulse** below NOW summarizes explicit decisions, actions,
blockers, questions, references, and rehearsal checkpoints from your local
notes. It can help decide what to play or arrange next, but it never changes
WebJam's operational status. For example, typing “recording finished” cannot
create a take or unlock export.

## What each app does

WebJam conducts the rehearsal. It starts or joins the private session, creates
and checks invitations, keeps recording truth, and provides Studio.

Jamulus is the live music engine. It owns your interface, inputs, outputs,
channels, buffer, jitter, feedback protection, and musician mix. Configure
those in Jamulus, not in WebJam.

Webex is optional talking/video. The direct **Webex Controls** action reveals
Conversation without opening or rejoining a meeting. WebJam can validate and
explicitly open a link, but it does not claim that Webex joined, muted,
selected devices, or sees anyone. WebJam also checks whether the native Webex
app is installed. If it is missing, an explicit button opens Cisco's official
installer in your browser; WebJam does not save a Webex password or
install/update Webex silently.

## Host a Jam

Choosing **Host a Jam** is authorization to start the private session—there is
no extra Start Session click. WebJam starts the server first, then opens a
visible Jamulus client against it. Set up sound in Jamulus; once WebJam sees
the authenticated connection, it moves straight into the session without an
extra setup, sound-confirmation, or Enter Jam click. Play a note and make sure
you can hear each other—WebJam never treats a meter or connection as proof of
that. **More → Band Check / Verify Sound** is there if you need help.

If setup is interrupted, WebJam keeps only a private, bounded recovery record.
It reuses progress only when the role and dedicated Jamulus profile fingerprint
still match. Otherwise it returns safely to native Jamulus setup.

## Join a Jam

Paste one WebJam invite or open it from Finder. WebJam parses it at ingress,
does not leave the bearer visible in the interface, and starts only the guest
services required for that invitation. If setup fails, the in-memory intent is
preserved only while it is safe to retry; WebJam never pretends an uncertain
invitation succeeded.

## Main session actions and More

The main session rail keeps the everyday destinations visible:

| Action | What it does |
| --- | --- |
| Webex Controls | Shows Conversation controls without opening the saved link |
| Reference Track | Host-only song source and route panel; loading never starts playback |
| Studio | Opens live completed-take review, or the current song workspace when WebJam was opened in Reference Studio |

Studio is intentionally absent from More. Use the direct **Studio** control or
Cmd/Ctrl+3 so there is one obvious route to the existing workspace.

| Item | What it does |
| --- | --- |
| Audio Settings in Jamulus | Brings the owned Jamulus window forward; use its Audio/Network Settings menu |
| Webex Controls | Routes to the same Conversation panel as the direct action; it has no launch side effect |
| Jamulus Updates… | Checks WebJam's signed compatibility catalog, downloads an approved update, waits until the session is idle, and offers explicit OS approval; managed previous-version rollback is macOS-only |
| Recording Setup | Sets Local Originals and takes storage; it does not alter Jamulus music routing |
| Reference Track… | Routes to the same host-only Track panel; source loading is independent, and current-source Play becomes eligible only after the Mac proves the required local BlackHole route |
| Use iPhone as Pocket Stage… | Starts an explicit, private-Wi-Fi developer-preview pairing window; it does not put phone audio in the jam |
| Notes | Opens session notes |
| Band Check / Verify Sound | Observes an already-live session without restarting it |
| Support | Creates a sanitized bundle only when you ask |

In Conversation on macOS, **Show Webex App** re-verifies Cisco's installed app.
If Webex is running, it dynamically verifies the exact Cisco PID and asks macOS
to activate that same app. If Webex is stopped, it launches the verified app
itself with no URL or document argument, then proves the exact path, PID,
publisher, and foreground state. Verification and native launch share one
filesystem-object-bound reference, so a replaced pathname cannot redirect the
request. Webex chooses which of its screens appears. This action never opens a
browser, hands off the meeting link, joins a meeting, or treats native request
acceptance as success. **Join / Open Meeting** is the only action that
hands the saved link to the operating system, once per click. **Change Link**
opens Settings. **Mute in Webex** shows the verified external app so you can
use its own Mute control; WebJam cannot verify or change Webex mute and does
not send a blind shortcut or touch Jamulus. Windows and Linux keep these
native-focus actions unavailable because their current packages do not verify
the installed app's publisher; use **Join / Open Meeting** there.

## Jamulus Updates

WebJam includes Jamulus 3.12.2 so a known-good version remains available
offline. **More → Jamulus Updates…** checks only WebJam's signed compatibility
catalog. It may download an approved version in the background, but it will not
install, activate, or roll back while music, hosting, practice, recording,
Reference Track, reconnection, or launch is active.

Jamulus may show its own red upgrade link before WebJam has approved that
release. Do not use that link for a WebJam-managed session; return to
**Jamulus Updates…**. WebJam keeps the known-good version until the newer one
passes its routing, RPC, recording, and Reference Track compatibility gates.

The dialog distinguishes **Available** (not downloaded), **Ready** (verified
bytes awaiting approval), **Deferred** (waiting for a clean stop), **Fallback**
(the known-good copy remains in use), and **Failed** (nothing was changed).
Use **Later** freely; the active version continues to work. On macOS, if a
managed update fails at launch or verification, use **Use previous version**
or continue with the embedded fallback. Windows and Linux use their normal
system installer recovery and retain the embedded fallback; WebJam does not
claim to roll back an OS-owned installation.

If a check cannot run, the dialog distinguishes ordinary offline access, a
trusted-connection problem, an unusable update-service response, and missing
packaged trust data. Follow its recovery text. If the problem repeats, choose
**More → Support** and save a Support Bundle; it includes the safe failure
category and TLS trust state without including your URL, local paths,
credentials, or the raw network error.

On a Mac, choose **Review license and install**, read the exact Jamulus terms,
then explicitly Agree or Not now. WebJam verifies the official Jamulus
Developer ID signature and Apple notarization and does not remove quarantine.
On Windows, the official Jamulus installer is unsigned; WebJam rechecks its
exact approved hash before you choose to open it, and Windows may show a
publisher/UAC warning. Linux opens the verified package in the normal desktop
package handler. WebJam never runs hidden `sudo`, disables Gatekeeper, or
changes another application.

Jamulus labels wrap after eight grapheme clusters and accept at most 16 UTF-16
units. The name fields show the same 8+8 preview. A short stage name stays on
one line; a longer accepted name may use two complete lines. WebJam rejects a
value Jamulus would silently shorten.

## Use iPhone as Pocket Stage — developer preview

Pocket Stage is currently an owner-device Xcode developer preview, not a
pre-signed iPhone binary. A v0.22.3 Mac DMG or ZIP includes **Pocket
Stage iPhone Setup** with the exact generated, CI-compiled Xcode project and
an optional **Open Pocket Stage in Xcode.command** convenience helper. Open
`WebJamPocketStage.xcodeproj` directly; if its file association fails, use
**Xcode → File → Open**. Then select your Apple Personal Team and a unique
bundle identifier, connect the iPhone, and press Run; release users do not need
the repository or XcodeGen.
Source developers can instead generate the checked-in project with
`ios/Generate Pocket Stage Project.command`. The app includes an in-app QR
scanner. Its text field is a Simulator/developer aid, not a physical-user
fallback, because the desktop intentionally does not expose the bearer pairing
text. If Camera permission is off, restore it in iPhone Settings and scan a
fresh code.

To try it, put the Mac and iPhone on the same private Wi-Fi, start the desktop
session normally, and choose **More → Use iPhone as Pocket Stage…**. The code is
one-use and expires after two minutes. It pins the phone to the desktop's
temporary self-signed certificate using the SHA-256 fingerprint of the exact
certificate DER bytes. If the phone disconnects, choose **New Code** and pair
again; the old QR and phone-local state are not reconnect credentials.

On macOS 15 or later, allow WebJam's **Local Network** request when you first
turn on Pocket Stage. If the phone remains on “Connecting,” open **System
Settings → Privacy & Security → Local Network**, enable WebJam, then stop and
restart iPhone sharing. macOS Application Firewall is separate: if needed,
allow WebJam in **System Settings → Network → Firewall → Options**. On Windows,
allow WebJam through Defender Firewall on **Private networks only**, never
public networks. On Linux, any firewall exception should likewise be limited
to the trusted private LAN. WebJam never changes a firewall rule or asks for
administrator access. An ad-hoc unsigned test build may be treated as a new app
identity after rebuilding and ask again.

After a computer sleep/wake gap or network-address change, WebJam retires the
old Pocket Stage listener and certificate rather than advertising a possibly
stale address. Open Pocket Stage again from **More** and scan the fresh code.
New codes are also refused before the temporary certificate could expire; stop
and reopen iPhone sharing to create a fresh identity.

After pairing, Pocket Stage can show current session/recording state and
session-local mix slots with bounded participant display labels. These labels
are visible only in the explicitly paired experience; the public Local
Companion API remains anonymous, and labels are excluded from logs,
diagnostics, and support bundles. The phone can change fader or mute and
add a timestamped marker to Session Canvas. A host may request recording
start/stop only after hosting, Jamulus connection, and the first-record /
Recording Setup choice are already complete on the desktop.

Pan remains a versioned snapshot field and reserved command, but this preview
does not present or apply it because Jamulus 3.12.2 has no proven client pan
command.

The current preview has no phone audio, participant identity beyond the bounded
paired-private labels, chat, reactions, solo command, rehearsal plan, section
or Studio transport, Studio editing, media transfer, or durable reconnect
credential. The existing Local API and Jamulus audio path do not change.
Physical iPhone pairing, OS permission/firewall recovery, interruption,
accessibility, mix correctness, recording, and rehearsal tests are **NOT RUN**
until recorded against exact builds.

## Reference Track — macOS source pilot

The host can choose the direct **Reference Track** action or **More → Reference Track…**
and inspect the same song-transport panel. Loading and route readiness are
independent: **Load Song…** accepts validated WAV/WAVE, AIFF, or FLAC even when
no playback route is ready. MP3 appears only when the packaged decoder reports
support. Loading decodes the first bounded audio block, so a source that cannot
produce usable audio fails during load. The panel shows source format, sample
rate, channels, duration, and a separate route state; **Recheck Route** refreshes
route evidence without starting playback. Play, pause, restart, paused seeking,
loop in/out, source trim, and an audible count-in remain transport controls.
Guests do not get the transport.

Reference Track is not Studio playback. Once the route is certified, its design
streams the song at 48 kHz into BlackHole channels 1/2, launches a separately
owned Jamulus client named `WebJam Track`, and isolates that client's returns
on BlackHole channels 3/4. The host must then hear it only through the normal
primary Jamulus mix, and every musician can adjust the `WebJam Track`
participant independently.

The v0.22.2 private test candidate keeps playback locked even on a Mac with
BlackHole installed. CoreAudio has a reported device-switch failure where its
process input query returns the output device instead, while Jamulus 3.12.2
does not expose an independent live-device query. Physical BlackHole,
direct-monitor, and two-endpoint evidence is also **NOT RUN**. WebJam therefore
does not turn a saved profile, process, moving meter, or synthetic test into
permission to route audio. Production refuses before scanning devices. There is
no user or environment override, and installing BlackHole, running setup, or
choosing **Recheck Route** cannot unlock downloaded v0.22.2.

In the current unreleased source, the production Mac backend derives initial
route authority from the machine. It requires macOS 14.2 or later and one
official, unambiguous BlackHole 16ch/64ch device at 48 kHz. Passing those
read-only prerequisite checks may make Play available; choosing Play then
checks both the owned primary and backing Jamulus PIDs, a session-unique
private backing profile and secret, separate ports, authenticated RPC,
connected roster, zero return faders, and combined route freshness. The
primary must remain on its physical interface while the backing client proves
the exact selected BlackHole device for input and output. One inherited
lifecycle claim prevents another WebJam window from starting a competing
16ch/64ch Track while the first backing child survives. BlackHole 2ch and
uncertain evidence are rejected. The constructor override is test-only; users
cannot bypass the production machine checks.

If private process, RPC, profile, secret, or route cleanup cannot be proved,
the panel reports cleanup pending. Choose **Stop** to retry. WebJam will not
replace the song or finish quitting until the retained cleanup succeeds; a
late startup failure cannot be reported as an earlier clean Close.

This is **Jamulus-routed**, not latency eliminated. It gets Jamulus's usual
buffering, jitter handling, and network delay. A server recording captures it
as a separate participant stem. WebJam shows only the source filename; the
folder path is never saved to settings or written to logs.

Machine-derived route eligibility is not a claim that anyone can hear clean
audio. Windows/Linux routing and physical two-endpoint macOS audibility are
not yet certified. Device-switch truth, BlackHole exclusivity, independent
mixes, no-direct-monitor proof, server-stem alignment, route removal, repeated
teardown, and a long rehearsal remain **NOT RUN** until recorded against an
exact controlled source build using the
[physical pilot](docs/plans/webjam-reference-track-macos-pilot.md).

## Recording

The host controls the shared multitrack take. At the first Record click, choose
either **Record Shared Jam Only** or **Also Keep This Mac’s Inputs**. The first
choice begins the shared take. The second opens Recording Setup so the musician
can explicitly choose an eligible two-channel local-capture device. A guest is
never blocked from joining because Local Originals are not configured.

## Studio and Track Export

Studio is designed for familiar multitrack review and arrangement. It
deliberately does not integrate with or control Logic or another editor.

- Drag a region to move it or drag an edge to trim it. The selected region can
  also be split, duplicated, disabled, or deleted without changing its WAV.
- Use the timeline ruler, zoom, scroll, snap choices, markers/sections, fades,
  crossfades, and cycle/loop range to review the arrangement. Track trim/fader,
  pan, mute, solo, export inclusion, and master choices are non-destructive.
- Choose **＋ Section** to name a Verse or Chorus, then drag its section bar in
  the ruler to move that whole block across every track as one Undo step.
- Use Arrow keys to select Arrange rows and regions, Alt+Left/Right to nudge,
  and Ctrl+Left/Right Bracket to trim an edge to the playhead. Ctrl+Alt+A
  auditions a selected take lane, Ctrl+Alt+C comps its selected region, and
  Ctrl+Alt+Left/Right reorders the named section at the playhead.
- Use normal Undo/Redo shortcuts to restore exact Studio snapshots. Edits
  autosave to a separate sidecar; if saving fails, the edit stays pending and
  Studio tells you that the recorded take is safe. WebJam also refuses to quit
  while that final save retry is still failing.
- Select a track and choose **＋ Add Take** to use a safely matching repeated
  take from the same session. Double-click the lane name to audition it without
  changing the saved comp. Option/Alt-drag a lane to choose a comp range.
- Waveforms arrive progressively for the visible timeline. Recorded gaps remain
  silence, and changing takes cancels stale waveform work.

Track Export produces equal-length 24-bit edited stems, aligned unity originals,
and a rough mix, plus markers, import instructions, the exact Studio document,
source manifests, provenance, and checksums. It fails closed if a source or
manifest changed instead of guessing. Importing that package in an external
editor is still a separate physical workflow gate; it is **NOT RUN** for the
v0.22.3 source tree.

Edited Studio packages require the secure descriptor-relative export available
on macOS/Linux. On Windows, Studio instead labels the action **Export Aligned
Originals**. It creates unity aligned originals and a reference mix from the
current trim, fader, pan, mute, and solo choices, while explicitly excluding
region edits, fades, comps, sections, master processing, and attached/repeated
take lanes.

Studio never rewrites the take manifest or source recordings. Removing a region,
take lane, or comp range changes only Studio's arrangement sidecar.

If Studio cannot save an arrangement choice, it keeps the edit dirty, keeps the
take open, and blocks switching away or quitting until a later retry succeeds.
An earlier export result is cleared when you change takes or make a new edit;
WebJam never presents one take's export as proof for another.

## Recovery

If music disconnects, WebJam shows what it can prove and keeps hosting and
recording truth conservative. Use **Audio Settings in Jamulus** for device or
native setup problems. Use **End Session** or **Leave Jam** for safe cleanup;
WebJam stops only processes it owns and finalizes a hosted take before ending
the server.
