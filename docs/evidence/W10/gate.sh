#!/usr/bin/env bash
# W10 source integrity: run offline from the worktree root.
set -euo pipefail
export QT_QPA_PLATFORM=offscreen
PY=.venv/bin/python

"$PY" -m pytest tests/test_take_export.py -q -k source_changes_during_export

# Standard Python checks from README / CONTRIBUTING and CI.
"$PY" -m ruff check webjam_qt/ core/ ui/ services/ api/
"$PY" -m ruff check tests/test_take_export.py
"$PY" -m compileall -q core webjam_qt ui services api tests
"$PY" -m pip check
"$PY" tools/runtime_dependency_policy.py --check
"$PY" ux_smoke_test.py

# Complete relevant modules, each in a fresh interpreter for Qt lifetime.
for test_file in \
  tests/test_take_export.py \
  tests/test_take_project.py \
  tests/test_take_library.py \
  tests/test_export_receipt.py \
  tests/test_studio_export.py \
  tests/test_studio_comping.py \
  tests/test_recording_studio.py \
  tests/test_art_start_ux.py; do
  printf '\n%s\n' "$test_file"
  "$PY" -m pytest "$test_file" -q
done

git diff --check
echo 'W10 source integrity gate: PASS'
