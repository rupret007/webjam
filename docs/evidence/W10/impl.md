# W10 — Reject changed source audio during aligned-originals export

Implemented on 2026-10-11 in `/Users/jeffstory/Documents/webjam-24h/W10`,
branch `webjam-24h/W10-changed-source-audio-can-still-publish-a`.
These notes and the accompanying gate supersede the campaign folder's stale
W10 input-stream-ownership notes. Earlier logs/screenshots are not evidence
for this source-integrity fix.

## Changes and files

- `core/take_export.py`: compare each segment's existing post-render SHA-256
  against its declared hash before adding source evidence. A mismatch raises
  `TakeExportError`; existing exception cleanup removes the temporary package
  before publication. The existing bounded hash read is reused, with no extra
  audio read or change to export-receipt semantics.
- `tests/test_take_export.py`: extend `_reordered_project_take` coverage with
  four cases: same-size/source-format rewrite or atomic replacement, just
  after pre-render validation or just after rendering. They require rejection,
  no new completed or temporary package, preservation of an earlier export,
  and no exporter modifications to source files or the project manifest.
- `README.md` and `CHANGELOG.md`: document rejection and recovery.
- `docs/evidence/W10/gate.sh` and `impl.md`: committed reproducible checks and
  evidence, mirrored to the requested campaign `items/W10/` paths.

The surfaced error for the fixture is: **“Bass changed during export. Reopen
the take and retry.”** Copy alone cannot prevent a mismatched package; the
exporter must reject it. This change is limited to declared-hash validation
in schema-v2 track export; it does not redesign the separate Studio renderer
or claim protection against every transient change restored between hashes.

## Exact test results

Focused command:

```bash
.venv/bin/python -m pytest tests/test_take_export.py -q -k source_changes_during_export
```

- Before product fix: exit 1, **4 failed, 40 deselected in 0.40s**. Every case
  failed with `DID NOT RAISE TakeExportError`.
- After product fix: exit 0, **4 passed, 40 deselected in 0.17s**.

Completed offline gate, run from the worktree root:

```bash
bash /Users/jeffstory/Documents/bob-overnight-inject/local-webjam-24h-1010/items/W10/gate.sh
```

Exit **0**, final line **`W10 source integrity gate: PASS`**; completed in
under one minute, within the requested 25-minute limit. Full output is in
the campaign's `items/W10/source-integrity-gate.log`.

| Check | Exact result |
| --- | --- |
| Focused regressions in gate | 4 passed, 40 deselected in 0.22s |
| Ruff, standard production directories | All checks passed! |
| Ruff, changed test module | All checks passed! |
| compileall, standard production directories and tests | Exit 0, no output |
| pip check | No broken requirements found. |
| Runtime dependency policy | Exit 0, no output |
| UX smoke | UX smoke gate passed. |
| `test_take_export.py` | 44 passed in 0.43s |
| `test_take_project.py` | 18 passed in 0.12s |
| `test_take_library.py` | 73 passed, 8 subtests passed in 0.73s |
| `test_export_receipt.py` | 9 passed in 0.08s |
| `test_studio_export.py` | 49 passed in 0.83s |
| `test_studio_comping.py` | 13 passed in 0.22s |
| `test_recording_studio.py` | 134 passed in 6.24s |
| `test_art_start_ux.py` | 55 passed in 1.16s |
| `git diff --check` | Exit 0 |

Total: **395 passed and 8 subtests passed across eight complete modules**,
plus the four focused cases. Each complete module ran in a fresh interpreter
as required by the repository. No tests or gates were weakened or skipped.
Pip emitted an unwritable-cache warning and disabled caching; its check passed.

## UX and unrun evidence

Read the campaign UX gate, launch copy, creative modes, and Art start tests.
Visible door choices remain **“Make together”**, **“Paint along”**, then
**“Host” / “Join”** for Art, and **“Host” / “Join”** for Music. The 55 passing
Art start tests cover those choices and banned first-screen words. Preview
labels and existing fail-closed decisions are unchanged.

**NOT RUN:** full repository test suite; new screenshots/manual visual feel;
real audio or physical devices; two-Mac sessions; Webex; Windows physical
aligned-originals fallback; external-editor import. Audio regression files
are synthetic WAVs, rendered offline without opening an audio device.

No build bundles were created. Disk check reported **11 GiB available**.
No push, merge, PR, tag, release, publication, or Latest change was performed.
