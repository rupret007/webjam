# W15 — Invitation confirmation owns the switch guard

Implemented only W15 on `webjam-24h/W15-invitation-confirm-dialog-is-inside-the`.

## What changed

The running-room invitation question now claims the existing single-switch guard before opening its modal event loop. Invitations received during the question replace the one memory-only pending invitation and its meeting URL. A Yes hands the same generation to one teardown worker; the latest invitation is then applied, including a replacement remote invitation.

No, dismissal, and exceptions release only the question's ownership and discard its queued invitation without stopping the current room, capturing its Library summary, changing settings, or preparing teardown. Shutdown invalidates an open question before taking cleanup ownership. A late outer Yes cannot resume or release a newer switch owner's latch. After Yes, recording/take/session identity, process owners, host role, room/startup generations, and cleanup/navigation state are rechecked before any teardown begins. A changed context leaves the new owner untouched and asks the person to reopen the invitation.

## Files

- `webjam_qt/controllers/application_controller.py`: confirmation ownership, cancellation, shutdown invalidation, post-question ownership checks, and accurate pending-invitation status.
- `tests/test_host_share_join_flow.py`: regression coverage for reentrant confirmation, latest meeting URL, No/dismissal/exception and retry, changed recording/session/process/room/startup ownership, concurrent cleanup, shutdown, summary failure, and stale outer continuation.
- `tests/test_controller_remote_transport.py`: extends existing remote-replacement test to arrivals during confirmation as well as teardown; preserves the reference-track retirement assertions.
- `docs/evidence/w15-invitation-confirmation/impl.md` and `gate.sh`: committed copies of this report and the offline gate. Requested copies also live in the campaign `items/W15` directory.

## UX gate

Reviewed `UX_GATE.md`, both audits, the W15 backlog details, supplied before-copy harvest, `launch_dialog.py`, `creative_modes.py`, and the Art start tests. This requires an ownership fix; copy alone cannot prevent two teardown workers.

Existing confirmation remains **“Join this room?”** (Art) / **“Join this jam?”** (Music), with **Yes / No**, default **No**. Body remains “WebJam will safely end your current room, then join the new one.” (Music uses “jam”.)

During confirmation, the new status is **“WebJam received a newer invitation. Choose Yes to join that one.”** It does not claim cleanup has begun. If ownership changes: **“The current room changed. Open the invitation again when you’re ready.”**

Launch words remain **Make together / Paint along → Host / Join**, with **Paste the invite** on Join. All 66 Art start UX tests pass, including banned-word and visible-door harvest checks. Preview labels and fail-closed recording/cleanup copy are unchanged. For the real maker-tool wording check, reviewed [Krita's File Menu](https://docs.krita.org/en/reference_manual/main_menu/file_menu.html), which uses direct action labels such as Open, Close, and Quit; no new control or door was introduced.

## Tests and exact results

Regression demonstrated before the product fix:

`QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q tests/test_host_share_join_flow.py -k single_flight_and_latest_wins`

**1 failed, 1 passed, 69 deselected in 1.62s**. The confirmation-arrival case failed because `question.call_count` was **2**, expected **1**. The existing teardown-arrival case passed.

Final offline gate invocation, from the worktree root:

`bash /Users/jeffstory/Documents/bob-overnight-inject/local-webjam-24h-1010/items/W15/gate.sh`

**Exit 0.** Focused runs:

- Invitation confirmation and latest-wins cases: **22 passed, 69 deselected in 1.81s**.
- Remote replacement cases: **2 passed, 30 deselected in 0.79s**.

Standard Python checks from `.github/workflows/ci.yml`: `pip check` reported **No broken requirements found**; `compileall` and runtime dependency policy check exited **0**; `git diff --check HEAD` exited **0**; product-wide Ruff reported **All checks passed**; `ux_smoke_test.py` reported **UX smoke gate passed**. Pip printed a non-writable cache warning and disabled its cache; this did not affect the check.

The relevant complete modules ran separately, following CI's Qt process-isolation convention:

| Module | Exact result |
| --- | --- |
| `test_host_share_join_flow.py` | 91 passed in 3.02s |
| `test_controller_remote_transport.py` | 32 passed in 2.12s |
| `test_runtime_shutdown_fail_closed.py` | 10 passed in 1.01s |
| `test_workspace_navigation.py` | 23 passed in 3.43s |
| `test_art_invitation_flow.py` | 38 passed in 2.45s |
| `test_art_invitation_entry.py` | 33 passed in 0.11s |
| `test_invitation_conversation_lifetime.py` | 9 passed in 1.20s |
| `test_invitation_conversation_ingress.py` | 63 passed in 0.74s |
| `test_reference_track_application_integration.py` | 33 passed in 2.21s |
| `test_jamulus_native_startup.py` | 78 passed in 0.55s |
| `test_art_start_ux.py` | 66 passed in 1.12s |

**576 complete-module tests passed, plus 24 focused test executions; zero failures or skips.** Full gate output is retained in campaign `items/W15/gate.log`. After the gate, only comment/line wrapping and evidence files changed; touched-product Ruff and `git diff --check` passed again.

## NOT RUN / limits

- **NOT RUN:** live OS invitation delivery between two Macs; physical/live audio capture or playback; Webex talk/share; packaged-app validation; interactive visual acceptance or new screenshots.
- Automated confirmation tests inject reentry through the question callback and use controlled workers; Qt runs offscreen. These are deterministic regression checks, not live invitation certification.
- **NOT RUN:** the entire repository suite beyond the relevant modules listed above; network dependency audit (gate is offline).
- No push, merge, PR, tag, release, publish, or Latest change. No paid APIs or sends. No unrelated processes touched. No large build outputs created; disk check showed **18 GiB available**.
