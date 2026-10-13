# W11 — Review1 fix round

Fixed on 2026-10-13 in `/Users/jeffstory/Documents/webjam-24h/W11`, branch
`webjam-24h/W11-art-settings-drops-jamulus-and-verify-so`, starting at `44e54ac`.

## Review issues addressed

- The full `JamulusNamePreview` layout now honors `plain_words=True`, showing
  **Others see: Alex** instead of **Mixer preview: Alex**. This reuses the
  compact launch widget's existing wording; no new maker copy was invented.
  Music keeps its existing help and **Mixer preview** line. Name validation,
  wrapping and fail-closed errors are unchanged.
- Also addressed the review's optional accessibility note: Art's optional-key
  disclosure announces its visible **Song tools and writing help keys
  (optional)** label instead of the engine name **Music AI**. Music's label
  remains unchanged.
- Strengthened `tests/test_art_settings_ux.py` without removing any assertions:
  the shared first-screen banned-word gate now checks visible and accessible
  copy (including tooltips and buttons) at both sizes. Exact name-display and
  accessible-description assertions cover short, wrapped and combining-Unicode
  names. Music wording is explicitly protected for both Verify Sound flags.
- Updated USER_GUIDE.md and CHANGELOG.md. The existing `gate.sh` already runs
  every affected module and needed no change; campaign and committed copies
  remain identical.

## Fix-round validation

Before the product fix, the strengthened full Art Settings test module returned
**5 failed, 5 passed in 1.12s**: two first-paint failures caught **Music AI** in
accessible copy; three exact-text failures caught **Mixer preview**. Evidence:
campaign `items/W11/fix-round-red.log`.

After the product fix, ran:

`bash /Users/jeffstory/Documents/bob-overnight-inject/local-webjam-24h-1010/items/W11/gate.sh`

**PASS, exit 0: 375 tests passed, 7 subtests passed.** Full modules ran in fresh
interpreters; no tests or gates were weakened, skipped, deleted or xfailed.

| Check | Exact result |
| --- | --- |
| `tests/test_art_settings_ux.py` | 10 passed in 0.84s |
| `tests/test_webex_settings_ui.py` | 20 passed in 0.63s |
| `tests/test_jamulus_name.py` | 22 passed in 0.08s |
| `tests/test_provider_key_settings_ui.py` | 14 passed in 0.52s |
| `tests/test_recording_studio.py` | 134 passed in 5.49s |
| `tests/test_art_conversation_link_journey.py` | 18 passed in 4.84s |
| `tests/test_application_controller_video_audio_paths.py` | 52 passed, 7 subtests passed in 1.46s |
| `tests/test_art_start_ux.py` | 66 passed in 1.05s |
| `tests/test_creative_modes_edge.py` | 39 passed in 0.09s |
| Product and focused-test ruff checks | All checks passed! |
| compileall | exit 0 |
| pip check | No broken requirements found. |
| ux_smoke_test.py | UX smoke gate passed. |
| git diff --check | exit 0 |

Full log: campaign `items/W11/fix-round-gate.log`. Pip again emitted only its
nonfatal unwritable-cache warning.

## UX evidence and limits

Read UX_GATE.md and the launch/start-card sources and tests. Art still starts
with **Make together** / **Paint along**, then **Host** / **Join**. The complete
66-case start suite passed. No Preview honesty labels or fail-closed copy changed.

Fresh offscreen PNGs and visible/accessibility text harvests for Art and Music
at **800×600** and **1280×800** are in campaign
`shots/W11/fix-round-art-settings/`. Both Art screenshots were visually inspected:
**Your name and optional meeting link.**, the existing plain hint, **Others see:
Alex**, **Meeting link (optional)**, optional keys, **Cancel**, and **Save** fit.
The captures also assert unclipped identity controls and the shared banned-word
gate for Art. Credentials were isolated and no meeting was opened.

**NOT RUN:** native-window/VoiceOver interaction, physical/live audio, two-Mac
checks, live Webex, packaging, and repository tests beyond the modules above.
No push, merge, PR, tag, release, publication or sends. No large build outputs
were created; approximately **18 GiB free** remained.

---

The initial implementation report below is historical. Its retained
**Mixer preview** wording and old test totals are superseded by this fix round.

# W11 — Art Settings drops Jamulus and Verify Sound

Implemented on 2026-10-13 in `/Users/jeffstory/Documents/webjam-24h/W11`, branch
`webjam-24h/W11-art-settings-drops-jamulus-and-verify-so`, baseline `220eb52`.

## What changed

