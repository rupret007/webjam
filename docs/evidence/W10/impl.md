# W10 — Art join title

The existing product already satisfies W10: Art shows **“Join the room.”** and
Music shows **“Join Music.”** The audit explicitly identified this as likely
already correct. No product copy or behavior was changed.

## Changes and files

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

## Exact validation results

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

## UX evidence

Read `UX_GATE.md`, both audits, launch copy, Art start definitions, and the
existing start-card tests. Inspected the supplied Music join screenshot and all
four new offscreen screenshots in campaign `shots/W10/after/`:

- `art-join-800x600.png`, `art-join-1280x800.png`: **“Join the room.”**
- `music-join-800x600.png`, `music-join-1280x800.png`: **“Join Music.”**

Both retain **“Paste the invite or the whole message.”** and **“If your invitation
says “same network,” use your host’s Wi-Fi or local network.”** Titles, input,
Join, and Back are visible. No new controls or copy were introduced. Qt reported
a missing Inter font during capture; these images use its available fallback.

## NOT RUN

Real audio, two-Mac checks, Webex, physical recording, live network joins,
packaged builds, and native Windows/Linux visual checks: **NOT RUN**.
The full repository test suite and online dependency audits: **NOT RUN**;
this gate covers the touched launch/join area. No existing tests were weakened,
skipped, deleted, or marked xfail. No push, merge, PR, release, or publish action.
