#!/usr/bin/env bash
# Run offline from the worktree root with the existing virtual environment.
set -euo pipefail
export QT_QPA_PLATFORM=offscreen
export PYTHONDONTWRITEBYTECODE=1
gate_cache="$(mktemp -d "${TMPDIR:-/tmp}/webjam-w03-headline.XXXXXX")"
trap 'rm -rf "$gate_cache"' EXIT
export PYTHONPYCACHEPREFIX="$gate_cache"

# Focused red/green regressions, followed by whole affected modules.
.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_reference_video_ui.py \
  -k 'the_host_panel_opens_on_the_no_video_path or empty_host_keeps_shared_lesson_first_above_silent_reference or the_host_panel_shows_a_failure_instead_of_a_stale_source'

# CONTRIBUTING.md / CI require a fresh interpreter for each Qt test module.
for test_file in \
  tests/test_reference_video_ui.py \
  tests/test_reference_video_coordinator.py \
  tests/test_reference_video_youtube.py \
  tests/test_paint_along_host_opening_ui.py \
  tests/test_paint_along_guest_opening_ui.py \
  tests/test_paint_along_copy_recovery_ui.py \
  tests/test_paint_along_youtube_ui.py \
  tests/test_polish_1006_round2.py \
  tests/test_art_start_ux.py; do
  printf '\n%s\n' "$test_file"
  .venv/bin/python -m pytest -q -p no:cacheprovider "$test_file"
done

.venv/bin/python ux_smoke_test.py
.venv/bin/python -m ruff check webjam_qt/windows/reference_video.py tests/test_reference_video_ui.py
.venv/bin/python -m compileall -q webjam_qt/windows/reference_video.py tests/test_reference_video_ui.py
.venv/bin/python -m pip check
git diff --check
