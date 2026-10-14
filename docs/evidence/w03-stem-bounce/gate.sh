#!/usr/bin/env bash
# W03: run offline from the worktree root; fresh Python per test module (CI).
set -euo pipefail
export QT_QPA_PLATFORM=offscreen
export PYTHONDONTWRITEBYTECODE=1
export PIP_DISABLE_PIP_VERSION_CHECK=1
export PIP_NO_INDEX=1
export PIP_NO_CACHE_DIR=1
PY=.venv/bin/python
status=0
run() {
  echo "+ $*"
  if "$@"; then :; else status=1; fi
}

# Fail on missing dependencies rather than allowing importorskip to hide coverage.
"$PY" -c 'import numpy, soundfile, pytest, PySide6'
run "$PY" -m pytest tests/test_stem_bench.py -q -k bounce_publication
run "$PY" -m ruff check webjam_qt/ core/ ui/ services/ api/
run "$PY" -m ruff check tests/test_stem_bench.py
compile_cache=$(mktemp -d "${TMPDIR:-/tmp}/webjam-w03-bounce-compile.XXXXXX")
trap 'rm -rf "$compile_cache"' EXIT
run env PYTHONPYCACHEPREFIX="$compile_cache" "$PY" -m compileall -q core webjam_qt ui services api tests
run "$PY" -m pip check
run "$PY" tools/runtime_dependency_policy.py --check
run "$PY" ux_smoke_test.py
# Standard Python gate for the changed core, its consumers, and the door UX bar.
for test_file in \
  tests/test_stem_bench.py \
  tests/test_song_workbench.py \
  tests/test_song_tools_coordinator.py \
  tests/test_song_overlay_ui.py \
  tests/test_art_start_ux.py; do
  run "$PY" -m pytest "$test_file" -q
done
run git diff --check
if ((status != 0)); then
  echo 'W03 stem bounce gate: FAIL (see results above)'
  exit "$status"
fi
echo 'W03 stem bounce gate: PASS'
