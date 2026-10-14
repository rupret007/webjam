# W25 — Abort metadata write on profile-file read error

Implemented and validated offline on 2026-10-12 in
`/Users/jeffstory/Documents/webjam-24h/W25`, branch
`webjam-24h/W25-abort-metadata-write-on-profile-file-rea`.

## Change

An `OSError` while loading the existing session profile map now aborts the
metadata write. The original bytes remain intact and the title edit remains
visible. A later save reads the map again and merges the current profile,
preserving even another profile's intervening edit. The bounded debug message
contains no exception details, paths, or titles.

Missing-file initialization and the existing corrupt-metadata replacement,
legacy migration, symlink, and borrowed-title policies remain unchanged.
Metadata remains best-effort: this change does not promise durable retention
of an unsaved title after quitting or add a new save-success notice.

## Files

- `webjam_qt/controllers/session_persistence.py`: handle read I/O errors before
  the existing corrupt-metadata fallback.
- `tests/test_session_persistence.py`: six regression cases (Music/Art ×
  permission denied, I/O error, descriptor exhaustion), plus a corrupt-JSON
  replacement control. Assert exact original bytes, no writer invocation,
  retained visible edit, and successful later merge with an intervening edit.
- `CHANGELOG.md`, `USER_GUIDE.md`: document the preservation behavior.
- `docs/evidence/W25/gate.sh`, `docs/evidence/W25/impl.md`: committed gate and
  implementation evidence; copied to the requested campaign `items/W25/` paths.

## Exact validation results

Before the product change:

```text
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_session_persistence.py -q -k 'metadata_read_error or corrupt_metadata_can_still'
6 failed, 1 passed, 27 deselected in 1.85s
```

All six regression cases failed at the assertion that the original file bytes
survive the failed read. The corrupt-JSON compatibility control passed.

After the product change, the initial complete persistence run:

```text
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_session_persistence.py -q
34 passed, 2 subtests passed in 1.94s
```

The requested gate was then run from the worktree root:

```text
bash /Users/jeffstory/Documents/bob-overnight-inject/local-webjam-24h-1010/items/W25/gate.sh
Exit 0; W25 gate: PASS
```

The gate uses `.venv/bin/python`, offline checks, and a fresh interpreter per
test module as required by the standard Python gate in `CONTRIBUTING.md`.
It covers all direct SessionPersistence test consumers, Notes quit, creative
modes, and the launch UX contract. No existing tests or assertions were changed.

| Gate check | Exact result |
| --- | --- |
| Focused regression/control selection | 7 passed, 27 deselected in 1.08s |
| `python -m ruff check webjam_qt/ core/ ui/ services/ api/` | All checks passed! |
| `python -m compileall -q core webjam_qt ui services api tests` | Exit 0, no output |
| `python -m pip check` | No broken requirements found. |
| `python ux_smoke_test.py` | UX smoke gate passed. |
| `test_session_persistence.py` | 34 passed, 2 subtests passed in 1.67s |
| `test_art_profile.py` | 47 passed in 0.49s |
| `test_notes_discovery_ui.py` | 9 passed in 1.51s |
| `test_notes_recovery_checkpoint_notice.py` | 3 passed in 0.56s |
| `test_notes_combined_unreadable.py` | 15 passed in 1.08s |
| `test_notes_restart_recovery_ui.py` | 8 passed in 0.98s |
| `test_notes_restart_recovery.py` | 23 passed in 1.30s |
| `test_notes_original_recheck.py` | 27 passed in 0.80s |
| `test_notes_recovery_guidance.py` | 5 passed in 0.57s |
| `test_notes_save_failures.py` | 20 passed in 0.88s |
| `test_notes_workspace_recovery.py` | 5 passed in 0.57s |
| `test_notes_export_recovery_boundary.py` | 9 passed in 0.59s |
| `test_notes_unreadable_recheck.py` | 19 passed in 1.17s |
| `test_notes_retry_integrity.py` | 21 passed in 0.70s |
| `test_notes_retry_quit.py` | 1 passed in 0.73s |
| `test_creative_modes_edge.py` | 39 passed in 0.09s |
| `test_art_start_ux.py` | 66 passed in 1.09s |

Module runs total **351 passed, 2 subtests passed**, plus the repeated focused
selection's **7 passed**. No failures or skips in the gate. Pip reported a
nonfatal unavailable-cache warning; its dependency check exited 0.
Additional checks: `bash -n docs/evidence/W25/gate.sh`,
`.venv/bin/python -m ruff check tests/test_session_persistence.py`, and
`git diff --check` all exited 0. Disk check: 22 GiB available; no large build
outputs created.

## UX gate

Read the campaign `UX_GATE.md`, launch copy, creative modes, and start-card
tests. Visible door words remain “What are you creating?”, “Make together”,
“Paint along”, “Host”, and “Join”. The 66 start UX tests passed, including the
banned-word and bounded-door contracts. No controls or product copy changed;
the fix protects saved work underneath the existing UI. Preview labels and
fail-closed decisions remain intact.

## NOT RUN

- Full repository-wide pytest sweep; the gate covers the touched areas.
- New screenshots and manual physical UI/feel review (no visual changes).
- Real audio, physical video playback, two-Mac sessions, Webex, native Windows.
- Packaged builds, signing, release/publication, remote push, merge, or PR.

The read failures are deterministic offline injections, not claims about
physical storage faults. Jeff retains final feel/merge judgment.
