#!/usr/bin/env bash
# W09: run offline from the worktree root; no installs or external services.
set -euo pipefail
export QT_QPA_PLATFORM=offscreen
PY=.venv/bin/python

"$PY" -m pytest tests/test_webex_settings_ui.py -q \
  -k settings_opened_from_profile_has_matching_subtitle_and_sections

# Standard source gate and fresh Python interpreter per relevant test module
# (CONTRIBUTING.md / DEVELOPMENT.md). Fail immediately; never retry failures.
"$PY" -m ruff check webjam_qt/ core/ ui/ services/ api/
"$PY" -m compileall -q core webjam_qt ui services api tests
"$PY" -m pip check
"$PY" tools/runtime_dependency_policy.py --check
"$PY" ux_smoke_test.py
git diff --check HEAD

for test_file in \
  tests/test_webex_settings_ui.py \
  tests/test_provider_key_settings_ui.py \
  tests/test_first_run_setup.py \
  tests/test_ready_check_ui.py \
  tests/test_zero_key_music.py \
  tests/test_application_controller_video_audio_paths.py \
  tests/test_art_conversation_link_journey.py \
  tests/test_art_start_ux.py \
  tests/test_feel_pass.py; do
  "$PY" -m pytest "$test_file" -q
done

echo 'W09 gate: PASS'
