# Cisco manager-demo Help: review packet

Marker: `WEBJAM_SHOW_WAVE_FINAL_BUILD_20260924`.

Keep this PR **OPEN DRAFT**, based on master
`da16749abc9f87cde127e01ea10a7712e5ce7800`, in the sole checkout
`/Users/jeffstory/Documents/webjam`. The PR body records the frozen tip and
its complete local and hosted results; preliminary checks do not qualify a tip.

## Review scope

- Art F1 / More → Help now selects an Art body: Make together, Paint along,
  host and guest video choices, optional Shared Canvas, explicit Conversation,
  local Notes, and Back to room / End Room / Leave Room.
- Silent YouTube lessons and each guest's own local file are distinct. No file
  transfer, video sound, or automatic meeting join is promised. Hidden AI Image
  actions and Music recording, Studio, mixer, and Band Check are not advertised.
- Music Help uses Host / Join. Live Studio reviews completed session takes;
  standalone projects require ending/leaving, relaunching, and choosing launch
  File → New Music Project…, then Play Along / Record or Open Project….
- The modal, branding, geometry, action dispatch, services, and ownership code
  are unchanged. This is Help routing and in-app copy only. The canonical
  in-app guidance and changelog accompany focused regression coverage.

## Verification and boundaries

The pre-freeze widget, brand, and application Help-dispatch suites passed
167 tests plus 52 subtests. The profile-switch regression exercises
Music → Art → Music in one window, checks each body's available actions, and
checks that opening Help preserves the selected workspace. The offline Help
case checks the standalone Music door. Independent source review checked the
actual launch, More, video, and Studio labels and caught the Art Esc behavior:
Esc returns from Paint along before leaving fullscreen.

Source and changed-test Ruff pass. The broader CONTRIBUTING command including
all `tests/` retains eight Ruff findings in two files byte-identical to master:
`test_paint_along_youtube_journey.py` and `test_paint_along_youtube_ui.py`
(imported pytest fixtures). The normal DEVELOPMENT/hosted production lint scope
is unchanged. Preserve these findings; do not repair unrelated tests here.

Eleven real offscreen modal cases passed through F1 and the existing
More → Help action (production controller dispatcher, window-only stand-in).
Music → Art → Music selected the current profile; standalone Music stayed
standalone. All OK buttons closed; before/after workspace, Notes, room, Studio,
and recording/export state matched, with zero side-effect signals. The loaded
conductor SHA-256 was
`6511a0864968d3dde6b6c76ff9e3a317a5a0198838ca2213e6bb9415f3fb48d5`.
Ignored `out/show-wave/cisco/HELP_ROUTING_QA.md`, `help_routing_results.json`,
`help_routing_summary.json`, probe source, HTML, and PNGs retain the full
observations and source hashes. This is synthetic local UI evidence,
not physical audio, a real Art follower, or native macOS focus/feel acceptance.

## Honest leftovers and review stop

- The existing QMessageBox can overflow compact screens after text reflow.
  Dialog sizing/scrolling is outside this explicitly narrowed copy-only draft.
  The probe records this limitation rather than treating full-dialog captures
  as proof that all content fits on a physical display. Music's corrected,
  longer guidance gives a 404×747 frame at (29,164) on offscreen 800×800;
  Art's 404×555 frame fits there but overflows the 760×600 placement fixture.
- More → Paint along… still has a local-file-only tooltip; the unused Art
  profile `quick_help` registry text also predates YouTube. Both remain outside
  the F1 / Help body correction.
- Physical/native demo rehearsal, audio, meeting, Logic import, device feel,
  signing, and notarization are **NOT RUN** in this draft. No source test
  substitutes for final full-wave Bob + Karen QA.
- Accepted open drafts #149 (`3af81dd`) and #152 (`05d46dd`) remain separate.
  SHOW_ONEPAGER.md / DEMO_SCRIPT.md and #152 are not retouched.

After the exact tip passes the complete local gate and all 13 normal hosted
jobs, stop for Bob MATCH + Karen leftover/security/UX review. No merge or
leftover-squash. No publish/tag/Latest until Bob says **FINAL BUILD** after all
MATCHED and full wave QA. No Pages, signing/notarization, additional checkout,
product clone, second WebJam Codex task, or changes to parked #37/#49.
