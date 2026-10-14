# W17 — Fence guest backend start on Stop

Implemented on `webjam-24h/W17-fence-guest-backend-start-on-stop`, based on
`220eb52`. Only W17 is addressed.

## What changed

Stop at “Securing connection” now prevents the retired guest worker from starting
its backend. The runtime rechecks ownership after publishing the stage callback.
The native adapter also rejects retired generations under its process ownership
lock, covering a Stop that wins after the runtime check but before installation.
No runtime condition is held across native startup or process shutdown.

The adapter's `stop_guest(generation=...)` retires pending launches even when no
process exists. It stops only the matching generation's process. Runtime Stop,
timeout cleanup, and late worker cleanup use that scoped operation. Process
ownership remains retained when shutdown fails, so cleanup can be retried.
The existing direct `stop()` interface is preserved; production runtime generations
advance across attempts. Legacy test backends retain their `stop()` interface.

Late workers release only their own pending invitation reference and clean up
through their original backend. They cannot stop a newer generation. Timeout
remains `TIMED_OUT` and requires a fresh invitation; uncertain enrollment still
uses the existing conservative invitation policy.

## Files

- `services/remote_session_runtime.py`: callback recheck, generation-scoped Stop,
  timeout cleanup, and late completion cleanup.
- `services/native_remote_transport.py`: retired-generation installation fence,
  exact process-generation ownership, and scoped shutdown.
- `tests/test_remote_session_runtime.py`: synchronous Stop callback and scoped
  stale-resource cleanup regressions.
- `tests/test_native_remote_transport.py`: native adapter with only the process
  replaced by `FakeProcess`; synchronous Stop, deterministic pre-install barriers
  for Stop/timeout, successful next join, and replacement-resource protection.
- `tools/gates/w17_guest_stop.sh`: committed offline gate, also copied verbatim to
  the requested campaign `items/W17/gate.sh`.
- `docs/notes/W17-guest-stop.md`: this implementation/test record, also written to
  the requested campaign `items/W17/impl.md`.

## UX gate

Read the campaign `UX_GATE.md`, launch copy in
`webjam_qt/windows/launch_dialog.py`, `core/creative_modes.py`, and the start-card
checks. This race requires an ownership fix; copy alone cannot stop the process.
The existing visible sequence is “Securing connection”, “Ending the secure
connection”, then “Audio session ended”. The fix makes the final state truthful.

Art still offers exactly “Make together” and “Paint along”, then “Host” / “Join”.
Music retains “Host” / “Join”. No first-screen copy, Preview labels, room choices,
silent-video behavior, or fail-closed recovery text changed. The UX smoke gate
and all 66 `test_art_start_ux.py` cases passed, including banned-word checks.

## Tests and exact results — 2026-10-12

Before the product change, the first focused run reproduced the two synchronous
Stop failures and the pre-install native process leak: **3 failed, 2 passed,
90 deselected in 0.59s**, exit 1.

After all eight regression cases were added, loading the two original product
modules from `git show 220eb52:<path>` into an isolated Python interpreter and
running the current regression tests produced **5 failed, 3 passed, 90 deselected
in 0.19s**, exit 1. The failures include stale resource cleanup and timeout before
native installation. This baseline check did not modify the worktree files.
Log: campaign `items/W17/regression-before-current.log`.

The same focused selector against the changed product passed:

```sh
.venv/bin/python -m pytest -q tests/test_remote_session_runtime.py tests/test_native_remote_transport.py -k 'stop_at_securing or stop_fences_native_install or late_native_result_cleanup or retired_worker_cleans'
```

Result: **8 passed, 90 deselected in 0.28s**, exit 0.

The offline gate was run from the worktree root:

```sh
bash tools/gates/w17_guest_stop.sh
```

Result: **exit 0; 624 passed across 33 modules; no skipped/xfail cases**.
The gate completed in approximately 90 seconds (well below 25 minutes).
Log: campaign `items/W17/gate-current.log`.

Standard repository checks:

- `.venv/bin/python -m ruff check webjam_qt/ core/ ui/ services/ api/`:
  `All checks passed!`
