#!/usr/bin/env bash
# W20: Guard Recording Setup Save on session change.
# Run from the worktree root, offline; no physical/live checks.
set -euo pipefail
test -f webjam_qt/windows/recording_setup.py
test -f webjam_qt/controllers/application_controller.py
test -x .venv/bin/python
export QT_QPA_PLATFORM=offscreen
export PYTHONDONTWRITEBYTECODE=1
w20_cache=$(mktemp -d "${TMPDIR:-/tmp}/webjam-w20-recording-setup.XXXXXX")
trap 'rm -rf "$w20_cache"' EXIT
export PYTHONPYCACHEPREFIX="$w20_cache"

.venv/bin/python -m ruff check webjam_qt/ core/ ui/ services/ api/
.venv/bin/python -m ruff check tests/test_recording_format_setup.py tests/test_recording_format_controller.py
.venv/bin/python -m compileall -q core webjam_qt ui services api tests
.venv/bin/python -m pip check
.venv/bin/python ux_smoke_test.py

# Focused W20 regressions (ordinary Save rejected on session/join change;
# live identity-stable Local Originals opt-out remains allowed).
.venv/bin/python -m pytest -p no:cacheprovider tests/test_recording_format_setup.py -q \
  -k 'session_change'
.venv/bin/python -m pytest -p no:cacheprovider tests/test_recording_format_controller.py -q \
  -k 'join_settings_change or guest_owner_change or ordinary_opt_out or stale_endpoint or modal_identity_change or generation_change_after'

# Complete modules for the touched Recording Setup dialog/controller path
# and adjacent Studio/UX owners. Fresh interpreter per module (CI / CONTRIBUTING.md).
w20_status=0
for test_file in \
  tests/test_recording_format_setup.py \
  tests/test_recording_format_controller.py \
  tests/test_recording_setup_compact_recovery.py \
  tests/test_recording_studio.py \
  tests/test_recording_readiness_ui.py \
  tests/test_guest_recording_preflight_guidance.py \
  tests/test_local_originals_choice_profiles.py \
  tests/test_art_start_ux.py
do
  echo "Testing $test_file"
  .venv/bin/python -m pytest -p no:cacheprovider "$test_file" -q || w20_status=1
done

git diff --check
if ((w20_status != 0)); then
  echo 'W20 Recording Setup session-change save gate: FAIL (see module results)'
  exit "$w20_status"
fi
echo 'W20 Recording Setup session-change save gate: PASS'
