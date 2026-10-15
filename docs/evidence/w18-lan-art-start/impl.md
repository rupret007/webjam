# W18 — LAN guest inherits host Art start

Implemented on `webjam-24h/W18-lan-guest-inherits-host-art-start-key` in the assigned worktree. No push, merge, PR, publication, or release changes.

## Change

`RoomParticipantController.receive_lan()` now copies the validated Art snapshot's `art_start_key` to `borrowed_start` before observing creative state and refreshing readiness. This matches native guest behavior and reuses the existing UI. Existing owner, generation, blocked-state and rejected-invitation guards precede the assignment. Later receipts replace the borrowed start; leaving clears it. Personal start/profile preferences are unchanged.

Files:
- `webjam_qt/controllers/room_participant.py`: one assignment in accepted LAN Art state handling.
- `tests/test_lan_art_start.py`: five regression cases using the real controller and offscreen widgets with a fake enrolled LAN peer. Covers Music/Art starting profiles, 800×600/1280×800, no shared video, visible/enabled/accessibly named action, saved preferences, host updates including empty legacy start, wrong owner/generation, and leave/late receipts.
- `USER_GUIDE.md` and `CHANGELOG.md`: document the corrected guest behavior.
- `docs/evidence/w18-lan-art-start/gate.sh` and this report: committed evidence; identical copies delivered to the requested campaign item directory.

## UX review

Read campaign `UX_GATE.md`, W18 backlog detail and UX/logic audits; inspected the supplied before screenshot and the after screenshots. Read launch copy, start registry, and start-card tests. No new copy or controls are introduced in the product.

Guests now see **“Paint along is starting”**, **“The host chose Paint along. Open the panel to see their video choice.”**, and **“Open Paint along”** before video share. The button is visible and enabled at both sizes. Art retains **“Make together”** / **“Paint along”**, then **“Host”** / **“Join”**. The 65 Art start UX tests pass, including banned first-screen words, two-card limits, and face-mark assertions. Existing in-room Preview and fail-closed video behavior remain intact.

Offscreen screenshots (fake peer, not a physical LAN session), visually reviewed:
- `shots/W18/after/lan-guest-paint-along-800x600.png`
- `shots/W18/after/lan-guest-paint-along-1280x800.png`

Paths above are relative to `/Users/jeffstory/Documents/bob-overnight-inject/local-webjam-24h-1010`.

## Executed validation — 2026-10-12

All pytest invocations used `.venv/bin/python`, `QT_QPA_PLATFORM=offscreen`, and fresh interpreters per module. No tests were excluded or loosened, no skip markers were added, and no failures were retried to conceal them. Existing platform/opt-in skips in the full suite are reported below.

