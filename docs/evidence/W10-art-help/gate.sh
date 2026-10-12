#!/usr/bin/env bash
# W10 Art Help: run offline from the worktree root (<25 minutes).
set -euo pipefail
export QT_QPA_PLATFORM=offscreen
export PYTHONDONTWRITEBYTECODE=1
PY=.venv/bin/python

"$PY" -m pytest -q -p no:cacheprovider tests/test_art_help_launch.py

# Standard Python checks, plus complete touched-area modules in fresh
# interpreters as required by CONTRIBUTING.md for Qt native lifetimes.
"$PY" -m ruff check webjam_qt/ core/ ui/ services/ api/
"$PY" -m ruff check tests/test_art_help_launch.py
"$PY" -m compileall -q core webjam_qt ui services api tests
"$PY" -m pip check
"$PY" tools/runtime_dependency_policy.py --check
"$PY" ux_smoke_test.py
for test_file in \
  tests/test_help_dialog.py \
  tests/test_workflow_help.py \
  tests/test_workflow_help_integration.py \
  tests/test_art_start_ux.py \
  tests/test_art_profile.py \
  tests/test_creator_live_presentation.py \
  tests/test_creative_modes_edge.py; do
  printf '\n%s\n' "$test_file"
  "$PY" -m pytest -q -p no:cacheprovider "$test_file"
done

git diff --check
echo 'W10 Art Help gate: PASS'
