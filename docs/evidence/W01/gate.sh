#!/usr/bin/env bash
# W01 File/Help door gate: offline, run from the worktree root.
# Includes menu/profile/installer regressions in test_art_start_ux.py.
# CONTRIBUTING.md / CI require one fresh Python interpreter per Qt module.
set -euo pipefail
export QT_QPA_PLATFORM=offscreen
export PYTHONDONTWRITEBYTECODE=1
PY=.venv/bin/python
status=0
run_tests() {
  "$PY" -m pytest -q -p no:cacheprovider "$1" || status=1
}

# Focused UX regressions (whole modules; no filters, retries or added skips).
for test_file in \
  tests/test_art_start_ux.py \
  tests/test_feel_pass.py \
  tests/test_ui_redesign_regressions.py \
  tests/test_host_share_join_flow.py; do
  run_tests "$test_file"
done

"$PY" -m ruff check webjam_qt/ core/ ui/ services/ api/ tests/test_art_start_ux.py tests/test_feel_pass.py tests/test_workspace_navigation.py tests/test_ui_redesign_regressions.py tests/test_host_share_join_flow.py
compile_cache=$(mktemp -d "${TMPDIR:-/tmp}/webjam-w01-compile.XXXXXX")
trap 'rm -rf "$compile_cache"' EXIT
PYTHONPYCACHEPREFIX="$compile_cache" "$PY" -m compileall -q core webjam_qt ui services api tests
"$PY" -m pip check
"$PY" ux_smoke_test.py

# Standard Python gate for the touched launch UI, saved-work entry and return.
for test_file in \
  tests/test_launch_keyboard_compact.py \
  tests/test_launch_door_tall_balance.py \
  tests/test_launch_paint_along_door_helper.py \
  tests/test_art_profile.py \
  tests/test_art_join_entry_ui.py \
  tests/test_first_run_setup.py \
  tests/test_legacy_mode_picker_retired.py \
  tests/test_session_library_launch.py \
  tests/test_workspace_navigation.py \
  tests/test_workspace_construction_failure.py \
  tests/test_offline_invitation_gate.py; do
  run_tests "$test_file"
done

if ((status != 0)); then
  echo 'W01 File-menu gate: FAIL (see test results above)' >&2
  exit "$status"
fi
echo 'W01 File-menu gate: PASS'
