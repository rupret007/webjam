# W01 — Combined implementation evidence

## Merge resolution context

The two branches used the W01 evidence paths for different changes. Both
implementation reports are preserved below as historical evidence of their
original branch runs. Their environment repairs, commands, counts, screenshots,
and limitations are scoped to those runs; they are not fresh merge validation.

The combined intent keeps the PR's Art/Music File-menu suppression and verified
Music setup entry, together with master's native-invitation media-ownership
protections. Master's current compact footer below Join and Art-door centering
also remain part of the intended launch behavior.

`gate.sh` retains the union of both complete module lists and non-test checks,
including runtime dependency policy and whitespace checks. Shared modules run
once, and each module gets a fresh Python interpreter, as required by
`CONTRIBUTING.md`. The gate defaults to `python3.11`, exports
`QT_QPA_PLATFORM=offscreen`, and keeps compilation outputs in a temporary cache.
Run the combined gate from the worktree root:

```sh
bash docs/evidence/W01/gate.sh
```

No fresh test results are claimed in these preserved reports.

## Merge validation (2026-10-13)

Resolved all three conflicted worktree files by keeping both changelog entries,
both historical implementation reports, and every check from both gates.
The combined gate contains 51 unique test modules. The launch implementation
retains master's compact Make together footer below Join, Paint along guidance
above Host, and Art-door centering alongside the PR's File/Help menu changes.

The Windows compact-door test previously called `QAction.trigger()` on Music
setup even while Art was selected. That bypassed the menu's visibility. It now
asserts that setup is hidden on Art, selects Music, and asserts that setup is
visible and enabled before triggering it. The existing compact-layout,
Host/Join, banned-word, and installer-page assertions remain intact; no tests
or assertions were deleted, skipped, marked xfail, or loosened.

The PR's 15 focused modules, including `tests/test_launch_keyboard_compact.py`,
were run in separate interpreters with:

```sh
QT_QPA_PLATFORM=offscreen python3.11 -m pytest -q "$test_file"
```

| Module | Final result |
| --- | --- |
| test_art_start_ux.py | 84 passed |
| test_feel_pass.py | 14 passed |
| test_ui_redesign_regressions.py | 37 passed |
| test_host_share_join_flow.py | 71 passed |
| test_launch_keyboard_compact.py | 21 passed |
| test_launch_door_tall_balance.py | 4 passed |
| test_launch_paint_along_door_helper.py | 2 passed |
| test_art_profile.py | 48 passed |
| test_art_join_entry_ui.py | 56 passed |
| test_first_run_setup.py | 24 passed |
| test_legacy_mode_picker_retired.py | 5 passed |
| test_session_library_launch.py | 24 passed |
| test_workspace_navigation.py | 23 passed |
| test_workspace_construction_failure.py | 6 passed |
| test_offline_invitation_gate.py | 11 passed |

**430 passed, 0 failed, 0 skipped.** The Art start module was rerun after the
assertion clarification; its final result above counts each test only once.
Ruff on product directories and the gate's focused test files, compilation,
`pip check`, runtime dependency policy, UX smoke, `bash -n`, and
`git diff --check` also passed. The broader combined 51-module gate and physical
checks were not run during this merge validation. No `svc-*` screens were
changed. Files remain unstaged for the caller; the Git index was not modified.

## Historical PR evidence: hide extra File/Help start doors

Implemented on `webjam-24h/W01-hide-extra-file-help-start-doors` in the assigned
W01 worktree. Scope is the launch menus, their tests, and matching documentation.

### What changed

- On both Art and Music, File now presents only **Session library…**. New Music
  Project, Podcast & Voice, Review & Rehearsal, their separator, and the New
  shortcut are detached from the live menu, including Join/Back transitions.
- Existing workflow actions and programmatic entry points are retained.
  Secondary profiles retain their menus. Detaching actions, rather than making
  QActions invisible, preserves their enabled state and existing save-retry
  behavior. No existing tests or assertions were removed or relaxed.
- Help **Music setup…** is visible only for Music when the Windows resolver
  supplies a verified bundled installer for a missing component. Profile
  changes refresh it, and failed installer integrity revalidation removes it.
- Art still shows **Make together**, **Paint along**, then **Host** / **Join**.
  Music still shows **Host** / **Join** only. No new copy, cards, providers,
  Preview caveats, or engines were added to the door. Room Preview and
  fail-closed copy are unchanged.
- The existing Music room **Studio** entry remains completed-take review.
  This change does not add a new standalone-project entry point; the guides
  no longer direct people to the removed Art/Music launch menu actions.

### Files

- `webjam_qt/windows/launch_dialog.py`: synchronize File/Help menu presentation.
- `tests/test_art_start_ux.py`: 15 added cases covering both profiles,
  switching, Join/Back, menu contents, New shortcut suppression, macOS/Linux/
  Windows installer combinations, working setup navigation, and invalidation.
