# Worth building — Music listening controls match the native mix

Base: fetched origin/master 2aba2f2f72f94d56f5c7f810fbe7ca326c5502b2.
Independent branch: codex/music-effective-listening-mix.
BEFORE: https://github.com/rupret007/Bob-the-Bot/issues/3#issuecomment-5592279206

A musician can Mute the reference track, adjust its fader to prepare a quieter
level, and still see Mute selected while WebJam sends a nonzero native gain.
Solo suppresses the other channels in the display, but their faders can likewise
send nonzero gain. If the soloed collaborator leaves, the remaining channels
look restored without receiving the native restoration command.

These are reproduced ordinary listening gestures in the production state
manager and native RPC serializer, with a memory sink replacing the send
boundary. Native monitor mute uses fader zero; there is no separate mute bit.
The UDP adapter is dormant in the product and cannot repair a missing or
contradictory native command. Physical audibility is NOT RUN.

Before → after: a muted or Solo-suppressed channel keeps zero effective gain
while its chosen fader changes. Unmute, unsolo, or the soloed musician departing
restores the appropriate stored level and prior mute choices. A late arrival
stays suppressed while Solo is active. Ordinary roster refresh does not reset
the listener's mix.

The existing one-thread-per-write path also allowed an older fader command
to arrive after Mute, or to enter a replacement RPC connection. A coalesced,
single-worker path applies current effective gain only for its participant and
monitor epoch. Commands already sent remain distinct from queued intent.

Real Qt journeys found that an existing participant card did not receive the
model's changed Solo/mute state. The same slice must project the authoritative
listening choices onto the existing cards so their controls agree with the mix.

This helps both reference-track and unaccompanied rehearsal through the existing
mixer. It outranks a new Art request mechanism while the remote-network decision
and complete physical sessions remain open. The change has no dependency on
the pending Art/Music integration drafts, and does not compose their evidence.

Acceptance must cover real native command serialization and real Qt listening
gestures, preserved chosen levels and prior mutes, addressed-channel independence,
roster entry/departure, and unchanged Art door/guest authority. Tests use
synthetic devices and command sinks, never live audio, capture or meetings.
Full local and hosted checks belong to the frozen exact tip; readiness for
independent review does not certify physical sound.

OPEN DRAFT PRE_KAREN only. Existing #96–#104 and parked #37/#49 stay untouched.
No merge/squash/tag/sign/release/Pages/Publish/deploy/spend/live Cisco, public
rendezvous, short codes, second engine, other repository or new goal.
Unsigned 0.27.2 remains Jeff-only. The sustained two-session goal remains open.
