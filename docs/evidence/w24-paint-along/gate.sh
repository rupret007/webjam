#!/bin/bash
# Run from the W24 worktree root. Offline; fresh interpreter per Qt module.
set -euo pipefail
export QT_QPA_PLATFORM=offscreen
export PYTHONDONTWRITEBYTECODE=1

test -x .venv/bin/python
.venv/bin/python -m pytest tests/test_youtube_video_player.py \
  -q -p no:cacheprovider \
  -k 'clipped_surface or real_hidden_surface'

# CONTRIBUTING.md / CI's standard Python gate, scoped to Paint along and
# workspace navigation, plus the campaign's mandatory start-screen UX checks.
for test_file in \
  tests/test_youtube_video_player.py \
  tests/test_youtube_lesson.py \
  tests/test_reference_video.py \
  tests/test_reference_video_youtube.py \
  tests/test_reference_video_coordinator.py \
  tests/test_reference_video_ui.py \
  tests/test_reference_video_application_integration.py \
  tests/test_session_transfer_reference_video.py \
  tests/test_paint_along_youtube_ui.py \
  tests/test_paint_along_youtube_journey.py \
  tests/test_workspace_navigation.py \
  tests/test_art_start_ux.py
do
  printf '\nTesting %s\n' "$test_file"
  .venv/bin/python -m pytest "$test_file" -q -p no:cacheprovider
done

.venv/bin/python ux_smoke_test.py
.venv/bin/python - <<'PY'
import compileall
import sys
from tempfile import TemporaryDirectory

with TemporaryDirectory(prefix="webjam-w24-pycache-") as cache:
    sys.pycache_prefix = cache
    results = [
        compileall.compile_dir(area, quiet=1)
        for area in ("core", "webjam_qt", "ui", "services", "api", "tests")
    ]
    if not all(results):
        raise SystemExit(1)
PY
.venv/bin/python -m pip check
.venv/bin/ruff check webjam_qt/ core/ ui/ services/ api/
git diff --check
