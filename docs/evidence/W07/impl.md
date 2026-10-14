# W07 — Refuse rehearsal-template exports WebJam cannot import

Implemented on `webjam-24h/W07-refuse-rehearsal-template-exports-that-w`, based on
`dd22f1ce33a76551fc172bf0f48524bb8e9f4254`. Local source change only.

## What changed

- `webjam_qt/widgets/rehearsal_plan.py`: serialize the reusable template once,
  encode it as UTF-8, and refuse bytes above the existing 2,097,152-byte import
  limit before calling the atomic writer. Accepted exports write those exact
  checked bytes with the existing private file mode. The importer and its
  limit are unchanged.
- Oversized exports leave existing destinations untouched and create no new
  destination. The current plan and all editor drafts remain intact. Refusal
  replaces any previous success status and displays the existing warning UI.
- `tests/test_rehearsal_plan_ui.py`: eight rejection cases cover new and existing
  destinations with ASCII, accented text, emoji, and JSON-escaped quotes. Four
  accepted cases cover the exact limit and one byte below it, with ASCII and
  emoji. They use actual save/load controls and filesystem bytes, checking song
  order, reusable fields, retained private drafts, and no success on refusal.
  Existing malformed/oversized import tests remain unchanged.
- `docs/evidence/W07/gate.sh` and this report are committed copies of the
  requested campaign outputs in `items/W07/`. Older Paint along W07 reports and
  gate were preserved as `impl-paint-host-archived.md` and
  `gate-paint-host-archived.sh` in that campaign directory.

## UX evidence

Visible warning title: **Plan not saved**.

Visible warning body: **This plan is too large. Shorten the goals and try again.
Your text is still here.** The persistent status starts **Plan not saved.** and
repeats that guidance. One next action: shorten the goals, then retry Save plan.
No editor length cap, truncation, import-limit increase, or new controls.

Reviewed launch copy, creative profiles, and `test_art_start_ux.py` per
`UX_GATE.md`; the launch gate passes, including banned-word and door-count
checks. Host/Join, Art's two starts, Preview, and physical-check honesty remain
unchanged. Maker-tool research: Audacity explicitly refuses exports beyond
format limits before attempting the export; see its
[official export-size guidance](https://manual.audacityteam.org/man/error_unsuitable_drive.html).
This supports putting actionable refusal at export time.

Captured and visually inspected the real Qt warning and persistent status using
offscreen rendering: `items/W07/template-export-refused-dialog.png` and
`items/W07/template-export-refused-panel-800x600.png`. Text fits and the latter
shows the saved-plan controls and failure status. The capture also verified the
previous file bytes remained intact. These are source UI captures, not physical
desktop or live rehearsal evidence.

## Exact test results

Before the product fix, the new focused cases produced **8 failed, 4 passed,
11 deselected in 1.77s**. All eight failures were oversized exports creating or
replacing destinations. Log: `items/W07/template-export-before-paragraphs.log`.

After the fix, a direct panel run produced **23 passed in 1.80s**.
The requested offline `items/W07/gate.sh` then exited **0**, with:

| Check | Result |
| --- | --- |
| `tests/test_rehearsal_plan_ui.py` | 23 passed in 1.73s |
| Ruff over `webjam_qt/ core/ ui/ services/ api/` | All checks passed |
| Compileall over `core webjam_qt ui services api tests` | Exit 0 |
| `python -m pip check` | No broken requirements found |
| `ux_smoke_test.py` | UX smoke gate passed |
| `tests/test_rehearsal_plan.py` | 14 passed in 0.05s |
| `tests/test_file_io.py` | 15 passed in 0.07s |
| `tests/test_session_library_dialog.py` | 24 passed in 0.97s |
| `tests/test_saved_follow_along.py` | 14 passed in 3.51s |
| `tests/test_rehearsal_recap.py` | 11 passed in 0.83s |
| `tests/test_art_start_ux.py` | 84 passed in 1.25s |
| `git diff --check` | Exit 0 |

Total: **185 passed**, no skips or xfails. Gate log:
`items/W07/template-export-gate.log`. Each Qt module runs in a fresh interpreter;
the gate makes no installs or network requests and completes well under 25 minutes.

The supplied `.venv` was a broken symlink. Replaced only that worktree link with
a small Python 3.11 environment using installed system site packages, without
downloads. All test commands use `.venv/bin/python`. Pip emitted an unwritable
cache warning; dependency checking still exited 0. Disk remained above 10 GB
free; no packages or large build artifacts were produced.

## Limitations / NOT RUN

- Real audio, physical desktop, two-Mac, Webex, and live rehearsal: **NOT RUN**.
- Packaged Windows/macOS app validation and full repository test suite:
  **NOT RUN**. Standard checks and relevant modules above ran.
- Live duplicate-PR verification: **NOT RUN successfully**; `gh pr list` failed
  to connect to api.github.com. The supplied backlog and local `open-prs.md`
  contain no overlapping rehearsal-template export item. Old W07 PR #346 is
  Paint along host action emphasis, a separate item. No PR was created.
- An initial diagnostic used a single two-million-character line and did not
  complete (process 90133; `template-export-before.log` stayed empty). The final
  fixtures use short paragraphs at the same exact byte boundaries to avoid
  stressing Qt line wrapping. Attempting to terminate only that diagnostic
  process was rejected by the execution sandbox; no PASS is claimed for it.
- No push, merge, tag, release, publishing, or physical/live claims.
