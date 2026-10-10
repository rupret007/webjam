#!/usr/bin/env bash
# Run offline from the worktree root. Fresh interpreter per test module,
# matching CONTRIBUTING.md and CI; no retries or test exclusions.
# Collect every result, but any failed command makes the gate fail.
set -euo pipefail
export QT_QPA_PLATFORM=offscreen
export PIP_NO_INDEX=1
export PIP_DISABLE_PIP_VERSION_CHECK=1
test -x .venv/bin/python
test -f webjam_qt/controllers/application_controller.py
status=0
run() {
  echo "RUN: $*"
  if "$@"; then
    echo "RESULT: PASS"
  else
    result=$?
    echo "RESULT: FAIL ($result)"
    status=1
  fi
}

run .venv/bin/python -m pytest tests/test_art_room_controller.py -q -k guest_creator_start
run .venv/bin/ruff check webjam_qt/ core/ ui/ services/ api/
run .venv/bin/ruff check tests/test_art_room_controller.py
run .venv/bin/python -m compileall -q core webjam_qt ui services api tests
run .venv/bin/python -m pip check
run .venv/bin/python tools/runtime_dependency_policy.py --check
run .venv/bin/python ux_smoke_test.py
run git diff --check HEAD

# Standard Python gate for Art, start/profile selection, room ownership,
# controller integration, canvas, and reference-video consumers.
while IFS= read -r test_file; do
  run .venv/bin/python -m pytest "$test_file" -q
done < <(git ls-files \
  'tests/test_art*.py' 'tests/test_application_controller*.py' \
  'tests/test_controller_remote*.py' 'tests/test_native_art*.py' \
  'tests/test_native_canvas*.py' 'tests/test_creator*.py' \
  'tests/test_creative*.py' 'tests/test_paint*.py' \
  'tests/test_reference_video*.py' 'tests/test_room*.py' \
  'tests/test_lan_room*.py' 'tests/test_two_session_art*.py' \
  'tests/test_shared_canvas*.py')
if ((status == 0)); then
  echo 'W23 gate: PASS'
else
  echo 'W23 gate: FAIL (see command results above)'
fi
exit "$status"
