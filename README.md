# WebJam v0.16.0

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
4. Return to WebJam and choose **I Finished Sound Setup**.
5. When WebJam can prove the connection, answer the one human question: does
   the returned instrument/band sound right?
6. Optionally add a Webex link. Music remains in Jamulus.
7. The host copies the invitation; everyone enters the jam.

There is no WebJam input/output picker, server field, port field, or Band
Check gate in Host/Join.

## Recording and Studio

Recording is optional and starts only when the host presses **Record**. On a
host's first recording, WebJam asks whether to record the shared Jamulus take
only or also retain this Mac's first two interface inputs as Local Originals.
The latter choice opens the clearly labeled Recording Setup panel; it never
changes Jamulus music settings.

Studio is a Logic-like multitrack review workspace, not a Logic integration.
It opens recorded takes, lets the musician choose a playback output when a
take is reviewed, preserves non-destructive mix choices, and exports aligned
stems plus a rough mix.

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

## Current release state

v0.16.0 is the final private test-night package, built from
`a36789978efbaac5e85fbc5c6ef55abae4ed42e3`:
`WebJam-v0.16.0-TEST-NIGHT-macos-arm64.zip`, SHA-256
`3ad2da6eccd99eb3965cc0e637ff147198e19446b3d878e4631a689cd5c9bf7b`.
The final source gate passed **1,807 tests** with 18 environment-bound skips
and zero failures/errors in 53.745 seconds. A fresh extracted package passed
strict/deep outer and nested-app signature checks, transport verification, and
a frozen Host smoke. The v0.15.0 ZIP and prior installed app are preserved as
rollback.

Automated source checks are evidence for code behavior. Real two-Mac audio,
interface changes, sleep/wake, interruption/recovery, and external-editor
import remain physical-pilot evidence and are recorded as **NOT RUN** until
people perform them.

## Guides

- [First jam](FIRST_JAM.md)
- [Musician guide](USER_GUIDE.md)
- [Simple language guide](README_SIMPLE.md)
- [Recording and Studio](RECORDING_AND_STUDIO.md)
- [Webex companion guidance](WEBEX_AUDIO_MODES.md)
- [Test procedure](TEST_PROCEDURE.md)
- [Architecture](ARCHITECTURE.md)
