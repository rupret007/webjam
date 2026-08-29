# WebJam

## Native creator collaboration and multitrack recording

WebJam is a creator-first desktop conductor for low-latency collaboration,
authoritative multitrack recording, arrangement, and review. It brings the
session lifecycle, Jamulus, provider-neutral meeting handoff, Studio, and an
owner-controlled iPhone companion into one understandable workflow—without
pretending to own systems that remain independent.

[![WebJam CI](https://github.com/rupret007/webjam/actions/workflows/ci.yml/badge.svg?branch=master)](https://github.com/rupret007/webjam/actions/workflows/ci.yml)
[![MIT license](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Latest release](https://img.shields.io/github/v/release/rupret007/webjam?label=Latest%20test%20candidate)](https://github.com/rupret007/webjam/releases/latest)

> **Testing download:** GitHub [**Latest**](https://github.com/rupret007/webjam/releases/latest)
> resolves to the published unsigned/ad-hoc
> [v0.27.1 private test release](https://github.com/rupret007/webjam/releases/tag/v0.27.1),
> release ID `377614785`, from exact tag commit
> `1fc25f87c3386b1cd94303ecb407cdaff6509d1f`. It has seven packages plus
> `WebJam-v0.27.1-SHA256SUMS.txt`. Windows is unsigned; macOS is ad-hoc signed
> and unnotarized. Tag workflow `33045632613` built all four desktop targets,
> but the workflow is **not publish-green**: its publisher failed closed because
> a release already used `v0.27.1`. Do not retag or mutate that release.

> **Candidate source boundary:** this checkout reports unsigned v0.27.2 and is
> post-v0.27.1-tag source. No v0.27.2 tag, draft, release, package, checksum,
> or physical PASS exists. The approved Jamulus ranges stop at v0.27.1, so
> v0.27.2 source deliberately fails closed for live Host/Join and the required
> component-input build gate. It is not a usable package until separate
> compatibility evidence is reviewed. Changes after the v0.27.1 tag are
> not in the v0.27.1 download. This checkout and branch artifacts are source
> evidence, not
> release packages. Shared Track play uses this Mac's official BlackHole
> 16ch/64ch route at 48 kHz and the bundled headless client; it does not wait
> for a signed catalog pin. The catalog remains sealed at exact WebJam v0.22.5.

> **Download boundary:** use a v0.27.1 package only when its exact filename and
> SHA-256 appear on the immutable
> [v0.27.1 GitHub release](https://github.com/rupret007/webjam/releases/tag/v0.27.1).
> Every physical v0.27 gate remains **NOT RUN**. Do not substitute this
> post-release checkout or a branch artifact.

New to WebJam? Start with the [simple-language guide](README_SIMPLE.md) or
[First Jam](FIRST_JAM.md); this README is the complete technical story.

## At a glance

| Area | Current state |
| --- | --- |
| Product | Creator-facing desktop conductor around Jamulus, optional external meeting conversation, Studio, and Pocket Stage |
| Published line | Unsigned/ad-hoc v0.27.1 GitHub Latest private test release; verify its checksum manifest |
| Current source line | Unsigned v0.27.2 source candidate; live Host/Join and package builds are compatibility-blocked |
| Trust posture | Windows unsigned; macOS ad-hoc signed and unnotarized |
| License | [MIT](LICENSE), with third-party notices shipped separately |
| Supported package targets | Windows x64, Ubuntu 22.04 x64, Intel Mac, Apple-silicon Mac |

WebJam has two deliberately separate creator workflows:

- Profile-specific **Host** and **Join** actions keep private live audio simple:
  set up sound in Jamulus, then collaborate.
- **Reference Studio** is a standalone creation workspace for playing with
  reference audio, recording ideas, arranging takes, mixing, and bouncing a
  demo. Music and Podcast & Voice projects do not require or alter a Jamulus
  session; Review & Rehearsal does not claim a standalone project in Preview.

WebJam is a conductor, not a replacement for the tools creators already trust.

## Five-minute demo

1. Open WebJam and choose **Art** or **Music**. Use **Podcast or review** only
   when that is the room you came to make.
2. For Art, choose **Talk & make**, **Paint together**, or **Paint along**,
   then **Host** / **Join**. Music uses **Host** / **Join**; Podcast & Voice
   and Review & Rehearsal retain their profile-specific Host/Join labels.
3. Configure interface, channels, headphones, and buffer in Jamulus. Confirm
   the authenticated audio connection, then use the profile-specific
   **Band Check**, **Sound Check**, or **Session Check** if needed.
4. Open **Conversation** only when conversation or video is wanted; use
   **Join / Open Meeting** for an explicit meeting-link handoff.
5. In an Art **Paint along** room, the video becomes the large WebJam
   workspace once the room exists. The host chooses **Share…**; each guest
   chooses **Open my copy…** for the same local file. **Back to room** returns
   to the conductor without ending the room or the video.
6. In a profile that supports Shared Track, choose **Add Shared Track** or
   drop supported reference audio on the live surface; loading does not start
   playback, and Play remains fail-closed until the isolated Jamulus route is
   proven.
7. In a recording-capable profile, choose **Record Session**, inspect every
   exact source in the readiness
   sheet, and start only when its required sources, storage, and Shared Track
   are ready. Then wait through **Finalizing** before opening the ready take in
   **Studio**.
8. For Music or Podcast & Voice, open the profile's local-project action for a
   separate creation and arrangement workspace. Review & Rehearsal correctly
   keeps standalone projects unavailable in Preview; Art intentionally has no
   recording or standalone-project path.

## Creator profiles

Current v0.27.2 source applies one saved creator profile across launch,
Host/Join, readiness, the live surface, recording, Studio, session records, and
new standalone projects:

| Profile | Status | Implemented boundary |
| --- | --- | --- |
| Music | GA | Jam/rehearsal language, Shared Track, authoritative session recording, completed-take Studio, and music-focused Reference Studio defaults |
| Podcast & Voice | GA | Session/speaker/microphone language, episode/reference-audio vocabulary, voice-focused input defaults, authoritative recording, and local project workflow |
| Review & Rehearsal | Preview | Live WebJam-audio Host/Join and Record Session plus playback/read-only completed-take review; no standalone project, take edit/comp/mix mutation, track export, shared notes, visual sync, or media timecode |
| Art | Preview | Live room with Talk & make, Drawpile-backed Paint together, or an embedded host-clocked Paint along workspace; no recording, take, standalone project, shipped video, or frame-accurate/timecode claim |

Profiles change presentation and safe defaults, not evidence rules. A legacy
project, take, or session with no profile metadata migrates to Music. Review &
Rehearsal always remains visibly marked Preview and fails closed where its
standalone workflow is not implemented.

## System ownership

| Product | Owns |
| --- | --- |
| WebJam | Private-session lifecycle, invitations, session truth, recording, Studio, export, recovery |
| Jamulus | Live interface, input/output channels, buffer, jitter, participant mix, and WebJam audio connection |
| Meeting service | Optional talking/video meeting and its own microphone, camera, speaker, participant state, and recording; WebJam never directly or automatically taps the meeting app, browser, or system output |

Room-clock delivery is fail-closed. A host may render its own current song or
reference-video position immediately, but WebJam records that pulse as shared
only after the authenticated private-session control accepts it. An inactive,
not-yet-attached, or temporarily failing peer plane is retried on the next
clock tick; it is never presented as proof that another participant received
or heard anything.

## Live collaboration flow

1. Open WebJam, choose a creator profile, then choose its Host or Join action.
2. WebJam starts the private session or consumes the invitation using the
   profile-specific vocabulary.
3. On a fresh Mac setup, Jamulus opens its dedicated WebJam profile and may
   need you to choose the interface, input channels, headphones, and buffer
   once. WebJam does not open the regular Jamulus profile or request access to
   data from other apps.
4. Jamulus opens normally. Choose the interface, channels, headphones, and
   buffer in **Jamulus → Settings → Audio/Network Settings**.
5. When WebJam sees the authenticated audio connection, it opens the session
   automatically; the host then copies the invitation.
6. Make sound and verify you can hear each other. Use **Band Check**, **Sound
   Check**, or **Session Check** for the selected profile if you need help.
7. Choose the direct **Conversation** action if participants want conversation
   or video. It shows WebJam's Conversation controls without opening a meeting;
   WebJam's live audio remains in Jamulus.
8. The host can add a **Shared Track** from the live surface. Its proven route
   enters Jamulus through the separately owned `WebJam Track` participant;
   every listener still verifies the result by listening.
9. Choose **Record Session** when the session is ready. One Stop action moves the
   take through **Stopping** and **Finalizing**; only **Ready** is a completed
   take that can open in Studio.

There is no WebJam input/output picker, server field, port field, or
profile-specific Check gate in Host/Join.

## Bounded Jamulus recovery

Since v0.22.4, the source treats a running Jamulus process as necessary but not
sufficient recovery evidence. Each replacement is bound to the exact recovery
generation, process generation, and process ID that WebJam launched. WebJam
returns to Connected only after that same process has fresh authenticated RPC
activity and its local participant row is proved in the roster. Delayed evidence
from an earlier process cannot authenticate its replacement.

Automatic recovery is bounded to five starts. While one is pending or in
flight, WebJam reports Recovery in progress instead of starting competing
clients. If the bounded attempt set is exhausted, automatic recovery stops and
the creator gets an explicit fresh Host/Join restart path; a timer cannot
silently create a sixth attempt or call an unauthenticated process Connected.
The Support Bundle records only the immutable generations, attempt state,
process ID/liveness, finite RPC freshness category, and finite RPC age. It
never includes the Jamulus profile path, RPC secret, invitation, meeting link,
or raw exception.

This recovery work first shipped in immutable v0.22.4, remains in historical
v0.22.5, and carries into later candidates. Publication does not convert any
physical gate to PASS.

## Jamulus updates without rebuilding WebJam

WebJam keeps its reviewed Jamulus 3.12.2 client, server, and isolated
Reference Track companion as an offline fallback. In the background it checks
a separately published, Ed25519-signed component catalog for Jamulus versions
that have passed WebJam's exact routing, RPC, recording, and packaging
contracts. It never follows an upstream “latest” link blindly.

The packaged updater uses WebJam's release-locked Certifi CA set with hostname
verification and TLS 1.2 or newer; launch-environment CA overrides cannot
replace that trust root. The v0.22.2 package-only release probe verified this
exact boundary against the live signed catalog before GitHub Latest
publication.

An approved update can download without interrupting an active session, but it cannot
install, activate, or roll back while a client, server, Reference Track,
recording, practice session, reconnect, or launch is active. **More → Jamulus
Updates** shows the active, available, previous, deferred, fallback, or failed
state and provides Check, Download, Later, explicit install approval, and
recovery controls. On macOS, the verified current and previous managed copies
remain available for rollback. Windows and Linux keep installation OS-owned
and retain the embedded 3.12.2 fallback; they do not claim an app-managed
previous-version rollback.

That fallback statement applies to the released v0.27.1 line. The unsigned
v0.27.2 source identity is outside every immutable approved WebJam range, so it
must reject the bundled, installed, and managed Jamulus client/server until a
separate compatibility review supplies new evidence. Presence of fallback
bytes is not authorization.

On macOS, WebJam shows the exact packaged Jamulus license before accepting the
official disk image's agreement, then preserves and verifies the upstream
Developer ID signature, notarization, architecture, version, inventory, and
quarantine. On Windows the upstream Jamulus installer is unsigned, so WebJam
verifies its catalog-approved size and SHA-256 immediately before the user
chooses to open it. Linux opens the approved package through the desktop
package handler. WebJam never uses hidden elevation, `sudo`, a command shell,
Gatekeeper bypasses, or an update that mutates `WebJam.app`.

Jamulus itself displays participant labels on two lines after eight grapheme
clusters and accepts at most 16 UTF-16 units. WebJam now enforces that one
contract at every entry point and previews the actual 8+8 layout instead of
allowing Jamulus to silently shorten an identity.

## Optional conversation apps

WebJam accepts any meeting platform that provides a link passing one
provider-neutral policy: a public HTTPS URL with a DNS hostname, no embedded
credentials or custom port, and no local/special-use or IP-literal hostname.
Known Webex, Zoom, Microsoft Teams, Google Meet, and FaceTime links receive
friendly service labels; other accepted providers use neutral meeting-service
wording. All use the same explicit **Join / Open Meeting** handoff, and the
normalized link can be copied. WebJam never claims that a handoff joined,
muted, found participants, or verified an unknown provider. FaceTime links are
Mac-only. Native detection, publisher proof, installation, mute guidance, and
bring-forward remain explicitly Webex-only.

WebJam never directly or automatically taps a meeting app, browser, or system
output. Record Session captures the authoritative Jamulus server stems plus
only the explicitly planned Local Originals from input devices the user
selects. Do not route meeting or system-output audio into those inputs; use the
meeting service's own recorder if that audio is needed.

The direct **Conversation** action and **More → Conversation** reveal the
Conversation panel and never launch or rejoin a meeting. On macOS, **Show Webex App** works
without a meeting handoff: each click re-verifies the exact Cisco bundle. If
Webex is running, WebJam verifies its exact PID and asks macOS to activate it.
If Webex is stopped, WebJam launches that verified app itself with no URL or
document argument, then re-verifies its path, PID, publisher, and foreground
state. Verification and launch use one filesystem-object-bound file reference,
so replacing the pathname cannot redirect the request. Webex decides which of
its own screens appears. WebJam never passes the saved link to this action,
invokes a browser, or treats native request acceptance as foreground proof.
**Join / Open Meeting** is the only meeting-link handoff and opens it once per
explicit click; **Change Link** returns to Settings.

Direct native activation stays disabled when WebJam cannot prove the app's
publisher on that platform. The current Windows and Linux packages can detect
an app location but do not establish a trusted publisher identity, so
**Show Webex App** and its focus-based mute guidance stay unavailable there;
**Join / Open Meeting** still uses the validated saved link through the
operating system or default browser.

Webex owns sign-in, camera, microphone, speakers, participants, mute, and
meeting state. Because the external native app does not expose verifiable mute
control to this integration, **Open Webex to Mute** shows the verified Webex app so
the creator can use its own Mute control and explicitly does not claim it
changed Webex or Jamulus.
WebJam detects the native Webex app and, when it is missing or invalid, offers
the architecture-correct official Cisco installer in the browser after
explicit confirmation. WebJam does not bundle, silently install, authenticate,
or update Cisco's proprietary application.

This release also records the design for a future focused Webex Embedded App
companion. That hosted surface could show WebJam status and approved controls
inside a Webex meeting or space, while the trusted desktop remains the audio
engine. It is not represented as implemented: it requires HTTPS hosting,
OAuth, organization approval, and a separately secured desktop/cloud
synchronization channel.

## Reference Studio

Choose **Reference Studio** from WebJam's main rail, then use **Play Along /
Record** for the shortest path: name a project, choose a local backing track,
and enter the standalone workspace. **New Project** and **Open Project** remain
available when starting without a backing track or returning to saved work.

Each project is a portable folder with a small manifest, a separate
non-destructive Studio arrangement, and immutable checksummed copies below
`Media/`. Importing or dragging audio into a project never edits the source
file. Save, autosave, last-known-good recovery, and atomic **Save As** preserve
the project and arrangement together; a conflict or incomplete transaction
fails visibly instead of silently choosing one copy.

Reference Studio provides:

- progressive waveforms; playback with bars/beats, click, count-in, cycle, and
  musical snap;
- audio tracks with explicit input mapping, record arm, punch and cycle
  recording, latency compensation, take lanes, dropout evidence, and explicit
  recovery when capture publication cannot finish atomically;
- non-destructive regions, fades, markers, named sections, section
  rearrangement, repeated takes, quick-swipe comping, and bounded undo/redo;
- mixer faders, pan, mute, solo, and sends; volume, pan, and mute automation;
  bounded built-in high-pass, EQ, compressor, gate, shared reverb, and a master
  safety limiter used consistently by playback and bounce;
- bounded backing-tempo analysis with confidence and an explicit manual review
  before changing the project grid; imported audio is never time-stretched;
- cancellable 24-bit WAV or FLAC mix/stem bounce with SHA-256, peak, clipped
  sample, and RMS analysis. MP3 bounce stays unavailable unless a separately
  tested, identified, license-safe encoder adapter is installed.

Project recording and playback use Reference Studio's local audio backend only.
They do not join Jamulus, change Jamulus devices, or send project audio to a
live session. Automated tests establish state, rendering, file-integrity, and
package behavior; physical interface audibility, real latency calibration, and
long hardware recording remain **NOT RUN** until recorded against an exact
candidate package.

See the [Reference Studio guide](docs/REFERENCE_STUDIO_MUSICIAN_GUIDE.md)
and [project architecture and migration decision](docs/adr/0006-standalone-reference-studio-projects.md).

## One creator guidance source

The Session HUD, stage, Session Canvas, recorder, Studio, sanitized diagnostics,
and optional Companion API now render one deterministic guidance snapshot. It
answers what WebJam is doing, what to do next, why that action is safe, which
recording/validation/Studio/export outputs are actually confirmed, and how to
recover. Topology-specific recovery copy still comes from observed facts, but
it passes through the same typed projection so the surfaces cannot disagree.

Session Canvas keeps operational truth separate from the local **Creative
Pulse** extracted from intentional notes. A note can suggest an arrangement or
next creative action; it cannot claim a connection, recording, validated take,
transfer, export, or human audibility. Guidance is local, deterministic,
inspectable, offline, and event-driven. It performs no work on meter, waveform,
playhead, animation, audio, capture, or playback callbacks.

The scratchpad is profile-scoped on this computer only. Switching profiles
atomically saves the current profile's private mode-0600 file and loads the
other profile's fixed file. Reads are regular-file-only, no-follow, and bounded
to 1 MiB. Notes are never shared, session-synchronized, or media-timecoded.

## Pocket Stage iPhone owner-device preview

The current source retains the narrow Pocket Stage v1 vertical slice
introduced in v0.19.0 for an owner's iPhone, unchanged since v0.22.4. On the desktop, choose
**More → Use iPhone as Pocket Stage…** after both devices are on the same
private Wi-Fi.
WebJam displays a one-use QR code that expires after two minutes and starts a
separate secure local gateway only for this explicitly requested sharing
session.

The gateway creates an ephemeral self-signed certificate. The QR carries the
exact SHA-256 fingerprint of that certificate's DER bytes, and the iPhone pins
the presented certificate to that value. The code can be claimed once; after a
disconnect, create a fresh code because this preview has no durable reconnect
credential.

The paired phone can display current session and recording state plus
session-local mix slots with bounded participant display labels. Those labels
are paired-private content and stay out of logs, diagnostics, support bundles,
and the anonymous public Local Companion API. Current controls are fader,
mute, a timestamped Session Canvas marker, and—only for a prepared host—record
start/stop after Recording Setup is completed on the desktop.

Pan remains in the versioned snapshot/protocol vocabulary for forward
compatibility, but this preview does not present or accept it as a live control:
the pinned Jamulus client has no proven pan command.

This preview has no phone audio, chat, reactions, solo command, session plan,
section/Studio transport, editing, or media transfer. The checked-in XcodeGen
spec reproducibly generates and CI-compiles the native SwiftUI app; an owner
still selects their Apple Personal Team in Xcode to install it on their own
iPhone. Both Mac downloads include a self-contained **Pocket Stage iPhone
Setup** folder with the generated, CI-compiled Xcode project and a clickable
opener, so release users do not need to clone the repository or install
XcodeGen. A paid Apple Developer Program membership is not required for this
owner-device path, although free Personal Team provisioning expires and must be
renewed periodically. No pre-signed iOS app or physical iPhone/session result
is claimed; all physical tests remain **NOT RUN**. The existing loopback Local
API and Jamulus audio path are unchanged. See the
[Pocket Stage developer-preview plan](docs/plans/webjam-pocket-stage-v1.md)
and [threat model](docs/security/pocket-stage-mobile-threat-model.md).

## Host-controlled Shared Track

v0.23.0 presents the existing macOS-first route engine as one canonical
**Shared Track** experience. During a hosted session the compact live deck,
its **Shared Track** action, and **More → Shared Track…** all lead to the same
transport. The older `ReferenceTrack` implementation and evidence vocabulary
remain internal compatibility names, not a competing creator workflow.

Loading is deliberately separate from routing: a host can load and inspect
WAV/WAVE, AIFF, or FLAC even while playback is locked, using **Add Track…** or
by dropping one local audio file on the live surface or complete transport.
MP3 appears in the picker only when the packaged decoder proves support.
Loading decodes the first bounded audio block; MP3 sources also receive a
bounded structural frame and duration check that accepts real-world encoder
gapless headers (LAME and ffmpeg's Lavc/Lavf) and one trailing APE tag.
Malformed, truncated, metadata-conflicting, or unusable input fails before
playback is considered, and the rejection names its bounded structural
reason without the file path.
The source name, duration, progressive waveform, position, loop, count-in,
route, cleanup, and dropout status remain visible. **Replace…** and **Remove**
are available only while stopped. **Recheck Route** refreshes route evidence
without starting playback.

Its intended route sends the decoded source through a separately owned
`WebJam Track` Jamulus client, so the song becomes one participant with an
independent level and recording stem.

Playback is **fail-closed behind machine-derived route proof** in published
v0.22.5, continuing the boundary first shipped in v0.22.4: Play stays refused
until the Mac proves the required isolated route, and any missing, changed, or
ambiguous evidence returns it to silence. Earlier candidates through v0.22.2
instead shipped with playback locked outright — Apple's CoreAudio process-device
property has a reported case where its input result becomes the process's
output device after an input switch, Jamulus 3.12.2 has no independent
live-device RPC, and its saved profile is not sufficient proof — so in a
downloaded v0.22.2 package no setting, environment variable, command-line
switch, or UI action, including **Recheck Route**, can unlock playback.

Since v0.22.4, route authority is derived on the Mac instead of
requiring a constructor-only grant. An official, unambiguous 48-kHz BlackHole
16ch/64ch route is necessary; when the production factory's read-only local
checks certify that prerequisite, Play may become available. Choosing Play
then requires fresh, PID-bound primary and backing route proof,
session-unique descriptor-pinned profile/secret files, dedicated ports,
private authenticated RPC, a connected roster, and zero return faders. Any
missing, changed, or ambiguous evidence fails closed.

The explicit constructor boolean remains a test-only seam; no setting,
environment variable, command-line switch, or UI action can bypass the
machine-derived checks. One global WebJam lifecycle claim is inherited by the
backing child so another WebJam process cannot start a competing Track while
an orphan survives. Active playback refuses replacement or removal. Failed
cleanup stays visible and retryable rather than allowing source replacement or
shutdown. Lost or stale route proof emits silence and retires the backing
client without ending the primary connection. Guests receive bounded,
path-free host state through the authenticated peer session, but only the host
controls transport. A legacy guest may see dedicated-channel presence only;
neither projection is presented as sample synchronization or audibility proof.
This is implementation and isolation evidence, not proof of physical
audibility, independent mixes, or freedom from direct monitoring.

This is **Jamulus-routed**, not latency eliminated. The track receives normal
Jamulus buffering, jitter handling, and network latency like another participant.
The source path is memory-only and excluded from settings and logs. Windows and
Linux backends, plus physical macOS audibility, independent-mix, recording,
route-removal, direct-monitor, device-switch, and long-session evidence
remain **NOT RUN**. See the
[architecture decision](docs/adr/0005-reference-track-jamulus-participant.md)
and [physical pilot runbook](docs/plans/webjam-reference-track-macos-pilot.md).

## Recording and Studio

Recording is optional and starts only when the host presses **Record Session**.
On a host's first recording, WebJam asks whether to record the shared Jamulus
take only or also retain this Mac's configured Local Originals. Recording
Setup can define named mono/stereo tracks totaling up to 32 enabled Local
Original input channels. One mono row creates one mono 24-bit WAV; one stereo
row binds adjacent device channels into one true two-channel 24-bit WAV. Tracks
map sequentially to device channels, while a genuinely empty configuration
preserves the compatible two-mono-input default. Disabling or opting out of
every configured row records no host Local Original. The input-map editor never
changes Jamulus music settings.

After that choice, current v0.27.2 source opens one path-free **Record
Session Readiness** sheet before it arms anything. The sheet lists every exact
planned server track, Local Original, and Shared Track with its source label,
mono/stereo format, required/optional status, current readiness, and a bounded
meter where one is available. Separate cards show recording storage and Shared
Track readiness. Explicit blockers disable **Start Recording**. Accepting the
sheet triggers a second private authority check; if the roster, input map,
guest obligation, storage, local device preflight, Shared Track identity, take,
or plan generation changed, recording is refused instead of starting from a
stale screen. Cancel starts no recorder, local stream, or Shared Track.
For every opted-in guest Local Original, the host then publishes a private,
take-scoped arm request visible only to that required participant. Each guest
must open the exact frozen input stream and return an authenticated
plan/topology acknowledgement before Jamulus recording can start. Zero-track
guest opt-outs do not block. A timeout, disconnect, stale generation, device
failure, or mismatched acknowledgement cancels the arm and retires the
provisional take; after all acknowledgements, WebJam repeats the full authority
check immediately before opening the host capture and calling the recorder.
If a guest has acknowledged but cannot yet observe whether the host committed
the take, shutdown preserves that audio as local recovery-only media and does
not upload it until the same take reaches authenticated Recording or terminal
truth.

The live action names the real lifecycle: **Preparing**, **Count-in**,
**Recording**, **Stopping**, **Finalizing**, **Ready**, **Needs attention**, or
cleanup pending. If a Shared Track is loaded and ready, confirmed recorder
start owns its count-in/play transition. **Stop Recording** requests the
recorder and Shared Track stops together, but each owner must still prove its
own cleanup before the take can be called Ready. Guests can see bounded
recording state; only the host controls the shared recording.

Before capture starts, one immutable recording plan binds the exact take,
roster/server stems, Shared Track fingerprint and playback generation, host
mono/stereo input topology, guest Local Original count/map obligations,
count-in, storage verdict, and expected source count. Finalization rechecks
those exact identities and refuses substitution, under/over-delivery, a changed
guest topology, or a different Shared Track generation.

Every new planned source also carries one stable logical-source ID through the
server topology, Local Original capture, guest transfer receipt, take manifest,
recovery, and Studio. Width is never inferred later: each source is exactly one
mono channel or one true stereo pair. A missing or duplicate ID, absent width,
topology drift, or extra/missing file fails closed rather than falling back to
a display name or track order.

The completed take keeps every authoritative Jamulus participant stem distinct,
presents the Shared Track as its own stable source, preserves true stereo Local
Originals as stereo through Studio and export, and includes only Local
Originals that were explicitly enabled and planned. Ambiguous identities,
missing media, unverified guest alignment, gaps, or incomplete publication stay
visible and fail closed instead of becoming a false multitrack success.

Studio is a professional DAW-style multitrack review workspace, not a Logic
integration and not a copy of Apple artwork or trade dress.
Editing, comping, mix mutation, sidecar saving, and export apply only to Music
and Podcast & Voice. Review & Rehearsal Preview provides completed-take
playback, scrubbing, and source inspection only, with no local project, edit,
comp, mix mutation, Studio sidecar, or export.
It opens recorded takes, lets the creator choose a playback output only while
reviewing, and provides a frame-accurate Arrange timeline. Regions can be moved,
trimmed, split, duplicated, faded, disabled, or removed; markers, sections,
cycle/loop playback, snap state, track mix controls, and master delivery choices
remain non-destructive. Name Verse/Chorus sections and drag a section bar to
reorder that whole song block across every track as one undoable ripple edit.
Complete or explicitly recovered recordings from the same session can be added
as take lanes, auditioned without changing the saved comp, and selected with
Option/Alt-drag quick-swipe ranges.

For new editable Music and Podcast & Voice takes, Studio also looks for exact
earlier counterparts and stacks their lanes automatically. This automatic edit
requires the same session and project rate, complete or explicitly recovered
media, one unique matching logical-source ID, matching participant/source kind
and mono/stereo topology, verified timing, and the same Shared Track fingerprint
where relevant. It is deterministic and idempotent. Legacy, duplicate, or
ambiguous candidates are skipped and remain available only through the safe
manual workflow. Review & Rehearsal Preview never creates those lanes or a
sidecar.

The live cards show conservative host-side per-source recording truth without
inventing guest evidence. Studio adds an undoable **Reset Mix** that preserves
export inclusion, keeps overload indicators latched for the playback epoch,
and automatically selects and opens a durably finalized take.

The current v0.27.2 Studio source view distinguishes plan-bound Jamulus server,
Local Original, and Shared Track lanes and can show their current state, level,
reported dropouts, and overload warning. A malformed, legacy, or duplicate
projection is cleared rather than presented as authoritative source truth.

Podcast & Voice's standalone path uses the Host mono + Guest stereo 48 kHz
preset, preserves that topology through recording and loop overdub, stores
chapter markers, and reopens the same episode arrangement. **Bounce Episode**
publishes a verified stereo PCM-24 WAV. Review & Rehearsal keeps the local
project path and every edit, mix, save, bounce, and export primitive disabled
at both the visible UI and lower-level controller boundary.

Arrange also has a mouse-free path: Arrow keys select rows and regions,
Alt+Left/Right nudges, bracket shortcuts trim to the playhead, and keyboard
commands audition/comp take lanes or move the named section at the playhead.

Waveforms load progressively for the visible timeline, preserve recorded gaps
as silence, and cancel stale work when the take or source changes. Undo/redo is
bounded and restores exact immutable arrangement snapshots. Edits autosave to
a separate Studio sidecar with conflict detection and last-known-good recovery;
a failed save stays dirty and retryable. The take manifest and source WAVs are
never rewritten by Studio. A final save failure blocks application close rather
than making the unsaved arrangement inaccessible.

Export creates equal-length 24-bit edited stems, aligned unity originals, and a
rough mix, together with markers, import instructions, the exact arrangement,
source manifests, provenance, and SHA-256 checksums. Cross-take comp sources are
bound by full take/track/segment identity and export fails closed if source,
manifest, or saved-state truth changes. A transferred guest Local Original is
preserved first, then becomes eligible for aligned export only when WebJam
verifies it against that participant's intact Jamulus server reference; uncertain
originals remain reviewable but cannot be represented as verified alignment.

The edited evidence-rich package is available on macOS/Linux runtimes with the
required secure directory APIs. Windows instead shows **Export Aligned
Originals**: unity originals and a reference mix may use current trim, fader,
pan, mute, and solo, but region edits, fades, comps, sections, master processing,
and attached/repeated take lanes are excluded and stated before export.

## Jamulus profile and privacy

On macOS WebJam launches Jamulus with the supported filename-only argument:

```text
--inifile WebJam-native-v0.16.ini
```

The verified non-sandboxed integrated Jamulus component resolves that filename
relative to WebJam's private launch workspace under
`~/Library/Application Support/WebJam`. Jamulus alone writes the native sound
settings. WebJam only validates the dedicated file as a bounded regular file,
fingerprints it for restart-safe readiness, and may read its device names as a
secondary route-consistency check. WebJam never opens or uses Jamulus's
container.

Reference Track uses a separate reviewed headless Jamulus component and keeps
its private profile and control credential under WebJam's Application Support
tree too. PID-bound CoreAudio route evidence—not saved profile text—proves
primary-route isolation. The outer WebJam bundle therefore
does not declare `NSAppDataUsageDescription`, and Host, Join, and Reference
Track must not produce an Other Application Data prompt. A separate microphone
prompt can still appear when an audio input is used.

WebJam never writes device, channel, buffer, jitter, quality, or mix values to
the user's native profile, and it never reads or overwrites the regular
`Jamulus.ini`. WebJam's private restart records contain only allowlisted
profile identity and phase hashes—never invitation URLs, meeting URLs or
provider hostnames, credentials, device identifiers, raw paths, or notes.

## Published release and source state

The published release and a development checkout are intentionally different
identities. Only the exact tag, release assets, checksum manifest, and live
GitHub release metadata are downloadable evidence. A protected-publisher
success is required before calling a release round publish-green; a release
existing on GitHub does not manufacture that proof. Do not use an untagged
checkout or ordinary branch build as a release.

GitHub [Latest](https://github.com/rupret007/webjam/releases/latest) is the
published unsigned/ad-hoc
[v0.27.1 release](https://github.com/rupret007/webjam/releases/tag/v0.27.1).
Release ID `377614785` was published at `2026-08-27T06:56:11Z`. Annotated
tag object `ba81f8ef65db1013f13773b1536c812af174d81f` peels to exact commit
`1fc25f87c3386b1cd94303ecb407cdaff6509d1f`. The release has seven packages
plus `WebJam-v0.27.1-SHA256SUMS.txt`; Windows is unsigned and macOS is ad-hoc
signed and unnotarized.

Tag workflow `33045632613` passed the four desktop builds and supporting
automated jobs, then its **Publish GitHub Release** job failed closed because a
draft or published release already used `v0.27.1`. The overall tag workflow is
therefore red and is not protected-publisher or publish-green evidence. The
release remains the current downloadable pointer, but neither that pointer nor
the green post-tag `master` workflows may be rewritten as a successful release
round.

This candidate checkout reports unsigned **v0.27.2** but is post-v0.27.1-tag source.
Changes after the released tag are not in the v0.27.1 packages. Shared Track
play uses this Mac's BlackHole route and the
bundled headless client; the signed catalog remains sealed at exact WebJam
v0.22.5 and does not authorize v0.27.2. The immutable Jamulus compatibility
registry also ends at v0.27.1, so live Host/Join and the required package-build
gate fail closed for this source candidate. Every physical result remains
**NOT RUN**.

The prior
[v0.27.0 release](https://github.com/rupret007/webjam/releases/tag/v0.27.0)
is immutable historical evidence. Its tag peeled to
`27530d8216db04d706b6e5a1a5906ba6030fa7be`, tag CI `33035065141` created
the eight-asset draft, and release ID `377546932` was published at
`2026-08-27T04:17:19Z`.

The prior
[v0.26.0 release](https://github.com/rupret007/webjam/releases/tag/v0.26.0)
is immutable historical evidence. That published tag peeled to
`4b5208098981943df8ddaf1fac31aa36c15146bb`; four-platform tag CI and the
protected publisher verified and published release ID `371442375` at
`2026-08-16T22:40:56Z`. It has seven packages plus
`WebJam-v0.26.0-SHA256SUMS.txt`. Its dedicated physical checklist still has
no physical PASS.

The prior v0.25.0 is a new creator-multitrack source and package identity and
never replaces v0.24.0 bytes. Its exact
[v0.25.0 release](https://github.com/rupret007/webjam/releases/tag/v0.25.0)
is immutable historical evidence with the expected eight assets and checksum
manifest. Source CI `31878786472`, successful tag CI `31879936789`, and
protected publisher run `31882801893` are its package evidence; release ID
`371028390` was published at `2026-08-15T11:45:43Z`. The exact
[v0.24.0 release](https://github.com/rupret007/webjam/releases/tag/v0.24.0)
remains immutable historical evidence with its original tag CI and protected
promotion. The exact
[v0.23.0 release](https://github.com/rupret007/webjam/releases/tag/v0.23.0)
remains immutable historical evidence with its original tag CI and protected
promotion. The immutable
[v0.22.5 release](https://github.com/rupret007/webjam/releases/tag/v0.22.5)
retains its original assets and evidence as a historical candidate.

Immutable v0.25.0 is a historical private test candidate. It is a
creator-multitrack identity and never replaces v0.24.0 bytes. Its fallback-only
testing lane proved that sealed v3 remains valid for
historical v0.22.5 and is rejected for v0.25.0, then published the exact frozen
packages with the reviewed embedded Jamulus 3.12.2. The baked compatibility
policy recognizes the already audited 3.12.2 and 3.12.3 identities through
exact v0.25.0 only; managed 3.12.3 download remains unavailable until a new
signed version-specific channel exists. Physical participant results remain
**NOT RUN** until separately recorded.

Published tags and assets remain immutable historical evidence. In particular,
v0.20.0 history must not be moved. The v0.21.0 history must not be moved or
silently replaced by this candidate. The v0.22.0 annotated tag and tagged bytes
remain immutable. The published v0.22.1 tag, assets, and checksums likewise
remain immutable; v0.22.2 is a new patch identity, never a moved tag or rebuilt
v0.22.1 asset.

v0.22.4 is likewise a new source and package identity. It carries the
Reference Studio editing and Overdub line and upgrades
`cryptography` to 50.0.0 to remediate CVE-2026-69247, CVE-2026-69248, and
CVE-2026-69249. Windows, Linux, and Apple-silicon macOS use hash-locked
upstream wheels. Because upstream no longer publishes an Intel macOS wheel,
that target uses one documented, hash-locked native x86_64 source-build
exception with static OpenSSL 3.5.7 LTS; CI verifies its official inputs,
architecture, linkage, installed runtime, license evidence, and package
inventory. No other target is permitted to use that exception.

v0.22.5 is a new source and package identity for the real-world MP3, Reference
Track, and first-demo reliability closeout. It did not move or replace v0.22.4;
its separate tag CI, eight-asset inventory, checksum, v3 catalog, and protected
promotion gates passed before publication on 2026-08-07.

The published v0.22.4 and v0.22.5 tags, titles, warning text, assets, and
checksums are immutable and must never be rebuilt or replaced.

The v0.22.4 workflow built four targets from one source identity: Windows x64,
Ubuntu 22.04 x64, Intel Mac, and Apple-silicon Mac. The published release
contains exactly seven packages—the Windows Setup and ZIP, two Mac DMGs and two
Mac ZIPs, and the Linux ZIP—plus one exact SHA-256 manifest. It was promoted
from a verified draft by the separate publisher and explicitly marked GitHub
**Latest**. For future candidates, a successful Actions build or draft release
alone is still not a published Latest release.

The Jamulus catalog is intentionally **not** one of the desktop assets. It is
published under a separate non-Latest component release, signed by an offline
release key, expires within 31 days, and carries a monotonically increasing
sequence. Immutable v3 sequence 6 authorizes exact WebJam 0.22.5 through
2026-09-05; v2 sequence 5 remains historical evidence for v0.22.4. The desktop
updater embeds only the matching public key and rejects
expired, replayed, downgraded, equivocated, wrong-target, wrong-architecture,
wrong-size, wrong-hash, wrong-publisher, or unexpected-inventory content.
Support Bundles record only the finite catalog connection category and packaged
TLS trust state, which helps distinguish ordinary offline access from a broken
package without copying URLs, paths, credentials, or raw exceptions.

The v0.22.5 release uses the fixed `jamulus-components-v3` boundary. Its public,
independently redownloaded, signature-valid sequence-6 catalog was verified
before desktop promotion. The v1 and v2 component tags, assets, and signed
bytes remain immutable; they were never moved or replaced. Missing, invalid,
expired, or wrong-target v3 metadata leaves WebJam on its reviewed embedded
3.12.2 fallback.

Successful branch and pull-request workflows also retain the unsigned Windows
x64 candidate on GitHub for 90 days as `webjam-windows-x64`. It contains
exactly the Setup executable, portable ZIP, and their scoped SHA-256 manifest;
it is engineering evidence, not a signed or published release.

The source tree also contains the reviewed cross-platform packaging path for a
direct Windows Setup executable, Intel and Apple Silicon macOS disk images,
portable ZIP fallbacks, and an Ubuntu 22.04 x64 ZIP. Other Ubuntu versions and
Linux distributions are not certified. Windows signing, macOS notarization,
and other production gates remain pending, so use every downloadable candidate
only within its explicitly stated test boundary.

Generic Windows x64 and Intel macOS archives from earlier CI/tag runs are
historical outputs, not promoted release packages. In particular, the v0.16.2
Windows archive is unsigned and its clean-install Jamulus action looked in the
wrong packaged-data location. Those v0.16.2 assets stay immutable as build
evidence; the fixes were versioned in v0.16.3 instead of silently replacing
files on the old tag.

The candidate installer formats improve download and installation, but they do
not substitute for platform trust. Release assets are visibly named
`UNSIGNED-TEST-ONLY` on Windows and `ADHOC-TEST-ONLY` on macOS. Each Mac DMG
and ZIP includes a drag-to-Applications app, an optional verified
`Install WebJam.command`, an explicitly advanced quarantine-removal helper,
candidate metadata, and a plain-language warning. Dragging the app to
Applications and using Apple's app-bundle Open Anyway flow is the primary
macOS path. Recent macOS versions may block downloaded `.command` files from
Finder, so those helpers are documented for explicit Terminal use instead.

The source continues to isolate Authenticode and Developer ID credentials in
separate `windows-release` and `macos-release` environment-bound manual trust
jobs. Those environments still need protection rules and credentials
before they can serve as production-trust gates. The optional jobs preserve the
future production-trust path without blocking private test candidates. Native
packaging installs the reviewed Python graph from target-specific, hash-locked
wheel files rather than resolving new dependencies during a release build.

The implemented Windows PFX path is suitable only when the project already has
an eligible exportable legacy or internal-enterprise code-signing key. Newly
issued public code-signing keys are normally hardware- or service-backed, so a
production-trusted release still needs an explicit signing-provider choice and
integration.
The repository has `windows-release`, `macos-release`, and `release-latest`
GitHub Environments. `release-latest` requires a maintainer review and accepts
deployments from `master` only; it contains no signing secret. The two native
trust environments still need protection rules and credentials, and no
credentialed trust run has completed. Configure independent reviewers,
deployment restrictions, and credential isolation before using those paths as
production-trust controls. A managed Windows PC may still require IT approval
even after valid publisher signing; candidate packages must never be described
as production-trusted installers.

Automated source and package checks are evidence for code and archive
integrity—not a substitute for creators hearing one another. For the v0.26.0
private test release, v0.25.0,
v0.24.0,
immutable v0.23.0,
historical v0.22.5, and immutable earlier lines,
real two-Mac audio, physical interface disconnect/reconnect, sleep/wake,
interruption and recording recovery, long-session operation, external-editor
import of the evidence-rich session export, physical Reference Studio
record/playback and latency calibration, signed clean installation, and
platform trust/notarization remain physical or credentialed evidence. They are
recorded as **NOT RUN** until people perform them; the source suite does not
promote a package or claim audibility.

## Guides

- [Documentation index](docs/README.md)
- [Project brief for technical stakeholders](docs/PROJECT_BRIEF.md)
- [v0.27.1 release and unsigned v0.27.2 source notes](CHANGELOG.md)
- [v0.26.0 creator-multitrack physical checklist — release identity verified; physical rows NOT RUN](V026_CREATOR_MULTITRACK_PHYSICAL_TEST_CHECKLIST.md)
- [v0.25.0 creator-multitrack physical checklist](V025_CREATOR_MULTITRACK_PHYSICAL_TEST_CHECKLIST.md)
- [v0.24.0 recording-first physical checklist](V024_RECORDING_FIRST_PHYSICAL_TEST_CHECKLIST.md)
- [Historical v0.23.0 Shared Track checklist](V023_SHARED_TRACK_RECORDING_PHYSICAL_TEST_CHECKLIST.md)
- [v0.18 unified-guidance pilot checklist](V018_UNIFIED_GUIDANCE_PILOT.md)
- [First jam](FIRST_JAM.md)
- [Creator guide](USER_GUIDE.md)
- [Simple language guide](README_SIMPLE.md)
- [Reference Studio guide](docs/REFERENCE_STUDIO_MUSICIAN_GUIDE.md)
- [Reference Studio architecture and migration](docs/adr/0006-standalone-reference-studio-projects.md)
- [Recording and Studio](RECORDING_AND_STUDIO.md)
- [Presence v2 recorder-correlation architecture](docs/adr/0009-presence-v2-recorder-correlation.md)
- [Pocket Stage developer-preview plan](docs/plans/webjam-pocket-stage-v1.md)
- [Pocket Stage threat model](docs/security/pocket-stage-mobile-threat-model.md)
- [Reference Track architecture](docs/adr/0005-reference-track-jamulus-participant.md)
- [Reference Track macOS physical pilot](docs/plans/webjam-reference-track-macos-pilot.md)
- [Webex sandbox demo gate](docs/plans/webjam-webex-sandbox-demo-gate.md)
- [Dual-musician rehearsal lab](DUAL_MUSICIAN_REHEARSAL_LAB.md)
- [v0.22.5 two-musician demo readiness scorecard](WEBJAM_V0225_DEMO_READINESS.md)
- [Webex companion guidance](WEBEX_AUDIO_MODES.md)
- [Jamulus component catalog release runbook](docs/JAMULUS_COMPONENT_RELEASE_RUNBOOK.md)
- [Test procedure](TEST_PROCEDURE.md)
- [Architecture](ARCHITECTURE.md)
