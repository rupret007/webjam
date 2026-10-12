# W03 — One primary next action in Paint along room

Implemented in `/Users/jeffstory/Documents/webjam-24h/W03` on
`webjam-24h/W03-one-primary-next-action-in-paint-along-r`, starting at `220eb52`.
This report supersedes the unrelated Music-idle report previously stored in
the campaign's W03 output directory.

An active host who chose Paint along and has neither a video nor a meeting
link configured now sees just **Open Paint along** in the room overview.
**Set Up Conversation** remains available through the existing **More** menu.
The overview Conversation button returns after a video or meeting link is
configured. Guest, Make together, recovery, and closing presentations retain
their existing controls and enabled-state rules.

## Files and behavior

- `core/art_room_presence.py`: name the existing immutable setup record
  `PAINT_ALONG_SETUP`; preserve its copy and five-field projection contract.
- `core/art_room_overview.py`: derive `conversation_deferred` only for the
  active, unconfigured Paint along host setup state.
- `webjam_qt/widgets/art_room_overview.py`: hide the redundant overview
  Conversation action while that state holds; restore visibility on updates.
- `tests/test_paint_along_room_next_action.py`: 14 regression cases cover
  waiting/connected hosts, 800×600 and 1280×800, one visible orange action,
  keyboard activation, action bounds, no scrolling, the More menu route,
  and transitions through configuration, guest, recovery, and closure states.
- `tests/test_art_room_overview_controller.py`: additional assertions exercise
  the production controller's host setup and meeting-link refresh path.
- `USER_GUIDE.md`, `CHANGELOG.md`: document the single overview action.
- `docs/evidence/W03/gate.sh`, `capture.py`, `impl.md`: repeatable offline gate,
  screenshot capture, and this handoff. The gate is also copied to the requested
  campaign `items/W03/gate.sh` location.

No existing assertions, gates, or tests were weakened, removed, or skipped.
The first implementation added a presence field; its strict schema test
rejected that change. The final implementation preserves that schema.

## UX gate

Existing visible words are reused: **Set up Paint along**, **Open Paint along**,
and “Open Paint along, then choose a local video file or a lesson link.”
The secondary route uses the existing **More → Set Up Conversation** labels.
No new product copy or controls were invented. Preview remains visible.

Read the campaign `UX_GATE.md`, W03 backlog details and audit findings, launch
copy, creative-mode start cards, and `test_art_start_ux.py`. Art still has exactly
**Make together** and **Paint along**, then **Host / Join**. The unchanged door
helper is “Host starts Paint along, then chooses a file or lesson link. Join
uses the host's invite.” The start-card and banned-word checks passed.

Inspected the supplied before screenshot and newly captured styled offscreen
screenshots at both requested sizes. The orange action fits without scrolling.
After images are in the campaign `shots/W03/after/` directory:

- `paint-along-next-action-800x600.png`
- `paint-along-next-action-1280x800.png`

Capture command, from the worktree root:

```sh
PYTHONDONTWRITEBYTECODE=1 QT_QPA_PLATFORM=offscreen .venv/bin/python docs/evidence/W03/capture.py /Users/jeffstory/Documents/bob-overnight-inject/local-webjam-24h-1010/shots/W03/after
```

These screenshots use production widgets and locally constructed room facts.
They are not evidence of a connected live session or human feel testing.

## Executed tests and exact results

All pytest invocations used `.venv/bin/python -m pytest -q -p no:cacheprovider`,
`QT_QPA_PLATFORM=offscreen`, and `PYTHONDONTWRITEBYTECODE=1`.

Before product changes, the new focused module produced **14 failed in 2.94s**:
the competing overview button remained visible. After the first implementation
it produced **14 passed in 2.75s**. The initial broader gate then stopped at the
unchanged bounded-presence-schema assertion: **627 passed, 1 failed in 0.72s**.
The final product code fixed that failure without changing its test.

Final gate command: `bash docs/evidence/W03/gate.sh`.
The gate returned **exit 1**, because this managed sandbox denies local socket
binding. It did not skip or suppress those errors. The two later modules and
quality checks were executed separately after the gate stopped.

| Module under `tests/` | Final result |
| --- | --- |
| `test_paint_along_room_next_action.py` | 14 passed in 2.83s |
| `test_art_room_presence.py` | 52 passed in 0.96s |
| `test_art_room_activities.py` | 628 passed in 0.87s |
| `test_art_room_overview.py` | 29 passed in 0.16s |
| `test_art_room_overview_ui.py` | 59 passed in 6.28s |
| `test_art_room_overview_controller.py` | 6 passed in 2.50s |
| `test_art_room_overview_lifetime.py` | 3 passed in 1.16s |
| `test_art_room_activities_ui.py` | 90 passed in 10.21s |
| `test_art_room_controller.py` | 17 passed in 2.75s |
| `test_art_session_strip.py` | 7 passed in 1.26s |
| `test_art_conversation_layout.py` | 32 passed in 4.17s |
| `test_art_conversation_next_action.py` | 32 passed in 5.22s |
| `test_art_video_offer_navigation.py` | 25 passed in 7.63s |
| `test_art_room_connection_names.py` | 6 passed, 13 setup errors in 1.24s |
| `test_art_room_connection_names_journey.py` | 33 passed in 8.91s |
| `test_art_start_ux.py` | 66 passed in 1.68s |

Total final module results: **1,099 passed, 13 setup errors**. All 13 errors are
`PermissionError: [Errno 1] Operation not permitted` from binding the fixture's
`127.0.0.1` server. Socket-dependent assertions are **NOT RUN**; rerun the
unchanged gate in a local environment that permits loopback sockets.

Additional checks, run with `.venv/bin/python`:

- `ux_smoke_test.py`: **UX smoke gate passed.**
- `-m ruff check` on the three changed product modules, both changed test
  modules, and capture script: **All checks passed!**
- `-m compileall -q` on the three changed product modules and both changed
  test modules: exit **0**.
- `-m pip check`: **No broken requirements found.** (Pip cache disabled by
  filesystem permissions.)
- `git diff --check`, `bash -n docs/evidence/W03/gate.sh`, and comparison of
  the committed and campaign gate copies: exit **0**.

Campaign logs: `paint-along-gate-initial.log`, `paint-along-gate.log`,
`paint-along-names-journey.log`, and `paint-along-door.log` in `items/W03/`.
The gate follows the repository's fresh Python interpreter per test module,
uses installed dependencies offline, and retains the socket tests as failures
when unavailable. No unrelated full-repository test run was attempted.

## NOT RUN and scope limits

Real audio, physical/live video playback, two-Mac sessions, Webex, packaged
builds, signing, notarization, and physical UX/feel: **NOT RUN**.
No external opens, sends, paid APIs, pushes, merges, PRs, tags, releases, or
Latest changes. No unrelated processes were stopped. No large build outputs
were created; filesystem free space was 24 GiB at the final check.
