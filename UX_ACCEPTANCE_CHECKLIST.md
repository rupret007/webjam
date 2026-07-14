# WebJam UX acceptance checklist

Use this checklist for the current Qt source candidate. The frozen v0.11.0
package has its own test-night worksheet; its Host/Join path goes directly to
Band Check and does not contain the source-only sound confirmation, CoreAudio
route preflight, or recording-storage guard. Setup Wizard, Ready Check, raw
endpoints, **Start Audio**, **Host & Start Audio**, and a visible Jamulus window
are legacy paths, not current acceptance criteria.

## Launch: understandable in five seconds

- [ ] A clean launch opens one calm screen with the WebJam identity, **Host a
      Jam**, and **Join a Jam**.
- [ ] **Host a Jam** is the single dominant action; Join is obvious without
      visually competing with it.
- [ ] The original three-part WebJam mark is sharp at different scales, works
      in one color, and never delays interaction.
- [ ] Choosing Join reveals one invitation field, one **Join Jam** action, and
      one Back action. No server address, port, process path, recorder setting,
      or routing option is visible.
- [ ] Invalid input is announced, focuses the field, and says to copy the link
      again from the host without exposing parser or networking detail.
- [ ] Double-clicking Host or Join cannot submit the operation twice; controls
      remain disabled only while the submission is active.
- [ ] The launch screen fits at 460×600 and remains balanced when enlarged.
- [ ] Tab and Shift+Tab follow the visual order, Enter activates the intended
      action, focus is obvious, and every interactive control has an accessible
      name and description.

## Visual system

- [ ] Active UI colors are near-black, white/neutral gray, and burnt orange
      (`#BF5700`) only; purple, teal, neon glow, red danger styling, and busy
      gradients do not appear.
- [ ] Orange identifies the primary action or current emphasis rather than
      covering large portions of the screen.
- [ ] Text and interactive boundaries meet contrast requirements; focus and
      status meaning do not rely on color alone.
- [ ] Buttons, inputs, menus, tooltips, dialogs, empty states, meters, Studio,
      and legacy secondary surfaces visibly belong to the same token system.
- [ ] The interface remains readable with increased system text size and does
      not depend on animation. The launch graphic and core states remain useful
      when reduced motion is preferred.

## Host and invitation

- [ ] **Host a Jam** opens one concise name and band-sound confirmation in the
      current macOS source candidate. A new or changed setup then opens Band Check;
      **Start Session** starts the bundled server and client in the background.
- [ ] **Starting your jam…** is real lifecycle state, not a fake delay.
- [ ] Copy Invite stays unavailable until the hosted service is alive and a
      usable same-LAN address exists.
- [ ] **Ready to share** explains that the host can send the link to a bandmate.
- [ ] The complete invitation can be copied with one control. A v2 invitation
      normally contains a reusable session-scoped bearer credential, not a
      one-use token; anyone holding it on the LAN can enroll until the host peer
      restarts. The app therefore masks pasted invite text, renders only
      **Private invite ready**, and never writes the full link to logs,
      diagnostics, or support output. It contains no recorder RPC secret, local
      path, or private musician data. If peer
      startup fails, **Automatic Local Originals are off** truthfully labels a
      v1 fallback that still joins/plays and receives a server track but has no
      WebJam-orchestrated guest local capture or delivery.
- [ ] The host is represented as **You** from authoritative session data; no
      preview or phantom participant is rendered as connected.

## Join and connection truth

- [ ] Cold-start link activation and paste-then-Join use the same strict
      invitation parser, fill/accept the same connection, and—in the current
      source candidate—show the same concise sound confirmation before Band
      Check and **Start Session**. An already-running deep link uses the same
      parser but honors the current-session/active-take guard before switching.
- [ ] A malformed or ambiguous invite is rejected in the Join window. A stale
      or unreachable invite becomes **This jam isn’t available** with a plain
      request to confirm the host and resend it.
- [ ] A running local process is never presented as proof of a connected jam.
      Real local session evidence is required before the UI reports connected.
- [ ] Connecting, connected, local-input-seen, bandmate-connected, and
      ready-to-play states reflect real roster and meter facts.
- [ ] The connection timeout stops an unproductive attempt and presents exactly
      one **Try Again** action in the primary stage.
- [ ] Offline or isolated Wi-Fi guidance is visible and does not ask a musician
      to inspect ports, addresses, or executables.

## Live session

- [ ] The live window has one restrained header, a dominant stage, one status
      surface, responsive participant tiles, and one bottom control bar.
- [ ] The bottom bar keeps **Copy Invite**, **Record**, **More**, and the
      role-aware **End Session** or **Leave Jam** visible.
- [ ] At 760×600, all four bottom controls remain available and no horizontal
      content is clipped.
- [ ] One through six participants form a balanced layout based on the actual
      viewport; resizing does not leave a fixed six-column grid or clipped
      cards.
- [ ] Local **Mute Monitor** copy makes clear that the action changes only what
      this musician hears. Fader, mute, and solo never imply control of another
      musician's mix.
- [ ] Meters stay still when no observation exists. State text and accessible
      descriptions distinguish quiet, signal present, and high signal without
      depending on meter color.
