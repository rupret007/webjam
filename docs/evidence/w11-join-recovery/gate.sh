#!/usr/bin/env bash
# Run from the W11 worktree root. Offline; each Qt module gets a fresh process.
set -euo pipefail
export QT_QPA_PLATFORM=offscreen
export PIP_DISABLE_PIP_VERSION_CHECK=1

test -x .venv/bin/python
.venv/bin/python -m pytest tests/test_invitation_ingress.py -q -k oversized
.venv/bin/python -m pytest tests/test_host_share_join_flow.py -q -k oversized

# Standard Python checks from CONTRIBUTING.md / DEVELOPMENT.md.
.venv/bin/ruff check webjam_qt/ core/ ui/ services/ api/
.venv/bin/python -m compileall -q core webjam_qt ui services api tests
.venv/bin/python -m pip check
.venv/bin/python ux_smoke_test.py

# Full modules for invite parsing, consumers, privacy, and the launch UX gate.
# Preserve CI's fresh-interpreter isolation; no retries or assertion filtering.
for test_file in \
  tests/test_invitation_ingress.py \
  tests/test_host_share_join_flow.py \
  tests/test_session_transfer_runtime.py \
  tests/test_remote_invitation.py \
  tests/test_remote_redaction.py \
  tests/test_music_lan_host_recovery.py \
  tests/test_session_library_launch.py \
  tests/test_workspace_navigation.py \
  tests/test_art_join_entry_ui.py \
  tests/test_art_start_ux.py \
  tests/test_launch_keyboard_compact.py
do
  printf '\nRunning %s\n' "$test_file"
  .venv/bin/python -m pytest "$test_file" -q
done
git diff --check
