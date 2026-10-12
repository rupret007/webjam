# W10 — Art Help launch bullets ≤3

Implemented 2026-10-12 in `/Users/jeffstory/Documents/webjam-24h/W10`, branch
`webjam-24h/W10-art-help-launch-bullets-3`.

## What changed

Copy-only product change in `webjam_qt/windows/conductor_window.py`: Art's
**Current workspace** Help overview now has three short launch steps and one
Paint along file-copy step, instead of six dense steps. Visible copy:

1. At launch, choose **Make together** or **Paint along**, then **Host**.
2. In the room, the host uses **Copy Invite**. Guests choose **Join** and paste
   the invite to follow the host's choice.
3. Make with your own tools or on paper. **Conversation** opens setup for
   talking and sharing in Webex.
4. **Paint along file copy:** the host uses **Choose process video…**; guests
   use **Open my copy**. Only a matching local file follows the host. Video
   stays silent in WebJam; talk stays in Webex. Local files are not transferred.

The first step contains no engine names. Detailed lesson setup remains in
the existing searchable **Paint along and play along with sound** topic.
Keyboard shortcuts and saved-work guidance remain available. Preview labels,
file matching, playback, launch choices, and external meeting behavior are
unchanged.

Other files:

- `tests/test_art_help_launch.py`: renders the real Art Help overview, checks
  four numbered steps, bounds each to 45 words to prevent paragraph packing,
  applies the existing banned-word assertion to the first step, and checks
  launch actions, own-tools guidance and silent matching-file/no-transfer copy.
  A second test searches the detailed lesson topic and checks its setup actions.
- `README.md`, `CHANGELOG.md`: describe the shorter overview and lesson search.
- `docs/evidence/W10-art-help/gate.sh`, `impl.md`: committed gate and evidence;
  identical copies at the requested campaign `items/W10/` paths.

The campaign reused W10 for earlier tasks. Its previous source-integrity
gate/report were preserved as `prior-source-integrity-gate.sh` and
`prior-source-integrity-impl.md`. Existing repository `docs/evidence/W10/`
gates/reports were left intact. Earlier W10 logs are not evidence for this task.

## Exact validation

Focused command (before and after the product edit):

```bash
QT_QPA_PLATFORM=offscreen PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_art_help_launch.py
```

- Before: **1 failed, 1 passed in 1.29s**, exit 1. The regression found six
  numbered steps instead of four.
- After: **2 passed in 3.04s**, exit 0.

Completed offline gate from the worktree root:

```bash
bash /Users/jeffstory/Documents/bob-overnight-inject/local-webjam-24h-1010/items/W10/gate.sh
```

Exit **0**, final line **W10 Art Help gate: PASS**, completed within one minute
(below the 25-minute limit). Full output:
`/Users/jeffstory/Documents/bob-overnight-inject/local-webjam-24h-1010/items/W10/art-help-gate.log`.

| Check | Exact result |
| --- | --- |
| New focused module | 2 passed in 1.20s |
| Ruff, standard production directories | All checks passed! |
| Ruff, new test module | All checks passed! |
| Standard compileall | Exit 0, no output |
| pip check | No broken requirements found. |
| Runtime dependency policy | Exit 0, no output |
| UX smoke | UX smoke gate passed. |
| `test_help_dialog.py` | 28 passed in 9.04s |
| `test_workflow_help.py` | 27 passed in 0.41s |
| `test_workflow_help_integration.py` | 6 passed in 1.87s |
| `test_art_start_ux.py` | 65 passed in 1.05s |
| `test_art_profile.py` | 47 passed in 0.47s |
| `test_creator_live_presentation.py` | 11 passed in 0.46s |
| `test_creative_modes_edge.py` | 39 passed in 0.08s |
| git diff --check | Exit 0 |

Total: **225 passed**, no skipped tests. Each module ran in a fresh interpreter,
following CONTRIBUTING.md. Pip disabled its unwritable cache and passed.
Existing Help tests cover compact/enlarged text, keyboard navigation, search,
profile refresh, and preserving work. No tests or gates were weakened.

## UX evidence and limits

Read the supplied backlog/audits, UX_GATE.md, launch copy, creative profiles,
and Art start tests. The gate keeps Art's two starts and Host/Join, checks banned
door words, and retains Preview honesty. For the required maker-tool research,
reviewed the [Krita getting-started manual](https://docs.krita.org/en/user_manual/getting_started/starting_krita.html):
its opening instructions name the visible action before detailed setup.
Applied that action-first pattern to WebJam's existing controls.

Inspected the supplied before image and both new offscreen screenshots:

- `shots/W10/after/help-art_800x600.png`: simulated available display 800×600;
  actual Help widget capture 640×572.
- `shots/W10/after/help-art_1280x800.png`: simulated available display 1280×800;
  actual Help widget capture 640×600.

Paths above are relative to the campaign directory. Captures use the real
ConductorWindow/HelpDialog, production stylesheet and mocked screen geometry,
without an application controller or connection. All four steps and shortcuts
are visible at the initial scroll position. Horizontal scroll maximum is zero
in both captures. Existing saved-work content still scrolls vertically (maximum
148 / 120 respectively); no claim that the entire Help topic fits unscrolled.

**NOT RUN:** full repository suite; packaged/native-window/manual user feel;
physical audio/video playback; two-Mac sessions; Webex or any live meeting.
Offscreen widget checks do not certify those behaviors.

No large builds were created; final disk check reported **26 GiB available**.
No push, merge, PR, tag, release, publication, sends, or Latest changes.
