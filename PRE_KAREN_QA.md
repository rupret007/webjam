# Pre-Karen QA — Art invitation entry and local-network retry

2026-09-05 CT. Branch `codex/webjam-art-entry-clarity`; exact base
`cf311470fadcee1a688f3b675eb6d2ca4094926d`.
[Coord BEFORE](https://github.com/rupret007/Bob-the-Bot/issues/3#issuecomment-5554784296).

This slice covers the complete invitation → Join → failed local connection →
retry or replacement path. #71 landed while this slice was in progress. The new branch was rebased onto
its verified squash on master; its room overview, personal-profile/title
restoration and native retry work remain baseline. The #71 branch was not edited.

| Claim challenged | Direct product evidence |
| --- | --- |
| A copied Art invitation describes a working route | Art copy always instructs Open WebJam → Join → paste the whole message. Same-network/keep-host-open wording comes from the successful LAN owner path, not a URL guess. Native/reference-local copy makes no public-service or same-network claim. Complete v2/v3 message round-trips preserve the original capability. |
| A guest does not need to understand protocol versions | Shared Join guidance works from default Music as well as Art and other profiles. It is conditional on the host's invitation, keeps one masked field and one Join action, and does not parse while typing. Optional meeting links remain separate and explicit. |
| Keyboard users can recover from an invalid paste | Enter and keypad Enter submit once. The field is re-enabled before focus is returned; the same key cannot activate a remembered Back default and hide the error. New tests reproduce the original failure after tabbing through Back. |
| Local-network retry does actual work | Real observer threads reach terminal failure with controlled peer responses and a deterministic clock. Retry confirms old-worker stop, resets the conductor only at that boundary, starts one new observer with the same typed invitation, and waits for a fresh authenticated host profile. |
| Retry cannot discard an owned connection | False/throwing stop retains the observer and old conductor token, with Try Leave Room cleanup. Duplicate/reentrant commands, stale owners/generations, End/Quit, recording, native ownership and active/nonterminal states cannot start a competing worker. |
| Activity and invitation policies stay truthful | Borrowed Art context survives the local retry; no Music owner starts until a fresh authenticated non-Art profile arrives. Native one-use replacement remains unchanged. Use Another Invite is reachable for a terminal LAN failure, and cancelling it restores LAN recovery instead of consumed-invite wording. |
| Compact UI and private data stay usable/safe | Native Cocoa Join renders cover 460×480 through 620×520, including longest existing save errors. All owned test windows are explicitly destroyed. InviteMessage excludes private clipboard text from repr; feedback and errors do not expose credentials or raw peer exceptions. |

**Pre-Karen local gate: PASS.** Final code on the verified post-#71 base:

- Focused Art invitation, Join, observer/retry, room overview/controller,
  native invitation and Music guidance: **335 passed**.
- Full raw `.venv/bin/pytest -q`: **7,201 passed, 26 skipped,
  99 subtests passed, 3 dependency deprecation warnings** in 238.01 seconds;
  process exit 0. No module isolation, exclusions, extra skips, or warning
  filters were added to this local run.
- Required Ruff scope, compileall, pip check, and `ux_smoke_test.py`: **PASS**.
- Six native Cocoa recovery renders at 720×560 and 1100×760: **PASS** for
  saved Music before host profile, Art before first connection, and Art
  after a confirmed connection. Try Again, Use Another Invite, and Leave
  remain visible, enabled and inside the window without overlap.
- Native overview/lifetime set: **53 passed** with full keyboard navigation
  explicitly enabled for that runner. Both deletion regressions reproduce
  the old deferred-callback bug; normal focus reveal still works. System
  keyboard preference is restored afterward.
- Native Join renders cover 460×480 through 620×520, including the longest
  existing save error, masked input, keyboard return, and visible actions.
- Independent review of the complete diff against updated master found no
  material issue. All three pre-existing stashes and the old #71 branch tip
  remain intact.

Self-review fixed compact guidance clipping, Enter/Return reaching Back after
an invalid paste, and a saved-Music retry retaining its old conductor token.
The conductor resets only after confirmed observer cleanup. A failed initial
LAN attempt now renders failure across saved profiles, and the actual owned
Leave control refreshes before a host profile arrives. Art distinguishes a
room never reached from a connection later lost. Retry clears that failure
only for a fresh observer; cleanup and recording retain precedence. A
widget-owned timer prevents delayed focus work after a closing Art window
is destroyed. No assertion was weakened or removed.

Local proof logs: `/private/tmp/webjam-post71-final-focused.log`,
`/private/tmp/webjam-post71-final-full-pytest.log`, and
`/private/tmp/webjam-post71-final-verification.json`. Native recovery evidence:
`/private/tmp/webjam-post71-rebased-lan-render.log` and corresponding PNGs.
These are synthetic controller/widget checks, not physical peer evidence.

The open draft records the final committed SHA, exact-tip hosted run/job
links, four desktop artifact matches, and final hosted gate status. Hosted
SUCCESS for that SHA, including all four desktop builds, is required before
Bob handoff; no local result substitutes for it.

Holds remain: Art Preview; existing silent local-file Paint along; own tools;
no automatic meeting/canvas launch, Webex on the Art door, new video stack,
short-code/public rendezvous or default Session Help claim. Music audio still
requires its own evidence. Physical/public/live-provider/installed unsigned
package click-feel NOT RUN. Unsigned 0.27.2 remains Jeff-only.
No merge/tag/sign/release/Pages/Release Trust. #37/#49 parked; #67 untouched.
Canonical checkout only; all pre-existing stashes remain preserved.
