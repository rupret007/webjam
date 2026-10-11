#!/usr/bin/env bash
# Run offline from the W03 worktree root. Fresh interpreter per Qt test module.
set -euo pipefail
test -f webjam_qt/session_state.py
test -x .venv/bin/python
export QT_QPA_PLATFORM=offscreen

echo 'W03 focused connection regression'
.venv/bin/python -m pytest tests/test_ui_redesign_regressions.py -q \
  -k music_hud_and_empty_stage_connection_agree

echo 'Standard Python checks'
.venv/bin/ruff check webjam_qt/ core/ ui/ services/ api/ tests/test_ui_redesign_regressions.py
.venv/bin/python -m compileall -q core webjam_qt ui services api tests
.venv/bin/python -m pip check

# Standard repository test gate, scoped to conductor, stage, and room chrome.
# Retain the room/launch modules listed in the previous campaign's W03 gate.
for test_file in \
  tests/test_ui_redesign_regressions.py \
  tests/test_session_conductor.py \
  tests/test_jamulus_native_startup.py \
  tests/test_unified_musician_guidance.py \
  tests/test_creator_live_presentation.py \
  tests/test_creator_profile_guidance.py \
  tests/test_art_profile.py \
  tests/test_session_hud_accessibility.py \
  tests/test_conductor_window_fit_to_screen.py \
  tests/test_art_session_strip.py \
  tests/test_qt_widgets.py \
  tests/test_art_room_overview_ui.py \
  tests/test_polish_1006_round2.py \
  tests/test_legacy_mode_picker_retired.py \
  tests/test_art_start_ux.py
do
  echo "$test_file"
  .venv/bin/python -m pytest "$test_file" -q
done
echo 'W03 gate: PASS'
