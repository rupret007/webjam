#!/usr/bin/env bash
# W01 combined gate: launch File/Help menus and native invitation media ownership.
# Run offline from the worktree root, with a fresh interpreter per Qt module.
set -euo pipefail
export QT_QPA_PLATFORM=offscreen
export PYTHONDONTWRITEBYTECODE=1
export PIP_DISABLE_PIP_VERSION_CHECK=1
PY="${PYTHON:-python3.11}"
status=0

run_tests() {
  echo "Running ${1}"
  "$PY" -m pytest -q -p no:cacheprovider "$1" || status=1
}

# Union of both W01 gates: complete modules, no filters, retries or added skips.
# This includes the compact launch footer and Art-door layout regressions.
test_files=(
  tests/test_art_start_ux.py
  tests/test_feel_pass.py
  tests/test_ui_redesign_regressions.py
  tests/test_host_share_join_flow.py
  tests/test_workspace_navigation.py
  tests/test_launch_keyboard_compact.py
  tests/test_launch_door_tall_balance.py
  tests/test_launch_paint_along_door_helper.py
  tests/test_art_profile.py
  tests/test_art_join_entry_ui.py
  tests/test_first_run_setup.py
  tests/test_legacy_mode_picker_retired.py
  tests/test_session_library_launch.py
  tests/test_workspace_construction_failure.py
  tests/test_offline_invitation_gate.py
  tests/test_native_admission_deferred_profile.py
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
  tests/test_jamulus_native_startup.py
  tests/test_session_library_coordinator.py
  tests/test_workspace_media_library_ui.py
  tests/test_workspace_media_activation.py
  tests/test_creative_modes_edge.py
)
for test_file in "${test_files[@]}"; do
  run_tests "$test_file"
done

"$PY" -m ruff check webjam_qt/ core/ ui/ services/ api/ tests/test_art_start_ux.py tests/test_feel_pass.py tests/test_workspace_navigation.py tests/test_ui_redesign_regressions.py tests/test_host_share_join_flow.py
compile_cache=$(mktemp -d "${TMPDIR:-/tmp}/webjam-w01-compile.XXXXXX")
trap 'rm -rf "$compile_cache"' EXIT
PYTHONPYCACHEPREFIX="$compile_cache" "$PY" -m compileall -q core webjam_qt ui services api tests
"$PY" -m pip check
"$PY" tools/runtime_dependency_policy.py --check
"$PY" ux_smoke_test.py
git diff --check

if ((status != 0)); then
  echo 'W01 combined gate: FAIL (see test results above)' >&2
  exit "$status"
fi
echo 'W01 combined gate: PASS'
