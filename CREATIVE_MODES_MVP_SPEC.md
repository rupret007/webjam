# Creator profiles — v0.26.0 release contract

> Status: Music, Podcast & Voice, and Review & Rehearsal are implemented in
> immutable v0.26.0, the GitHub **Latest** private test release. The exact tag,
> packages, checksum manifest, and protected publication are verified release
> evidence; no physical PASS is claimed. **Art is added after v0.26.0
> and has no release evidence yet**: it is covered by automated tests only, and
> its two-computer behavior is **NOT RUN**. This document supersedes the earlier
> speculative cross-discipline MVP and describes only bounded current behavior.
> Physical and platform-trust results remain **NOT RUN**.

## Product decision

WebJam remains one product and one evidence architecture. A creator profile
selects vocabulary, launch actions, readiness labels, safe defaults, and
capability gates. It does not fork session, recorder, transfer, Studio, export,
security, or meeting-handoff truth.

| Profile | Tier | Live and recording | Completed session take | Standalone project |
| --- | --- | --- | --- | --- |
| Music | GA | Host/Join, Band Check, Shared Track, Record Session | Playback, edit, comp, mix, export | Music Project |
| Podcast & Voice | GA | Host Remote Recording/Join Recording, Sound Check, reference audio, Record Session | Playback, edit, comp, mix, export | Host + Guest or Solo Voice; 48 kHz, time ruler, count-in/click off |
| Review & Rehearsal | Preview | Host Review/Join Review, Session Check, WebJam-audio Record Session | Playback/read-only review | Blocked |
| Art | Preview | Host Art/Join Art, Session Check, optional host-clocked reference video | None — the session is not recorded | Blocked |

Review & Rehearsal also blocks take editing, comping, mix mutation, track
export, shared notes, visual synchronization, and media timecode. No profile
directly or automatically taps a meeting app, browser, or system output.
Scratchpads are profile-scoped on one computer, stored through fixed
private mode-0600 files with regular-file/no-follow reads bounded to 1 MiB.
They are never shared, session-synchronized, or media-timecoded.

## Art (added after v0.26.0, no release evidence)

Art is a room where artists in any medium — painting, drawing, sculpture,
anything on a table — talk while they work. Conversation uses the same WebJam
audio path and the same optional external meeting handoff as every other
profile. The profile adds three capabilities of its own, all optional: a
**shared canvas** it hands to Drawpile, a **host-clocked reference video**, and
an in-session **AI image** action it hands to Krita.

**A room with none of them is the first-class path**, not a degraded empty
player. Nothing about the profile requires anyone to share anything.

Art shipped its Preview under the name "Studio Visit". The old `studio_visit`
key remains only as a migrate-from alias so a saved choice survives the rename.
`visual_studio` deliberately does not move here: a session recorded under that
legacy mode resolves today to a profile that can play it back, and Art records
nothing.

### The three starts

Launch shows exactly three cards for Art, in this order, and the registry
refuses a fourth:

| Start | What it opens |
| --- | --- |
| **Talk & make** | Just the room and your voices. No canvas, no video. |
| **Paint together** | The room, plus one canvas you all draw on. |
| **Paint along** | The room, plus one video you all watch in step. |

Those are the words on the cards. **No component names itself on the first
screen** -- not the painting program, not the audio path, not the image
generator. A person choosing a room should not have to learn what it is built
on to decide whether they want it. Each component introduces itself in the
room, at the moment it matters, and only if something is missing: "Install
Drawpile to paint together" is a one-liner behind Paint together, never a word
on the card.

The name field asks for a name. Its validation is unchanged -- the mixer still
refuses a name it cannot show -- but the field and its preview say so in plain
words rather than naming the component.

A start carries **at most one** add-on. Combining the canvas and the video is
an in-room decision the host makes afterwards, not a fourth card, because the
point of the list is that a person reads it once. **AI is not a start either**:
nobody decides what they are making by choosing an image generator, so it is an
in-session action available from any of the three starts, and the registry
refuses a start that expresses it. A profile that offers starts
must keep a talk-only one, so an add-on can never look required. No other
profile offers start cards.

Joining re-picks nothing. One pasted WebJam invitation carries whatever the
host started, including to an artist who joins late.

### Shared canvas

WebJam does not paint. Real-time collaborative painting is a solved
open-source problem — Drawpile already has the operational transform, the
MyPaint and Krita-style brush engines, layers, ORA and PSD export, and tablet
pressure — so WebJam does for Drawpile what it does for Jamulus: it finds the
real program, launches it, and carries the joining information.

