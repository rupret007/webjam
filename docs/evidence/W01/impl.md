# W01 — Music idle copy names Start Session

Implemented on `webjam-24h/W01-idle-copy-names-start-session` in the assigned W01 worktree. Product commit: `f4101cd` (`Point Music idle guidance at Start Session`).

## What changed

The Music idle sentence is now **“Choose Start Session to begin a rehearsal.”** It names the enabled orange **Start Session** button. The idle Music stage no longer adds a premature recording hint (including the guest's **“Your host can choose Record Session…”**). The sentence fits on one line at both 800×600 and 1280×800.

This is W01 only. The HUD and center card still repeat the same guidance; consolidating those surfaces is W03. Art and other profiles retain their existing guidance. Session actions, permission recovery, Preview labels, and fail-closed evidence remain intact.

Files:

- `core/session_conductor.py`: Music idle sentence.
- `webjam_qt/controllers/application_controller.py`: omit the Music-only idle recording footer.
- `tests/test_creator_live_presentation.py`: update the exact-copy contract and cover all three conductor roles.
- `tests/test_unified_musician_guidance.py`: inspect visible real-window labels and buttons for host/guest settings at both sizes; require the visible enabled primary, matching one-line guidance, no Host/Join words, and no recording footer.
- `CHANGELOG.md`, `USER_GUIDE.md`: describe the visible outcome.
- `docs/evidence/W01/gate.sh`, `docs/evidence/W01/impl.md`: committed copies of the requested campaign gate and report.

## UX evidence

Read the campaign UX gate, W01 details, both audits, launch copy, creative profile registry, and Art start tests. Maker-tool reference: [Audacity's Recording manual](https://manual.audacityteam.org/man/recording.html) directs users to the named Record button. The application here is the same basic copy rule: name the control the person can actually see.

Reviewed the supplied `shots/W01/before/music-room-idle_800x600.png` and these newly captured offscreen images:

- `shots/W01/after/music-room-idle-800x600.png` — 37,015 bytes.
- `shots/W01/after/music-room-idle-1280x800.png` — 41,149 bytes.

Paths are relative to `/Users/jeffstory/Documents/bob-overnight-inject/local-webjam-24h-1010`. Both show **“Ready when you are”**, **“Choose Start Session to begin a rehearsal.”**, and the orange **“Start Session”** control, without Host/Join wording. Only these two images and their manifest were generated for this work; other pre-existing images in that directory are not evidence from this run.

The existing `shot_capture.py` Music fixture was used with its isolated runtime and `microphone_permission_status` mocked to `authorized`. These are offscreen fixtures, not a live launch/audio certification. The window regression uses the same explicit permission fixture; it does not modify product permission behavior.

## Tests and exact results

All Python checks used `.venv/bin/python`. Tests used `QT_QPA_PLATFORM=offscreen`, `PYTHONDONTWRITEBYTECODE=1`, and `-p no:cacheprovider`.

Regression proof against original product code:

- `python -m pytest -q -p no:cacheprovider tests/test_creator_live_presentation.py -k music_idle_guidance_names_its_primary_control`: **3 failed, 11 deselected in 0.17s**, because the original message said Host/Join.
- After completing the window fixture, temporarily restored both product files from the original HEAD, ran `python -m pytest -q -p no:cacheprovider tests/test_unified_musician_guidance.py -k music_idle_window_points_to_visible_start_session --tb=short`, and restored the implementation in a `finally` block: **4 failed, 19 deselected in 2.76s**, all rejecting visible Host/Join wording.
- During test authoring, initial window runs failed at primary visibility because this machine's denied microphone permission displayed recovery copy. A temporary probe also checked the hidden strip control. Corrected the new fixture to mock authorized permission and check the real HUD primary. Those setup failures were not product regressions and were not counted as regression proof.

`bash docs/evidence/W01/gate.sh`: **exit 0**. The identical requested campaign `items/W01/gate.sh` runs offline from the worktree root. It runs focused cases followed by complete relevant modules, each in a fresh interpreter, following the repository's standard Qt/Python test gate.

| Gate step | Exact result |
| --- | --- |
| Focused conductor roles | 3 passed, 11 deselected in 0.12s |
| Focused host/guest windows × two sizes | 4 passed, 19 deselected in 1.98s |
| `test_session_conductor.py` | 12 passed in 0.11s |
| `test_creator_live_presentation.py` | 14 passed in 0.74s |
| `test_unified_musician_guidance.py` | 23 passed in 4.24s |
| `test_musician_guidance.py` | 40 passed in 0.13s |
| `test_creator_profile_guidance.py` | 17 passed in 1.92s |
| `test_art_profile.py` | 47 passed in 0.97s |
| `test_art_start_ux.py` | 55 passed in 1.36s |
| `test_recorder_and_stage_cards.py` | 15 passed in 1.31s |
| `test_conductor_window_fit_to_screen.py` | 5 passed in 1.23s |
| `test_art_session_strip.py` | 6 passed in 0.99s |
| `test_in_room_honesty_goldens.py` | 14 passed in 0.22s |
| `ux_smoke_test.py` | UX smoke gate passed |
| Ruff on both product files | All checks passed |
| compileall on the four changed Python files | exit 0 |
| `git diff --check` | exit 0 |

Total: **248 tests in complete relevant modules**, plus **7 focused cases** repeated explicitly. No skips or xfails in this gate. The gate completed well below 25 minutes. Full output: campaign `items/W01/gate.log`.

Additional checks: `.venv/bin/python -m pip check` reported **“No broken requirements found.”**; `.venv/bin/python -m ruff check webjam_qt/ core/ ui/ services/ api/` reported **“All checks passed!”**; `bash -n` and comparison of the two gate script copies exited 0.

Broad Python gate: **NOT PASS — stopped at an environment failure**. Ran the repository's complete tracked-module sequence (`git ls-files 'tests/test_*.py'`, 508 modules), each with `.venv/bin/python -m pytest "$test_file" -q -p no:cacheprovider`, fresh interpreters, fail-fast, and a 20-minute overall timeout. The first 33 modules completed with **813 passed**, **14 existing skips** (11 in `test_art_companion_http_policy.py`, 3 in `test_art_companion_swift_integration.py`), plus **4 subtests passed**. Module 34, `test_art_lan_host_portable.py`, reported **4 failed in 1.39s** and exit 1 because local listener startup returned **PermissionError** in this restricted sandbox. The remaining **474 modules were NOT RUN** after the fail-fast stop. No test or skip policy was changed. Full log: campaign `items/W01/full-python-gate.log`.

Baseline comparison: temporarily restored the two product files from `f4101cd^`, ran that complete failing module with the same environment and `--tb=short`, then restored the implementation in a `finally` block. Result: **the same 4 failed in 2.13s**, again `Host start failed: PermissionError`, exit 1. Log: campaign `items/W01/baseline-lan-gate.log`. The broad-gate failure is reproducible without W01; it was not bypassed or represented as a pass.

## NOT RUN and limits

**NOT RUN:** real audio, physical devices, two-Mac sessions, Webex, live network sessions, native visual/feel review, packaging, signing, release checks, or paid APIs. Offscreen permission state is mocked. No pushes, merges, PRs, tags, releases, publishing, or sends. No large build output was created; disk had **29 GiB free** at the check after the focused gate.
