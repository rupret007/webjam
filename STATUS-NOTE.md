# Status — novice name field slice (Music door)

## Done

- Music Host shows **Your name** + Jamulus wrap preview before validation; Art still hides the name until validation fails.
- Music Join shows the name field on the join page (block reparented from header); wrap preview stays on Host only.
- Empty name still blocked via existing `validate_jamulus_name` / `_validated_musician_name` copy.
- Ten-second-read harvest (`tests/support/start_ux.py`) unchanged; `test_feel_pass` / `test_art_start_ux` pass.
- Regression: `test_music_shows_the_name_on_the_door_before_validation_art_hides_it`.

## Tests

- Focused pytest (launch/start/join/name): **235 passed** (`test_art_join_entry_ui`, `test_art_start_ux`, `test_feel_pass`, `test_ui_redesign_regressions`, `test_host_share_join_flow`, `test_jamulus_name`).
- Full `tests/` suite not re-run to completion here (very long); no failures in the affected slice.

## UI/UX check

- Music door: person sees name on first screen (Host) and on Join; label hidden on Join to save height; placeholder/accessibility still “Your name”.
- Art door: unchanged (name appears only after failed Host/Join validation).
- Supported narrow Music Join height: **460×520** (was 480 for Art-only join) documented in join layout test.

## Branch

- `draft/cursor-queue-19-webjam-novice-name-field-slice-6ea925c0`
