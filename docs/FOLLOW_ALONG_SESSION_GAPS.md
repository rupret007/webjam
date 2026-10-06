# Follow-along real-session gaps

Honest map of what automated tests prove today versus what a multi-person,
multi-attempt pilot still must observe. Physical audio, cameras, echo, and feel
remain **NOT RUN** unless recorded in
[docs/FOLLOW_ALONG_PILOT_RECORD.md](FOLLOW_ALONG_PILOT_RECORD.md).

## (a) Interrupted and repeated sessions

| Scenario | Covered by tests | Not covered | Green tests cannot prove |
| --- | --- | --- | --- |
| End/Leave then second session same day (same app process) | `test_second_session_rejects_old_requests_and_reuses_saved_lesson` (stale lesson-request context, saved reference via `use_saved_lesson`, no auto media); `test_end_action_retires_host_requests_before_the_cleanup_worker`; `test_failed_leave_retires_request_authority_before_cleanup_wait`; `test_actual_lan_leave_restores_personal_meeting_only_after_cleanup_receipt` | Full host **and** guest UI re-join in one process after End; app restart mid-lesson with OS killing the process | Hearing, Webex sharing, or that both people reach the same meeting without coaching |
| App restart mid-lesson | `test_cold_join_art_leave_and_reopen_preserve_personal_workspace`; saved-lesson library shape in `test_saved_follow_along.py` | Lesson-request or meeting state after kill/relaunch while a room was live | Whether the packaged app restores the same room or only personal workspace |
| Host ends while guest has pending pause | `test_end_action_retires_host_requests_before_the_cleanup_worker`; host navigation retires in `test_host_navigation_or_meeting_replacement_retires_old_request_and_ack` | Guest still in meeting app when host ends WebJam only | Guest experience in Webex after host End |

## (b) Reconnect

| Scenario | Covered by tests | Not covered | Green tests cannot prove |
| --- | --- | --- | --- |
| Guest drop + rejoin (same identity) | `test_guest_rejoin_gets_fresh_admission_without_stale_acknowledgement`; `test_route_loss_drops_requests_and_recovery_requires_explicit_host_entry`; `test_lost_post_response_reconciles_from_get_without_resend_or_inferred_ack` (worker); presence rotation in `test_presence_is_only_refreshed_by_state_read_and_exact_boundary_refuses` | Real Wi‑Fi drop, sleep/wake, VPN change | Latency, audible lesson continuity, camera |
| Guest rejoin with new install identity | `test_new_admission_is_distinct` in `test_lesson_request_model.py` (store level) | Two browsers / two WebJam installs same person | Name display in host notices |
| Host network change | `test_route_loss_drops_requests_and_recovery_requires_explicit_host_entry` | Host IP change with guests still connected | Separate-home reachability |
| Lesson/pause state after rejoin | Fresh admission after presence gap for unacked pause (`core/lesson_request.py` + reconnect journey test) | Host still in browser with video playing | Browser pause/resume |

## (c) Multi-participant

| Scenario | Covered by tests | Not covered | Green tests cannot prove |
| --- | --- | --- | --- |
| Host + 2–3 guests | `test_host_tracks_each_guest_pause_and_acknowledgement_isolated`; widget separation in `test_host_acknowledges_exact_request_and_same_names_remain_separate` | Three simultaneous guests in one LAN server journey | Three real computers, name collisions in Webex |
| Late guest after lesson chosen | `test_late_guest_gets_current_lesson_without_host_rechoosing`; `test_existing_copy_link_and_guest_add_link_fix_without_rejoin` (meeting link) | Late guest physical pilot row | Guest hears one shared lesson |
| One guest leaves | `test_departing_guest_presence_expiry_removes_only_that_notice` | Guest B leaves while A stays | Others unaffected in meeting audio |
| Per-guest pause/ack | `test_guest_pause_host_ack_and_ready_are_human_requests_only`; multi-host test above; `test_other_participant_cannot_use_admission` | 3+ distinct pause rows under load | Host actually pauses browser |

## (d) Accessibility / layout

| Scenario | Covered by tests | Not covered | Green tests cannot prove |
| --- | --- | --- | --- |
| Small window / larger text | `test_lesson_helper_fits_and_scrolls_with_expanded_sound_tips`; `test_initial_art_entry_keeps_both_routes_readable`; `test_connected_music_guest_can_reach_setup_and_leave`; `test_live_door_fits_800x600_with_larger_text`; `test_guest_lesson_controls_fit_with_larger_text` | Screen reader on follow-along surfaces in a 3-person call | VoiceOver/NVDA reading order in live Webex |
| Keyboard | Journey tests use `QTest.keyClick` on primary buttons; lesson request widgets space/enter; `test_art_and_music_doors_are_both_tab_and_arrow_reachable` (Art/Music arrows, start-card Down); guest pause Space in `test_guest_lesson_buttons_have_names_descriptions_and_keyboard` | Full tab order with multiple host notice rows | User can operate without mouse under stress |
| Accessible names on pause rows | `test_host_acknowledges_exact_request_and_same_names_remain_separate`; `test_host_pause_rows_expose_names_and_status`; handoff descriptions in `test_lesson_handoff_actions_keep_accessible_descriptions` | — | Names match real participants |

## (e) Audio path guidance

| Scenario | Covered by tests | Not covered | Green tests cannot prove |
| --- | --- | --- | --- |
| Meeting vs Jamulus vs Shared Track copy | `lesson_setup_steps` / `lesson_sound_guidance` in `core/follow_along.py`; music guest tests in `test_follow_along_music_guest.py`; ensemble journey in `test_follow_along_journey.py` | — | Any route sounds correct |
| One audible lesson / no echo | Copy in handoff and pilot readiness docs; guest “keep your own player closed” in journeys | `test_late_guest` physical row; ensemble latency row | Duplicate YouTube or echo |
| Shared Track + Webex ensemble | `test_two_session_music_integration.py`; `test_composed_music_reconnect.py` (music roster) | Art+music mixed room | Jamulus return path |

## New regression files (this change)

- `tests/test_follow_along_multi_participant.py` — host + two guests + late join; isolated acks; presence expiry.
- `tests/test_follow_along_repeated_session.py` — End then host Start; stale request context; saved lesson reuse.
- `tests/test_follow_along_reconnect.py` — guest loss/reconnect; fresh admission; no false ack.
- `tests/test_launch_keyboard_compact.py` — Art/Music arrow keys, Tab to Host/Join, 800×600 with 20px text, accessible names on door controls.
- `tests/test_follow_along_surface_a11y.py` — guest pause keyboard, host-row names/status, lesson-handoff descriptions, compact lesson controls, missing-file next action.

## Production note

Unacknowledged pause requests now receive a **new admission** after the
participant has been absent longer than the lesson-request presence TTL, so
reconnect does not inherit a stale “accepted” row (see `LessonRequestStore.record_state_read`).
