# W23 — Reconcile workspace Save after post-primary sync failure

Implemented on `webjam-24h/W23-reconcile-workspace-save-after-post-prim` in
`/Users/jeffstory/Documents/webjam-24h/W23`. Product commit: `531849f`.

## What changed

A failed regular save now returns an immutable, in-memory attempt receipt to
its owning coordinator/editor. It binds the workspace ID, original disk token
and revision to the exact attempted serialized bytes. Ordinary stale callers
without that receipt still conflict, including the existing stale-retry test.

Save retry checks the complete primary bytes under the library write lock and
re-publishes those same bytes through the existing file and directory fsync
helper. It reports success and advances the owner only after that operation
returns successfully. An unchanged retry retains the revision and original
backup. Newer Notes/title/Art/rehearsal/take/recap edits continue through normal
save after reconciliation. A subsequent publication failure retains both the
new attempt and its reconciled base so another retry remains possible.

External revisions or payload changes stay on the existing conflict/copy path.
Repeated durability failures retain the draft and old owner token. This is
regular-save recovery during the owning editor/controller lifetime; backup
import journals, new-workspace creation and restart recovery were not redesigned.

## Files

- `core/session_library.py`: attempt receipt, unconfirmed-save exception and exact-byte retry with durability confirmation.
- `webjam_qt/controllers/session_library.py`: retain receipts for live saves and coordinated editor saves.
- `webjam_qt/windows/session_library.py`: retain receipts for standalone library editing.
- `tests/support/workspace_save_faults.py`: inject directory-sync failure after real primary publication.
- `tests/test_session_library_save_retry.py`: 15 core recovery cases, including repeated failure, all-content conflicts, ownership, backup/damage preservation, subsequent-write failures and a read-race check.
- `tests/test_session_library_coordinator.py`: three added live/editor recovery and external-conflict regressions.
- `tests/test_session_library_dialog.py`: two added explicit Save-button cases, with and without newer drafts.
- `USER_GUIDE.md`, `CHANGELOG.md`: document the retry behavior.
- `docs/evidence/W23/{impl.md,gate.sh}`: committed copies of this report and the requested offline gate.

## Regression evidence

Temporarily restored the three production modules to original HEAD while
retaining the new UI tests, then restored the implementation in a `finally`
block. Each module ran in its own interpreter:

`PYTHONDONTWRITEBYTECODE=1 QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -p no:cacheprovider -q <module> -k post_primary`

- Original dialog: **2 failed, 22 deselected in 3.05s**. Explicit Save remained dirty after the retry.
- Original coordinator: **2 failed, 1 passed, 36 deselected in 3.30s**. Both recovery paths remained failed; the external-conflict control passed.
- During test development, the first late-take fixture lacked a recording reservation: **1 failed, 4 passed, 58 deselected in 3.11s**. Added the existing production start/reservation setup; no assertion was removed or loosened.

## Final offline gate — PASS

Command, run from the assigned worktree root:

```bash
bash /Users/jeffstory/Documents/bob-overnight-inject/local-webjam-24h-1010/items/W23/gate.sh
```

Exit **0**. The gate uses `.venv/bin/python`, no installs or network access, and
fresh interpreters per Qt test module. Standard Python checks follow
`CONTRIBUTING.md` / `DEVELOPMENT.md`, with the test modules scoped to the touched
persistence/editor areas and their backup callers.

| Check | Exact final result |
| --- | --- |
| New core retry module | 15 passed in 0.17s |
| Coordinator `-k post_primary` | 3 passed, 36 deselected in 1.02s |
| Dialog `-k post_primary` | 2 passed, 22 deselected in 0.71s |
| Ruff: `webjam_qt/ core/ ui/ services/ api/` | All checks passed |
| Compileall: `core webjam_qt ui services api tests` | Exit 0 |
| `pip check` | No broken requirements found |
| `ux_smoke_test.py` | UX smoke gate passed |
| `test_file_io.py` | 15 passed in 0.10s |
| `test_session_library.py` | 34 passed in 0.29s |
| `test_session_library_coordinator.py` | 39 passed in 1.95s |
| `test_session_library_dialog.py` | 24 passed in 1.07s |
| `test_session_library_launch.py` | 24 passed in 4.12s |
| `test_saved_work_restart.py` | 1 passed in 3.51s |
| `test_workspace_backup.py` | 96 passed, 1 skipped in 0.59s |
| `test_workspace_backup_coordinator.py` | 1 passed in 0.53s |
| `test_workspace_backup_library_ui.py` | 26 passed in 1.14s |
| `test_workspace_media_backup.py` | 65 passed in 1.06s |
| `test_workspace_media_library_ui.py` | 31 passed in 3.14s |
| `test_art_start_ux.py` | 66 passed in 1.13s |
| `git diff --check` | Exit 0 |

**437 distinct tests passed, 1 existing platform skip.** The five focused UI
cases also run in their full modules: 442 passing test executions in the gate.
The existing skip is `test_windows_native_handle_closes_if_crt_transfer_fails`,
reason “Real Windows native handle ownership,” on macOS. No test was weakened,
removed, xfailed or newly skipped. Pip emitted a non-fatal warning that its
usual user cache was not writable; dependency verification succeeded.
Additional Ruff check of the two new test/support modules: **All checks passed**.

## UX and limits

Read the campaign UX gate and launch/start-card code/tests. Door words remain
“Make together,” “Paint along,” “Host,” “Join,” and “Paste the invite or the
whole message.” The 66 start tests and UX smoke enforce existing door choices
and banned-word checks. No presentation changes were needed for this logic fix.
The editor still says “Use Save to retry or Save as copy.” Successful confirmed
retry says “Saved on this computer.” Preview and fail-closed decisions remain.

**NOT RUN:** full repository suite beyond the listed areas; fresh screenshots
and interactive visual review; packaged build; Windows execution; physical
power-loss/crash testing; real audio; two-Mac sessions; Webex. Injected filesystem
failures and offscreen tests establish code behavior, not physical durability
or live-session certification. Attempt receipts are intentionally in memory.

No push, merge, PR, tag, release, publication, paid API or sends. No large build
outputs were created. Disk availability was **21 GiB** at the final check.
