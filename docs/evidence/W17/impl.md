# W17 — Honor recorder stop when the start notice was missed

Implemented in `/Users/jeffstory/Documents/webjam-24h/W17` on
`webjam-24h/W17-honor-recorder-stop-when-the-start-notic`.

## What changed

`RecordingCoordinator.on_server_state(False)` now processes a stop for an active
take with confirmed start evidence and no stop evidence, even when the cached
server-notification flag is already false. The normal stop path records
`recording_stopped_unexpectedly`, marks recovery `NEEDS_ATTENTION`, clears armed
state, notifies peers of finalization, and starts validation once. Stop evidence
then makes repeated false notices idempotent. Idle and still-unconfirmed start
notices retain their existing behavior. RPC confirmation does not fabricate a
true server notification. W18's late start-RPC ordering is outside this change.

## Files

- `webjam_qt/controllers/recording_coordinator.py`: take-evidence-aware notification deduplication.
- `tests/test_server_rpc_and_record_button.py`: three new tests beside the authoritative-stop tests.
- `docs/evidence/W17/gate.sh`: committed copy of the requested offline gate.
- `docs/evidence/W17/impl.md`: committed copy of this report.
- Campaign `items/W17/gate.sh` and `items/W17/impl.md`: requested outputs.

The existing campaign files described an earlier, unrelated W17 guest-backend
item. Their contents were preserved in `prior-guest-backend-gate.sh` and
`prior-guest-backend-impl.md` in the campaign item directory. All 33 pre-existing
gate test modules remain in the new gate; no test or gate was weakened.

## UX gate

Read the supplied `UX_GATE.md`, both audits, W17 details, launch copy,
`core/creative_modes.py`, and start-card tests. The repaired sequence shows
**“Finalizing…”**, disables Record, stops the recording clock, and flashes
**“Server recording stopped.”** The test checks these actual UI states and copy.
The evidence remains fail-closed as `NEEDS_ATTENTION`; finalization is not a
claim that the take passed validation. No new copy was needed.

The door contract remains Music **“Host” / “Join”**, Art **“Make together” /
“Paint along”**, then **“Host” / “Join”**. The 66 start-card/door tests passed,
including banned-word harvesting. Preview and NOT RUN wording was unchanged.

## Tests and exact results

All commands used `.venv/bin/python`, with `QT_QPA_PLATFORM=offscreen` for Qt.

Before changing product code:

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_server_rpc_and_record_button.py -q \
  -k 'missed_start_notice or false_idle_notice or false_notice_before_confirmed_start'
