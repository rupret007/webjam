#!/usr/bin/env bash
# Run from the W15 worktree root. Offline; fresh Qt interpreter per module.
set -euo pipefail
export QT_QPA_PLATFORM=offscreen
.venv/bin/ruff check webjam_qt/controllers/application_controller.py webjam_qt/session_state.py webjam_qt/widgets/participant_grid.py tests/test_connected_session_presentation.py
.venv/bin/python -m compileall -q core webjam_qt ui services api tests
.venv/bin/python -m pip check
.venv/bin/python ux_smoke_test.py
for test_file in \
  tests/test_connected_session_presentation.py \
  tests/test_session_conductor.py \
  tests/test_musician_guidance.py \
  tests/test_unified_musician_guidance.py \
  tests/test_application_controller_toggle.py \
  tests/test_application_controller_session_pulse.py \
  tests/test_music_status_chips.py \
  tests/test_status_chip_policy.py \
  tests/test_qt_widgets.py \
  tests/test_session_hud_accessibility.py \
  tests/test_art_start_ux.py \
  tests/test_conductor_window_fit_to_screen.py \
  tests/test_recovery_lifecycle_contract.py \
  tests/test_application_controller_band_check_gate.py; do
  echo "Testing $test_file"
  .venv/bin/python -m pytest "$test_file" -q
done
