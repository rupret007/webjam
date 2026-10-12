# W10 — Combined implementation evidence

Two independent changes used the W10 evidence directory. Both original reports
are preserved below as historical evidence, with their original dates and
results. The shared `gate.sh` now runs the union of both validation gates, with
each complete Qt test module in a fresh interpreter. Historical campaign-copy
and validation statements below describe the original runs, not this merge.

## W10 — Reject changed source audio during aligned-originals export

Implemented on 2026-10-11 in `/Users/jeffstory/Documents/webjam-24h/W10`,
branch `webjam-24h/W10-changed-source-audio-can-still-publish-a`.
These notes and the accompanying gate supersede the campaign folder's stale
W10 input-stream-ownership notes. Earlier logs/screenshots are not evidence
for this source-integrity fix.

### Changes and files

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

### Exact test results

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

### UX and unrun evidence

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

## W10 — Art join title

The existing product already satisfies W10: Art shows **“Join the room.”** and
Music shows **“Join Music.”** The audit explicitly identified this as likely
already correct. No product copy or behavior was changed.

### Changes and files

- `tests/test_art_start_ux.py`: four new offscreen cases cover saved Art/Music
  profiles at 800×600 and 1280×800, visible profile-card clicks in both directions,
  both Art starts, Join, and Back. Assertions cover the exact displayed and
  accessible title, title width, unchanged invitation subtitle, disabled empty
  Join action, and the existing banned-word harvest.
- `docs/evidence/W10/gate.sh`: offline gate using `.venv/bin/python`, with the
  README's lint, compile, dependency, UX-smoke checks and fresh Python processes
  per relevant test module. Run from the worktree root.
- `docs/evidence/W10/impl.md`: this report. Identical report and gate copies are
  provided in the campaign's `items/W10/` directory.

### Exact validation results

Run on 2026-10-10 from `/Users/jeffstory/Documents/webjam-24h/W10` on
`webjam-24h/W10-art-join-title-join-the-room`.

Baseline focused command:

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_art_start_ux.py -q -k join_title_tracks_visible_profile_and_back_navigation
```

Result: **4 passed, 55 deselected in 1.58s** on unchanged product code.
There is no honest fail-before/product-fix result to report for this already
correct behavior. Instead, regression sensitivity was checked with a deliberate,
process-local mutation (no source edits):

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python - <<'PY'
from dataclasses import replace
import pytest
from webjam_qt.windows import launch_dialog
launch_dialog._CREATOR_LAUNCH_COPY['art'] = replace(
    launch_dialog._CREATOR_LAUNCH_COPY['art'], join_title='Join Music.'
)
raise SystemExit(pytest.main([
    'tests/test_art_start_ux.py', '-q',
    '-k', 'join_title_tracks_visible_profile_and_back_navigation',
    '--tb=short',
]))
PY
```

Result: **4 failed, 55 deselected in 0.42s**, exit 1. Every failure was
`'Join Music.' != 'Join the room.'`. This is mutation evidence, not a baseline
failure. The mutation ends with that interpreter.

Final gate command:

```bash
bash /Users/jeffstory/Documents/bob-overnight-inject/local-webjam-24h-1010/items/W10/gate.sh
```

Result: **exit 0, W10 gate: PASS** (under one minute).

| Check | Exact result |
| --- | --- |
| Ruff, production Python directories | All checks passed! |
| compileall, production directories and tests | Exit 0, no output |
| pip check | No broken requirements found. |
| UX smoke | UX smoke gate passed. |
| Focused W10 cases | 4 passed, 55 deselected in 0.93s |
| `test_art_start_ux.py` | 59 passed in 0.98s |
| `test_launch_keyboard_compact.py` | 4 passed in 0.59s |
| `test_art_join_entry_ui.py` | 56 passed in 1.33s |
| `test_creator_live_presentation.py` | 11 passed in 0.50s |
| `test_host_share_join_flow.py` | 69 passed in 2.27s |
| `git diff --check` | Exit 0 |

Full gate output: campaign `items/W10/gate.log`. Pip emitted an unwritable-cache
warning and disabled its cache; dependency validation still passed.

### UX evidence

Read `UX_GATE.md`, both audits, launch copy, Art start definitions, and the
existing start-card tests. Inspected the supplied Music join screenshot and all
four new offscreen screenshots in campaign `shots/W10/after/`:

- `art-join-800x600.png`, `art-join-1280x800.png`: **“Join the room.”**
- `music-join-800x600.png`, `music-join-1280x800.png`: **“Join Music.”**

Both retain **“Paste the invite or the whole message.”** and **“If your invitation
says “same network,” use your host’s Wi-Fi or local network.”** Titles, input,
Join, and Back are visible. No new controls or copy were introduced. Qt reported
a missing Inter font during capture; these images use its available fallback.

### NOT RUN

Real audio, two-Mac checks, Webex, physical recording, live network joins,
packaged builds, and native Windows/Linux visual checks: **NOT RUN**.
The full repository test suite and online dependency audits: **NOT RUN**;
this gate covers the touched launch/join area. No existing tests were weakened,
skipped, deleted, or marked xfail. No push, merge, PR, release, or publish action.
