#!/usr/bin/env bash
# W19: run from the worktree root, offline; no physical/live checks.
set -euo pipefail
export QT_QPA_PLATFORM=offscreen
export PYTHONDONTWRITEBYTECODE=1

# Focused regressions, including publication cleanup and musician guidance.
.venv/bin/python -m pytest tests/test_take_export.py -q -k 'source_changes_mid_render or unchanged_sources_have_verified_receipt or closes_pinned_sources'
.venv/bin/python -m pytest tests/test_recording_studio.py -q -k source_change_gives_reopen

# Standard repository Python/static gate (DEVELOPMENT.md / CONTRIBUTING.md).
.venv/bin/python -m ruff check webjam_qt/ core/ ui/ services/ api/
.venv/bin/python -m ruff check tests/test_take_export.py tests/test_recording_studio.py tests/test_send_to_logic_ui.py
.venv/bin/python -m compileall -q core webjam_qt ui services api tests
.venv/bin/python -m pip check
.venv/bin/python ux_smoke_test.py

# Complete relevant modules; fresh interpreters preserve Qt lifetime isolation.
for test_file in \
  tests/test_take_export.py \
  tests/test_recording_studio.py \
  tests/test_export_receipt.py \
  tests/test_take_project.py \
  tests/test_take_library.py \
  tests/test_studio_export.py \
  tests/test_studio_renderer.py \
  tests/test_studio_project.py \
  tests/test_studio_retired_callbacks.py \
  tests/test_studio_compact_workspace.py \
  tests/test_send_to_logic_ui.py \
  tests/test_logic_handoff.py \
  tests/test_logic_handoff_adapter.py \
  tests/test_art_start_ux.py; do
  echo "Testing $test_file"
  .venv/bin/python -m pytest "$test_file" -q
done
git diff --check
