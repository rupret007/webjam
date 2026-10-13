#!/usr/bin/env bash
# W17 offline Python gate. Run from the worktree root; no native build or network.
set -euo pipefail
export QT_QPA_PLATFORM=offscreen
export PYTHONDONTWRITEBYTECODE=1

.venv/bin/python -m ruff check webjam_qt/ core/ ui/ services/ api/
.venv/bin/python -m compileall -q core webjam_qt ui services api tests
.venv/bin/python -m pip check
.venv/bin/python ux_smoke_test.py

# Fresh interpreters preserve the repository's Qt lifetime isolation policy.
# Focused regressions first, then the transport boundary, its Python consumers,
# and the required door/start-card UX checks. No live/native integration gate.
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
  tests/test_native_admission_deferred_profile.py
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
  tests/test_art_start_ux.py
  tests/test_creative_modes_edge.py
)
for test_file in "${test_files[@]}"; do
  echo "Running ${test_file}"
  .venv/bin/python -m pytest "$test_file" -q
done
