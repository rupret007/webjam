#!/usr/bin/env bash
# Run from the worktree root. Offline; no installs, builds, or live sessions.
set -euo pipefail
export QT_QPA_PLATFORM=offscreen
export PYTHONDONTWRITEBYTECODE=1

# Focused regression first, then the standard Python checks for touched areas.
.venv/bin/python -m pytest tests/test_lan_art_start.py -q
.venv/bin/ruff check webjam_qt/ core/ ui/ services/ api/
.venv/bin/python -m compileall -q core webjam_qt ui services api tests
.venv/bin/python -m pip check
.venv/bin/python ux_smoke_test.py

# CONTRIBUTING.md / CI: each complete module gets a fresh Qt interpreter;
# stop at any failure, with no retries, filtering, or new skips.
test_files=(
  tests/test_art_start_ux.py
  tests/test_art_room_controller.py
  tests/test_art_room_overview_controller.py
  tests/test_art_room_presence.py
  tests/test_art_room_overview.py
  tests/test_art_room_overview_ui.py
  tests/test_art_room_contract.py
  tests/test_art_room_recovery.py
  tests/test_art_room_connection_facts.py
  tests/test_art_pending_profile_facts.py
  tests/test_art_lan_retry.py
  tests/test_art_exit_next_action.py
  tests/test_art_paint_along_connection_journey.py
  tests/test_art_video_offer_navigation.py
  tests/test_two_session_art_integration.py
  tests/test_composed_invitation_music_mix.py
  tests/test_rejected_invitation_journey.py
  tests/test_invitation_conversation_lifetime.py
  tests/test_lan_invitation_rejection.py
  tests/test_music_lan_guest_handoff_recovery.py
  tests/test_follow_along_reconnect.py
  tests/test_session_transfer_art_start.py
  tests/test_room_state.py
  tests/test_reference_video_coordinator.py
  tests/test_reference_video_application_integration.py
  tests/test_shared_canvas_coordinator.py
  tests/test_room_clock_coordinator.py
)
for test_file in "${test_files[@]}"; do
  echo "Testing $test_file"
  .venv/bin/python -m pytest "$test_file" -q
done