- `USER_GUIDE.md`, `docs/REFERENCE_STUDIO_MUSICIAN_GUIDE.md`, `CHANGELOG.md`:
  describe the actual launch and retained Studio behavior.
- `docs/evidence/W01/gate.sh`, `docs/evidence/W01/impl.md`: committed evidence.
  Matching requested copies are in the campaign's `items/W01/` directory.

### Exact validation results

The supplied `.venv` symlink was broken. Replaced only this worktree's link
with a local system-site-packages venv using the already installed Homebrew
Python 3.11.16, pytest 9.1.1, PySide6 6.11.1, and ruff 0.15.22. No downloads.

Before the product change:

```sh
QT_QPA_PLATFORM=offscreen PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_art_start_ux.py -k 'live_door_file_menu or music_setup_menu'
```

**15 failed, 66 deselected in 2.39s.** Failures demonstrate the extra File
starts, setup visible on disallowed profiles/platforms, and stale setup after
installer rejection. Log: `items/W01/current-red.log`.

An initial post-change test run had **2 failed, 79 passed in 2.29s** because
the new test's transient `QAction.menu()` wrapper lost its Qt object. Reading
the owned QMenu directly fixed the test lifetime; its assertions stayed intact.
The next full module run was **81 passed in 2.08s**.

The first scoped gate exposed four enabled-state regressions from Qt's
`setVisible(False)` behavior: **412 passed, 4 failed** across 15 modules.
The product now detaches workspace actions instead. Existing tests were
unchanged. Log: `items/W01/current-intermediate-gate.log`.

Final gate, run from the worktree root:

```sh
bash docs/evidence/W01/gate.sh
```

**Exit 0; 416 passed, no skipped tests, across 15 modules.**
Log: `items/W01/current-gate.log`.

| Module | Exact final result |
| --- | --- |
| test_art_start_ux.py | 81 passed in 1.86s |
| test_feel_pass.py | 14 passed in 0.92s |
| test_ui_redesign_regressions.py | 37 passed in 4.20s |
| test_host_share_join_flow.py | 70 passed in 3.03s |
| test_launch_keyboard_compact.py | 12 passed in 1.03s |
| test_launch_door_tall_balance.py | 4 passed in 0.86s |
| test_launch_paint_along_door_helper.py | 2 passed in 0.85s |
| test_art_profile.py | 47 passed in 0.83s |
| test_art_join_entry_ui.py | 56 passed in 1.78s |
| test_first_run_setup.py | 24 passed in 1.27s |
| test_legacy_mode_picker_retired.py | 5 passed in 1.17s |
| test_session_library_launch.py | 24 passed in 5.51s |
| test_workspace_navigation.py | 23 passed in 4.61s |
| test_workspace_construction_failure.py | 6 passed in 1.45s |
| test_offline_invitation_gate.py | 11 passed in 1.54s |

The gate preserves every check in the supplied campaign gate. Per
CONTRIBUTING.md and CI, every test module runs in a fresh Python interpreter.
It also passed ruff over the standard product directories and relevant tests,
compileall over product and tests, pip check (**No broken requirements found**),
and `ux_smoke_test.py` (**UX smoke gate passed**). Temporary compilation outputs
are cleaned by a trap. `bash -n` and `git diff --check` passed separately.

Offscreen 800×600 Art/Music door and File-menu PNGs plus menu text are in
`shots/W01/current-after/`. Visually inspected the Art door and Music File
popup: the popup contains **Session library…** alone; the Art door retains
both cards and Host/Join. These are widget fixtures, not native/live evidence.

### NOT RUN and boundaries

- Physical/live audio, two-Mac collaboration, Webex, native Windows installer
  launch, and physical keyboard/native menu interaction: **NOT RUN**.
- Entire repository Python suite, packaging, signing, and release gates:
  **NOT RUN**; the scoped standard Python gate above ran completely.
- No push, merge, PR, tag, release, Latest change, paid API, or sends.
- Disk check at completion: **18 GiB available**; no build outputs retained.
- Existing older campaign evidence was not used as evidence for this run;
  the previous report is preserved as `items/W01/impl-before-current-run.md`.

## Historical master evidence: keep native invitations unused during local media work

Implemented on `webjam-24h/W01-don-t-consume-a-native-invite-during-loc` in the assigned worktree. No push, PR, merge, tag, release, publication, paid API, or send.

### Behavior and UX

Native invitation acceptance, deferred startup, and the enrollment boundary now check Library/Studio media ownership before constructing a transport. A blocked acceptance leaves the user's invitation reusable; an invitation already loaded for startup remains in memory. Finishing local work does not join automatically: the user explicitly opens the same still-valid invitation again or starts the loaded invitation.

The existing guidance is reused verbatim:

> Wait for the workspace operation result before continuing another workspace.

If media work begins after enrollment and defers profile adoption beyond the existing deadline, the runtime records `local_operation_pending`, retires the uncertain capability, and offers **Paste New Invite**. Its room guidance reads:

