#!/usr/bin/env bash
# W10: run offline from the worktree root. Each Qt module gets a fresh process.
set -euo pipefail
export QT_QPA_PLATFORM=offscreen
PY=.venv/bin/python

"$PY" -m ruff check webjam_qt/ core/ ui/ services/ api/
"$PY" -m compileall -q core webjam_qt ui services api tests
"$PY" -m pip check
"$PY" ux_smoke_test.py

# Focused W10 contract, followed by the standard per-module Python gate
# for the launch door, profile presentation, and invitation entry areas.
"$PY" -m pytest tests/test_art_start_ux.py -q \
  -k join_title_tracks_visible_profile_and_back_navigation
for test_file in \
  tests/test_art_start_ux.py \
  tests/test_launch_keyboard_compact.py \
  tests/test_art_join_entry_ui.py \
  tests/test_creator_live_presentation.py \
  tests/test_host_share_join_flow.py; do
  "$PY" -m pytest "$test_file" -q
done

echo 'W10 gate: PASS'