- `webjam_qt/windows/simple_settings.py`: Art's subtitle is **Your name and
  optional meeting link.** The name editor's accessible name is **Your name**.
  Art uses `JamulusNamePreview(..., plain_words=True)` and thus the existing
  `PLAIN_NAME_HELP`: **Others see up to 16 characters and wrap after 8. Use a
  short stage name for one line.** Its preview is accessible as **What others
  see**. The shared name limits and preview behavior remain intact.
- Art never creates the **Verify Sound** button, including when the caller
  leaves `show_band_check_action=True`. Its footer offers **Cancel** and
  **Save**. Music retains its existing subtitle, name help, live-music section,
  accessible name, and optional Verify Sound action.
- `tests/test_art_settings_ux.py`: eight focused cases cover first paint at
  800×600 and 1280×800, unclipped identity controls, plain accessible help,
  preview updates, both profiles with both Verify Sound flag values, and real
  temporary-file saving of Art identity and meeting preferences without a
  sound check or external meeting launch.
- `USER_GUIDE.md` and `CHANGELOG.md`: document the Art Settings behavior.
- `docs/evidence/W11/gate.sh` and this report: committed reproducible gate and
  evidence. Identical copies are in the requested campaign `items/W11/` path.
  The old reports for the previous Music-door assignment were preserved there
  as `previous-music-door-gate.sh` and `previous-music-door-impl.md`.

## Exact test results

Before changing product code:

`QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q tests/test_art_settings_ux.py`

**4 failed, 4 passed in 1.24s**, exit 1. Failures identified the old subtitle at
both sizes, the musician accessible name, and Verify Sound offered for Art.

After changing product code, the same focused command returned **8 passed in
1.15s**, exit 0.

Then ran from the worktree root:

`bash /Users/jeffstory/Documents/bob-overnight-inject/local-webjam-24h-1010/items/W11/gate.sh`

**PASS**, exit 0, comfortably below 25 minutes. The script uses the existing
`.venv/bin/python`, makes no installs or network requests, and follows
CONTRIBUTING.md / CI by running each full relevant test module in a fresh
interpreter. No tests or assertions were removed, skipped, or loosened.

| Check | Exact result |
| --- | --- |
| `tests/test_art_settings_ux.py` | 8 passed in 1.16s |
| `tests/test_webex_settings_ui.py` | 20 passed in 0.70s |
| `tests/test_jamulus_name.py` | 22 passed in 0.11s |
| `tests/test_provider_key_settings_ui.py` | 14 passed in 0.54s |
| `tests/test_recording_studio.py` | 134 passed in 5.90s |
| `tests/test_art_conversation_link_journey.py` | 18 passed in 4.87s |
| `tests/test_application_controller_video_audio_paths.py` | 52 passed, 7 subtests passed in 1.49s |
| `tests/test_art_start_ux.py` | 66 passed in 1.14s |
| `tests/test_creative_modes_edge.py` | 39 passed in 0.09s |
| `python -m ruff check webjam_qt/ core/ ui/ services/ api/` | All checks passed! |
| `python -m ruff check tests/test_art_settings_ux.py` | All checks passed! |
| `python -m compileall -q core webjam_qt ui services api tests` | exit 0, no output |
| `python -m pip check` | No broken requirements found. |
| `python ux_smoke_test.py` | UX smoke gate passed. |
| `git diff --check` | exit 0, no output |

Total: **373 tests passed, 7 subtests passed**. Pip emitted a nonfatal warning
that its user cache directory was unwritable and disabled the cache.
Full log: campaign `items/W11/art-settings-gate.log`.

## UX evidence and limits

Read the campaign UX_GATE.md, W11 backlog details, UX/logic audits, and the
launch copy/start-card sources (`launch_dialog.py`, `creative_modes.py`,
`test_art_start_ux.py`). Art keeps **Make together** / **Paint along**, then
**Host** / **Join**. The existing complete start suite passed, including the
banned-word gate and two-card checks. No new maker copy was invented; W11's
specified subtitle and the existing plain help were reused.

Inspected the before Art Settings screenshot and fresh offscreen Art captures
at **800×600** and **1280×800**. Subtitle, identity and hint fit; the footer has
Cancel and Save. Captured Music at both sizes as well. PNGs and text harvests
are under campaign `shots/W11/after-art-settings/`, named
`settings-{art,music}-{800x600,1280x800}.{png,txt}`. These are Qt fixture images,
not live-session evidence. Credentials were isolated; no meeting was opened.
The shared plain help still uses its existing “stage name” / “Mixer preview”
wording; W11 switches to that existing contract without changing it globally.

**NOT RUN:** real audio, physical/live sessions, two-Mac checks, live Webex,
interactive screen-reader checks, packaging, and the entire repository suite
outside the relevant modules above. Art Preview labels and fail-closed
behavior are unchanged. No push, merge, PR, tag, release, or publication.
No build outputs were created; disk retained approximately **19 GiB free**.