```

**1 failed, 2 passed, 140 deselected in 1.73s** (exit 1). The missed-start
regression failed with `Expected '_begin_take_validation' to be called once.
Called 0 times.` This reproduced W17 before the fix.

After the fix, the same command: **3 passed, 140 deselected in 1.25s** (exit 0).
The gate rerun also passed: **3 passed, 140 deselected in 1.42s**.

The gate was invoked from the worktree root:

```bash
bash /Users/jeffstory/Documents/bob-overnight-inject/local-webjam-24h-1010/items/W17/gate.sh
```

**Gate exit 1, not green:** the full `test_server_rpc_and_record_button.py`
module returned **7 failed, 136 passed, 13 subtests passed in 2.29s**. All seven
failures are `TestJamulusServerRpc` fixtures denied localhost socket binding by
the sandbox (`PermissionError: [Errno 1] Operation not permitted` at
`_srv.bind(("127.0.0.1", 0))`). They are: `test_call_timeout_raises`,
`test_call_without_connect_raises`, `test_error_response_raises`,
`test_full_record_cycle`, `test_get_clients`,
`test_unreachable_port_covers_same_mac_and_remote_server`, and
`test_wrong_secret_raises_actionable_error`. No skip, xfail, socket mock, or gate
exclusion was added to hide these failures.

Passed standard checks: `-m ruff check webjam_qt/ core/ ui/ services/ api/`
(`All checks passed!`), `-m compileall -q core webjam_qt ui services api tests`
(exit 0), `-m pip check` (`No broken requirements found.`), and
`ux_smoke_test.py` (`UX smoke gate passed.`). The final gate also includes
`tools/runtime_dependency_policy.py --check`; that command was added after the
first gate attempt and run separately (exit 0). `bash -n` on both gate copies
and `git diff --check` passed.

Because the gate stops on failure, its remaining 41 modules were each run
separately with `.venv/bin/python -m pytest "$test_file" -q` in fresh processes,
matching CI's Qt lifetime isolation. **836 passed; all 41 processes exited 0**.
Together with the recorder module: **972 passed, 7 sandbox socket failures,
13 subtests passed** (focused reruns not double-counted).

| Remaining module | Exact pytest result |
| --- | --- |
| `tests/test_recorder_and_stage_cards.py` | 15 passed in 1.23s |
| `tests/test_recording_action_recovery.py` | 11 passed in 2.51s |
| `tests/test_recording_recovery_navigation.py` | 5 passed in 1.90s |
| `tests/test_recording_studio_shutdown_retry.py` | 29 passed in 2.11s |
| `tests/test_recording_studio.py` | 134 passed in 6.45s |
| `tests/test_participant_recording_state.py` | 3 passed in 0.44s |
| `tests/test_recording_manifest_journal.py` | 21 passed in 0.22s |
| `tests/test_session_health.py` | 2 passed in 0.06s |
| `tests/test_remote_session_runtime.py` | 34 passed in 0.29s |
| `tests/test_native_remote_transport.py` | 56 passed in 1.41s |
| `tests/test_transport_runtime.py` | 31 passed in 3.44s |
| `tests/test_remote_invitation_owner.py` | 8 passed in 0.13s |
| `tests/test_remote_redaction.py` | 6 passed in 0.09s |
| `tests/test_remote_launch_ingress.py` | 10 passed in 0.98s |
| `tests/test_controller_remote_transport.py` | 31 passed in 2.33s |
| `tests/test_controller_room_help.py` | 3 passed in 0.90s |
| `tests/test_room_help_controller.py` | 51 passed in 0.56s |
| `tests/test_art_native_retry.py` | 3 passed in 1.00s |
| `tests/test_native_admission_deferred_profile.py` | 2 passed in 2.12s |
| `tests/test_art_pending_profile_facts.py` | 17 passed in 3.59s |
| `tests/test_native_art_activities.py` | 12 passed in 2.91s |
| `tests/test_native_art_room_return.py` | 11 passed in 3.91s |
| `tests/test_art_room_controller.py` | 17 passed in 1.89s |
| `tests/test_art_room_recovery.py` | 6 passed in 1.10s |
| `tests/test_art_room_overview_controller.py` | 6 passed in 1.32s |
| `tests/test_art_cold_invitation_context.py` | 23 passed in 3.28s |
| `tests/test_art_canvas_connection_journey.py` | 15 passed in 2.30s |
| `tests/test_canvas_publication_journey.py` | 8 passed in 1.97s |
| `tests/test_invitation_conversation_lifetime.py` | 9 passed in 1.29s |
| `tests/test_art_conversation_link_journey.py` | 18 passed in 5.01s |
| `tests/test_art_notes_conversation_journey.py` | 18 passed in 5.87s |
| `tests/test_art_profile_guidance_journey.py` | 14 passed in 3.84s |
| `tests/test_art_shared_lesson_native_host.py` | 6 passed in 1.40s |
| `tests/test_art_exit_next_action.py` | 27 passed in 8.38s |
| `tests/test_art_lan_host_recovery.py` | 22 passed in 5.78s |
| `tests/test_music_lan_host_recovery.py` | 29 passed in 6.98s |
| `tests/test_two_session_art_integration.py` | 4 passed in 2.86s |
| `tests/test_two_session_art_host_recovery.py` | 6 passed in 1.82s |
| `tests/test_two_session_music_host_recovery.py` | 8 passed in 2.28s |
| `tests/test_art_start_ux.py` | 66 passed in 1.14s |
| `tests/test_creative_modes_edge.py` | 39 passed in 0.09s |

Logs in the campaign item directory: `recorder-stop-gate.log` and
`recorder-stop-remaining-tests.log`. The gate performs no downloads or native
builds, retains full modules, and uses `.venv/bin/python` throughout. The
remaining modules reported 97.18 seconds of pytest time in total.

## NOT RUN / limits

- Real audio capture/playback, physical audio devices, two-Mac sessions, Webex,
  packaged-app interaction, and live recorder verification: **NOT RUN**.
- New screenshots or a manual visual/keyboard pass: **NOT RUN**; this change is
  verified by offscreen state/copy assertions and the existing UX tests.
- Entire repository Python suite, native integration, online dependency audits:
  **NOT RUN**. The standard gate for the touched areas and preserved campaign
  modules was attempted as described above.
- Successful execution of the seven localhost RPC tests: **NOT RUN**; their
  attempts failed at sandbox socket setup. The gate retains them for a runner
  that permits localhost sockets.

No push, merge, PR, tag, release, publish, or Latest change. No paid APIs or
messages sent. No large build output created; final disk check showed 18 GiB
available.