- **Drawpile hosts the canvas.** WebJam opens Drawpile on its own Host page and
  stops there, because Drawpile's host flow asks for a title, a password, and a
  server, and answering those on someone's behalf would be a guess. The
  recommended shape is Drawpile's default **Personal** session, which is
  password-protected, rather than a public listed one.
- **The host shares the invitation WebJam is given.** Both forms Drawpile hands
  a person are accepted: the `drawpile://` (or `ws`/`wss`) session URL, and the
  `https://…/invites/…` link its Invite dialog copies. The web form is
  normalized into the session form exactly as Drawpile's own Join page
  normalizes it, password fragment included, because `--join` skips that
  rewrite.
- **One WebJam invitation is enough.** The canvas address rides the same
  authenticated peer plane as the reference video, so a guest never has to be
  sent a second link through a second product, and a late joiner lands on the
  same canvas.
- **It never touches disk.** Publication is memory-only, like the reference
  video's position, because a Drawpile session password has no business
  surviving in the durable recording journal. A restarted host offers no canvas
  until its owner shares one again.
- **Discovery is explicit.** WebJam bundles no Drawpile and makes no publisher
  claim about one, so it checks a fixed list of absolute install locations with
  no `PATH` search and no glob. A wildcard would let any executable named
  `drawpile` inherit an affordance the artist thinks they granted to Drawpile.
- **Fail closed, always.** No Drawpile means an install path and an honest
  status, never a blank surface implying a canvas is open. A projection this
  computer cannot parse stops before a launcher rather than becoming a URL from
  another machine. A guest has no share and no withdraw.
- **WebJam cannot see the canvas.** It never reports who is painting, and
  leaving a WebJam room closes nobody's Drawpile.

### Reference video

The reference video is the visual analog of Shared Track, with one deliberate
architectural difference. Shared Track sends decoded audio through Jamulus, so
a guest never needs the host's file. A reference video is **not routed
anywhere**: each computer plays its own local copy of the same file, clocked by
the host.

- **The user brings the file.** WebJam ships no video, bundles none, downloads
  none, and ingests from no third-party service. The only source is a local
  file the person already has the right to play.
- **Host-only transport.** Only the host may play, pause, stop, or move the
  position. Guests have no transport control; the follower type exposes none.
- **Same-file identity or nothing.** The host hashes its file's
  descriptor-bound bytes and publishes a *session-scoped HMAC* of that hash
  rather than the hash itself. Peers hold the same session token, so they can
  prove they opened the host's exact file, while the published digest is
  meaningless outside the room and cannot be matched against a known media
  library. A digest captured in one room never matches in another.
- **Fail closed, always.** A computer that has not opened a copy, opened a
  different file, or whose copy later moved, changed, or became unreadable does
  not play. It says which of those happened. There is no silent wrong picture.
- **Late join.** A guest arriving mid-play lands on the host's published
  position advanced by the locally measured age of that projection, so no clock
  synchronization between computers is needed or claimed.
- **A lost host clock stops playback.** If the host's position becomes too old
  to trust, followers hold and say so rather than drifting while claiming to be
  in sync.
- **Hiding is always available.** An artist may ignore the video entirely,
  before or after opening a file, and remain fully in the room.
- **Local player only.** If a computer cannot play video, it says so and stays
  in the room.

### Honesty bar

Sync is host play/pause/stop/seek plus a position corrected on a tolerance
bounded by the peer poll interval — the same bar as Shared Track. It is
**not** frame-accurate review and carries **no media timecode**. Art's
capability set asserts `media_timecode=False`, and the registry refuses to load
an Art profile that claims otherwise.

Because the reference video is not bound into the recording plan's source
identity, **Art does not record a session**. Rather than fake a take whose
sources cannot be proven, the profile disables session recording, and therefore
take review, take editing, and track export. Its conductor offers no Record
action.

### The room clock

Art is the visual half of a room that also carries live audio, and the room
clock is what makes those halves one product rather than two windows. It
answers "where are we right now" once, for everybody, from whoever actually
owns the pulse.

A clock has exactly one owner at a time, and its source is always named:

| Source | Owner | What it states |
| --- | --- | --- |
| `song_form` | a music surface | bar, beat, section, and optionally tempo and meter |
| `reference_video` | Art's host-clocked video | a position in that file |
| `none` | nobody | the room has no pulse |

`none` is a first-class answer. A room where people talk and work has no clock,
and Art says so plainly rather than showing a hopeful zero.

**A song outranks a video.** When something in the room owns a song, that is
the pulse a painter should be riding; a reference video speaks only when no
song does.

