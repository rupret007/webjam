# Worth-Building — current guidance for Art guests

2026-09-06 CT. Branch `codex/webjam-finish-product-art-creator-guidance`;
exact origin/master base `743ebb9b2068f2431cfa217876dc2473e8a7f3e4`.
Started from verified `c1431851`; Bob subsequently leftover-squashed #77
(AFTER 5557467434). All local work was preserved and reapplied onto that
verified master, retaining #77 chat ownership and layout changes.
[BEFORE 5557402896](https://github.com/rupret007/Bob-the-Bot/issues/3#issuecomment-5557402896)
claims 01:15:53–05:15:53 CT after reading #3 body/latest BEFORE/AFTER.
The previous #77 slice was completely handed off in AFTER 5557394859:
OPEN DRAFT `efc4caad5c49ba095169a68e21679909a5160e28`, hosted SUCCESS with
four desktop builds in both runs, PRE_KAREN complete and lease released.
Its branch, prior branches and all four stashes remain protected. This is a
fresh branch in `/Users/jeffstory/Documents/WebJam` only.

**Worth-Building: PASS — current-profile creative guidance.** A guest with a
saved Music profile joins an authenticated Art room. The shell and Notes owner
switch to Art, but Creative Pulse still uses Music's cached next step and
summary. Depending on the saved notes, that can mean a sound check or a tighter
groove beside an Art draft. Waiting past the note-edit debounce does not repair
it. An artist must type a new note or trigger an unrelated refresh to see the
current Art guidance. This fails the ten-second test for a sculptor, 3D artist,
or anyone simply joining to talk about their work.

Source: `_apply_creator_profile_key` switches persistence and labels without
refreshing the cached Pulse. Notes restoration deliberately blocks editor
signals to avoid creating a fake edit/save. `_refresh_session_pulse` already
uses the correct active profile and local Notes when called; the missing link
is the completed profile/Notes ownership transition, not a new suggestion
engine or transport. Leaving also restores title metadata after switching the
profile; derived content must correspond to the final current context and
remain honest if restoration fails.

Baseline on the original `c1431851` master uses the actual ApplicationController and
controlled native-room backend, isolated local data, and no external launches:
`/private/tmp/test_webjam_post77_guidance_before.py`, log
`/private/tmp/webjam-post77-guidance-before-corrected.log` — **2 passed in
3.68s**. One defect assertion proves stale Music Pulse after Art adoption and
its repair by an intentional edit. The control proves existing explicit Leave
restores saved Music guidance; preserving that behavior is required. These are
baseline observations, not post-change proof. The first probe attempt used an
over-specific expected sentence and a nonexistent participant method; its two
harness failures are preserved in `/private/tmp/webjam-post77-guidance-before.log`.

Build one bounded slice: keep existing deterministic local guidance aligned
with the active creator profile and its restored Notes, including authenticated
Art adoption, explicit profile changes and return to a saved profile. Refresh
at semantic ownership changes, not on room ticks or media callbacks. Preserve
raw Notes, cursor/selection/undo, pending/failed saves, title ownership and
connection recovery. Repeated same-context room receipts must remain quiet;
late callbacks and shutdown must not publish retired creative context. Failed
derivation must not leave an old profile's guidance beside new Notes.

The strict Leave/rejoin journeys also exposed a related ownership failure:
a previous Music client’s stopped state can fail the new guest attempt while
its current transport is still discovering the authenticated creator profile.
The room later connects as Art, but accepted guidance remains on the old Music
failure. Only a live current guest profile probe may describe enrollment in
progress and audio not started; actual failure, cleanup, retirement and profile
acceptance must end that treatment. Conductor phase/action ownership remains
unchanged, and no Art profile or audio success is guessed.

Verify actual LAN/native and local profile journeys without typing; saved and
pending Art Notes; leaving/rejoining and restoration failure; no artificial
note edits, save writes, editor launches or participant claims; no private
payload in diagnostics/public projections. Run focused Art/door/session/invite
and guidance tests, native UX checks/captures, raw full pytest, required static
checks and UX smoke, independent PRE_KAREN leftover/privacy/security review,
and exact-tip hosted SUCCESS including four desktop builds. One OPEN DRAFT for
Karen, coord AFTER, agent:none / lease cleared, then stop for Bob conductor.

Other audits were thin: Make together already supports own tools and optional
canvas; reviewed participant/Paint along recovery has bounded current-owner
behavior. Suggestion's promise about Notes versus its Krita image handoff is a
separate inherited mismatch, explicitly deferred. Do not invent a new engine,
move the image feature, or rehash #74 publication, #75 dual Open routes, #76
room return, or #77 Talk & share/compact layout.

Completed local proof: 48 new regression cases; final native Cocoa 48 PASS;
focused 2,882 PASS plus 49 subtests; complete raw pytest 8,398 PASS, 26 existing
skips and 99 subtests, exit 0. Four native screenshots show current Art
guidance without post-authentication typing. Required static/UX gates and
independent PRE_KAREN leftover/privacy/security review PASS. Partial controller
fixtures were updated for their deliberately absent Notes renderer without
changing transport assertions. Failed-run history is retained in PRE_KAREN.
Exact-tip hosted four-desktop SUCCESS and one OPEN DRAFT remain the final
handoff gates, recorded in the PR and coord AFTER rather than guessed here.

Holds: Art Preview; own tools; silent local-file Paint along; Webex first-class
beside WebJam and never on the Art door; no second video stack or automatic
meeting/canvas launch. No short-code/public rendezvous, merge/tag/sign/Pages/
Release Trust/release. Unsigned 0.27.2 Jeff-only. Physical/live-provider/
installed-owner-package gates NOT RUN. Parked #37/#49 and #77 untouched; stay
off #67. Black/white/neutral gray/burnt orange. WebJam only.
