# WebJam v0.22.0 unsigned private test candidate

WebJam has two deliberately separate musician workflows:

- **Host a Jam** and **Join a Jam** keep private live rehearsal simple: set up
  sound in Jamulus, then play.
- **Reference Studio** is a standalone songwriting and rehearsal workspace for
  playing with a backing track, recording ideas, arranging takes, mixing, and
  bouncing a demo. It does not require or alter a Jamulus session.

WebJam is still a conductor, not a replacement for the tools musicians already
trust.

| Product | Owns |
| --- | --- |
| WebJam | Private-session lifecycle, invitations, session truth, recording, Studio, export, recovery |
| Jamulus | Live interface, input/output channels, buffer, jitter, musician mix, and music connection |
| Webex | Optional talking/video meeting and its own microphone, camera, speaker, and participant state |

## The normal flow

1. Open WebJam and choose **Host a Jam** or **Join a Jam**.
2. WebJam starts the private session or consumes the invitation.
3. Jamulus opens normally. Choose the interface, channels, headphones, and
   buffer in **Jamulus → Settings → Audio/Network Settings**.
4. When WebJam sees the authenticated music connection, it opens the session
   automatically; the host then copies the invitation.
5. Play a note and make sure you can hear each other. Use **More → Band Check
   / Verify Sound** if you need help.
6. Add Webex only if your band wants it through **More → Webex /
   Conversation**; music remains in Jamulus.

There is no WebJam input/output picker, server field, port field, or Band
Check gate in Host/Join.

## Jamulus updates without rebuilding WebJam

WebJam v0.22.0 keeps its reviewed Jamulus 3.12.2 client, server, and isolated
Reference Track companion as an offline fallback. In the background it checks
a separately published, Ed25519-signed component catalog for Jamulus versions
that have passed WebJam's exact routing, RPC, recording, and packaging
contracts. It never follows an upstream “latest” link blindly.

An approved update can download without interrupting rehearsal, but it cannot
install, activate, or roll back while a client, server, Reference Track,
recording, practice session, reconnect, or launch is active. **More → Jamulus
Updates** shows the active, available, previous, deferred, fallback, or failed
state and provides Check, Download, Later, explicit install approval, and
recovery controls. On macOS, the verified current and previous managed copies
remain available for rollback. Windows and Linux keep installation OS-owned
and retain the embedded 3.12.2 fallback; they do not claim an app-managed
previous-version rollback.

On macOS, WebJam shows the exact packaged Jamulus license before accepting the
official disk image's agreement, then preserves and verifies the upstream
Developer ID signature, notarization, architecture, version, inventory, and
quarantine. On Windows the upstream Jamulus installer is unsigned, so WebJam
verifies its catalog-approved size and SHA-256 immediately before the user
chooses to open it. Linux opens the approved package through the desktop
package handler. WebJam never uses hidden elevation, `sudo`, a command shell,
Gatekeeper bypasses, or an update that mutates `WebJam.app`.

Jamulus itself displays musician labels on two lines after eight grapheme
clusters and accepts at most 16 UTF-16 units. WebJam now enforces that one
contract at every entry point and previews the actual 8+8 layout instead of
allowing Jamulus to silently shorten an identity.

## Optional Webex app

WebJam still stores only a musician's Meeting or Personal Room link and opens
the meeting externally; Webex owns sign-in, camera, microphone, speakers,
participants, and meeting state. WebJam now detects the native Webex app and,
when it is missing or invalid, offers the architecture-correct official Cisco
installer in the browser after explicit confirmation. WebJam does not bundle,
silently install, authenticate, or update Cisco's proprietary application.

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
live rehearsal. Automated tests establish state, rendering, file-integrity, and
package behavior; physical interface audibility, real latency calibration, and
long hardware recording remain **NOT RUN** until recorded against an exact
candidate package.

See the [Reference Studio musician guide](docs/REFERENCE_STUDIO_MUSICIAN_GUIDE.md)
and [project architecture and migration decision](docs/adr/0006-standalone-reference-studio-projects.md).

## One musician guide

The Session HUD, stage, Session Canvas, recorder, Studio, sanitized diagnostics,
and optional Companion API now render one deterministic guidance snapshot. It
answers what WebJam is doing, what to do next, why that action is safe, which
recording/validation/Studio/export outputs are actually confirmed, and how to
recover. Topology-specific recovery copy still comes from observed facts, but
it passes through the same typed projection so the surfaces cannot disagree.

