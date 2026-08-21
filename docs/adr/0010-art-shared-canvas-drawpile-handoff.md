# ADR 0010: Art's shared canvas is a Drawpile handoff

- Status: Accepted for the Art Preview; no release or physical evidence
- Date: 2026-08-21
- Scope: The Art creator profile's optional shared canvas

## Context

Art is a room where artists talk while they work. The most requested thing
beyond talking is painting on the same surface at the same time.

WebJam could build that surface. It would need an operational transform for
concurrent strokes, a brush engine, layers, blend modes, pressure and tilt from
a tablet, undo that behaves under concurrency, and an export format other
programs can open. Each of those is a project. Together they are a paint
program, and a paint program is not what WebJam is for.

Drawpile already is that program. It is GPL, it is actively maintained, its
whole purpose is real-time collaborative painting, it carries the MyPaint and
Krita-style brush engines, layers, ORA and PSD export, and tablet pressure, and
it has a public server plus community servers that two people on the internet
can use without either of them running a server by hand.

WebJam already has a precedent for exactly this shape. It does not implement
low-latency audio; Jamulus does, and WebJam finds it, launches it, and conducts
the session around it.

## Decision

Art's shared canvas is a Drawpile session. WebJam brokers it and paints nothing.

**WebJam's three responsibilities.**

1. **Find the real program.** Discovery checks a fixed list of absolute install
   locations, overridable through `WEBJAM_DRAWPILE_CANDIDATES`. There is no
   `PATH` search and no glob, because a wildcard would let any executable named
   `drawpile` inherit an affordance the artist believes they granted to
   Drawpile. WebJam bundles no Drawpile binary and therefore makes no publisher
   claim about one; what it insists on is that the candidate resolves to a real,
   executable, regular file. Symlinks are followed rather than rejected, because
   Flatpak, Homebrew, and Snap all publish their entry points as links.
2. **Speak its invitation language.** Drawpile's desktop client accepts a
   session URL positionally or through `--join`, and its Join page silently
   rewrites the `https://…/invites/…` link that its Invite dialog copies into
   the `drawpile://` form. The `--join` path does not perform that rewrite, so
   WebJam performs the same documented normalization itself, including moving a
   `#password` fragment into Drawpile's `p` query parameter. Both forms are
   accepted from the host; anything else fails closed.
3. **Carry the invitation to the room.** The canvas address rides the same
   authenticated private peer plane as the reference video. A guest who was
   handed one WebJam invitation therefore receives the canvas too, including a
   guest who joins after the host shared it. Nobody is sent a second link
   through a second product.

**What WebJam deliberately does not do.**

- It does not host. Drawpile's host flow asks for a title, a password, and a
  server, and answering those on someone's behalf would be a guess. WebJam
  opens Drawpile on its own Host page and stops, and the copy points at
  Drawpile's default **Personal** session so only people with the invitation
  can join. It never posts a session, listed or unlisted, on an artist's behalf.
- It does not run `drawpile-srv`. Hosting on a home machine needs a forwarded
  port, breaks the web client, and hands out a residential IP address. The
  public and community servers are the path that works for two people on the
  internet.
- It does not hold a Drawpile account, scrape a credential, or require one:
  Drawpile lets a guest continue without an account, and WebJam does not
  override that.
- It has no canvas, brush, colour, or layer control of its own. A second, worse
  copy of Drawpile's tools inside WebJam would be a lie about where the
  painting happens.
- It does not offer a launch menu of other creative tools. Finishing a piece in
  Krita, MyPaint, GIMP, Inkscape, Blender, or OpenToonz is the artist's own
  business, and putting a tool picker at launch would undo the point of showing
  three short choices.

**Honesty and failure.** WebJam cannot see the canvas, so it never reports who
is painting or that anyone opened it. No Drawpile means an install path and a
plain status, never a blank surface implying a canvas is open. A projection this
computer cannot parse stops before a launcher rather than becoming a URL from
another machine. A guest has no share and no withdraw, structurally. Leaving a
WebJam room releases WebJam's pointer to the canvas and closes nobody's
Drawpile.

**Secrecy.** A Drawpile invitation can embed the session password. It is
therefore treated the way WebJam treats its own invitation bearer: kept out of
reprs, never logged, entered through a password-echo field, and published
memory-only so it never reaches the durable recording journal. A restarted host
offers no canvas until its owner shares one again.

## Alternatives considered

- **An in-process Qt paint surface.** Rejected. It would be a toy pretending to
  be a tool, and the honest version of it is Drawpile.
- **Embedding Drawpile's web client in a WebView.** Rejected. It would
  reintroduce a web/media runtime that ADR 0004 deliberately removed, and the
  web client is unavailable for locally hosted sessions anyway.
- **Driving Drawpile headlessly to host on the artist's behalf.** Rejected. The
  host dialog is a real decision about a password and a server. An honest
  handoff beats a guess, and WebJam still automates everything the documented
  CLI allows: the Host page, and joining by URL.
- **Shipping a bundled Drawpile.** Not done here. It would require the full
  bundled-binary trust apparatus WebJam applies to Jamulus — pinned versions, a
  signed catalog, hash verification at discovery and again immediately before
  launch — plus GPL redistribution obligations. Detecting the artist's own
  install is honest, small, and enough for a Preview.

## Consequences

Art gains a real collaborative canvas with brushes, layers, and export that
WebJam could not have built, and gains it without WebJam claiming to draw
anything. The cost is a dependency the artist installs themselves and a handoff
they can see happening. Both are stated in the UI rather than hidden.

Two-computer behavior is **NOT RUN**. This contract is covered by automated
tests only; the handoff has not been exercised against a real Drawpile install
on two machines.

## References

- [Drawpile: hosting sessions](https://docs.drawpile.net/help/common/hosting.html)
- [Drawpile: joining sessions](https://docs.drawpile.net/help/common/joining.html)
- ADR 0004: external Webex launch, for the same find-launch-conduct boundary
  applied to the meeting window
- ADR 0005: the Jamulus reference-track participant, for the bundled-binary
  trust rules a future bundled Drawpile would have to satisfy
