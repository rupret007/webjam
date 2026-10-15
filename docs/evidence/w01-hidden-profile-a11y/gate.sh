#!/usr/bin/env bash
# W01 accessibility gate. Run from the worktree root; offline, no installs.
set -euo pipefail
[[ -f webjam_qt/windows/launch_dialog.py && -x .venv/bin/python ]] || {
  echo 'Run from the W01 worktree root with its .venv available.' >&2
  exit 1
}
export QT_QPA_PLATFORM=offscreen
export PYTHONDONTWRITEBYTECODE=1
export PIP_DISABLE_PIP_VERSION_CHECK=1

# Focused regression includes hidden accessibility metadata and round trips.
.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_art_start_ux.py \
  -k 'hidden_profile_picker_a11y or profile_picker_a11y_tracks'

# Standard offline source checks from CONTRIBUTING.md / DEVELOPMENT.md.
.venv/bin/ruff check webjam_qt/ core/ ui/ services/ api/
.venv/bin/python -m compileall -q core webjam_qt ui services api tests
.venv/bin/python -m pip check
.venv/bin/python ux_smoke_test.py
git diff --check HEAD

# Standard Python gate for the touched launch/profile area: fresh interpreter
# per complete module, retaining every assertion, without retries.
for test_file in \
  tests/test_art_start_ux.py \
  tests/test_host_share_join_flow.py \
  tests/test_ui_redesign_regressions.py \
  tests/test_launch_keyboard_compact.py \
  tests/test_creative_modes_edge.py \
  tests/test_first_run_setup.py \
  tests/test_qt_widgets.py
do
  echo "Testing ${test_file}"
  .venv/bin/python -m pytest "$test_file" -q -p no:cacheprovider
done
echo 'W01 accessibility gate: PASS'
