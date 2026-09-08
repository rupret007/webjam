# Worth building — keep an established session past invitation expiry

Base: `2aba2f2f72f94d56f5c7f810fbe7ca326c5502b2`, fetched `origin/master`.
Branch: `codex/established-session-lifetime`; canonical WebJam checkout only.
BEFORE: https://github.com/rupret007/Bob-the-Bot/issues/3#issuecomment-5589815415.

Two target experiences need a room that survives a lesson or rehearsal: two
artists easily join, see and talk to one another, and follow a Bob Ross mountain
painting lesson; two musicians join and rehearse together, with or without a
reference track. This slice fixes a duration defect in the existing private
native reference transport. It does not claim either full experience is proved.

The in-memory baseline used the production reference-service registry and relay
authentication with its default 600-second invitation TTL and 90-second idle
timeout. Both enrolled peers' valid keepalives were accepted at second 599. At
600, the service removed the room and rejected both peers despite activity one
second earlier. Separately, the native runner gave the entire connected
operation the invitation deadline. Fixing only the service would still end the
native session at that deadline.

Before: an invitation's admission window also ends an already enrolled room.
After: the invitation still limits admission, while an enrolled room has an
independent, finite active lifetime. The service ceiling defaults to eight hours
from original registration and can only be lowered; the existing 90-second idle
timeout can end it sooner. Native operations also stop at their actual identity
certificate expiry. Enrollment or continued traffic never restarts the ceiling.

Native setup remains deadline-bound through mutual peer proof and the room
handshake. Only then is its setup child context canceled and the live pumps run
under the separate owned-operation context. Local teardown joins owned workers;
a failed close retains its operation for retry and cannot acknowledge success
or permit a second operation. The desktop retains that cleanup obligation across
Reset Invite → Try again without restoring the retired invitation or room
authority. Host cleanup uses a fresh, bounded authenticated
close attempt because the original service-control connection can idle out.
Local `peer_closed` remains a local teardown receipt, not an implied remote
removal acknowledgment.

This is a stronger next step than more entry copy: an Art lesson or Music
rehearsal can reasonably outlast the invitation window. The defect exists on
master and can be corrected independently of drafts #96–#101. Those drafts
remain unchanged; #101 has its separate hosted-green handoff. No dependency on a
public profile or an unanswered public-rendezvous exception is introduced.

At documentation preparation, the complete reference-service suite passed
**79 tests**. Focused native race checks and strengthened real service/native
integration also passed. Full local and hosted checks on the final frozen tip
remain pending;
their exact commands, results, tip SHA, and four desktop builds belong in the
OPEN DRAFT body and coord AFTER. Fake-clock and controlled transport evidence do
not establish a physical 60-minute session, public reachability, or audio latency.

Keep the Art door at Make together + Paint along → Host/Join, with its existing
squirrel-with-fro mark. Conversation/Webex sharing remains beside WebJam, and
guests gain no seek authority. No public endpoint/profile, short-code, player,
second video stack, Drawpile/shared-canvas work, or automatic meeting/media action
is added. Parked #37/#49 stay untouched. One OPEN DRAFT PRE_KAREN; no
merge/squash/tag/sign/release/deploy/Pages/Release Trust/Publish/send/spend/live
Cisco. Unsigned 0.27.2 remains Jeff-only. Self-QA is not Karen PASS; independent
leftover, security, and ten-second UX review must match the final tip.
