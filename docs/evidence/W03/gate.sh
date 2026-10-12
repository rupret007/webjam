#!/usr/bin/env bash
# Run from the worktree root, offline, with the existing virtual environment.
set -euo pipefail
export QT_QPA_PLATFORM=offscreen
export PYTHONDONTWRITEBYTECODE=1
gate_cache="$(mktemp -d "${TMPDIR:-/tmp}/webjam-w03-gate.XXXXXX")"
trap 'rm -rf "$gate_cache"' EXIT
export PYTHONPYCACHEPREFIX="$gate_cache"

# Focused regression first; standard pytest gate for affected areas follows.
# Each module gets a fresh interpreter, as required by CONTRIBUTING.md / CI.
for test_file in \
  tests/test_paint_along_room_next_action.py \
  tests/test_art_room_presence.py \
  tests/test_art_room_activities.py \
  tests/test_art_room_overview.py \
  tests/test_art_room_overview_ui.py \
  tests/test_art_room_overview_controller.py \
  tests/test_art_room_overview_lifetime.py \
  tests/test_art_room_activities_ui.py \
  tests/test_art_room_controller.py \
  tests/test_art_session_strip.py \
  tests/test_art_conversation_layout.py \
  tests/test_art_conversation_next_action.py \
  tests/test_art_video_offer_navigation.py \
  tests/test_art_room_connection_names.py \
  tests/test_art_room_connection_names_journey.py \
  tests/test_art_start_ux.py; do
  printf '\n%s\n' "$test_file"
  .venv/bin/python -m pytest -q -p no:cacheprovider "$test_file"
done

.venv/bin/python ux_smoke_test.py
.venv/bin/python -m ruff check core/art_room_presence.py core/art_room_overview.py \
  webjam_qt/widgets/art_room_overview.py tests/test_paint_along_room_next_action.py \
  tests/test_art_room_overview_controller.py
.venv/bin/python -m compileall -q core/art_room_presence.py core/art_room_overview.py \
  webjam_qt/widgets/art_room_overview.py tests/test_paint_along_room_next_action.py \
  tests/test_art_room_overview_controller.py
.venv/bin/python -m pip check
git diff --check
