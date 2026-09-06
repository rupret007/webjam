# Pre-Karen QA — Art host network recovery

Canonical checkout: /Users/jeffstory/Documents/WebJam only. Fresh branch
`codex/webjam-finish-product-art-host-recovery`; exact master base
`ff198d790243a819aa1166ea6afa72fa2989380a` (Bob's post-#78 squash).
[BEFORE 5557966863](https://github.com/rupret007/Bob-the-Bot/issues/3#issuecomment-5557966863)
claims 03:15:14–07:15:14 CT on 2026-09-06. All 45 previous branch refs and
four stashes match; #78's branch and parked #37/#49 remain untouched.

A host's retained LAN listener now enters recoverable network interruption
when its private local route disappears or changes. Same-address recovery
preserves the listener, invitation, current conductor attempt and optional
local work. Fresh authenticated-reader expiry remains the membership proof;
a restored route alone only opens the room for guests to join.

Try Again with no usable address keeps the existing listener. A usable changed
address triggers replacement only after confirmed old-listener cleanup. Stop
must return True and leave the old listener inactive. End, Quit or replacement
ownership wins over a stale retry; only confirmed cleanup permits a new
conductor attempt. Cleanup also suppresses invitation copying if the old
address returns. Initial startup failures remain failures.

Host-specific interruption copy reaches the HUD, room overview and Notes;
guests retain their existing reconnection explanation. The existing shared
guidance override supplies the useful Try Again action. No renderer starts a
new connection while showing state.

Logging uses `webjam.qt.room_participant`, under WebJam's existing redacted,
rotating handler. Fixed event messages distinguish network loss/return,
deferred retry, requested/completed/abandoned listener replacement and
unconfirmed cleanup. Repeated ticks produce no duplicate transition records.
The Art listener startup warning omits raw exceptions; Music's existing path
is unchanged. No addresses, invitation credentials, artist names, Notes,
canvas passwords or private exception details are added to diagnostics.

**Final local gate and PRE_KAREN leftover/privacy/security/UX: PASS.**

- Complete raw, unfiltered pytest: **8,420 passed, 26 existing skips, 99 subtests,
  3 dependency deprecation warnings, 504.32s, exit 0**. One process:
  `QT_QPA_PLATFORM=offscreen .venv/bin/pytest -vv`.
- Final focused Art/room/transport/guidance/logging gate: **72 modules, 2,604
  passed, one existing opt-in native sidecar skip, 96.21s, exit 0**.
- All **22 new cases passed on native Cocoa in 8.30s**, including the actual
  configured rotating-log round trip, corrected Notes explanation and two
  real 760×600 views. Their captures were refreshed during this final run.
- Required Ruff, compileall, pip check, runtime dependency policy, UX smoke and
  diff checks passed. Two independent reviews matched all six frozen source/
  test hashes and found no remaining material leftover/privacy/security/UX issue.

Hosted exact-tip SUCCESS including all four desktop builds is a separate gate.
Its checkout, test, job and artifact evidence belongs in the PR and coord AFTER;
a local passing suite is not hosted or release-package proof.

Recorded supporting checks before the two final review corrections:

- Native recovery matrix: 21 passed in 11.22s; six initial loss/return cases
  also passed in 5.39s. Existing Art controller/overview/UI: 83 passed in 20.88s.
- Focused 69 modules: 2,582 passed, one existing opt-in native sidecar integration
  skipped, 91.01s. That process integration is exercised separately by hosted CI.
- Two native 760×600 Cocoa/Inter 13px captures: 2 passed in 3.36s. Root and an
  independent reviewer inspected both Conversation-open/closed views: Try Again,
  own-tools guidance, Conversation and End are readable and reachable.
- Ruff required directories and touched tests, compileall, pip check, source
  UX smoke, runtime dependency policy and diff checks passed.

Independent leftover/privacy/security review found no remaining source issue
after the corrections below. Exact committed-tip review and hosted proof are
recorded in the draft and coordination AFTER.

## Failure and review history

1. The first native baseline probe aborted during initial window construction
   (exit 134), before a recovery assertion. Its fixture globally replaced Qt's
   clipboard with a non-Qt object. The replacement is now scoped to explicit
   Copy dispatches; no product widget or global Qt workaround was changed.
2. The corrected native baseline failed at the intended assertion: the room
   returned WAITING but the HUD still offered native-only Reset Invite (one
   expected failure, 3.99s). The corrected production passed all six variations.
3. Review found host Notes inherited guest-facing `why` text. Only Art host
   recovery evidence wording was corrected; guest wording is unchanged.
4. Review of logging configuration found that a module-name logger would miss
   WebJam's configured handler. It now uses the existing `webjam` hierarchy;
   an actual isolated log-file round trip is required, beyond captured messages.
5. The first full run was deliberately interrupted for those two corrections:
   2,775 passed, one skipped, 14 subtests, one dependency warning, 220.30s,
   exit 2. This partial run is not full-suite proof. Its raw output is retained.

Raw evidence is preserved under `/private/tmp/webjam-post78-*`: baseline
construction abort, expected baseline failure, native/focused logs, native
captures and metadata, interrupted full run, final proof manifests and logs.
No exclusions, weakened transport assertions, forced conductor success,
provider launches or hidden retries establish a passing gate.

One OPEN DRAFT for Karen; exact-tip hosted SUCCESS with all four desktops;
PRE_KAREN; AFTER and agent:none; stop for Bob conductor. Art remains Preview.
Physical/two-device/live-provider/installed-package gates NOT RUN. Silent local
Paint along, own tools, Webex beside WebJam and never on the Art door. No second
video stack, auto-launch, short-code/public rendezvous, merge/tag/sign/Pages/
Release Trust/publish/GitHub Latest release. Unsigned 0.27.2 remains Jeff-only.
Black/white/neutral gray/burnt orange. WebJam only.
