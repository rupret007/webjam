# PRE_KAREN — ordinary Art hosting across desktop platforms

Branch `codex/art-lan-host-platforms`, fresh fetched master
`2aba2f2f72f94d56f5c7f810fbe7ca326c5502b2`.
[BEFORE](https://github.com/rupret007/Bob-the-Bot/issues/3#issuecomment-5595493289)
at 2026-09-08 22:49 CT. Exact frozen tip, full local results and actual hosted
receipts are recorded in the OPEN DRAFT body and coord AFTER.

## Leftover honesty

This fixes a real first-action barrier, not a new transport: the Art door's
Mac-only Host gate hid an existing authenticated Python LAN path on Windows
and Linux. It now permits that ordinary Art path on supported desktop platforms.
The submission boundary also rejects stale/direct calls for unavailable hosts.
No room-connected or media-playing status is inferred from pressing Host.

The slice starts independently from master. #110 at
`686aa2b639aea2183ef53d327c3940782d6d21f3` remains the separately verified
combined candidate. Its Conversation, lesson-request, Music listening and
recovery changes are not claimed here; composition is explicit future work.

## Ten-second UX self-QA

- An artist chooses Make together or Paint along, then Host. No Music install,
  video file, drawing program or extra setup choice is required to open the room.
- Available Art hosting adds no helper line or door chrome. Make together,
  Paint along, Host and Join remain the existing visible actions.
- Unavailable profile/platform/native choices retain the restriction in both
  the helper and accessible Host description, even after changing Art cards.
- Back/profile transitions reevaluate the action. Save failure restores the
  current Art action; an ongoing Join cannot be overwritten by a Host call.
- Real runtime tests distinguish a waiting host, enrollment without presence,
  an authenticated guest reader, failed startup and completed cleanup.
- Missing address or bind failure cannot produce a copied invite. Retry uses
  the existing owner. No new loader, player, audio or provider workflow is added.

## Security and ownership self-QA

Production changes are confined to LaunchDialog. The predicate preserves Mac
behavior and admits only Windows/Linux + current Art + no explicit native lab
request. It uses the existing trimmed `WEBJAM_ENABLE_REFERENCE_LOCAL == "1"`
interpretation. The actual Host callback rechecks it before settings mutation.
The existing submission lock still owns duplicate prevention.

The controller's native-first dispatch, signature/integrity verification,
Music startup, private-address validation, bearer authorization and cleanup
logic are unchanged. Native opt-in never silently becomes an ordinary LAN host.
No protocol, public endpoint, token lifetime, persistence format, permission,
media capture, browser action or automatic send is added.

Portable tests isolate settings, repositories, notes, logs and secrets in
explicit temporary storage. The real host owner binds through a controlled
loopback seam and the real guest talks only to that owned listener. Enrollment
alone and rejected authentication do not count as presence. Guards reject
unrelated network/process/provider/audio actions; callback and teardown errors
fail the test. Windows filesystem permission behavior is not an ACL guarantee.

## Verification and failure history

Policy baseline: **23 failed / 10 passed**. Corrected policy: **33 passed**.
Existing Art UX: **43 passed**. Its first corrected run had one obsolete
Windows-host assertion fail; the test now retains a denied-platform control and
checks that available Art hosts have no redundant helper line. Earlier compact
Windows expectations were updated without removing layout/door assertions.

The initial portable fixture had a module-restoration problem and a sandbox
loopback-bind denial; those failures are retained separately from product
failures. The fixture restores only its optional-audio module entry. The bounded
real-listener test runs with the required local socket permission, while guards
continue to deny unrelated effects. Final portable evidence is in the PR body.

At the original `8ecbdf0` tip, one full local run had 8,813 passes and a
20-second timeout in the unchanged Pocket Stage WebSocket upgrade test. The
unchanged gateway module then passed 40 tests, followed by a complete local
run with 8,814 passes and all 14 checks green. The first timeout is unexplained;
no source or timeout workaround was applied. Both full records are retained.

Both original Windows hosted jobs stopped before Art startup because Python
3.11's first platform lookup reads Windows version metadata through a process.
The fixture's application-process guard rejected that standard-library lookup.
The correction reads the real `platform.uname()` once before installing the
application guards. No OS/architecture value is invented or patched, and every
application/network/provider/media guard and ownership assertion remains.
The corrected portable module passes three tests locally on macOS; final-tip
Windows evidence still requires the actual hosted run. Original hosted failures
remain in `out/art-lan-host-platforms/tip-8ecbdf0` and the PR evidence.

Every tracked top-level application test module runs in a fresh Python process,
matching the existing Qt lifetime isolation. Required static, dependency, native,
sidecar, service and UX checks run on the frozen source. Hosted CI remains
allowed to run and adds one bounded, dependency-free unittest invocation to
each existing desktop job before packaging. Release/manual gates are unchanged.
Do not claim hosted green until the exact-tip runs and all four desktop runtime
steps/builds have been inspected. Evidence: `out/art-lan-host-platforms`.

## Open acceptance and holds

Physical LAN reachability/firewall prompts, installed-app behavior, different
home networks, Art faces/narration/independent listening and Music audibility,
routing/timing/endurance are **NOT RUN**. The separate-home architecture decision
remains unanswered. Four builds and loopback do not complete either real session.
Codex self-QA is not Karen PASS; independent review remains pending.

Keep OPEN DRAFT. No merge/squash/tag/sign/release/Pages/Release Trust/Publish/
deploy/spend/live Cisco, unsolicited messages or automatic capture. Unsigned
0.27.2 remains Jeff-only. Parked #37/#49 untouched; no second goal, other repo,
short codes/public rendezvous or second media engine. The two Art cards, squirrel
mark, guest-never-seek rule and all original draft heads remain protected.
