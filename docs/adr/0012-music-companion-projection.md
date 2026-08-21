# ADR 0012: A companion-safe Music status projection

- Status: Accepted
- Date: 2026-08-21
- Scope: What a Music session publishes outside the desktop window, and what it accepts back

## Context

A companion surface — a Webex Embedded App panel, or anything else living
outside the desktop window — needs to show the song and ask for work. That
surface is being built on a separate track. This ADR covers only the Music half:
the contract, in `core/`, so both sides can be built and proven independently.

Two risks make this worth pinning rather than improvising.

The first is leakage. A Music session knows things a companion must never
receive: the path of the file the host chose, the Music AI API key, and the
signed upload and result URLs a job produces. None of those are needed to draw
a chord chart.

The second is authority. A companion is a surface, not an owner. If a request
from one could name a file, it could name the live Jamulus mix, a meeting
recording, or anything else on the host's disk.

## Decision

`core/music_companion.py` is the entire contract, imports only the standard
library, and holds no transport, no HTTPS start page, and no pairing. It is
deliberately asymmetric.

### Outward: a bounded projection

`MusicCompanionSnapshot` carries where the song is (section, bar, beat,
position, and the source of that position), what it is in (key and BPM, each
with `stated` or `detected`), the chord overlay and one lyric line, whether a
Song tools job is running, and the current suggestion, labelled as one.

It carries **no filesystem path, no API key, and no upload or result URL**.
That is enforced rather than intended: `build_snapshot` scrubs every text field
through a pattern that drops anything URL-shaped, path-shaped, or traversal-
shaped, and bounds every field. The Shared Track is reported as
`shared_track_loaded: true`, never as a filename.

The projection **does** carry musician-authored song text, because showing the
song is the point. That is precisely why it carries nothing else, and why the
overlay is capped at eight rows and the lyric at one bounded line.

### Inward: a request that cannot name a file

`MusicCompanionCommand` has three fields — `name`, `verb`, `section` — and
there is no fourth. A payload carrying `path`, `inputUrl`, or `api_key` does
not fail loudly; those fields simply do not exist, so `parse_command` drops
them. A tool request therefore means exactly one thing: *run this verb on the
Shared Track the host already loaded*. With no Shared Track loaded the answer
is no, and the reason says to choose one on the desktop.

`evaluate_command` is where the desktop decides. A press is a request:

- outside a Music session, nothing is accepted;
- write-help and chord suggestions need no host role, key, or track, because
  they are local, read-only, and upload nothing;
- a tool request additionally requires the host role, a verb the account can
  actually run, a loaded Shared Track, and no job already running.

A companion request never opens a file picker. A dialog nobody asked for, in
front of a musician looking at another window, is worse than a refusal.

### Music is never gated on a companion

With no companion at all, the same state renders in the native session strip
and the Song panel, exactly as it does today, asserted by test.
`core.meeting_companion.music_features_require_meeting()` remains `False`. This
ADR adds a surface; it removes no capability from anyone without one.

Whether the Embedded App itself ships, and under what organization and Control
Hub constraints, is owned by the companion track and by ADR 0007. This ADR does
not decide it and does not depend on the answer.

### ADR 0002 still holds

The projection is creative guidance, not operational truth. It never advances a
conductor phase, changes recording, or alters the primary action, and building
it does no work on a realtime path. Suggestions cross the wire with
`"label": "suggestion"` in the payload, so a companion cannot render one as a
measurement even by accident.

## Consequences

The companion track can build against a stable, versioned shape with a pinned
field list and `describe_contract()` to assert against, without waiting on the
Music side or reaching into it. The Music side gains no dependency on a
companion existing.

The cost is that the projection is a snapshot rather than a stream: a companion
polls or subscribes through whatever transport that track chooses, and
`revision` is what tells it something changed.

## References

- ADR 0002 — unified musician guidance, which this respects
- ADR 0004 — external meeting launch; the meeting stays its own application
- ADR 0007 — Webex Embedded App companion, owned by the companion track
- ADR 0010 — Song tools and Music AI
- ADR 0011 — the shared song clock this projects
