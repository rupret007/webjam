# Queue 21 — novice Jamulus handoff copy

## Result: PASS

## Done
- `_startup_native_sound_setup_message()` is the single source for Music native
  setup HUD detail and `_startup_guidance_override()` projection.
- Copy states Jamulus is the live sound app, Settings → Audio/Network Settings,
  and instrument/mic + headphones; dedicated-profile and automatic advancement
  wording unchanged.
- Bring Jamulus Forward + authenticated advancement unchanged.
- Extended `tests/test_jamulus_native_startup.py` for host/guest × launching/setup
  HUD/guidance parity; updated layout fixture in `test_ui_redesign_regressions.py`.

## Verification (re-run 2026-09-27)
- `python3.11 -m pytest tests/test_jamulus_native_startup.py -q` → 82 passed
- `python3.11 -m pytest tests/test_ui_redesign_regressions.py::test_native_jamulus_setup_guidance_fits_at_760_by_600 -q` → 1 passed
- `python3.11 -m pytest tests/test_unified_musician_guidance.py::test_native_setup_and_exceptional_recovery_share_the_same_copy -q` → 1 passed

## Commit / push
- SHA: `0b94921ea6be34a80d200615cc83ed17f8339adc`
- https://github.com/rupret007/webjam/commit/0b94921
- Branch: `draft/cursor-queue-21-webjam-novice-jamulus-handoff-copy-70f842ee` (origin matches; pr158-fold lease remote verified)
- Open PR: https://github.com/rupret007/webjam/pull/new/draft/cursor-queue-21-webjam-novice-jamulus-handoff-copy-70f842ee

## Remaining risks
- None identified for copy parity; full CI not re-run in this session.
