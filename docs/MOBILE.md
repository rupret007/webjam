# Mobile Art companion (north star locked 2026-09-12)

**Status:** Phase-1 source spike in OPEN DRAFT #118. Jeff opened the spike
with `WEBJAM_MOBILE_ART_SPIKE_CODEX_20260912`; the previous plan-only hold is
satisfied. The north star and later phases below remain locked.
**Recorded:** 2026-09-12
**Base:** `origin/master` `ce0ce6dd` (DEMO.md #117 landed)
**Owner:** Jeff owns public Art copy/feel and physical acceptance.

This native iPhone/iPad guest app is separate from Pocket Stage
([ADR 0003](adr/0003-pocket-stage-mobile-companion.md), owner iPhone as a
second screen beside a desktop session). It is not the meeting-window Art
companion projection ([ADR 0013](adr/0013-art-companion-projection-and-commands.md)).
Desktop Make together + Paint along and Host/Join remain the room's starting
point. The companion reuses authenticated LAN enrollment/state and hands
Conversation to Webex or the host's other configured meeting service.

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

## Delivery phases

| Phase | Meaning |
| --- | --- |
| Phase 1 thin Art spike | Native iPhone/iPad, iOS/iPadOS 17+, on the desktop host's private LAN. Paste the complete local invitation, Join, follow the host's Art start, open Conversation, and Leave. Source and unsigned simulator evidence only. |
| Later native coverage | Android and different-network mobile joining are not implemented in this draft. Unsupported invitation versions lead to a clear next step. |
| PWA later | Progressive web app is a later delivery option, not the Phase 1 shape and not a second product. |

## What Phase 1 implements

- One scrolling Join form and room, with Dynamic Type, large touch actions,
  phone/tablet layout and both Art starts. The host chooses the start; a guest
  cannot overwrite it. Older hosts without that fact show generic Art context.
- Paint along explains where to watch the host's process video in Conversation
  and where to paint: Procreate, paper, or CSP. There is no phone player,
  synchronized clock, workspace launcher or canvas. Ask for pauses in the
  meeting; desktop lesson-request buttons are not added to mobile in this slice.
- Webex has an explicit **Open Webex** action when the full invitation includes
  its labeled HTTPS conversation link. Other validated meeting links name
  their own service. Opening is not joining, muted status, or a media proof.
- Music follows room status only. Listening, talking and chatting happen in
  the external meeting if the host shares sound there. No Jamulus stream,
  mobile Music Host, recorder, mixer, upload or live-jam controls.
- Connected means a successful authenticated state read, not enrollment.
  Lost connections clear current room facts; backgrounding cancels requests
  and pauses updates. Returning rechecks the same room. Leave clears private
  in-memory context; it does not close the meeting.

## Protocol and privacy

The existing v2 LAN protocol uses bearer-authenticated **plaintext HTTP** on
literal RFC1918 IPv4 only. It is not encrypted or an Internet mobile service.
No server or public endpoint is added. Invitations and conversation links
remain in memory, never settings, logs, process arguments or URL handlers.
The app accepts an explicit paste; the OS sees a validated meeting URL only
after an explicit tap. Redirects, public/DNS control endpoints, v1/v3, ambiguous
invitations, foreign rooms and responses over 64 KiB are rejected. HTTP
credentials, cookies, caches and proxy forwarding are disabled for room calls.
Requests have bounded timeouts. If Foundation withholds a stalled response's
headers, the app reports unavailability; it cannot infer an unseen status.
An observed authentication rejection clears the private room context.
Received workspace addresses, file labels/digests, capture state and lesson
admission tokens are discarded; only finite Art/Music presentation survives.
The iOS target declares only the three private IPv4 CIDR exceptions for
HTTP, following [Apple's IP-address ATS rules](https://developer.apple.com/documentation/bundleresources/information-property-list/nsapptransportsecurity/nsexceptiondomains).
There is no arbitrary-load exception. An app-hosted iOS runtime test must
prove real Join through this policy in addition to macOS Swift tests.

This enrollment currently has the same participant scope as the desktop LAN
guest; it is not a new least-privilege server role. The app exposes only
enrollment and state reads. Pocket Stage and its encrypted pinned-WSS pairing
remain unchanged.

## Build and evidence

See [DEVELOPMENT.md](../DEVELOPMENT.md#art-companion-source-spike) for the
separate `ios/art-companion.yml` target and checks. The hosted Art job builds
and tests unsigned iPhone SE (375-point narrow width) and iPad simulators and
uploads XCTest results and screenshots. Room screenshots use clearly marked,
finite Debug-only layout fixtures, not a fabricated live connection. Live
Swift URLSession → Python host tests separately prove enrollment, presence,
both Art starts, Music limits, authentication rejection and host shutdown.

Human review must inspect Join with keyboard, both Art cards, Paint along,
Conversation recovery, long text and Music honesty. Physical touch feel,
iPad Split View, real Webex video/audio and background handoff behavior on a
device remain **NOT RUN** until Jeff records them. This is not a signed app,
App Store/TestFlight build or a new desktop release.

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
"Set up workspace". That is Paint-along-first north star copy,
not a Drawpile removal. Jeff may glance at the feel of that copy before
anyone lands it.

## Remaining holds

- Merging this branch
- Touching parked #37 or #49
- A Music-mobile jam claim
- A toy in-WebJam canvas
- A second video stack
- Pages, a tag, a release, or a 0.27.2 rewrite
- Any broader delivery or public rollout beyond this source spike
