#!/usr/bin/env bash
# W25 metadata read failures. Run offline from the worktree root.
set -euo pipefail
export QT_QPA_PLATFORM=offscreen
export PIP_DISABLE_PIP_VERSION_CHECK=1
PY=.venv/bin/python

# Regression first; each subsequent Qt test module has a fresh interpreter,
# following CONTRIBUTING.md and the repository's standard Python gate.
"$PY" -m pytest tests/test_session_persistence.py -q \
  -k 'metadata_read_error or corrupt_metadata_can_still'
"$PY" -m ruff check webjam_qt/ core/ ui/ services/ api/
"$PY" -m compileall -q core webjam_qt ui services api tests
"$PY" -m pip check
"$PY" ux_smoke_test.py

# Persistence, its Notes consumers, profile contracts, and the launch UX gate.
for test_file in \
  tests/test_session_persistence.py \
  tests/test_art_profile.py \
  tests/test_notes_discovery_ui.py \
  tests/test_notes_recovery_checkpoint_notice.py \
  tests/test_notes_combined_unreadable.py \
  tests/test_notes_restart_recovery_ui.py \
  tests/test_notes_restart_recovery.py \
  tests/test_notes_original_recheck.py \
  tests/test_notes_recovery_guidance.py \
  tests/test_notes_save_failures.py \
  tests/test_notes_workspace_recovery.py \
  tests/test_notes_export_recovery_boundary.py \
  tests/test_notes_unreadable_recheck.py \
  tests/test_notes_retry_integrity.py \
  tests/test_notes_retry_quit.py \
  tests/test_creative_modes_edge.py \
  tests/test_art_start_ux.py; do
  echo "Running $test_file"
  "$PY" -m pytest "$test_file" -q
done

echo 'W25 gate: PASS'
