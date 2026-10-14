# W22 — Defer LAN CONNECTED until profile adoption

Implemented on `webjam-24h/W22-defer-lan-connected-until-profile-adopti`.
Product/test commit: `77344d9` (`Wait for profile adoption before LAN room admission`).
Local commits only; no push, merge, PR, release, tag, or publication.

## What changed

- LAN Art admission now requires `_apply_creator_profile_key(..., host_owned=True)` to succeed before clearing probing, projecting creative state, or marking CONNECTED.
- LAN Music discovery keeps its observer and invitation while profile adoption is deferred. It cannot construct the recording peer or begin audio startup early.
- A current receipt retries after 200 ms, for at most five seconds of receipt age. Fresh network receipts may resume admission after expiry. Replacement receipts, loss, Stop, generation changes, shutdown, and terminal observer reasons invalidate deferred work.
- Music handoff preserves the successfully adopted host profile through peer cleanup, avoiding a second switch through the personal profile. Ordinary Leave still restores the personal profile. Unconfirmed discovery cleanup retains the existing cleanup/recovery behavior.
- Existing recovery tests now assert the exact profile-preserving handoff argument; no assertions were removed or loosened.

## Files

- `webjam_qt/controllers/room_participant.py`: receipt-bound admission, guarded retry, loss/Stop invalidation, Music handoff ordering.
- `webjam_qt/controllers/application_controller.py`: explicit preservation of the adopted profile during discovery-to-recording handoff only.
- `tests/test_lan_admission_deferred_profile.py`: 16 new cases covering both profile directions, the actual Library media guard, one visible profile transition, no early audio, retry expiry, superseding receipts, and stale/lost/stopped/replaced owners.
- `tests/test_art_lan_retry.py`, `tests/test_music_lan_guest_handoff_recovery.py`: retain strict ownership assertions and require profile preservation.
- `CHANGELOG.md`, `USER_GUIDE.md`: describe the Unreleased behavior.
- `docs/evidence/W22/gate.sh`, `docs/evidence/W22/impl.md`: committed copies of the requested campaign artifacts.

## UX gate

Read the campaign `UX_GATE.md`, launch copy, `core/creative_modes.py`, start-card tests, and the supplied before Join screenshot. No launch layout or vocabulary changed.

People still see **Make together**, **Paint along**, then **Host** / **Join**; Music keeps **Host** / **Join**. Join says **Join the room.** and **Paste the invite or the whole message.** While admission is deferred, the existing guidance stays **Contacting the host** / **WebJam is opening the room from your invitation.** Art reaches **You’re in** only after adoption succeeds.

`test_art_start_ux.py`: **66 passed** (including first-screen banned-word/card checks). `test_creative_modes_edge.py`: **39 passed**. UX smoke gate: **PASS**. Preview and fail-closed decisions remain unchanged.

## Tests and exact results

All pytest invocations used `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q` (the gate additionally uses `-p no:cacheprovider`), one module per fresh interpreter, without skips, xfails, or retry-on-failure logic.

- Red run before product changes: the initial 12 new cases all failed, **12 failed in 3.06s**, recorded in `before-tests.log`.
- Final new suite, including four additional lifecycle cases: **16 passed in 2.28s** in `gate-final.log`; an independent focused run recorded **16 passed in 1.90s** in `focused-final.log`.
- Standard Ruff gate: **PASS**, `All checks passed!`.
- Standard compileall: **PASS**, exit 0.
- `.venv/bin/python -m pip check`: **PASS**, `No broken requirements found.` (sandbox cache warning only).
- `.venv/bin/python ux_smoke_test.py`: **PASS**, `UX smoke gate passed.`
- `git diff --check` and `bash -n gate.sh`: **PASS**.

The unmodified fail-closed gate was run twice and **exited 1** at `test_art_room_connection_facts.py`: the sandbox denies `socket.bind` with `PermissionError: [Errno 1] Operation not permitted`. Remaining modules were then run individually, preserving all their tests. No socket test was skipped or weakened. Six modules require blocked loopback sockets; their exact results are below. All their failures/errors show the same denied-bind condition.

One intermediate controller regression rejected a fresh receipt after a modeled terminal loss. Product code was corrected to retain fresh-receipt recovery while invalidating cached retries; the unchanged overview module then passed **6 tests in 1.31s**. Affected recovery modules were rerun after that correction (`recovery-final.log`).

Latest results across the 43 attempted gate modules: **861 passed, 5 failed, 20 setup errors, 0 skipped**. The failures/errors are environmental socket-bind restrictions, so the complete gate is **not PASS**. The separately run remainder took roughly two minutes; this is an offline source gate, with no package/native build.

