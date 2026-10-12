#!/usr/bin/env bash
# W21: local Paint along natural completion and replay.
# Run from the worktree root. Offline; no real media or external services.
set -euo pipefail
export QT_QPA_PLATFORM=offscreen
export PYTHONDONTWRITEBYTECODE=1
PY=.venv/bin/python
[[ -x "$PY" ]] || { echo 'Run from the worktree root with .venv/bin/python available.' >&2; exit 1; }

# Focused regressions, each module in a fresh process (CONTRIBUTING.md).
"$PY" -m pytest -q tests/test_reference_video.py -k 'local_natural_end or local_pause_mid or local_replay or local_completion or local_seek_after'
"$PY" -m pytest -q tests/test_reference_video_coordinator.py -k local_natural_end
"$PY" -m pytest -q tests/test_reference_video_ui.py -k local_natural_end
"$PY" -m pytest -q tests/test_paint_along_player_health.py -k 'local_playback_state or local_end_observation'

# Standard Python checks and the full touched-area modules, without retries.
.venv/bin/ruff check webjam_qt/ core/ ui/ services/ api/
"$PY" -m compileall -q core webjam_qt ui services api tests
"$PY" -m pip check
"$PY" ux_smoke_test.py
for test_file in \
  tests/test_reference_video.py \
  tests/test_reference_video_coordinator.py \
  tests/test_reference_video_ui.py \
  tests/test_paint_along_player_health.py \
  tests/test_reference_video_youtube.py \
  tests/test_youtube_video_player.py \
  tests/test_reference_video_application_integration.py \
  tests/test_paint_along_youtube_journey.py \
  tests/test_paint_along_guest_journey.py \
  tests/test_paint_along_host_opening.py \
  tests/test_paint_along_host_opening_ui.py \
  tests/test_paint_along_copy_recovery_ui.py \
  tests/test_paint_along_seek_input.py \
  tests/test_follow_along_journey.py \
  tests/test_session_transfer_reference_video.py \
  tests/test_room_clock.py \
  tests/test_room_clock_coordinator.py \
  tests/test_art_start_ux.py \
  tests/test_creative_modes_edge.py; do
  "$PY" -m pytest "$test_file" -q
done
git diff --check
