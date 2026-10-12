# W21 — Local video end leaves Playing state

Implemented on `webjam-24h/W21-local-video-end-leaves-playing-state`, based on
`220eb52`. This report replaces the prior W21 report for the older “Pause timeout
keeps lesson shared” assignment. Only the current local-video EOF item is changed.

## What changed

- The Qt local player reports `EndOfMedia` as `ended`, separately from ordinary
  stopped/paused playback, and still rejects backend errors before reporting it.
- Host refresh consumes that observation, keeps the source shared, and publishes
  `paused` at the known final duration. Duration alone is not treated as EOF.
- Explicit local Play at the end seeks to zero before playing. The existing
  generation guard prevents a withdrawn share from being restarted during seek;
  a failed seek fails closed. Ordinary mid-video pause/resume does not rewind,
  and seeking earlier after completion resumes from the chosen position.
- The existing panel now naturally shows **“Paused.”**, **“0:02 / 0:02”**, and
  **“Play”** for the two-second fixture. Play changes it to **“Playing.”** and
  **“0:00 / 0:02”**. No new controls, copy, wire states, or doors were needed.
- The coordinator regression sends the actual published payload through
  `SessionControlState.publish_reference_video` and into a guest coordinator;
  the guest pauses at EOF and follows replay from zero. This is an in-process
  fake-player/wire-schema check, not a two-computer claim.

## Files

- `core/reference_video.py`: host EOF reconciliation and guarded local replay.
- `webjam_qt/widgets/reference_video_player.py`: Qt playback-state observation.
- `tests/test_reference_video.py`: end/replay, rounded/reset EOF position,
  resumed seek, mid-video pause, explicit EOF proof, failed/retired rewind.
- `tests/test_paint_along_player_health.py`: real Qt enums with controlled backend,
  natural EOF vs stopped/paused/playing, and backend failure precedence.
- `tests/test_reference_video_coordinator.py`: publication and follower regression.
- `tests/test_reference_video_ui.py`: real panel state and Play-button replay.
- `USER_GUIDE.md`, `CHANGELOG.md`: user-visible behavior and evidence limits.
- `tools/gates/w21_local_video_end.sh`: committed reproducible offline gate;
  identical to the requested campaign `items/W21/gate.sh`.
- `docs/W21_LOCAL_VIDEO_END.md`: committed copy of this evidence report.

## Before-change regression evidence

All commands used `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q`:

- `tests/test_reference_video.py -k 'local_natural_end or local_pause_mid or local_replay'`
  — **3 failed, 1 passed, 74 deselected in 0.19s**. EOF remained playing; replay
  did not seek, including failed/retired rewind cases.
- `tests/test_reference_video_coordinator.py -k local_natural_end`
  — **1 failed, 25 deselected in 1.13s**. Published state remained playing.
- `tests/test_paint_along_player_health.py -k 'local_playback_state or local_end_observation'`
  — **5 failed, 29 deselected in 0.51s**. Adapter had no playback-state method.
- `tests/test_reference_video_ui.py -k local_natural_end`
  — **1 failed, 32 deselected in 0.92s**. Actual panel still said “Playing.”.

## After-change results

Ran from the worktree root:

`bash /Users/jeffstory/Documents/bob-overnight-inject/local-webjam-24h-1010/items/W21/gate.sh`

**Exit 1: blocked by sandbox socket permissions; not a passing full gate.**
Log: `items/W21/gate-local-video-end.log` in the campaign directory.
The script retains every touched-area module from the previous gate and adds
EOF regressions, host-opening UI, room-clock, and door coverage. Modules run in
fresh interpreters as required by CONTRIBUTING.md. No skips, retries, xfails,
or relaxed assertions were added.

Focused selections: **8 passed, 74 deselected in 0.16s** (core), **1 passed,
25 deselected in 0.56s** (coordinator), **1 passed, 32 deselected in 0.55s** (UI),
**5 passed, 29 deselected in 0.36s** (adapter). Total **15 passed**.

Standard checks: production-tree Ruff **passed**; Python compileall for
`core webjam_qt ui services api tests` **passed**; pip check **No broken
requirements found**; `ux_smoke_test.py` **UX smoke gate passed**.

Full touched-area modules, in gate order:

| Test module | Exact result |
| --- | --- |
| test_reference_video.py | 82 passed in 0.25s |
| test_reference_video_coordinator.py | 26 passed in 0.41s |
| test_reference_video_ui.py | 33 passed in 0.87s |
| test_paint_along_player_health.py | 34 passed in 0.36s |
| test_reference_video_youtube.py | 24 passed in 0.42s |
| test_youtube_video_player.py | 16 passed in 0.31s |
| test_reference_video_application_integration.py | 37 passed in 0.61s |
| test_paint_along_youtube_journey.py | 1 passed in 0.81s |
| test_paint_along_guest_journey.py | 8 passed in 1.20s |
| test_paint_along_host_opening.py | 22 passed in 0.43s |
| test_paint_along_host_opening_ui.py | 19 passed in 3.36s |
| test_paint_along_copy_recovery_ui.py | 41 passed in 5.85s |
| test_paint_along_seek_input.py | 21 passed in 1.37s |
| test_follow_along_journey.py | 13 passed in 2.57s |
| test_session_transfer_reference_video.py | 4 failed, 26 passed in 0.48s |
| test_room_clock.py | 72 passed in 0.20s |
| test_room_clock_coordinator.py | 27 passed in 0.61s |
| test_art_start_ux.py | 66 passed in 1.15s |
| test_creative_modes_edge.py | 39 passed in 0.08s |

The last four modules were run separately after the gate stopped, each using
`QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest "$test_file" -q`.
Combined full-module results: **607 passed, 4 failed**. Focused selections
repeat 15 of those passing tests; they are not additional unique coverage.

All four failures occur at `socket.bind` with `PermissionError: [Errno 1]
Operation not permitted`: authenticated local-file receipt, authenticated
online-lesson receipt, late-joining artist, and wrong-file artist wire tests.
Their socket-dependent behavior is **NOT RUN**; no product failure is inferred
from the denied bind. The gate intentionally retains these tests and exits
nonzero in this sandbox. It requires a local environment allowing loopback
sockets to pass completely; no Internet or external service is required.

`bash -n` for the gate, byte-for-byte comparison of both gate copies, and
`git diff --check`: **passed**. Disk remained about **26 GiB free**. No large
build outputs were created.

## UX and NOT RUN

Read UX_GATE.md, launch copy, creator starts, and start-card tests. Inspected
the supplied before surface. The door retains **“Make together”**, **“Paint
along”**, then **“Host” / “Join”**; Music remains Host/Join. All 66 start-UX
checks passed. Preview, silent playback, meeting-first sharing, and fail-closed
behavior are unchanged.

Captured offscreen screenshots using a fake two-second player and the real
embedded panel, with no decoding. Visually inspected the 800×600 EOF capture:
“Paused.”, final clock and Play are visible together. Files under campaign
`shots/W21/after/`:

- `local-video-ended-offscreen-800x600.png`
- `local-video-ended-offscreen-1280x800.png`
- `local-video-replay-offscreen-1280x800.png`

**NOT RUN:** real codec EOF/replay, physical video/audio, two-Mac sessions,
Webex, live YouTube, packaged-platform checks, and the entire repository suite.
These offscreen/fake-player checks establish source behavior only. No pushes,
merges, PRs, releases, tags, external messages, or paid APIs were used.
