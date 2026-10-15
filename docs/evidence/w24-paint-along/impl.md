# W24 — Play when Paint along page is shown

Implemented on `webjam-24h/W24-play-when-paint-along-page-is-shown`.

## What changed

The YouTube player's Play guard now uses Qt's effective widget visibility and
the window's minimized state. The workspace stack hides inactive pages, so a
shown Paint along page can play even when its compact holder clips the
200×200 surface. The previous requirement that more than half the surface be
visible incorrectly rejected that state. The native surface retains its
200×200 minimum size.

Hidden pages, hidden surfaces, hidden windows, and minimized windows still
refuse Play without issuing a browser play command. Showing the page/surface
and restoring the window allows an explicit Play again. Readiness, mute,
browser acknowledgement, and existing pause-on-navigation behavior remain
unchanged.

## UX gate

Read the supplied UX_GATE.md, W24 details, audit reports, launch copy,
creative modes, and start-card tests. Inspected the supplied compact Art-room
before screenshot; it shows the room's entry action, not a loaded native
YouTube surface. No new UI wording or controls were introduced.

Visible words remain **“Play”**, **“Pause”**, and, only when hidden/minimized,
**“Return to Paint along to play this lesson.”** The Art door retains exactly
**“Make together”** and **“Paint along”**, followed by **“Host”** / **“Join”**.
The banned-word/extra-door tests pass. Preview and fail-closed decisions are
unchanged. No new maker-tool copy was invented.

## Files

- `webjam_qt/widgets/youtube_video_player.py`: replace the visible-area guard.
- `tests/test_youtube_video_player.py`: six real-Qt visibility cases using
  the production visibility method, the actual workspace/panel, a QWidget
  stand-in for the native video container, and offline browser responses.
  Two cases focus the current panel and prove its surface is at most half
  visible; four cover hidden/minimized states and recovery.
- `USER_GUIDE.md`, `CHANGELOG.md`: document compact Play behavior.
- `docs/evidence/w24-paint-along/gate.sh`, `impl.md`: committed gate/report,
  also copied to the requested campaign `items/W24/` paths.

The campaign's pre-existing W24 report/gate described an unrelated recording
timeout task. Exact copies were preserved in `items/W24/previous-recording-item/`
before writing this task's requested outputs. No repository test/gate was
removed, weakened, skipped, or marked xfail.

## Tests and exact results

All pytest commands used `.venv/bin/python`, `QT_QPA_PLATFORM=offscreen`,
`PYTHONDONTWRITEBYTECODE=1`, and `-q -p no:cacheprovider`. Full modules use fresh
interpreters, following CONTRIBUTING.md and CI.

Regression selector: `clipped_surface or real_hidden_surface` in
`tests/test_youtube_video_player.py`.

- Before the product change: **2 failed, 4 passed, 16 deselected in 1.63s**.
  Both clipped, focused, current-page cases raised the incorrect return-to-page
  error at Play. Hidden-state cases passed.
- After the change, the full player module: **22 passed in 1.05s**.
- Requested gate invocation: focused cases **6 passed, 16 deselected in 1.24s**.
  The gate exited **1** at the session-transfer module (details below).

| Full module | Exact result |
| --- | --- |
| `test_youtube_video_player.py` | 22 passed in 0.94s |
| `test_youtube_lesson.py` | 30 passed in 0.08s |
| `test_reference_video.py` | 74 passed in 0.25s |
| `test_reference_video_youtube.py` | 24 passed in 0.94s |
| `test_reference_video_coordinator.py` | 25 passed in 0.94s |
| `test_reference_video_ui.py` | 32 passed in 1.23s |
| `test_reference_video_application_integration.py` | 37 passed in 1.12s |
| `test_session_transfer_reference_video.py` | 4 failed, 26 passed in 0.51s |
| `test_paint_along_youtube_ui.py` | 4 passed in 1.21s |
| `test_paint_along_youtube_journey.py` | 1 passed in 1.82s |
| `test_workspace_navigation.py` | 23 passed in 4.53s |
| `test_art_start_ux.py` | 66 passed in 1.92s |

Full-module total: **364 passed, 4 failed**. Focused runs overlap that total.
The last four modules ran independently after the gate stopped, without
altering the gate or omitting any assertions.

All four failures are existing local-socket tests, rejected at server setup
by `socket.bind` with `PermissionError: [Errno 1] Operation not permitted`:

- `test_authenticated_guest_receives_the_host_reference_video_projection`
- `test_authenticated_online_lesson_offer_roundtrips_without_a_file_path`
- `test_a_late_joining_artist_lands_on_the_hosts_current_position`
- `test_an_artist_without_the_hosts_file_stays_in_the_room_without_playing`

The gate retains these tests for an environment that allows loopback binding.
Its full result is **FAILED in this sandbox**, not PASS. Raw gate output:
campaign `items/W24/paint-along-gate-results.txt`.

Remaining standard checks ran independently after the stopped gate:

- `.venv/bin/python ux_smoke_test.py`: **UX smoke gate passed**, exit 0.
- Compileall for `core webjam_qt ui services api tests` using the gate's
  temporary bytecode directory: exit **0**; temporary bytecode removed.
- `.venv/bin/python -m pip check`: **No broken requirements found**, exit 0
  (pip also reported its unwritable cache was disabled).
- `.venv/bin/ruff check webjam_qt/ core/ ui/ services/ api/`:
  **All checks passed**, exit 0.
- `bash -n docs/evidence/w24-paint-along/gate.sh`: exit **0**.
- `git diff --check`: exit **0**.

The offline gate requires no installs or downloads and is scoped well below
25 minutes. No large build outputs were created; available disk remained
25 GiB.

## NOT RUN

- Successful execution of the four loopback tests blocked by the sandbox.
- Full repository suite beyond the listed relevant modules.
- Real YouTube/IFrame playback, native video-container clipping/visual measure
  at 720×560, or physical codec/audio output. Tests model clipping with real
  Qt widget geometry and fixed 40/100px holders; they do not certify WebKit.
- Two-Mac sessions, Webex talk/share, live services, packaged applications,
  physical accessibility/feel checks, or new before/after screenshots.

Only W24 was implemented. No push, merge, PR, tag, release, publication, or
Latest change was performed.
