#!/usr/bin/env bash
# W11 Art Settings: run offline from the worktree root using the existing venv.
set -euo pipefail
export QT_QPA_PLATFORM=offscreen
PY=.venv/bin/python
result=0
run_check() {
  if "$@"; then
    return 0
  else
    result=1
  fi
}

run_check "$PY" -m pytest -q tests/test_art_settings_ux.py
run_check "$PY" -m ruff check webjam_qt/ core/ ui/ services/ api/
run_check "$PY" -m ruff check tests/test_art_settings_ux.py
run_check "$PY" -m compileall -q core webjam_qt ui services api tests
run_check "$PY" -m pip check
run_check "$PY" ux_smoke_test.py

# Standard Python gate for touched areas: fresh interpreter per full module,
# matching CONTRIBUTING.md and CI's Qt lifetime isolation. No test filtering.
for test_file in \
  tests/test_webex_settings_ui.py \
  tests/test_jamulus_name.py \
  tests/test_provider_key_settings_ui.py \
  tests/test_recording_studio.py \
  tests/test_art_conversation_link_journey.py \
  tests/test_application_controller_video_audio_paths.py \
  tests/test_art_start_ux.py \
  tests/test_creative_modes_edge.py; do
  printf '\n%s\n' "$test_file"
  run_check "$PY" -m pytest -q "$test_file"
done
run_check git diff --check
if (( result == 0 )); then
  echo 'W11 Art Settings gate: PASS'
else
  echo 'W11 Art Settings gate: FAIL (see results above)'
fi
exit "$result"
