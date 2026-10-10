# W29 — Art room switch copy

Implemented on `webjam-24h/W29-art-room-switch-copy-not-jam` in the assigned
W29 worktree. Product/test commit: `5c40a13`.

## What changed

The same-LAN invitation switch captures the current creator profile before
asynchronous cleanup. Art uses room vocabulary throughout confirmation,
joining progress, cleanup failure, and invitation-application failure. Music
keeps its existing wording. This is a presentation-only change; invitation
parsing, ownership, cleanup order, default No, and fail-closed retry/disabled
states are preserved.

Words a person sees with Art active:

- **Join this room?**
- **WebJam will safely end your current room, then join the new one.**
- **Joining your room…** / **WebJam is switching the room connection safely.**
- **WebJam couldn’t open the new room safely**
- Cleanup unresolved: **Try End Room** for a host; **Try Leave Room** for a guest.
- Cleanup completed but invite application failed: **The previous room connection
  was stopped, but the new invitation could not be applied safely. Quit and
  reopen WebJam, then open the invitation again.** The Start Room control remains
  disabled, matching the existing recovery behavior.

## Files

- `webjam_qt/controllers/application_controller.py`: bounded profile-aware copy
  in `_accept_band_invitation`, including its asynchronous failure closures.
- `tests/test_art_invite_switch_copy.py`: 14 tests covering Art/Music, host/guest,
  No/cancellation, joining progress, unresolved cleanup, failed invite application,
  and retaining Art wording after a profile changes while a switch is pending.
  Tests use real controller/UI objects and controlled cleanup services; no live
  participant connection or media process is needed.
- `CHANGELOG.md`, `USER_GUIDE.md`: explain the visible Art switch/recovery copy.
- `docs/evidence/W29/gate.sh`, `docs/evidence/W29/impl.md`: committed campaign
  gate and evidence. Identical copies are placed in the requested campaign
  `items/W29/` directory.

## UX gate

Read the campaign `UX_GATE.md`, W29 backlog/audit evidence, launch copy in
`launch_dialog.py`, and Art start definitions in `creative_modes.py`. Inspected
the supplied `art-room-make-together_800x600.png` before image. Start-card tests
and the repository UX smoke gate pass. No doors, optional-tool choices, Preview
labels, or physical evidence claims changed.

Maker-tool reference: [Drawpile Joining Sessions](https://docs.drawpile.net/help/common/joining.html)
uses a direct invite/paste/Join flow. WebJam retains its own established Art
room vocabulary and adds no setup explanation or tool name to the door.

## Tests and exact results

Before the product change, the initial 12-case regression module returned
**6 failed, 6 passed in 1.87s**, exit 1. Art confirmation/progress assertions
failed on the existing “jam” wording; the Music cases passed. Progress was then
split into two independent tests so failure cases reach their failure HUD
assertions, and profile-change coverage was added to the failure cases.

During test development, Ruff caught one imported-fixture F811 diagnostic.
An attempted dynamic-fixture lookup hid the fixture dependency from the repo's
database isolation: that intermediate run returned **14 failed in 3.18s**.
Restored explicit fixture injection and annotated the intentional pytest name
binding. The final gate below passes with repository isolation active.

Final command, run from the worktree root on 2026-10-10:

```sh
bash /Users/jeffstory/Documents/bob-overnight-inject/local-webjam-24h-1010/items/W29/gate.sh
```

**Exit 0; 429 passed across 13 full modules; no failures or skips.** Each module
ran in a fresh interpreter using `.venv/bin/python`, matching the standard
Python test gate's Qt isolation pattern for the touched areas. No test filters,
retries, skips, xfails, or weakened assertions were introduced.

| Module | Exact pytest result |
| --- | --- |
| `test_art_invite_switch_copy.py` | 14 passed in 1.89s |
| `test_host_share_join_flow.py` | 69 passed in 3.38s |
| `test_art_invitation_flow.py` | 30 passed in 2.65s |
| `test_invitation_ingress.py` | 28 passed in 0.13s |
| `test_invitation_conversation_ingress.py` | 63 passed in 0.94s |
| `test_controller_remote_transport.py` | 31 passed in 2.41s |
| `test_runtime_shutdown_fail_closed.py` | 10 passed in 1.27s |
| `test_offline_invitation_gate.py` | 11 passed in 1.10s |
| `test_application_controller_creator_copy.py` | 35 passed in 0.69s |
| `test_art_room_controller.py` | 17 passed in 2.18s |
| `test_art_start_ux.py` | 55 passed in 1.07s |
| `test_art_participant_door.py` | 33 passed in 1.10s |
| `test_art_invitation_entry.py` | 33 passed in 0.14s |

Other final gate commands:

- `.venv/bin/ruff check webjam_qt/ core/ ui/ services/ api/ tests/test_art_invite_switch_copy.py`
  — exit 0, **All checks passed!**
- `.venv/bin/python -m compileall -q core webjam_qt ui services api tests`
  — exit 0, no output.
- `.venv/bin/python -m pip check` — exit 0, **No broken requirements found.**
  Pip reported its usual unwritable user-cache warning; no installation occurred.
- `.venv/bin/python ux_smoke_test.py` — exit 0, **UX smoke gate passed.**
- `git diff --check` — exit 0, no output.

The gate is offline, uses installed dependencies, and completes well below
25 minutes. No build outputs were created. Final observed available disk:
10,820,472 KiB (more than 10 GiB).

## NOT RUN

- Whole-repository Python suite outside the listed touched-area modules.
- New after screenshots, interactive desktop feel, and larger-text visual review.
- Real audio, physical devices, two-Mac joining/switching, and live Webex.
- Packaging, signing, notarization, deployment, and release checks.

No push, merge, PR, tag, release, publish, or Latest change was performed.