Session Canvas keeps operational truth separate from the local **Creative
Pulse** extracted from intentional notes. A note can suggest an arrangement or
next rehearsal action; it cannot claim a connection, recording, validated take,
transfer, export, or human audibility. Guidance is local, deterministic,
inspectable, offline, and event-driven. It performs no work on meter, waveform,
playhead, animation, audio, capture, or playback callbacks.

## Pocket Stage iPhone owner-device preview

The v0.22.0 candidate retains the narrow Pocket Stage v1 vertical slice
introduced in v0.19.0 for an owner's iPhone. On the desktop, choose
**More -> Use iPhone** after both devices are on the same private Wi-Fi.
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

This preview has no phone audio, chat, reactions, solo command, rehearsal plan,
section/Studio transport, editing, or media transfer. The checked-in XcodeGen
spec reproducibly generates and CI-compiles the native SwiftUI app; an owner
still selects their Apple Personal Team in Xcode to install it on their own
iPhone. Both Mac downloads include a self-contained **Pocket Stage iPhone
Setup** folder with the generated, CI-compiled Xcode project and a clickable
opener, so release users do not need to clone the repository or install
XcodeGen. A paid Apple Developer Program membership is not required for this
owner-device path, although free Personal Team provisioning expires and must be
renewed periodically. No pre-signed iOS app or physical iPhone/rehearsal result
is claimed; all physical tests remain **NOT RUN**. The existing loopback Local
API and Jamulus audio path are unchanged. See the
[Pocket Stage developer-preview plan](docs/plans/webjam-pocket-stage-v1.md)
and [threat model](docs/security/pocket-stage-mobile-threat-model.md).

## Host-controlled Reference Track pilot

The source tree contains a macOS-first Reference Track engine and host UI under
**More**. Its intended route sends a local WAV, AIFF, FLAC, or
decoder-supported MP3 through a separately owned `WebJam Track` Jamulus client,
so the song becomes one participant with an independent level and recording
stem.

Playback remains deliberately **locked in the v0.22.0 private test candidate**.
Apple's CoreAudio process-device property has a reported case where its input
result becomes the process's output device after an input switch. Jamulus
3.12.2 has no independent live-device RPC, and its saved profile is not
sufficient proof. Because physical BlackHole isolation and direct-monitor
tests are also **NOT RUN**, production wiring refuses playback before scanning,
launching a backing client, or opening audio. There is no setting, environment
variable, command-line switch, or UI override.

The retained source-pilot implementation is exercised only through an explicit
constructor-only certification seam. It requires macOS 14.2 or later, an
unambiguous 48-kHz BlackHole 16ch/64ch route, live PID-bound primary-route
checks, a dedicated profile and ports, private authenticated RPC, a connected
roster, and zero return faders. Lost or stale proof emits silence and retires
the backing client without ending the primary connection. These mechanisms are
implementation evidence, not permission for a release package to play.

This is **Jamulus-routed**, not latency eliminated. The track receives normal
Jamulus buffering, jitter handling, and network latency like another musician.
The source path is memory-only and excluded from settings and logs. Windows and
Linux backends, plus physical macOS audibility, independent-mix, recording,
route-removal, direct-monitor, device-switch, and long-rehearsal evidence
remain **NOT RUN**. See the
[architecture decision](docs/adr/0005-reference-track-jamulus-participant.md)
and [physical pilot runbook](docs/plans/webjam-reference-track-macos-pilot.md).

## Recording and Studio

Recording is optional and starts only when the host presses **Record**. On a
host's first recording, WebJam asks whether to record the shared Jamulus take
only or also retain this Mac's first two interface inputs as Local Originals.
The latter choice opens the clearly labeled Recording Setup panel; it never
changes Jamulus music settings.

Studio is a Logic-like multitrack review workspace, not a Logic integration.
It opens recorded takes, lets the musician choose a playback output only while
reviewing, and provides a frame-accurate Arrange timeline. Regions can be moved,
trimmed, split, duplicated, faded, disabled, or removed; markers, sections,
cycle/loop playback, snap state, track mix controls, and master delivery choices
remain non-destructive. Name Verse/Chorus sections and drag a section bar to
reorder that whole song block across every track as one undoable ripple edit.
Complete or explicitly recovered recordings from the same session can be added
as take lanes, auditioned without changing the saved comp, and selected with
Option/Alt-drag quick-swipe ranges.

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
verifies it against that musician's intact Jamulus server reference; uncertain
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

