# Pre-Karen QA — Art Leave and the next room

Canonical checkout `/Users/jeffstory/Documents/WebJam`; branch
`codex/webjam-finish-product-art-end-leave`; exact master base
`0f097e70130b6ef80668f2c4cd0df896f012a592` after Bob's #80 squash.
BEFORE [5559172070](https://github.com/rupret007/Bob-the-Bot/issues/3#issuecomment-5559172070),
07:20:13–11:20:13 CT on 2026-09-06; marker
`OVERNIGHT_NEXT_ART_REBASE_20260906_0715`. The earlier 8d708d56 base gate was
explicitly superseded by Jeff. This is the same task.

## Product and ownership

Confirmed Art Leave offers Paste New Invite; host End offers Start New Room.
The local finite receipt uses the room's captured role across saved Host/Join
settings and restored Art/Music workspaces. It remains an idle presentation,
so take preservation, Studio review and export keep their existing precedence.
Conductor reset still advances its generation and captures the exited role.
The existing invitation-only dialog preserves the workspace when canceled;
new invitation acceptance creates a fresh owner. Historical callbacks cannot
resurrect the departed room or authorize implicit hosting.

Native discovery now exposes Leave before the host's profile arrives. The
cleanup owner captures that pending invitation by identity, retains it through
failed cleanup, and retires it only after cleanup and profile restoration
succeed with no runtime remaining. Art cleanup failure updates Notes immediately
to the same Try Leave Room/Try End Room action owned by the header. Pending or
failed cleanup blocks new invitation entry. Existing Music startup behavior
is preserved by limiting that extra refresh to captured Art cleanup.

No automatic meeting, canvas, player or Music launch; no new transport. Private
invitations remain in memory outside public guidance/diagnostics. The existing
rotating redacted log and bounded lifecycle events remain authoritative; this
slice adds no log sink or payload logging. Privacy cases exclude private Notes,
titles and native stop exceptions, and repeated receipt rendering stays stable.

## Verification

- Native macOS Cocoa: **40 passed in 11.46s** (27 real controller journeys and
  13 pure guidance checks). Controlled network/process boundaries; no live providers.
- Native capture gate: **2 passed in 3.18s**. Four 760×600 captures cover Art
  and restored Music, each in room and Notes views. Real Notes→room navigation,
  button geometry and mouse activation reach the invitation dialog. Root and
  independent review confirm readable copy and one reachable entry action.
- Focused suite: **66 modules; 2,308 passed, 2 existing skips, 2 subtests,
  122.68s, exit 0**.
- Full unfiltered suite: **8,511 passed, 26 existing skips, 3 dependency
  deprecation warnings, 99 subtests, 387.26s, exit 0**. One process:
  `QT_QPA_PLATFORM=offscreen .venv/bin/pytest -vv`.
- Ruff, compileall, pip check, runtime dependency policy and UX smoke: PASS.
- Independent PRE_KAREN leftover/security/UX review: PASS on the same six-file
  source/test freeze. No source changes during final validation.
- Preservation audit: 48 original refs checked; 47 unchanged and local master
  alone advanced to the authorized base. All four stashes MATCH. The #80 source
  branch remains `da1b3036822e92a70784339af1a76187c06cb058`.

## Regression and review history

Fixture-first failures reproduced Start Session after guest Leave, stale Notes
on failed cleanup, retained pending native invitation, inaccessible discovery
Leave, and a misleading Record Session hint after restoring Music. Native stop
faults use exceptions because the backend stop contract is void; LAN faults use
False. Native callback assertions wait for the actual queued receipt, and title
assertions expect the restored personal title rather than the borrowed room name.

An earlier focused run was deliberately interrupted at 2,211 passing tests for
compact navigation inspection. Subsequent geometry and mouse checks found no
missing-HUD defect; individual exact-SHA image inspection resolved an apparent
omission in a multi-image display. The recording hint was removed. Interrupted
and intermediate results are retained and are not final passing evidence.

Raw logs, source hashes, four screenshots, review and preservation manifests
are under `/private/tmp/webjam-post80-art-*`; fixture history is also under
`/private/tmp/webjam-art-exit-*`. Exact-tip hosted SUCCESS, all four desktop
checkout/artifact proofs and final tip/tree belong in the OPEN DRAFT and AFTER.
Local verification does not establish release-package trust.

Draft for Karen; AFTER and agent:none, then stop for Bob. Parked #37/#49
untouched; stay off #67. No merge/tag/sign/Pages/Release Trust/publish/GitHub
Latest, short-code/public rendezvous, or other repositories. Unsigned 0.27.2
Jeff-only. Art Preview, squirrel Paint along mark, own tools, silent local-file
Paint along and Webex beside WebJam retain their boundaries. Physical,
live-provider, two-device and installed-package checks NOT RUN.