| Module | Latest result |
| --- | --- |
| `tests/test_lan_admission_deferred_profile.py` | 16 passed in 2.28s |
| `tests/test_native_admission_deferred_profile.py` | 2 passed in 2.07s |
| `tests/test_art_room_controller.py` | 17 passed in 1.83s |
| `tests/test_art_pending_profile_facts.py` | 17 passed in 3.11s |
| `tests/test_art_room_connection_facts.py` | 1 failed, 25 passed in 0.31s |
| `tests/test_art_room_recovery.py` | 6 passed in 1.12s |
| `tests/test_art_room_return_journey.py` | 24 passed in 6.96s |
| `tests/test_art_room_overview_controller.py` | 6 passed in 1.31s |
| `tests/test_art_lan_retry.py` | 35 passed in 4.41s |
| `tests/test_art_native_retry.py` | 3 passed in 0.95s |
| `tests/test_music_lan_guest_handoff_recovery.py` | 22 passed in 1.92s |
| `tests/test_lan_invitation_rejection.py` | 13 passed in 0.48s |
| `tests/test_rejected_invitation_journey.py` | 10 passed in 3.52s |
| `tests/test_art_cold_invitation_context.py` | 23 passed in 3.40s |
| `tests/test_art_invitation_flow.py` | 38 passed in 2.62s |
| `tests/test_art_invitation_entry.py` | 33 passed in 0.10s |
| `tests/test_art_audio_cleanup.py` | 15 passed in 0.54s |
| `tests/test_runtime_shutdown_fail_closed.py` | 10 passed in 1.13s |
| `tests/test_application_controller_band_check_gate.py` | 38 passed in 0.86s |
| `tests/test_guest_preflight_shared_recovery.py` | 20 passed in 3.36s |
| `tests/test_guest_recording_preflight_guidance.py` | 1 failed, 21 passed in 1.95s |
| `tests/test_controller_remote_transport.py` | 31 passed in 2.23s |
| `tests/test_native_art_room_return.py` | 11 passed in 3.45s |
| `tests/test_art_profile_guidance_journey.py` | 14 passed in 3.49s |
| `tests/test_art_room_connection_names_journey.py` | 33 passed in 7.80s |
| `tests/test_art_lesson_request_journey.py` | 18 errors in 9.57s |
| `tests/test_lan_lesson_request_worker.py` | 46 passed in 0.23s |
| `tests/test_session_transfer_art_start.py` | 3 failed, 20 passed in 0.40s |
| `tests/test_two_session_art_integration.py` | 4 passed in 1.51s |
| `tests/test_two_session_art_host_recovery.py` | 6 passed in 1.59s |
| `tests/test_invitation_conversation_lifetime.py` | 9 passed in 1.25s |
| `tests/test_invitation_conversation_journey.py` | 12 passed in 1.14s |
| `tests/test_composed_invitation_music_mix.py` | 4 passed in 1.23s |
| `tests/test_composed_music_invitation_conversation.py` | 3 passed in 1.19s |
| `tests/test_follow_along_music_guest.py` | 10 passed in 2.33s |
| `tests/test_follow_along_reconnect.py` | 1 warning, 1 error in 1.26s |
| `tests/test_follow_along_repeated_session.py` | 1 warning, 1 error in 1.28s |
| `tests/test_host_share_join_flow.py` | 70 passed in 2.28s |
| `tests/test_recording_format_controller.py` | 19 passed in 1.58s |
| `tests/test_reference_video_application_integration.py` | 37 passed in 0.68s |
| `tests/test_reference_track_application_integration.py` | 33 passed in 2.25s |
| `tests/test_art_start_ux.py` | 66 passed in 1.16s |
| `tests/test_creative_modes_edge.py` | 39 passed in 0.09s |

Raw evidence is in `/Users/jeffstory/Documents/bob-overnight-inject/local-webjam-24h-1010/items/W22/`: `before-tests.log`, `gate.log`, `gate-final.log`, `focused-final.log`, `overview-final.log`, `recovery-final.log`, `remaining-results.txt`, and per-module `test_*.log` files.

## Gate usage

From the assigned worktree root:

```bash
bash /Users/jeffstory/Documents/bob-overnight-inject/local-webjam-24h-1010/items/W22/gate.sh
```

The committed `docs/evidence/W22/gate.sh` is identical. It runs the focused tests, standard Python checks, and complete touched-area modules, offline, with `.venv/bin/python`. A denied local socket remains a hard failure for the driver to rerun in a socket-enabled environment.

## NOT RUN

- Physical/live audio, real devices, two-Mac LAN admission, and Webex: **NOT RUN**.
- Manual feel review and new after screenshots: **NOT RUN**; behavior was verified through offscreen controller/UI assertions.
- Full repository suite outside the 43 listed touched-area modules: **NOT RUN**.
- Successful socket-enabled execution of the six blocked modules: **NOT RUN**.
- Packaged builds, signing, notarization, and publication: **NOT RUN**.

No large builds were created. Final disk check showed **20 GiB available**, above the 10 GB floor.
