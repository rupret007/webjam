# W24 — Start timeout copy not stop failure

Implemented on `webjam-24h/W24-start-timeout-copy-not-stop-failure`.

## Change and UX

An ambiguous start timeout now explains the attempted start independently of
the conservative possibly-recording flag. The visible dialog body reads
**“WebJam couldn't confirm that recording started.”** Its Details heading is
**“Recording Start Unconfirmed”** instead of **“Recording Could Not Stop.”**
The shared dialog's window title remains **“Something needs attention.”**

Recovery is unchanged: the recorder remains possibly armed, the phase remains
`STOP_FAILED`, local capture is preserved, and the retry requests Stop. The
existing guidance remains **“The server may still be recording. Try Stop Again
now.”** The strip still offers **“■ Finish Stop”**. Actual stop failures retain
their stop-specific explanation. No new Start is offered on an ambiguous reply.

Read the campaign UX gate, launch copy, creator modes, and Art start tests.
Art's **“Make together”** / **“Paint along”** and **“Host”** / **“Join”** doors
are unchanged; the banned-word and extra-door tests pass. Preview and fail-closed
decisions are unchanged. Maker-tool reference checked before copy selection:
[Audacity's recording manual](https://manual.audacityteam.org/man/recording.html),
which distinguishes starting recording from explicitly stopping it. WebJam's
uncertainty wording reflects its own unconfirmed server response.

## Files

- `webjam_qt/controllers/recording_coordinator.py`: choose ambiguous-start
  explanation before interpreting the recovery flag as a stop failure.
- `tests/test_server_rpc_and_record_button.py`: strengthen the ambiguous-start
  regression to check title/body, preserved capture, and the actual retry's
  stop target; assert unchanged actual-stop copy.
- `CHANGELOG.md`, `RECORDING_AND_STUDIO.md`: document the correction and recovery.
- `docs/evidence/w24/gate.sh`, `docs/evidence/w24/impl.md`: committed evidence.
  Matching copies are provided at the requested campaign `items/W24/` paths.

## Tests and exact results

Commands used `.venv/bin/python`, `QT_QPA_PLATFORM=offscreen`,
`PYTHONDONTWRITEBYTECODE=1`, and pytest `-q -p no:cacheprovider`.
Every full test module ran in a fresh interpreter, as CONTRIBUTING.md and CI
require. No test or gate was skipped, weakened, or retried to obtain a pass.

Focused selector in `gate.sh`:
`ambiguous_start_failure_preserves_capture_and_retries_stop or stop_failure_stays_visibly_recording_and_retries_stop or worker_failure_offers_actionable_retry`.

- Before product change: **1 failed, 2 passed, 137 deselected in 1.18s**.
  The failure was the incorrect `Recording Could Not Stop` heading.
- After product change: **3 passed, 137 deselected in 1.21s**.
- Gate invocation: focused tests **3 passed, 137 deselected in 1.04s**;
  full RPC/record-button module **7 failed, 133 passed, 13 subtests passed in
  1.93s**. Exit **1**, because this sandbox denies `bind(("127.0.0.1", 0))`
  with `PermissionError: [Errno 1] Operation not permitted` in the fake RPC
  server's setup. All seven failures are `TestJamulusServerRpc` tests:
  `test_call_timeout_raises`, `test_call_without_connect_raises`,
  `test_error_response_raises`, `test_full_record_cycle`, `test_get_clients`,
  `test_unreachable_port_covers_same_mac_and_remote_server`, and
  `test_wrong_secret_raises_actionable_error`.

The gate stops on failure. Its remaining commands were run independently:

| Module/check | Exact result |
| --- | --- |
| `test_recording_action_recovery.py` | 11 passed in 2.59s |
| `test_recording_studio.py` | 134 passed in 6.39s |
| `test_recording_studio_shutdown_retry.py` | 29 passed in 2.39s |
| `test_recording_recovery_navigation.py` | 5 passed in 1.67s |
| `test_art_start_ux.py` | 55 passed in 1.47s |
| `ux_smoke_test.py` | UX smoke gate passed; exit 0 |
| compileall: `core webjam_qt ui services api tests` | PASS; exit 0; temporary bytecode removed |
| `.venv/bin/python -m pip check` | No broken requirements found; exit 0 |
| `.venv/bin/ruff check webjam_qt/ core/ ui/ services/ api/` | All checks passed; exit 0 |
| `bash -n docs/evidence/w24/gate.sh` | exit 0 |
| `git diff --check` | exit 0 |

Full-module total: **367 passed, 7 failed, 13 subtests passed**. Focused reruns
overlap that total. The complete gate is **FAILED in this sandbox**, not PASS.
The offline gate retains all seven RPC tests for execution where loopback
binding is permitted. It requires no downloads or physical devices and is
scoped to fit the requested 25-minute limit; this sandbox run stopped in seconds.

Raw results: campaign `items/W24/gate-results.txt` and the five
`items/W24/test_*.txt` files. The final gate also includes compileall, run
independently after adding it. Disk remained at 11 GiB available; no build
outputs were created.

## NOT RUN

- Successful loopback RPC test execution (sandbox binding restriction).
- Full repository test suite beyond the listed relevant modules.
- Real recording/audio capture or playback, two-Mac recovery, Webex talk/share.
- Physical visual/accessibility checks, packaged applications, live services.
- New before/after screenshots: the supplied before directory contains general
  door/room captures, not the timeout dialog; copy and recovery were verified
  through automated controller/UI tests.

Only W24 was changed. No push, merge, PR, tag, release, publication, or Latest
change was performed.
