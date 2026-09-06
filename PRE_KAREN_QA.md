# Pre-Karen QA — Art Conversation meeting-link entry

Branch `codex/webjam-finish-product-art-conversation-link`, exact master base
`4128f374e870544b29298b592c49fc931d3e5555`. Canonical checkout:
`/Users/jeffstory/Documents/WebJam`. BEFORE
[5562518611](https://github.com/rupret007/Bob-the-Bot/issues/3#issuecomment-5562518611),
base amendment [5562606345](https://github.com/rupret007/Bob-the-Bot/issues/3#issuecomment-5562606345).

## Product and leftover review — PASS

Notes → Talk & share → Add/Change Link opens the existing meeting field,
expanded, visible and focused. Existing text is selected for replacement.
Save updates Conversation immediately; Cancel changes no settings. Return
focus goes to the current useful meeting action only while the same room and
workspace remain visible. Navigation and completed Leave during the modal win.
Ordinary Settings and optional-key entry keep their previous behavior.

The final source and 18 real-controller guest journeys received independent
read-only leftover, security and UX review. No actionable findings remain.
The room's named artist roster remains unimplemented; this slice adds no
participant discovery claim. #81/#82 and the landed #83 lifecycle work remain
outside this change, as do Art door copy, assets and host media publication.

## Privacy and ownership review — PASS

- Latest-settings merging preserves the current role and endpoints during
  modal invitation/room callbacks; the room is never restarted for a link edit.
- Save does not launch a browser, meeting, canvas, player or audio process.
  A changed link clears the old handoff evidence; an external meeting already
  open stays open until the person leaves it in that service.
- Notes, undo/selection and unsent text remain local. Tests assert private
  markers are absent from logs, public projections and user feedback.
- No new log sink or payload field. Existing rotating/redacted diagnostics
  and exception-type-only settings-save errors remain unchanged.

## Verification

- Initial four Add/Change guest fixtures failed before the source change.
- Native Cocoa: **18 passed**, 7.63s. Actual settings at 640×560 and room return
  at 760×600 inspected; input, Save/Cancel and keyboard next action are reachable.
- Focused 88-module suite, including landed #83 and meeting/privacy coverage:
  **2,872 passed**, 2 existing skips, 18 subtests, 162.41s.
- Ruff, compileall, pip check, runtime dependency policy and UX smoke: PASS.
- Full pytest and hosted tests/integrations/four desktop builds are the final
  handoff gates. Their completed results, exact tip/tree and run/artifact links
  are recorded in the PR description and coord AFTER; native or focused success
  alone does not authorize a green handoff.

## Holds

One OPEN DRAFT for Karen. Parked #37/#49 and protected predecessor branches
remain untouched. Unsigned 0.27.2 is Jeff-only; no merge/tag/sign/Pages/Release
Trust/publish/GitHub Latest. No short-code, public rendezvous, live Cisco or
second video stack. Webex stays beside WebJam and off the Art door. Physical,
two-device, installed-package and live-provider verification remain NOT RUN.
