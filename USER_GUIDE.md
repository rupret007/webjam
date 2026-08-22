# WebJam creator guide — v0.26.0

> This guide describes immutable v0.26.0, the GitHub **Latest** private test
> release. Use only its exact checksum-verified assets when a downloadable
> package is required. Windows is unsigned and macOS is ad-hoc signed and
> unnotarized. Every v0.26 physical result remains **NOT RUN** until separately
> observed against an exact release asset.

## Follow the current guide

The always-visible Session HUD is the dominant action surface. **Notes** opens
Session Canvas, where **NOW** repeats the same status, next step, evidence-based
reason, output results, and recent meaningful events. Studio repeats the shared
next step while showing take validation, non-destructive edit/save state, and
export outcome. These are views of one result, not separate checklists.

The **Creative Pulse** below NOW summarizes explicit decisions, actions,
blockers, questions, references, and creator-profile checkpoints from your local
notes. It can help decide what to do or arrange next, but it never changes
WebJam's operational status. For example, typing “recording finished” cannot
create a take or unlock export.

At launch, choose **Music** (GA), **Podcast & Voice** (GA), **Review &
Rehearsal** (Preview), or **Art** (Preview). The profile follows the
launch, live session, recording, Studio, local session metadata, and new
standalone projects. Legacy content without a saved profile opens as Music.
Review & Rehearsal allows live WebJam-audio Host/Join, Record Session, local
notes, and playback/read-only review of completed session takes. It blocks
standalone projects, take editing/comp/mix mutation, track export, shared
notes, visual sync, and media timecode. Art is a room for artists in any
medium; see [Art](#art) below. No profile directly or automatically taps a
meeting app, browser, or system output.

## Art

Art (Preview) opens a room where you talk while you work — painting, drawing,
sculpting, anything at a table. When you choose Art at launch, you pick one of
three ways to start, and nothing more:

- **Talk & make** — just the room and your voices. Make whatever you're making,
  on paper, in clay, or in whatever you already use. This is a completely
  normal room; nothing asks you to share anything.
- **Paint together** — the room, plus one canvas you all draw on.
- **Paint along** — the room, plus one video you all watch in step.

The first screen deliberately does not name the programs behind those last two.
You find out which one to install at the moment you need it, in the room, and
only if it is missing.

AI image work is *not* a fourth choice: it is an in-session action available
from any of the three, because nobody plans a session around an image
generator.

Then **Host** or **Join** as in every other profile. Joining is one pasted
invitation: it carries whatever the host started, so there is nothing else for
a guest to choose. The host can add the other option later from inside the
room, so you never have to pick both up front.

### Painting together

WebJam does not paint. Drawpile does — it is the open-source collaborative
painting program, with its own brushes, layers, and export. WebJam finds it,
opens it, and carries the invitation so nobody has to be sent a second link.

1. Everyone installs [Drawpile](https://drawpile.net/download/). WebJam does
   not install it for you, and it will say plainly when it cannot find it.
2. The host chooses **More ▾ → Shared Canvas…**, then **Host in Drawpile**.
   Drawpile opens on its own Host page. Leave the session set to **Personal**
   so only people with your invitation can join.
3. Once the canvas is up, the host copies Drawpile's invitation
   (**Session → Invite**) and pastes it into WebJam's **Share with the room**
   field. Include the password when Drawpile offers to.
4. Everyone else opens the same panel and chooses **Open shared canvas**.
   Drawpile opens on the host's canvas. Someone who joins the room later gets
   the same canvas automatically.

Things worth knowing:

- **Only the host chooses the canvas.** A guest can open it, and nothing more.
- **WebJam cannot see the canvas.** Drawpile shows who is actually painting;
  WebJam will never claim to know.
- **Brushes, colours, and layers live in Drawpile.** WebJam deliberately has
  none of them.
- **Leaving the WebJam room does not close Drawpile.** It is your own program.
- If WebJam cannot find Drawpile, or cannot read the invitation it was given,
  it says so and opens nothing rather than showing you an empty window.

### Painting along to a video

If you want a reference to work from, the host can share one video file:

1. The host chooses **More ▾ → Reference Video…**, then **Share Video…**, and
   picks a local video file they have the right to play. WebJam does not ship,
   bundle, download, or fetch any video, and it will not open anything from a
   streaming service.
2. Everyone else opens the same panel and chooses **Open My Copy…**, pointing at
   their own copy of that same file. WebJam checks that it really is the same
   file. If it is not, it says so and plays nothing rather than showing you the
   wrong thing.
3. The host presses play, pause, stop, or drags the position. Everyone follows.
   If you join partway through, you land where the host currently is.

Things worth knowing:

- **Only the host controls playback.** Guests have no play, pause, stop, or
  scrub control, by design.
- **You can hide the video** at any time and keep working. You stay in the room
  and in the conversation.
- **If your copy moves, changes, or disappears**, WebJam stops following and
  tells you, instead of drifting silently.
- **If WebJam loses track of the host's position**, it holds rather than
  guessing. Playback resumes when the host is heard from again.
- **This is not frame-accurate review.** Everyone stays within about a second
  of the host. There is no timecode, and Art is not a video review tool.

### Making or editing an image with AI

Once you are in the room, **More ▾ → AI Image…** offers two things and nothing
else: **Make** a new image from a description, or **Edit** a photo you already
have.

WebJam does not generate anything. Krita's **AI Image Generation** plugin does,
on your own computer.

1. Install [Krita](https://krita.org/en/download/) 5.2.0 or newer, then install
   [Krita AI Diffusion](https://github.com/Acly/krita-ai-diffusion/releases/latest)
   into it (Krita → Tools → Scripts → Import Python Plugin from File).
2. In WebJam, choose **Make** for a fresh canvas, or **Edit…** and pick an image
   you own. Krita opens.
3. In Krita, use **Settings → Dockers → AI Image Generation**. Type your
   prompt there, or select part of your photo and fill, extend, or remove it.
   The first time, the plugin will offer to install its local backend for you.

Things worth knowing:

- **Everything stays on your computer.** WebJam only ever looks for an image
  backend at a loopback address, and refuses a remote or cloud one outright.
  Your photos are never uploaded, and no API key is needed.
- **What you make is your file.** It is not sent to the room. If you want the
  others to see it, drop it on the shared canvas yourself.
- **Nobody generates on your behalf.** The host cannot Make or Edit on your
  machine, and you cannot on theirs. Guests use it for themselves.
- **The prompt, the model, and the settings are Krita's.** WebJam has no prompt
  box and no model list, on purpose.
- If Krita or the plugin is missing, WebJam says which one and offers the
  download rather than opening an editor that cannot generate.

### Seeing where the room is

If the room has a pulse, the shared canvas panel shows it in one line, so you
can keep painting and still know where everyone is:

- **Bar 17.3 · Chorus** — something in the room owns a song, and you are riding
  its bars.
- **2:14 / 5:30** — the host's reference video is running, and that is the
  position in the file.
- **No shared clock** — the room has neither. That is normal; work freely.

Things worth knowing:

- **It is a readout, not a control.** The room has one owner of its pulse, and
  reading it does not make you that owner.
- **A video position is never shown as a bar.** A place in a file is not a
  place in a song, and WebJam will not pretend otherwise.
- **If WebJam loses track of the owner**, it stops the clock and says so rather
  than drifting.

### What Art does not do

Art has no camera feed and **does not record the session**, so there is no take
to review afterwards. It has no image generator of its own, no model list, and
no cloud image service, and no song engine, metronome, or chord detection — it
reads a musical pulse that something else in the room owns. Your notes stay local to your own computer as in every
other profile. There is no standalone Art project in this Preview.

Art is also the whole product on your desktop. If you want to see faces, use
the Conversation card's **Show Webex App** and **Join / Open Meeting** as in
every other profile: a free or personal Webex account is enough, and Webex is a
second window beside WebJam rather than something WebJam runs. Remember that
WebJam's mute and Webex's mute are separate controls, and that leaving a WebJam
room does not leave your meeting.

Each profile has a separate local scratchpad on this computer. Switching
profiles safely saves and loads the matching private file; reads refuse links
or files over 1 MiB. Scratchpad content is never shared, synchronized with
another participant, or tied to media timecode.

## What each app does

WebJam conducts the live session. It starts or joins the private session, creates
and checks invitations, keeps recording truth, and provides Studio.

Jamulus is WebJam's live audio engine. It owns your interface, inputs, outputs,
channels, buffer, jitter, feedback protection, and participant mix. Configure
those in Jamulus, not in WebJam.

Any meeting platform can provide optional talking/video when its meeting link
is a public HTTPS URL with a DNS hostname and passes WebJam's safety checks.
Known Webex, Zoom, Microsoft Teams, Google Meet, and FaceTime links receive
friendly labels; any other accepted provider uses neutral Conversation
wording. The direct **Conversation** action reveals the controls without
opening or rejoining a meeting. WebJam can validate and explicitly open the
link, but it does not claim that the service joined, muted, selected devices,
or sees anyone, and it does not natively verify an unknown provider. WebJam's
native app checks are exclusively for Webex. If Webex is missing, an explicit
button opens Cisco's official installer in your browser; WebJam does not save
a Webex password or install/update Webex silently.

WebJam never directly or automatically taps Webex, Zoom, Teams, Google Meet,
FaceTime, another meeting app, a browser, or system output. Record Session
includes authoritative Jamulus server stems and explicitly planned Local
Originals from only the input devices you select. Do not route meeting or
system audio into those inputs. Use the meeting service's own recording feature
separately if that is required.

## Host a live session

Choose **Host**, **Host Remote Recording**, or **Host Review** for the selected
profile. That action authorizes the private session—there is no extra Start
Session click. WebJam starts the server first, then opens a
visible Jamulus client against it. Set up sound in Jamulus; once WebJam sees
the authenticated connection, it moves straight into the session without an
extra setup, sound-confirmation, or Enter Jam click. Play a note and make sure
you can hear each other—WebJam never treats a meter or connection as proof of
that. Use Music's **Band Check**, Podcast's **Sound Check**, or Review's
**Session Check (Preview)** if you need help.

If setup is interrupted, WebJam keeps only a private, bounded recovery record.
It reuses progress only when the role and dedicated Jamulus profile fingerprint
still match. Otherwise it returns safely to native Jamulus setup.

## Join a live session

Choose **Join**, **Join Recording**, or **Join Review**, then paste one WebJam
invite—or open it from Finder. WebJam parses it at ingress,
does not leave the bearer visible in the interface, and starts only the guest
services required for that invitation. If setup fails, the in-memory intent is
preserved only while it is safe to retry; WebJam never pretends an uncertain
invitation succeeded.

## Main session actions and More

The main session rail keeps the everyday destinations visible:

| Action | What it does |
| --- | --- |
| Conversation | Shows meeting controls without opening the saved link |
| Shared Track | Host-only live waveform and transport; loading never starts playback |
| Studio | Opens live completed-take review, or the current song workspace when WebJam was opened in Reference Studio |

Studio is intentionally absent from More. Use the direct **Studio** control or
Cmd/Ctrl+3 so there is one obvious route to the existing workspace.

| Item | What it does |
| --- | --- |
| Audio Settings in Jamulus | Brings the owned Jamulus window forward; use its Audio/Network Settings menu |
| Conversation | Routes to the same panel as the direct action; it has no launch side effect |
| Jamulus Updates… | Checks WebJam's signed compatibility catalog, downloads an approved update, waits until the session is idle, and offers explicit OS approval; managed previous-version rollback is macOS-only |
| Recording Setup | Sets Local Originals and takes storage; it does not alter Jamulus music routing |
| Shared Track… | Routes to the same host-only transport; source loading is independent, and current-source Play becomes eligible only after the Mac proves the required local BlackHole route |
| Use iPhone as Pocket Stage… | Starts an explicit, private-Wi-Fi developer-preview pairing window; it does not put phone audio in the live session |
| Notes | Opens session notes |
| Band Check / Sound Check / Session Check | Profile-specific presentation of the same bounded live-audio observer; it does not restart the session |
| Support | Creates a sanitized bundle only when you ask |

In Conversation on macOS, the Webex-only **Show Webex App** action re-verifies
Cisco's installed app.
If Webex is running, it dynamically verifies the exact Cisco PID and asks macOS
to activate that same app. If Webex is stopped, it launches the verified app
itself with no URL or document argument, then proves the exact path, PID,
publisher, and foreground state. Verification and native launch share one
filesystem-object-bound reference, so a replaced pathname cannot redirect the
request. Webex chooses which of its screens appears. This action never opens a
browser, hands off the meeting link, joins a meeting, or treats native request
acceptance as success. **Join / Open Meeting** is the only action that
hands the saved link to the operating system, once per click. **Change Link**
opens Settings. **Open Webex to Mute** shows the verified external app so you can
use its own Mute control; WebJam cannot verify or change Webex mute and does
not send a blind shortcut or touch Jamulus. Windows and Linux keep these
native-focus actions unavailable because their current packages do not verify
the installed app's publisher; use **Join / Open Meeting** there.

## Jamulus Updates

WebJam includes Jamulus 3.12.2 so a known-good version remains available
offline. **More → Jamulus Updates…** checks only WebJam's signed compatibility
catalog. It may download an approved version in the background, but it will not
install, activate, or roll back while music, hosting, practice, recording,
Shared Track, reconnection, or launch is active.

Jamulus may show its own red upgrade link before WebJam has approved that
release. Do not use that link for a WebJam-managed session; return to
**Jamulus Updates…**. WebJam keeps the known-good version until the newer one
passes its routing, RPC, recording, and Shared Track compatibility gates.

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
pre-signed iPhone binary. A v0.22.5 Mac DMG or ZIP includes **Pocket
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
paired-private labels, chat, reactions, solo command, session plan, section
or Studio transport, Studio editing, media transfer, or durable reconnect
credential. The existing Local API and Jamulus audio path do not change.
Physical iPhone pairing, OS permission/firewall recovery, interruption,
accessibility, mix correctness, recording, and live-session tests are **NOT RUN**
until recorded against exact builds.

## Shared Track — macOS private test release

The host can choose **Add Shared Track**, drop one supported local file on the
live-session surface, or open **Shared Track** / **More → Shared Track…** for
the same complete transport. Loading and route readiness are independent:
**Add Track…** accepts validated WAV/WAVE, AIFF, or FLAC even when no playback
route is ready. Picker and drop use exactly the same validation. MP3 appears
only when the packaged decoder reports support. The
structural MP3 scan accepts the gapless headers real-world encoders write
(LAME, and ffmpeg's Lavc/Lavf) and one trailing APE tag before the optional
ID3v1 trailer; a file that still fails names its exact structural reason
without exposing the file path. Loading decodes the first bounded audio
block, so a source that cannot produce usable audio fails during load. If
playback ever starves and emits silence, the panel reports audible dropouts
instead of silently claiming clean playback. The live deck and full transport
show the path-free source name, duration, progressive waveform, playhead,
count-in, and separate route/cleanup state. **Recheck Route** refreshes route
evidence without starting playback. Play, pause, stop, restart, paused seeking,
loop in/out, source trim, and an audible count-in remain transport controls.
**Replace…** and **Remove** require a safe stopped state; an attempted change
during playback is refused. Guests do not get the transport.

Shared Track is not Studio playback. Once the route is certified, its design
streams the song at 48 kHz into BlackHole channels 1/2, launches a separately
owned Jamulus client named `WebJam Track`, and isolates that client's returns
on BlackHole channels 3/4. The host must then hear it only through the normal
primary Jamulus mix, and every participant can adjust the `WebJam Track`
participant independently in Jamulus. Guest WebJam surfaces receive bounded,
path-free state through the authenticated peer session but do not receive
transport authority. Older peer state may expose only the dedicated channel;
WebJam does not infer synchronization, isolation, or audibility from roster
presence.

The v0.22.2 private test candidate keeps playback locked even on a Mac with
BlackHole installed. CoreAudio has a reported device-switch failure where its
process input query returns the output device instead, while Jamulus 3.12.2
does not expose an independent live-device query. Physical BlackHole,
direct-monitor, and two-endpoint evidence is also **NOT RUN**. WebJam therefore
does not turn a saved profile, process, moving meter, or synthetic test into
permission to route audio. Production refuses before scanning devices. There is
no user or environment override, and installing BlackHole, running setup, or
choosing **Recheck Route** cannot unlock downloaded v0.22.2.

In published v0.22.4, the production Mac backend derives initial
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
replace or remove the song or finish quitting until the retained cleanup
succeeds; a late startup failure cannot be reported as an earlier clean Close.

This is **Jamulus-routed**, not latency eliminated. It gets Jamulus's usual
buffering, jitter handling, and network delay. A server recording captures it
as a separate participant stem; Studio classifies that stem as **Shared
Track**, not as another participant. WebJam shows only the source filename; the
folder path is never saved to settings or written to logs.

Machine-derived route eligibility is not a claim that anyone can hear clean
audio. Windows/Linux routing and physical two-endpoint macOS audibility are
not yet certified. Device-switch truth, BlackHole exclusivity, independent
mixes, no-direct-monitor proof, server-stem alignment, route removal, repeated
teardown, and a long session remain **NOT RUN** until recorded against an
exact controlled source build using the
[physical pilot](docs/plans/webjam-reference-track-macos-pilot.md).

## Record Session

The host controls the shared multitrack take. At the first **Record Session**
click, choose the profile's shared-only action—**Record Shared Jam Only**,
**Record Shared Voice Take Only**, or **Record Shared WebJam Audio Only**—or
choose **Also Keep This Mac’s Inputs**. A shared-only choice begins the take.
The input choice opens Recording Setup so the creator can choose an eligible
local-capture device and edit
named mono/stereo input tracks totaling up to 32 enabled Local Original input
channels. Tracks allocate device channels sequentially; an empty track list
preserves the compatible two-input default. Each mono row creates one mono
PCM-24 WAV. Each stereo row binds adjacent device channels and creates one true
two-channel PCM-24 WAV; that topology remains stereo through recovery, Studio,
and export. If every row is opted out, no host Local Original is recorded. A
guest is never blocked from
joining because Local Originals are not configured.

Before any recorder starts, WebJam freezes one take-scoped plan with the exact
roster/server stem IDs, Shared Track fingerprint and playback generation, host
mono/stereo topology, each guest's path-free Local Original count/map
obligation and presence generation, count-in, storage verdict, and expected
source count. Finalization rechecks those facts and refuses source
substitution, a changed map, or missing/extra delivery instead of calling the
take Ready.

WebJam v0.26.0 presents that frozen plan in one accessible,
path-free **Record Session Readiness** sheet. Every server track, Local
Original, and Shared Track row shows its source label, exact mono/stereo format,
required/optional status, readiness, and a bounded meter when available.
Storage and Shared Track cards sit above explicit blockers. **Start Recording**
is disabled while a required fact is unresolved. Accepting the sheet is not a
bypass: WebJam rechecks the take/plan generation, roster, input maps, guest
obligations, device preflight, storage, and Shared Track identity before it
arms anything. Cancelling retires the provisional take without starting
capture.

For each opted-in guest Local Original, WebJam next sends a private,
take-scoped arm only to that required guest. The guest opens the exact planned
input stream and authenticates an acknowledgement bound to the take, plan,
presence generation, map, ordered widths, and stable source IDs. The Jamulus
recorder stays off until every required acknowledgement is current and the host
rechecks all authority again. A zero-track opt-out does not block; timeout,
disconnect, device-open failure, stale identity, or mismatched topology cancels
the arm and starts no server recorder.
If host commit truth is temporarily unknown after acknowledgement, the guest
keeps that audio local and recovery-only. It is not uploaded until authenticated
state for the same take reaches Recording or a terminal result.

Every planned source has one stable logical-source ID from the server or input
map through guest transfer, take manifest, recovery, and Studio. Each width is
exactly one mono channel or one stereo pair. Missing or duplicate IDs, absent
width, topology changes, and extra/missing delivered files fail closed instead
of falling back to display names or row order.

WebJam shows the take's actual progression: **Idle**, **Preparing**,
**Count-in**, **Recording**, **Stopping**, **Finalizing**, **Ready**, **Needs
attention**, or cleanup pending. If a Shared Track is ready, confirmed recorder
start begins its count-in/play path. Press **Stop Recording** once; WebJam asks
both owners to stop, but does not call the take complete until recorder
validation and Shared Track cleanup are each settled. A second Record request
cannot collide with a take that is still stopping or finalizing.

Guests see bounded recording state but cannot control the host recorder. Each
authoritatively correlated Jamulus participant appears once, the Shared Track has
its own stable source identity, and only explicitly enabled Local Originals
are added. Missing media, gaps, ambiguous identity, unverified timing, transfer
work, or publication failure remains visible rather than becoming a duplicate
or invented track.

While a take owns the plan, Studio's live source view distinguishes Jamulus
server, Local Original, and Shared Track lanes. It can show each source's
current state, level when available, reported dropouts, and overload warning.
If a projection is legacy, malformed, or duplicates a logical source, the view
clears it instead of presenting uncertain rows as recorder truth.

## Studio and Track Export

Studio is designed for familiar multitrack review and arrangement. It
deliberately does not integrate with or control Logic or another editor.

The editing, arrangement, comping, mix-mutation, and export steps below apply
only to Music and Podcast & Voice. Review & Rehearsal Preview can play, scrub,
and inspect a completed take and its sources, but it cannot mutate the take's
arrangement or mix, create a Studio sidecar, or export tracks. Art
does not record a session at all, so it has no take and no Studio.

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
- When a new Music or Podcast & Voice take completes, Studio automatically
  stacks only earlier lanes with the same session, project rate, unique stable
  logical-source ID, participant/source kind, mono/stereo topology, verified
  timing, and Shared Track fingerprint where applicable. It skips legacy,
  duplicate, incomplete, or ambiguous matches. Review Preview never performs
  this automatic edit or creates a sidecar.
- Waveforms arrive progressively for the visible timeline. Recorded gaps remain
  silence, and changing takes cancels stale waveform work.

Track Export produces equal-length 24-bit edited stems, aligned unity originals,
and a rough mix, plus markers, import instructions, the exact Studio document,
source manifests, provenance, and checksums. It fails closed if a source or
manifest changed instead of guessing. Importing that package in an external
editor is still a separate physical workflow gate; it is **NOT RUN** for the
v0.26.0 private test release.

For a standalone Podcast & Voice episode, use the 48 kHz Host-mono +
Guest-stereo preset, record the first pass, add a chapter marker, set a cycle
for loop overdub, and stop when the alternate pass is complete. Save and reopen
to verify the chapter and channel topology, then choose **Bounce Episode** for
a verified stereo PCM-24 WAV. Review & Rehearsal Preview cannot create/open
this local project and blocks edit, mix, save, bounce, and export operations at
their lower-level controller entry points as well as in the visible UI.

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
native setup problems. Use **End Session** or the profile's **Leave** action for
safe cleanup;
WebJam stops only processes it owns and finalizes a hosted take before ending
the server.

The [v0.26 physical checklist](V026_CREATOR_MULTITRACK_PHYSICAL_TEST_CHECKLIST.md)
still has every physical observation and release-decision row **NOT RUN** and
must be executed only against an exact checksum-verified release package. The
[v0.25 checklist](V025_CREATOR_MULTITRACK_PHYSICAL_TEST_CHECKLIST.md) remains
immutable historical evidence for the prior release.
