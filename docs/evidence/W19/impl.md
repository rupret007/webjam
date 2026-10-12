# W19 — Show restart after poll-loss disarm

When an Art guest’s lesson-request poll fails once, `LanRoomGuest` correctly disarms
request authority (`_lesson_enabled = False`) while the LAN room binding can stay
current and the next poll can succeed inside the connection grace window. Before
the fix, `RoomParticipantController.project_lesson_requests()` only surfaced
**Restart pause requests** when `_lesson_current(binding)` became false (connection
loss), so a disarmed-but-still-connected guest saw **Ask the host aloud.** with
Pause/Ready disabled and no recovery path except leave/re-enter.

## What changed

- `services/lan_room_guest.py`: read-only `lesson_requests_enabled` property exposing
  whether the guest worker still holds lesson-request authority after arming.
- `webjam_qt/controllers/room_participant.py`: `disarmed_guest` branch in
  `project_lesson_requests()` — when binding is still current for a guest but
  `lesson_requests_enabled` is false, take the same retire/wire-none path as a
  stale binding and show the existing **Restart pause requests** control (no
  auto-rearm on a successful GET; explicit click still calls
  `activate_lesson_requests`).
- `tests/test_art_lesson_request_journey.py`:
  `test_guest_restart_reappears_after_transient_poll_failure_within_grace` — induces
  one failed `state_with_lesson_requests` poll, confirms the next poll restores
  connection but not submit authority, asserts restart visibility and that click
  re-enables requests without replaying abandoned intents.

Product fix landed in commit `ee3e32b7af0f2e32ef22204f9731dc4d5ef819cb` on master.
This worktree branch carries that code; campaign evidence adds `docs/evidence/W19/`.

## Files (product)

| Path | Role |
|------|------|
| `services/lan_room_guest.py` | `lesson_requests_enabled` property |
| `webjam_qt/controllers/room_participant.py` | `disarmed_guest` → restart visibility |
| `tests/test_art_lesson_request_journey.py` | Regression journey test |
| `webjam_qt/widgets/lesson_handoff.py` | Existing **Restart pause requests** button (unchanged) |

## Fail-before behavior

Without the `disarmed_guest` branch, `_lesson_current(binding)` remained true after
a transient poll failure once `connection_available` returned, so projection stayed
on the guest active path, hid restart, and left controls disabled —
`test_guest_restart_reappears_after_transient_poll_failure_within_grace` fails on
`assert panel.lesson_handoff.restart_button.isVisibleTo(app.window)` (verified by
reverting the `disarmed_guest` condition locally during development; not committed).

## Exact validation results

Run on **2026-10-12** from `/Users/jeffstory/Documents/webjam-24h/W19` on branch
`webjam-24h/W19-show-restart-after-poll-loss-disarm` at `220eb52` (includes `ee3e32b`).

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m ruff check webjam_qt/controllers/room_participant.py services/lan_room_guest.py
```
**All checks passed.**

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_art_lesson_request_journey.py::test_guest_restart_reappears_after_transient_poll_failure_within_grace -q
```
**1 passed in 3.17s**

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_art_lesson_request_journey.py -q
```
**18 passed in 18.49s**

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_lan_lesson_request_worker.py::test_poll_loss_disarms_and_abandons_intent_until_explicit_helper_entry -q
```
**1 passed in 0.18s**

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_lan_lesson_request_worker.py -q
```
**46 passed in 0.15s**

Full gate: `docs/evidence/W19/gate.sh` — **PASS** (same commands as above).

## NOT RUN

Real audio, two-Mac LAN, Webex, physical devices, and live reference-service HTTP.
Before screenshots under campaign `shots/W19/before` were not re-captured in this pass.
