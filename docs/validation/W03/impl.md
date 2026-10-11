# W03 — Join disabled state readable

Implemented only W03 on `webjam-24h/W03-join-disabled-state-readable` in
`/Users/jeffstory/Documents/webjam-24h/W03`.

## What changed

The empty Join page now immediately says **“Paste an invite to continue”** in
its existing status line. The same helper returns after clearing a pasted
invite, entering whitespace, or returning from Back. A nonempty paste still
says **“Invitation pasted — choose Join”**; validation still happens on Join.
The field stays masked. Existing keyboard order is field → Join → Back in
both empty and pasted states. Empty Join remains blocked.

This is a copy-only product fix: no added controls, theme changes, parser
changes, room changes, or changes to Preview/fail-closed decisions.

Files:

- `webjam_qt/windows/launch_dialog.py`: reuse `_EMPTY_JOIN_PROMPT` for initial,
  reset, and empty-input presentation; use the backlog's explicit helper copy.
- `tests/test_art_join_entry_ui.py`: 12 new cases (four profiles × three empty
  inputs), checking initial display, paste/clear, Back/reopen, blocked state,
  no parsing, and Tab order before and after a paste.
- `tests/test_art_start_ux.py`, `tests/test_art_participant_door.py`,
  `tests/test_host_share_join_flow.py`: exact expectations follow the new copy.
  The old assertion that clearing differs from the activation prompt becomes
  an explicit blocked-state assertion, since both now intentionally show the
  same next step. All existing error, privacy, focus, and invitation checks remain.
- `USER_GUIDE.md`, `CHANGELOG.md`: document the helper and keyboard route.
- `docs/validation/W03/gate.sh`, `docs/validation/W03/impl.md`: committed copies
  of the campaign handoff files.

## UX gate

Read the campaign `UX_GATE.md`, W03 backlog details, UX/logic audits,
`launch_dialog.py`, `creative_modes.py` contract coverage, and start-card tests.
The door/start-card and banned-word tests pass. Visible strings include
**“Join the room.”**, **“Paste an invite to continue”**, **“Paste the invite”**,
**“Join”**, and **“Back”**. The two Art starts and Music Host/Join remain intact.

Maker-tool reference checked before selecting copy:
[Figma's invitation guidance](https://help.figma.com/hc/en-us/articles/1500008023902-Accept-invitations)
uses invitation-led joining. The actual helper wording comes from this backlog.

Reviewed the supplied `shots/W03/before/join-paste-invite_800x600.png`.
Captured six offscreen PNGs under campaign `shots/W03/after/`: Art and Music
at 620×520, 800×600, and 1280×800. Opened and visually reviewed Art 620×520,
Art 800×600, and Music 1280×800: helper and actions fit without clipping.
Qt reported a missing Inter font and used fallback fonts; these are offscreen
source-checkout screenshots, not native/live-session evidence.

## Tests and exact results

Before the product edit:

```sh
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_art_join_entry_ui.py -q -k empty_join_explains_next_step_before_activation
```

**12 failed, 56 deselected in 1.48s**, exit 1. Each failure was the expected
old `Paste your invitation` versus required `Paste an invite to continue`.

After the edit, the standalone Join-entry module passed: **68 passed in 1.90s**.
Then ran the final gate from the worktree root:

```sh
bash /Users/jeffstory/Documents/bob-overnight-inject/local-webjam-24h-1010/items/W03/gate.sh
```

**Exit 0**. Offline gate; fresh interpreter per module as CONTRIBUTING.md
requires. Completed well within 25 minutes. Exact final pytest results:

| Run | Result |
| --- | --- |
| Focused W03 cases | 12 passed, 56 deselected in 1.09s |
| `test_art_join_entry_ui.py` | 68 passed in 1.55s |
| `test_art_start_ux.py` | 55 passed in 0.95s |
| `test_art_participant_door.py` | 33 passed in 0.98s |
| `test_host_share_join_flow.py` | 69 passed in 2.20s |
| `test_launch_keyboard_compact.py` | 4 passed in 0.58s |
| `test_art_cold_invitation_context.py` | 23 passed in 3.37s |
| `test_art_invitation_flow.py` | 30 passed in 2.15s |
| `test_remote_launch_ingress.py` | 10 passed in 0.79s |
| `test_invitation_conversation_ingress.py` | 63 passed in 0.74s |
| `test_theme_qss.py` | 9 passed in 0.24s |

Ten complete modules: **364 passed**, plus the focused **12 passed** run.
No tests skipped or xfailed. The focused selector runs first; its entire module
runs again in the standard gate.

- `.venv/bin/python ux_smoke_test.py`: `UX smoke gate passed.`
- `.venv/bin/python -m compileall -q core webjam_qt ui services api tests`: exit 0.
- `.venv/bin/python -m pip check`: `No broken requirements found.` (nonfatal
  warning about the unwritable global pip cache).
- `.venv/bin/ruff check webjam_qt/ core/ ui/ services/ api/`: `All checks passed!`
- `git diff --check`: exit 0.

Full output is in campaign `items/W03/gate.log`.

## NOT RUN / limits

- Physical/live audio, two-Mac joining, Webex, native screen-reader checks,
  Windows/native UI, human maker ten-second/feel review: **NOT RUN**.
- Full repository test suite and packaged-app testing: **NOT RUN**; the gate
  covers the touched launch/invitation area and its theme/UX contracts.
- Disk was already below the campaign floor before work: 8.1 GiB free;
  subsequent check showed 8.2 GiB. No large builds were created and no
  unrelated files or processes were removed. The >10 GB floor is not met.
- No push, merge, PR, release, tag, publish, or sends performed.