Jamulus creates and owns that profile. WebJam never writes its device,
channel, buffer, jitter, quality, or mix values, and it never overwrites the
musician's normal `Jamulus.ini`. WebJam’s private restart records contain only
allowlisted profile and phase hashes—never invitation URLs, Webex URLs,
credentials, device identifiers, raw paths, or notes.

## Source and candidate state

The source tree reports **v0.22.0** and adds the signed Jamulus component
updater, exact Jamulus-name preview/validation, native Webex detection and
official installer handoff, and expanded privacy-safe diagnostics. It retains
standalone Reference Studio, Pocket Stage, the capability-gated macOS Reference
Track pilot, session Studio, and the reviewed unsigned-candidate packaging
described above. Published tags and assets remain immutable historical
evidence. In particular, v0.20.0 history must not be moved. The
v0.21.0 history must not be moved or silently replaced by this candidate.

The v0.22.0 candidate workflow builds four targets from one source identity:
Windows x64, Ubuntu 22.04 x64, Intel Mac, and Apple-silicon Mac. Its draft
GitHub release must contain exactly seven packages—the Windows Setup and ZIP,
two Mac DMGs and two Mac ZIPs, and the Linux ZIP—plus one exact SHA-256
manifest. After the draft inventory and every checksum pass, the separate
publisher must publish it as a non-prerelease and explicitly mark it
GitHub **Latest**. A successful Actions build or draft release alone is not a
published Latest release.

The Jamulus catalog is intentionally **not** one of those desktop assets. It is
published under a separate non-Latest component release, signed by an offline
release key, expires within 31 days, and carries a monotonically increasing
sequence. The desktop updater embeds only the matching public key and rejects
expired, replayed, downgraded, equivocated, wrong-target, wrong-architecture,
wrong-size, wrong-hash, wrong-publisher, or unexpected-inventory content.

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
separate protected `windows-release` and `macos-release` manual rehearsal jobs.
Those optional jobs preserve the future production-trust path without blocking
private test candidates. Native packaging installs the reviewed Python graph
from target-specific, hash-locked wheel files rather than resolving new
dependencies during a release build.

The implemented Windows PFX path is suitable only when the project already has
an eligible exportable legacy or internal-enterprise code-signing key. Newly
issued public code-signing keys are normally hardware- or service-backed, so a
production-trusted release still needs an explicit signing-provider choice and
integration.
The repository does not yet have the protected GitHub Environments or
credentials configured, and no credentialed rehearsal has completed. A managed
Windows PC may still require IT approval even after valid publisher signing;
candidate packages must never be described as production-trusted installers.

Automated source and package checks are evidence for code and archive
integrity—not a substitute for musicians hearing one another. For v0.22.0,
real two-Mac audio, physical interface disconnect/reconnect, sleep/wake,
interruption and recording recovery, long-session operation, external-editor
import of the evidence-rich session export, physical Reference Studio
record/playback and latency calibration, signed clean installation, and
platform trust/notarization remain physical or credentialed evidence. They are
recorded as **NOT RUN** until people perform them; the source suite does not
promote a package or claim audibility.

## Guides

- [v0.22.0 candidate notes and changelog](CHANGELOG.md)
- [v0.18 unified-guidance pilot checklist](V018_UNIFIED_GUIDANCE_PILOT.md)
- [First jam](FIRST_JAM.md)
- [Musician guide](USER_GUIDE.md)
- [Simple language guide](README_SIMPLE.md)
- [Reference Studio musician guide](docs/REFERENCE_STUDIO_MUSICIAN_GUIDE.md)
- [Reference Studio architecture and migration](docs/adr/0006-standalone-reference-studio-projects.md)
- [Recording and Studio](RECORDING_AND_STUDIO.md)
- [Pocket Stage developer-preview plan](docs/plans/webjam-pocket-stage-v1.md)
- [Pocket Stage threat model](docs/security/pocket-stage-mobile-threat-model.md)
- [Reference Track architecture](docs/adr/0005-reference-track-jamulus-participant.md)
- [Reference Track macOS physical pilot](docs/plans/webjam-reference-track-macos-pilot.md)
- [Webex sandbox demo gate](docs/plans/webjam-webex-sandbox-demo-gate.md)
- [Dual-musician rehearsal lab](DUAL_MUSICIAN_REHEARSAL_LAB.md)
- [Webex companion guidance](WEBEX_AUDIO_MODES.md)
- [Jamulus component catalog release runbook](docs/JAMULUS_COMPONENT_RELEASE_RUNBOOK.md)
- [Test procedure](TEST_PROCEDURE.md)
- [Architecture](ARCHITECTURE.md)
