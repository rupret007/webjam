# W10 — Recording ownership and Art join title evidence

Two campaigns used the W10 evidence path. Both historical reports and their
validation results are retained below. `gate.sh` now runs both focused
regressions and the union of their test modules, each in a fresh interpreter.
The results below describe the original runs, not validation of this merge.

## W10 — Keep Studio input ownership until close is confirmed

Implemented the recording W10 item on
`webjam-24h/W10-keep-ownership-of-a-studio-input-stream`, in the assigned W10
worktree. The recording evidence below is separate from the earlier W10 Art
join-title evidence retained later in this file; Art screenshots/logs are not
evidence for the recording fix.

### What changed

- `core/project_recording.py`: own an input stream as soon as construction
  succeeds, and clear ownership only after `close()` returns successfully.
  Failed start, stop, and abort cleanup retain the handle when close fails.
  The existing start guard then blocks a replacement stream, and later stop or
  abort reaches the original device. Successful close still releases ownership
  even when stop/abort itself failed; existing bounded error copy is unchanged.
- `tests/test_project_recording.py`: extend the existing cleanup-failure test
  with `fail_stop` plus `fail_close`, asserting abort reaches the original
  stream. Add repeated stop/abort close failures, both retry operations,
  failed-start cleanup, no duplicate close after release, restart after
  successful cleanup, and a real `ProjectMultitrackRecorder` fallback-abort
  regression using a fake sounddevice module. Capture failure still produces
  recovery evidence rather than publishing a successful take.
- `CHANGELOG.md` and `docs/REFERENCE_STUDIO_MUSICIAN_GUIDE.md`: document the
  device-release behavior.
- `docs/evidence/W10/gate.sh` and this report: committed reproducible gate and
  evidence, also copied to the requested campaign `items/W10/` paths.

The snapshot's existing `running` flag remains conservative while a stream is
owned; this is not a claim that a failed device is physically capturing sound.

### Exact validation results

Run on 2026-10-10 from `/Users/jeffstory/Documents/webjam-24h/W10`.

Focused command, first run with the new tests and unchanged product code:

```bash
.venv/bin/python -m pytest tests/test_project_recording.py -q \
  -k 'closes_after_start_stop_and_abort_failures or retains_stream_until_close_succeeds or retains_failed_start_until_close_succeeds or fallback_abort_reaches_original_stream' \
  --tb=short
```

**Before fix:** exit 1, **12 failed, 1 passed, 19 deselected in 0.36s**.
Eleven failures exposed a false `running=False` after unconfirmed close; the
recorder regression exposed zero calls to the original stream's abort.

**After fix, same command:** exit 0, **13 passed, 19 deselected in 0.19s**.

Final offline gate command:

```bash
bash /Users/jeffstory/Documents/bob-overnight-inject/local-webjam-24h-1010/items/W10/gate.sh
```

**Exit 0, `W10 recording ownership gate: PASS`**, completed within the
25-minute limit. Uses `.venv/bin/python` throughout and follows the repository's
fresh interpreter per test module policy, without retries.

| Gate check | Exact result |
| --- | --- |
| Focused regression selection | 13 passed, 19 deselected in 0.22s |
| Ruff: production Python directories | All checks passed! |
| compileall: production directories and tests | Exit 0, no output |
| pip check | No broken requirements found. |
| UX smoke | UX smoke gate passed. |
| `test_project_recording.py` | 32 passed in 0.22s |
| `test_project_recording_commit.py` | 10 passed in 0.31s |
| `test_recording_sources.py` | 20 passed in 0.08s |
| `test_reference_studio_recording_ui.py` | 6 passed in 2.95s |
| `test_reference_studio_application.py` | 14 passed in 1.61s |
| `test_recording_studio.py` | 134 passed in 5.74s |
| `test_recording_studio_shutdown_retry.py` | 29 passed in 1.54s |
| `test_song_studio_clone.py` | 13 passed in 0.28s |
| `test_v026_podcast_voice_journey.py` | 3 passed in 0.86s |
| `test_art_start_ux.py` | 55 passed in 0.98s |
| `git diff --check` | Exit 0 |

Total: **316 passing tests across 10 complete modules**, plus the 13-case
focused run. No test was weakened, skipped, deleted, or marked xfail.
Pip warned that its cache directory was unwritable and disabled caching;
dependency validation passed. Raw gate output is in campaign
`items/W10/recording-gate.log`. The old `gate.log` belongs to the earlier Art
task and is not used here. Disk check after the gate: **12 GiB available**;
no large build outputs were created.

### UX gate

Read the campaign `UX_GATE.md`, both audits, launch copy, creator starts, and
start-card tests. This backend correction requires no new UI or copy.
Existing device errors remain “WebJam couldn't stop the Studio input device
cleanly.” / “WebJam couldn't abort the Studio input device cleanly.”
The launch gate verifies **Make together** (“Paint, sculpt, 3D print, or just
talk.”), **Paint along** (“Follow a silent video from a file or lesson link.”),
and **Host** / **Join**, with no extra doors or banned first-screen words.
Preview labels and fail-closed behavior remain unchanged.

### NOT RUN

- Physical audio or microphone capture, real interface stop/close failures,
  two-Mac sessions, Webex, and other live checks: **NOT RUN**.
- Packaged builds, signing, native Windows/Linux execution, and new screenshot
  captures: **NOT RUN**. No visual surface changed.
- Full repository test suite and online dependency audits: **NOT RUN**.
  The standard Python gate was scoped to the affected recording/Studio area,
  with the required launch UX checks.
- No push, merge, PR, tag, release, publishing, or Latest change was performed.

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
