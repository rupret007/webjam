# ADR 0011: One shared song clock, published to every creator profile

- Status: Accepted
- Date: 2026-08-21
- Scope: Session position as a cross-profile contract

## Context

A WebJam session has always had a host, a form the room writes into its notes,
and a tempo. What it has never had is one agreed answer to **where are we**
that anything other than the mixer can read.

Without one, every surface invents its own. The Song panel knows the form but
not the position. The conductor knows the phase but not the bar. Anything
outside Music — a review profile marking a take, a painter working to the
music later — has nothing at all to synchronise to, so it would have to guess
or be handed a Music-shaped object it should not have to understand.

Meanwhile WebJam performs **no beat tracking** on the live Jamulus mix, and
adding some is not a small thing: it needs a tap on the mixed audio, a
detector, and a policy for what happens when it disagrees with the musicians.
Pretending to have it would be worse than not having it.

## Decision

Publish one host-run clock as a first-class, profile-agnostic contract.

### Shared Track owns the position whenever it holds a song

A live session already has one host-owned transport for audio. Whenever the
Shared Track holds a song, that transport *is* where the room is, so the clock
reads its `position_s` instead of counting independently and
`position_source` reports `shared_track`. The panel's start button and locate
control are disabled while that is true, because a second start button beside a
playing backing track is how a band ends up arguing with its own screen.
Releasing the Shared Track hands the count back at the position the room last
heard, rather than snapping to the top of the form.

Guests get this for free: host and guest projections both arrive at
`SessionStrip.set_shared_track_snapshot`, so everyone reads the same bar.

Bar mapping still assumes the file begins at bar one and runs at the room's
stated tempo. That assumption is reported in the copy, not hidden.

### It is a reference, not a measurement

`core.song_clock.SongClock` counts beats, bars, and sections across the form the
room wrote, at the tempo the room stated or a Music AI job detected. The host
starts it. It is a shared click, not a follower.

`SongClockSnapshot.following_audio` is `False` and is part of the published
contract precisely so a subscriber can tell the difference. The UI copy says
the clock "does not follow the band" and "will not correct if the band drifts".
Past the end of the written form it holds on the last part rather than counting
into bars nobody wrote.

If real beat tracking is ever added, it arrives as a new field alongside this
one, not as a quiet change of meaning in an existing one.

### Section lengths are stated or admitted

`[Verse x8]` states a part's length. Where the room has not said, the clock uses
eight bars and reports `section_lengths_assumed = True`, which the panel shows
as "lengths assumed". Time signatures come from `Time: 3/4`.

### The published contract

`SongClockSnapshot.to_public_dict()` is plain JSON-ready values: section, index,
role, bar, bar-in-section, beat, total bars, beats per bar, key, key source,
BPM, BPM source, current chords, the section list, and the two honesty flags.

It contains **no audio, no file path, and no participant identity**, asserted by
test. `describe_contract()` names the fields so a subscribing profile can assert
against them rather than discover a rename at runtime.

`SongClockPublisher.subscribe()` returns its own unsubscribe. A subscriber that
raises is dropped rather than allowed to propagate: a broken canvas must never
be able to stop a jam.

### What this ADR does not do

It does not implement Art, Drawpile, a canvas, or any drawing surface, and a
test asserts none appeared. It publishes the timeline another profile would
need. Building that profile is separate work.

`core/song_clock.py` imports only the standard library and `core.song_form`,
asserted by test, so nothing musical has to be imported to read bars.

## Consequences

The Music room gets a real conductor: the form shows a playhead, the conductor
line leads with position once the clock runs, and "go to the chorus" is one
control. Any future profile gets a timeline it can subscribe to in a few lines
without learning what a stem is.

The cost is that the clock is only as right as the tempo it was given, and it
will drift from a band that rushes. That is stated everywhere it is shown,
which is the honest version of a limitation WebJam cannot currently remove.

## References

- ADR 0010 — Song tools and Music AI, which produces the detected key and tempo
  this clock can run on
- ADR 0002 — unified musician guidance, the existing conductor surface
