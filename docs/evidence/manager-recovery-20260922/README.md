# Notes and recording recovery evidence

These are actual offscreen Qt renders using controlled local fixtures, the
repository stylesheet and the production controller/widgets. They are not
physical audio, meeting, packaging or Jeff-feel acceptance.

- [Art room, 720 × 560](hidden-draft-art.png): a recovered Music draft remains
  discoverable while the connected Art room stays open. **Music notes need
  saving. → Review Notes** opens recovery without changing the room or writing
  the original. Covered by `test_notes_discovery_ui.py`, including larger text,
  keyboard focus, explicit save, privacy and clearing/reappearing notices.
- [Standalone Studio, 720 × 560](hidden-draft-studio.png): the same recovery
  action remains visible with the session HUD hidden. The selected workspace
  and existing Studio actions remain available.
- [Guest Studio](guest-recording-readiness.png): returning from a saved take
  restores the current **Local Originals require 48 kHz** remedy and enabled
  **Setup** control. Shared guidance no longer says to select a take, and an
  unproven participant row says **NOT RECORDING**, not **ARMED**. Controlled
  roster/capture fixtures do not claim a running audio device or server.

Regression fixtures first reproduced hidden recovery, external-file overwrite
on retry/Quit, protected-file export aliases, stale modal intent, blocked Stop,
stale guest guidance, and false live-source readiness. Notes copy-export
continuation and ambiguous publication retries have separate external-edit
rejection cases. Existing tests remain in place.

The PR's PRE_KAREN section records the final local gate and hosted run results.