**Two rules keep this from becoming a fake music engine.** The first is
structural: a `reference_video` clock **cannot** carry a bar, beat, section,
tempo, or meter, because the wire schema refuses that combination. A file
offset is not a musical position, so no caller can produce one by passing an
extra keyword. The second is that only *elapsed time* is ever extrapolated,
and only by the locally measured age of the projection — the same bounded trick
the reference video follower uses, needing no clock shared between computers. A
bar is rendered exactly as it was published and is never advanced, interpolated,
or derived.

**Art owns no song engine.** The song-form owner is a published seam, and Art
supplies nothing for it: every Art surface works exactly as well with no
musical pulse. A music surface can become the owner later by calling
`publish_room_clock_state`, and no painting surface changes when it does. The
projection is deliberately **not** gated on a creator profile for exactly that
reason.

The readout is one line with nothing to press, shown where a painter already
is. The room has one owner, and someone reading the pulse is not it. A lost
owner stops the clock and says so rather than drifting.

### AI image

Someone in the room can have AI **Make** a new image from text, or **Edit** a
photo they already own. One in-session action, two verbs, same button family.

WebJam generates nothing. The real stack is **Krita AI Diffusion** — a Krita
plugin — driving a **local ComfyUI** backend, which covers generation,
inpaint/outpaint, object removal, and photo editing. WebJam finds Krita, checks
that the plugin is installed in Krita's own `pykrita/ai_diffusion` folder, and
opens Krita on a fresh canvas (Make) or on the artist's file (Edit).

- **No generator of WebJam's own.** There is no prompt box, no model list, no
  LoRA browser, no sampler, and no step count. Krita owns all of it, and
  reproducing any of it here would be inventing a generator rather than
  integrating one. WebJam does not even take the prompt.
- **Loopback only.** The backend address is checked in exactly one place and
  must be on this machine. A remote or cloud address is refused before a
  request is built, including one arriving from an edited config file or an
  environment variable. The probe is a `GET` to ComfyUI's read-only status
  endpoint with proxies and redirects disabled. No path through this feature
  can upload an artist's photo to somebody else's computer.
- **A backend WebJam cannot see is normal.** Krita AI Diffusion installs and
  manages its own server, and also connects to one already running. Both are
  ready states.
- **Results are the artist's files.** They live on that computer and belong to
  whoever made them. WebJam ships no models and no image catalog, requires no
  cloud key for the happy path, and never asks for one.
- **Nothing is published.** The module has no publisher, imports no transfer
  layer, and the session wire schema gains no AI member. A generated image
  reaches the room only if its owner drops it on the shared Drawpile canvas, or
  if the host later shares a file they own under the reference-video contract —
  which remains video, under same-file identity.
- **Nobody drives anyone else's generator.** There is no host and no guest
  here, only this computer. Guests Make and Edit for themselves.
- **Fail closed.** No Krita, or Krita without the plugin, offers an install
  path and says which is missing. Neither verb is enabled until both are real.

The shared canvas is never fed to a model. WebJam does not read it, and any
future choice to do so would be an explicit, separate decision rather than a
side effect of this action.

### Beside the meeting window

Art keeps `meeting_handoff` like every other profile. The conversation and the
faces belong to a meeting app -- Webex primarily, any accepted provider
otherwise -- and Art runs beside that window rather than replacing it.

- Every Art panel is non-modal and narrow, and none opens on anyone's behalf.
- A reference video is **silent**. It is never routed anywhere, so each
  computer holds its own copy, and an unmuted one would lay a second
  soundtrack over the conversation on every machine. The live audio path and
  the meeting app own sound; the video is the picture.
- Only the existing meeting controls may focus or launch the meeting app. No
  Art tick, snapshot, or notice raises a window.
- Nothing in Art reads or writes the saved meeting URL, imports the
  meeting-app service, selects an audio device, or captures screen, browser,
  or system output. Tests enforce this structurally.
- Hiding the video is local to one artist's player, so it costs them neither
  the live audio nor the meeting faces.

See ADR 0004 for the full boundary.

### In-session chrome

Few controls. Talk is already there. The reference video adds host transport
and a guest hide. The canvas adds one open action and a status line — ready,
missing Drawpile, or unreadable. AI adds two buttons and a status line. The
room clock adds no control at all, because it is a readout. There is no brush,
colour, or layer control in WebJam because those belong to Drawpile, and no
prompt or model control because those belong to Krita.

### Explicit non-goals for Art

- no canvas surface inside WebJam, and no brush, colour, or layer control;
- no Drawpile server run by WebJam, and no session posted on an artist's behalf;
- no launch menu of other creative tools; finishing a piece in MyPaint, GIMP,
  Inkscape, Blender, or OpenToonz is the artist's own business. Krita is opened
  only as the host of the AI image plugin, from the in-session AI action, and
  never offered as a start;
