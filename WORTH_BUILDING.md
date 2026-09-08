# Worth building — verify one combined invitation-to-session flow

Branch: `codex/two-session-integration`, canonical checkout only.
Declared dependent stack rooted at #97
`14aa874aa28cc3bc8cdfe647c888bc61547daa89`; original drafts remain untouched.

## Observed gap and why this comes next

Separate branch tests do not prove that the improvements work together. A guest
may have a different personal meeting from the invitation's room meeting. The
combined flow must retain the right context through shared-lesson entry, room
meeting replacement/removal, Retry and Leave. Music recording intent must remain
correct within that same room lifecycle.

This is an unverified integration boundary, not a newly invented feature or a
claim of a reproduced product defect. Reimplementing pending work would duplicate
it. The approved sustained goal permits a documented stack for this dependency.

## Source reuse and intended before/after

The stack starts from #97 `14aa874` and reuses the existing source from #96
`41a2665`, both #98 commits ending at `830bed1`, #99 `c2a088f`, and #100 `0cf1f2c`.
Full original revisions are recorded in PRE_KAREN_QA.md. Their original master
was `2aba2f2f72f94d56f5c7f810fbe7ca326c5502b2`; their green results remain
historical evidence for their own heads.

Before: separate drafts improve own-space Art entry, invitations, shared lessons,
Shared Track support/recording and offline timing, without a verified combined
candidate. Intended after, subject to pending tests: one reviewable source stack
preserves those existing behaviors together, including the correct room meeting,
one useful next action, personal settings/work and deliberate recording intent.

## Acceptance — PENDING

New regressions will exercise full invitation → shared lesson with different
personal/room meetings, replacement/removal and missing-link behavior, explicit
Open, failed launch/Retry, queued replacement, successful/failed Leave, role
reset, Notes/focus and compact layout. Music coverage combines room context with
pending/loading/failed source intent, cleanup, active Stop and ROUTING/PLAYING.
No source defect is claimed until a combined regression demonstrates one.

Source replay, combined integration tests, full local verification and hosted
CI including all four desktops are PENDING. Final evidence must identify the
actual combined tip; component results cannot certify conflict resolution.

## Remaining gates and holds

The two-home network decision remains pending. No remote service, short code or
public rendezvous is introduced. Real Art audio/faces/pause requests, any-artist
usability, Music mixing/reference timing and sustained rehearsal remain NOT RUN.
Synthetic RTT and packaging success do not establish physical acceptance.

The goal remains INCOMPLETE. No Karen PASS is claimed; independent review must
match the final combined tip. OPEN DRAFT only; originals and parked #37/#49 stay
untouched. No merge/squash/tag/sign/release/Pages/Release Trust/Publish/deploy/
spend/live Cisco, unsolicited send or automatic capture. Unsigned 0.27.2 stays
Jeff-only. No second media
engine, other repo, parent injection or second WebJam goal. Maintain the coord
#3 four-hour codex lease and America/Chicago BEFORE/AFTER; release at handoff.
