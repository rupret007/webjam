# Worth Building — add a meeting link from Art Talk & share

An Art guest opens Notes → Talk & share → Add Link. Previously this opened
generic Settings with Conversation collapsed and keyboard focus on Name.
Change Link expanded Conversation but still focused Name. After Save, the
confirmation said settings would apply next session, although the meeting
card already used the new link. Four actual LAN/native guest journeys failed
on base `6073a30a51cd1f616527c4b376caeeaaf9cb6037` before the source change.

Add/Change Link now opens the existing meeting field, scrolled into view and
ready to type or replace the selected link. Save applies immediately and says
so. Back in the same visible Conversation, keyboard focus moves to Join / Open
Meeting, or Add Link after removal. Joining remains an explicit action. An
open external meeting stays open after changing or removing its saved link.

Modal callbacks may change the room or workspace. The existing settings merge
uses current personal preferences; the return only focuses Conversation when
profile, room owners/generation and workspace still match. It never navigates
back to a retired room. Notes, unsent text and room ownership are preserved.
Ordinary Settings and optional keys retain their existing entry behavior.

This closes the Add Link leftover recorded by #83, independently of that
Paint along work. After Bob landed #83, this new branch was advanced to exact
master `4128f374e870544b29298b592c49fc931d3e5555`, as Jeff authorized. No protected
source branch was changed. Canonical checkout:
`/Users/jeffstory/Documents/WebJam`; branch
`codex/webjam-finish-product-art-conversation-link`.

BEFORE [5562518611](https://github.com/rupret007/Bob-the-Bot/issues/3#issuecomment-5562518611),
base amendment [5562606345](https://github.com/rupret007/Bob-the-Bot/issues/3#issuecomment-5562606345).
Draft only. Parked #37/#49 and unsigned 0.27.2 remain Jeff-only holds. No new
transport, video stack, door copy, short-code, public rendezvous or logging
payload. Existing rotating/redacted diagnostics remain in use. Physical,
two-device, installed-package and live-provider checks remain NOT RUN.
