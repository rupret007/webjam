# Worth building — ask for time during a shared Art lesson

Branch: `codex/art-lesson-requests`, canonical WebJam checkout.
BEFORE: https://github.com/rupret007/Bob-the-Bot/issues/3#issuecomment-5593972013

## One observed gap

A guest following the shared Bob Ross browser lesson can ask aloud for a pause,
but narration or a muted microphone can hide that request. The existing Art
Conversation helper has no in-room request or host acknowledgement. This gap
interrupts the actual paint-together journey after joining has already worked.

Before: the guest must speak over the lesson and has no visible receipt.
After: **Ask for a pause** and **Ready to continue** send fixed, temporary requests
to the current host's WebJam. The host sees the guest's chosen name and can
**Acknowledge request**, then operates the browser manually. Delivery, uncertain
delivery, acknowledgement, refusal and expiry have distinct meanings.

This advances a supported existing Art path while the different-home networking
decision remains unanswered. It introduces no player, video/canvas stack,
public route or guest playback authority. Host attention while another app is
foreground and actual picture/audio pause remain physical acceptance items.

## Declared dependency

This branch starts from exact #107 `392c959a74af135fe2a92c13ef09fbfe541fb476`,
branch `codex/recovery-mix-composition`. That candidate already composes the
invitation, room recovery, Art Conversation and Music listening changes.
Fetched master was `2aba2f2f72f94d56f5c7f810fbe7ca326c5502b2`. Original drafts
remain unchanged. #107's completed verification does not verify this new delta.

## Acceptance and boundaries

- The existing authenticated LAN state poll supplies only the guest's own
  request capability/receipt. One bounded POST carries pause or ready; there
  is no remote activation or host-acknowledgement endpoint.
- Fixed schemas, 512-byte bodies, 32 retained admissions, monotonic revisions,
  30-second notices, individual/global rate limits and five-second presence
  checks bound the feature. Expiry never permits replay of older intent.
- One existing guest worker handles at most one POST per poll cycle, then the
  normal room GET. No automatic POST retry. Explicit uncertain-delivery retry
  uses the same request; a later GET can reconcile a lost reply.
- Each signal and reply stays bound to its current room, owner and request.
  Navigation, WebJam meeting/source replacement, route loss, End/Leave and shutdown
  retire authority before cleanup waits. Guest navigation never retires the host.
  Meeting edits retain lesson instructions with inactive requests. Browser-only
  lesson changes require explicit helper exit/reentry; WebJam cannot observe them.
- Host notices retain exact request identity and PlainText chosen names.
  Acknowledgement never means paused, resumed, heard or seen. Old/unsupported
  rooms keep the spoken fallback and their ordinary connection semantics.
- Real Qt/controller plus authenticated localhost journeys verify integration;
  model/HTTP/worker/widget tests exercise limits, ordering and refusal.
  Run the complete required bar and exact-tip hosted desktop matrix.

Physical two-home joining, Art attention/faces/narration/independent listening,
Music routing/audibility/latency/endurance and Karen review remain open.
This change cannot certify those outcomes or resolve provider limitations.

## Holds

OPEN DRAFT PRE_KAREN only. No merge/squash/tag/sign/release/Pages/Release Trust/
Publish/deploy/spend/live Cisco, unsolicited send, automatic media/capture,
short codes/public rendezvous, second engine, other repo or new goal.
Unsigned 0.27.2 remains Jeff-only. Parked #37/#49, source drafts, Art's two-card
door, squirrel mark and guest-never-seek remain. Maintain the four-hour lease,
timestamped BEFORE/AFTER in America/Chicago and release at handoff.