1. Before product change: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_lan_art_start.py -q --tb=short` — **5 failed in 1.26s**. Four cases showed “Make from your own space” instead of “Paint along is starting”; the update case showed empty `borrowed_start` instead of `talk_and_make`.
2. After product change, same command — **5 passed in 1.25s**.
3. `bash docs/evidence/w18-lan-art-start/gate.sh` — **exit 1**, approximately 20 seconds. Focused tests, repository-wide production Ruff, compileall, pip check (“No broken requirements found”), UX smoke gate, and the first eight surrounding test modules pass. The next module reports **25 passed, 1 failed** because this sandbox denies localhost socket bind (`PermissionError: [Errno 1] Operation not permitted`). Gate remains fail-fast and retains that test.
4. All 18 remaining modules listed in the gate were then run individually with `.venv/bin/python -m pytest "$test_file" -q --tb=short`, without filtering. **351 passed, 3 failed, 1 setup error**, approximately 45 seconds. The failures/error are also localhost bind denials. Combined with the gate run: **629 passed, 4 failed, 1 setup error** across its 28 complete modules. This is not a green gate.
5. Repository standard full tracked-module loop from `CONTRIBUTING.md`, with `--tb=short` added only for output, was attempted separately: **821 passed, 11 subtests passed, 14 existing skips, 4 failed**, approximately 58 seconds; exit 1 at `tests/test_art_lan_host_portable.py`. All four failures report host listener `PermissionError`. Remaining tracked modules after that failure: **NOT RUN** by the fail-fast loop. These counts overlap the focused-area run and are not additional unique coverage.
6. `.venv/bin/ruff check tests/test_lan_art_start.py` and `git diff --check` — **PASS**.
7. Screenshot capture repeated the new module using a temporary pytest hook with the existing isolated fixtures: **5 passed in 1.00s**; both PNGs saved and inspected.

Gate and supplemental module results:

| Module | Exact result |
| --- | --- |
| `tests/test_lan_art_start.py` | 5 passed in 1.29s |
| `tests/test_art_start_ux.py` | 65 passed in 3.06s |
| `tests/test_art_room_controller.py` | 17 passed in 1.82s |
| `tests/test_art_room_overview_controller.py` | 6 passed in 1.13s |
| `tests/test_art_room_presence.py` | 52 passed in 0.55s |
| `tests/test_art_room_overview.py` | 29 passed in 0.13s |
| `tests/test_art_room_overview_ui.py` | 59 passed in 5.05s |
| `tests/test_art_room_contract.py` | 14 passed in 0.12s |
| `tests/test_art_room_recovery.py` | 6 passed in 1.04s |
| `tests/test_art_room_connection_facts.py` | 1 failed, 25 passed in 0.27s |
| `tests/test_art_pending_profile_facts.py` | 17 passed in 3.02s |
| `tests/test_art_lan_retry.py` | 35 passed in 4.31s |
| `tests/test_art_exit_next_action.py` | 27 passed in 8.52s |
| `tests/test_art_paint_along_connection_journey.py` | 21 passed in 2.89s |
| `tests/test_art_video_offer_navigation.py` | 25 passed in 6.93s |
| `tests/test_two_session_art_integration.py` | 4 passed in 1.81s |
| `tests/test_composed_invitation_music_mix.py` | 4 passed in 1.53s |
| `tests/test_rejected_invitation_journey.py` | 10 passed in 3.84s |
| `tests/test_invitation_conversation_lifetime.py` | 9 passed in 1.35s |
| `tests/test_lan_invitation_rejection.py` | 13 passed in 0.45s |
| `tests/test_music_lan_guest_handoff_recovery.py` | 22 passed in 1.98s |
| `tests/test_follow_along_reconnect.py` | 1 warning, 1 error in 1.26s |
| `tests/test_session_transfer_art_start.py` | 3 failed, 20 passed in 0.39s |
| `tests/test_room_state.py` | 30 passed in 0.12s |
| `tests/test_reference_video_coordinator.py` | 25 passed in 0.47s |
| `tests/test_reference_video_application_integration.py` | 37 passed in 0.68s |
| `tests/test_shared_canvas_coordinator.py` | 25 passed in 0.41s |
| `tests/test_room_clock_coordinator.py` | 27 passed in 0.45s |

Sandbox-blocked checks:
- `test_art_room_connection_facts.py::test_host_counts_only_fresh_authenticated_room_readers`: localhost bind denied.
- `test_follow_along_reconnect.py::test_guest_rejoin_gets_fresh_admission_without_stale_acknowledgement`: setup localhost bind denied.
- `test_session_transfer_art_start.py::test_authenticated_lan_guest_reads_but_cannot_choose_the_host_start`: all three start-key variants hit localhost bind denial.
- Full standard loop: all four `test_art_lan_host_portable.py` cases cannot start the host listener in this sandbox.

Exploratory harness corrections: the first authored test draft incorrectly expected `reference_video is None`; snapshots actually carry an idle, unshared video object. That draft returned 5 failures in 1.80s; corrected assertions verify no share/no identity before the meaningful pre-fix run above. An initial standalone screenshot probe lacked pytest database isolation and failed with SQLite “unable to open database file”; screenshots were subsequently captured through the isolated pytest fixtures. No product change was made for either harness issue.

## NOT RUN and limits

Real audio, real video playback/codecs, two-Mac LAN, physical device checks, Webex, and live network sessions: **NOT RUN**. The fake-peer regression verifies propagation from the accepted snapshot callback and rendered guidance; it does not certify live transport or playback. Successful localhost integration execution is blocked by this environment and must be rerun with the unchanged gate where local socket binding is permitted.

No paid APIs, sends, external network requests, or unrelated process changes. No builds created. Disk check: **25 GiB available**, above the 10 GB floor.
