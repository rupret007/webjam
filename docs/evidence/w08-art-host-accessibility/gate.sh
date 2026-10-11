#!/usr/bin/env bash
# Run from the worktree root, offline, with a fresh interpreter per Qt module.
set -euo pipefail
export QT_QPA_PLATFORM=offscreen
export PYTHONDONTWRITEBYTECODE=1
export PIP_NO_INDEX=1
export PIP_DISABLE_PIP_VERSION_CHECK=1

test -x .venv/bin/python
test -f webjam_qt/windows/launch_dialog.py

.venv/bin/python -m pytest tests/test_art_start_ux.py -q -p no:cacheprovider \
  -k 'art_host_copy_describes or art_host_accessibility_matches'

# Standard source checks from CONTRIBUTING.md / DEVELOPMENT.md / README.md.
.venv/bin/ruff check webjam_qt/ core/ ui/ services/ api/ tests/test_art_start_ux.py
.venv/bin/python -m compileall -q core webjam_qt ui services api tests
.venv/bin/python -m pip check
.venv/bin/python ux_smoke_test.py

# Full modules covering launch copy, cards, accessibility and host/invite flows.
for test_file in \
  tests/test_art_start_ux.py \
  tests/test_launch_keyboard_compact.py \
  tests/test_art_join_entry_ui.py \
  tests/test_art_participant_door.py \
  tests/test_art_host_platforms.py \
  tests/test_art_profile.py \
  tests/test_creative_modes_edge.py \
  tests/test_first_run_setup.py \
  tests/test_host_share_join_flow.py \
  tests/test_host_share_readiness.py \
  tests/test_remote_launch_ingress.py \
  tests/test_brand_mark.py \
  tests/test_polish_1006_round2.py \
  tests/test_feel_pass.py \
  tests/test_ui_redesign_regressions.py; do
  echo "Running ${test_file}"
  .venv/bin/python -m pytest "$test_file" -q -p no:cacheprovider
done

git diff --check
echo "W08 a11y gate: PASS"
