# W20 — Guard Recording Setup Save on session change

Worktree: `/Users/jeffstory/Documents/webjam-24h/W20`
Branch: `webjam-24h/W20-guard-recording-setup-save-on-session-ch`
Verified on 2026-10-12. This report supersedes the earlier validation report.

## What changed

The supplied branch already contained implementation commit `674d854`
(`Reject Recording Setup Save when session identity changed under the modal.`).
Its product behavior was retained and verified:

- `webjam_qt/windows/recording_setup.py`: every Save checks session identity
  before writing the opening settings snapshot. A rejected Save preserves the
  visible draft. An unavailable guard fails closed with path-free guidance.
- `webjam_qt/controllers/application_controller.py`: compares the opening
  settings snapshot, settings generation, and guest owner; passes the guard
  to Setup and rechecks after acceptance before installing settings. The
  separate idle-only format guard still permits ordinary Local Originals
  opt-out during an unchanged active session.

This follow-up adds five regression cases in
`tests/test_recording_format_controller.py`: generation-only changes, in-place
endpoint/role/meeting preference edits without changing the settings object or
its generation, and a generation change after Save but before application.
They assert rejection before any write, exact reopen guidance, retained draft,
unchanged saved bytes, and refusal to install a stale accepted result.

`USER_GUIDE.md` now explains close/reopen/review and reapplying edits.
`CHANGELOG.md` records the W20 behavior. `docs/evidence/W20/gate.sh` and this
report are committed evidence copies; identical copies are provided at the
requested campaign `items/W20/gate.sh` and `items/W20/impl.md` paths.
The gate retains every existing check and adds the five focused cases.
No tests were weakened, skipped, deleted, or xfailed.

## UX gate

Read campaign `UX_GATE.md`, W20 backlog details, both audits, launch copy in
`launch_dialog.py`, the starts in `creative_modes.py`, and the Art start tests.
The user sees the existing one-action guidance:

> The session setup changed. Close and reopen Recording Setup to review it.

No new product copy was invented. Music still offers **Host / Join**; Art still
has exactly **Make together / Paint along**, then **Host / Join**. The Art start
suite passed all 66 tests. Preview and fail-closed honesty copy are unchanged.
No screenshots or physical UX observations are claimed by this verification.

## Regression proof

On the supplied fixed code:

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_recording_format_controller.py -q -k 'modal_identity_change or generation_change_after'
```

**5 passed, 23 deselected in 1.27s**, exit 0.

Temporarily replaced only the two product files with their versions from
`674d854^`, keeping the added tests. A Python `try/finally` restored both
current files after running the same selection with `--tb=short`.
**5 failed, 23 deselected in 1.45s**, exit 1. Four cases attempted the stale
write; the fifth installed a new settings object despite the generation change.
This is actual pre-fix behavior, not a simulated guard failure. Current product
files were restored and have no diff. Raw output:
`items/W20/verification-regression-before.log` in the campaign directory.

## Exact final gate results

Run from the worktree root:

```bash
bash /Users/jeffstory/Documents/bob-overnight-inject/local-webjam-24h-1010/items/W20/gate.sh
```

**Exit 1: gate FAIL.** Focused W20 checks pass. One adjacent test cannot bind
its localhost socket under this sandbox and fails with
`PermissionError: [Errno 1] Operation not permitted` in `socketserver.py`.
The test remains in the gate; no skip, retry, or mock was added to hide it.
This same failure occurred on the initial gate run before the added tests.

| Check | Exact result |
| --- | --- |
| Ruff: production directories | All checks passed! |
| Ruff: both recording-format test modules | All checks passed! |
| compileall: core/webjam_qt/ui/services/api/tests | Exit 0 |
| pip check | No broken requirements found. |
| UX smoke | UX smoke gate passed. |
| Focused dialog session-change cases | 2 passed, 17 deselected in 1.02s |
| Focused controller cases | 9 passed, 19 deselected in 1.46s |
| test_recording_format_setup.py | 19 passed in 0.75s |
| test_recording_format_controller.py | 28 passed in 2.30s |
| test_recording_setup_compact_recovery.py | 8 passed in 0.79s |
| test_recording_studio.py | 134 passed in 6.22s |
| test_recording_readiness_ui.py | 16 passed in 0.92s |
| test_guest_recording_preflight_guidance.py | 1 failed, 21 passed in 2.29s |
| test_local_originals_choice_profiles.py | 20 passed in 0.94s |
| test_art_start_ux.py | 66 passed in 1.31s |
| git diff --check | Exit 0 |

Failed test:
`test_studio_opt_out_publishes_fresh_authenticated_zero_track_host_proof`.
Full-module total: **312 passed, 1 failed**, plus the separately run focused
selection (**11 passed**). Raw final output is in campaign
`items/W20/verification-gate.log`. Pip also reported its normal inaccessible
user-cache warning; dependency verification succeeded.

## NOT RUN and scope

Real audio, two-Mac, Webex, native Windows, physical usability: **NOT RUN**.
Full repository test suite beyond the listed relevant modules: **NOT RUN**.
Package build, signing, notarization, publication: **NOT RUN**.
No push, merge, PR, tag, release, sends, paid APIs, or unrelated processes were
used. Only this worktree/branch and requested campaign artifacts were written.
Disk remained about 21 GiB free; no large build outputs were created and the
gate cleans its temporary bytecode directory on exit.
