#!/usr/bin/env bash
# W07: refuse rehearsal-template exports above the existing import byte limit.
# Run offline from the worktree root. Use a fresh interpreter per Qt module.
set -euo pipefail
export QT_QPA_PLATFORM=offscreen
export PYTHONDONTWRITEBYTECODE=1
PY=.venv/bin/python

"$PY" -m pytest -q -p no:cacheprovider tests/test_rehearsal_plan_ui.py

# Standard Python checks from CONTRIBUTING.md / DEVELOPMENT.md.
"$PY" -m ruff check webjam_qt/ core/ ui/ services/ api/
"$PY" -m compileall -q core webjam_qt ui services api tests
"$PY" -m pip check
"$PY" ux_smoke_test.py
for test_file in \
  tests/test_rehearsal_plan.py \
  tests/test_file_io.py \
  tests/test_session_library_dialog.py \
  tests/test_saved_follow_along.py \
  tests/test_rehearsal_recap.py \
  tests/test_art_start_ux.py; do
  "$PY" -m pytest -q -p no:cacheprovider "$test_file"
done

git diff --check
echo 'W07 rehearsal-template export gate: PASS'
