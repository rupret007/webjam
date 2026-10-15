#!/usr/bin/env bash
# W05: run offline from the worktree root; no installs, builds or live services.
# CONTRIBUTING.md / DEVELOPMENT.md require a fresh interpreter per Qt module.
set -euo pipefail
export QT_QPA_PLATFORM=offscreen
PY=.venv/bin/python

"$PY" -m pytest -q tests/test_art_room_overview.py -k presence_action
"$PY" -m pytest -q tests/test_art_room_overview_ui.py -k presence_action

"$PY" -m ruff check webjam_qt/ core/ ui/ services/ api/
"$PY" -m ruff check tests/test_art_room_overview.py tests/test_art_room_overview_ui.py
"$PY" -m compileall -q core webjam_qt ui services api tests
"$PY" -m pip check
"$PY" ux_smoke_test.py

for test_file in \
  tests/test_art_room_overview.py \
  tests/test_art_room_overview_ui.py \
  tests/test_art_room_overview_controller.py \
  tests/test_art_room_overview_lifetime.py \
  tests/test_art_room_presence.py \
  tests/test_art_room_connection_names_journey.py \
  tests/test_art_activity_guest_journey.py \
  tests/test_art_paint_along_connection_journey.py \
  tests/test_art_start_ux.py; do
  "$PY" -m pytest -q "$test_file"
done

git diff --check
echo 'W05 gate: PASS'
