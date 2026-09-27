# Queue 21 — novice Jamulus handoff copy

## Done
- Added `_startup_native_sound_setup_message()` as the single source for Music native
  setup HUD detail and `_startup_guidance_override()` projection.
- Copy now states Jamulus is the live sound app, Settings → Audio/Network Settings,
  and instrument/mic + headphones, with existing dedicated-profile and automatic
  advancement wording unchanged.
- Extended `tests/test_jamulus_native_startup.py` for host/guest × launching/setup
  HUD/guidance parity; updated layout fixture in `test_ui_redesign_regressions.py`.

## Verification
- `python3.11 -m pytest tests/test_jamulus_native_startup.py -q` → 82 passed
- `python3.11 -m pytest tests/test_ui_redesign_regressions.py::test_native_jamulus_setup_guidance_fits_at_760_by_600 -q` → 1 passed
- `python3.11 -m pytest tests/test_unified_musician_guidance.py::test_native_setup_and_exceptional_recovery_share_the_same_copy -q` → 1 passed

## Push
- Draft tip pushed from assigned worktree (branch checked out here; pr158-fold lease
  holds a different branch).
