#!/usr/bin/env bash
# W21: falsey malformed input maps must not authorize supplemental capture.
# Run offline from the worktree root; synthetic/offscreen checks only.
set -euo pipefail
export QT_QPA_PLATFORM=offscreen
export PYTHONDONTWRITEBYTECODE=1
PY=.venv/bin/python
[[ -x "$PY" ]] || { echo 'Run from the worktree root with .venv/bin/python available.' >&2; exit 1; }

# Focused regressions and compatibility controls.
"$PY" -m pytest -q -p no:cacheprovider tests/test_input_map_settings.py \
  -k 'falsey_malformed or legacy_empty_input_maps or valid_input_maps_preserve'

# Standard Python gate from CONTRIBUTING.md / DEVELOPMENT.md.
.venv/bin/ruff check webjam_qt/ core/ ui/ services/ api/
"$PY" -m compileall -q core webjam_qt ui services api tests
"$PY" -m pip check
"$PY" ux_smoke_test.py

# Full touched-area modules, each in a fresh interpreter, without retries.
for test_file in \
  tests/test_input_map_settings.py \
  tests/test_settings_edge.py \
  tests/test_settings_corruption_recovery.py \
  tests/test_recording_format_settings.py \
  tests/test_session_recording_plan.py \
  tests/test_local_capture.py \
  tests/test_recording_format_setup.py \
  tests/test_recording_format_controller.py \
  tests/test_recording_setup_compact_recovery.py \
  tests/test_recording_readiness.py \
  tests/test_recording_readiness_ui.py \
  tests/test_server_rpc_and_record_button.py \
  tests/test_dual_musician_rehearsal_lab.py \
  tests/test_multitrack_proof_lab.py \
  tests/test_art_start_ux.py \
  tests/test_creative_modes_edge.py; do
  echo "Testing $test_file"
  "$PY" -m pytest -q -p no:cacheprovider "$test_file"
done
"$PY" tools/run_multitrack_proof_lab.py --report /tmp/webjam-w21-multitrack-proof-lab.json
git diff --check
