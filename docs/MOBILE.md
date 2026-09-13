# Mobile plan (locked 2026-09-12)

**Status:** Planning locked with Bob. Not implemented. Not a spike.
**Recorded:** 2026-09-12
**Base:** `origin/master` `ce0ce6dd` (DEMO.md #117 landed)
**Owner:** Jeff opens any later spike. No Codex, Fable, or Sonnet work
until that happens.

This note is the product hold for phone and tablet. It is not Pocket Stage
([ADR 0003](adr/0003-pocket-stage-mobile-companion.md), owner iPhone as a
second screen beside a desktop session). It is not the meeting-window Art
companion projection ([ADR 0013](adr/0013-art-companion-projection-and-commands.md)).
It does not change Host/Join, Jamulus, Drawpile, Paint along, or Webex.

## Locked decision

Phone and tablet are an **Art companion**.

A guest on a phone or tablet **Joins** and then works in the same two Art
starts the desktop already has:

- **Make together** — talk and make in one room; optional shared workspace
  when the group wants one surface.
- **Paint along** — follow one silent process video while each person paints
  in their own space.

**Music Host stays on the desktop.** A phone or tablet does not host a live
Music room and does not claim to be a low-latency jam client.

**Music on mobile is listen / chat / follow only.** Someone can hear, talk,
and stay with the room. They cannot be sold, labeled, or demoed as jamming
from the phone. Jamulus still owns live audio; that path stays on the
computer that can run it honestly.

## What comes later (not now)

| Later | Meaning |
| --- | --- |
| Phase 1 thin Art spike | Smallest Join + Make together / Paint along companion a phone can actually use. Codex/Sonnet only after Jeff opens that spike. |
| PWA later | Progressive web app is a later delivery option, not the Phase 1 shape and not a second product. |

Until Jeff opens the spike: planning is done. Do not start Codex, Fable,
or a new agent on this surface.

## Holds

These stay held. A mobile plan is not permission to move them.

- **Parked [#37](https://github.com/rupret007/webjam/pull/37)** — shared
  workspace delivery retry. Do not restack, rebase, or merge it from here.
- **Parked [#49](https://github.com/rupret007/webjam/pull/49)** — Pocket
  Stage Mac CI kit. Different surface; leave it parked.
- **No GitHub Pages.** Do not publish a Pages site as a mobile stand-in.
- **No v0.27.2 mutation.** Latest release `379360694` and tag `v0.27.2`
  stay immutable. A checkout is not a package.
- **No second video stack.** Paint along remains the one video workspace.
  Do not add a phone player, a second clock, or a browser video surface.
- **Webex stays first-class for talk/share.** Conversation is still the
  meeting handoff. Mobile does not grow its own talk/share stack.
- **No toy canvas.** Shared making stays a real Drawpile workspace
  ([ADR 0010](adr/0010-art-shared-canvas-drawpile-handoff.md)). WebJam
  does not paint, and it does not ship a doodle box to avoid installing
  the real program.

Signing, notarization, physical two-device feel, and any App Store /
TestFlight path remain owner-only and **NOT RUN**.

## Leftover copy on this branch

The same leftover also softens already-local Art room copy: user-facing
"canvas" / "Install Drawpile" lines move toward "workspace" /
"Set up shared workspace". That is Paint-along-first north star copy,
not a Drawpile removal. Jeff may glance at the feel of that copy before
anyone lands it.

## What this document does not authorize

- Merging this branch
- Touching parked #37 or #49
- A Music-mobile jam claim
- A toy in-WebJam canvas
- A second video stack
- Pages, a tag, a release, or a 0.27.2 rewrite
- Starting Codex/Fable/Sonnet before Jeff opens the spike
