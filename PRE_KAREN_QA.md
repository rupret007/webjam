# PRE_KAREN — Make together starts from the artist's own space

Base: `2aba2f2f72f94d56f5c7f810fbe7ca326c5502b2` (master after #95).
Branch: `codex/art-copy-first-action`; canonical WebJam checkout only.
Marker: `OVERNIGHT_WEBJAM_ART_20260908_0056`.
BEFORE lease: Bob-the-Bot coord #3, 2026-09-08 01:00 CT.

## Leftover honesty

The Art door is already the locked shape: **Make together** and **Paint along**,
then **Host** or **Join**. The remaining failure is after Host, where Make
together needed to read as a room for any artist, not a softened music session
or a canvas setup. This slice names the already-existing first action instead
of adding a new feature.

The default Art room activity is now **Make from your own space**. Its detail
names paper, clay, a model, a printer, or the artist's usual app, then points
to **Conversation** as the optional talk or screen-share path. That same
promise appears in the Art invite text and first-session docs, because the
invite carries the session to guests.

## Ten-second UX self-QA

- First screen still has exactly Art/Music, then Art's two cards, then Host/Join.
- The door still has no banned engine/vendor/setup words and no fourth card.
- A Make together host gets one current room truth: the room is open, make from
  your own space, and copy the invite.
- A Make together guest does not re-pick the door. After joining, the room says
  their own space is enough and leaves Conversation as the optional talk/share
  route.
- A sculptor, potter, model maker, 3D printer user, painter, or talk-only
  participant can start without a canvas, video, music setup, or file picker.
- Paint along remains the separate silent local-file path. Its guest preparation
  behavior from #95 is preserved and Make together still requires no video setup.
- Compact 720x560 Art room layout stays inside the panel with no scroll in the
  focused production-copy states.

## Security and ownership self-QA

- Production changes are copy-only in the Art overview model, existing startup
  stage hint, and invitation text. No protocol, transport, authentication,
  meeting launch, player, file picker, canvas launcher, recorder, updater,
  package, or release path changed.
- The room still publishes no participant roster beyond existing bounded
  connection-name evidence for hosts. The text names no private meeting URL,
  file path, invite secret, or local project data.
- Conversation remains an explicit panel action. The copy does not launch a
  meeting, capture media, change audio, or claim provider success.
- Optional canvas and Paint along actions still appear only from their existing
  offered room state. Unknown or stale activity targets fall back to the same
  own-space room text with no action.
- Music, Podcast & Voice, and Review & Rehearsal launch and live-audio flows are
  not rewritten.

## Verification

Focused product proof passed: `tests/test_art_start_ux.py`,
`tests/test_art_room_overview.py`, `tests/test_art_room_overview_ui.py`,
`tests/test_art_room_overview_controller.py`,
`tests/test_application_controller_creator_copy.py`, and
`tests/test_paint_along_prepared_copy.py`: **178 passed**. After the invite
copy assertions were aligned, the broader affected group passed **274 tests**.

Ruff across core/webjam_qt/ui/services/api/tests, compileall, pip check,
runtime dependency policy, and git diff --check passed.

Full local proof on the final tree used the repository CI isolation pattern:
every tracked `tests/test_*.py` module, in git order, in a fresh Python
interpreter with `QT_QPA_PLATFORM=offscreen` and cache disabled. Result:
**8,778 passed / 26 skipped / 99 subtests / 5 warnings** across **370 modules**.
Log: `out/art-copy-first-action/isolated-tests.log`; JSON:
`out/art-copy-first-action/isolated-results.json`.

Hosted CI evidence belongs in the draft PR body and coord AFTER after it runs
on the frozen pushed tip.

## Limits and holds

Physical playback, live Webex/Cisco, OS-native focus, installed-app feel,
signing/notarization/platform trust are **NOT RUN**. Codex self-QA is not
independent review or Karen PASS.

Keep the PR OPEN DRAFT. Stop for Karen leftover + security + ten-second UX on
the exact final tip. No merge/squash/tag/sign/Pages/Release Trust/Publish/
release/deploy/spend/live Cisco. Unsigned 0.27.2 stays Jeff-only; never merge
unsigned WebJam. Parked #37/#49 remain untouched. No short-code/public
rendezvous, Music lane, Drawpile/shared-canvas work, second video stack, other
repo, second Goal, or parent injection. Door stays exactly Make together +
Paint along → Host/Join with the squirrel mark unchanged.
