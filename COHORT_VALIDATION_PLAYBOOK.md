# WebJam v0.10.0 closed-pilot playbook

This is the coordination guide for a small musician cohort. The exact physical
acceptance evidence belongs in
[`SUNDAY_TWO_MAC_PILOT.md`](SUNDAY_TWO_MAC_PILOT.md); do not substitute this
short guide for that gate.

## Message to send before the rehearsal

```text
We're testing WebJam v0.10.0 tonight.

1. Download the exact ZIP and verify both the filename and SHA-256 I send you.
2. Unzip it, move WebJam.app to Applications, then Control-click it and choose
   Open the first time. The private build is ad-hoc signed, not notarized.
3. Connect your interface and wired headphones. In macOS Sound, choose the
   interface for both input and output. Turn speakers off.
4. Put your Mac on the same Wi-Fi as the host and turn off VPN software.
5. Host: choose Host a Jam, complete Band Check if it appears, and choose Start
   Session. Bandmate: wait for the host's complete webjam:// invitation.
6. Open the private invite, or choose Join a Jam and paste it. Complete Band
   Check when WebJam asks, including input, left/right headphones, five-second
   recording, and playback, then choose Start Session. Do not screenshot, post,
   or paste the invite into support notes.

You should never be asked for a server, port, process path, or routing setup.
If anything fails, stop and capture what WebJam says before changing settings.
```

## Host

1. Open the exact v0.10.0 app and choose **Host a Jam** once.
2. Complete Band Check if WebJam asks, then choose **Start Session**.
3. If macOS requests microphone or incoming-network access, allow it. If
   microphone access was previously denied, use WebJam's **Open System
   Settings** recovery action.
4. Wait for **Ready to share**, then choose **Copy Invite** and send the complete
   link directly to the intended bandmate without editing it. A v2 link is a
   reusable session-scoped bearer credential, not a one-use token; anyone
   holding it on the LAN can enroll until the host peer restarts. If WebJam
   warns **Automatic Local Originals are off**,
   its v1 fallback still joins the music session and receives a host-side server
   track, but has no WebJam-orchestrated guest local capture or delivery.
5. Do not send the invite while the app still says **Starting your jam…**.

## Bandmate

1. Open the invite, or use the one-field **Join a Jam** screen.
2. Complete Band Check if WebJam asks, then choose **Start Session**.
3. Wait for the real participant state. A running process or spinner is not a
   connection claim.
4. If joining times out, confirm both Macs are on the same non-guest Wi-Fi and
   use the single **Try Again** action. If the jam is unavailable, ask the host
   for a fresh invite.

## Play and record

1. Play one instrument at a time and confirm audibility with each musician.
   Meters are observed signal, not proof of what the other person heard.
2. Move one bandmate fader and use **Mute Monitor**; confirm only that listener's
   monitor changes.
3. On the host and each v2-connected guest that should retain interface
   originals, open **More → Multitrack Studio → Recording Setup**, explicitly
   enable inputs 1 and 2, and select the intended shareable 48-kHz interface.
   The host still controls the shared take.
4. The host records at least 60 seconds. With an active v2 invite, briefly
   interrupt the guest's Wi-Fi, keep playing, reconnect, and wait for validation
   and resumed guest-original transfer before ending. If the host reported the
   v1 fallback, mark WebJam guest local-original capture and delivery **NOT
   AVAILABLE** instead of claiming that recovery test passed.
5. Confirm one server WAV per musician and every opted-in local original is
   present or truthfully disclosed. Check Studio playback and pan/mute/solo,
   then use **Export for Logic**. Require equal-length numbered PCM24 stems,
   references, reports, analysis, and checksums; import the numbered stems
   together at `0:00` and use references only for comparison.

## Recovery and finish

1. Confirm the recorded interruption showed **Connection interrupted**, no stale
   ready claim, stable participant identity, continued opted-in local capture,
   and automatic or one-action recovery.
2. The guest uses **Leave Jam**; confirm any active opted-in guest original was
   finalized, its resumable queue persisted, and a final upload was attempted.
   Confirm the host session remains available. An unreachable host must leave
   the original and queue on the guest Mac, not create a false delivered state.
3. Rejoin. If a host take is active, press **Stop Rec** and wait for **Take
   saved**; **End Session** must remain blocked until then. End the session and
   confirm that it ends the jam for everyone.
4. **Ending…** / **Leaving…** must persist until cleanup finishes. After quit,
   verify that no WebJam-owned music client, server, or `caffeinate` remains.

## Evidence and escalation

- Record the exact ZIP filename and SHA-256 before starting.
- Capture screenshots of any unclear state before retrying.
- Preserve the take folder and each Mac's local originals. Keep raw logs local;
  do not send them directly.
- Use **More → Band Check → Save Support Bundle** for the previewed, allowlisted,
  redacted support archive. `Ctrl+Shift+D` copies a short redacted summary.
- Do not widen the cohort until every item in the exact two-Mac pilot passes.
