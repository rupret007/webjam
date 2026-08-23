# WebJam quality + product review

**Reviewer:** Grok 4.6 (code quality + product, review-first)  
**Owner:** Jeff Story  
**Tip inspected:** `master` `c51751d` (merge of #21; product tip `5ca6ba5`)  
**Date:** 2026-08-23  

This is a review, not a feature dump. Jeff owns merge and feel. This branch
fixes only P0/P1 items that were clearly wrong and testable. Everything else
is recorded here for Jeff.

## 1. Tip, recent PRs, CI

Recent landings on `master` (all closed/merged):

| PR | What | Notes |
| --- | --- | --- |
| #16 | Jamulus update test clock | Stopped a calendar-date CI red |
| #15 | Studio Visit + reference video | Later overwritten as Art door by #19 |
| #14 | Recording recovery + proof lab | Audio core |
| #19 | Art ten-second door | Three cards; in-room canvas/video/AI |
| #20 | Jamulus ephemeral port hold | Test harness flake |
| #18 | Merge/release map + UX gate | Docs; now stale vs landed #17 |
| #17 | Music song tools + shared clock | Music door stays Host/Join only |
| #21 | Release round report on `5ca6ba5` | Required CI green on run 32578847050 |

Open issues: none.  
Physical / signing / two-Mac / live Music AI: **NOT RUN** (fail closed).  
Do not call Art or Music ready from this review. Automated green is not a
ten-second human read.

`docs/MERGE_AND_RELEASE.md` still says #17 is the open product branch / draft.
That is factually wrong after `5ca6ba5`. The map's tests pin that stale
wording, so this pass leaves the map alone rather than rewrite process history
under Jeff.

## 2. Ten-second UX (source, not a ready claim)

| Room | Source door | Automated hold | Human gate |
| --- | --- | --- | --- |
| Art (`last_creator_profile_key=art`) | Talk & make / Paint together / Paint along, then Host / Join | `tests/test_art_start_ux.py` | Jeff's feel |
| Music (default) | Host / Join only; picker, name, and local studio hidden | `tests/test_legacy_mode_picker_retired.py`, `tests/test_host_share_join_flow.py` | Jeff's feel |

Banned first-screen terms (Studio Visit, Drawpile, Jamulus, host-clocked,
Moises, BYOK, Preview caveats, API) are held for Art cards. Known exceptions
and holes are ranked below. **Neither room is called ready.**

## 3. Ranked findings

### P1 — Art Record / Shared Track chrome failed open (FIXED)

**Evidence:** `webjam_qt/controllers/application_controller.py` set
`set_recording_available(hosting and connected)` and
`set_reference_track_available(hosting)` with no capability check. Art sets
`session_recording=False` and `shared_reference_audio=False` in
`core/creative_modes.py`. Click-path already flashed "unavailable"; the strip
still offered **● Record Session** and Shared Track.

`webjam_qt/widgets/session_strip.py` `_sync_creator_profile_controls` had no
Art branch, so Art inherited Review copy ("review session", playback Preview).

**Fix on this branch:** capability-gate Record and Shared Track in the
controller and again in the strip setters (fail closed even if a caller
passes True). Hide Studio and Recording Setup when the profile has no take /
local project / recording. Art-specific strip copy. Tests in
`tests/test_art_session_strip.py`.

### P1 — Music AI key re-persisted after store migration (FIXED)

**Evidence:** `ProviderCredentials.save()` wrote the OS store and left
`settings.music_ai_api_key` intact. `save_settings()` dumps `asdict(settings)`,
so the next Host/Join or Settings Save wrote the plaintext field again.
`provider_keys.py` claims a key never reaches the settings file.
`keys_changed` is still unconnected; that is no longer required for this path.

**Fix on this branch:** successful store write clears the legacy field and
best-effort persists the cleared settings object. Tests in
`tests/test_provider_credentials.py` and `tests/test_provider_key_settings_ui.py`.

Residual: if persist fails (invalid name / disk), the in-memory field is still
cleared and the next successful save drops the file copy.

### P1 — Default Music door has no visible path to Art (NOT FIXED)

**Evidence:** default `last_creator_profile_key` is `music`
(`core/settings.py`). Music hides the profile combo
(`launch_dialog.py` ~798–800). Settings has no profile control
(`simple_settings.py`). Session-strip legacy picker is retired.
Guest follow of an Art host is in-session only and not persisted
(`application_controller.py` `_apply_creator_profile_key(..., host_owned=True)`).

A first-run musician who wants Art cannot choose it on the first screen.
Adding a "Not music?" control would be a door-chrome change. Jeff owns feel;
this pass does not invent that control.

### P1 — First-screen Preview / Ready labels (NOT FIXED)

**Evidence:** `launch_dialog.py` 491–495 adds `(Preview)` / `(Ready)` to every
combo item. Visible on Art / Podcast / Review doors. Jeff banned Preview
caveats on the first screen. #19 left the one-word marker on purpose and
asked Jeff before dropping it.

### P1 — Review first screen is a caveat wall (NOT FIXED)

**Evidence:** `_CREATOR_LAUNCH_COPY["review_rehearsal"]` embeds "This Preview
does not synchronize…", `MEETING_DIRECT_CAPTURE_BOUNDARY`, and helper
"Preview: host or join a review…". Held by `tests/test_host_share_join_flow.py`.
Copy change is feel; not silently rewritten.

### P2 — Windows first screen can say "Install Jamulus"

**Evidence:** `launch_dialog.py` 528–536. #19 kept the name because it only
appears when the audio path is absent. Conflicts with the first-screen
component-name ban. Left as the documented exception.

### P2 — Podcast first screen offers `New Local Recording`

Intentional GA offline path. Asymmetric vs Music Host/Join only. Not a defect
unless Jeff narrows Podcast to the same door.

### P2 — Stale merge map vs landed #17

`docs/MERGE_AND_RELEASE.md` still treats #17 as open/draft.
`tests/test_merge_and_release_map.py` pins that story. Changelog Unreleased
already says #17 landed. Docs-only reconcile left for Jeff.

### P2 — README five-minute demo omits Art

`README.md` still lists Music / Podcast / Review only. Art is on the launch
combo when the picker is visible.

### P2 — `FirstRunSetupDialog` is dead chrome

`webjam_qt/windows/first_run_setup.py` is not on the `app.py` startup path.
`SimpleSettingsDialog.should_show_on_startup` returns False. Drift risk only.

### P2 — Timing flakes (not touched)

- `tests/test_studio_renderer.py` wall-clock bounds (`< 0.1`, `< 0.2`)
- `tests/test_pocket_stage_gateway.py` `sleep(0.02)` concurrent-offer race

#19/#17 already flagged the Pocket Stage handshake flake. A wrong "fix" could
mask a real pairing race. Left alone.

### P2 — Krita edit path is check-then-use

`core/krita_ai.py` `lstat` then later `Popen`. Local-user TOCTOU. Not a
network boundary. Left alone.

### Not a defect

- Windows `CRED_PERSIST_LOCAL_MACHINE` is per-user-on-this-machine persist,
  not machine-wide across accounts. Do not change to ENTERPRISE (roams).
- Local bridge stays loopback + Host-header check. Unauthenticated by design;
  Art companion projection is deliberately not on this socket.
- Studio Visit user-facing launch strings are gone; `studio_visit` migrates
  to Art. `visual_studio` takes stay Review so they remain playable.
- `save_settings` still round-trips a legacy `music_ai_api_key` that has not
  been moved to the store (upgrade must not drop a working key).

## 4. What this PR changes

Safe, testable fail-closed fixes only. No new doors, no rename, no
integration menu, no auto-post.

## 5. What Jeff still owns

- Human ten-second read of Art and Music doors on an installed build
- Whether Music should reveal a quiet path to Art
- Whether `(Preview)` / `(Ready)` and Review caveat sentences stay on the door
- Merge, tag, and any v0.27 call
- Physical, signing, two-Mac, and live Music AI rows remain **NOT RUN**
