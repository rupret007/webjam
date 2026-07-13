# WebJam v0.9.0 UX acceptance checklist

Use this checklist for the current Qt app. Setup Wizard, Ready Check, raw
endpoints, **Start Audio**, **Host & Start Audio**, and a visible Jamulus window
are legacy paths, not v0.9.0 acceptance criteria.

## Launch: understandable in five seconds

- [ ] A clean launch opens one calm screen with the WebJam identity, **Host a
      Jam**, and **Join a Jam**.
- [ ] **Host a Jam** is the single dominant action; Join is obvious without
      visually competing with it.
- [ ] The supporting signal graphic is original, lightweight, static, sharp at
      different scales, and never delays interaction.
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

- [ ] **Host a Jam** requires one click in the normal macOS build and starts
      the bundled server and client in the background.
- [ ] **Starting your jam…** is real lifecycle state, not a fake delay.
- [ ] Copy Invite stays unavailable until the hosted service is alive and a
      usable same-LAN address exists.
- [ ] **Ready to share** explains that the host can send the link to a bandmate.
- [ ] The complete invitation can be copied with one control. It contains no
      recorder secret, credentials, local path, or private musician data.
- [ ] The host is represented as **You** from authoritative session data; no
      preview or phantom participant is rendered as connected.

## Join and connection truth

- [ ] Cold-start link activation and paste-then-Join use the same strict
      invitation parser and produce the same session.
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
      Settings, and Troubleshooting—remain under **More**.

## Permission and error states

- [ ] Before the first macOS microphone prompt, WebJam explains why access is
      needed and offers **Continue**.
- [ ] A denied or restricted microphone permission shows **Microphone access is
      off** with one **Open System Settings** action.
- [ ] After opening settings, WebJam explains how to return and offers **Try
      Again**. The user is not sent to an unrelated preferences form.
- [ ] **Connection interrupted** clears stale participant/audio truth, announces
      that recovery is in progress, and restores readiness only after real
      reconnection evidence.
- [ ] Recoverable failures have one next action. Raw exceptions, stack traces,
      process names, RPC detail, and secrets remain in logs or Troubleshooting.
- [ ] A fatal startup failure is caught, logged, and shown as a concise message
      that tells the user to quit/reopen rather than exposing an exception.

## End, leave, and cleanup truth

- [ ] A host sees **End Session** and a confirmation that the jam will end for
      everyone. A guest sees **Leave Jam** and a confirmation that only this Mac
      will disconnect.
- [ ] The stage and bottom action remain in **Ending…** or **Leaving…** state
      until the worker actually finishes.
- [ ] An active host recording is stopped and saved before client/server
      cleanup.
- [ ] Studio Recording Setup clearly separates automatic per-musician server
      tracks from optional host inputs 1 and 2; joiners cannot enable host-only
      capture, and explicit capture/output choices persist.
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
- [ ] The exact frozen v0.9.0 app passes packaged startup, Host/Join runtime,
      deep-link, cleanup, and resource/version inspection.
- [ ] The exact ZIP and SHA-256 used tonight are recorded in
      [`SUNDAY_TWO_MAC_PILOT.md`](SUNDAY_TWO_MAC_PILOT.md).
