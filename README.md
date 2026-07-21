# WebJam v0.17.0 Studio source candidate

WebJam is the simplest way to start a private band rehearsal: choose **Host a
Jam** or **Join a Jam**, set up sound in Jamulus, then play.

It is a conductor, not a replacement for the tools musicians already trust.

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

## Source and published release state

The source tree reports **v0.17.0** and contains the Studio arrangement work
described above. No v0.17.0 desktop package has been promoted. The current
published rollback/reference release remains
[**v0.16.3**](https://github.com/rupret007/webjam/releases/tag/v0.16.3), a
private test candidate with one promoted primary artifact:

- `WebJam-v0.16.3-RC-4d8c046-windows-x64-setup.exe`

The source tree also contains the reviewed cross-platform packaging path for a
direct Windows Setup executable, Intel and Apple Silicon macOS disk images,
portable ZIP fallbacks, and an Ubuntu 22.04 x64 ZIP. Other Ubuntu versions and
Linux distributions are not certified. The published v0.16.3 build is still a
private test candidate. Windows signing, macOS notarization, and other
production gates remain pending, so use it only as a reviewed test release.

Generic Windows x64 and Intel macOS archives from earlier CI/tag runs are
historical outputs, not promoted release packages. In particular, the v0.16.2
Windows archive is unsigned and its clean-install Jamulus action looked in the
wrong packaged-data location. Those v0.16.2 assets stay immutable as build
evidence; the fixes were versioned in v0.16.3 instead of silently replacing
files on the old tag.

The v0.16.3 installer formats improve download and installation, but they do
not substitute for platform trust. Ordinary Actions downloads are visibly
named `UNSIGNED-TEST-ONLY` on Windows and `ADHOC-TEST-ONLY` on macOS. The source
now isolates Authenticode and Developer ID credentials in separate protected
`windows-release` and `macos-release` environment jobs. Those jobs pin the
expected publisher/Team identity, sign and verify the direct Setup/DMG assets,
remove private keys before launch, and retain trust evidence. Native packaging
also installs the reviewed Python graph from target-specific, hash-locked wheel
files rather than resolving new dependencies during a release build.

The implemented Windows PFX path is suitable only when the project already has
an eligible exportable legacy or internal-enterprise code-signing key. Newly
issued public code-signing keys are normally hardware- or service-backed, so a
public release still needs an explicit signing-provider choice and integration.
The repository does not yet have the protected GitHub Environments or
credentials configured, and no credentialed rehearsal has completed. A managed
Windows PC may still require IT approval even after valid publisher signing;
current test artifacts must not be promoted as public installers.

Automated source and package checks are evidence for code and archive
integrity—not a substitute for musicians hearing one another. For v0.17.0,
real two-Mac audio, physical interface disconnect/reconnect, sleep/wake,
interruption and recording recovery, long-session operation, external-editor
import of the new evidence-rich export, signed clean installation, and platform
trust/notarization remain physical or credentialed evidence. They are recorded
as **NOT RUN** until people perform them; the source suite does not promote a
package or claim audibility.

## Guides

- [v0.17.0 release notes and changelog](CHANGELOG.md)
- [First jam](FIRST_JAM.md)
- [Musician guide](USER_GUIDE.md)
- [Simple language guide](README_SIMPLE.md)
- [Recording and Studio](RECORDING_AND_STUDIO.md)
- [Dual-musician rehearsal lab](DUAL_MUSICIAN_REHEARSAL_LAB.md)
- [Webex companion guidance](WEBEX_AUDIO_MODES.md)
- [Test procedure](TEST_PROCEDURE.md)
- [Architecture](ARCHITECTURE.md)
