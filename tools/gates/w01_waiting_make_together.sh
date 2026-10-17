#!/usr/bin/env bash
# W01: run from the worktree root, offline, using the local Python environment.
set -euo pipefail
export QT_QPA_PLATFORM=offscreen
export PYTHONDONTWRITEBYTECODE=1
export PIP_DISABLE_PIP_VERSION_CHECK=1
export PIP_NO_CACHE_DIR=1
export PIP_NO_INDEX=1
export WEBJAM_ENABLE_SENTRY=0

# Full relevant modules, each in a fresh interpreter as CONTRIBUTING.md requires.
# No shared controller or transport behavior changes in this visibility-only fix.
test_files=(
  tests/test_art_room_overview_ui.py
  tests/test_art_room_overview.py
  tests/test_art_room_overview_controller.py
  tests/test_art_room_overview_lifetime.py
  tests/test_art_room_connection_facts.py
  tests/test_art_room_activities.py
  tests/test_art_room_activities_ui.py
  tests/test_paint_along_hud_ui.py
  tests/test_art_room_presence.py
  tests/test_art_session_strip.py
  tests/test_session_hud_accessibility.py
  tests/test_art_start_ux.py
  tests/test_art_first_screen_ten_second.py
  tests/test_meeting_companion.py
  tests/test_unified_musician_guidance.py
  tests/test_polish_1006_round2.py
  tests/test_creative_modes_edge.py
  tests/test_compact_layout_smoke.py
)
w01_status=0
for test_file in "${test_files[@]}"; do
  echo "Running ${test_file}"
  .venv/bin/python -m pytest "$test_file" -q -p no:cacheprovider || w01_status=1
done

# Standard repository Python/source gate; no dependency downloads or live checks.
.venv/bin/python -m ruff check webjam_qt/ core/ ui/ services/ api/
w01_cache=$(mktemp -d "${TMPDIR:-/tmp}/w01-bytecode.XXXXXX")
trap 'rm -rf "$w01_cache"' EXIT
PYTHONPYCACHEPREFIX="$w01_cache" .venv/bin/python -m compileall -q core webjam_qt ui services api tests
.venv/bin/python -m pip check
.venv/bin/python tools/runtime_dependency_policy.py --check
.venv/bin/python tools/changelog_fragments.py check --base origin/master
.venv/bin/python ux_smoke_test.py
git diff --check
if ((w01_status != 0)); then
  echo "W01 gate: FAIL (see pytest results above)"
  exit "$w01_status"
fi
echo "W01 gate: PASS"
