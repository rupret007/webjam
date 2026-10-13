#!/usr/bin/env bash
# W01: run offline from the worktree root. Fresh interpreter per Qt module.
set -euo pipefail
export QT_QPA_PLATFORM=offscreen
export PYTHONDONTWRITEBYTECODE=1
export PIP_DISABLE_PIP_VERSION_CHECK=1

.venv/bin/python -m pytest tests/test_native_admission_deferred_profile.py -q
.venv/bin/python -m ruff check webjam_qt/ core/ ui/ services/ api/
.venv/bin/python -m compileall -q core webjam_qt ui services api tests
.venv/bin/python -m pip check
.venv/bin/python tools/runtime_dependency_policy.py --check
.venv/bin/python ux_smoke_test.py

# Standard Python gate for the touched native admission/runtime/controller
# areas, Library/Studio ownership, and mandatory door/start-card UX checks.
test_files=(
  tests/test_remote_session_runtime.py
  tests/test_native_remote_transport.py
  tests/test_transport_runtime.py
  tests/test_remote_invitation_owner.py
  tests/test_remote_redaction.py
  tests/test_remote_launch_ingress.py
  tests/test_controller_remote_transport.py
  tests/test_controller_room_help.py
  tests/test_room_help_controller.py
  tests/test_art_native_retry.py
  tests/test_art_pending_profile_facts.py
  tests/test_native_art_activities.py
  tests/test_native_art_room_return.py
  tests/test_art_room_controller.py
  tests/test_art_room_recovery.py
  tests/test_art_room_overview_controller.py
  tests/test_art_cold_invitation_context.py
  tests/test_art_canvas_connection_journey.py
  tests/test_canvas_publication_journey.py
  tests/test_invitation_conversation_lifetime.py
  tests/test_art_conversation_link_journey.py
  tests/test_art_notes_conversation_journey.py
  tests/test_art_profile_guidance_journey.py
  tests/test_art_shared_lesson_native_host.py
  tests/test_art_exit_next_action.py
  tests/test_art_lan_host_recovery.py
  tests/test_music_lan_host_recovery.py
  tests/test_two_session_art_integration.py
  tests/test_two_session_art_host_recovery.py
  tests/test_two_session_music_host_recovery.py
  tests/test_host_share_join_flow.py
  tests/test_jamulus_native_startup.py
  tests/test_offline_invitation_gate.py
  tests/test_session_library_coordinator.py
  tests/test_workspace_media_library_ui.py
  tests/test_workspace_media_activation.py
  tests/test_workspace_navigation.py
  tests/test_art_start_ux.py
  tests/test_creative_modes_edge.py
)
for test_file in "${test_files[@]}"; do
  echo "Running ${test_file}"
  .venv/bin/python -m pytest "$test_file" -q
done

git diff --check
echo "W01 gate: PASS"
