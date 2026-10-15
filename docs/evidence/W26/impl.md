# W26 — Reject playlist embed pseudo-ids

Implemented on `webjam-24h/W26-reject-playlist-embed-pseudo-ids` in the supplied
W26 worktree, based on `220eb52`. Validation date: 2026-10-12.

## What changed

`parse_youtube_lesson_url` now rejects `/embed/videoseries` and
`/embed/live_stream` before their 11-character endpoint names can become video
identities. Rejection uses the existing actionable message:

> Paste a YouTube video link, such as youtube.com/watch?v=….

The rejection applies on all currently supported YouTube embed hosts, with or
without query parameters. It preserves a currently playing lesson and its
published room state. Individual video embeds still accept playlist context and
start timestamps. This is a path validation fix; copy alone could not prevent
the invalid lesson from being loaded/shared. No network lookup is needed.

## Files

- `core/youtube_lesson.py`: reject the two reserved embed paths.
- `tests/test_youtube_lesson.py`: 18 rejection cases spanning two endpoints,
  three hosts and three query variants; one valid video embed control.
- `tests/test_reference_video_youtube.py`: two coordinator regressions proving
  rejection preserves the current lesson, playback and publication history.
- `USER_GUIDE.md`, `CHANGELOG.md`: document the individual-video requirement.
- `docs/evidence/W26/gate.sh`: repeatable offline gate using `.venv/bin/python`.
- `docs/evidence/W26/impl.md`: this report.

The campaign `items/W26/gate.sh` and `items/W26/impl.md` are copies of these
committed evidence files. The previous external W26 gate/report described an
unrelated invitation task; this report records only this run. Existing repository
tests and gates were not removed or weakened.

## Focused red/green evidence

Before changing product code:

```sh
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest \
  tests/test_youtube_lesson.py tests/test_reference_video_youtube.py \
  -q -k embed_pseudo_ids
```

Exit **1**: **20 failed, 55 deselected in 1.39s**. All failures were missing
expected exceptions: 18 `ValueError` cases and two `ReferenceVideoError` cases.

After the fix, the same two modules with
`-k 'embed_pseudo_ids or video_embed_with_playlist_context'` returned exit **0**:
**21 passed, 54 deselected in 0.69s**.

## Final gate results

Executed from the worktree root:

```sh
bash /Users/jeffstory/Documents/bob-overnight-inject/local-webjam-24h-1010/items/W26/gate.sh
```

Exit **0**, ending with `W26 gate: PASS`. Full output is in campaign
`items/W26/gate-current.log`. The gate uses the repository's fresh Python process
per test module convention, with offscreen Qt and installed dependencies only.

| Check | Exact result |
| --- | --- |
| Focused parser tests | 19 passed, 30 deselected in 0.09s |
| Focused coordinator tests | 2 passed, 24 deselected in 0.82s |
| Ruff standard product scope | All checks passed |
| compileall: core, webjam_qt, ui, services, api, tests | Exit 0 |
| pip check | No broken requirements found |
| ux_smoke_test.py | UX smoke gate passed |
| git diff --check | Exit 0 |
| test_youtube_lesson.py | 49 passed in 0.18s |
| test_reference_video_youtube.py | 26 passed in 0.53s |
| test_reference_video.py | 74 passed in 0.36s |
| test_reference_video_coordinator.py | 25 passed in 0.48s |
| test_youtube_video_player.py | 16 passed in 0.46s |
| test_paint_along_youtube_ui.py | 4 passed in 0.79s |
| test_paint_along_youtube_journey.py | 1 passed in 1.03s |
| test_follow_along_handoff_ui.py | 4 passed in 0.53s |
| test_saved_follow_along.py | 14 passed in 4.05s |
| test_rehearsal_plan.py | 14 passed in 0.09s |
| test_art_workspace.py | 19 passed in 0.65s |
| test_art_start_ux.py | 66 passed in 1.23s |

Full touched-area modules: **312 passed**, no failures or skips. The 21 focused
passes are a repeated subset, not additional unique tests. Pip reported that its
user cache directory was unwritable and disabled that cache; dependency checking
succeeded.

## UX gate and evidence boundaries

Read the campaign UX gate, backlog details, both audits, launch copy and creator
start definitions. Reviewed the supplied before image
`shots/W26/before/door-art-paint-along-800x600.png`. Its visible choices are
“Make together”, “Paint along”, “Host” and “Join”; Paint along says “Follow a
silent video from a file or lesson link.” The passing 66-test start UX module
checks the existing door contract and banned-word rules. No launch controls,
Preview labels, meeting ownership or fail-closed copy changed.

- Full repository test suite: **NOT RUN**; the gate covers the touched paths.
- New after screenshots / interactive visual walkthrough: **NOT RUN**; no
  layout or visible copy changed in product code.
- Real YouTube/network playback, physical audio/video, two-Mac and Webex
  sessions: **NOT RUN**. Coordinator coverage uses fake players and a fake peer.
- Packaged builds, signing, releases and publication: **NOT RUN**.

Initial available disk was 24 GiB. No large build outputs were created.
