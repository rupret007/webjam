# W23 — Guest creator start from host only

Implemented on `webjam-24h/W23-guest-creator-start-from-host-only` in the W23
worktree. Product commit: `05d8055` (`Use only host room starts for guests`).

## Change

- `webjam_qt/controllers/application_controller.py`: a guest resolves
  `creator_start` only from `room.borrowed_start`, against the active profile.
  An empty or unrecognized borrowed start returns `None`. The saved door card
  remains the local/host choice and is never overwritten by joining.
- `webjam_qt/controllers/room_participant.py`: authenticated LAN apply copies
  `state.art_start_key` before refreshing the profile and room. This is the
  minimal W16 dependency identified in W23's backlog; the base branch lacked
  that assignment. Native apply already supplies it. Existing successful
  Leave cleanup clears the borrowed state and role, restoring the saved card.
- `tests/test_art_room_controller.py`: five new regression cases cover both
  saved-card directions over LAN/native, the cold LAN constructor, guest
  authority despite saved Host settings, buffered native receipt before apply,
  an older LAN host with no start, waiting-room actions, no premature video
  opening, and real Leave-worker restoration of the saved card.
- `CHANGELOG.md` and `USER_GUIDE.md`: document guest ownership and saved-card
  restoration. `docs/evidence/w23/` contains this report, the runnable gate,
  and exact per-command results. Copies are provided in the campaign's
  `items/W23/` directory.

## UX check

Read the supplied UX gate, both audits, launch/start-card code and tests, and
the supplied 800×600 Art door and Make together room screenshots. No new UI
copy or controls were needed. While probing, the tested visible room copy is
“Checking the host's room”, with no start-driven activity action. After host
Paint along applies, the room offers “Paint along is starting” and “Open
Paint along”. Make together guests receive no video action from their saved
card. The door keeps “Make together”, “Paint along”, “Host”, and “Join”.
The 55 door tests retain the banned-word and exactly-two-card assertions.
Preview and fail-closed copy are unchanged.

## Validation — 2026-10-10

Use `.venv/bin/python`; Qt tests run offscreen in a fresh interpreter per
module, matching the repository's Python gate. No assertions, skip markers,
or existing tests were changed or removed.

| Check | Exact result |
| --- | --- |
| Original product, initial four regression cases: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_art_room_controller.py -q -k guest_creator_start` | **4 failed, 17 deselected in 2.03s**; every failure showed a saved card where `None` was required |
| Final focused selection, same command (five cases) | **5 passed, 17 deselected in 1.39s** |
| Complete room-controller module, independent focused run | **22 passed in 2.24s** |
| Door UX module in final gate | **55 passed in 1.02s** |
| `bash docs/evidence/w23/gate.sh` | **FAIL, exit 1**; 102 complete relevant modules: **3387 passed, 11 subtests passed, 14 skipped, 5 failed, 30 errors**; totals exclude the repeated focused selection |
| Final gate duration, log creation through final write | **257.04 seconds**, below 25 minutes |
| Ruff: standard product directories and modified test file | **PASS** |
| Standard `compileall`, `pip check`, runtime dependency policy | **PASS**; pip reported no broken requirements |
| `ux_smoke_test.py`, `git diff --check HEAD`, gate Bash syntax | **PASS** |

The final gate runs all selected modules and exits nonzero if any command
fails. Its exact per-command output summaries are in
[`gate-results.txt`](gate-results.txt). The initial fail-fast gate stopped at
the four LAN portable failures; the final gate collected the remaining
results without suppressing those failures.

All five failures and all 30 setup errors are localhost socket restrictions:

| Module | Result | Cause |
| --- | --- | --- |
| `test_art_lan_host_portable.py` | 4 failed | Host listener startup: `PermissionError` |
| `test_art_lesson_request_journey.py` | 17 errors | Socket bind: `PermissionError: [Errno 1] Operation not permitted` |
| `test_art_room_connection_facts.py` | 1 failed, 25 passed | Socket bind: same permission error |
| `test_art_room_connection_names.py` | 6 passed, 13 errors | Socket bind: same permission error |

A separate minimal Python `socket.socket().bind(("127.0.0.1", 0))` also
failed with `PermissionError: [Errno 1] Operation not permitted` (exit 1).
This sandbox does not allow permission escalation, so the gate remains FAIL;
it must be rerun in an environment permitting local sockets. No test was
disabled to obtain a green result. The 14 skips are existing opt-in macOS
Swift integration markers (11 HTTP-policy tests and 3 Swift integration
tests), not new skips or gate exclusions.

## NOT RUN

- Physical audio, real decoder/video playback, two-Mac/LAN reachability,
  Webex, camera/share checks, and human UX feel: **NOT RUN**.
- Opt-in Swift/device integrations: **NOT RUN** (existing skips above).
- Unrelated repository test modules outside the 102-module touched-area
  gate: **NOT RUN**.
- Packaged builds, signing, publishing, pushes, merges, PRs, and release
  changes: **NOT RUN**.

No large build outputs were created. Disk check after testing showed 15 GiB
available, above the campaign's 10 GB floor.
