# ADR 0012: One room clock, named owners, no music engine in Art

- Status: Accepted for the Art Preview; no release or physical evidence
- Date: 2026-08-21
- Scope: The room-wide pulse projection, and the seam a music surface owns

## Context

WebJam's live room already carried three kinds of truth that never met. Jamulus
carries the audio. Art's reference video carries a host-clocked position in a
file. A shared Drawpile canvas carries strokes. Each surface knew its own
state, and none of them could answer the one question that makes a mixed room
feel like one room: *where are we right now?*

That question is the whole reason this product is not two applications. A
painter working on the cover while the band plays should be able to glance at
the canvas panel and see the bar. A songwriter should be able to see that
someone is painting without being able to drive their canvas. Neither is
possible if the pulse lives inside whichever surface happens to own it.

What exists today does not do this:

- Moises and Chordify overlay facts on a finished solo track.
- Hookpad's Aria suggests chords for a written region.
- Drawpile is a shared canvas with no musical clock at all.
- Jamulus is live audio with no song facts and no canvas.
- BandLab generates a sketch and hands you off to a DAW.
- Endlesss is loops, with no songwriter surface and no paint-along.

The gap is not a better generator or a better canvas. It is a *shared pulse*
that several unlike surfaces can read.

## Decision

Add one room-wide projection, `RoomClockSessionSnapshot`, carried on the same
private authenticated peer plane as the reference video and the shared canvas.

**Exactly one owner, always named.** The projection's `source` is
`song_form`, `reference_video`, or `none`. A reader never infers which kind of
pulse it is looking at, and never has to.

**`none` is a first-class answer.** A room where people talk and work has no
pulse. Art says so plainly. A hopeful `0:00` would be a small lie told
continuously.

**A song outranks a video.** When something in the room owns a song, that is
what a painter should be riding. A reference video speaks for the room only
when no song does.

**Art does not own a song engine, and the schema makes that permanent.** This
is the load-bearing part. A `reference_video` clock *cannot* carry a bar, beat,
section, tempo, or meter: the snapshot's own validation refuses the
combination. A file offset is not a musical position, and making that a wire
rule rather than a convention means no future caller can turn Art into a
metronome by passing one extra keyword. It is not a matter of anybody
remembering.

**Only elapsed time is extrapolated.** A running clock advances by the age of
the projection as measured *locally* — the same bounded trick the reference
video follower already uses, requiring no clock shared between computers. A
bar, beat, or section is rendered exactly as published and is never advanced,
interpolated, or derived. If the owner goes quiet past a bound, the clock stops
and says so rather than drifting while claiming to be in sync.

**The song-form owner is a published seam, and Art supplies nothing for it.**
`RoomClockCoordinator` takes a `song_form_provider` callable; Art's is
`no_song_form`, which returns `None`. Every Art surface works exactly as well
with no musical pulse. A music surface becomes the owner by calling
`HostPeerSession.publish_room_clock_state`, and not one painting surface
changes when it does. Tests exercise that seam with a fake owner so the
precedence rule, the handback, and the no-owner case are all proven before
anything real plugs in.

**Not gated on a creator profile.** Art renders the clock today, but the
projection belongs to the room, not to Art. Gating it on an Art capability
would have blocked the music surface it exists for.

**A readout, never a transport.** The widget has no button, no slider, and no
focus. The room has one owner of its pulse, and someone reading it is not that
owner. The coordinator exposes no `play`, `pause`, `seek`, or `set_bar`, and a
test pins their absence.

**Memory-only.** Like the reference video position and the canvas address, the
clock is never fsynced into the durable recording journal. A restarted host has
no pulse until its owner republishes, so nobody resumes against a bar nobody is
holding.

## Alternatives considered

- **Deriving bars from the reference video position.** Rejected, and now
  impossible on the wire. It needs a tempo and a meter WebJam does not have,
  and it would produce a confident musical claim from a file offset. This is
  the exact failure the schema rule exists to prevent.
- **Letting Art detect tempo from the live audio.** Rejected. That is a music
  engine, and a wrong one is worse than none. Art reads a pulse; it does not
  compute one.
- **Putting the clock inside the reference video projection.** Rejected. It
  would make the pulse a property of Art's video rather than of the room, and a
  music surface would then have to publish through a video feature to be heard.
- **Letting several owners publish at once and merging them.** Rejected. Two
  pulses is no pulse. One owner, named, with a stated precedence rule.
- **A capability gate on Art.** Rejected. The clock's most important future
  owner is not Art.
- **Making the readout scrubbable.** Rejected. A painter moving the band's
  position by dragging a label is not a feature.

## Consequences

A painter on the shared canvas can read the bar the band is on without leaving
the canvas, and a room with no song still works unchanged. The music surface
gets a seam it can own without touching any visual code, and the schema
guarantees it cannot be undercut by a plausible-looking shortcut later.

The cost is one more projection on the peer plane and one more thing to keep
honest. The honesty is enforced by the schema rather than by review.

Two-computer behavior is **NOT RUN**. This contract is covered by automated
tests only, and the `song_form` source has never been published by a real music
surface, because none exists yet.

## References

- ADR 0010: Art's shared canvas as a Drawpile handoff
- ADR 0011: Art's AI image action as a Krita handoff
- `core/reference_video.py`: the locally measured age technique this reuses
