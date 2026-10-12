# W01 — Dedupe idle “Ready when you are”

Implemented only W01 on `webjam-24h/W01-dedupe-idle-ready-when-you-are`.

## What changed

Idle Music now shows **Ready when you are** and its helper once, in the HUD
beside **Start Session**. The stage card keeps **NOT CONNECTED** and the
existing host/guest recording hint. Its repeated title and helper labels are
hidden and no longer reserve layout space. No new product copy was invented.
The existing helper “Choose Host or Join to begin a rehearsal.” is unchanged;
correcting that instruction belongs to W02.

An explicit `SessionUiState.show_summary` presentation flag defaults to true.
Only ordinary Music IDLE rendering sets it false. Setup, recovery, display
overrides, standalone grids, other profiles, and completed Art-room guidance
retain their summary. The underlying summary and accessible description are
retained; the HUD continues to expose the visible instruction and action.
No session state, audio, recording, Preview, or fail-closed decisions changed.

## Files

- `webjam_qt/session_state.py`: backward-compatible summary visibility flag.
- `webjam_qt/widgets/participant_grid.py`: apply visibility to both labels on
  every render, so later setup/recovery renders restore them.
- `webjam_qt/controllers/application_controller.py`: opt out of the duplicate
  summary only for ordinary idle Music.
- `tests/test_music_idle_summary.py`: real controller/window regressions for
  host and guest at 800×600 and 1280×800, repeated idle/recovery rendering,
  one visible enabled Start Session action, accessible HUD copy, setup
  restoration, and unchanged Art/Podcast/Review summary visibility.
- `README_SIMPLE.md`, `CHANGELOG.md`: document the visible outcome.
- `docs/evidence/W01/gate.sh`, `docs/evidence/W01/impl.md`: committed copies
  of the campaign gate and this report.

## Tests and exact results

Before product edits:

`QT_QPA_PLATFORM=offscreen PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_music_idle_summary.py`

**5 failed in 2.52s**. All four host/guest × size cases counted two visible
“Ready when you are” labels rather than one; the setup-transition test also
failed because the idle title was still visible. These five regressions then
passed after the product change: **5 passed in 1.91s**. Three additional
profile-preservation cases were subsequently added.

Final gate: `bash docs/evidence/W01/gate.sh` — **exit 0, W01 gate: PASS**.
The identical executable is at the requested campaign `items/W01/gate.sh`.
Run from the worktree root, offline; each test module gets a fresh interpreter
as required by CONTRIBUTING.md and CI. No test filtering, skips, retries,
xfails, removed assertions, or weakened gates were introduced.

| Check | Exact result |
| --- | --- |
| `tests/test_music_idle_summary.py` | 8 passed in 2.55s |
| Ruff, product Python areas plus new test | All checks passed; exit 0 |
| Standard `compileall` for core/webjam_qt/ui/services/api/tests | exit 0 |
| `pip check` | No broken requirements found; exit 0 |
| `ux_smoke_test.py` | UX smoke gate passed; exit 0 |
| `tests/test_qt_widgets.py` | 107 passed, 45 subtests passed in 3.27s |
| `tests/test_session_conductor.py` | 12 passed in 0.12s |
| `tests/test_creator_live_presentation.py` | 11 passed in 0.79s |
| `tests/test_unified_musician_guidance.py` | 19 passed in 4.08s |
| `tests/test_jamulus_native_startup.py` | 78 passed in 1.03s |
| `tests/test_session_hud_accessibility.py` | 1 passed in 0.69s |
| `tests/test_art_start_ux.py` | 66 passed in 1.51s |

Total in the gate: **302 passed, 45 subtests passed**, no failures or skips.
Pip reported its normal sandbox cache warning and completed successfully.
The compile cache was temporary and automatically removed.

## UX evidence

Read the campaign UX_GATE.md, both audits, the W01 backlog details, launch
copy in `launch_dialog.py`, Art start definitions in `creative_modes.py`, and
the start-card test contract. The existing door tests and UX smoke gate pass.

Before: campaign `shots/W01/before/music-room-idle-800x600.png`.
After: campaign `shots/W01/after/music-room-idle-{host,guest}-{800x600,1280x800}.png`.
All four after images were captured from the real offscreen controller/window
regression fixture with synthetic role facts and the production stylesheet.
Capture used a temporary pytest plugin; that invocation also completed with
**8 passed in 1.97s**. Visually inspected the guest 800×600 and host 1280×800
images: one headline/helper pair beside Start Session, smaller stage card,
no clipped HUD action. The stage recording hints remain conditional wording,
not claims of recording readiness. Screenshots are offscreen evidence only.

## NOT RUN and scope limits

- Physical/live audio, two-Mac sessions, Webex, native Windows, human
  screen-reader testing, and Jeff's subjective feel review: **NOT RUN**.
- Full repository Python suite, packaging, signing, and publication:
  **NOT RUN**. The standard Python gate was run for the touched presentation
  areas and startup/recovery consumers, plus the mandatory door UX contract.
- No push, merge, PR, tag, release, Latest mutation, paid API, or sends.
  No external processes were stopped. Disk had 24 GiB free at initial check;
  no build outputs were created beyond the removed temporary compile cache.
