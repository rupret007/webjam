# W03 — Preserve a completed stem bounce when replacement write fails

Implemented in `/Users/jeffstory/Documents/webjam-24h/W03` on
`webjam-24h/W03-preserve-a-completed-stem-bounce-when-re`, starting from
`c2f78c73aab8171291ac8741b2b5a9562a247b69`.

## Change and files

- `core/stem_bench.py`: encode into an exclusively created, private temporary
  WAV beside the destination. Close the encoder, reopen for WAV/PCM24, frame,
  channel and rate validation, then publish with `os.replace`. Failures retain
  the existing final file or leave a fresh destination absent. Cleanup removes
  only this invocation's temporary path; a cleanup error cannot mask the save
  error. The existing error remains “WebJam could not save the mixed stems.”
- `tests/test_stem_bench.py`: 16 new cases cover first export and replacement
  with OSError/RuntimeError after a partial write, invalid WAV, incorrect frame
  count/channels/rate, failed publication, and successful publication. Tests
  compare the complete directory's files and bytes, including source stems and
  an unrelated temporary-looking file. Success checks decode actual PCM24 WAV
  samples, gain, mono duplication, padding, rate, channels, and frame count;
  the final pathname stays unchanged until encoding completes.
- `CHANGELOG.md`, `README_SIMPLE.md`: describe preserved mixes and the retry
  action. No product UI copy or layout changes.
- `docs/evidence/w03-stem-bounce/gate.sh`: reproducible offline gate, mirrored
  to the requested campaign `items/W03/gate.sh`.
- This report is mirrored to the requested campaign `items/W03/impl.md`.

## Exact checks run

All Python checks used `.venv/bin/python`. The supplied `.venv` was a broken
symlink to `webjam-finish-1005/.venv`; replaced only this worktree's link with
a small local `--system-site-packages --without-pip` environment using the
installed `/opt/homebrew/bin/python3.11`. No downloads or installs. Installed
test packages: pytest 9.1.1, numpy 2.4.6, soundfile 0.14.0, PySide6 6.11.1;
ruff 0.15.22.

Before the product fix, the added regression selection
`-m pytest tests/test_stem_bench.py -q -k bounce_publication` produced
**16 failed, 31 deselected in 0.28s**. Log: campaign
`items/W03/bounce-red.log`. All 16 cases fail on the original implementation.

Executed the requested `items/W03/gate.sh` from the worktree root. Exit **0**,
ending **W03 stem bounce gate: PASS**, within 25 minutes. Exact results from
`items/W03/bounce-gate.log`:

| Check | Result |
| --- | --- |
| Focused bounce publication regressions | 16 passed, 31 deselected in 0.21s |
| Complete `tests/test_stem_bench.py` | 47 passed in 0.27s |
| Complete `tests/test_song_workbench.py` | 59 passed in 0.31s |
| Complete `tests/test_song_tools_coordinator.py` | 129 passed in 2.35s |
| Complete `tests/test_song_overlay_ui.py` | 107 passed in 1.98s |
| Complete `tests/test_art_start_ux.py` | 69 passed in 1.76s |
| Ruff: `webjam_qt/ core/ ui/ services/ api/` and changed test module | All checks passed |
| Compileall: `core webjam_qt ui services api tests` | Exit 0 |
| `-m pip check` | No broken requirements found |
| `tools/runtime_dependency_policy.py --check` | Exit 0 |
| `ux_smoke_test.py` | UX smoke gate passed |
| `git diff --check` | Exit 0 |

The complete modules total **411 passed**, no failures or skips. The focused
16-case run is additional repeated coverage. Each complete module runs in a
fresh interpreter as required by CONTRIBUTING.md and CI. Compile caches are
created in a temporary directory and deleted by the gate. Disk remained at
11 GiB free; no build/package output was produced.

## UX and duplicate guard

Read the campaign UX_GATE.md, backlog W03 details, audit-ux.md and relevant
audit-logic.md finding; inspected launch copy, creative modes, and the
start-card tests. Relevant visible words remain “Send to jam”, “Mixing stems
for the jam…”, and “WebJam could not save the mixed stems.” Art's two starts
remain “Make together” / “Paint along”, followed by “Host” / “Join”; Music
remains “Host” / “Join”. The 69-test door suite includes banned-word and
extra-door checks. Preview, Suggestion and fail-closed honesty are unchanged.
This file-publication bug requires a code fix; copy alone cannot preserve a WAV.

The supplied `open-prs.md` and W03 backlog report no overlapping destination
publication fix. #339 covers changed source audio, and #354's older W03 covers
an observed recorder with no owned take. Attempted a read-only live
`gh pr list --state open --limit 100 --json number,title,headRefName`; it failed
with “error connecting to api.github.com”. Current live PR coverage is
unverified. No PR created, push, merge, tag, release, or publication performed.

## NOT RUN and limits

- Physical audio/listening, two-Mac, live Jamulus and Webex checks: **NOT RUN**.
- Windows runtime/filesystem execution: **NOT RUN**. Handles close before
  replacement, but this run is macOS only.
- New screenshots/manual visual feel review: **NOT RUN**; no UI change.
- Full repository test suite and packaging: **NOT RUN**; gate covers touched
  core, direct consumers, and mandated UX checks.
- Power-loss durability: **NOT RUN**, not claimed. Atomic publication protects
  against encode/validation/replace failures; this change adds no directory
  fsync guarantee. A filesystem that also refuses cleanup can retain the
  private staging file. No work was done on W04's stale room callback issue.

Tiny real WAV decoding and injected filesystem failures prove file integrity
and numeric mixing behavior; they do not prove physical audio quality.
