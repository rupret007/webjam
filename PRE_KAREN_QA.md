# PRE_KAREN — Music invitation guidance

Branch `codex/music-lan-invite-guidance`, independent base `origin/master`
`2aba2f2f72f94d56f5c7f810fbe7ca326c5502b2`.
[BEFORE](https://github.com/rupret007/Bob-the-Bot/issues/3#issuecomment-5594685090).
Final source SHA, local results and hosted checks belong in the draft PR body.

## Product delta and ten-second UX

A Music guest previously received either Open the link or no joining action,
although the portable protected route is explicit paste. The invitation now
names Open WebJam → Join → paste the full invitation. When the host's actual
sharing path requires it, the same message explains same Wi-Fi/local network
and keeping the host room open. The host receives matching whole-message
copy confirmation. Song, room and optional meeting stay in the same message.

Copying does not establish that anyone joined. LAN guidance does not provide
separate-home connectivity. No new door, setup choice or provider requirement
is introduced. Art's two-card door and invitation wording remain intact.

## Security and ownership

- Preserve the exact generated capability and existing message/typed-object
  redaction. No new parser or serialized fields. Canonical version recognition
  selects copy only; it never accepts or authorizes an invitation.
- Derive network scope from the host's existing sharing-path fact, not the
  invitation text. Native owners retain their current scope without new LAN,
  public-reachability or installed URL-handler claims.
- Keep bearer process-argument rejection on all supported ingress platforms.
  Explicit full-message paste uses the existing validated LaunchDialog path.
- Keep unavailable-host and failed-invitation generation from copying. Do not
  put private links or tokens in copy-confirmation text or logs.
- No media, audio, meeting, network connection, automatic send, settings
  adoption or recording is triggered by this change.

## Meaningful verification

Exercise the actual Music host-copy action with/without an optional meeting,
then feed that copied message to the real LaunchDialog. Verify exactly one
unchanged transport invitation, typed fields, complete-message acceptance,
cleared paste input, unchanged personal settings and truthful host feedback.
Keep readiness and Art/native/platform ingress regression coverage. Replace
only the old test that explicitly required the misleading Music copy.

Use the repository's isolated-module full local suite and required static,
native, service and UX checks. Hosted CI must finish on the exact final tip,
including all four desktop builds. Disclose failures, skips and retries.
Automated dialog acceptance is not physical room connection or audible proof.

## Dependencies, remaining acceptance and holds

No dependency on #97–#108: this branch starts at master. It carries the optional
meeting in the outgoing text but does not claim #97's room Conversation adoption.
That existing behavior needs later explicit composition with this correction.

Different-home joining/network decision, package selection, two-person Art
lesson/faces/narration/independent listening levels, Music routing/audibility/
latency/endurance and Karen review remain open. Codex self-QA is not Karen PASS.

OPEN DRAFT only. No merge/squash/tag/sign/release/Pages/Release Trust/Publish/
deploy/spend/live Cisco, automatic capture, unsolicited send, short-code/public
rendezvous, second engine, other repo or new goal. Unsigned 0.27.2 Jeff-only;
parked #37/#49 and previous draft tips unchanged. Maintain the four-hour lease,
post exact-tip AFTER, and release at handoff. Continue safe goal work afterward.
