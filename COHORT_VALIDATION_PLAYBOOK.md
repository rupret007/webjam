# WebJam v0.9.0 closed-pilot playbook

This is the coordination guide for a small musician cohort. The exact physical
acceptance evidence belongs in
[`SUNDAY_TWO_MAC_PILOT.md`](SUNDAY_TWO_MAC_PILOT.md); do not substitute this
short guide for that gate.

## Message to send before the rehearsal

```text
We're testing WebJam v0.9.0 tonight.

1. Download the exact ZIP and verify the filename I send you.
2. Unzip it, move WebJam.app to Applications, then Control-click it and choose
   Open the first time. The private build is ad-hoc signed, not notarized.
3. Connect your interface and wired headphones. In macOS Sound, choose the
   interface for both input and output. Turn speakers off.
4. Put your Mac on the same Wi-Fi as the host and turn off VPN software.
5. Wait for the host's complete webjam:// invitation. Open it, or launch WebJam,
   choose Join a Jam, paste it, and choose Join Jam.

You should never be asked for a server, port, process path, or routing setup.
If anything fails, stop and capture what WebJam says before changing settings.
```

## Host

1. Open the exact v0.9.0 app and choose **Host a Jam** once.
2. If macOS requests microphone or incoming-network access, allow it. If
   microphone access was previously denied, use WebJam's **Open System
   Settings** recovery action.
3. Wait for **Ready to share**, then choose **Copy Invite** and send the complete
   link without editing it.
4. Do not send the invite while the app still says **Starting your jam…**.

## Bandmate

1. Open the invite, or use the one-field **Join a Jam** screen.
2. Wait for the real participant state. A running process or spinner is not a
   connection claim.
3. If joining times out, confirm both Macs are on the same non-guest Wi-Fi and
   use the single **Try Again** action. If the jam is unavailable, ask the host
   for a fresh invite.

## Play and record

1. Play one instrument at a time and confirm audibility with each musician.
   Meters are observed signal, not proof of what the other person heard.
2. Move one bandmate fader and use **Mute Monitor**; confirm only that listener's
   monitor changes.
3. The host opens **More → Multitrack Studio**, records at least 60 seconds, and
   waits for validation before ending.
4. Confirm one server WAV per musician, Studio stereo playback and pan/mute/
   solo, then use **Export for Logic**. Require equal-length numbered 24-bit
   stems and import them together at `0:00`; use the rough mix only as a
   reference.

## Recovery and finish

1. While not recording, interrupt the guest's Wi-Fi briefly. Require
   **Connection interrupted**, no stale ready claim, and automatic or one-action
   recovery.
2. The guest uses **Leave Jam**; confirm the host session remains available.
3. Rejoin, then the host uses **End Session** and confirms that it ends the jam
   for everyone.
4. **Ending…** / **Leaving…** must persist until cleanup finishes. After quit,
   verify that no WebJam-owned music client, server, or `caffeinate` remains.

## Evidence and escalation

- Record the exact ZIP filename and SHA-256 before starting.
- Capture screenshots of any unclear state before retrying.
- Preserve `~/.webjam.log`, `~/.webjam_jamulus.log`, and any take folder.
- Use **More → Troubleshooting** or **Ctrl+Shift+D** for redacted diagnostics.
- Do not widen the cohort until every item in the exact two-Mac pilot passes.
