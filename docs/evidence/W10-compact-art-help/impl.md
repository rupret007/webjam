# W10 — Compact Art help first screen

Implemented in `/Users/jeffstory/Documents/webjam-24h/W10` on
`webjam-24h/W10-compact-art-help-first-screen`, 2026-10-12.

## What changed

Art's Current workspace Help now opens with exactly three bullets:

- Choose **Make together** or **Paint along**.
- Choose **Host** and share your invite, or **Join** and paste the host's invite.
- In the room, make with your own tools or on paper, or follow the **Paint along** prompt.

**Shared lessons (expand)** follows those bullets and starts collapsed. It is
an internal disclosure link in the existing scrollable help document; mouse
click or Tab then Enter expands/collapses it. The label and triangle describe
its state. Rendering retains keyboard access to the disclosure. It never opens
an external URL or dispatches workflow navigation. Topic/profile changes reset
it to collapsed. Music help is unchanged.

Advanced instructions retain Copy Invite, optional Shared Canvas, Webex sound
sharing, YouTube browser handoff, late meeting-link copy/add, silent local-file
matching/no-transfer rules, Back to room, End/Leave and shortcuts. Saved-work
and Art-project detail remains in existing searchable topics. No door choices,
Preview labels, playback, file verification or external meeting behavior changed.

## Files

- `webjam_qt/windows/conductor_window.py`: three-bullet Art overview; removes
  duplicate saved-work/project paragraphs from its first screen.
- `webjam_qt/windows/help_dialog.py`: collapsed, keyboard-accessible Art-only
  Shared lessons disclosure and advanced instructions.
- `tests/test_art_help_first_screen.py`: eight cases across 800×600 and
  1280×800; three bullets in door order, banned words absent throughout the
  collapsed body, no scrolling needed, keyboard and mouse disclosure, reset
  on topic/profile changes, no external action, expanded 480×500/22px scrolling.
- `tests/test_help_dialog.py`: retains every existing check and additionally
  checks the new disclosure's focus/anchor before tabbing to Close Help.
- `tests/test_qt_widgets.py`: adds collapsed-content checks, then applies all
  existing Art instruction and Music-exclusion assertions to the expanded body.
- `CHANGELOG.md`, `docs/WORKFLOW_CONTINUITY_PILOT.md`: user-facing help guidance.
- `docs/evidence/W10-compact-art-help/gate.sh` and this report: committed copies
  of the requested campaign `items/W10/gate.sh` and `items/W10/impl.md`.

The campaign paths previously described a different W10 attempt/branch. Those
reports were preserved as `prior-art-help-launch-gate.sh` and
`prior-art-help-launch-impl.md` before replacing the requested output files.
Earlier campaign logs are not evidence for this implementation.

## UX review

Inspected the supplied before screenshot and rendered the actual revised Help
window offscreen. At 800×600 all three bullets, Shared lessons and OK are visible
without scrolling. Advanced instructions enter the same scrollable document only
after expansion; no extra window or second scrollbar was introduced. All banned
door terms are absent from the collapsed body, including YouTube as an additional
regression guard. Existing Art start-card tests pass.

After images (offscreen renderings, not physical device evidence):
`/Users/jeffstory/Documents/bob-overnight-inject/local-webjam-24h-1010/shots/W10/after-compact-help/`
contains collapsed and expanded PNGs for 800×600 and 1280×800. The final 800×600
collapsed rendering was visually inspected.

Maker-tool research required by UX_GATE.md: reviewed the official
[Krita Starting Krita guide](https://docs.krita.org/en/user_manual/getting_started/starting_krita.html),
which begins with concrete actions and links to advanced information. The
implementation follows that concise-start pattern; no tool brand was added to
the Art help door.

## Exact validation

Initial focused regression command against unchanged product code:

```sh
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_art_help_first_screen.py -q
```

**3 failed in 1.66s**, exit 1: no list bullets, no keyboard expansion, no
expander response. The final module adds mouse/enlarged-text coverage and runs
all four cases at both requested display sizes.

Final offline gate, from the worktree root:

```sh
bash /Users/jeffstory/Documents/bob-overnight-inject/local-webjam-24h-1010/items/W10/gate.sh
```

**Exit 0**, final line `W10 compact Art Help gate: PASS`. Completed within the
25-minute budget. Fresh Python interpreters per module follow CONTRIBUTING.md.
Full output: campaign `items/W10/compact-art-help-gate.log`.

| Check | Exact final result |
| --- | --- |
| Focused Art help tests | 8 passed in 2.54s |
| Ruff, standard production directories | All checks passed! |
| Ruff, focused and help-dialog tests | All checks passed! |
| Standard compileall | Exit 0, no output |
| pip check | No broken requirements found. |
| Runtime dependency policy | Exit 0, no output |
| UX smoke | UX smoke gate passed. |
| `test_help_dialog.py` | 28 passed in 8.87s |
| `test_workflow_help.py` | 27 passed in 0.41s |
| `test_workflow_help_integration.py` | 6 passed in 1.99s |
| `test_art_start_ux.py` | 66 passed in 1.09s |
| `test_art_profile.py` | 47 passed in 0.49s |
| `test_creator_live_presentation.py` | 11 passed in 0.47s |
| `test_creative_modes_edge.py` | 39 passed in 0.09s |
| `test_brand_mark.py` | 14 passed in 1.62s |
| `test_theme_qss.py` | 10 passed, 8 subtests passed in 0.68s |
| `test_qt_widgets.py` | 107 passed, 45 subtests passed in 2.34s |
| `test_application_controller_video_audio_paths.py` | 52 passed, 7 subtests passed in 1.69s |
| `git diff --check` | Exit 0, no output |

Total: **415 tests and 60 subtests passed**. Pip reported its normal
non-writable cache warning and disabled caching; dependency checking passed.
An intermediate broad run exposed the old overview-content expectation; all
its assertions were retained for the expanded state before this final run.

## NOT RUN

- Physical/live audio, two-Mac sessions, real Webex sharing or sound, live
  YouTube playback, screen-reader/native VoiceOver feel: **NOT RUN**.
- Full unrelated repository suite, packaging, signing, notarization: **NOT RUN**.
- Push, merge, PR creation, tags, releases, publication: **NOT RUN**.

No large build outputs were created. Disk check showed **23 GiB available**.
