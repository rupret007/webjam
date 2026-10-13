# W01 — Hide extra File/Help start doors

Implemented on `webjam-24h/W01-hide-extra-file-help-start-doors` in the assigned
W01 worktree. Scope is the launch menus, their tests, and matching documentation.

## What changed

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

## Files

- `webjam_qt/windows/launch_dialog.py`: synchronize File/Help menu presentation.
- `tests/test_art_start_ux.py`: 15 added cases covering both profiles,
  switching, Join/Back, menu contents, New shortcut suppression, macOS/Linux/
  Windows installer combinations, working setup navigation, and invalidation.
- `USER_GUIDE.md`, `docs/REFERENCE_STUDIO_MUSICIAN_GUIDE.md`, `CHANGELOG.md`:
  describe the actual launch and retained Studio behavior.
- `docs/evidence/W01/gate.sh`, `docs/evidence/W01/impl.md`: committed evidence.
  Matching requested copies are in the campaign's `items/W01/` directory.

## Exact validation results

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

## NOT RUN and boundaries

- Physical/live audio, two-Mac collaboration, Webex, native Windows installer
  launch, and physical keyboard/native menu interaction: **NOT RUN**.
- Entire repository Python suite, packaging, signing, and release gates:
  **NOT RUN**; the scoped standard Python gate above ran completely.
- No push, merge, PR, tag, release, Latest change, paid API, or sends.
- Disk check at completion: **18 GiB available**; no build outputs retained.
- Existing older campaign evidence was not used as evidence for this run;
  the previous report is preserved as `items/W01/impl-before-current-run.md`.
