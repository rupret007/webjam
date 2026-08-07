# WebJam

## Unified creative collaboration for live music

WebJam is a musician-first desktop conductor for low-latency rehearsal,
recording, arrangement, and creative review. It brings the session lifecycle,
Jamulus, optional Webex conversation, Reference Studio, and an owner-controlled
iPhone companion into one understandable workflow—without pretending to own
the systems that should remain independent.

[![WebJam CI](https://github.com/rupret007/webjam/actions/workflows/ci.yml/badge.svg?branch=master)](https://github.com/rupret007/webjam/actions/workflows/ci.yml)
[![MIT license](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Latest release](https://img.shields.io/github/v/release/rupret007/webjam?label=Latest%20test%20candidate)](https://github.com/rupret007/webjam/releases/latest)

> **Published download:** [v0.22.4](https://github.com/rupret007/webjam/releases/tag/v0.22.4)
> is GitHub **Latest** and immutable. It is an unsigned private test candidate:
> Windows is unsigned; macOS is ad-hoc signed and unnotarized. Physical and
> credentialed gates remain **NOT RUN** unless exact asset evidence says otherwise.

> **Development boundary:** master is the v0.22.5 release-candidate line. It is
> not a downloadable release until exact tag CI, draft verification, checksums,
> and protected promotion pass. Do not substitute an untagged checkout for the
> published v0.22.4 package.

## At a glance

| Area | Current state |
| --- | --- |
| Product | Musician-facing desktop conductor around Jamulus, Webex, Studio, and Pocket Stage |
| Published line | v0.22.4 private test candidate, four-platform immutable release |
| Active candidate | v0.22.5 Reference Track and first-demo reliability closeout |
| Trust posture | Windows unsigned; macOS ad-hoc signed and unnotarized |
| License | [MIT](LICENSE), with third-party notices shipped separately |
| Supported package targets | Windows x64, Ubuntu 22.04 x64, Intel Mac, Apple-silicon Mac |

WebJam has two deliberately separate musician workflows:

- **Host a Jam** and **Join a Jam** keep private live rehearsal simple: set up
  sound in Jamulus, then play.
- **Reference Studio** is a standalone songwriting and rehearsal workspace for
  playing with a backing track, recording ideas, arranging takes, mixing, and
  bouncing a demo. It does not require or alter a Jamulus session.

WebJam is a conductor, not a replacement for the tools musicians already trust.

## Five-minute demo

1. Open WebJam and choose **Host a Jam** or **Join a Jam**.
2. Configure interface, channels, headphones, and buffer in Jamulus.
3. Confirm the authenticated music connection, then use **Band Check / Verify
   Sound** if needed.
4. Open **Webex Controls** only when conversation or video is wanted; use
   **Join / Open Meeting** for an explicit meeting-link handoff.
5. Open **Reference Studio** for local songwriting and arrangement. The
   v0.22.4 candidate includes DAW-style multi-region editing and loop Overdub;
   read the release notes before the first take.

## System ownership

| Product | Owns |
| --- | --- |
| WebJam | Private-session lifecycle, invitations, session truth, recording, Studio, export, recovery |
| Jamulus | Live interface, input/output channels, buffer, jitter, musician mix, and music connection |
| Webex | Optional talking/video meeting and its own microphone, camera, speaker, and participant state |

## Live rehearsal flow

1. Open WebJam and choose **Host a Jam** or **Join a Jam**.
2. WebJam starts the private session or consumes the invitation.
3. On a fresh Mac setup, Jamulus opens its dedicated WebJam profile and may
   need you to choose the interface, input channels, headphones, and buffer
   once. WebJam does not open the regular Jamulus profile or request access to
   data from other apps.
4. Jamulus opens normally. Choose the interface, channels, headphones, and
   buffer in **Jamulus → Settings → Audio/Network Settings**.
5. When WebJam sees the authenticated music connection, it opens the session
   automatically; the host then copies the invitation.
6. Play a note and make sure you can hear each other. Use **More → Band Check
   / Verify Sound** if you need help.
7. Choose the direct **Webex Controls** action if your band wants conversation
   or video. It shows WebJam's Conversation controls without opening a meeting;
   music remains in Jamulus.

There is no WebJam input/output picker, server field, port field, or Band
Check gate in Host/Join.

## Bounded Jamulus recovery in the v0.22.4 candidate

The v0.22.4 source treats a running Jamulus process as necessary but not
sufficient recovery evidence. Each replacement is bound to the exact recovery
generation, process generation, and process ID that WebJam launched. WebJam
returns to Connected only after that same process has fresh authenticated RPC
activity and its local musician row is proved in the roster. Delayed evidence
from an earlier process cannot authenticate its replacement.

Automatic recovery is bounded to five starts. While one is pending or in
flight, WebJam reports Recovery in progress instead of starting competing
clients. If the bounded attempt set is exhausted, automatic recovery stops and
the musician gets an explicit fresh Host/Join restart path; a timer cannot
silently create a sixth attempt or call an unauthenticated process Connected.
The Support Bundle records only the immutable generations, attempt state,
process ID/liveness, finite RPC freshness category, and finite RPC age. It
never includes the Jamulus profile path, RPC secret, invitation, meeting link,
or raw exception.

This recovery work ships in the immutable v0.22.4 private test candidate,
which is GitHub **Latest**. Publication does not convert any physical gate to
PASS.

## Jamulus updates without rebuilding WebJam

WebJam v0.22.4 keeps its reviewed Jamulus 3.12.2 client, server, and isolated
Reference Track companion as an offline fallback. In the background it checks
a separately published, Ed25519-signed component catalog for Jamulus versions
that have passed WebJam's exact routing, RPC, recording, and packaging
contracts. It never follows an upstream “latest” link blindly.

The packaged updater uses WebJam's release-locked Certifi CA set with hostname
verification and TLS 1.2 or newer; launch-environment CA overrides cannot
replace that trust root. The v0.22.2 package-only release probe verified this
exact boundary against the live signed catalog before GitHub Latest
publication.

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

WebJam still stores only a musician's Meeting or Personal Room link. The direct
**Webex Controls** action and **More → Webex Controls** reveal the Conversation
panel and never launch or rejoin a meeting. On macOS, **Show Webex App** works
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
**Join / Open Meeting** still uses the validated Webex link through the
operating system or supported browser.

Webex owns sign-in, camera, microphone, speakers, participants, mute, and
meeting state. Because the external native app does not expose verifiable mute
control to this integration, **Open Webex to Mute** shows the verified Webex app so
the musician can use its own Mute control and explicitly does not claim it
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

The v0.22.4 candidate retains the narrow Pocket Stage v1 vertical slice
introduced in v0.19.0 for an owner's iPhone. On the desktop, choose
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

The source tree contains a macOS-first Reference Track engine. During a hosted
session the direct **Reference Track** action opens it; **More → Reference Track…** routes
to the same panel. Loading is deliberately separate from routing: a host can
load and inspect WAV/WAVE, AIFF, or FLAC even while playback is locked, via
the file picker or by dropping one local audio file on the panel.
MP3 appears in the picker only when the packaged decoder proves support.
Loading decodes the first bounded audio block; MP3 sources also receive a
bounded structural frame and duration check that accepts real-world encoder
gapless headers (LAME and ffmpeg's Lavc/Lavf) and one trailing APE tag.
Malformed, truncated, metadata-conflicting, or unusable input fails before
playback is considered, and the rejection names its bounded structural
reason without the file path.
**Recheck Route** refreshes route evidence without starting playback.

Its intended route sends the decoded source through a separately owned
`WebJam Track` Jamulus client, so the song becomes one participant with an
independent level and recording stem.

Playback remains deliberately **locked in the v0.22.2 private test candidate**.
Apple's CoreAudio process-device property has a reported case where its input
result becomes the process's output device after an input switch. Jamulus
3.12.2 has no independent live-device RPC, and its saved profile is not
sufficient proof. Because physical BlackHole isolation and direct-monitor
tests are also **NOT RUN**, production wiring refuses playback before scanning,
launching a backing client, or opening audio. There is no setting, environment
variable, command-line switch, or UI override. Installing BlackHole, running its
setup guidance, or choosing **Recheck Route** cannot unlock a downloaded
v0.22.2 package.

The v0.22.4 candidate derives route authority on the Mac instead of
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
an orphan survives. Failed cleanup stays visible and retryable rather than
allowing source replacement or shutdown. Lost or stale route proof emits
silence and retires the backing client without ending the primary connection.
This is implementation and isolation evidence, not proof of physical
audibility, independent mixes, or freedom from direct monitoring.

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
the musician's native profile, and it never reads or overwrites the regular
`Jamulus.ini`. WebJam's private restart records contain only allowlisted
profile identity and phase hashes—never invitation URLs, Webex URLs,
credentials, device identifiers, raw paths, or notes.

## Published source and candidate state

The published release and the development checkout are intentionally different
identities. `master` is the v0.22.5 release-candidate line; it is not part of
the downloadable release until the exact tag, draft, checksums, component
catalog, and promotion all pass. Do not use an untagged source checkout as
evidence for the immutable download.

The source tree reports **v0.22.5**. The
[published release](https://github.com/rupret007/webjam/releases/tag/v0.22.4)
remains **v0.22.4**, an immutable non-prerelease explicitly titled as an
unsigned private test candidate and marked GitHub **Latest**. It retains direct
Live access to Webex, host-only Reference Track, standalone Reference Studio,
session Studio, Pocket Stage, the Trinity three-loop identity, and the reviewed
unsigned/ad-hoc candidate packaging described above.

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

v0.22.5 is a new candidate source and package identity for the real-world MP3,
Reference Track, and first-demo reliability closeout. It does not move or
replace v0.22.4 and is not public until its separate release gates pass.

The published v0.22.4 tag, title, warning text, assets, and checksums are now
immutable and must never be rebuilt or replaced.

The v0.22.4 workflow built four targets from one source identity: Windows x64,
Ubuntu 22.04 x64, Intel Mac, and Apple-silicon Mac. The published release
contains exactly seven packages—the Windows Setup and ZIP, two Mac DMGs and two
Mac ZIPs, and the Linux ZIP—plus one exact SHA-256 manifest. It was promoted
from a verified draft by the separate publisher and explicitly marked GitHub
**Latest**. For future candidates, a successful Actions build or draft release
alone is still not a published Latest release.

The Jamulus catalog is intentionally **not** one of those desktop assets. It is
published under a separate non-Latest component release, signed by an offline
release key, expires within 31 days, and carries a monotonically increasing
sequence. Its immutable sequence 5 authorizes exact WebJam 0.22.4 through
2026-09-03. The desktop updater embeds only the matching public key and rejects
expired, replayed, downgraded, equivocated, wrong-target, wrong-architecture,
wrong-size, wrong-hash, wrong-publisher, or unexpected-inventory content.
Support Bundles record only the finite catalog connection category and packaged
TLS trust state, which helps distinguish ordinary offline access from a broken
package without copying URLs, paths, credentials, or raw exceptions.

The v0.22.5 candidate uses a new fixed `jamulus-components-v3` boundary. It
cannot be promoted until a public, independently redownloaded, signature-valid
sequence-6 catalog authorizes exact WebJam 0.22.5. The v1 and v2 component tags,
assets, and signed bytes remain immutable; they are never moved or replaced to
make the new candidate work. Missing, invalid, expired, or wrong-target v3
metadata leaves WebJam on its reviewed embedded 3.12.2 fallback.

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
separate `windows-release` and `macos-release` environment-bound manual
rehearsal jobs. Those environments still need protection rules and credentials
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
GitHub Environments, but they currently have no protection rules; the two trust
environments also have no release credentials, and no credentialed rehearsal
has completed. Configure required reviewers, deployment restrictions, and
credential isolation before using them as production-trust controls. A managed
Windows PC may still require IT approval even after valid publisher signing;
candidate packages must never be described as production-trusted installers.

Automated source and package checks are evidence for code and archive
integrity—not a substitute for musicians hearing one another. For v0.22.4,
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
- [v0.22.5 candidate notes and release history](CHANGELOG.md)
- [v0.18 unified-guidance pilot checklist](V018_UNIFIED_GUIDANCE_PILOT.md)
- [First jam](FIRST_JAM.md)
- [Musician guide](USER_GUIDE.md)
- [Simple language guide](README_SIMPLE.md)
- [Reference Studio musician guide](docs/REFERENCE_STUDIO_MUSICIAN_GUIDE.md)
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
