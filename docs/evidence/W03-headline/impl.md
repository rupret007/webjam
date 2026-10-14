# W03 — Drop leading “Or” on silent-reference headline

Implemented only this backlog item in `/Users/jeffstory/Documents/webjam-24h/W03`,
branch `webjam-24h/W03-drop-leading-or-on-silent-reference-head`.
This report supersedes the previous campaign's unrelated W03 report.

## Change and files

- `webjam_qt/windows/reference_video.py`: one-line copy change from “Or choose
  a silent reference” to **Choose a silent reference**. The shared constant
  also supplies the headline after a host file failure; its error remains visible.
- `tests/test_reference_video_ui.py`: update both exact headline assertions;
  add styled 800×600 and 1280×800 regressions checking the headline and that
  **Watch a shared lesson** is enabled, fully inside the initial viewport,
  above the headline, first among visible action buttons, and the only visible
  `PrimaryButton`. Local-file and YouTube actions retain `GhostButton` styling.
- `USER_GUIDE.md`, `CHANGELOG.md`: document the visible headline and hierarchy.
- `docs/evidence/W03-headline/gate.sh`: offline focused and affected-area gate.
- `docs/evidence/W03-headline/capture.py`: repeatable offscreen screenshots.
- `docs/evidence/W03-headline/impl.md`: this evidence report.

The requested campaign `items/W03/impl.md` and `items/W03/gate.sh` are copies
of the committed report and gate. Existing campaign artifacts were preserved
except for those two explicitly requested output files.

## UX review

Read the campaign UX gate, W03 backlog details, both audits, launch copy,
creative-mode start cards, and start-card tests. Art still offers **Make
together** / **Paint along**, then **Host** / **Join**. The helper remains
“Host starts Paint along, then chooses a file or lesson link. Join uses the
host's invite.” Start-card and banned-word tests pass.

The visible empty-host headline is **Choose a silent reference**. Its guidance
remains “Choose a local video or YouTube link below for silent playback inside
WebJam. You control it.” The first orange primary remains **Watch a shared
lesson**. Preview, silence, meeting ownership, and fail-closed copy are unchanged.
The wording was specified by the backlog; no new product terminology was invented.

Reviewed the supplied compact before image and both new styled offscreen
screenshots. The share control is fully visible first at both sizes. The lower
controls' compact clipping is the separately scoped W04 item and is not fixed here.

After images (campaign directory):

- `shots/W03/after-headline/paint-along-host-headline-800x600.png`
- `shots/W03/after-headline/paint-along-host-headline-1280x800.png`

Capture command from the worktree root (exit 0, both images saved and reviewed):

```bash
PYTHONDONTWRITEBYTECODE=1 QT_QPA_PLATFORM=offscreen .venv/bin/python docs/evidence/W03-headline/capture.py /Users/jeffstory/Documents/bob-overnight-inject/local-webjam-24h-1010/shots/W03/after-headline
```

## Tests and exact results

Before changing product code, ran the updated opening/failure assertions and
both new size cases with this selection:

```bash
QT_QPA_PLATFORM=offscreen PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_reference_video_ui.py -k 'the_host_panel_opens_on_the_no_video_path or empty_host_keeps_shared_lesson_first_above_silent_reference or the_host_panel_shows_a_failure_instead_of_a_stale_source'
```

**4 failed, 30 deselected in 1.03s**: every failure was the old headline versus
“Choose a silent reference”. Log: `items/W03/headline-red.log`.
After the product change, the complete UI module passed: **34 passed in 1.74s**.

Ran the requested gate from the worktree root:

```bash
bash /Users/jeffstory/Documents/bob-overnight-inject/local-webjam-24h-1010/items/W03/gate.sh
```

Exit **0**. Log: `items/W03/headline-gate.log`.
The gate uses `.venv/bin/python`, requires no network/install/build, and runs
each affected test module in a fresh interpreter per CONTRIBUTING.md / CI.

| Selection | Exact result |
| --- | --- |
| Four focused regression cases | 4 passed, 30 deselected in 1.12s |
| `tests/test_reference_video_ui.py` | 34 passed in 1.49s |
| `tests/test_reference_video_coordinator.py` | 25 passed in 1.17s |
| `tests/test_reference_video_youtube.py` | 24 passed in 1.07s |
| `tests/test_paint_along_host_opening_ui.py` | 19 passed in 4.34s |
| `tests/test_paint_along_guest_opening_ui.py` | 22 passed in 6.79s |
| `tests/test_paint_along_copy_recovery_ui.py` | 41 passed in 6.98s |
| `tests/test_paint_along_youtube_ui.py` | 4 passed in 0.97s |
| `tests/test_polish_1006_round2.py` | 5 passed in 1.51s |
| `tests/test_art_start_ux.py` | 66 passed in 1.65s |
| `.venv/bin/python ux_smoke_test.py` | UX smoke gate passed |
| Ruff, changed product/test files | All checks passed |
| Compileall, changed product/test files | Exit 0 |
| `.venv/bin/python -m pip check` | No broken requirements found; exit 0 |
| `git diff --check` | Exit 0 |

Whole modules: **240 passed**, no skips or xfails. Four focused cases are
repeated separately, for 244 passing executions in the gate. The `-k` selection
is only the initial focused run; the complete module follows without exclusions.
Ruff on the capture script and `bash -n` on the gate also passed (exit 0).
Pip reported its non-writable user cache and disabled that cache; dependency
validation passed. Qt emitted a missing “Sans Serif” alias warning during capture.

## Limits / NOT RUN

- Full repository suite: **NOT RUN**; this copy-only change used complete
  affected-area modules and the UX gate, with no shared controller changes.
- Physical/live audio, listening, two-Mac networking, Webex share/talk,
  live keyboard/feel, and packaged-app checks: **NOT RUN**.
- Screenshots are offscreen production-widget fixtures, not live sessions.

No existing test was weakened, skipped, deleted, or xfailed. No layout,
controller, transport, or media behavior changed. No build outputs were created;
the gate removed its temporary bytecode cache. Disk check showed 18 GiB free.
No push, merge, PR, tag, release, publication, external send, or Latest change.
