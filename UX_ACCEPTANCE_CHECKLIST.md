# WebJam v0.22.2 UX acceptance checklist

> **Unreleased after v0.22.2:** this maintained checklist includes source
> behavior not present in the immutable published v0.22.2 packages.

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

- [ ] Direct **Webex Controls** and **Studio** actions remain visible on the main
      session rail; hosts also see **Reference Track**, with no clipping or lost
      Record, Copy Invite, or End/Leave action at the supported compact sizes.
- [ ] Direct **Webex Controls** and **More → Webex Controls** reveal and focus
      the same panel without opening a URL; repeated navigation remains
      side-effect free.
- [ ] On macOS, **Show Webex App** re-verifies the Cisco bundle and, when
      running, the exact PID before activation. When stopped, it launches the
      verified app itself with no URL or document argument, then proves the
      exact path, PID, publisher, and foreground state. Webex chooses its own
      screen. It never passes a URL, opens a browser, or hands off a meeting;
      only **Join / Open Meeting** does so, once per click.
- [ ] The UI distinguishes activated-running and launched-app outcomes, never
      treats native request acceptance as foreground proof, survives a
      pathname-replacement test through one identity-bound file reference, and
      never claims that it joined a meeting or changed mute.
- [ ] Windows/Linux keep native focus unavailable without publisher proof and
      still provide the truthful **Join / Open Meeting** handoff.
- [ ] **Mute in Webex** shows the verified app for its own Mute control and
      truthfully says that WebJam neither changes nor verifies external mute or
      Jamulus.
- [ ] Webex link is optional, external, persisted without credentials, and
      never auto-opened.
- [ ] UI says Jamulus carries music and reminds musicians to mute Webex while
      playing.
- [ ] Recording starts only when Record is pressed.
- [ ] First host Record offers shared-only or Local Originals.
- [ ] Local Originals selection is clearly separate from Jamulus setup.
- [ ] Studio playback output is shown only in Studio review.
- [ ] Direct **Studio**, its More entry, and Cmd/Ctrl+3 reuse the existing
      live-take/offline-project route and preserve a working return to Live.
- [ ] A host can load and inspect WAV/WAVE, AIFF, or FLAC in **Reference Track** while
      route readiness is unavailable; MP3 is offered only when the packaged
      decoder proves support, and load decodes the first bounded audio block.
- [ ] Source and route states remain distinct. **Recheck Route** starts no
      playback, production locks before route scanning, and BlackHole setup or
      Recheck cannot unlock a downloaded v0.22.2 package.
- [ ] Reference Studio opens independently of Host/Join, retains the canonical
      trefoil/trinity mark, and never changes a Jamulus session or settings.
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
- [ ] No clipping or horizontal scrolling at 720×560, 760×600, 1024×768, or
      1440×900.
- [ ] Black, white, neutral gray, and burnt orange are the only authored UI
      colors; no purple or teal returns.
- [ ] Cancel, retry, End, and Leave preserve safe process/recording truth.
- [ ] Recovery data contains no invitation, URL, credential, device, path, or
      note content.
- [ ] Support diagnostics expose only bounded Reference Track source/route
      facts—never its source name, folder path, or a raw backend error.

## End, leave, and cleanup truth

- [ ] Cancel during startup cannot leave a late Jamulus client or private
      server running.
- [ ] A concurrent Reference Track Close cannot report `closed` before an
      in-flight backing-client start has either retired cleanly or exposed
      retryable cleanup pending.
- [ ] Reference Track cleanup retains its owned process, RPC, session-unique
      profile/secret, and global 16ch/64ch lifecycle claim until every step is
      proved; **Stop** retries and shutdown remains blocked on uncertainty.
- [ ] End/Leave stops only owned processes and preserves an unfinished local
      original for review rather than calling it complete.

## Release validation

- [ ] CI-scope lint, compile, dependency, diff, UX-smoke, and full tests pass.
- [ ] The package contains checksum-verified Jamulus/JamulusServer 3.12.2,
      has verified signatures and transport provenance, and passes fresh
      extraction.
- [ ] Physical two-Mac and hardware evidence is listed separately as PASS,
      FAIL, or NOT RUN.
- [ ] v0.22.2 remains a private test candidate until real-output guidance
      review, Arrange/comp playback, physical Reference Studio playback and
      recording, external-editor import, signed clean installation, and
      platform trust gates have evidence; all currently remain **NOT RUN**.
