#!/usr/bin/env bash
# Run offline from the W10 worktree root. Fresh processes isolate Qt lifetimes.
set -euo pipefail
export QT_QPA_PLATFORM=offscreen
export PYTHONDONTWRITEBYTECODE=1
PY=.venv/bin/python

"$PY" -m pytest -q -p no:cacheprovider tests/test_art_help_first_screen.py
"$PY" -m ruff check webjam_qt/ core/ ui/ services/ api/
"$PY" -m ruff check tests/test_art_help_first_screen.py tests/test_help_dialog.py
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
  tests/test_creative_modes_edge.py \
  tests/test_brand_mark.py \
  tests/test_theme_qss.py \
  tests/test_qt_widgets.py \
  tests/test_application_controller_video_audio_paths.py; do
  printf '\n%s\n' "$test_file"
  "$PY" -m pytest -q -p no:cacheprovider "$test_file"
done
git diff --check
printf '\nW10 compact Art Help gate: PASS\n'
