#!/usr/bin/env bash
# Run offline from the W05 worktree root. Fresh Python process per Qt module.
set -euo pipefail
export QT_QPA_PLATFORM=offscreen
export PIP_NO_CACHE_DIR=1
PY=.venv/bin/python

"$PY" -m pytest -q tests/test_session_library_coordinator.py -k summary_export
"$PY" -m ruff check webjam_qt/ core/ ui/ services/ api/
"$PY" -m ruff check tests/test_session_library_coordinator.py
"$PY" -m compileall -q core webjam_qt ui services api tests
"$PY" -m pip check
"$PY" -c "from tools.changelog_fragments import check; problems=check('origin/master'); print('\n'.join(problems) or 'changelog fragment OK'); raise SystemExit(1 if problems else 0)"
"$PY" ux_smoke_test.py

# CONTRIBUTING.md / DEVELOPMENT.md standard gate for the touched Library UI,
# its persistence/recovery and backup consumers, and the campaign's door UX.
for test_file in \
  tests/test_session_library_coordinator.py \
  tests/test_session_library_dialog.py \
  tests/test_session_library.py \
  tests/test_session_library_launch.py \
  tests/test_session_library_create_retry.py \
  tests/test_session_library_save_retry.py \
  tests/test_workspace_backup_library_ui.py \
  tests/test_workspace_media_library_ui.py \
  tests/test_notes_workspace_recovery.py \
  tests/test_art_start_ux.py; do
  "$PY" -m pytest -q "$test_file"
done

git diff --check
echo 'W05 summary-export gate: PASS'