- [ ] Secondary features—Notes, Multitrack Studio, optional conversation,
      Settings, and Band Check—remain under **More**.
- [ ] Current-source Settings exposes display name, **Band input**, **Band
      output & review**, and an optional conversation link. On macOS it says
      the pair is staged for the next Jamulus session, never that a musician
      has already heard it.

## Permission and error states

- [ ] In the current source candidate, Host/Join on a new or changed setup
      first shows the concise sound confirmation, then Band Check; F2, **More
      → Band Check**, and **Settings → Run Band Check** open the same guided
      readiness flow. During a live jam it observes the running session without
      opening a second device or restarting services.
- [ ] Band Check reports **Ready to Jam**, **Ready with a Warning**, or
      **Action Needed** in words and keeps technical detail collapsed by
      default.
- [ ] In a freshly built candidate containing the storage guard, an unusable
      folder or dangerously low free space reports one corrective action and
      starts neither local capture nor the server recorder. Low storage renders
      a warning; it is not a claim that a long rehearsal is safe. This check is
      not applicable to the frozen v0.11.0 ZIP, which predates the guard.
- [ ] Before the first macOS microphone prompt, WebJam explains why access is
      needed and offers **Continue**.
- [ ] A denied or restricted microphone permission shows **Microphone access is
      off** with one **Open System Settings** action.
- [ ] After opening settings, WebJam explains how to return and offers **Try
      Again**. The user is not sent to an unrelated preferences form.
- [ ] In the current source candidate, a missing, ambiguous, or non-48-kHz
      selected macOS band device blocks client/server launch before any external
      process starts, gives one safe correction path, and never exposes a raw
      path or CoreAudio error. An automatic reconnect never silently chooses a
      newly changed default route.
- [ ] **Connection interrupted** clears stale participant/audio truth, announces
      that recovery is in progress, and restores readiness only after real
      reconnection evidence.
- [ ] Recoverable failures have one next action. Appropriate process/RPC detail
      stays in sanitized logs or collapsed Band Check technical details; raw
      exceptions and secrets never render, and support output redacts them.
- [ ] A fatal startup failure is caught, logged, and shown as a concise message
      that tells the user to quit/reopen rather than exposing an exception.

## End, leave, and cleanup truth

- [ ] A host sees **End Session** and a confirmation that the jam will end for
      everyone. A guest sees **Leave Jam** and a confirmation that only this Mac
      will disconnect.
- [ ] The stage and bottom action remain in **Ending…** or **Leaving…** state
      until the worker actually finishes.
- [ ] An active or validating host take blocks **End Session** until the host
      presses **Stop Rec** if needed and waits for **Take saved**; only then can
      client/server cleanup begin.
- [ ] Studio Recording Setup clearly separates automatic per-musician server
      tracks from optional inputs 1 and 2 kept locally on this Mac. Either host
      or an active-v2 guest can explicitly opt in; only the host controls the
      shared take, and explicit capture/output choices persist. The Takes
      folder is chosen before a session and remains fixed while it is running.
      A v1 guest sees no false local-capture claim.
- [ ] An opted-in guest keeps recording through a peer-control outage and later
      transfers a size/SHA/PCM-verified copy without moving or deleting the
      guest original.
- [ ] **Leave Jam** finalizes any active opted-in guest original, persists the
      resumable upload queue, and attempts a final upload before disconnecting.
      An unreachable host leaves truthful recoverable media on the guest Mac.
- [ ] A finished take exposes gain, pan, mute, solo, wired-output selection,
      and **Export for Logic**. Export stays responsive, never rewrites source
      audio, and reports either the ready folder or an actionable safe failure.
- [ ] A cleanup failure produces **WebJam couldn’t finish cleanly** and never a
      false success state.
- [ ] Closing a live window uses the same role-aware confirmation and cleanup
      behavior. A second confirmation is not shown after the recording flow has
      already handled it.

## Keyboard and assistive technology

- [ ] Main-window order is title → each participant's fader/mute/solo → Copy
      Invite → Record → More → End/Leave. Participant changes rebuild the order
      without trapping focus.
- [ ] Join/leave and connection/recovery changes create screen-reader-friendly
      announcements without repeatedly announcing unchanged state.
- [ ] Decorative graphics and waveforms are not focus stops but have meaningful
      accessible descriptions where they convey content.
- [ ] All primary targets are comfortably operable by mouse and keyboard; small
      Studio mute/solo controls remain at least 30×30.

## Release validation

- [ ] `git diff --check` passes.
- [ ] Ruff and compile checks pass for all first-party source roots.
- [ ] `QT_QPA_PLATFORM=offscreen .venv/bin/python ux_smoke_test.py` passes.
- [ ] `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/ -q` passes.
- [ ] Launch, Join, invalid invite, permission denied, connecting, ready,
      interrupted, unavailable, ending/leaving, and fatal-error renders have
      been visually reviewed.
- [ ] The exact frozen v0.11.0 app passes packaged startup, Band Check,
      Host/Join runtime,
      deep-link, cleanup, and resource/version inspection.
- [ ] The exact ZIP and SHA-256 used tonight are recorded in
      [`SUNDAY_TWO_MAC_PILOT.md`](SUNDAY_TWO_MAC_PILOT.md).
