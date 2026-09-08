# Worth building — recover from a rejected LAN invitation

BEFORE: https://github.com/rupret007/Bob-the-Bot/issues/3#issuecomment-5591698362
Branch: `codex/rejected-invitation-recovery`
Independent base: fetched `origin/master` at `2aba2f2f72f94d56f5c7f810fbe7ca326c5502b2`.
All existing drafts, including #103 and parked #37/#49, stay unchanged.

## Observed failure and product delta

The actual peer client classifies HTTP 401 as an authentication rejection.
The LAN room observer instead catches it as a temporary network fault, waits
through the generic timeout, and lets the artist retry the same rejected
invitation. A bounded no-socket reproduction against the production client,
observer and room controller confirmed `retry_setup` with the identical invite.

Before: a rejected invitation leads to repeated Try Again attempts. After: a
typed rejection stops that observer promptly and gives one Paste New Invite
action through the existing Join/cleanup flow. Temporary network failures keep
Try Again. This applies before host profile discovery and after joining an Art
room, regardless of the guest's saved Art/Music preference.

This beats adding a new pause-request protocol now because it repairs the
first required step: getting into a room. It cannot make private-network
invitations work across different homes. That architecture decision stays open.

## Acceptance and ownership

Prove rejection at enrollment and state read, no continued polling or revival
by queued old state/loss callbacks, fixed secret-safe guidance, and no reuse of
the rejected capability. Keep temporary loss retryable. Prove real HUD/canvas/
Pocket guidance, canceled Join preserves Notes and rejection, and a new invite
cannot replace unresolved cleanup. No source video, microphone, meeting,
recording or live engine is launched by these checks.

Run focused regressions, the full application suite and required local static/
UX checks. Hosted CI must pass on the exact draft tip, including four desktops.
Automated proof is separate from physical remote joining, audibility, latency
and Karen leftover/security/ten-second UX PASS.

## Holds

OPEN DRAFT PRE_KAREN only. No merge/squash/tag/sign/release/Pages/Publish/deploy/
spend/live Cisco or automatic capture. Unsigned 0.27.2 remains Jeff-only. No
other repo, new goal, parent injection, short codes or public rendezvous.
Four-hour lease; BEFORE/AFTER in America/Chicago; release at handoff.
