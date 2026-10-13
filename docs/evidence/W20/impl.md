# W20 — Fix first rejected Save guidance visibility

Worktree: `/Users/jeffstory/Documents/webjam-24h/W20`
Branch: `webjam-24h/W20-guard-recording-setup-save-on-session-ch`
Verified on 2026-10-12 against review1.md, starting at `e03a3ce`.
This fix-round report supersedes the previous validation report; its historical
results remain in commit `e03a3ce` and the campaign verification logs.

## Change and review resolution

Addressed the review's P2: the first rejected Save could leave the entire reopen
message below the scroll viewport. `RecordingSetupDialog._show_error` now queues
scrolling for the next Qt event-loop turn, after the newly visible wrapped label
has been laid out and the scroll range updated. The timer uses the dialog as its
context so deletion cancels the callback. Production scope is one helper and
one import in `webjam_qt/windows/recording_setup.py`.

The existing session snapshot/generation/guest identity guards from `674d854`
and `e03a3ce` remain intact, including rejection before writing, post-acceptance
rechecking, retained drafts, fail-closed exceptions, and unchanged-session
Local Originals opt-out. No product copy or honesty decisions changed.

Added six real Qt keyboard/geometry regressions in
`tests/test_recording_setup_compact_recovery.py`: 620×360, 620×440, and 620×620,
each at normal and 125% font stretch with the production stylesheet. Each opens
with a clear guard, changes it to stale, focuses Save and presses Return once.
The test requires the entire error rectangle inside the viewport and sufficient
height for wrapped text, without test-driven scrolling. It also asserts no
write, retained draft, exact accessible guidance, retained Save focus, and
working Escape dismissal.

The guide and changelog explain automatic visibility. The gate adds this
focused selection and lints its module, retaining all previous checks and
assertions. Campaign `items/W20/impl.md` and `gate.sh` match the committed copies
in `docs/evidence/W20/`. No test was weakened, skipped, deleted, or xfailed.

## Regression proof

Before changing product code, ran:

```bash
QT_QPA_PLATFORM=offscreen PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -p no:cacheprovider tests/test_recording_setup_compact_recovery.py -q -k first_session_change --tb=short
```

**6 failed, 8 deselected in 1.25s**: all failed the viewport containment
assertion. At 620×440 with normal text, the error started at y=346 while the
viewport height was 334. Raw output: campaign `items/W20/fix1-regression-before.log`.
With the final fix, the same selection gave **6 passed, 8 deselected in 0.80s**.

## Final gate

```bash
RUFF_NO_CACHE=true bash /Users/jeffstory/Documents/bob-overnight-inject/local-webjam-24h-1010/items/W20/gate.sh
```

**Exit 1: gate FAIL.** Complete modules: **318 passed, 1 failed**.
Separately run focused selections: **17 passed**. The sole failure remains
`test_studio_opt_out_publishes_fresh_authenticated_zero_track_host_proof` in
`tests/test_guest_recording_preflight_guidance.py:361`: the sandbox denies
binding `127.0.0.1` with `PermissionError: [Errno 1] Operation not permitted`.
This matches review1's baseline failure; no retry, skip, or mock conceals it.
Raw complete output: campaign `items/W20/fix1-gate.log`.

| Check | Result |
| --- | --- |
| Ruff: production directories and all three relevant test modules | Passed |
| compileall: core/webjam_qt/ui/services/api/tests | Exit 0 |
| pip check | No broken requirements found |
| UX smoke | Passed |
| Focused dialog session-change cases | 2 passed, 17 deselected |
| Focused controller cases | 9 passed, 19 deselected |
| Focused first-rejection visibility cases | 6 passed, 8 deselected |
| test_recording_format_setup.py | 19 passed |
| test_recording_format_controller.py | 28 passed |
| test_recording_setup_compact_recovery.py | 14 passed |
| test_recording_studio.py | 134 passed |
| test_recording_readiness_ui.py | 16 passed |
| test_guest_recording_preflight_guidance.py | 21 passed, 1 failed |
| test_local_originals_choice_profiles.py | 20 passed |
| test_art_start_ux.py | 66 passed |
| git diff --check | Exit 0 |

## UX gate and limits

Read campaign UX_GATE.md, launch copy in `launch_dialog.py`, starts in
`creative_modes.py`, and Art start tests. Music retains **Host / Join**.
Art retains exactly **Make together / Paint along**, then **Host / Join**.
The Art suite checks banned first-screen words and extra doors. The existing
rejection still says:

> The session setup changed. Close and reopen Recording Setup to review it.

Preview labels and fail-closed copy are unchanged. The visibility evidence is
an offscreen software check with the application stylesheet, not a physical UX
observation. Native screen-reader announcement, real audio, two-Mac, Webex,
native Windows, physical usability, and the full repository suite: **NOT RUN**.
Package build, signing, notarization, and publication: **NOT RUN**.

No push, merge, PR, tag, release, sends, paid APIs, unrelated process changes,
or Latest changes. Only this worktree/branch and requested campaign artifacts
were written. Disk remains about 21 GiB free; no large build outputs were
created. The gate removes its temporary bytecode directory on exit.
