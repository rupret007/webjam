#!/usr/bin/env bash
# Run from the worktree root. Offline; each Qt module gets a fresh interpreter.
set -euo pipefail
export QT_QPA_PLATFORM=offscreen
export PYTHONDONTWRITEBYTECODE=1

test -x .venv/bin/python
test -f webjam_qt/windows/launch_dialog.py

.venv/bin/python -m pytest tests/test_art_join_entry_ui.py -q \
  -k empty_join_explains_next_step_before_activation

# CONTRIBUTING.md's Python gate, scoped to launch/invitation and theme contracts.
for test_file in \
  tests/test_art_join_entry_ui.py \
  tests/test_art_start_ux.py \
  tests/test_art_participant_door.py \
  tests/test_host_share_join_flow.py \
  tests/test_launch_keyboard_compact.py \
  tests/test_art_cold_invitation_context.py \
  tests/test_art_invitation_flow.py \
  tests/test_remote_launch_ingress.py \
  tests/test_invitation_conversation_ingress.py \
  tests/test_theme_qss.py; do
  printf '\nRunning %s\n' "$test_file"
  .venv/bin/python -m pytest "$test_file" -q
done

.venv/bin/python ux_smoke_test.py
.venv/bin/python -m compileall -q core webjam_qt ui services api tests
.venv/bin/python -m pip check
.venv/bin/ruff check webjam_qt/ core/ ui/ services/ api/
git diff --check
