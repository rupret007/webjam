# Queue 22 — novice busy-session remedy

## Result

**PASS**

## Commit

- SHA: `3eb3925`
- Branch: `draft/cursor-queue-22-webjam-novice-busy-session-remedy-f9df52cf`
- https://github.com/rupret007/webjam/commit/3eb3925
- https://github.com/rupret007/webjam/tree/draft/cursor-queue-22-webjam-novice-busy-session-remedy-f9df52cf

## Change

Native startup maps bridge `Port in use` to bounded `port_in_use` guidance (close other WebJam window, wait, Try Again). Reinstall copy remains `component_open_failed` for missing Jamulus only.

## Tests (local)

- `python3.11 -m pytest tests/test_jamulus_native_startup.py::test_rejected_launch_with_port_in_use_uses_busy_session_remedy_not_reinstall tests/test_jamulus_native_startup.py::test_startup_poll_maps_terminal_jamulus_state_to_bounded_reason tests/test_jamulus_native_startup.py::test_startup_failure_uses_one_bounded_message_and_action -q` — 14 passed
- `python3.11 -m unittest tests.test_bridge_port_conflict -q` — OK (9 tests)

## Remaining risks

- CI not run in this worktree session; full matrix unverified here.
