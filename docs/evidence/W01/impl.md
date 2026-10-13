# W01 — Keep native invitations unused during local media work

Implemented on `webjam-24h/W01-don-t-consume-a-native-invite-during-loc` in the assigned worktree. No push, PR, merge, tag, release, publication, paid API, or send.

## Behavior and UX

Native invitation acceptance, deferred startup, and the enrollment boundary now check Library/Studio media ownership before constructing a transport. A blocked acceptance leaves the user's invitation reusable; an invitation already loaded for startup remains in memory. Finishing local work does not join automatically: the user explicitly opens the same still-valid invitation again or starts the loaded invitation.

The existing guidance is reused verbatim:

> Wait for the workspace operation result before continuing another workspace.

If media work begins after enrollment and defers profile adoption beyond the existing deadline, the runtime records `local_operation_pending`, retires the uncertain capability, and offers **Paste New Invite**. Its room guidance reads:

> Local media work kept this room from opening. Finish that work, then ask the host for a fresh invitation and choose Paste New Invite.

The five-second room-state freshness limit is unchanged. Truly absent/stale first room state retains the existing fail-closed/update guidance. Short local deferrals still adopt fresh state when work completes. Neither a failed connection nor an uncertain enrollment can be retried automatically.

Reviewed `UX_GATE.md`, `launch_dialog.py`, `creative_modes.py`, the supplied before-shot text, and the start-card tests. The door still reads **Make together**, **Paint along**, **Host**, **Join**; its existing helper says “Host starts Make together. Join uses the host's invite.” No new doors, engine names, or first-screen caveats. Preview and fail-closed honesty remain intact. This is an ownership fix; copy alone could not prevent capability consumption.

## Files

- `webjam_qt/controllers/application_controller.py`: shared media-ownership guard at acceptance, startup, and enrollment; local-deferral recovery copy.
- `webjam_qt/controllers/room_participant.py`: remember profile deferral for the current native attempt and distinguish it at the unchanged deadline.
- `services/remote_session_runtime.py`: allowlisted local-deferral terminal reason, with no safe-retry authorization.
- `tests/test_native_admission_deferred_profile.py`: Library and Studio crossed with three entry paths; no construction while busy; explicit same-invite reuse; six-second local deferral; absent/stale state; no admission/replay after failure. Existing short-deferral tests remain.
- `tests/test_remote_session_runtime.py`: local deferral preserves terminal failure and rejects capability replay.
- `USER_GUIDE.md`, `CHANGELOG.md`: user-facing behavior and recovery documentation.
- `docs/evidence/W01/`: committed copies of this report and the executable gate.

## Tests and exact results

All commands used `.venv/bin/python`, with Qt offscreen and fresh interpreters per module. The supplied `.venv` symlink was broken; only this worktree's ignored symlink was repointed to the existing `/Users/jeffstory/Claude/Projects/WebJam/repo/.venv`. No dependencies were downloaded or shared environment contents changed.

| Check | Result |
| --- | --- |
| Final admission regressions against original `HEAD` product code | **7 failed, 4 passed**; six pre-enrollment cases and the long local deferral fail. `baseline-regressions.log` |
| New runtime regression against original product code | **1 failed** (missing local-deferral reason). `baseline-runtime.log` |
| Fixed admission module | **11 passed** |
| Fixed runtime module | **35 passed** |
| W01 gate: 40 affected-area modules, including focused regressions | **912 passed, 0 failed, 0 skipped**. `gate.log` |
| Product Ruff; compileall; pip check; runtime dependency policy; UX smoke; diff whitespace | **PASS** |
| Ruff on both modified test modules | **PASS** |
| Required Art start-card / banned-word checks | **66 passed** (`test_art_start_ux.py`, included in gate) |
| Creative-mode contracts | **39 passed** (`test_creative_modes_edge.py`, included in gate) |
| Repository-wide per-module Python gate | **821 passed, 11 subtests passed, 14 existing skips, 4 failed**, then stopped at `test_art_lan_host_portable.py`. `full-suite.log` |
| Same portable LAN module against unchanged `HEAD` product code | **4 failed** with the same `Host start failed: PermissionError`. `baseline-portable.log` |

The full-suite failures are local-listener permission failures in this restricted environment, reproduced before the change. No test was weakened, deleted, skipped, marked xfail, or retried to obtain a green full-suite claim. The remaining **478** modules of that all-repository run were **NOT RUN** after its fail-fast stop; the separate W01 gate did run the affected modules listed in `gate.sh`.

An initial implementation also changed stale-first-state recovery wording; the existing stale-state test rejected it. Product code was narrowed to confirmed local deferral, preserving that existing assertion unchanged. Original product files were temporarily restored for baseline checks and then restored byte-for-byte to the implementation; the focused tests and complete W01 gate were rerun afterward.

Run the offline gate from the worktree root:

```bash
bash /Users/jeffstory/Documents/bob-overnight-inject/local-webjam-24h-1010/items/W01/gate.sh
```

The final complete gate exited **0** in **107.02 seconds wall time** (`/usr/bin/time -p`: user 83.42s, sys 8.36s), below 25 minutes. It lists full affected test modules, retains every assertion, and uses a fresh interpreter for each Qt module. It makes no downloads. The full repository sweep is reported separately because local socket permissions prevent it passing here.

## NOT RUN / limitations

- Real audio, real two-Mac/native sidecar joining, Webex, physical keyboard, packaged-app behavior, and subjective feel: **NOT RUN**. Controlled transport and clock fixtures are not live evidence.
- New screenshot capture: **NOT RUN successfully**. An optional standalone capture attempt stopped before controller construction because its default database was inaccessible; no fresh screenshot is claimed. Existing campaign `after` files were not changed or treated as evidence from this run. Visible guidance is checked through the actual Qt HUD in the regression suite.
- No large build outputs created; disk check showed approximately **19 GiB free**, above the 10 GB requirement.
