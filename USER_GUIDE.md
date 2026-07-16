# WebJam musician guide — v0.16

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
| Studio | Reviews takes and exposes playback output only for review |
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

Studio is designed for familiar multitrack review: track lanes, simple level,
pan, mute, solo, waveform, and aligned export. It deliberately is not a DAW
and does not integrate with or control Logic. It produces portable recordings
that can be imported into an editor at the start of the timeline.

## Recovery

If music disconnects, WebJam shows what it can prove and keeps hosting and
recording truth conservative. Use **Audio Settings in Jamulus** for device or
native setup problems. Use **End Session** or **Leave Jam** for safe cleanup;
WebJam stops only processes it owns and finalizes a hosted take before ending
the server.
