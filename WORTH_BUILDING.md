# Art desktop Host with the complete invitation journey

The verified combined Art/Music candidate #110 still disables Art Host on
Windows and Linux. Independent #111 fixes that door, but does not contain
#110's room-scoped Conversation, lesson requests or Music listening/recovery
work. A user needs those existing capabilities in one candidate.

This is deliberate composition, not a new claim that the parent fixes are
unfinished. Fresh branch `codex/art-host-invitation-composition` starts from
#110 `686aa2b639aea2183ef53d327c3940782d6d21f3`. It applies the base-relative
#111 `89a36efda1332555201a40f0623fe0e67a020aba` LaunchDialog, test and CI delta.
Common fetched master is `2aba2f2f72f94d56f5c7f810fbe7ca326c5502b2`.
The original drafts, including parked #37/#49, remain unchanged.

Before: a Windows/Linux artist cannot Host from the combined candidate's
Make together or Paint along door. The independent platform fix has separate
proof, without the newer complete invitation/context behavior.

After: ordinary Art Host is available on Windows, Linux and Mac in that
combined candidate. One full copied invitation carries the host's saved
meeting. A guest joins through the actual paste/bootstrap path, adopts that
meeting for the room, and retains their own saved meeting and Notes after
Leave. A guest starting in Music does not choose Art again: authenticated room
state selects Art, then Leave restores the saved profile. The authenticated
room still uses the existing LAN transport.

The worth-building proof is the joined-up runtime boundary: enabled Host,
real owned listener, Copy, actual guest bootstrap, authenticated named
presence, temporary guest meeting adoption, and real Leave/End cleanup.
A fresh Host uses its saved meeting; no temporary host context is invented
merely to make the test pass. Parent greens alone do not prove composition.

Keep both Art cards and the squirrel mark. Preserve guest-never-seek, explicit
provider/player opening, the lesson request/acknowledgment limits, and Music
mix/recovery behavior. An explicit native lab request keeps its existing
platform gate; it never silently falls back to an ordinary LAN room.

Success requires the relevant component and composed runtime tests, the full
local test bar, and both exact-tip hosted workflows including all four desktop
builds and their actual-platform Art runtime steps. Source loopback evidence
and packaging do not prove installed-app networking, physical media or
separate-home usability. Network decision and physical Art/Music acceptance
remain open. OPEN DRAFT PRE_KAREN only; Karen review remains pending.
