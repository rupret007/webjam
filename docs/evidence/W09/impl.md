# W09 — Art profile-aware Simple Settings subtitle

## Change

Art Settings now says **“Set your name and optional meeting link.”** Music
and the default profile retain **“Jamulus carries the music. WebJam keeps
your jam organized.”** The existing Art branch continues to omit **Live
music** and **Open Jamulus Audio Settings**.

The controller already supplies `creator_profile_key=self.creator_profile.key`;
no controller or persistence change was needed. This is a conditional copy
change only. Existing name validation, optional keys, Preview labels, and
fail-closed behavior remain intact.

Files:

- `webjam_qt/windows/simple_settings.py`: choose the subtitle by active profile.
- `tests/test_webex_settings_ui.py`: four regression cases exercise the real
  controller settings-opening method with a lightweight controller fixture and
  a real themed Qt dialog (Art/Music × 800×600/1280×800). Check exact subtitle,
  viewport fit, section/button visibility, and reachable name/meeting/Save/Cancel
  controls. Also assert unchanged default and explicit Music subtitles.
- `README_SIMPLE.md`, `CHANGELOG.md`: document the visible Art behavior.
- `docs/evidence/W09/gate.sh`, `docs/evidence/W09/impl.md`: committed copies of
  the requested campaign gate and implementation record.

## UX evidence

Read the campaign UX gate, W09 detail, both audits, `launch_dialog.py`,
`creative_modes.py`, and `test_art_start_ux.py`. The door remains **Make together**
(“Paint, sculpt, 3D print, or just talk.”) and **Paint along** (“Follow a silent
video from a file or lesson link.”), followed by **Host** / **Join**. All 66
start UX tests pass, including the first-screen banned-word/card checks.

Maker-tool research: [Krita's official General Settings documentation](https://docs.krita.org/en/reference_manual/preferences/general_settings.html)
uses concrete settings categories and control names. The chosen sentence names
the two existing tasks in this dialog, following that approach; it is also the
copy suggested by the W09 audit. The attempted Blender documentation fetch was
unavailable; no conclusion relies on it.

Reviewed the supplied `shots/W09/before/settings-800x600.png`. That capture uses
the default Music profile, so its Live music section is expected. Generated
four isolated, offscreen Qt captures in campaign `shots/W09/after/`:
`settings-{art,music}-{800x600,1280x800}.png`. Visually inspected the Art 800×600
capture: subtitle is one line, Live music is absent, and Save/Cancel are visible.
These are UI fixture captures, not physical/live application evidence.

## Exact test results

All commands ran from `/Users/jeffstory/Documents/webjam-24h/W09`, using
`.venv/bin/python` and `QT_QPA_PLATFORM=offscreen`.

Before the product change:

```text
.venv/bin/python -m pytest tests/test_webex_settings_ui.py -q \
  -k settings_opened_from_profile_has_matching_subtitle_and_sections
2 failed, 2 passed, 20 deselected in 1.47s (exit 1)
```

Both Art cases failed on the old music subtitle; both Music cases passed.

After the product change, the same focused command:

```text
4 passed, 20 deselected in 0.86s (exit 0)
```

Executed the requested external gate with bash from the worktree root. It
completed successfully within the 25-minute budget, offline, with no installs,
retries, exclusions, or test changes that weaken assertions. Exact gate results:

| Check | Result |
| --- | --- |
| Focused regression | 4 passed, 20 deselected in 0.70s |
| Ruff, standard product directories | All checks passed; exit 0 |
| compileall, standard directories including tests | exit 0 |
| pip check | No broken requirements found; exit 0 |
| runtime_dependency_policy.py --check | exit 0 |
| ux_smoke_test.py | UX smoke gate passed; exit 0 |
| git diff --check HEAD | exit 0 |
| test_webex_settings_ui.py | 24 passed in 0.70s |
| test_provider_key_settings_ui.py | 14 passed in 0.56s |
| test_first_run_setup.py | 24 passed in 0.82s |
| test_ready_check_ui.py | 11 passed in 0.76s |
| test_zero_key_music.py | 22 passed in 0.67s |
| test_application_controller_video_audio_paths.py | 52 passed, 7 subtests passed in 1.46s |
| test_art_conversation_link_journey.py | 18 passed in 4.96s |
| test_art_start_ux.py | 66 passed in 1.13s |
| test_feel_pass.py | 14 passed in 0.64s |
| Gate final result | W09 gate: PASS; exit 0 |

The nine whole-module runs total 245 passed plus 7 subtests; the focused gate
run repeats four of those cases. Each module ran in a fresh interpreter, matching
the repository's standard Python gate for touched areas. Pip emitted a sandbox
cache-directory warning and disabled its cache; the dependency check passed.
The raw gate output is retained at campaign `items/W09/gate.log`.

## NOT RUN

- Full repository test suite beyond the relevant modules above: **NOT RUN**.
- Physical/live audio, two-Mac sessions, Webex sign-in/talk/share, and human
  interaction/feel checks: **NOT RUN**.
- Packaging, signing, installation, release, or publication: **NOT RUN**.

No external application was launched by the regression fixture; no real session
or audio owner was constructed. No push, merge, PR, tag, release, or Latest
operation was performed. No build outputs were created; initial free disk was
24 GiB (above the 10 GB minimum).
