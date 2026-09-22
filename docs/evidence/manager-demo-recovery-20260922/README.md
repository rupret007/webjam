# Manager demo: actionable recording and local-save recovery

This product slice is stacked on recovery draft #146 (`023c6f7716aa8bdbcffd95a896a6fe7f5e541774`). Art draft #145 remains separate and unchanged. Both existing drafts have green hosted checks, including all desktop builds, as of this review.

## Reproduced friction and resulting behavior

| Before | Now | Regression proof |
| --- | --- | --- |
| A connected guest's failed optional Local Original setup left the HUD saying “Band connected” and Notes saying “Hear each other, then play.” | One specific Recording Setup action reaches the existing dialog from Live and an empty Studio. HUD, Notes, Studio, diagnostics and Pocket share the same guidance. | `test_guest_preflight_shared_recovery.py`, `test_guest_preflight_fresh_studio.py` |
| Studio's failed save offered Open Details, which opened sound checking. | Retry Save retries the exact selected take's unsaved document through its existing durable owner. Changed take, edit, workspace, profile, export, or quit state rejects a stale click. | `test_studio_demo_recovery_actions.py`, `test_studio_retry_api.py` |
| An unreadable saved Notes file had no recovery action before a draft existed. Another workspace's retained draft also hid this recovery. | Recheck Saved Notes reads the active original without writing. It preserves other drafts and checkpoints, and rechecks the editor/workspace before and after reading. | `test_notes_unreadable_recheck.py`, `test_notes_combined_unreadable.py` |
| Playback errors sent people to Recording Setup, which has no playback-output picker. | The error names Playback output and Play in Studio. | `test_studio_demo_recovery_actions.py` |
| Compact Studio clipped export recovery instructions; long Takes paths pushed Recording Setup controls outside its viewport. | Studio hints wrap. Setup paths wrap as plain text, with vertical scrolling and a shorter Local Originals checkbox. | `test_studio_retry_api.py`, `test_recording_setup_compact_recovery.py` |
| The Local Originals profile fixture deleted top-level widgets retained by sibling tests, then delivered queued work to deleted Studio objects. | It disposes only its own dialogs and drains their layout work before deletion. All tests remain. | Combined Studio/profile module gate, `test_local_originals_choice_profiles.py` |

## Exact on-screen copy

- Guest example: “Local Originals need attention” / “Local Originals require 48 kHz. Choose Recording Setup to review Local Originals or turn them off.” / **Recording Setup**.
- Studio save: “Studio choices need attention” / “WebJam couldn't save your latest Studio choices. Check file access and free space, then choose Retry Save.” / **Retry Save**.
- Notes: “Saved notes could not be opened. Choose Recheck Saved Notes.” / **Recheck Saved Notes**.
- Combined Notes example: “Art saved notes could not be opened. Choose Recheck Saved Notes.” and “Music: Permission denied. Choose Save Notes.” Both recovery actions remain available.
- Playback: “Studio couldn't open the selected playback output. Choose another Playback output in Studio, then choose Play.”
- Recording Setup checkbox: “Keep local inputs as Local Originals”. Existing format-change guards and explicit consent remain in place.

## Rendered proof

These are actual offscreen Qt fixtures with synthetic participants, source files and failures. They prove presentation and interaction layout; they do not claim microphone, physical recording, signing, or a real meeting PASS.

| Surface | Evidence |
| --- | --- |
| Guest Live, 760×600 | [Recording Setup action](guest-live-recovery.png) |
| Guest empty Studio, 760×600 | [Same recovery action](guest-studio-recovery.png) |
| Recording Setup, 620×620 | [Format/input recovery](recording-setup-recovery.png), [folder controls after vertical scrolling](recording-setup-folders.png) |
| Studio save failure | [760×600](studio-retry-760.png), [1000×740](studio-retry-1000.png) |
| Studio playback failure, 760×600 | [Visible Playback output and Play](studio-playback-recovery.png) |
| Studio export failure, standalone 760×600 | [Effective 125% text size](studio-export-125.png) |
| Notes, 280×560 | [Unavailable original](notes-recheck.png), [reopened original](notes-reopened.png) |
| Two workspace recovery, 280×560 | [22px wider-font status and actions](notes-workspace-recovery-large-text.png) |
| Music original recovery, 280×560 | [22px wider-font action contents](notes-recheck-large-text.png) |

