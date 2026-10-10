#!/bin/bash
# Run from the W29 worktree root. Uses installed dependencies; no downloads.
set -euo pipefail
export QT_QPA_PLATFORM=offscreen

test -x .venv/bin/python
.venv/bin/python -m pytest tests/test_art_invite_switch_copy.py -q
.venv/bin/ruff check webjam_qt/ core/ ui/ services/ api/ tests/test_art_invite_switch_copy.py
.venv/bin/python -m compileall -q core webjam_qt ui services api tests
.venv/bin/python -m pip check
.venv/bin/python ux_smoke_test.py

# Standard Python gate for the touched invitation/controller/Art surfaces:
# one complete module per interpreter, as in CONTRIBUTING.md and CI.
# No filters, retries, assertion changes, or network/package installation.
for test_file in \
  tests/test_host_share_join_flow.py \
  tests/test_art_invitation_flow.py \
  tests/test_invitation_ingress.py \
  tests/test_invitation_conversation_ingress.py \
  tests/test_controller_remote_transport.py \
  tests/test_runtime_shutdown_fail_closed.py \
  tests/test_offline_invitation_gate.py \
  tests/test_application_controller_creator_copy.py \
  tests/test_art_room_controller.py \
  tests/test_art_start_ux.py \
  tests/test_art_participant_door.py \
  tests/test_art_invitation_entry.py
do
  printf '\nRunning %s\n' "$test_file"
  .venv/bin/python -m pytest "$test_file" -q
done
git diff --check
