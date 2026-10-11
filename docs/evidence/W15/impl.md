# W15 — Legacy invite from Art opens Music

Implemented on `webjam-24h/W15-pasting-a-legacy-v1-invite-from-the-art` in the assigned worktree. Product commit: `b5a6903`. Local commits only.

## What changed

A parsed non-peer v1 `BandInvite` now borrows the Music profile before startup, both at cold launch and when accepting an invitation in an existing window. `begin_startup_journey()` follows the existing Music guest path instead of reopening Join. No peer enrollment, Local Originals capability, or connection success is invented. The saved profile is preserved and restored on leave.

If a pending Library media operation refuses the profile change, acceptance stops without launching audio or reopening Join. The user sees: **This invite is for a music jam. Finish the workspace operation, then join again.** After the operation finishes, the same invitation can be accepted.

Files:

- `webjam_qt/controllers/application_controller.py`: borrow Music at the two v1 entry points; respect deferred profile changes.
- `tests/test_art_cold_invitation_context.py`: five new cases covering cold/running entry from Art/Music and deferred workspace operations. Assertions include no `_paste_new_invitation()` call, one guest launch, no host/peer launch, saved profile retention, and leave restoration.
- `CHANGELOG.md`, `README_SIMPLE.md`: describe the compatibility behavior.
- `docs/evidence/W15/impl.md`, `docs/evidence/W15/gate.sh`: versioned campaign deliverables. Matching copies are written to the requested external `items/W15` directory.

## UX gate

Read campaign UX_GATE.md, backlog W15 details, both audit reports, launch copy, creative start definitions, and start-card tests. No door/layout changes: Art still shows **Make together** / **Paint along**, then **Host** / **Join**; Music shows **Host** / **Join**. Join's Art heading remains **Join the room.** The 55 start-card/first-screen tests and UX smoke pass, including banned-word checks. Preview and fail-closed evidence requirements remain intact.

Maker-tool reference: [BandLab's collaboration invitation help](https://help.bandlab.com/hc/en-us/articles/115002945253-How-do-I-invite-other-users-to-collaborate) uses invitations tied to the host's project. This supports keeping the guest's action as Join and letting the invitation select the session; no new profile picker was added.

## Exact validation

Before product changes, the new four-case regression command:

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_art_cold_invitation_context.py -q -k legacy_invite
```

Result: **4 failed, 23 deselected in 1.73s**, exit 1. Both Art cases failed because `_paste_new_invitation()` was called once. Both Music cases failed because the session did not borrow the profile. After the fix those four cases passed; the fifth deferred-operation case was then added.

Final focused + touched-area standard Python gate:

```bash
bash /Users/jeffstory/Documents/bob-overnight-inject/local-webjam-24h-1010/items/W15/gate.sh
```

Result: **exit 0**, comfortably under 25 minutes. Offline, using `.venv/bin/python` and a fresh interpreter for each Qt module, following CONTRIBUTING.md/CI. **672 passed across 24 complete modules**, plus **5 passed / 23 deselected** for the separate focused invocation (those five are also in the complete module run). No failures or skips in this gate. Full-source Ruff: **All checks passed**. Compileall: **exit 0**. Pip check: **No broken requirements found**. UX smoke: **passed**. `git diff --check`: **exit 0**. Pip warned that its user cache was not writable and disabled that cache; no installation/network was used.

| Test module / command scope | Exact result |
| --- | --- |
| `Focused legacy-invite regression` | 5 passed, 23 deselected in 1.28s |
| `tests/test_art_cold_invitation_context.py` | 28 passed in 3.58s |
| `tests/test_art_start_ux.py` | 55 passed in 1.10s |
| `tests/test_art_invitation_entry.py` | 33 passed in 0.10s |
| `tests/test_art_invitation_flow.py` | 30 passed in 2.03s |
| `tests/test_art_room_controller.py` | 17 passed in 1.67s |
| `tests/test_art_pending_profile_facts.py` | 17 passed in 2.83s |
| `tests/test_art_profile.py` | 47 passed in 0.56s |
| `tests/test_creator_profile_guidance.py` | 17 passed in 1.44s |
| `tests/test_jamulus_native_startup.py` | 78 passed in 0.56s |
| `tests/test_host_share_join_flow.py` | 69 passed in 2.27s |
| `tests/test_invitation_ingress.py` | 28 passed in 0.10s |
| `tests/test_offline_invitation_gate.py` | 11 passed in 0.90s |
| `tests/test_invitation_conversation_lifetime.py` | 9 passed in 1.47s |
| `tests/test_rejected_invitation_journey.py` | 10 passed in 3.53s |
| `tests/test_controller_remote_transport.py` | 31 passed in 2.19s |
| `tests/test_remote_host_loopback_binding.py` | 12 passed in 0.77s |
| `tests/test_remote_launch_ingress.py` | 10 passed in 0.78s |
| `tests/test_composed_invitation_music_mix.py` | 4 passed in 1.15s |
| `tests/test_music_lan_guest_handoff_recovery.py` | 22 passed in 1.81s |
| `tests/test_local_originals_choice_profiles.py` | 20 passed in 0.70s |
| `tests/test_session_library_launch.py` | 24 passed in 4.19s |
| `tests/test_session_library_coordinator.py` | 28 passed in 1.56s |
| `tests/test_application_controller_band_check_gate.py` | 38 passed in 0.83s |
| `tests/test_recovery_lifecycle_contract.py` | 34 passed in 2.10s |

## Repository-wide attempt and environment limit

Also ran the unfiltered repository-wide standard loop, one tracked `tests/test_*.py` file per interpreter, stopping on first failure as CI does:

```bash
git ls-files 'tests/test_*.py' | while IFS= read -r test_file; do
  QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest "$test_file" -q || exit $?
done
```

Result: **exit 1**, 34 modules reached; **818 passed, 14 existing skips, 4 failed**. The four failures are all in `tests/test_art_lan_host_portable.py`: `test_full_art_invite_bootstrap_keeps_conversation_temporary_and_notes_local`, `test_make_together_host_copy_join_and_leave`, `test_missing_address_then_bind_failure_retry_keeps_host_fail_closed`, and `test_paint_along_host_copy_join_and_leave_without_opening_media`. Every failure reports **Host start failed: PermissionError** while creating the LAN listener.

Baseline check: temporarily loaded the exact pre-change controller from `b5a6903^`, ran that unchanged module, then restored the committed controller in a `finally` block. Result: **the same 4 failures in 1.92s**, exit 1, with the same listener `PermissionError`. No test assertion, skip, gate, or socket policy was modified. The full suite is **not PASS**; remaining 474 modules after the fail-fast stop are **NOT RUN** in that attempt. Separate relevant-module results above remain valid.

Logs beside this report: `gate-results.txt`, `full-suite-results.txt`, `baseline-lan-results.txt`.

## NOT RUN

Real audio, live Jamulus connection, two-Mac hosting/joining, Webex, physical devices, installed-package checks, and physical UX/feel: **NOT RUN**. No new screenshots were captured; this change is controller routing, and the UI checks were offscreen automated tests. No connection or audibility claim is based on those tests. No push, merge, PR, tag, release, publication, or Latest modification was performed.

Disk remained at 11 GiB available in `df -h .` (above 10 GB). No large build outputs were created.
