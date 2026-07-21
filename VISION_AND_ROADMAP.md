# WebJam vision and roadmap — v0.18.0

## Product direction

WebJam should feel like a bandmate who gets a rehearsal ready, not a technical
control panel. The core promise is **Host or Join, set up sound in Jamulus, and
play.**

v0.17 keeps the v0.16 session boundary and makes Studio a practical
non-destructive arrangement workspace:

- WebJam conducts private sessions, invitations, recording, Studio, export,
  recovery, and support.
- Jamulus owns the live music engine and all audio configuration.
- Webex remains optional conversation/video.
- Studio is Logic-like multitrack review, not Logic integration.

## Shipped through v0.17 source

- Simple Host/Join first screen.
- Non-modal role-aware startup journey.
- Visible Jamulus native setup with a dedicated, Jamulus-owned profile.
- Automatic session handoff after authenticated Jamulus connection proof.
- Optional Webex under **More**, never in the music startup path.
- First-Record Local Originals choice and Studio-only playback output.
- Private, allowlisted restart recovery state.
- Burnt-orange three-loop WebJam mark and black/white/orange UI.
- Frame-domain Arrange editing with regions, fades/crossfades, markers,
  cycle/snap state, mixer/master choices, and bounded exact undo/redo.
- Coalesced, conflict-aware schema-v2 Studio autosave and last-known-good
  sidecar recovery without rewriting the take manifest or media.
- Same-session repeated-take lanes, non-persistent audition, and quick-swipe
  comp ranges bound to full source identity.
- Progressive viewport waveform tiles with gap silence, source verification,
  stale-work cancellation, and bounded caching.
- A shared playback/export renderer and atomic 24-bit export packages with
  source manifests, arrangement, provenance, and checksums.

## Next evidence

The next work is physical validation, not invention of more startup screens:

- two-Mac rehearsals with real interfaces;
- device loss, sleep/wake, and interruption recovery;
- shared/local take completion and transfer;
- Arrange/comp playback through real outputs and Studio export import in a real
  editor;
- packaged-code-signing and installation evidence.

Those physical and credentialed v0.18.0 gates remain **NOT RUN**. Automated
source coverage is necessary evidence, but it does not promote a package or
prove audibility.

No future feature should pull Jamulus device controls, Webex meeting controls,
or Local Originals choices back into Host/Join.
