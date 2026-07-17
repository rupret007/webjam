# WebJam v0.16.3 RC 4d8c046

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
It opens recorded takes, lets the musician choose a playback output when a
take is reviewed, preserves non-destructive mix choices, and exports aligned
stems plus a rough mix. A transferred guest Local Original is preserved first,
then becomes eligible for an aligned export only when WebJam verifies it
against that musician's intact Jamulus server reference; uncertain originals
stay available for Studio review with waiting or unverified timing evidence.
A selected aligned export waits until each original is verified or deliberately
deselected rather than guessing where it belongs.

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

The release page now points to `v0.16.3` as the current release and is kept
clean with a single primary public file:

- `WebJam-v0.16.3-RC-4d8c046-windows-x64-setup.exe`

It is still a private test candidate. Windows signing, macOS notarization, and
other production gates remain pending, so use this build as a reviewed test
release only.

Automated source and package checks are evidence for code and archive
integrity—not a substitute for musicians hearing one another. Real two-Mac
audio, interface changes, sleep/wake, interruption/recovery, and
external-editor import remain physical-pilot evidence and are recorded as
**NOT RUN** until people perform them.

## Guides

- [First jam](FIRST_JAM.md)
- [Musician guide](USER_GUIDE.md)
- [Simple language guide](README_SIMPLE.md)
- [Recording and Studio](RECORDING_AND_STUDIO.md)
- [Dual-musician rehearsal lab](DUAL_MUSICIAN_REHEARSAL_LAB.md)
- [Webex companion guidance](WEBEX_AUDIO_MODES.md)
- [Test procedure](TEST_PROCEDURE.md)
- [Architecture](ARCHITECTURE.md)
