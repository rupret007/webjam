# WebJam musician guide — v0.17.0

## What each app does

WebJam conducts the rehearsal. It starts or joins the private session, creates
and checks invitations, keeps recording truth, and provides Studio.

Jamulus is the live music engine. It owns your interface, inputs, outputs,
channels, buffer, jitter, feedback protection, and musician mix. Configure
those in Jamulus, not in WebJam.

Webex is optional talking/video. WebJam can validate and open a link, but it
does not claim that Webex joined, muted, selected devices, or sees anyone.

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

## More menu

| Item | What it does |
| --- | --- |
| Audio Settings in Jamulus | Brings the owned Jamulus window forward; use its Audio/Network Settings menu |
| Webex / Conversation | Opens a configured link externally or lets you add one in WebJam Settings |
| Recording Setup | Sets Local Originals and takes storage; it does not alter Jamulus music routing |
| Studio | Reviews and arranges takes; playback output appears only for review |
| Notes | Opens session notes |
| Band Check / Verify Sound | Observes an already-live session without restarting it |
| Support | Creates a sanitized bundle only when you ask |

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
v0.17.0 source candidate.

Edited Studio packages require the secure descriptor-relative export available
on macOS/Linux. On Windows, Studio instead labels the action **Export Aligned
Originals**. It creates unity aligned originals and a reference mix from the
current trim, fader, pan, mute, and solo choices, while explicitly excluding
region edits, fades, comps, sections, master processing, and attached/repeated
take lanes.

Studio never rewrites the take manifest or source recordings. Removing a region,
take lane, or comp range changes only Studio's arrangement sidecar.

## Recovery

If music disconnects, WebJam shows what it can prove and keeps hosting and
recording truth conservative. Use **Audio Settings in Jamulus** for device or
native setup problems. Use **End Session** or **Leave Jam** for safe cleanup;
WebJam stops only processes it owns and finalizes a hosted take before ending
the server.
