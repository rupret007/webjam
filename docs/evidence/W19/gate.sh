#!/usr/bin/env bash
# W19: run offline from the worktree root. Each Qt module gets a fresh process.
set -euo pipefail
export QT_QPA_PLATFORM=offscreen
PY=.venv/bin/python

"$PY" -m ruff check webjam_qt/controllers/room_participant.py services/lan_room_guest.py
"$PY" -m compileall -q core webjam_qt services tests

# Focused W19 regression (would fail before ee3e32b disarmed_guest projection).
"$PY" -m pytest tests/test_art_lesson_request_journey.py::test_guest_restart_reappears_after_transient_poll_failure_within_grace -q

# Follow-along lesson-request areas touched by W19.
for test_file in \
  tests/test_art_lesson_request_journey.py \
  tests/test_lan_lesson_request_worker.py; do
  "$PY" -m pytest "$test_file" -q
done

echo 'W19 gate: PASS'