The screenshot fixtures use the same controller/Studio/Notes setup and failure injection as the regression files above. Rendering uses `QT_QPA_PLATFORM=offscreen`, `load_stylesheet()`, and the actual widget's `grab()`. Layout tests additionally exercise 125% wider glyphs in Recording Setup, long folder paths including literal `<archive>`, and 13px/22px Notes text.

## Local verification

The exact 32-module recovery/readiness gate passes 677 tests and is in [focused-modules.json](focused-modules.json). Run from the repo root:

```sh
QT_QPA_PLATFORM=offscreen .venv/bin/python - <<'PY'
import json, subprocess, sys
from pathlib import Path
modules = json.loads(Path('docs/evidence/manager-demo-recovery-20260922/focused-modules.json').read_text())
raise SystemExit(subprocess.call([sys.executable, '-m', 'pytest', '-q', *modules]))
PY
```

The documented Art compatibility subset is 211 tests across:

```text
test_art_profile_guidance_journey.py
test_art_conversation_next_action.py
test_art_room_overview_controller.py
test_art_room_return_journey.py
test_art_notes_conversation_journey.py
test_art_native_retry.py
test_art_paint_along_connection_journey.py
test_art_notes_communication.py
test_art_room_overview_ui.py
```

Swift protocol/transport tests accept both new desktop recovery action values while keeping phone recording commands disabled. `go test ./...` passes in `transport`; production ruff, compileall, dependency consistency, runtime dependency policy, UX smoke, and diff checks pass. Hosted results belong to the PR's exact tip and are recorded in its PRE_KAREN body once complete.

The first hosted run exposed `test_live_hud_waits_for_human_hearing_confirmation_before_using_ready_style`: recovery rendering accessed Studio before checking whether the current role/action used it. The same failure reproduced locally. Product guards now reject ineligible recovery before accessing its UI owner; the original test is unchanged. The corrected native-startup/shared-recovery gate passes 128 tests.

An additional [12-module controller compatibility gate](controller-compatibility-modules.json) passes 376 tests in fresh processes, matching CI's Qt lifetime isolation. Run each listed module with `.venv/bin/python -m pytest -q`. Combined with the 677-test primary gate and 211-test Art subset, local coverage is **1,225 unique Python tests** (the 20 Local Originals profile and 19 unified-guidance tests appear in both gates).

Hosted Linux then exposed clipping in the original combined-Notes 22px assertion. Wider/taller Verdana text reproduced the defect locally. The Art footer now chooses the arrangement needing less height during recovery, and the status label reserves its measured wrapped height. Measurement is refreshed when width, font, copy or visibility changes, and clears when recovery ends. The long Music Recheck action also uses narrow padding. The original assertion remains intact; three added regressions verify both Notes states, actual button-content space, and reflow/reset behavior. The wider-font images above show the resulting 280×560 layouts. Studio and Setup also passed the existing 22 layout tests with wider/taller fonts; this stress check supplements hosted Linux verification.

## Ranked remaining work

1. Embedded schema-2 Studio at the full-window 760×600 floor still compresses Arrange/mixer content beneath its toolbars. The new recovery action and footer fit, but this pre-existing layout deserves a dedicated interaction pass. The 1000×740 screenshot shows the usable demo workspace.
2. Healthy Music Notes' existing toolbar/pulse can clip at 22px in a narrow panel after recovery. The failure and combined recovery states tested here remain readable.
3. Studio save recovery still uses general storage guidance. Distinguishing permission, space, and external-document conflicts with separate safe remedies is a further product slice; this change retains conflict protection and quit veto rather than forcing an overwrite.

Leave OPEN DRAFT for independent Karen review and Jeff feel. No merge, tag, release, Latest, spend, signing/notarization, or physical PASS; parked #37/#49 are untouched.
