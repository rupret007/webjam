# W15 — HUD matches connected grid state

Implemented on `webjam-24h/W15-hud-matches-connected-grid-state` in the assigned W15 worktree. Local commit only; no push, merge, publication, or release changes.

## What changed

The conductor already supplied the correct connected headline, but its stage adapter mapped both CONNECTED and LIVE to NOT_CONNECTED. The footer depended on a separate roster callback and could retain its initial disconnected label.

- `webjam_qt/session_state.py`: add the UI-only CONNECTED phase.
- `webjam_qt/widgets/participant_grid.py`: explicit CONNECTED eyebrow.
- `webjam_qt/controllers/application_controller.py`: map CONNECTED/LIVE correctly and refresh Audio/Session chips from accepted conductor connection milestones in the same render. Keep existing participant counts when a roster exists. Authentication, reconnect, failure, ending, and idle replace stale connected labels. Art and private-practice chip handling remains with its existing owners. No service state, connection authority, or audibility evidence is changed.
- `tests/test_connected_session_presentation.py`: 11 focused cases cover host/guest, connected/live empty grids, both status chips, lost connection, return, idle, renewed authentication, and failure.
- `CHANGELOG.md`, `USER_GUIDE.md`: document the correction and distinguish connection from audible sound.
- `docs/evidence/W15/impl.md`, `docs/evidence/W15/gate.sh`: versioned copies of the requested campaign deliverables.

## UX review

Read campaign UX_GATE.md, W15 backlog details, relevant audit evidence, launch copy in `launch_dialog.py`, and start definitions in `creative_modes.py`. Existing words are reused; no new door or control was added. Door gate remains Music Host/Join and Art Make together/Paint along, then Host/Join. `test_art_start_ux.py`: 55 passed; UX smoke passed.

Visible connected host copy: **Band connected** / **Hear each other, then Record when you are ready.** Empty stage: **CONNECTED**. Footer: **Session: Connected** and **Audio: Connected**. No **NOT CONNECTED** appears beneath the connected HUD. Preview and audibility honesty are retained.

Offscreen screenshots captured and visually inspected at 800×600 and 1280×800:

- `shots/W15/after/music-room-connected_800x600.png`
- `shots/W15/after/music-room-connected_1280x800.png`

These use the campaign screenshot harness with synthetic conductor facts, test-isolated storage, and simulated granted microphone permission. They are layout evidence, not physical connection evidence. The temporary screenshot test passed (1 passed in 0.78s) and was removed after capture.

## Exact validation results

Run from the worktree root:

```bash
bash /Users/jeffstory/Documents/bob-overnight-inject/local-webjam-24h-1010/items/W15/gate.sh
```

Exit **0**. Offline gate completed well under 25 minutes. Uses `.venv/bin/python` and the repository's fresh-interpreter-per-module pytest convention for touched areas.

- Ruff, touched Python files: **All checks passed!**
- Standard compileall (`core webjam_qt ui services api tests`): exit **0**.
- `pip check`: **No broken requirements found.** Read-only pip-cache warning only.
- `ux_smoke_test.py`: **UX smoke gate passed.**

| Test module | Exact result |
| --- | --- |
| test_connected_session_presentation.py | 11 passed in 1.36s |
| test_session_conductor.py | 12 passed in 0.07s |
| test_musician_guidance.py | 40 passed in 0.11s |
| test_unified_musician_guidance.py | 19 passed in 3.30s |
| test_application_controller_toggle.py | 22 passed in 0.82s |
| test_application_controller_session_pulse.py | 6 passed in 0.90s |
| test_music_status_chips.py | 14 passed in 0.79s |
| test_status_chip_policy.py | 35 passed in 0.09s |
| test_qt_widgets.py | 106 passed, 45 subtests passed in 2.35s |
| test_session_hud_accessibility.py | 1 passed in 0.43s |
| test_art_start_ux.py | 55 passed in 0.80s |
| test_conductor_window_fit_to_screen.py | 5 passed in 0.64s |
| test_recovery_lifecycle_contract.py | 34 passed in 1.87s |
| test_application_controller_band_check_gate.py | 38 passed in 0.78s |

Total: **398 passed, plus 45 subtests passed**, no skips or xfails in this gate. Full output: campaign `items/W15/gate-results.txt`.

Regression proof: temporarily restored the three product files to the branch's original HEAD, ran the final focused tests, then restored the implementation in a `finally` block. Result: **11 failed in 1.14s**, exit **1**, at the intended stale-stage/chip assertions. Output: campaign `items/W15/regression-before.txt`. The implementation's focused tests passed again after restoration. `git diff --check` passed.

## NOT RUN

- Full repository test suite outside the selected touched-area modules: **NOT RUN**.
- Real audio, microphone capture, physical/live connectivity, two-Mac session, Webex, packaged/native-window checks, and human ten-second feel acceptance: **NOT RUN**.

No large build output was produced; free disk remained approximately 11 GiB (above 10 GB).
