# WebJam v0.16 UX acceptance checklist

## Launch

- [ ] First screen shows only **Host a Jam** and **Join a Jam**.
- [ ] Join shows exactly one invite field after the musician asks to join.
- [ ] Host/Join shows no Band input, Band output, recording input, Studio
      output, server, port, sample-rate, or Webex form.
- [ ] No extra Start Session decision follows Host/Join.
- [ ] Host server starts before Jamulus client launch.
- [ ] Jamulus opens visibly and can be foregrounded without UI automation.

## Music readiness

- [ ] WebJam says that Jamulus owns interface, channels, headphones, and
      buffer.
- [ ] WebJam waits for process/RPC/connection/local-identity proof.
- [ ] WebJam asks a human whether returned music sounds right.
- [ ] A meter or a running process alone never marks sound ready.
- [ ] Returning exact-profile host/guest takes a fast path.
- [ ] Changed/missing profile returns to native Jamulus setup safely.

## Optional features

- [ ] Webex is offered only after music readiness.
- [ ] Webex link is optional, external, and never auto-opened.
- [ ] UI says Jamulus carries music and reminds musicians to mute Webex while
      playing.
- [ ] Recording starts only when Record is pressed.
- [ ] First host Record offers shared-only or Local Originals.
- [ ] Local Originals selection is clearly separate from Jamulus setup.
- [ ] Studio playback output is shown only in Studio review.
- [ ] Studio feels like a compact multitrack workspace and does not claim
      Logic integration.

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