> Local media work kept this room from opening. Finish that work, then ask the host for a fresh invitation and choose Paste New Invite.

The five-second room-state freshness limit is unchanged. Truly absent/stale first room state retains the existing fail-closed/update guidance. Short local deferrals still adopt fresh state when work completes. Neither a failed connection nor an uncertain enrollment can be retried automatically.

Reviewed `UX_GATE.md`, `launch_dialog.py`, `creative_modes.py`, the supplied before-shot text, and the start-card tests. The door still reads **Make together**, **Paint along**, **Host**, **Join**; its existing helper says “Host starts Make together. Join uses the host's invite.” No new doors, engine names, or first-screen caveats. Preview and fail-closed honesty remain intact. This is an ownership fix; copy alone could not prevent capability consumption.

### Files

- `webjam_qt/controllers/application_controller.py`: shared media-ownership guard at acceptance, startup, and enrollment; local-deferral recovery copy.
- `webjam_qt/controllers/room_participant.py`: remember profile deferral for the current native attempt and distinguish it at the unchanged deadline.
- `services/remote_session_runtime.py`: allowlisted local-deferral terminal reason, with no safe-retry authorization.
- `tests/test_native_admission_deferred_profile.py`: Library and Studio crossed with three entry paths; no construction while busy; explicit same-invite reuse; six-second local deferral; absent/stale state; no admission/replay after failure. Existing short-deferral tests remain.
- `tests/test_remote_session_runtime.py`: local deferral preserves terminal failure and rejects capability replay.
- `USER_GUIDE.md`, `CHANGELOG.md`: user-facing behavior and recovery documentation.
- `docs/evidence/W01/`: committed copies of this report and the executable gate.

### Tests and exact results

All commands used `.venv/bin/python`, with Qt offscreen and fresh interpreters per module. The supplied `.venv` symlink was broken; only this worktree's ignored symlink was repointed to the existing `/Users/jeffstory/Claude/Projects/WebJam/repo/.venv`. No dependencies were downloaded or shared environment contents changed.

| Check | Result |
| --- | --- |
| Final admission regressions against original `HEAD` product code | **7 failed, 4 passed**; six pre-enrollment cases and the long local deferral fail. `baseline-regressions.log` |
| New runtime regression against original product code | **1 failed** (missing local-deferral reason). `baseline-runtime.log` |
| Fixed admission module | **11 passed** |
| Fixed runtime module | **35 passed** |
| W01 gate: 40 affected-area modules, including focused regressions | **912 passed, 0 failed, 0 skipped**. `gate.log` |
| Product Ruff; compileall; pip check; runtime dependency policy; UX smoke; diff whitespace | **PASS** |
| Ruff on both modified test modules | **PASS** |
| Required Art start-card / banned-word checks | **66 passed** (`test_art_start_ux.py`, included in gate) |
| Creative-mode contracts | **39 passed** (`test_creative_modes_edge.py`, included in gate) |
| Repository-wide per-module Python gate | **821 passed, 11 subtests passed, 14 existing skips, 4 failed**, then stopped at `test_art_lan_host_portable.py`. `full-suite.log` |
| Same portable LAN module against unchanged `HEAD` product code | **4 failed** with the same `Host start failed: PermissionError`. `baseline-portable.log` |

The full-suite failures are local-listener permission failures in this restricted environment, reproduced before the change. No test was weakened, deleted, skipped, marked xfail, or retried to obtain a green full-suite claim. The remaining **478** modules of that all-repository run were **NOT RUN** after its fail-fast stop; the separate W01 gate did run the affected modules listed in `gate.sh`.

An initial implementation also changed stale-first-state recovery wording; the existing stale-state test rejected it. Product code was narrowed to confirmed local deferral, preserving that existing assertion unchanged. Original product files were temporarily restored for baseline checks and then restored byte-for-byte to the implementation; the focused tests and complete W01 gate were rerun afterward.

Run the offline gate from the worktree root:

```bash
bash /Users/jeffstory/Documents/bob-overnight-inject/local-webjam-24h-1010/items/W01/gate.sh
```

The final complete gate exited **0** in **107.02 seconds wall time** (`/usr/bin/time -p`: user 83.42s, sys 8.36s), below 25 minutes. It lists full affected test modules, retains every assertion, and uses a fresh interpreter for each Qt module. It makes no downloads. The full repository sweep is reported separately because local socket permissions prevent it passing here.

### NOT RUN / limitations

- Real audio, real two-Mac/native sidecar joining, Webex, physical keyboard, packaged-app behavior, and subjective feel: **NOT RUN**. Controlled transport and clock fixtures are not live evidence.
- New screenshot capture: **NOT RUN successfully**. An optional standalone capture attempt stopped before controller construction because its default database was inaccessible; no fresh screenshot is claimed. Existing campaign `after` files were not changed or treated as evidence from this run. Visible guidance is checked through the actual Qt HUD in the regression suite.
- No large build outputs created; disk check showed approximately **19 GiB free**, above the 10 GB requirement.
