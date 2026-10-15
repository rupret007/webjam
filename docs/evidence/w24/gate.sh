#!/bin/bash
# Run from the W24 worktree root. Offline; each Qt module gets a fresh process.
set -euo pipefail
export QT_QPA_PLATFORM=offscreen
export PYTHONDONTWRITEBYTECODE=1

test -x .venv/bin/python
.venv/bin/python -m pytest tests/test_server_rpc_and_record_button.py \
  -q -p no:cacheprovider \
  -k 'ambiguous_start_failure_preserves_capture_and_retries_stop or stop_failure_stays_visibly_recording_and_retries_stop or worker_failure_offers_actionable_retry'

# CONTRIBUTING.md / CI's standard Python gate, scoped to recording recovery
# and its UI, plus the campaign's mandatory start-screen UX checks.
for test_file in \
  tests/test_server_rpc_and_record_button.py \
  tests/test_recording_action_recovery.py \
  tests/test_recording_studio.py \
  tests/test_recording_studio_shutdown_retry.py \
  tests/test_recording_recovery_navigation.py \
  tests/test_art_start_ux.py
do
  printf '\nTesting %s\n' "$test_file"
  .venv/bin/python -m pytest "$test_file" -q -p no:cacheprovider
done

.venv/bin/python ux_smoke_test.py
.venv/bin/python - <<'PY'
import compileall
import sys
from tempfile import TemporaryDirectory

with TemporaryDirectory(prefix="webjam-w24-pycache-") as cache:
    sys.pycache_prefix = cache
    results = [
        compileall.compile_dir(area, quiet=1)
        for area in ("core", "webjam_qt", "ui", "services", "api", "tests")
    ]
    if not all(results):
        raise SystemExit(1)
PY
.venv/bin/python -m pip check
.venv/bin/ruff check webjam_qt/ core/ ui/ services/ api/
git diff --check
