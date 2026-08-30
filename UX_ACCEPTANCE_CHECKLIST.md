# WebJam v0.27.2 source UX acceptance checklist

> This checklist describes current unsigned v0.27.2 source. Immutable
> unsigned/ad-hoc release `379360694` is GitHub **Latest** for private testing.
> Its lightweight `v0.27.2` tag points to exact commit
> `9c6ca3de96aa7eb261c65b7dee768ab48144169c`; seven packages are covered by
> `WebJam-v0.27.2-SHA256SUMS.txt`.
> Historical v0.27.1 results remain bound to exact v0.27.1 assets. Every
> v0.27.2 physical and platform-trust gate is **NOT RUN** and cannot inherit
> that evidence.
> Exact v0.27.2 assets are package evidence, but not physical evidence.
> Host/Join is compatible with the existing exact Jamulus 3.12.2 and 3.12.3
> records. Use only a checksum-verified release asset for packaged checks.

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

- [ ] First screen asks **What are you creating?** and offers Music (GA),
      Podcast & Voice (GA), and Review & Rehearsal (Preview).
- [ ] Profile selection changes the primary actions to **Host** / **Join**,
      **Host Remote Recording** / **Join Recording**, or **Host Review** /
      **Join Review** before startup begins.
- [ ] Music uses **Band Check**, Podcast & Voice uses **Sound Check**, and
      Review & Rehearsal uses **Session Check (Preview)**.
- [ ] Join shows exactly one invite field after the creator asks to join.
- [ ] Host/Join shows no Jamulus input, Jamulus output, recording input, Studio
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

- [ ] Direct **Conversation** and **Studio** actions remain visible on the main
      session rail; hosts also see the compact **Shared Track** deck and
      **Record Session**, with no clipping or lost Copy Invite or End/Leave
      action at the supported compact sizes.
- [ ] Direct **Conversation** and **More → Conversation** reveal and focus
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
- [ ] **Open Webex to Mute** shows the verified app for its own Mute control and
      truthfully says that WebJam neither changes nor verifies external mute or
      Jamulus.
- [ ] Any public HTTPS DNS-host meeting link that passes the hardened policy
      uses one optional, external, credential-free handoff and never auto-opens.
      Known Webex, Zoom, Microsoft Teams, Google Meet, and FaceTime links get
      friendly labels; other accepted providers stay neutral and receive no
      native-verification claim.
- [ ] Credentials, custom ports, local/special-use names, IP literals,
      percent-encoded hosts, and known-brand lookalikes fail closed; unknown
      provider URLs and hostnames never enter logs or Support Bundles.
- [ ] UI says Jamulus carries WebJam audio and reminds creators to mute the selected
      meeting service while playing.
- [ ] No meeting-link handoff or native-app focus creates a recording source,
      and WebJam never directly or automatically taps a meeting app, browser,
      or system output.
- [ ] Local Originals record only explicitly selected input devices; the UI
      warns users not to route meeting or system-output audio into those
      inputs.
- [ ] Recording starts only when **Record Session** is pressed.
- [ ] First host **Record Session** offers shared-only or Local Originals.
- [ ] Local Originals selection is clearly separate from Jamulus setup.
- [ ] One mono input-map row creates one mono PCM-24 file; one stereo row binds
      adjacent device channels into one true two-channel PCM-24 file, and
      recovery, gaps, Studio, and export preserve that topology.
- [ ] Recording preflight freezes the exact roster/server stems, Shared Track
      fingerprint/playback generation, host logical topology, guest Local
      Original obligations, storage verdict, and expected source count.
- [ ] Reconnect/topology drift, source substitution, and missing or extra
      planned delivery fail closed before Ready.
- [ ] Record Session plainly distinguishes Preparing, Count-in, Recording,
      Stopping, Finalizing, Ready, Needs attention, and cleanup pending; Stop is
      never presented as immediate completion.
- [ ] A ready Shared Track begins its count-in/play transition only after the
      recorder generation is confirmed. One **Stop Recording** action requests
      both stops without hiding either owner's failure or pending cleanup.
