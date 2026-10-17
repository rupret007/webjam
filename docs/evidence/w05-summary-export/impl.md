# W05 — Preserve another writer's file after a failed summary export

Implemented 2026-10-16 in `/Users/jeffstory/Documents/webjam-24h/W05`, branch
`webjam-24h/W05-do-not-delete-another-writer-s-file-afte`.

## What changed

Library summary export never unlinks the selected pathname after creation.
Another writer can replace that pathname while the export owns an open file;
cleanup must not delete that writer's replacement. An identity check followed by
unlink would still race, so this fix deliberately retains uncertain output.
It keeps exclusive creation, mode 0600, write/flush/fsync/close confirmation,
and the reconciled snapshot captured before the modal picker. A failed stream
wrapper initialization also closes the owned descriptor.

Visible copy after a failure following creation:

> Summary export failed. The chosen file may be incomplete. Retry with a new filename.

Open/create refusal retains:

> Summary was not exported. Choose a new filename in a writable folder.

Success retains:

> Summary exported. Original work is unchanged.

This is the small retention-based fix permitted by the acceptance criteria, not
staged publication. Failed output can remain empty, partial, or fully written
but unconfirmed; the message does not claim deletion or successful export.
Retries require a different filename. Original workspace content stays saved.

## Files

- `webjam_qt/windows/session_library.py`: safe failure handling and truthful status.
- `tests/test_session_library_coordinator.py`: 13 new cases; strengthen the existing
  successful reconciliation test with exact summary and success-copy assertions.
- `USER_GUIDE.md`: new-filename and retained-output recovery guidance.
- `changelog.d/summary-export-preservation.md`: user-visible fix.
- `docs/evidence/w05-summary-export/{gate.sh,impl.md}`: committed gate and evidence.
- Campaign `items/W05/{gate.sh,impl.md}`: requested copies. The prior unrelated
  bookmark report/gate are archived there before replacement.
- Campaign `shots/W05/after/summary-export-failure-800x600.png`: inspected offscreen
  real Library widget, native unthemed fixture, injected fsync error. Status and
  Export summary remain fully visible at 800x600. No physical UI claim.

## Verification — RUN

The initial `.venv` was a dangling symlink to `webjam-finish-1005/.venv`.
Replaced only that worktree-local broken link with a small ignored venv created
using `/opt/homebrew/bin/python3.11 -m venv --system-site-packages --without-pip .venv`.
No installs/downloads or shared-environment edits. All tests below use
`.venv/bin/python` and Qt offscreen. The full gate runs each module in a fresh
interpreter, as required by CONTRIBUTING.md and DEVELOPMENT.md.

Red reproduction, before the product change:

` .venv/bin/python -m pytest -q tests/test_session_library_coordinator.py -k 'summary_export_io_failure or summary_export_refuses_existing_destination' `

**8 failed, 4 passed, 69 deselected in 2.08s.** All eight failures assert the
removed output still exists; four replace it with binary sentinel bytes at the
write, flush, fsync, or close failure boundary. All existing-destination cases
passed. An intermediate implementation run exposed a context-manager binding
mistake (8 failed, 12 passed); corrected before the successful gate below.

`bash docs/evidence/w05-summary-export/gate.sh` — **PASS, exit 0**:

| Check | Exact result |
| --- | --- |
| Focused coordinator `-k summary_export` | 21 passed, 61 deselected in 1.72s |
| Full session_library_coordinator | 82 passed in 3.89s |
| Full session_library_dialog | 25 passed in 1.19s |
| Full session_library | 34 passed in 0.31s |
| Full session_library_launch | 24 passed in 4.89s |
| Full session_library_create_retry | 6 passed in 0.08s |
| Full session_library_save_retry | 15 passed in 0.12s |
| Full workspace_backup_library_ui | 33 passed in 1.44s |
| Full workspace_media_library_ui | 31 passed in 3.14s |
| Full notes_workspace_recovery | 5 passed in 0.69s |
| Full art_start_ux | 98 passed in 2.48s |
| Ruff product directories + touched test module | All checks passed |
| compileall core/webjam_qt/ui/services/api/tests | exit 0 |
| pip check | No broken requirements found |
| ux_smoke_test.py | UX smoke gate passed |
| git diff --check | exit 0 |

**353 full-module test passes plus 21 focused passes (374 executions); no skips.**
Gate log: campaign `items/W05/summary-export-gate.log`. Gate completes well below
25 minutes, entirely offline. Additional commands:

- `.venv/bin/python tools/gates/test_integrity_guard.py fake-clock`: OK, found nothing.
- `.venv/bin/python tools/gates/test_integrity_guard.py loosening --base origin/master`:
  OK, found nothing.

New regressions cover ordinary/replacement write, flush, fsync, and close errors;
byte-identical replacement preservation; regular file, hardlink, symlink and
dangling-symlink refusal; empty retained output and descriptor closure when
fdopen fails. Existing selected-before-picker, canceled picker, late recap,
live Notes and conflicting-draft tests remain intact. No tests weakened.

## UX and ownership checks

Read the campaign UX gate, both audits, W05 detail, launch copy, creative modes,
and Art start tests. Door choices, Preview labels and fail-closed decisions are
unchanged. Copy reference: [Apple Pages: Save and name a document](https://support.apple.com/guide/pages/save-and-name-a-document-tan95caaa4ff/mac),
which uses naming/location as the familiar saving workflow. Recovery here asks
for one action, a new filename, without filesystem jargon.

Live duplicate check: shell `gh pr list` could not reach api.github.com. GitHub
connector searches for open PRs in rupret007/webjam with `summary` and `W05`
succeeded. The summary matches were #391, #386, #372, #388 and #387, all unrelated;
W05 had no matches. No PR was created. No push, merge, tag, release, publication,
Latest change, external send, paid API, or unrelated process operation.

Disk remained about 24 GiB free, above the 10 GB floor. Local venv is small;
no large build outputs created.

## NOT RUN

Full unrelated repository suite; actual disk/power failure; actual cloud-sync
replacement; real audio; two-Mac sessions; Webex; physical keyboard/window
acceptance; packaged/signed builds; Windows execution. Faults are controlled
injections in real production export methods with real temporary files.
