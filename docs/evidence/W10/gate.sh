#!/usr/bin/env bash
# W10 recording ownership: run offline from the worktree root.
set -euo pipefail
export QT_QPA_PLATFORM=offscreen
PY=.venv/bin/python

# Focused regression first; no physical audio device is opened.
"$PY" -m pytest tests/test_project_recording.py -q \
  -k 'closes_after_start_stop_and_abort_failures or retains_stream_until_close_succeeds or retains_failed_start_until_close_succeeds or fallback_abort_reaches_original_stream'

# Standard local Python checks from CONTRIBUTING.md / DEVELOPMENT.md.
"$PY" -m ruff check webjam_qt/ core/ ui/ services/ api/
"$PY" -m compileall -q core webjam_qt ui services api tests
"$PY" -m pip check
"$PY" ux_smoke_test.py

# Full relevant modules, each in a fresh interpreter as required by CI.
for test_file in \
  tests/test_project_recording.py \
  tests/test_project_recording_commit.py \
  tests/test_recording_sources.py \
  tests/test_reference_studio_recording_ui.py \
  tests/test_reference_studio_application.py \
  tests/test_recording_studio.py \
  tests/test_recording_studio_shutdown_retry.py \
  tests/test_song_studio_clone.py \
  tests/test_v026_podcast_voice_journey.py \
  tests/test_art_start_ux.py; do
  "$PY" -m pytest "$test_file" -q
done

git diff --check
echo 'W10 recording ownership gate: PASS'
