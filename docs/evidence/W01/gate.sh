#!/usr/bin/env bash
# W01: run from the worktree root, using installed dependencies only.
set -euo pipefail
export QT_QPA_PLATFORM=offscreen
export PYTHONDONTWRITEBYTECODE=1

# Focused acceptance regressions (fresh Python process for each Qt module).
.venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_creator_live_presentation.py -k music_idle_guidance_names_its_primary_control
.venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_unified_musician_guidance.py -k music_idle_window_points_to_visible_start_session

# Standard Python gate for the touched presentation/controller areas, plus
# neighboring profile, lifecycle, compact-window, and launch-door contracts.
# Match CONTRIBUTING.md / CI: complete modules, isolated native Qt lifetimes.
for test_file in \
  tests/test_session_conductor.py \
  tests/test_creator_live_presentation.py \
  tests/test_unified_musician_guidance.py \
  tests/test_musician_guidance.py \
  tests/test_creator_profile_guidance.py \
  tests/test_art_profile.py \
  tests/test_art_start_ux.py \
  tests/test_recorder_and_stage_cards.py \
  tests/test_conductor_window_fit_to_screen.py \
  tests/test_art_session_strip.py \
  tests/test_in_room_honesty_goldens.py; do
  echo "pytest $test_file"
  .venv/bin/python -m pytest "$test_file" -q -p no:cacheprovider
done
.venv/bin/python ux_smoke_test.py
.venv/bin/python -m ruff check core/session_conductor.py webjam_qt/controllers/application_controller.py
.venv/bin/python -m compileall -q core/session_conductor.py webjam_qt/controllers/application_controller.py \
  tests/test_creator_live_presentation.py tests/test_unified_musician_guidance.py
git diff --check
