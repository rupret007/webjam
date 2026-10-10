# W11 — Recover Join after an oversized numeric peer port

Implemented on `webjam-24h/W11-restore-the-join-form-after-an-oversized`.

## What changed

`core/network_invite.py` converts the validated ASCII peer-port text once,
normalizes a conversion `ValueError` to the existing fixed-copy `InviteLinkError`
with suppressed exception context, and retains the 1–65535 range check.
The existing ingress and Join recovery handlers now receive that error:
the private invitation is cleared, input and Back are enabled, focus returns
to the input, and a corrected invitation can be submitted in the same dialog.

The user sees **Needs attention** and **That invitation is malformed. Copy a
new invitation from your host.** Join stays disabled while the field is empty
and becomes available after pasting the replacement. No new controls or copy
were needed. Bearer text is not echoed in the error, accessibility description,
logs, or saved settings. The Preview and fail-closed wording remain unchanged.

## Files

- `core/network_invite.py`: normalize peer-port conversion failure.
- `tests/test_invitation_ingress.py`: oversized numeric port at parser and
  ingress boundaries; fixed bounded error without bearer reflection.
- `tests/test_host_share_join_flow.py`: Music and Art recovery, enabled controls,
  focus, cleared private input, no settings write on rejection, and successful
  corrected-invite submission using the actual Join button.
- `CHANGELOG.md`, `USER_GUIDE.md`: describe recovery.
- `docs/evidence/w11-join-recovery/{impl.md,gate.sh}`: committed evidence and gate.
  Identical copies are supplied at the requested campaign `items/W11/` paths.

## Tests and exact results

Python 3.12.13; Qt offscreen. Each test module ran in a fresh interpreter as
required by `CONTRIBUTING.md` and CI. No tests were skipped, loosened, or retried.
Each regression uses 5,000 ASCII digits in a valid v2 invite below the 8,192
character paste limit. The local integer-conversion limit was 4,300 digits.

Before the product fix:

- `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_invitation_ingress.py -q -k oversized --tb=short`:
  **2 failed, 28 deselected in 0.12s**, both unnormalized conversion `ValueError`.
- `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_host_share_join_flow.py -q -k oversized --tb=short`:
  **2 failed, 69 deselected in 1.65s**, same error escaping Join for Music and Art.

After the fix, the requested `items/W11/gate.sh` was run from the worktree root:

- Focused ingress: **2 passed, 28 deselected in 0.07s**.
- Focused Join: **2 passed, 69 deselected in 0.97s**.
- `.venv/bin/ruff check webjam_qt/ core/ ui/ services/ api/`: **All checks passed!**
- `.venv/bin/python -m compileall -q core webjam_qt ui services api tests`: **exit 0**.
- `.venv/bin/python -m pip check`: **No broken requirements found.**
  Pip reported its unwritable cache was disabled; no dependency installation ran.
- `.venv/bin/python ux_smoke_test.py`: **UX smoke gate passed.**
- Full ingress module: **30 passed in 0.14s**.
- Full host/share/Join module: **71 passed in 2.26s**.
- Full session-transfer runtime module: **13 failed, 19 passed, 9 errors in 3.21s**.
  All 22 failures/errors were `PermissionError: [Errno 1] Operation not permitted`
  at local socket binding. The gate exited **1**, retaining these tests.
  Sandbox policy forbids escalation; this is not reported as a passing gate.

The remaining gate modules were then run once separately with
`QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest <module> -q`:

| Module | Exact result |
| --- | --- |
| `tests/test_remote_invitation.py` | 85 passed in 0.21s |
| `tests/test_remote_redaction.py` | 6 passed in 0.08s |
| `tests/test_music_lan_host_recovery.py` | 29 passed in 6.76s |
| `tests/test_session_library_launch.py` | 24 passed in 4.73s |
| `tests/test_workspace_navigation.py` | 23 passed in 3.35s |
| `tests/test_art_join_entry_ui.py` | 56 passed in 1.44s |
| `tests/test_art_start_ux.py` | 55 passed in 1.01s |
| `tests/test_launch_keyboard_compact.py` | 4 passed in 0.66s |

`git diff --check`: **exit 0**. Gate script syntax (`bash -n`): **exit 0**.
The gate is offline, uses `.venv/bin/python`, and retains the full touched-area
modules plus standard Python checks. Observed test durations total under one
minute, well below the 25-minute budget. It stops on the first failure.

## UX evidence and limits

Read the campaign UX gate, W11 backlog details, logic/UX audits, launch copy,
creator-profile definitions, and start-card tests. Reviewed the supplied
`shots/W11/before/join-compact.png`. Rendered and visually reviewed actual Qt
recovery at 800×600 with the app stylesheet and synthetic private invitations:

- `shots/W11/after/join-music-recovered-compact.png`
- `shots/W11/after/join-art-recovered-compact.png`

These are offscreen renders, not physical/live observations. No generated artwork
or mock UI was used. Both show the empty focused field, bounded error, Join, and
Back without clipping. Door banned-word/card-count tests passed.

Campaign logs: `items/W11/gate.log` and `items/W11/remaining-tests.log`.
Disk free after verification: approximately **15 GiB**; no large build outputs.

**NOT RUN:** complete repository suite, unrestricted local-socket verification,
physical/live audio, two-Mac joining/networking, real Webex, native Windows,
real video-codec checks, and packaged-app testing. No push, merge, PR, tag,
release, publication, or Latest change.
