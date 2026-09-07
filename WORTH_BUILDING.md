# Worth Building — Art Conversation follows the saved meeting

Base: fetched master `159f4447a78e05e324067088d02480682ab0e4d8`.
Branch: `codex/art-conversation-next-action`.
Canonical checkout: `/Users/jeffstory/Documents/WebJam`.
Marker: `OVERNIGHT_WEBJAM_CONTINUE_20260906_2340`.

## The real leftover

With Google Meet configured and a verified Webex installation, opening Art
Conversation focused **Show Webex App**. Its meeting tooltip also recommended
Webex as the way back to that unrelated meeting. The same preference sent an
artist with no saved link toward an app instead of Add Link.

A real Qt reproduction established the wrong target before edits. The first
regression run produced **23 failures / 1 pass**, including actual host, LAN
guest and native guest Notes-to-Conversation journeys.

The LAN host name list already covers authenticated freshness, privacy,
duplicate names and keyboard recovery. A native named roster needs additional
authenticated name facts. This slice fixes a present wrong-app action without
inventing artists or another transport. It does not repeat room Leave/rejoin,
shared canvas, timeline seeking or #87's host video opening.

## Before and after

Before, native installation determined the first keyboard action and every
control had the same visual emphasis. After, the saved meeting and handoff
state determine one highlighted existing action:

- No link: **Add Link**.
- Saved link, including a failed handoff: **Join / Open Meeting**.
- Opening: the disabled **Opening…** action and focused status; repeated input
  does not activate another control.
- Opened Webex link and verified app: **Show Webex App**, which promises only
  app activation. Other providers or unavailable native proof: **Open Again**.

Native controls stay explicitly Webex-only. Art tooltip advice never substitutes
Webex for another meeting. Rendering never launches a meeting, app or player.
A late native recheck also cannot take focus back after the artist moves on.

## Proof and limits

The new suite covers real Qt focus and input, passive detection, profile return,
pending handoffs, real Settings add/change/remove, and real host/LAN/native
room journeys through Notes. Existing Conversation layout, room return, door,
Music and native verification tests remain relevant.

Final local and hosted results, exact tip/tree and four desktop builds belong
in the draft PR and coord AFTER. Tests use synthetic saved links and controlled
provider/native evidence. Live meetings, actual app activation, installed-app
feel, physical playback and platform trust remain **NOT RUN**. A successful
external handoff is not proof of meeting membership or sharing.

#87 was reviewed by Karen and leftover-squashed by Bob onto master
`159f4447a78e05e324067088d02480682ab0e4d8`. This branch starts from that new tip.
Parked #37/#49, the Art door and unsigned 0.27.2 holds remain untouched.

BEFORE: https://github.com/rupret007/Bob-the-Bot/issues/3#issuecomment-5565319916.

Base-advance BEFORE: https://github.com/rupret007/Bob-the-Bot/issues/3#issuecomment-5565690193.