- `.venv/bin/python -m compileall -q core webjam_qt ui services api tests`:
  exit 0, no output.
- `.venv/bin/python -m pip check`: `No broken requirements found.`
  Pip also reported its existing unwritable user cache and disabled that cache.
- `.venv/bin/python ux_smoke_test.py`: `UX smoke gate passed.`
- Ruff on both modified test files: `All checks passed!`
- `git diff --check`, `bash -n tools/gates/w17_guest_stop.sh`, and comparison of
  the committed/external gate scripts: exit 0.

Each test module ran in a fresh interpreter with `QT_QPA_PLATFORM=offscreen`,
following the repository's standard Python/Qt gate policy for the touched areas.

| Module | Passed | Seconds |
| --- | ---: | ---: |
| `tests/test_remote_session_runtime.py` | 36 | 0.26 |
| `tests/test_native_remote_transport.py` | 62 | 1.34 |
| `tests/test_transport_runtime.py` | 31 | 3.22 |
| `tests/test_remote_invitation_owner.py` | 8 | 0.12 |
| `tests/test_remote_redaction.py` | 6 | 0.08 |
| `tests/test_remote_launch_ingress.py` | 10 | 1.39 |
| `tests/test_controller_remote_transport.py` | 31 | 2.18 |
| `tests/test_controller_room_help.py` | 3 | 0.87 |
| `tests/test_room_help_controller.py` | 51 | 0.51 |
| `tests/test_art_native_retry.py` | 3 | 0.95 |
| `tests/test_native_admission_deferred_profile.py` | 2 | 2.01 |
| `tests/test_art_pending_profile_facts.py` | 17 | 3.00 |
| `tests/test_native_art_activities.py` | 12 | 2.08 |
| `tests/test_native_art_room_return.py` | 11 | 3.61 |
| `tests/test_art_room_controller.py` | 17 | 1.74 |
| `tests/test_art_room_recovery.py` | 6 | 1.08 |
| `tests/test_art_room_overview_controller.py` | 6 | 1.16 |
| `tests/test_art_cold_invitation_context.py` | 23 | 6.15 |
| `tests/test_art_canvas_connection_journey.py` | 15 | 2.40 |
| `tests/test_canvas_publication_journey.py` | 8 | 2.11 |
| `tests/test_invitation_conversation_lifetime.py` | 9 | 1.33 |
| `tests/test_art_conversation_link_journey.py` | 18 | 5.06 |
| `tests/test_art_notes_conversation_journey.py` | 18 | 5.27 |
| `tests/test_art_profile_guidance_journey.py` | 14 | 3.57 |
| `tests/test_art_shared_lesson_native_host.py` | 6 | 1.23 |
| `tests/test_art_exit_next_action.py` | 27 | 7.83 |
| `tests/test_art_lan_host_recovery.py` | 22 | 5.07 |
| `tests/test_music_lan_host_recovery.py` | 29 | 6.24 |
| `tests/test_two_session_art_integration.py` | 4 | 1.52 |
| `tests/test_two_session_art_host_recovery.py` | 6 | 1.51 |
| `tests/test_two_session_music_host_recovery.py` | 8 | 2.00 |
| `tests/test_art_start_ux.py` | 66 | 1.15 |
| `tests/test_creative_modes_edge.py` | 39 | 0.10 |

## NOT RUN

- Full repository test suite outside the 33 relevant modules: NOT RUN.
- Real/native transport sidecar integration and live remote enrollment: NOT RUN.
- Physical audio, two-Mac connections, Webex, and invitation consumption at a
  live service: NOT RUN. Native adapter regressions use `FakeProcess`; the
  transport-runtime suite uses its existing synthetic subprocess fixtures.
- New before/after screenshots and human feel review: NOT RUN; this changes
  lifecycle behavior without changing layouts or copy. Existing campaign
  screenshots were not regenerated.
- Packaging, signing, notarization, release, push, merge, and PR creation: NOT RUN.

No external networking, paid APIs, sends, or native builds were used. Available
space remained about 22 GiB; no large build outputs were created. Jeff retains
merge and feel decisions.
