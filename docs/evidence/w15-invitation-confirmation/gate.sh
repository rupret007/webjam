#!/usr/bin/env bash
# Run from the W15 worktree root. Uses installed dependencies; no downloads.
set -euo pipefail
export QT_QPA_PLATFORM=offscreen
export PIP_DISABLE_PIP_VERSION_CHECK=1
python=.venv/bin/python
test -x "$python"
test -f webjam_qt/controllers/application_controller.py

# Focused regressions, injecting reentrant arrivals during confirmation.
"$python" -m pytest -q tests/test_host_share_join_flow.py \
  -k 'invite_confirmation or single_flight_and_latest_wins'
"$python" -m pytest -q tests/test_controller_remote_transport.py \
  -k latest_remote_invite

# The repository's standard offline Python checks (.github/workflows/ci.yml).
"$python" -m pip check
"$python" -m compileall -q core webjam_qt ui services api tests
"$python" tools/runtime_dependency_policy.py --check
git diff --check HEAD
"$python" -m ruff check webjam_qt/ core/ ui/ services/ api/
"$python" ux_smoke_test.py

# CI gives each Qt test module its own native process lifetime. Apply that
# same gate to the invitation, lifecycle, shutdown, recording guard and UX areas.
for test_file in \
  tests/test_host_share_join_flow.py \
  tests/test_controller_remote_transport.py \
  tests/test_runtime_shutdown_fail_closed.py \
  tests/test_workspace_navigation.py \
  tests/test_art_invitation_flow.py \
  tests/test_art_invitation_entry.py \
  tests/test_invitation_conversation_lifetime.py \
  tests/test_invitation_conversation_ingress.py \
  tests/test_reference_track_application_integration.py \
  tests/test_jamulus_native_startup.py \
  tests/test_art_start_ux.py; do
  "$python" -m pytest "$test_file" -v
done