- [ ] Guests see bounded recording state but no host recorder control.
- [ ] Studio playback output is shown only in Studio review.
- [ ] Direct **Studio** and Cmd/Ctrl+3 reuse the existing
      live-take/offline-project route and preserve a working return to Live;
      Studio is intentionally absent from More.
- [ ] A host can use **Add Shared Track** or drop WAV/WAVE, AIFF, or FLAC on the
      live session while route readiness is unavailable; MP3 is offered only
      when the packaged decoder proves support, and picker/drop both decode the
      first bounded audio block through the same validation.
- [ ] The compact live deck and complete Shared Track transport show the
      path-free name, duration, progressive waveform/playhead, loop, count-in,
      route, dropout, and cleanup state without turning the live screen into a
      full DAW.
- [ ] **Replace…** and **Remove** work while safely stopped and are visibly
      unavailable during route ownership, playback, stopping, or cleanup.
- [ ] Guests receive authenticated, bounded, path-free Shared Track state with
      monotonic generation handling but no transport authority or audibility
      field. Legacy `WebJam Track` presence fallback is never described as
      synchronized, isolated, healthy, or audible.
- [ ] Source and route states remain distinct. **Recheck Route** starts no
      playback. In the v0.24 package, Play may become available only after the
      production Mac path certifies an official 48-kHz BlackHole 16ch/64ch
      route; exact live isolation is still rechecked at startup and uncertainty
      fails closed. This machine result is not physical audibility proof.
- [ ] Reference Studio opens independently of Host/Join, retains the canonical
      trefoil/trinity mark, and never changes a Jamulus session or settings.
- [ ] Studio feels like a compact multitrack workspace and does not claim
      Logic integration.
- [ ] Arrange/edit/comp/mix mutation, Studio sidecars, and export are available
      only in Music or Podcast & Voice. Review & Rehearsal exposes playback,
      scrubbing, and source inspection only.
- [ ] Every authoritative participant appears once, the recorded Shared Track is
      distinctly named and typed, and Local Originals appear only after
      explicit opt-in and real-media evidence; no stereo mix is duplicated to
      imitate multitrack recording.
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
- [ ] Review & Rehearsal Preview allows live WebJam-audio Host/Join, Record
      Session, local-only scratchpad notes, and playback/read-only review of a
      completed session take.
- [ ] Review & Rehearsal Preview blocks standalone projects, take
      editing/comp/mix mutation, track export, shared notes, visual sync, and
      media timecode.

## Permission and error states

- [ ] Jamulus permission or sound setup problems point the creator back to
      Jamulus instead of duplicating a WebJam device picker.
- [ ] A missing music component, failed server, invalid invitation, or failed
      optional meeting-link save states one plain next action without exposing private
      connection details.

## Layout, accessibility, and recovery

- [ ] One dominant action is visible for each startup step.
- [ ] Keyboard focus reaches input and action controls in order.
- [ ] Accessible names describe the symbol, invitation field, meeting-link field,
      and actions.
- [ ] No clipping or horizontal scrolling at 720×560, 760×600, 1024×768, or
      1440×900.
- [ ] Black, white, neutral gray, and burnt orange are the only authored UI
      colors; no purple or teal returns.
- [ ] Cancel, retry, End, and Leave preserve safe process/recording truth.
- [ ] Recovery data contains no invitation, URL, credential, device, path, or
      note content.
- [ ] Support diagnostics expose only bounded Shared Track source/route,
      count-in, dropout, cleanup, recording-generation, and take facts—never
      its source name, folder path, participant names, or a raw backend error.

## End, leave, and cleanup truth

- [ ] Cancel during startup cannot leave a late Jamulus client or private
      server running.
- [ ] A concurrent Shared Track Close cannot report `closed` before an
      in-flight backing-client start has either retired cleanly or exposed
      retryable cleanup pending.
- [ ] Shared Track cleanup retains its owned process, RPC, session-unique
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
- [ ] No v0.27.2 physical or production-trust claim is made until the dedicated
      [physical checklist](V026_CREATOR_MULTITRACK_PHYSICAL_TEST_CHECKLIST.md)
      records exact-asset results. Two-machine music, Shared Track, recording,
      Studio, external-editor, accessibility, signing, installation, and
      platform-trust gates all currently remain **NOT RUN**.
