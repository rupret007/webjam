# W10 — Keep Studio input ownership until close is confirmed

Implemented the recording W10 item on
`webjam-24h/W10-keep-ownership-of-a-studio-input-stream`, in the assigned W10
worktree. This report and gate replace the campaign directory's stale W10 Art
join-title artifacts; prior Art screenshots/logs are not evidence for this fix.

## What changed

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

## Exact validation results

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

## UX gate

Read the campaign `UX_GATE.md`, both audits, launch copy, creator starts, and
start-card tests. This backend correction requires no new UI or copy.
Existing device errors remain “WebJam couldn't stop the Studio input device
cleanly.” / “WebJam couldn't abort the Studio input device cleanly.”
The launch gate verifies **Make together** (“Paint, sculpt, 3D print, or just
talk.”), **Paint along** (“Follow a silent video from a file or lesson link.”),
and **Host** / **Join**, with no extra doors or banned first-screen words.
Preview labels and fail-closed behavior remain unchanged.

## NOT RUN

- Physical audio or microphone capture, real interface stop/close failures,
  two-Mac sessions, Webex, and other live checks: **NOT RUN**.
- Packaged builds, signing, native Windows/Linux execution, and new screenshot
  captures: **NOT RUN**. No visual surface changed.
- Full repository test suite and online dependency audits: **NOT RUN**.
  The standard Python gate was scoped to the affected recording/Studio area,
  with the required launch UX checks.
- No push, merge, PR, tag, release, publishing, or Latest change was performed.
