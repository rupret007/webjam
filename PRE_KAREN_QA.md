# Pre-Karen QA — return to either Art activity

2026-09-05 CT. Branch `codex/webjam-finish-product-art-guest`; verified base
`2b84e5acd94ff7dee327e79480351334c5e15977` (post-#74 squash).
[Initial BEFORE](https://github.com/rupret007/Bob-the-Bot/issues/3#issuecomment-5556059873)
and [master-refresh BEFORE](https://github.com/rupret007/Bob-the-Bot/issues/3#issuecomment-5556160339).
The source audit started at 95536f31. #74 landed during implementation; only
this unpublished branch was refreshed. All local changes and four prior
stashes were preserved. #74's branch remains untouched at 67a76ed1.

When a room offers a canvas and Paint along, an artist can return to either
existing panel from the room. Each has its own status and Open button. The
strip retains its existing priority; a missing app or recovery request no
longer hides the other activity's room route. Canvas publication, video copy
replacement, invitation recovery and Leave are existing baseline behavior.

| Claim challenged | Product evidence |
| --- | --- |
| Both actual activities remain reachable | Real LAN and native guest/controller journeys visit both panels with hidden video, a required local copy or local playback attention, and with Drawpile installed or missing. The previous one-action room dispatcher rejected the other activity. |
| Opening a panel preserves the artist's work | Navigation reuses the current panels and local player. It does not load another file, reveal hidden video, launch Drawpile or start a meeting. Explicit Show video and canvas Open remain separate tested actions. |
| Recovery priority is still truthful | The strip's existing presence policy is unchanged. All canvas/video states and host/guest intent combinations retain that primary status; only another actual activity becomes secondary. #74's pending share/withdraw status still takes priority. Personal image work and saved guest preferences cannot invent another room offer. |
| Old actions cannot reopen an unavailable activity | Both actions are derived again from current room/cleanup facts at dispatch. LAN/native withdrawal removes only the withdrawn route. Connection loss removes both; current LAN receipts restore them. Native loss followed by Leave rejects late receipts and stale panel intents. |
| The room remains private and independent of Music | Journeys retain the same connection owner, generation and borrowed Art context while preserving saved Music/Art preferences. No Music startup or Webex launch occurs. Private invitation, filename, backend detail and identity markers stay out of public projections, accessibility and diagnostics. |
| Both actions fit beside Conversation | Actual themed Cocoa widgets cover compact and normal windows, normal and 125% glyph width, video recovery and #74 canvas pending states. Without Conversation, both routes fit without scrolling. With Conversation open, keyboard focus reveals each action without overlap. Full keyboard navigation is enabled only for the Qt test process and restored; system preferences are unchanged. |

**Pre-Karen local gate: PASS.** Final proof on the post-#74 base:

- Focused Art/door/session/invitation/canvas/video set: **2,355 passed** across
  55 modules in 61.99 seconds.
- Full raw `QT_QPA_PLATFORM=offscreen .venv/bin/pytest -q`: **8,189 passed,
  26 existing skips, 99 subtests passed, 3 dependency deprecation warnings**
  in 287.30 seconds; process exit 0. No module isolation, exclusions, extra
  skips, warning filters or retries in this full run.
- Native Cocoa: **129 distinct tests passed** across final runs: 54 new
  activity UI cases, 50 existing overview UI cases, 3 lifetime cases and
  22 actual LAN/native guest journeys. The UI matrix includes the newly
  landed canvas share/withdraw retry states.
- Pure activity/overview model set: **644 passed**. All 77 canvas/video state
  pairs preserve the original presence policy, including pending publication.
- Required Ruff scope, compileall, pip check, whitespace and UX smoke: **PASS**.
  New and changed test modules also pass Ruff.

Eight final native screenshots were inspected, including compact missing-app,
hidden-video, share-pending and withdraw-pending states. These use synthetic
room/player/launcher facts and establish widget usability, not physical codec,
Drawpile or multi-computer performance. Independent source/privacy review
found no actionable issue in the model/controller/widget integration.

Self-QA corrected the main window's tab order so the additional activity sits
between the primary activity and Conversation. Compact density keeps both
rows readable. Existing keyboard assertions were retained; their fixtures now
exercise full Qt keyboard navigation independent of macOS text-only Tab mode.
No product or operating-system keyboard preference was changed.

The open draft will record its final SHA, exact-tip hosted run/job links and
all four desktop artifact records. Hosted SUCCESS is required before the Bob
handoff; local proof does not substitute for it.

Holds: Art Preview; own tools; existing silent local-file Paint along; Webex
talk/share separate and never on the Art door. No automatic meeting/canvas
launch, second video stack, short-code/public rendezvous or default Session
Help product claim. Music audio remains evidence-based. Physical/public/
live-provider/installed-owner-package gates NOT RUN. Unsigned 0.27.2 stays
Jeff-only. No merge/tag/sign/release/Pages/Release Trust. #37/#49 parked;
#67 and all protected branches untouched. Canonical WebJam only. One OPEN
DRAFT for Karen, then coord AFTER and agent:none/FREE. Art Notes/chat remains
a separately identified future slice.
