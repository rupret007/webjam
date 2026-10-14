#!/usr/bin/env bash
# W23: reconcile regular workspace save after post-primary directory-sync failure.
# Run offline from the assigned worktree root; each Qt module gets a fresh process.
set -euo pipefail
export QT_QPA_PLATFORM=offscreen
export PYTHONDONTWRITEBYTECODE=1
PY=.venv/bin/python

# Focused recovery regressions, including ownership and external-change refusal.
"$PY" -m pytest -p no:cacheprovider -q tests/test_session_library_save_retry.py
"$PY" -m pytest -p no:cacheprovider -q tests/test_session_library_coordinator.py -k post_primary
"$PY" -m pytest -p no:cacheprovider -q tests/test_session_library_dialog.py -k post_primary

# Standard Python gate from CONTRIBUTING.md / DEVELOPMENT.md, scoped to the
# persistence/editor/backup callers touched here. No dependency/network installs.
"$PY" -m ruff check webjam_qt/ core/ ui/ services/ api/
"$PY" -m compileall -q core webjam_qt ui services api tests
"$PY" -m pip check
"$PY" ux_smoke_test.py
for test_file in \
  tests/test_file_io.py \
  tests/test_session_library.py \
  tests/test_session_library_coordinator.py \
  tests/test_session_library_dialog.py \
  tests/test_session_library_launch.py \
  tests/test_saved_work_restart.py \
  tests/test_workspace_backup.py \
  tests/test_workspace_backup_coordinator.py \
  tests/test_workspace_backup_library_ui.py \
  tests/test_workspace_media_backup.py \
  tests/test_workspace_media_library_ui.py \
  tests/test_art_start_ux.py; do
  "$PY" -m pytest -p no:cacheprovider -q "$test_file"
done

git diff --check
echo 'W23 workspace save reconciliation gate: PASS'
