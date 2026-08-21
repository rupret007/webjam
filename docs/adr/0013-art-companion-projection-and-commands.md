# ADR 0013: The Art companion projection and command contract

- Status: **Accepted**
- Date: 2026-08-21
- Scope: What an Art room tells a paired companion, and what it accepts back
- Related: ADR 0002 (guidance projection), ADR 0003 (Pocket Stage pairing),
  ADR 0004 (Webex as a second window), ADR 0007 (the companion track),
  ADR 0010 (Drawpile handoff), ADR 0011 (Krita handoff),
  ADR 0012 (the room clock)

## Context

A companion panel showing an Art room inside a meeting window is two new
things at once: a second place Art state is *displayed*, and a second place
commands can *arrive from*. Those are different risks and they need separate
answers.

The display risk is leakage. Art's own snapshots legitimately carry private
detail -- the display name of a file on the host's disk, a session-scoped
identity digest, a canvas server and session label that can imply a password,
the loopback address of somebody's image generator. All of it is fine on the
machine that owns it. None of it belongs in a page rendered by a meeting
client.

The command risk is authority. A panel can be reloaded, backgrounded, shown
on a screen someone is sharing, or driven by a guest. If a companion's
messages were treated as decisions, then host-only transport would stop being
host-only, and a remote message could start programs on someone's computer.

Cisco-side work -- the iframe, the hosted page, the pairing transport -- is a
separate track. This ADR settles only the seam.

## Decision

### The projection is an allowlist, structurally

`core/art_companion.ArtCompanionProjection` carries seven fields: a
generation, a revision, whether this desktop is in a room, three finite
states, and one authority flag.

| Field | Values |
| --- | --- |
| `canvas` | `none`, `ready`, `opening`, `missing_app`, `unreadable` |
| `video` | `none`, `ready`, `playing`, `paused`, `hidden`, `needs_file`, `mismatched_file`, `file_unavailable`, `host_attention`, `stalled` |
| `ai` | `unavailable`, `idle`, `handed_off`, `failed` |
| `transport_allowed` | host only |

The safety property is that there is nowhere to put anything else. Paths,
file names, canvas addresses, digests, tokens, participant names, positions,
and images have no field, so they cannot be added by accident -- only by
changing the type, which fails a test that enumerates it.

Three naming choices are load-bearing:

- **`opening`, never `open`.** WebJam launches Drawpile; it cannot see inside
  it. The state records the launch, not the result.
- **`handed_off`, never `running`.** Same reason. A companion showing a
  progress spinner for a job it cannot observe would be a small lie repeated
  continuously, so the vocabulary has no word for it.
- **No prompt field, not a bounded one.** WebJam never takes a prompt; the
  generator owns it. There is nothing to truncate or redact because there is
  nothing there.

`transport_allowed` is the only authority fact, and it is derived from this
desktop's own role rather than from anything a companion asserts about itself.

### Commands are requests, checked twice

`ArtCommand` is closed: `open_canvas`, `hide_video`, `play_video`,
`pause_video`, `stop_video`, `seek_video`, `ai_make`, `ai_edit`. Each declares
a required scope (`observe`, `canvas`, `transport`, `ai`) and a bounded
argument list. `authorize_art_command` decides one and performs nothing, so
the same rules hold wherever a transport is eventually wired in.

Two guards carry the weight:

**Host-only transport stays host-only.** The four transport commands are
refused with `not_host` unless the projection this desktop built for itself
says otherwise. A guest's companion cannot be handed the host's transport by
any path, including one that claims to be the host.

**Starting a program needs a local yes.** `open_canvas`, `ai_make`, and
`ai_edit` launch software on somebody's computer, so they return
`needs_local_confirmation` rather than `accepted`. A panel inside a meeting
may ask; the person at the desk decides. `hide_video` and the transport
commands change state the desktop already owns and need no prompt.

`ai_edit` deliberately takes no path. A companion cannot name a file on
someone else's disk, so the desktop opens its own picker after the
confirmation.

Every request is bound to a generation and an expected revision. The
generation moves when the room does, so an intent formed in one session cannot
be replayed into the next; the revision moves only when the projected view
actually changes, so binding to it means "what I saw" rather than "when I
asked". Receipts carry a finite reason code and never raw error text.

### Art does not know the companion exists

The dependency runs one way. `art_companion_projection()` reads the
coordinators that already own each fact; no Art surface imports the contract,
which is what makes the no-companion path the only path they have. A test
walks the imports of every Art module to keep it that way.

A pairing changes exactly one desktop behaviour. Without one, an Art panel
raises and activates as always. With one, it opens without taking focus:
someone reading this room in a meeting window does not want the desktop
jumping in front of the faces they are talking to in order to show them what
they are already looking at. That is ADR 0004's focus rule applied to a case
ADR 0004 could not have anticipated.

## Consequences

- A companion can show canvas, video, and image status honestly, and cannot
  show anything private, because the private things have no field.
- A companion is useful to a guest (hide video, open canvas, make an image for
  themselves) without ever gaining the host's transport.
- A companion cannot silently launch Drawpile or Krita on a paired machine.
- The states are coarser than the desktop's. A companion showing
  `missing_app` cannot render Drawpile's install copy; the desktop owns the
  recovery, which is the right place for it.
- Free and personal Webex accounts still cannot load a custom Embedded App
  (ADR 0007). That is a reason the fallback is the product, not a gap in it.

## Rejected alternatives

**Publish the snapshots directly.** Fastest, and wrong. The host's snapshots
carry a file name, a digest, and a canvas label; a projection that starts as a
copy becomes a leak the first time a field is added upstream.

**Bound the prompt instead of omitting it.** Considered because a bounded
prompt would let a companion echo what was asked for. Rejected: WebJam does
not have the prompt to bound. Adding a field would mean *taking* one, turning
Make and Edit into a prompt workflow -- the launch menu Art exists not to be.

**Trust a paired companion with the host's transport.** Rejected. Pairing
proves which device, not which role. Deriving `transport_allowed` from the
desktop's own role means the answer cannot be argued with.

**Let commands act immediately.** Rejected for the three that start programs.
Silent remote launch of local software is the kind of capability that is fine
until the panel is on a shared screen.

**Put the room clock in the projection.** Deferred, not refused. It is
bounded and non-private and would extend cleanly (ADR 0012), but nobody has
asked a companion to show it yet, and the contract is easier to widen later
than to narrow.
