# STATUS-NOTE — draft/cursor-webjam-ux-implement-top-7e388dd8

**Date:** 2026-09-27  
**Punchlist item:** #1 — Empty error box on the Join page (High / S)

## Done

- `_join_error` starts hidden; shown only when text is set; `_clear_join_error` hides and clears accessible description.
- `_announce_error` and focus routing unchanged.

## Tests (python3.11)

```
python3.11 -m pytest \
  tests/test_art_join_entry_ui.py::test_pristine_join_page_hides_empty_join_error \
  tests/test_art_participant_door.py::test_paste_is_unchecked_and_replacing_it_clears_spoken_errors \
  tests/test_art_participant_door.py::test_failed_save_requests_a_fresh_paste_and_that_retry_succeeds \
  tests/test_host_share_join_flow.py::test_pasted_join_save_failure_is_visible_and_retryable \
  tests/test_host_share_join_flow.py::test_cold_invitation_save_failure_is_visible_on_join_page \
  tests/test_remote_launch_ingress.py -q
```

**Result:** 20 passed (1.39s)

Note: full `test_art_join_entry_ui.py` layout parametrization fails headless on this host (window activation); not run as gate for this change.

## Commit

`778320b` — fix(ui): hide empty Join error label until a message is set

## PR

https://github.com/rupret007/webjam/pull/162 (draft)
