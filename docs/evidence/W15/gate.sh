#!/usr/bin/env bash
# W15 legacy invite routing. Run offline from the worktree root.
# Match CONTRIBUTING.md/CI: give each Qt test module a fresh interpreter.
set -euo pipefail
export QT_QPA_PLATFORM=offscreen
.venv/bin/python -m pytest tests/test_art_cold_invitation_context.py -q -k legacy_invite
.venv/bin/python -m ruff check webjam_qt/ core/ ui/ services/ api/
.venv/bin/python -m compileall -q core webjam_qt ui services api tests
.venv/bin/python -m pip check
.venv/bin/python ux_smoke_test.py
for test_file in \
  tests/test_art_cold_invitation_context.py \
  tests/test_art_start_ux.py \
  tests/test_art_invitation_entry.py \
  tests/test_art_invitation_flow.py \
  tests/test_art_room_controller.py \
  tests/test_art_pending_profile_facts.py \
  tests/test_art_profile.py \
  tests/test_creator_profile_guidance.py \
  tests/test_jamulus_native_startup.py \
  tests/test_host_share_join_flow.py \
  tests/test_invitation_ingress.py \
  tests/test_offline_invitation_gate.py \
  tests/test_invitation_conversation_lifetime.py \
  tests/test_rejected_invitation_journey.py \
  tests/test_controller_remote_transport.py \
  tests/test_remote_host_loopback_binding.py \
  tests/test_remote_launch_ingress.py \
  tests/test_composed_invitation_music_mix.py \
  tests/test_music_lan_guest_handoff_recovery.py \
  tests/test_local_originals_choice_profiles.py \
  tests/test_session_library_launch.py \
  tests/test_session_library_coordinator.py \
  tests/test_application_controller_band_check_gate.py \
  tests/test_recovery_lifecycle_contract.py; do
  echo "Testing $test_file"
  .venv/bin/python -m pytest "$test_file" -q
done
git diff --check
