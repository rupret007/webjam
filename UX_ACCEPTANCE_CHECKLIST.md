# WebJam v0.19.0 UX acceptance checklist

## Unified guidance

- [ ] HUD, passive stage, Session Canvas, recorder, and Studio use the same
      current status and next action at every meaningful transition.
- [ ] Session Canvas explains why the action is valid, shows recent meaningful
      events, and distinguishes recording, take, guest-media, Studio, and
      export outcomes.
- [ ] Creative Pulse remains clearly separate; typing an operational claim in
      notes never changes connection, recording, take, or export truth.
- [ ] A topology-specific recovery remains specific without showing a
      contradictory generic message on another surface.
- [ ] Opening Studio leaves its take list/export control as the action owner;
      no duplicate HUD button competes with it.
- [ ] Switching takes or editing after export clears the old export result.
- [ ] A failed Studio save keeps the take and dirty edit open and offers safe
      retry wording.
- [ ] Repeated equivalent updates do not churn screen-reader descriptions;
      playhead, waveform, meter, and animation timers emit no guidance updates.

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
- [ ] Arrange exposes visible timeline/ruler, fixed track headers, zoom/scroll,
      accessible selection, and semantic region actions without clipping at the
      supported compact size.
- [ ] Move/trim gestures, Split, Duplicate, Disable/Delete, snap, and Undo/Redo
      update playback while leaving the take manifest and WAVs unchanged.
- [ ] A named Verse/Chorus section bar can be dragged earlier or later as one
      ripple edit across every track; Undo restores it, and an unsafe
      seam-crossing interval refuses the whole move without partial state.
- [ ] A safely matched same-session repeated take can be added as a lane,
      auditioned without changing the saved comp, and Option/Alt-dragged into a
      non-overlapping comp range.
- [ ] Waveforms arrive progressively, show declared gaps as silence, and never
      show stale results after switching takes.
- [ ] Autosave failure keeps the edit visibly dirty/retryable and describes the
      recorded take as safe; a conflict never silently overwrites another edit.
- [ ] Export reports one complete evidence-rich package or a safe failure—never
      a partial folder presented as success.

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
- [ ] v0.19.0 remains a private test candidate until real-output guidance review,
      Arrange/comp playback,
      external-editor import, signed clean installation, and platform trust
      gates have evidence; all currently remain **NOT RUN**.
