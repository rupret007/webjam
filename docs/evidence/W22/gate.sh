#!/usr/bin/env bash
# W22: run offline from the worktree root. No physical/live-media claims.
set -euo pipefail
export QT_QPA_PLATFORM=offscreen
export PYTHONDONTWRITEBYTECODE=1
PY=.venv/bin/python
[[ -x "$PY" ]] || { echo 'Run from the worktree root with .venv/bin/python available.' >&2; exit 1; }

# Focused regression: actual Library guard plus controlled stale callbacks.
"$PY" -m pytest -q -p no:cacheprovider tests/test_lan_admission_deferred_profile.py

# Standard Python gate, as documented in CONTRIBUTING.md / DEVELOPMENT.md.
.venv/bin/ruff check webjam_qt/ core/ ui/ services/ api/
"$PY" -m compileall -q core webjam_qt ui services api tests
"$PY" -m pip check
"$PY" ux_smoke_test.py

# Complete touched-area modules in fresh interpreters, without retries.
for test_file in \
  tests/test_native_admission_deferred_profile.py \
  tests/test_art_room_controller.py \
  tests/test_art_pending_profile_facts.py \
  tests/test_art_room_connection_facts.py \
  tests/test_art_room_recovery.py \
  tests/test_art_room_return_journey.py \
  tests/test_art_room_overview_controller.py \
  tests/test_art_lan_retry.py \
  tests/test_art_native_retry.py \
  tests/test_music_lan_guest_handoff_recovery.py \
  tests/test_lan_invitation_rejection.py \
  tests/test_rejected_invitation_journey.py \
  tests/test_art_cold_invitation_context.py \
  tests/test_art_invitation_flow.py \
  tests/test_art_invitation_entry.py \
  tests/test_art_audio_cleanup.py \
  tests/test_runtime_shutdown_fail_closed.py \
  tests/test_application_controller_band_check_gate.py \
  tests/test_guest_preflight_shared_recovery.py \
  tests/test_guest_recording_preflight_guidance.py \
  tests/test_controller_remote_transport.py \
  tests/test_native_art_room_return.py \
  tests/test_art_profile_guidance_journey.py \
  tests/test_art_room_connection_names_journey.py \
  tests/test_art_lesson_request_journey.py \
  tests/test_lan_lesson_request_worker.py \
  tests/test_session_transfer_art_start.py \
  tests/test_two_session_art_integration.py \
  tests/test_two_session_art_host_recovery.py \
  tests/test_invitation_conversation_lifetime.py \
  tests/test_invitation_conversation_journey.py \
  tests/test_composed_invitation_music_mix.py \
  tests/test_composed_music_invitation_conversation.py \
  tests/test_follow_along_music_guest.py \
  tests/test_follow_along_reconnect.py \
  tests/test_follow_along_repeated_session.py \
  tests/test_host_share_join_flow.py \
  tests/test_recording_format_controller.py \
  tests/test_reference_video_application_integration.py \
  tests/test_reference_track_application_integration.py \
  tests/test_art_start_ux.py \
  tests/test_creative_modes_edge.py; do
  echo "Testing $test_file"
  "$PY" -m pytest -q -p no:cacheprovider "$test_file"
done
git diff --check
