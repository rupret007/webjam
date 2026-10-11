# W03 — Music HUD and empty stage connection agreement

## What changed

The HUD and stage were already refreshed together. The stage adapter omitted
the conductor's CONNECTED and LIVE phases, so both fell through to
NOT_CONNECTED. Added an explicit connected UI phase and mapped those two
accepted conductor phases to it. The stage now reads **CONNECTED** alongside
**Band connected** / **Hear each other, then Record when you are ready.**
(host), or **Hear each other, then play.** (guest). Before the full band is
verified, it reads **CONNECTED** / **Connected to the jam**. Existing idle,
recovery, Preview, and audibility evidence rules are unchanged. No new action
or door was added; the wording reuses the existing connection vocabulary.

## Files

- `webjam_qt/session_state.py`: explicit CONNECTED UI phase.
- `webjam_qt/controllers/application_controller.py`: map CONNECTED/LIVE to it.
- `webjam_qt/widgets/participant_grid.py`: explicit CONNECTED eyebrow label.
- `tests/test_ui_redesign_regressions.py`: rendered HUD/stage regression for
  host and guest at 800×600 and 1280×800. Each case traverses idle → live →
  reconnecting → connected → live → idle and checks canonical phase, HUD
  title, visible stage title/eyebrow, connection property, and label containment.
- `CHANGELOG.md`, `USER_GUIDE.md`: unreleased behavior and connection evidence.
- `docs/evidence/W03/{gate.sh,impl.md}`: committed copies of the requested
  campaign artifacts. The external copies live in `items/W03/`.

## Exact checks and results

All commands used the worktree's `.venv/bin/python`; Qt tests ran offscreen.

- Before product changes, the finished regression command
  `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_ui_redesign_regressions.py -q -k music_hud_and_empty_stage_connection_agree --tb=short`
  returned **4 failed, 34 deselected in 1.84s** (exit 1). All four failures
  were `assert 'NOT CONNECTED' == 'CONNECTED'` while the HUD was Band connected.
  An earlier fixture draft omitted setup intent and failed at the phase assertion;
  that fixture was corrected before recording the product regression above.
- The same focused command after the fix: **4 passed, 34 deselected in 1.46s**.
- Requested external `items/W03/gate.sh`: **exit 0**, ending `W03 gate: PASS`.
  It runs offline, in well under 25 minutes, with a fresh interpreter per module
  as required by CONTRIBUTING.md. It retains the prior campaign gate's room
  chrome modules and adds the current item’s conductor/stage suites.
- Gate focused rerun: **4 passed, 34 deselected in 1.62s**.
- Standard Ruff check over `webjam_qt/ core/ ui/ services/ api/` plus the changed
  test: **All checks passed!** Compileall over `core webjam_qt ui services api
  tests`: **exit 0**. Pip check: **No broken requirements found.** Pip emitted
  a cache-directory permission warning and disabled its cache.
- `git diff --check`: **exit 0**.

| Full test module in touched-area gate | Exact result |
| --- | --- |
| test_ui_redesign_regressions.py | 38 passed in 3.87s |
| test_session_conductor.py | 12 passed in 0.10s |
| test_jamulus_native_startup.py | 78 passed in 0.52s |
| test_unified_musician_guidance.py | 19 passed in 3.26s |
| test_creator_live_presentation.py | 11 passed in 0.47s |
| test_creator_profile_guidance.py | 17 passed in 1.40s |
| test_art_profile.py | 47 passed in 0.51s |
| test_session_hud_accessibility.py | 1 passed in 0.45s |
| test_conductor_window_fit_to_screen.py | 5 passed in 0.65s |
| test_art_session_strip.py | 6 passed in 0.54s |
| test_qt_widgets.py | 106 passed, 45 subtests passed in 2.26s |
| test_art_room_overview_ui.py | 55 passed in 4.89s |
| test_polish_1006_round2.py | 5 passed in 0.72s |
| test_legacy_mode_picker_retired.py | 5 passed in 0.56s |
| test_art_start_ux.py | 55 passed in 0.88s |

Total full-module gate: **460 passed, 45 subtests passed**; no failures or skips.
Raw gate output: campaign `items/W03/gate-connection.log`.

## UX evidence and limits

Read the campaign UX_GATE.md, launch copy, creative modes and start-card tests.
The first-screen gate passed: Music still offers **Host / Join**; Art still
offers **Make together / Paint along**, then **Host / Join**. No first-screen
copy or Preview decisions changed.

Inspected the supplied 800×600 before image and new offscreen captures at
800×600 and 1280×800 in campaign `shots/W03/after-connection/`. Both after
captures show **CONNECTED** above **Band connected**, without clipped text.
Capture rerun: **4 passed, 34 deselected in 1.55s**. These inject conductor
facts and do not run audio; the separate transport footer still reflects the
unstarted test transport. They prove only the HUD/stage presentation addressed
by W03. The initial capture attempt failed before pytest loaded because an
unrelated `/private/tmp/inspect.py` shadowed the standard library; using a
dedicated temporary plugin directory resolved that without touching that file.

**NOT RUN:** all 508 repository test modules as a full-repository gate;
physical/live audio, two-Mac, Webex, native on-screen feel, packaging, signing,
and release checks. No physical success is inferred from injected facts or Qt
tests. No builds or large outputs created; free disk remained above 10 GB.

The pre-existing external W03 report/gate described an earlier, different
item. Preserved them as `impl-previous-campaign.md` and
`gate-previous-campaign.sh` before writing this item’s requested artifacts.
