#!/usr/bin/env bash
# W01: offline, from the worktree root; fresh interpreter per Qt test module.
set -euo pipefail
export QT_QPA_PLATFORM=offscreen
export PYTHONDONTWRITEBYTECODE=1
PY=.venv/bin/python

"$PY" -m pytest -q -p no:cacheprovider tests/test_music_idle_summary.py
"$PY" -m ruff check webjam_qt/ core/ ui/ services/ api/ tests/test_music_idle_summary.py
compile_cache=$(mktemp -d "${TMPDIR:-/tmp}/webjam-w01-compile.XXXXXX")
trap 'rm -rf "$compile_cache"' EXIT
PYTHONPYCACHEPREFIX="$compile_cache" "$PY" -m compileall -q core webjam_qt ui services api tests
"$PY" -m pip check
"$PY" ux_smoke_test.py

# Standard Python gate for the touched presentation path, its startup/recovery
# callers, and the mandated launch-copy contract. No test filters or retries.
for test_file in \
  tests/test_qt_widgets.py \
  tests/test_session_conductor.py \
  tests/test_creator_live_presentation.py \
  tests/test_unified_musician_guidance.py \
  tests/test_jamulus_native_startup.py \
  tests/test_session_hud_accessibility.py \
  tests/test_art_start_ux.py; do
  "$PY" -m pytest -q -p no:cacheprovider "$test_file"
done

echo 'W01 gate: PASS'
