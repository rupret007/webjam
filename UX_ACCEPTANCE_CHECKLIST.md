# WebJam v0.16 UX acceptance checklist

## Launch: understandable in five seconds

- [ ] First screen shows only **Host a Jam** and **Join a Jam**.
- [ ] Join shows exactly one invite field after the musician asks to join.
- [ ] Host/Join shows no Band input, Band output, recording input, Studio
      output, server, port, sample-rate, or Webex form.
- [ ] No extra Start Session decision follows Host/Join.
- [ ] Host server starts before Jamulus client launch.
- [ ] Jamulus opens visibly and can be foregrounded without UI automation.

## Host and invitation

- [ ] A host can copy an invitation only after the private server is ready.
- [ ] A guest can paste one invitation without seeing host, port, or secret
      details.
- [ ] Invitation contents never appear in status text, accessibility labels,
      recovery records, or support output.

## Music readiness

- [ ] WebJam says that Jamulus owns interface, channels, headphones, and
      buffer.
- [ ] WebJam waits for process/RPC/connection/local-identity proof.
- [ ] After that proof, WebJam moves into the ordinary session HUD without a
      setup, sound-confirmation, or Enter Jam click.
- [ ] A meter or a running process alone never claims that musicians can hear
      each other.
- [ ] Musicians verify audibility by playing a note; Band Check is optional
      help, not a startup gate.
- [ ] An unchanged recovered profile never bypasses fresh connection proof.
- [ ] Changed/missing profile returns to native Jamulus setup safely.

## Optional features

- [ ] Webex is available under **More** and never delays music readiness,
      invitation sharing, or entry to the session.
- [ ] Webex link is optional, external, and never auto-opened.
- [ ] UI says Jamulus carries music and reminds musicians to mute Webex while
      playing.
- [ ] Recording starts only when Record is pressed.
- [ ] First host Record offers shared-only or Local Originals.
- [ ] Local Originals selection is clearly separate from Jamulus setup.
- [ ] Studio playback output is shown only in Studio review.
- [ ] Studio feels like a compact multitrack workspace and does not claim
      Logic integration.

## Permission and error states

- [ ] Jamulus permission or sound setup problems point the musician back to
      Jamulus instead of duplicating a WebJam device picker.
- [ ] A missing music component, failed server, invalid invitation, or failed
      optional Webex save states one plain next action without exposing private
      connection details.

## Layout, accessibility, and recovery

- [ ] One dominant action is visible for each startup step.
- [ ] Keyboard focus reaches input and action controls in order.
- [ ] Accessible names describe the symbol, invitation field, Webex field,
      and actions.
- [ ] No clipping or horizontal scrolling at 760×600, 1024×768, or 1440×900.
- [ ] Black, white, neutral gray, and burnt orange are the only authored UI
      colors; no purple or teal returns.
- [ ] Cancel, retry, End, and Leave preserve safe process/recording truth.
- [ ] Recovery data contains no invitation, URL, credential, device, path, or
      note content.

## End, leave, and cleanup truth

- [ ] Cancel during startup cannot leave a late Jamulus client or private
      server running.
- [ ] End/Leave stops only owned processes and preserves an unfinished local
      original for review rather than calling it complete.

## Release validation

- [ ] CI-scope lint, compile, dependency, diff, UX-smoke, and full tests pass.
- [ ] The package contains checksum-verified Jamulus/JamulusServer 3.12.2,
      has verified signatures and transport provenance, and passes fresh
      extraction.
- [ ] Physical two-Mac and hardware evidence is listed separately as PASS,
      FAIL, or NOT RUN.
