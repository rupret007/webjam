# PRE_KAREN — Art Conversation next action

Base `159f4447a78e05e324067088d02480682ab0e4d8`, fresh
`codex/art-conversation-next-action`, canonical WebJam checkout.
Marker: `OVERNIGHT_WEBJAM_CONTINUE_20260906_2340`.
BEFORE: https://github.com/rupret007/Bob-the-Bot/issues/3#issuecomment-5565319916.

## Product and ten-second self-QA

An artist opening Conversation is directed to their saved meeting, regardless
of whether a different native app is installed. One existing button is primary.
Missing link offers Add Link; configured link offers Join / Open Meeting;
Opening focuses non-actionable status. After a Webex handoff, verified native
activation may be primary, without claiming a specific meeting window. Other
providers keep their own Open Again link.

The first run reproduced 23 failures / 1 pass. Actual host, LAN guest and native
guest journeys prove that entering from Notes sends nothing and opens nothing;
one deliberate keyboard activation reaches the existing link handoff, not
native Webex activation. Notes, the current room identity and generation remain.

## Focus ownership and security self-QA

- No new URL, parser, protocol, network request, launcher, payload, player,
  public field, log sink or timer. The existing validated link handoff and
  native publisher verification remain the action owners.
- Art's visual emphasis and entry focus share one state-derived choice.
  Passive detection/profile rendering changes presentation without intent
  or taking focus away from the artist.
- Disabling the focused link action first places focus on its status. Repeated
  Space cannot fall through to another button while the handoff is pending.
  Completion does not pull focus back.
- Self-QA additionally reproduced a late native recheck stealing focus after
  the artist moved to Change Link. Restoration now requires that the check's
  status still has focus; a hidden or abandoned panel does not regain it.
- Real Settings add/change/remove returns to the newly applicable action without
  launching a meeting or restarting the room. Provider guidance uses bounded
  service labels; no private meeting URL or notes are added to tooltips or logs.
- Native controls retain explicit Webex-only names and require existing
  publisher proof. Music retains its established entry focus and visual roles;
  the shared native-check restoration also respects the current focus owner.

## Verification and honest boundaries

The new tests use actual Qt widgets/input and the ApplicationController with
temporary settings/notes/databases, isolated credentials, synthetic links and
controlled host/LAN/native/provider fixtures. Existing compact Conversation
layout, Art door, room return, native busy-state and Music cases are retained.
Final counts, commands, exact tip/tree and actual hosted test/integration/four
desktop results belong in the OPEN DRAFT and coord AFTER.

Live provider meetings, real Webex activation, installed-app feel, physical
playback and platform trust are **NOT RUN**. Successful external handoff is
not authenticated meeting membership, a selected meeting window or screen
sharing. Those stay with Webex/the selected service. This is Codex self-QA,
not independent review or Karen PASS.

#87 passed Karen and was leftover-squashed by Bob onto this base. Its
reviewed source tip was `5ac6afb1dedc32025b71fd8a8c89e22123712b37`; its handoff is
https://github.com/rupret007/Bob-the-Bot/issues/3#issuecomment-5565158722.
#37/#49 remain parked; #86 stays merged. Make together + Paint along → Host/Join
and the squirrel-with-fro art are unchanged. No merge/squash/tag/sign/Pages/
Release Trust/Publish/release/deploy/spend/live Cisco/public rendezvous/other-repo
lane. Unsigned 0.27.2 stays Jeff-only. Stop at one new draft for Karen leftover
+ security + UX; Bob may leftover-squash only after PASS with tip MATCH.

Base-advance BEFORE: https://github.com/rupret007/Bob-the-Bot/issues/3#issuecomment-5565690193.