- no image generator, prompt, model, or sampler inside WebJam, and no cloud
  image API;
- no reading of the shared canvas by any model;
- no song engine, metronome, tempo detection, or chord inference in Art. Art
  reads a musical pulse that something else owns, and never computes one;
- no camera-on-the-easel feed;
- no shipped, bundled, or downloadable video catalog, and no ripped or ingested
  third-party lesson content;
- no frame-accurate video review and no media timecode;
- no Jamulus reference-audio route;
- no recorded take, take review, take editing, or track export;
- no standalone Art project;
- no Webex Embedded App, companion projection, or in-meeting surface. A free or
  personal Webex account cannot create or load a custom embedded app, so the
  desktop application is the whole product and Webex stays the second window
  described in ADR 0004.

## Persistence and migration

- The selected profile key persists in settings and new session metadata.
- New standalone projects and recorded session evidence retain their profile.
- A legacy project, take, session, or settings record without a profile key
  migrates to Music.
- Previously persisted mode aliases map explicitly to one current profile;
  malformed or unknown keys fail safely to Music rather than inventing a mode.
- Opening a Review & Rehearsal standalone project is refused before mutation.
- `art` is the canonical key. `studio_visit` is a migrate-from alias for the
  Preview that shipped under the old name, and the legacy `visual_studio` mode
  continues to migrate to Review & Rehearsal, so no recorded session silently
  becomes an unreviewable Art session.
- The chosen start key is re-validated against the resolved profile on every
  load and save. A start decides whether a canvas or a video is armed, so a
  stale or foreign key falls back to the talk-only start.
- `visual_studio` remains a valid legacy *mode* key in its own registry, so
  session metadata that records it keeps resolving.
- The retired five-mode list is not offered anywhere as a picker. What someone
  is making is chosen once, at launch, from the creator profiles.
- Every profile owns a distinct private scratchpad file; a profile registered
  without one refuses to start rather than writing another profile's notes.

## Shared evidence rules

Every recording profile uses the same authoritative recording plan. It binds
the exact take, roster/server stems, Shared Track fingerprint and playback
generation, host mono/stereo input topology, guest Local Original obligations,
storage verdict, count-in/pre-roll, and expected source count. Finalization
rechecks that identity and fails closed on missing, extra, changed, or
substituted sources.

Art is deliberately outside that plan. Its reference video is not a
recorded source and is not bound into any take, so the profile disables session
recording entirely rather than producing a take whose sources cannot be proven.
There is one session truth, and Art does not invent a second one.

Before capture, the same path-free readiness sheet shows every exact server,
Local Original, and Shared Track row with mono/stereo topology,
required/optional obligation, readiness/meter, storage, Shared Track state, and
blockers. Start remains disabled until required facts are ready; private plan
authority is checked again before arming. Stable logical-source IDs bind new
recordings through transfer, manifests, recovery, Studio, and automatic exact
repeated-take lanes. Music and Podcast & Voice permit those automatic lanes;
Review Preview does not create them or a Studio sidecar.

Podcast & Voice's local Host + Guest journey is 48 kHz with one mono Host and
one stereo Guest track, persistent chapter markers, record/loop-overdub, and
verified stereo PCM-24 **Bounce Episode**. Review Preview blocks every local
create/open, edit, mix, save, bounce, and export entry point.

Any meeting platform may receive an explicit hardened public-HTTPS link
handoff. Native app verification and focus remain Webex-only. WebJam never
directly or automatically taps a meeting app, browser, or system output.
Record Session can include explicitly planned Local Originals from input
devices the user selects, so users must not route meeting or system-output
audio into those inputs.

## Explicit non-goals across every profile

- no visual canvas or frame-accurate video review;
- no shared or network-synchronized notes;
- no direct or automatic meeting-app, browser, or system-output capture;
- no media timecode;
- no Review & Rehearsal standalone project, edit, comp, mix mutation, or export;
- no Art canvas, camera feed, shipped video catalog, ingested
  third-party lesson content, or recorded take;
- no claim that a link handoff joined, muted, found participants, or recorded;
- no physical-audio, hardware, signing, notarization, or accessibility PASS
  without exact package evidence in the v0.26 checklist;
- no physical two-computer PASS for Art's reference video: its
  host-clocked follow behavior is proven by automated tests only.

Art synchronizes one host-clocked reference video, which is the single
exception to the blanket "no visual synchronization" statement elsewhere in
this document. That exception does not extend to frame accuracy or timecode,
which remain non-goals for every profile.
