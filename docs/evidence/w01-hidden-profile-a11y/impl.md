# W01 — Hidden profile selector accessibility

Implemented on `webjam-24h/W01-hide-podcast-review-from-hidden-profile`, based
on `8c55089`. Scope: W01 only.

## Change

The hidden launch profile selector now describes **“Art or Music”**. When the
selector is shown, its description returns to **“Choose Music, Podcast and
Voice, Review and Rehearsal, or Art.”** The same visibility decision controls
both properties, including repeated profile changes.

Visible door copy remains **“Art”**, **“Music”**, **“Make together”**,
**“Paint along”**, **“Host”**, and **“Join”**. Art retains exactly two start
cards; Music retains Host/Join. Profile options, Preview labels and fail-closed
behavior are unchanged. No new wording was invented for visible controls.

Files:

- `webjam_qt/windows/launch_dialog.py`: assign the description with the selector's
  visibility instead of assigning the full profile list during construction.
- `tests/test_art_start_ux.py`: six parameterized regression cases harvest the
  visible door plus hidden selector metadata for Art/Music, and exercise two
  round trips through each legacy profile, preserving its full description and
  canonical options whenever shown.
- `CHANGELOG.md`, `README_SIMPLE.md`: describe the accessibility correction.
- `docs/evidence/w01-hidden-profile-a11y/gate.sh` and this report: reproducible
  offline validation and implementation evidence. Copies are provided at the
  requested campaign `items/W01/gate.sh` and `items/W01/impl.md` paths.

## Tests and exact results

All commands used the worktree's `.venv/bin/python` and offscreen Qt. No test
assertions were removed, loosened, skipped, or marked xfail.

Regression command:

```bash
QT_QPA_PLATFORM=offscreen PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_art_start_ux.py -k 'hidden_profile_picker_a11y or profile_picker_a11y_tracks'
```

- Final regression tests against the original `HEAD` launch implementation:
  **6 failed, 55 deselected in 1.00s**, exit 1. The original description leaked
  Podcast/Review on both doors and after all four legacy-profile round trips.
  The changed source was restored immediately after this controlled check.
- Final implementation, focused gate: **6 passed, 55 deselected in 1.34s**.
- An intermediate empty-description implementation failed the existing
  nonempty-description assertion in `test_host_share_join_flow.py`:
  **1 failed, 68 passed in 2.78s**. Product code was corrected to use the audit's
  proposed **“Art or Music”** alternative; the existing assertion was retained.

Final gate command, run from the worktree root:

```bash
bash /Users/jeffstory/Documents/bob-overnight-inject/local-webjam-24h-1010/items/W01/gate.sh
```

**Exit 0; W01 accessibility gate: PASS.** The complete gate finished in under
one minute, within the requested 25-minute limit. Each complete module ran in
a fresh interpreter, following CONTRIBUTING.md / DEVELOPMENT.md.

| Check | Exact result |
| --- | --- |
| Ruff: `webjam_qt/ core/ ui/ services/ api/` | All checks passed! |
| Compileall: `core webjam_qt ui services api tests` | Exit 0 |
| `python -m pip check` | No broken requirements found. |
| `python ux_smoke_test.py` | UX smoke gate passed. |
| `git diff --check HEAD` | Exit 0 |
| `tests/test_art_start_ux.py` | 61 passed in 1.15s |
| `tests/test_host_share_join_flow.py` | 69 passed in 2.29s |
| `tests/test_ui_redesign_regressions.py` | 34 passed in 3.54s |
| `tests/test_launch_keyboard_compact.py` | 4 passed in 0.65s |
| `tests/test_creative_modes_edge.py` | 39 passed in 0.10s |
| `tests/test_first_run_setup.py` | 24 passed in 0.98s |
| `tests/test_qt_widgets.py` | 106 passed, 45 subtests passed in 2.57s |

Totals: **337 passed and 45 subtests passed** in complete modules, plus the
**6 focused passes**. Pip reported its cache was unavailable in the sandbox;
the dependency check passed. Raw logs are in campaign `items/W01/red-a11y.log`
and `items/W01/gate-a11y.log`.

## Limits / NOT RUN

- Native screen-reader/VoiceOver session: **NOT RUN**; evidence is an offscreen
  Qt metadata harvest, not physical assistive-technology use.
- New screenshots/manual visual review: **NOT RUN**; no visible presentation
  changed, and the complete door/compact keyboard tests passed.
- Real audio, physical video playback, two-Mac, Webex, packaged builds,
  signing/notarization: **NOT RUN**.
- Full repository Python suite: **NOT RUN**; the gate covers the touched launch,
  profile, and Qt areas and standard offline source checks.

No network operations, sends, pushes, merges, PRs, tags, releases or Latest
changes. No large build output was produced. Last disk check showed 18 GiB
available, above the required 10 GB floor.
