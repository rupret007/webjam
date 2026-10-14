#!/usr/bin/env bash
# W26: run offline from the worktree root using installed dependencies.
set -euo pipefail
export QT_QPA_PLATFORM=offscreen
PY=.venv/bin/python

# Focused regressions, including the valid video-with-playlist-context control.
"$PY" -m pytest tests/test_youtube_lesson.py -q \
  -k 'embed_pseudo_ids or video_embed_with_playlist_context'
"$PY" -m pytest tests/test_reference_video_youtube.py -q -k embed_pseudo_ids

# Standard repository source checks and UX gate.
"$PY" -m ruff check webjam_qt/ core/ ui/ services/ api/
"$PY" -m compileall -q core webjam_qt ui services api tests
"$PY" -m pip check
"$PY" ux_smoke_test.py
git diff --check

# Standard fresh-interpreter-per-module Python gate for the affected paths.
for test_file in \
  tests/test_youtube_lesson.py \
  tests/test_reference_video_youtube.py \
  tests/test_reference_video.py \
  tests/test_reference_video_coordinator.py \
  tests/test_youtube_video_player.py \
  tests/test_paint_along_youtube_ui.py \
  tests/test_paint_along_youtube_journey.py \
  tests/test_follow_along_handoff_ui.py \
  tests/test_saved_follow_along.py \
  tests/test_rehearsal_plan.py \
  tests/test_art_workspace.py \
  tests/test_art_start_ux.py; do
  echo "W26 gate: $test_file"
  "$PY" -m pytest "$test_file" -q
done

echo 'W26 gate: PASS'
