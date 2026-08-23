# WebJam Changelog

All notable improvements and features for the WebJam creator collaboration platform.

---

## [Unreleased]

> Work after the immutable v0.26.0 release boundary belongs here. Every
> published tag, release, and asset remains immutable historical evidence.

### In-room next step, no leftover Preview lecture

- After Host, Music and Art say one thing: **Copy the invite. That is the next step.** The extra "send it when you want them in" sentence is gone.
- After Join, Art's one next step is **Enter the room.** Once people are here, Music is **Hear each other, then Record / play** — Band Check (F2) stays on the key, not in the sentence.
- Art's empty stage, window title, strip, and notes no longer say Preview. Review still does. Art notes no longer talk like a songwriter.
- The wire now refuses a song clock with no written bar or section. A leftover elapsed-only Shared Track pulse from a peer reads as no clock, not as form.

### Form honesty and Art's one next step

- A Shared Track with no written parts is not a song clock. Painters ride bars and named sections only when the room wrote them; a timer is not dressed up as form.
- The published song clock no longer invents 4/4 from the count's default. Assumed section lengths, "this is a count, not the playing", and the written shape (Verse → Chorus) travel with the pulse so Art sees the same honesty Music already shows.
- Art's HUD is one next step: **Copy the invite** after Host, **You are in** after Join, **make what you came to make** once people are here. The Drawpile / meeting-capture lecture left the HUD; tool names still appear on the chip when something is missing.
- Music's first-screen Host/Join descriptions dropped "this Mac" / invitation-link chrome.

### In-room next step, Shared Track pulse, start-UX CI hold

- Art after Host/Join now has one next step: **Copy the invite**, then **You are in. Wait for the other artist, then listen.** Once connected: **Hear each other, then make.** Music guests are told to play, not to Record.
- A Shared Track without a written form can publish into the room clock, so a painter can ride it. The wire no longer refuses a song clock that only has elapsed time.
- Banned first-screen words fail CI from one shared list, including tooltips, the join page, and a planted-word check.

### In-room next step, quieter Art, shared song clock

- After Host, Music's one next step is **Copy the invite**. After Join, the room says you are in and to wait, then play. Once the band is connected, the host's one action is **Record** — hear each other first, then press it.
- Art no longer inherits Music startup copy ("jam", "band", "instrument") or Review's empty-stage meeting-capture lecture. After Host/Join the room speaks to artists: **Enter the room**, not Enter Studio.
- Music's existing song clock (form or Shared Track) now publishes into the room clock, so a painter can ride the same pulse without a second stack or a new launch card.

### Music door path to Art

- Default Music launch stays **Host** / **Join** only. A quiet **Art, podcast, or review** line reveals the existing room picker, so a first-run musician can reach Art without an integration wall, a profile combo on the Music door, or a third primary action. Returning to Music hides the picker again.
- First-screen picker labels are now just the room names — no `(Preview)` / `(Ready)` chrome. Review’s door is **Host Review** / **Join Review** and “Host or join a review.”, not a Preview or meeting-capture caveat wall.

### Quality review fail-closed chrome

- Art live chrome no longer offers **Record Session**, **Shared Track**, **Recording Setup**, or **Studio** when the profile forbids those capabilities. Switching back to Music/Podcast/Review restores them. Art strip copy addresses artists, not a Review session.
- Saving a Music AI key into the OS credential store now clears the legacy `music_ai_api_key` settings field so a later Host/Join or Settings save cannot write the plaintext copy back.

### Recording recovery and multitrack proof lab

- Landed via [#14](https://github.com/rupret007/webjam/pull/14): recording recovery hardening, guest capture finalization, Studio toolbar readability, local alignment confidence bounds, platform capability test guards, updater catalog time fixtures, exact multitrack proof lab (`tests/support/multitrack_proof_lab.py`, `tools/run_multitrack_proof_lab.py`), annotation-only runtime dependency removal, and auditable Jamulus soak recording preservation.

### Jamulus harness port hold

- Landed via [#20](https://github.com/rupret007/webjam/pull/20): reserve Jamulus ports as one held set in the test harness to reduce `_free_port` TOCTOU flakes on Integration.

### Merge and release map / ten-second UX gate

- Landed via [#18](https://github.com/rupret007/webjam/pull/18): documents the land order, required CI jobs for a release round, and the ten-second UX door law (Art three cards; Music Host/Join only).

### Music song tools, write-help, and shared clock

- Landed via [#17](https://github.com/rupret007/webjam/pull/17): in-jam song tools and write-help on native song objects, one shared song/room clock, Music live door stays **Host / Join** only (no profile picker / display name / local studio on the first screen). BYOK and Music AI keys remain Settings-only; zero-key jam still works.

### Art creator profile (Preview)

- Added **Art**, a Preview creator profile for artists working in any medium —
  painting, drawing, sculpture, anything at a table. It reuses the existing
  session conductor, invite, roster, and meeting-handoff rules unchanged, and
  speaks to *artists* rather than musicians throughout: launch, conductor,
  session pulse, and menu copy. Registry validation refuses to load an Art
  profile whose vocabulary addresses a band.
- Launch renders **start cards from the registry** rather than hardcoding them.
  Art has exactly three, in a fixed order, and the registry refuses a fourth:
  **Talk & make** opens a room and nothing else, **Paint together** adds a
  shared Drawpile canvas, and **Paint along** adds the host-clocked reference
  video. A start carries at most one add-on, so combining them is an in-room
  decision instead of a grid of cards, and any profile offering starts must
  keep the talk-only door open so an add-on can never look required. Joining
  re-picks nothing: one pasted invitation carries whatever the host started.
- The room now carries **one honest line about its canvas or its video**, in
  the session strip beside the other room controls. A host who chose "Paint
  together" used to press Host and land in a room that said nothing about a
  canvas; the only mention was a nine-second message reading "open More →
  Shared Canvas", which is a user interface explaining how to navigate itself.
  - It answers one question — what does this room need from me right now — so
    a missing painting program or a video this computer cannot follow comes
    before a canvas that is simply fine, and there is never more than one line.
  - It is the way in while a host has not set their chosen layer up ("Set up
    shared canvas"), the room's status once they have ("Shared canvas"), a
    recovery when something is absent ("Install Drawpile"), and the only route
    back from a hidden video.
  - **A talk-only room shows nothing at all**, and neither does any other
    profile — not a greyed-out slot, but absent. Someone who chose to just talk
    and work has a finished room, and the chrome agrees.
  - The **image action is deliberately not in it.** It is personal to whoever
    runs it rather than a thing the room has, and including it would make the
    line permanent in every Art room.
  - Neither tone is filled: the strip already has one loud control, so a
    request carries an accent edge and a description carries no accent at all.
    Pinned by rendering the chip at rest and counting accent pixels.
  - Rendered from the same projection a paired companion panel reads, so the
    room and a meeting-window panel cannot disagree about what is happening.
    Reading that state is not depending on a companion, and the line is drawn
    where it belongs: the room may share the state vocabulary, and may never
    touch the command contract or ask whether anything is paired.
  - Pressing it opens nothing by itself. The chip asks, the controller decides,
    and the existing rule against taking focus from the meeting window holds.
- Added an **optional shared canvas, painted by Drawpile**. Real-time
  collaborative painting is a solved open-source problem, so WebJam does for
  Drawpile exactly what it does for Jamulus: it finds the real program,
  launches it, and carries the one piece of joining information a guest would
  otherwise be sent separately. WebJam draws no strokes, runs no Drawpile
  server, holds no Drawpile account, and cannot see the canvas.
  - Hosting opens Drawpile's own Host page rather than guessing at its dialog,
    and the copy points at a **Personal** (password-protected) session rather
    than a public listed one.
  - Both invitation forms Drawpile hands a person are accepted: the
    `drawpile://` (or `ws`/`wss`) session URL and the `https://…/invites/…`
    link its Invite dialog copies. The web form is normalized into the session
    form exactly as Drawpile's own Join page normalizes it, password fragment
    included, because `--join` skips that rewrite.
  - Discovery checks a fixed list of absolute install locations with **no PATH
    search and no glob**. A wildcard would let any executable named `drawpile`
    inherit an affordance the artist thinks they granted to Drawpile.
    `WEBJAM_DRAWPILE_CANDIDATES` overrides the list for an unusual install.
  - Every failure is closed: no Drawpile means an install path and an honest
    status, never a blank surface implying a canvas is open; a projection this
    computer cannot parse stops before a launcher rather than becoming a URL
    from another machine; and a guest has no share and no withdraw at all.
  - The canvas address is **memory-only**, like the reference video's position,
    because a Drawpile session password has no business surviving in the
    durable recording journal. A restarted host offers no canvas until its
    owner shares one again.
- **Art runs beside the meeting window, and now provably so.** Webex is the
  primary meeting platform with other providers still supported, and Art keeps
  the same `meeting_handoff` as every other profile rather than growing faces
  of its own.
  - A reference video is now **silent from its first frame**. It is never
    routed anywhere, so every computer holds its own copy, and an unmuted one
    laid a second soundtrack over the conversation on every machine at once.
    The player already had the mute and the stated intent; nothing had ever
    called it. The live audio path and the meeting app own sound; the video is
    the picture.
  - Only the existing Webex Controls and Show Webex App may focus or launch the
    meeting app. No Art snapshot handler, tick, or notice raises a window, and
    that is checked structurally rather than by review.
  - Nothing in Art reads or writes the saved meeting URL, imports the
    meeting-app service, selects an audio device, or captures screen, browser,
    or system output. Nor does it embed a meeting, add OAuth, or send a blind
    mute shortcut.
  - Every Art panel is non-modal and narrow enough to leave a conversation
    beside it, and a talk-only room leaves nothing on screen at all.
  - Art's copy never claims WebJam joined, muted, or can hear anything.
  - Non-Webex links keep the identical handoff. Webex is primary in copy and
    control labels, not the only accepted host.
- Added a **room clock**: one named pulse the whole room can read, whatever
  kind of maker you are. This is the piece that makes a shared canvas and a
  live song one product rather than two windows -- a painter working on the
  cover can see what bar the band is on without leaving the canvas.
  - A clock has exactly one owner and its source is always stated: `song_form`
    when something in the room owns a song, `reference_video` when Art's
    host-clocked video is running, or `none` when the room honestly has no
    pulse. `none` is a first-class answer, not a degraded one; a hopeful
    `0:00` would be a small lie told continuously.
  - **A song outranks a video.** When something owns a song, that is the pulse
    a painter should be riding.
  - **A file offset is never a bar, and the wire enforces it.** A
    `reference_video` clock cannot carry a bar, beat, section, tempo, or meter;
    the schema refuses the combination outright, so no future caller can turn
    Art into a metronome by passing one extra keyword.
  - **Only elapsed time is extrapolated**, and only by the age of the
    projection as measured locally -- the same bounded technique the reference
    video follower uses, needing no clock shared between computers. A musical
    position is rendered exactly as published and never advanced here. A lost
    owner stops the clock and says so rather than drifting.
  - **Art owns no song engine, and the seam is published.** The song-form
    owner is a callable that Art supplies nothing for, so every Art surface
    works exactly as well with no musical pulse. A music surface becomes the
    owner through `publish_room_clock_state` and no painting surface changes.
    The projection is deliberately not gated on a creator profile for that
    reason.
  - The readout is one line in the shared canvas panel with nothing to press:
    the room has one owner of its pulse, and someone reading it is not it.
    Memory-only like the other live projections, so a restarted host has no
    clock until its owner republishes.
- Added an **in-session AI image action**: Make a new image from text, or Edit
  a photo the artist already owns. One action, two verbs, and deliberately
  **not** a fourth start card, because nobody decides what they are making by
  choosing an image generator. The registry refuses a start that expresses AI.
  - WebJam generates nothing. The real stack is **Krita AI Diffusion** driving
    a **local ComfyUI**, which already covers generation, inpaint, outpaint,
    object removal, and photo editing. WebJam finds Krita, checks the plugin is
    installed in Krita's own `pykrita/ai_diffusion` folder, and opens Krita on
    a fresh canvas or on the artist's file.
  - There is **no prompt box, no model list, no LoRA browser, no sampler, and
    no step count**. Krita owns all of it, and WebJam does not even take the
    prompt.
  - **Loopback only**, checked in exactly one place. A remote or cloud backend
    address is refused before a request is built, including one arriving from
    an edited config file or `WEBJAM_COMFYUI_URL`. The probe is a `GET` to
    ComfyUI's read-only status endpoint with proxies and redirects disabled, so
    no path through this feature can upload an artist's photo anywhere.
  - A backend WebJam cannot see is a **normal state**: Krita AI Diffusion
    installs and manages its own server, and also connects to one already
    running. Both are ready.
  - **Nothing is published.** The module has no publisher, imports no transfer
    layer, and the session wire schema gains no AI member. A generated image
    reaches the room only if its owner drops it on the shared canvas, or if the
    host later shares a file they own under the unchanged reference-video
    contract. The shared canvas is never read by any model.
  - **Nobody drives anyone else's generator.** There is no host and no guest in
    this path, only this computer; guests Make and Edit for themselves.
  - **Fail closed.** A missing Krita or a missing plugin says which one and
    offers that download rather than opening an editor that cannot generate.
    WebJam ships no models, no image catalog, and needs no cloud key.
- Added an **optional host-clocked reference video**. A room with none of the
  three add-ons is the first-class path; nothing requires anyone to share anything.
  When the host does share, everyone watches their own local copy of the same
  file under the host's play, pause, stop, and position control.
- The reference video is **not routed through Jamulus**. Unlike Shared Track,
  which sends decoded audio to guests, each computer plays its own file, so
  same-file identity must be provable across machines. The host hashes its
  file's descriptor-bound bytes and publishes a **session-scoped HMAC** of that
  hash rather than the hash itself: peers holding the session token can prove
  they opened the host's exact file, while the published digest is meaningless
  outside the room and cannot be matched against a known media library. A
  digest captured in one room never matches in another.
- Every reference video failure path is closed. A computer that has not opened
  a copy, opened a different file, or whose copy later moved, changed, or
  became unreadable does not play, and says which of those happened. A guest
  arriving mid-play lands on the host's position advanced by the locally
  measured age of that projection, so no clock synchronization between machines
  is needed or claimed. If the host's position becomes too old to trust,
  followers hold rather than drifting while claiming to be in sync.
- Guests **drive neither add-on**. The reference-video follower type and its
  panel expose no play, pause, stop, or seek control, and the canvas follower
  exposes no share or withdraw. What a guest still owns is local: hiding the
  video, and opening the canvas in their own Drawpile.
- Art **does not record a session**. Its reference video is not bound into the
  recording plan's source identity, so rather than fake a take whose sources
  cannot be proven, the profile disables session recording and therefore take
  review, editing, and export. Its conductor offers no Record action, and there
  is no second session truth.
- Both projections travel beside Shared Track on the private peer plane with
  the same rules: bounded, memory-only, and monotonic. A late joiner's first
  poll already carries whatever the host shared, so nobody has to wait for the
  host to touch something.
- Sync is host transport plus a position corrected on a tolerance bounded by
  the peer poll interval — the same honesty bar as Shared Track. It is **not**
  frame-accurate review and carries **no media timecode**; the registry refuses
  an Art profile that claims otherwise. There is no camera feed, no canvas
  surface inside WebJam, no image generator or model of WebJam's own, no song
  engine, metronome, tempo detection, or chord inference, and no shipped,
  downloaded, or ingested video or image catalog.
- Art ships **no Webex Embedded App or in-meeting surface** of its own. Webex
  stays the second window described in ADR 0004: Show Webex App, Join / Open
  Meeting, and Webex Controls. WebJam's mute and Webex's mute remain separate
  controls, and leaving a WebJam room does not leave a meeting.
- Art does publish a **companion-safe status projection and command contract**
  (ADR 0013) for a companion panel built on a separate track. It is a seam,
  not a surface: no iframe, hosted page, or pairing transport ships here.
  - The projection is an allowlist of finite states — canvas
    (`none`/`ready`/`opening`/`missing_app`/`unreadable`), reference video
    (`none`/`ready`/`playing`/`paused`/`hidden` plus the blocked states), the
    image action (`unavailable`/`idle`/`handed_off`/`failed`), and one
    host-only `transport_allowed` flag. Nothing private has a **field** to
    travel in: no path, file name, canvas address, identity digest, session
    token, participant name, playback position, or image. There is no prompt
    field rather than a length-capped one, because WebJam never holds a
    prompt — the generator owns it.
  - It says `opening`, not `open`, and `handed_off`, not `running`. WebJam
    launches Drawpile and Krita; it cannot see inside them, so a companion is
    given no vocabulary for a progress spinner it could not justify.
  - Commands a paired companion may **ask** for: `open_canvas`, `hide_video`,
    host transport `play`/`pause`/`stop`/`seek`, and `ai_make`/`ai_edit`. Each
    declares a scope and a bounded argument list, and is bound to a generation
    and revision so an intent formed in one room cannot be replayed into the
    next. Receipts carry a finite reason and no raw error text.
  - **Host-only transport stays host-only.** A guest's companion is refused
    with `not_host`, decided from this desktop's own role rather than from
    anything the panel claims about itself.
  - **Anything that would start another program waits for a local yes.**
    `open_canvas`, `ai_make`, and `ai_edit` return
    `needs_local_confirmation`; a panel inside a meeting may ask, and the
    person at the desk decides. `ai_edit` carries no path, so the desktop
    opens its own picker. Hiding the video and moving the transport change
    state the desktop already owns and need no prompt.
- **Art works with nothing paired, and cannot learn to need a companion.** The
  dependency runs one way: no Art surface imports the contract, and a test
  walks their imports to keep it that way. A pairing changes exactly one
  desktop behaviour — with a panel already showing this room, opening an Art
  panel no longer takes focus, because pulling the desktop in front of the
  faces someone is talking to would be the focus-stealing ADR 0004 rules out.
  A free or personal Webex account still cannot load a custom embedded app,
  which is why the fallback is the product rather than a gap in it.
- Fixed the **Join / Open Meeting** tooltip, which told every platform to "use
  Show Webex App to bring Webex forward". ADR 0004 keeps native activation
  disabled on Windows and Linux because their detection does not establish
  publisher proof, and the button was correctly disabled there — but the advice
  was not, so it pointed at a control that could not do what the sentence said.
  The suggestion now appears only where the publisher is verified, and
  detection re-renders it rather than leaving the previous platform's answer in
  place.
- Raised the quiet secondary action from a 30px to a **44px hit target**. Quiet
  is about weight, not size: the fill is still absent, the colour still muted
  and the type still small, but a secondary action should be easy to press and
  hard to mistake for the primary one, which are different problems.
- Pinned the rules that are easy to state and easy to lose, on the desktop and
  on the projection alike. Each was already true; none of them would have been
  noticed breaking until somebody in a real session got a wrong answer.
  - **Two mutes stay two mutes.** No Art panel offers a control whose label
    contains "mute", and none of their copy mentions a microphone or claims to
    reach the call. The projection has no field for a mute and the command
    contract has no verb for one.
  - **Ending one thing never ends another.** No Art panel intercepts its own
    close, so dismissing a window cannot withdraw a share or leave a room.
    Closing Drawpile ends no session, hiding the video leaves nobody, and no
    Art surface — or companion command — offers to end, leave, or disconnect.
  - **Opening a painting program takes nobody's microphone.** Neither launch
    vector carries an audio, device, or mic flag, and no Art module imports an
    audio library or the mixer.
  - **A hosted canvas is personal.** The host command is exactly
    `--start-page host`: never a public listing, never an adult-content flag.
  - **Fail-closed copy is a recovery on one line** — bounded, path-free, at
    most two sentences, and never containing "error", "failed", "exception",
    "traceback", or "capability".
  - **One theme, applied completely.** Each Art panel renders pixel-identically
    under a light and a dark OS palette. WebJam sets a stylesheet but no
    palette, so a widget whose background came from the stylesheet while its
    text colour fell through to the system would be unreadable on exactly one
    kind of machine — and whoever wrote it would never see the broken one.
- `art` is the canonical profile key. `studio_visit` — the key the Preview
  briefly used — is now a **migrate-from alias only**, so a saved choice
  survives the rename. The legacy `visual_studio` mode still migrates to
  Review & Rehearsal, because a session recorded under it must stay reviewable
  and Art records nothing. Unknown keys still fail safely to Music, and Music,
  Podcast & Voice, and Review & Rehearsal are unchanged. The saved start key is
  re-validated against the resolved profile on every load and save, so a stale
  or foreign key falls back to talk-only rather than arming a capability the
  profile no longer has.
- The retired five-mode list is not offered anywhere as a picker, so "Visual
  Studio" cannot resurface beside the profile someone already chose. Its combo
  is hidden, disabled, and unlaid-out, and the legacy mode key still resolves
  so session metadata recording it is unaffected.
- Launch copy and a private scratchpad path remain required for every
  registered profile, so a future profile fails at import instead of as a
  KeyError mid-selection.
- Two-computer behavior is **NOT RUN**: this profile is covered by automated
  tests only and has no release or physical evidence. The Drawpile and Krita
  handoffs in particular have not been exercised against real installs of
  either program, the AI path has never met a real ComfyUI backend, and the
  room clock's `song_form` source has never been published by a real music
  surface because none exists yet.

### Internal

- Discovery rules for a program WebJam did not ship now live once, in
  `core/external_program.py`: explicit absolute locations, no `PATH` search, no
  glob, and a resolved real executable file. Drawpile and Krita share it.

### Test reliability

- Fixed a suite-wide ordering hazard: some startup tests ran the real
  application bootstrap against the shared `QApplication`, which installed
  WebJam's bundled font and its whole stylesheet and left both in place. Two
  layout tests in another file then measured text elision and minimum button
  widths against an appearance their own setup never chose, so whether they
  passed depended on file order. Restoring both also cut the suite's runtime
  roughly in half, because later widget tests no longer re-style against a
  stylesheet nothing asked for.
- Fixed a Band Check dialog test helper that could return before its
  background scan thread had reported, leaving that thread to run the real scan
  after its patch was torn down and to emit into a dialog the finished test no
  longer held.
- Pinned the Jamulus component-update tests to a fixed clock inside their
  signed catalog's validity window. The fixture is issued at a fixed date and
  valid for twenty days, so a service left on the real wall clock accepted it
  and then began rejecting it as `catalog-authorization-stale`, turning the
  file red on a calendar date with no code change.

## [0.26.0] — Demo-proven creator multitrack private test release (2026-08-16)

> Published on 2026-08-16 as immutable GitHub **Latest** release
> [`v0.26.0`](https://github.com/rupret007/webjam/releases/tag/v0.26.0) after
> exact annotated-tag, four-platform CI, eight-asset inventory, checksum,
> fallback, protected-promotion, and public-redownload verification passed.
> Publication is not physical PASS evidence: every physical, hardware,
> provider, accessibility, and release-decision row remains **NOT RUN**. Record
> any later physical evidence only in
> [`V026_CREATOR_MULTITRACK_PHYSICAL_TEST_CHECKLIST.md`](V026_CREATOR_MULTITRACK_PHYSICAL_TEST_CHECKLIST.md).

### Record Session readiness and lifecycle

- Added an accessible, path-free **Record Session Readiness** sheet before any
  recorder, local-capture stream, or Shared Track playback is armed. It shows
  every exact planned server track, Local Original, and Shared Track with its
  participant/source label, mono or stereo topology, required/optional status,
  readiness, bounded meter, storage verdict, Shared Track verdict, and explicit
  blockers. **Start Recording** remains unavailable while a required fact is
  unresolved.
- Accepting the sheet is not recorder authority: WebJam rechecks the immutable
  take/plan generation, roster proof, input maps, guest obligations, storage,
  local device preflight, and Shared Track identity immediately before arming.
  A changed fact or cancellation retires the provisional take without starting
  a recorder or creating media.
- Required guest Local Originals now use a backward-compatible, take-scoped
  arm/ack handshake. The arm is visible only to its required authenticated
  participant; the guest opens its exact frozen device/map before acknowledging
  the plan fingerprint, presence generation, ordered mono/stereo widths, and
  stable source IDs. Jamulus recording start is withheld until every required
  ACK and a final authority recheck succeed. Timeout, disconnect, device-open failure,
  stale or mismatched ACK, and late callbacks cancel safely; exact zero-track
  opt-outs do not block. A fully acknowledged but ambiguously confirmed server
  start can still move to fail-closed Finalizing after an exact confirmed Stop,
  without inventing a start timestamp or discarding guest recovery media.
  A guest that shuts down before observing host commit keeps acknowledged media
  local and recovery-only; it cannot enter the upload queue until authenticated
  state for that same take reaches Recording or a terminal result.
- The creator-facing lifecycle now follows **Preparing → Count-in → Recording →
  Stopping → Finalizing → Ready**, with **Needs attention** reserved for
  preserved media that still requires explicit recovery. Count-in and
  finalization are observed recorder states rather than optimistic UI-only
  transitions.

### Exact source topology and live multitrack feedback

- Every new server stem, Shared Track, host input row, and guest Local Original
  carries one stable logical-source identity from the frozen recording plan
  through transfer receipts, take manifests, recovery, Studio, and repeated
  takes. New recordings fail closed instead of matching by display name, row
  order, or a newly invented fallback identity.
- Server, host, and guest topology is exact and ordered: each source is one
  mono channel or one true stereo pair. Missing topology, duplicate logical
  IDs, changed maps, and missing/extra delivered files are rejected rather
  than flattened or guessed. Capture stalls are padded as disclosed silence
  and remain visible as dropout evidence.
- Studio's recording view can now present the plan-bound server, Local
  Original, and Shared Track rows as separate live lanes with source state,
  meters where available, reported dropouts, and overload warnings. Malformed,
  legacy, or duplicate source projections clear instead of being displayed as
  authoritative.

### Repeated takes and creator journeys

- When a new editable Music or Podcast & Voice take completes, Studio
  automatically stacks eligible earlier takes from the same session as
  repeated-take lanes. Automatic matching requires the same project sample
  rate, a complete or explicitly recovered take, one unique stable logical
  source ID, matching participant/source kind and channel topology, verified
  timing, and the same Shared Track fingerprint where applicable. The action is
  deterministic and idempotent; uncertain or legacy matches remain manual.
- The Podcast & Voice local journey now proves a 48 kHz Host-mono + Guest-stereo
  project through recording, loop overdub, persistent chapter markers, save and
  reopen, and **Bounce Episode** to verified stereo PCM-24 WAV. Track topology
  and immutable recording evidence survive both passes.
- Review & Rehearsal remains Preview and read-only. Completed session takes can
  be played, scrubbed, and inspected, but automatic lane creation, local
  project create/open, arrangement edits, comping, mix mutation, sidecar
  writes, and export are blocked at both UI and lower-level controller entry
  points.

### Meeting and release boundaries

- Webex, Zoom, Microsoft Teams, Google Meet, FaceTime on Mac, and any other
  accepted provider remain optional external HTTPS meeting handoffs. No
  meeting link or native-app focus action becomes a recording source; WebJam
  never directly or automatically captures a meeting app, browser, or system
  output. Meeting-service recording remains owned by that service.
- Began the release with an all-**NOT RUN** v0.26 physical checklist and an
  inert, fail-closed publication stub. Exact tag CI, four-platform
  package/checksum verification, pinned promotion facts, protected
  publication, and public redownload verification subsequently passed, making
  immutable v0.26.0 GitHub **Latest**. The physical decision ledger remains
  honest: every unobserved physical row is still **NOT RUN**.

## [0.25.0] — Creator profiles and authoritative multitrack private test candidate (2026-08-15)

> Published on 2026-08-15 as the immutable GitHub **Latest** private test
> candidate and a new identity after immutable v0.24.0. Exact annotated tag,
> four-platform tag CI, eight-asset release, checksums, fallback proof, post-tag
> pins, protected promotion, and public redownload verification passed. Windows
> remains unsigned; macOS remains ad-hoc signed and unnotarized. Every v0.25
> physical and hardware result remains **NOT RUN**.

### Creator profiles

- Added one persisted creator-profile boundary across launch, Host/Join,
  readiness, live session, recording, Studio, local session records, and new
  standalone projects. Legacy metadata without a profile migrates to Music.
- **Music** and **Podcast & Voice** are GA profiles. Podcast & Voice applies
  episode, reference-audio, speaker, microphone, and Sound Check language
  plus voice-oriented local-project defaults without weakening recording or
  export evidence.
- **Review & Rehearsal** is visibly Preview. It allows live WebJam-audio
  Host/Join, Record Session, local notes, and playback/read-only review of
  completed session takes. It blocks standalone projects, take editing/comp/mix
  mutation, track export, shared notes, visual sync, and media timecode. No
  profile directly or automatically taps a meeting app, browser, or system
  output.

### Authoritative multitrack recording

- A durable per-take recording plan now binds the exact roster/server stem IDs,
  Shared Track content fingerprint and playback generation, host input map,
  guest Local Original obligations, count-in, storage verdict, and expected
  source count before capture begins. Finalization rechecks those exact facts
  and fails closed on substitution, under/over-delivery, or topology drift.
- Local input maps are logical tracks. A mono row creates one mono PCM-24 WAV;
  a stereo row binds adjacent device channels into one true two-channel PCM-24
  WAV. Stereo identity and gap evidence remain intact through recovery, take
  loading, Studio rendering, and export. Opting out every row records no host
  Local Original; only a genuinely empty legacy map uses two mono defaults.
- Each participating guest freezes a take-scoped, path-free Local Original
  count/map fingerprint and presence generation before host recording starts.
  Reconnects, changed maps, missing or extra files, and source substitution are
  refused rather than silently accepted as the planned take.
- WebJam never directly or automatically taps a meeting app, browser, or system
  output. Record Session captures authoritative Jamulus server stems and
  explicitly planned Local Originals from input devices the user selects; users
  must not route meeting or system-output audio into those inputs.

### Meeting-platform and product boundaries

- Any provider can use the hardened external handoff when its URL is public
  HTTPS with a DNS hostname and has no credentials, custom port, IP literal, or
  local/special-use destination. Known providers receive friendly labels;
  unknown accepted providers remain neutral.
- Native application verification, installation guidance, activation, and
  focus-based mute guidance remain Webex-only. A successful link handoff never
  claims meeting join, mute, participants, or recording.
- Added a v0.25 physical checklist and a publisher that began as a deliberately
  non-executable placeholder. It was enabled only after the exact post-tag
  object/commit, tag CI, draft release, body, and asset-inventory pins were
  recorded, then passed protected publication and public redownload checks.

## [0.24.0] — Recording-first workstation private test candidate (2026-08-11)

> Published on 2026-08-11 as the immutable GitHub **Latest** private test
> candidate and a new identity after immutable v0.23.0. Exact annotated tag,
> four-platform tag CI, eight-asset draft, checksums, fallback proof, and
> protected promotion passed. Windows remains unsigned; macOS remains ad-hoc
> signed and unnotarized. Automated evidence does not convert any
> physical-musician **NOT RUN** result to PASS.

### Record Session — one authoritative recording plan (step 1)

- Added `core/session_recording_plan.py`: one immutable
  `SessionRecordingPlan` per take, binding session/take identity, plan
  generation, the proven roster, expected server stems, Shared Track
  fingerprint and playback generation, configurable input maps (up to 32
  rows and 32 enabled Local Original input channels across mono/stereo
  tracks), count-in/pre-roll, the
  storage readiness verdict, expected source inventory, and creation
  time. Construction fails closed on any invalid fact — including
  action-needed storage — the repr is redacted, the public projection is
  path-free counts only, and `plan_fingerprint()` yields a stable digest
  the finalization gate and take manifest can bind so a result produced
  under different facts can never masquerade as this plan's outcome.
- Wired the plan into Record Session: `_begin_recording_start` binds one
  best-effort plan per take (deduplicated durable roster, storage verdict,
  Shared Track planned flag, expected source count) at the exact seam
  where every fact is final and nothing irreversible has happened, stores
  it take-scoped alongside the existing immutable RPC binding, and
  records `plan_fingerprint()` in the evidence journal and take manifest
  through a new optional `SessionEvidence.recording_plan_fingerprint`
  field (additive: legacy journals and manifests load unchanged). Binding
  is recorded, not yet enforced - fingerprint verification at the Ready
  gate lands next - and a binding failure logs and never blocks a take
  that would succeed today.

### Record Session — configurable input-map settings (step 2, part 1 checkpoint)

- Added the `input_maps` settings field: up to 32 named mono/stereo local
  input rows with enable and Local Original opt-in, capped at 32 enabled
  Local Original input channels. It is strictly coerced on load: one
  malformed or over-capacity non-empty map disables supplemental capture so
  it can never silently record fewer or different tracks than the musician
  selected. An intentionally empty map retains the compatibility rule that
  enabled local capture means the fixed two host stems.
  `configured_input_map_bindings()` parses the field into validated plan
  bindings for the coming editor UI. At this intermediate checkpoint, the
  capture layer still recorded the fixed two-stem map; phase 8 below
  supersedes that limitation and makes the configured map authoritative.

### Record Session — capture engine accepts mapped track lists (step 2, part 2 checkpoint)

- Generalized `LocalInputCapture` from the hard-wired two-stem pair to a
  validated list of 1-32 uniquely named mono tracks mapped onto unique
  device channels, still through one input stream, one SPSC ring, one
  writer thread, and the existing crash-recovery checkpoint (which now
  also records the exact track map and true stream width). Track names
  are restricted to a conservative filesystem-safe alphabet and hostile
  specifications fail closed. The default remains exactly the historical
  host-guitar/host-vocal pair. At this intermediate checkpoint, the recording
  coordinator still used that default; phase 8 below completes configured-map
  capture, Local Original classification, and storage budgeting.

### Record Session — per-source recording truth projection (step 3, part 1)

- Added `core/recording_sources.py`: one pure, conservative projection
  from the take's existing evidence (roster, recorder receipts, conflict
  keys, receipt freeze) to one bounded state row per source - armed,
  waiting, recording, conflicted, missing, finalized - for the coming
  live recording workspace. Receipts are treated as identity evidence,
  not liveness: an unproven musician WAITS during recording and can go
  MISSING only after the receipt set freezes; only an explicit conflict
  renders CONFLICTED; the UI-only count-in phase is an active phase; and
  no fingerprint, digest, or path ever leaves the projection. The
  coordinator exposes it via `recording_source_presentations()`,
  snapshotting under the receipt lock so the UI thread can call it
  safely. Rendering on participant cards and the guest-side derivation
  follow, with their exact seams and guard tests recorded in the plan.

### Record Session — per-musician state on the live cards (step 3, part 2)

- Participant cards now show each musician's per-take recording truth as
  a compact text badge on the role line - Armed, Waiting…, ● REC, Needs
  attention, Missing, Saved - fed from the conservative projection, with
  the state also announced in the card's accessible description (never
  color-only) and rendered strictly inside the pinned card geometry. The
  grid refreshes on every recorder phase change and participant push;
  the refresh is presentation-only and can never break recorder state.

### Record Session — guest cards claim nothing they cannot prove (step 3 complete)

- Completed the recording workspace step: guest participant cards render
  no per-musician recording badges, because a guest holds no
  per-musician proof - the authenticated session-wide signal remains the
  guest truth. The guarantee is structural (guest coordinators never
  enter active recording phases, so the per-source projection is empty
  even against stale receipts) and locked by regression.

### Studio — honest Shared Track identity refusals and one vocabulary (step 4)

- Comp lane refusals now tell the truth about Shared Track identity: a
  repeated take that played a different uploaded song says so, and a
  legacy take without source evidence names the missing proof - instead
  of the generic "no unambiguous matching track". The v0.23
  fingerprint-equality gate itself is unchanged (equal, nonempty digests
  or no match) and is now a public reason-carrying predicate.
- Studio speaks one vocabulary: MUSICIAN / SHARED TRACK / LOCAL ORIGINAL
  badges with matching descriptions and inspector text, aligned with the
  live-surface projection and the plan's musician-facing language.

### Studio — one-click undoable Reset Mix (step 5)

- The completed-take Studio mixer gains a "Reset Mix" action beside the
  master controls: one undoable edit returns every track's trim, volume,
  pan, mute, and solo plus the master gain and limiter to defaults,
  while deliberately preserving each track's export inclusion - a reset
  must never silently re-include a track the musician excluded. The
  action no-ops when the mix is already default, keeps widgets and the
  player in sync, and one undo restores the entire previous mix. Export
  invariant suites re-ran green (schema-v2 fail-closed export, shared
  authoritative mix, durable mix ids).

### Conversation — provider adapter, service-aware card, Copy Link (step 6)

- One provider adapter (`MeetingProvider`) now fronts every meeting
  service with recognition facts only: key, label, hostname, platform
  gate, and native-detection support (true solely for Webex, the one app
  WebJam verifies and activates). Future authenticated integrations
  extend this boundary; nothing claims them today.
- The conversation card names the saved link's service in its title -
  "Zoom conversation", "Google Meet video" - while unknown providers use
  neutral Conversation wording. It gains a Copy Link action that is enabled
  only when a validated link is saved and copies the normalized URL with a
  confirmation flash.

### Verification — full-repository closing sweep (step 7)

- All 250 tracked test modules pass under CI-style per-file isolation with
  offscreen Qt; pinned ruff, diff-check, and the release-metadata and
  workflow-limit contracts are clean. The sweep surfaced and fixed one
  robustness gap: the participant-card badge tests now dispose widgets
  deterministically, since interpreter-exit collection of unparented
  QWidgets crashes Qt offscreen teardown. Physical-musician gates remain
  NOT RUN until exact packages are tested by hand.

### Record Session — configured input maps drive recording (phase 8)

- Configured input maps are no longer a wish-list: one resolver maps
  enabled Local-Original entries onto sequential device channels (stereo
  entries become L/R mono stems, hostile names sanitize into safe
  `local-` stems) and drives the actual capture, the per-take recording
  plan, the finalization's required-stem count, the storage budget (per
  track instead of a hardcoded 2), and diagnostics. Take classification
  recognizes configured stems alongside the legacy pair, with stable
  local channel numbering. Only a genuinely empty configuration retains the
  legacy host-guitar/host-vocal pair. A malformed or over-capacity non-empty
  map fails closed, and an intentionally opted-out map records no local stems;
  capture disabled also records nothing. Explicit device-channel selection
  stays reserved for a later editor phase.

### Record Session — input-track editor (phase 9)

- Recording Setup gains an "Edit Input Tracks…" editor: add, name, and
  remove local input tracks, each mono or stereo with enable and Local
  Original opt-in, validated through the same rules the settings loader
  enforces (non-empty, unique, bounded, control-free names; up to 32 rows and
  32 enabled Local Original input channels).
  Saved tracks drive capture through the phase-8 resolver; an empty editor
  keeps the default two isolated stems. A summary line shows the current
  configuration. Explicit per-track device-channel selection stays
  reserved (tracks allocate inputs sequentially, the resolver's default).

### Studio — sticky per-take overload latch (phase 10)

- A clip during playback now stays lit on the lane and master meters for
  the rest of the take instead of vanishing on the next UI tick, so a
  single mid-take overload is actionable. The latch is sticky within one
  playback epoch and clears cleanly when transport restarts or seeks,
  computed inside the existing level lock so the realtime meter path is
  unchanged. `overloaded_sources()` exposes the sticky state (master flag
  plus clipped channel ids) for diagnostics and a future badge.

### Conversation — provider-neutral meeting links

- The saved conversation link now accepts any meeting platform that supplies
  a public HTTPS URL with a DNS hostname through one hardened policy (no
  userinfo, custom ports, local/special-use names, IP literals, percent-encoded
  hosts, or known-brand lookalikes). **Join / Open Meeting** hands any accepted
  link to the operating system exactly once; WebJam still never claims join,
  mute, provider verification, or meeting state. FaceTime links open only on a
  Mac, and the handoff error says so honestly on other platforms.
- Settings, setup wizard, and first-run link fields use friendly labels for
  known Webex, Zoom, Microsoft Teams, Google Meet, and FaceTime links (for
  example "Zoom site: us02web.zoom.us") and neutral wording for any other
  accepted provider. Known allowlisted services may be redacted to their
  origin; unknown-provider URLs and hostnames are fully removed from logs,
  mappings, diagnostics, and Support Bundles.
- Native app detection, Cisco installation guidance, bring-forward, mute
  guidance, and publisher verification remain Webex-only; every other service
  opens through the default browser or its installed link handler.

## [0.23.0] — Shared Track and native multitrack private test candidate (2026-08-10)

> The exact publication state is authoritative at the
> [v0.23.0 release page](https://github.com/rupret007/webjam/releases/tag/v0.23.0).
> Published immutable as GitHub **Latest** on 2026-08-10 from tag commit
> `416186a3ea9cddc1ff01a2b0d61f5e1d5dfc70c8`, tag CI run `31368570400`,
> release `367773776`, and protected promotion run `31371289158`. Publication
> does not convert any physical-musician **NOT RUN** result to PASS.

### One native Shared Track experience

- Reworked the musician-facing live-session feature from **Reference Track**
  into one canonical **Shared Track** surface while retaining the established
  internal route and storage vocabulary where compatibility requires it. The
  host can add a supported local file with **Add Track…** or drag and drop,
  inspect its path-free name, duration, progressive waveform, transport, loop,
  count-in, route, cleanup, and dropout state, and safely **Replace…** or
  **Remove** it only while stopped.
- Added a compact Shared Track deck to the live session instead of making the
  normal rehearsal screen a second DAW. The deck exposes one clear source and
  readiness state, a progressive waveform/playhead, and direct access to the
  complete transport. Guests receive an authenticated, generation-bounded,
  path-free Shared Track projection through the existing peer session but
  never receive transport authority or a false claim of audibility,
  synchronization, isolation, or health. Legacy peer state remains compatible
  and can fall back to dedicated-channel presence only.
- Preserved the separately owned `WebJam Track` Jamulus participant, exact
  source validation and MP3 structural cross-checks, isolated-route proof,
  fixed realtime rings, stale-generation rejection, fail-closed silence, and
  retryable owned-process cleanup. Replacing or removing a source during an
  active route is refused instead of implicitly racing teardown.
- Added a bounded waveform summary to the in-memory UI snapshot. The
  privacy-safe support projection retains only allowlisted state/counters such
  as count-in, route, dropout, and cleanup. Source paths, names, waveform data,
  device identifiers, credentials, invitations, and raw backend errors remain
  excluded from logs and Support Bundles.

### Record Session and take finalization

- Promoted the existing recording pipeline to the primary live action
  **Record Session** with explicit **Preparing**, **Count-in**, **Recording**,
  **Stopping**, **Finalizing**, **Ready**, **Needs attention**, and cleanup
  presentation. Duplicate starts are refused while the current generation is
  preparing, recording, stopping, or finalizing.
- When a loaded Shared Track is ready, confirmed recording start now owns the
  transition into its count-in/playback path. **Stop Recording** requests one
  coordinated stop while preserving the recorder and Shared Track as separate
  owners whose cleanup must each be proven. A playback or teardown failure
  cannot be represented as a successfully finalized take.
- Kept host authority for the shared recorder while showing guests bounded
  recording state, including Finalizing before the host publishes the terminal
  Ready/attention result. Optional Local Originals remain explicit opt-in and
  require valid input setup; they never alter Jamulus's interface, buffer,
  channels, or live mix.
- Retained exact recorder/roster correlation, timing evidence, immutable source
  WAVs, private staging, atomic manifests, checksums, recovery journals, and
  fail-closed ambiguous matching. Shared Track stems now retain a stable
  source identity across takes and are presented distinctly from musician and
  local-original tracks in Studio.

### Studio continuity and DAW-quality presentation

- Made take finalization and Studio read as one workflow: the live surface
  reports finalization before the take becomes ready, and Studio opens the
  immutable take with named participant, **Shared Track**, and explicitly
  enabled Local Original sources rather than generic duplicate labels.
- Polished live and Studio recording controls around one primary action per
  state, consistent Record/Stop wording, visible count-in/finalization, and
  stronger accessible names while preserving WebJam's black, neutral-gray,
  white, and burnt-orange identity.
- Reused the existing non-destructive Studio implementation: aligned waveform
  review, region selection/editing, fades and crossfades, loop/cycle, markers
  and sections, take lanes and comping, mixer controls, bounded undo/redo,
  autosave/conflict recovery, and evidence-rich export remain one canonical
  arrangement system. No Apple assets, artwork, exact layout, or trade dress
  are copied, and no Logic integration is claimed.

### Evidence boundary and known limits

- Advanced both package SBOM identities and the three platform package read-me
  sources to the v0.23.0 boundary. The baked Jamulus compatibility
  matrix authorizes the already audited 3.12.2/3.12.3 identities through exact
  v0.23.0 only and remains fail-closed for v0.23.1. The private testing-release
  lane proves the sealed v0.22.5 catalog is rejected and keeps embedded 3.12.2;
  a new signed component channel is still required for managed 3.12.3 download.
- Automated checks exercise state, identity, source validation, route failure,
  recording coordination, Studio mapping, privacy, and UI behavior. They do
  not prove that a musician heard audio through a physical interface.
- Guest Shared Track state comes from the authenticated versioned peer
  projection, never roster inference. It reports host transport facts for UI
  continuity, not sample-clock synchronization or audibility. A legacy peer
  may expose only dedicated-channel presence, and that fallback remains
  explicitly weaker evidence.
- Guest Local Originals remain preserved before alignment. If the matching
  server reference, transfer completion, or timing evidence is missing or
  ambiguous, Studio shows waiting/unverified media and export fails closed
  instead of guessing.
- Two-machine Jamulus audibility, Shared Track isolation and independent mix,
  server-stem alignment, physical hardware recovery, long-session recording,
  Studio output, external-editor import, accessibility on packaged builds,
  SmartScreen, Gatekeeper, signing, and notarization are all **NOT RUN** for
  v0.23.0. Use the
  [v0.23 physical test checklist](V023_SHARED_TRACK_RECORDING_PHYSICAL_TEST_CHECKLIST.md)
  and record results only against an exact package name, build ID, SHA-256,
  environment, and evidence location.

## [0.22.5] — 2026-08-07 reference-demo reliability private test candidate

> Published as the immutable GitHub **Latest** unsigned/ad-hoc private test
> candidate at the time, after tag CI, exact draft verification, checksums,
> and protected promotion passed. It is preserved as historical evidence.

### Demo safety and presentation

- Added a default-safe pre-start warning when macOS can clearly prove that
  Jamulus will use both a built-in microphone and built-in speakers. The check
  covers first run and saved system defaults, never changes a device, never
  stores or reports hardware names, and times out to an honest unknown state.
- Kept external interfaces, headphones, virtual routes, uncertain evidence,
  and already-running sessions out of the warning path. Declining leaves audio
  stopped; **Start Anyway** is a fresh, non-persistent decision.
- Made Help and About application-modal top-level dialogs that are clamped to
  an available display even when WebJam was restored partly off-screen.
- Aligned every disabled Reference Track transport button to one visual state
  so unavailable actions no longer look brighter than their peers.

### Reference Track — real-world MP3 acceptance and honest playback state

- Fixed the near-universal MP3 rejection: the structural scan now parses the
  gapless (encoder delay/padding) extension for LAME-, Lavc-, and
  Lavf-tagged writers, matching what mpg123 — the decoder inside the locked
  libsndfile build — actually honors, so ffmpeg-encoded MP3s reconcile and
  load. The decoder-side cross-check is unchanged and still fails closed on
  disagreement.
- One trailing APEv2/APEv1 tag (as written by mp3gain and common tag
  editors), before the optional ID3v1 trailer, is now excluded from the
  physical frame walk exactly like ID3v1. Malformed APE tags still reject.
- Every MP3 structural rejection now names its exact bounded, path-free
  reason ("WebJam couldn't validate that MP3 (…)") instead of one generic
  sentence, and Reference Track load failures pass the decoder's message
  through to the panel.
- The Reference Track panel accepts one dragged local audio file anywhere on
  the window as an alternative to the file picker, through the same
  validation and routing-safety path. Multi-file, remote-URL, and
  unsupported payloads are ignored.
- Playback that starves and emits silence is no longer invisible: the
  snapshot carries the stream's bounded underrun counter and the panel warns
  about audible dropouts after 100 ms of cumulative zero-fill while the
  transport reports playing or paused.
- The realtime route-proof freshness budget was raised from 1.2 s to 3.0 s
  so one honest timeout-bounded fader-proof round (2 + roster RPC calls plus
  two CoreAudio scans) cannot latch a permanent mid-song fault on a busy
  multi-musician session. A stalled monitor still silences the stream within
  three seconds, and genuine route failures still stop audio immediately
  through the safety epoch.

### Presentation and packaging text

- The Studio return button is labelled "Back to Live" instead of "Live".
- The three platform package read-me sources identify exact v0.22.5
  pre-publication packages. Their stated condition was satisfied by the
  `WebJam-v0.22.5-SHA256SUMS.txt` manifest and verified promotion; those
  immutable package bytes retain their original warning. Published v0.22.4
  package bytes remain immutable and unchanged.

## [0.22.4] — 2026-08-04 DAW and reliability private test candidate

### Release and presentation

- Published the Reference Studio multi-region editing and loop Overdub work
  as a new versioned test candidate without moving the immutable v0.22.3 tag.
- Added a versioned Jamulus component-catalog channel for exact v0.22.4
  compatibility while retaining the sealed v1 catalog for v0.22.3.
- Hardened macOS disk-image creation against transient `hdiutil` resource-busy
  failures with verified temporary output and bounded retry.

### Reference Studio — DAW-style editing and overdub

- Reference Studio now supports multi-region selection. Shift-click or
  Ctrl/Cmd-click regions to build a selection, or use **Select All**
  (⌘/Ctrl+A). Cut, Copy, Paste, and Delete act on the whole selection as one
  undoable edit. Paste lands the earliest copied region at the playhead and
  preserves every other copy's exact relative offset, so a multi-track phrase
  pastes as one phrase; a paste whose destination track no longer exists fails
  closed. Copies use new durable IDs and never rewrite source recordings or
  tombstones. The **Select All** command is no longer a disabled placeholder.
- Added first-class **Overdub** loop recording (Transport menu, shortcut
  **O**, and a transport-bar toggle). With a loop set, Record loops the cycle
  range and stacks each complete pass as its own take lane with no pass-count
  dialog; comp the result with the existing Option/Alt-drag or Quick-Swipe
  flow. Overdub reuses the sample-accurate, crash-safe project recorder and
  take-lane commit unchanged, so recording identity, durable evidence, and the
  non-destructive boundary are preserved. With no loop set, Record explains how
  to set one rather than recording a straight punch.
- Two-endpoint physical overdub monitoring and audibility remain **NOT RUN**.

---

## [0.22.3] — 2026-08-04 security and reliability private test candidate

### Release and dependency security

- Upgraded the frozen runtime to `cryptography` 50.0.0, the first reviewed
  version that remediates CVE-2026-69247, CVE-2026-69248, and
  CVE-2026-69249 together. Windows, Linux, and Apple-silicon macOS consume
  exact hash-locked upstream wheels.
- Upstream no longer publishes an Intel macOS wheel. The Intel package uses
  one documented, hash-locked native x86_64 source-build exception with a
  privately staged static OpenSSL 3.5.7 LTS build. CI verifies the official
  source archives, build inputs, resulting architecture, OpenSSL identity,
  static linkage, installed runtime paths, license evidence, and final package
  inventory. This exception does not loosen any other target to source builds.
- Published v0.22.3 as GitHub's immutable **Latest** release after exact
  four-platform tag CI, the signed sequence-4 Jamulus catalog, frozen-package
  launches on all four targets, checksum verification, and protected
  promotion all passed.
- The release remains an unsigned Windows and ad-hoc-signed, unnotarized
  macOS private test candidate. Physical two-Mac audio, interface and hardware
  recovery, sleep/wake, long-session, Reference Track audibility/isolation,
  external-editor, SmartScreen, Gatekeeper, signing, and notarization gates
  remain **NOT RUN** unless separately recorded against an exact v0.22.3
  asset and SHA-256.

### Reference Track route ownership and cleanup

- Hardened the controlled macOS source pilot around one global WebJam
  Reference Track lifecycle. Every start gets unique private Jamulus profile
  and RPC-secret names opened through a retained directory descriptor; cleanup
  accepts only the exact files WebJam created or Jamulus's bounded,
  identity-matching profile rewrite.
- The lifecycle claim is held both in-process and by an inherited kernel
  socket in the owned backing Jamulus process. A second WebJam process cannot
  claim either eligible BlackHole route while the first route—or an orphaned
  backing client—still owns it.
- Playback now requires fresh PID-bound CoreAudio proof for both the primary
  musician Jamulus client and the separately owned `WebJam Track` client.
  Uncertain, changed, or stale proof emits silence and tears down the backing
  route without stopping the primary session.
- Startup teardown failures remain visible as cleanup pending. **Stop** retries
  the retained process, RPC, file, and route owners; source replacement and
  application shutdown stay blocked until cleanup is proved. Blocking route
  start, Stop, and Close are serialized inside the core controller so a late
  startup failure cannot be hidden by an earlier clean-close result.
- These are source and machine-test improvements, not production
  certification. Two-endpoint audibility, independent mix, no-direct-monitor,
  recording-stem, failure, 25-cycle, and 60-minute physical gates remain
  **NOT RUN** for the private candidate and cannot be represented as PASS.

### Exact MP3 duration and end-of-song playback

- Replaced trust in an MP3 decoder's advertised duration with a bounded,
  descriptor-bound MPEG Layer III frame inventory. WebJam now verifies the
  complete frame chain, Xing/Info counts, supported LAME or structured
  iTunSMPB gapless metadata, source identity, and exact decoder boundaries
  before Reference Track playback.
- Reconciled raw and gapless-trimmed decoder models so encoder delay and
  padding are excluded without mistaking a valid codec tail for a missing
  song. Damaged, truncated, conflicting, or changing files still fail closed
  with path-free guidance.
- A short decoder block is zeroed and rejected before it reaches the real-time
  ring. The final partial block is committed exactly once, reaches the song's
  validated duration, and only then reports normal end of song.
- Fixed **Restart** during live Reference Track playback so its explicit
  beginning-of-song seek cannot be replaced by the prior callback position.
  Source and frozen-runtime tests now advance a track, restart it at 0:00, and
  then verify exact normal end-of-song playback.

### Permissionless macOS Jamulus profiles

- Host and Join now give the verified non-sandboxed integrated Jamulus
  component a dedicated filename while WebJam keeps that profile, its launch
  workspace, and loopback credential under its own Application Support tree.
  WebJam no longer opens or verifies files in Jamulus's container.
- Reference Track keeps its separate private profile and control credential in
  WebJam's Application Support tree. PID-bound CoreAudio route proof remains
  authoritative; the primary profile's device names are only a secondary
  consistency check.
- The outer Mac bundle no longer declares `NSAppDataUsageDescription`.
  Host, Join, and Reference Track must not ask for Other Application Data or
  Full Disk Access. A fresh Jamulus profile can still require one-time sound
  setup and macOS can separately request microphone access for the chosen
  audio input. The regular `Jamulus.ini` remains untouched.

### Authenticated bounded Jamulus recovery

- Replaced mutable reconnect-field inference with one immutable recovery
  snapshot bound to the exact recovery generation, process generation, and
  process ID. A replacement is accepted only after its authenticated RPC is
  fresh and the same local musician identity appears in the roster; delayed
  proof from an older process cannot authenticate a newer one.
- Kept automatic client recovery to five starts, with pending and in-flight
  work represented explicitly. Exhaustion stops automatic retries and presents
  a fresh explicit Host/Join restart instead of remaining indefinitely in
  Recovering, silently starting a sixth client, or treating process existence
  as a connection.
- Added the same immutable recovery truth to privacy-safe diagnostics and
  Support Bundles: generations, launch/pending/active state, attempts and
  maximum, in-flight/exhausted state, process ID/liveness, RPC freshness, and
  finite RPC age. Monotonic deadlines, paths, profiles, secrets, invitations,
  meeting links, and raw exceptions are excluded.
- This work ships in the immutable v0.22.3 **Latest** private test candidate.
  Publication does not convert any remaining physical gate to PASS.

### Webex native-show reliability

- **Show Webex App** now validates a retained Core Foundation file-reference
  URL against Cisco's designated requirement and passes that same
  filesystem-object identity directly to `NSWorkspace` for both running and
  stopped states. Path replacement cannot redirect the request. It contains no
  meeting URL or document argument; **Join / Open Meeting** remains the only
  meeting-link handoff.
- A running instance is verified before the request. Success then requires a
  fresh exact path/PID match, repeated Cisco process verification, a fresh
  foreground observation, and one final verification/observation pass. Command
  acceptance alone is never reported as success. A stopped launch has its own
  typed result, and Webex remains responsible for the screen it displays.

## [0.22.2] — 2026-07-29 demo-navigation and source-first Track candidate

### Direct musician workflows

- Added always-reachable **Webex** and **Studio** actions to the live session
  rail, plus host-only **Reference Track**. The Trinity three-loop identity and existing
  Record, Copy Invite, and End/Leave ownership remain unchanged.
- Made direct and More-menu entries route to the same canonical workflow.
  **Studio** still selects completed-take review in a live jam and the song
  project workspace in standalone Reference Studio; no duplicate editor or
  meeting lifecycle was added.
- Added compact-size, keyboard, focus, tooltip, accessibility, and duplicate-
  callback coverage for the direct actions at WebJam's supported geometry.

### Truthful Webex handoff

- The main **Webex** action and **More → Webex / Conversation** now reveal and
  focus the Conversation panel without opening the saved meeting link.
- Added distinct **Bring Forward**, **Join / Open**, **Change Link**, and
  **Mute in Webex** actions. Bring Forward requests activation only after the
  installed native app passes the platform publisher check; otherwise
  Join/Open remains available. Only Join/Open performs one explicit,
  single-flight URL handoff bound to the exact link authorized by that click.
  Settings continue to persist only the validated Meeting or Personal Room
  URL.
- External native Webex does not provide a verifiable mute state/control to
  this integration. Mute in Webex therefore brings the app forward for its own
  Mute control and explicitly does not claim to change Webex or Jamulus. No
  blind global shortcut, Webex credential, token, or private meeting path is
  used or logged.

### Reference Track source and diagnostics

- Separated source preparation from playback-route authority. A host can load
  and inspect a valid song while route certification is unavailable; Play
  remains fail-closed until fresh isolation evidence is proven. **Recheck
  Route** refreshes capability without starting playback. A song selected
  during a slow initial route probe is retained in memory and loaded once the
  probe finishes instead of being discarded.
- Consolidated Reference Track probing/decoding on the hardened bounded project
  decoder, accepted real `.wave` files consistently, and advertises MP3 only
  when the packaged runtime proves decoder support. WAV/WAVE, AIFF, and FLAC
  remain the unconditional source formats.
- Added distinct source and route presentation plus allowlisted Support Bundle
  facts for state, format, sample rate, channel count, duration, route reason,
  platform, and backend. Source names, paths, raw errors, URLs, and credentials
  remain excluded.

### Startup reliability and evidence

- Fixed an intermittent slow-host-start race where reconnect supervision could
  cancel the still-active manual Jamulus launch generation and clear its native
  profile, producing a false “couldn't restore its Jamulus profile” failure.
  Reconnect remains generation-guarded and fail-closed for genuine stale work.
- Added focused concurrency, decoder, route-lock, redaction, Webex idempotency,
  native-focus, and Qt navigation tests. The four native packages remain an
  unsigned Windows/portable Linux and ad-hoc-signed, unnotarized Mac private
  test candidate.
- Made Reference Track prepare/load operations generation-bound: Stop, Close,
  session end, or an incompatible Webex route change cancels stale work,
  tears down any unpublished companion, and never resurrects a closed
  controller. Route probes and source selection are bounded/coalesced, while
  teardown retains priority and unproved cleanup remains visible in support
  diagnostics.
- Physical two-endpoint Reference Track audibility/isolation, two-Mac music,
  Webex mute/join state, hardware interruption, sleep/wake, long-session,
  external-editor import, Windows publisher trust, and Apple notarization
  remain **NOT RUN** unless separately recorded against the exact published
  package and checksum.

## [0.22.1] — 2026-07-28 frozen updater reliability candidate

### Packaged Jamulus update checks

- Fixed a package-only TLS trust defect found while exercising the exact
  v0.22.0 Mac draft. Source Python could reach the signed component catalog,
  but frozen Python looked for an OpenSSL CA file that was not present even
  though the release already contained Certifi's reviewed CA bundle.
- The updater now constructs its HTTPS context from the release-locked,
  packaged Certifi certificate bytes, requires hostname verification,
  `CERT_REQUIRED`, and TLS 1.2 or newer, and does not honor
  `SSL_CERT_FILE`/`SSL_CERT_DIR` as alternate updater trust roots. The signed
  Ed25519 catalog, exact host/redirect allowlist, size limits, and component
  hash checks remain unchanged.
- Added distinct, path-free recovery and Support Bundle facts for offline
  access, trusted-connection failure, unusable service response, and missing
  packaged trust data. Musicians continue on the embedded Jamulus 3.12.2
  fallback without a crash or partial update.
- Added a fixed-URL frozen-runtime release probe that fetches and verifies the
  live catalog with the packaged public key and exact WebJam version while
  deliberately poisoning both CA environment variables. It accepts no
  arbitrary URL, key, or trust path.

### Release integrity

- Removed the last hardcoded candidate version from the macOS managed-Jamulus
  resolver; it now receives the running WebJam version from the update service.
- Corrected Latest-promotion policy so the stable
  `jamulus-components-v1` channel tag remains immutably anchored at the original
  v0.22.0 component-channel commit. A higher signed catalog sequence—not tag
  movement—authorizes v0.22.1.
- The v0.22.0 annotated tag and tagged bytes remain immutable failure
  evidence. Its unpublished draft is retained untouched until v0.22.1 is
  publicly verified, then only that obsolete draft is deleted by release ID.
  v0.22.1 is the first candidate eligible for publication after its exact
  four-platform packages, renewed sequence-2 catalog, frozen updater check, and
  GitHub Latest promotion all pass.

## [0.22.0] — 2026-07-28 secure component and integration test candidate

### Jamulus updates without rebuilding WebJam

- Added one immutable `JamulusCompatibility` registry for approved client,
  server, and HEADLESS roles. It records exact upstream tag/commit, target,
  architecture, size, SHA-256, runtime/RPC capabilities, WebJam range,
  activation policy, publisher evidence, licenses, notices, and source offer.
  The desktop retains Jamulus 3.12.2 as its offline and rollback fallback.
- Added an Ed25519-signed Jamulus component catalog with an embedded public key,
  exact WebJam version, 31-day maximum validity, monotonically increasing
  sequence, rollback/equivocation protection, canonical JSON, bounded parsing,
  and exact HTTPS source/redirect host policy. WebJam never follows an upstream
  `latest` pointer.
- Added a private per-user component store with bounded asynchronous checks and
  downloads, cancellation, single-flight and cross-process locks, exact-byte
  revalidation, atomic current/previous state, verified rollback, corrupt-state
  recovery, and the resolution order managed → embedded fallback →
  explicit/system copy.
- Added **More → Jamulus Updates** with truthful checking, available,
  downloading, ready, deferred, installed, fallback, cancelled, and failed
  states. Updates never interrupt the musician client, hosted/practice server,
  Reference Track, recording, reconnect, or launch lifecycle.
- On macOS the updater requires explicit review/acceptance of the exact packaged
  Jamulus license before mounting the DMG, preserves quarantine, and verifies
  the untouched upstream Developer ID Team `V9ZZ6B9WH8`, bundle identities,
  version, architecture, notarization, symlinks, and exact volume inventory.
  On Windows it discloses that the official Jamulus installer is unsigned and
  rehashes it immediately before explicit OS handoff. Linux opens the approved
  package through the desktop handler. No path uses hidden elevation, `sudo`, a
  shell, Gatekeeper weakening, or mutation of `WebJam.app`.
- Approved official Jamulus 3.12.3 client/server inputs are exercised beside
  3.12.2 in real-Jamulus CI. The custom 3.12.3 HEADLESS build remains
  evidence-only and fail-closed pending qualified AGPL section 13 review;
  Reference Track continues to use its reviewed embedded 3.12.2 companion.

### Identity, Webex, and supportability

- Added one Jamulus musician-name validator across onboarding, settings,
  environment/config recovery, native profiles, launch, RPC, and legacy paths.
  It rejects controls, newlines, and values over 16 UTF-16 units rather than
  allowing silent Jamulus truncation, preserves accepted Unicode, and provides
  an accessible 8+8 mixer-label preview.
- Added native Webex app detection. On macOS WebJam verifies Cisco bundle
  identity, Developer ID Team `DE8Y96K9QP`, deep signature, and notarization.
  When Webex is absent or invalid, an explicit action opens Cisco's
  architecture-correct official installer URL. WebJam does not redistribute,
  silently install, authenticate, or update the proprietary Webex app.
- Kept meeting launch truthful and external: WebJam stores only the user's
  Meeting or Personal Room link, while Webex owns sign-in and meeting state.
  Added an ADR for a future focused Webex Embedded App companion whose hosted,
  authorized surface would synchronize with—not replace—the trusted desktop
  audio engine.
- Expanded bounded diagnostics and Support Bundle evidence with path-free
  Jamulus updater/catalog/fallback state and Webex installation/publisher
  state. Raw paths, meeting links, user names, credentials, tokens, downloaded
  URLs, and exception text remain excluded.

### Packaging and release evidence

- Preserved the exact four-platform unsigned/ad-hoc desktop candidate matrix:
  Windows x64 Setup and ZIP, Ubuntu 22.04 x64 ZIP, and DMG/ZIP packages for
  Intel and Apple-silicon Macs, plus one exact checksum manifest.
- Added the exact Jamulus 3.12.3 mixed AGPL-3.0-or-later/GPL-3.0-or-later
  COPYING text, component SBOM, source-offer/build evidence, Windows unsigned
  trust assertion, and macOS no-mount/no-SLA-acceptance input verification.
- The expiring signed Jamulus catalog is published separately from the desktop
  release and therefore cannot change the desktop release's exact
  seven-package-plus-manifest inventory or GitHub Latest semantics.
- Windows signing, macOS WebJam notarization, physical two-musician audibility,
  hardware disconnect/reconnect, sleep/wake, long-session, Reference Track
  isolation, and real Webex/Jamulus simultaneous-audio gates remain **NOT RUN**
  unless evidence names the exact candidate asset and checksum.

## [0.21.0] — 2026-07-28 standalone Reference Studio test candidate

### Project-first songwriting

- Added **Reference Studio** as a standalone project workflow beside, but
  independent from, Host/Join and session-take Studio. **Play Along / Record**
  creates a project around a local backing track; musicians can also create an
  empty project, open a recent project, or drag/import collected media.
- Added portable song-project bundles with durable IDs, immutable checksummed
  media copies, path-free manifests, explicit relink/collection truth,
  conflict-aware atomic saves, bounded autosaves, last-known-good recovery,
  and an atomic **Save As** transaction that clones media and arrangement
  together under a fresh project identity. Existing v0.20.0 session data and
  schema-2 Studio sidecars keep their prior meaning and are not silently
  converted into standalone projects.
- Added the established continuous trefoil—also described in the product as
  the trinity mark—to Reference Studio home and workspace navigation without
  creating a second logo source.

### Play, record, and arrange

- Added asynchronous verified-media preparation, progressive waveform tiles,
  local project playback, bars-and-beats position, click, count-in, cycle, and
  musical snap. Reference Studio owns this local audio path; it does not start,
  join, configure, or feed a Jamulus session.
- Added audio-track creation, naming, duplication, removal, input mapping and
  arming, plus count-in, punch, cycle-pass, and latency-compensated recording.
  Committed recordings become immutable project media, arrangement regions,
  and take lanes. Bounded dropout and pass evidence survives save/recovery, and
  an interrupted post-capture transaction remains an explicit recovery
  candidate instead of being represented as a completed recording.
- Extended non-destructive Arrange behavior to standalone sources: move, trim,
  split, duplicate, disable, delete, fades, cycle ranges, markers, named
  sections and whole-section moves, repeated take lanes, quick-swipe comp
  ranges, and bounded undo/redo. The source media is never edited in place.

### Mix, tempo, and delivery

- Added a standalone mixer with fader, pan, mute, solo, shared-reverb sends,
  master gain, and safety limiter. Added bounded built-in high-pass, EQ,
  compressor, gate, and shared reverb processing, plus exact-frame volume, pan,
  and mute automation. Playback and bounce use the same validated routing and
  deterministic DSP graph.
- Added cancellable backing-track tempo analysis over bounded decoded windows.
  The result includes confidence and always enters a musician-review dialog
  where BPM and meter can be corrected before application. Applying a result
  changes the grid and click only; it does not time-stretch imported audio.
- Added cancellable 24-bit WAV and FLAC bounce for a whole project, the enabled
  cycle, or a selected track, with optional backing and processed stems.
  Publication is atomic and each artifact reports SHA-256, peak dBFS, clipped
  samples, and deterministic RMS dBFS. MP3 bounce is unavailable by default
  because this candidate ships no separately self-tested, identified,
  license-safe MP3 encoder adapter.

### Candidate distribution and evidence boundary

- The v0.21.0 candidate matrix covers Windows x64, Ubuntu 22.04 x64, Intel Mac,
  and Apple-silicon Mac from one source identity. Windows remains unsigned;
  both Mac builds remain ad-hoc signed and unnotarized. These are private-test
  packages, not production-trusted installers.
- Tag automation creates a draft containing exactly the Windows Setup and ZIP,
  two Mac DMGs and two Mac ZIPs, the Linux ZIP, and one SHA-256 manifest. A
  separate manual publisher rejects any other inventory, verifies all seven
  package checksums, publishes a non-prerelease with GitHub's explicit
  **Latest** setting, and verifies `/releases/latest` afterward. A CI artifact
  or draft is not the Latest release.
- Automated tests provide model, renderer, persistence, rollback, UI-contract,
  and packaging evidence. Physical Reference Studio record/playback,
  interface routing, latency calibration, dropout recovery, long-session
  behavior, two-musician audibility, clean-download platform prompts, signing,
  and notarization remain **NOT RUN** unless separately recorded against the
  exact candidate hashes.

## [0.20.0] — 2026-07-27 Webex handoff and Reference Track test candidate

- Restored WebJam's approved continuous trefoil identity from the earlier
  three-loop mark. One canonical analytic curve now deterministically produces
  the desktop SVG, Windows ICO, macOS ICNS, and Pocket Stage AppIcon/visible
  artwork; Help and About use the same mark and byte-contract tests prevent
  packaged assets from drifting.
- Hardened candidate shutdown so WebJam keeps ownership visible and retryable
  until its Reference Track, Pocket Stage, private transfer services, Jamulus
  processes, hosted server, secure transport, and localhost companion listener
  are confirmed stopped. Dynamic Webex and Reference Track status changes are
  now announced to assistive technology.
- Kept Reference Track controls reachable at the supported 760×600 floor and
  made keyboard seek/loop/trim/count-in edits survive stale polling until the
  controller acknowledges them. Join-save failures now stay visible and
  retryable, Settings validation announces and focuses errors, and Notes export
  is unavailable until there is actual note content.
- The ordinary Windows x64 Actions artifact now contains exactly the unsigned
  Setup executable, portable ZIP, and a verified two-entry SHA-256 manifest.
  Windows install/launch/upgrade/uninstall validation uses paths containing
  spaces, and the downloadable candidate artifact is retained for 90 days.
- Added a host-controlled **Reference Track** source pilot behind **More**.
  The bounded 48-kHz engine, separate `WebJam Track` client, authenticated RPC,
  zero-fader checks, transport controls, and path-free decoding are present.
  Production playback is locked before native work because CoreAudio has a
  reported false input-device result after a device switch and Jamulus 3.12.2
  has no independent live-device RPC. Only an explicit constructor seam can
  exercise the backend in controlled source tests; there is no packaged
  setting, environment, command-line, or UI bypass.
- The retained Reference Track implementation fails closed on
  host/session/route/RPC loss and tears down before the primary musician
  client. It is described as Jamulus-routed, not latency eliminated. Physical
  two-endpoint audibility, device-switch truth, BlackHole exclusivity,
  independent mixes, direct-monitor isolation, recording-stem behavior, and
  long-session use remain **NOT RUN**.
- Removed the dormant embedded Webex browser and guest-token path. WebJam now
  persists only the musician's optional Meeting or Personal Room link, opens
  it externally after an explicit action, and reports only that handoff—not a
  Webex join, mute, participant, camera, or microphone state.
- Fixed the visible startup **Bring Jamulus Forward** action, late macOS
  invitation-error delivery, and failed Notes chat sends. An unsent message is
  restored to the composer and is never falsely added to the session record.
- Added discoverable **Help** and **About WebJam** actions under **More**. About
  reports the candidate version, target, trust boundary, and privacy-safe build
  identity.
- Resetting a private invitation now requires explicit confirmation. Webex
  menu wording recovers after a link is configured, and unavailable Jamulus
  guidance no longer claims the application is still opening.
- Made drag-to-Applications plus Apple's app-bundle **Open Anyway** flow the
  primary macOS installation guidance. Optional integrity-checking helpers are
  documented for explicit Terminal use because current macOS versions may
  block quarantined `.command` files from Finder.
- Added visible-menu routing, dynamic Studio **Add Take**, failure-state,
  accessibility, and package-instruction regression coverage. Active guides
  now identify the v0.20.0 candidate consistently.

## [0.19.0] — 2026-07-22 Pocket Stage owner-device test candidate

### Download and startup correction

- Corrected the release/source mismatch that made GitHub's **Latest** v0.18.1
  package appear to contain Pocket Stage even though that immutable release
  predates the feature. The candidate now has its own v0.19.0 identity.
- Added a self-contained **Pocket Stage iPhone Setup** folder to both Mac DMGs
  and ZIPs. It carries the exact generated Xcode project that CI compiled, its
  complete local Swift package, matching desktop version/build metadata, and a
  clickable **Open Pocket Stage in Xcode.command** helper.
- The packaged owner-device path no longer requires cloning the repository or
  installing XcodeGen. It still requires full Xcode, a user-selected unique
  bundle identifier, and an Apple ID Personal Team; no paid Apple Developer
  Program membership or pre-signed iOS binary is claimed.

### Pocket Stage v1

- Added an owner-device Pocket Stage vertical slice behind **More -> Use iPhone**.
  It starts a separate private-Wi-Fi WSS gateway, renders a one-use QR that
  expires in two minutes, and uses an ephemeral self-signed certificate pinned
  by the exact SHA-256 of its leaf DER bytes.
- Added a strict desktop protocol/core, one-use pairing registry, immutable
  mobile projection, bounded gateway, pairing dialog, controller command
  routing, deterministic Swift protocol/transport/state-model tests, and a
  native SwiftUI owner-device app generated reproducibly from a checked-in
  XcodeGen specification.
- The explicitly paired phone can see current session/recording state and
  session-local mix slots with bounded paired-private display labels, then
  change fader/mute, add a Session Canvas marker, or request host recording
  after desktop setup. Labels are excluded from logs, diagnostics, support
  bundles, and the anonymous public Local Companion API.
- Pan remains in the forward-compatible snapshot/protocol vocabulary but is
  not presented or accepted: the pinned Jamulus client has no proven pan RPC.
- Deliberate limits: no phone audio, chat, reactions, solo command, rehearsal
  plan, section/Studio transport, media transfer, or durable reconnect
  credential. The existing Local API and Jamulus audio path are unchanged.
- CI generates and compiles the complete unsigned iOS app and an automated gate
  pairs the real Swift transport with the live Python WSS gateway. Installation
  on an owner's iPhone still requires selecting an Apple Personal Team in Xcode;
  both v0.19.0 Mac containers include the complete owner-device Xcode setup kit,
  but no pre-signed iOS app. Physical iPhone pairing, permissions/firewalls,
  interruption, accessibility, realtime mix/recording, and rehearsal
  non-interference remain **NOT RUN**.

## [0.18.1] — 2026-07-21 unsigned private test candidate

### Downloadable candidate lane

- Restored the reviewed pre-certificate distribution model as an explicit
  candidate lane: version tags build Windows x64, Ubuntu 22.04 x64, Intel Mac,
  and Apple-silicon Mac from one commit and create a draft GitHub release
  without requiring Apple Developer or Windows publisher credentials.
- Candidate filenames state their trust boundary. Windows packages contain
  `UNSIGNED-TEST-ONLY`; macOS packages contain `ADHOC-TEST-ONLY`. The release
  title and opening warning identify the release as a private test candidate,
  and a verified SHA-256 manifest covers the exact seven-package inventory.
- Protected Windows signing and macOS Developer ID/notarization remain intact
  as manual, environment-gated rehearsal jobs for a future trusted release.

### macOS installation helpers

- Both Mac architectures now carry a guided `Install WebJam.command`, a
  separately labeled advanced quarantine-removal helper, candidate metadata,
  and `READ ME FIRST.txt` in the DMG and portable ZIP.
- Both helpers verify the ad-hoc signature, version, build ID, architecture,
  executable inventory, and transport checksum before staging an installation.
  Replacement is rollback-safe and uses `/Applications` when writable or
  `~/Applications` otherwise; neither helper invokes `sudo` or disables
  Gatekeeper globally.
- The guided path preserves quarantine and explains Apple's Open Anyway flow.
  The advanced path requires explicit confirmation and removes quarantine only
  from the installed WebJam bundle before launch.

### Evidence boundary

- Windows remains unsigned and macOS remains ad-hoc signed and unnotarized.
  Physical audio, real interface, clean-download Gatekeeper/SmartScreen,
  signing, and notarization evidence remain **NOT RUN** unless separately
  recorded against the exact v0.18.1 package hashes.

## [0.18.0] — 2026-07-21 unified musician guidance source candidate

### One dependable workflow

- Added one immutable musician-guidance projection over the guarded session
  conductor. The Session HUD, passive participant stage, Session Canvas,
  recorder/Studio feedback, support bundle, and optional Companion API now use
  the same phase, stable action ID, evidence category, recovery category, and
  recording/take/guest-media/Studio/export results.
- Brought native Jamulus setup and topology-specific recovery wording through
  bounded typed display overrides. The fixed copy can be more helpful without
  replacing conductor phase/evidence truth or producing a separate UI state
  machine. Studio-owned actions remain in Studio instead of adding a duplicate
  HUD button.
- Evolved Session Canvas with a calm NOW record for status, next action, why,
  output results, and recent reason-free transitions. The separate Creative
  Pulse continues to extract local decisions, actions, blockers, questions,
  references, and checkpoints, but notes cannot create operational truth.

### Recording, Studio, and recovery truth

- Connected confirmed recorder, take validation, guest Local Original capture
  and transfer, Studio selection/validation/dirty/save state, and export worker
  outcomes into one output vocabulary. Process start, button press, stop
  request, meter activity, and note text remain insufficient proof.
- Added a non-identifying Studio take revision so a completed export is cleared
  after selecting another take or making a new edit. Manifest warnings no
  longer block an otherwise supported export, while actual manifest errors,
  review-only state, or export blockers still require attention.
- Separated Studio persistence failure from safe load/recovery notices. A
  failed arrangement save stays dirty and keeps the take open; leaving Studio
  or closing the app cannot make that unsaved work inaccessible.
- Guidance refresh is semantic and idempotent. It does not run from meter,
  waveform, playhead, animation, audio, capture, or playback timers/callbacks.

### Privacy, accessibility, and public contracts

- Added bounded accessible descriptions and visible shared guidance to Canvas
  and normal-size Studio while retaining the tested 760×600 composition. The
  compact Studio continues to rely on the always-visible HUD for its dominant
  next action and exposes the full state to assistive technology.
- Reduced Companion participants to anonymous session-local slots and removed
  musician names, internal channel IDs, server address, Webex state, and raw
  connection objects. Public guidance contains finite values only; creative
  notes and local explanatory copy remain private.
- Support bundles strictly re-sanitize guidance. Recorder and localhost API
  failure responses use fixed messages rather than raw exceptions, paths, or
  tokens.
- Updated the native transport dependency set to `golang.org/x/net` 0.56.0
  (with its compatible `x/crypto` and `x/sys` versions) after the CI
  vulnerability gate identified GO-2026-5942 in the prior indirect version.

### Evidence boundary

- No v0.18.0 package has been promoted. Physical two-Mac audibility,
  interface disconnect/reconnect, sleep/wake, Local Originals on real devices,
  long-session use, real-output Studio review, external-editor import, signed
  clean installation, and platform trust/notarization remain **NOT RUN** until
  recorded against an exact candidate build.

## [0.17.0] — 2026-07-19 Studio arrangement source candidate

### Arrange editing and take-lane comping

- Replaced the mix-only Studio sidecar with an immutable, frame-domain
  arrangement document. Studio now supports moving, trimming, splitting,
  duplicating, disabling, and deleting regions; per-region fades and validated
  crossfades; markers and sections; cycle/loop playback and snap state; track
  trim/fader/pan, mute, solo, and export inclusion; and master gain/limiter
  choices.
- Added a responsive Arrange timeline with fixed track headers, zoom and
  horizontal/vertical navigation, ruler/playhead, visible-region waveform
  tiles, fades, crossfades, markers, cycle ranges, and keyboard undo/redo and
  region actions. Arrow-key row/region selection, keyboard nudge/trim,
  take-lane audition/comp, and named-section movement provide a mouse-free
  editing path. Rendering work is culled to the visible viewport.
- Named section bars can now be dragged to reorder Verse/Chorus song blocks as
  one undoable ripple edit across every track. Regions split at permutation
  seams with their exact affine source mapping, contained arrangement metadata
  follows the block, and unsafe seam-crossing intervals fail atomically. The
  player reloads the result while source media and tombstones remain unchanged.
- Enabled cycle ranges now loop playback on exact project frames even when a
  boundary falls inside an output-device block. For cycles of four frames or
  more, a deterministic short seam fade covers short and multi-wrap device
  blocks without changing transport frame counts. One- through three-frame
  pathological cycles deliberately remain sample-exact and non-silent, so
  their raw seam is not de-clicked. Physical click-free playback remains
  **NOT RUN**.
- Added repeated-take lanes for complete or explicitly recovered takes from the
  same session, sample rate, and unambiguous musician. Musicians can audition a
  lane without changing the saved comp and Option/Alt-drag ranges into a comp;
  new selections split prior overlaps and use short equal-power boundaries.
  Removing a lane tombstones only Studio choices—the repeated recording stays
  in Takes.

### Recording truth, persistence, and history

- Arrangement state contains durable take/track/segment IDs and integer frames,
  never source paths. The take manifest and every source WAV remain read-only
  during load, edit, comp, undo/redo, autosave, playback, and export.
- Added bounded immutable undo/redo history with exact snapshot restoration,
  stable IDs, coalescing for adjacent continuous-control gestures, divergent
  redo invalidation, and both entry-count and serialized-byte limits. An edit
  remains current even when it is too large to retain as an undo step.
- Added coalesced autosave of the schema-v2 Studio sidecar with exact-byte
  compare-and-swap tokens, process and cross-process locking, atomic replace,
  last-known-good backup recovery, and fail-closed conflict handling. A failed
  save keeps the edit dirty and retryable; switching takes never silently
  discards unsaved work, and application close is refused if the final save
  retry still fails. Schema-v1 mix choices migrate in memory and their original
  bytes are preserved on the first explicit schema-v2 save.

### Waveforms, playback, and export provenance

- Added cancellable, viewport-scoped waveform tiles with bounded LRU entry and
  byte budgets. Declared recording gaps render as silence, stale generations
  cannot publish into a newly selected take, mix-only changes reuse valid
  tiles, and source-identity changes invalidate them. Trusted media is opened
  without following symbolic links and is rechecked against its durable
  identity and checksum.
- Added a deterministic sparse 12-track, 60-minute workspace stress gate. It
  passes load/edit/save/reopen, compact Arrange zoom/viewport, bounded waveform
  scheduling, bounded-block export cancellation/cleanup, and unchanged source
  hashes without allocating or playing a full session. Physical long-session
  operation remains **NOT RUN**.
- Playback and export now share the arrangement renderer for frame/rate
  conversion, regions, fades, crossfades, comps, gaps, track state, and master
  delivery behavior. Cross-take sources require a trusted full
  take/track/segment catalog and fail closed if source or manifest truth changes.
- Playback checksum validation and source-reader preparation now run on a
  cancellable generation-tagged worker. Audio callbacks use preopened
  descriptor-bound readers without open/stat/fstat work; stale take/edit
  preparations cannot start output, and terminal source failures are drained on
  the UI thread.
- Studio export transactionally publishes equal-length 24-bit edited stems,
  aligned unity originals, a rough mix, markers/sections CSV, import
  instructions, the exact Studio document, source take manifests, provenance,
  and SHA-256 checksums. Provenance records selected tracks and source keys,
  input/output hashes, timeline identity, clipping counts, and an explicit
  external-editor validation status of `NOT RUN`. Cancellation and
  pre-publication failures remove the unpublished package without changing
  source evidence.
- Descriptor-relative edited Studio packages are available only on macOS and
  Linux runtimes with the required secure directory APIs. Windows labels its
  separate path **Export Aligned Originals**: it applies current trim, fader,
  pan, mute, and solo choices to a reference mix, but excludes arrangement
  edits, fades, comps, sections, master processing, and attached/repeated take
  lanes. A failed edited Studio export never silently falls back to that path.

### Evidence boundary

- Automated model, persistence, history, controller, renderer, comping,
  waveform, export, and headless Qt interaction tests cover the source
  implementation. They do not prove physical audibility or hardware behavior.
- No `v0.17.0` package has been promoted. Real two-Mac listening, physical
  interface disconnect/reconnect, sleep/wake, interruption and recording
  recovery, long-session operation, external-editor import, signed clean
  installation, and platform trust/notarization gates remain **NOT RUN** for
  this source candidate. The published `v0.16.3` private test artifact remains
  the current rollback/reference package until those gates are performed.

---

## [0.16.3] — 2026-07-17 private cross-platform test build

### Session stability and studio behavior

- Kept the simple Host/Join-first flow from `v0.16.2` and carried forward the
  recording/Studio behavior hardening from the then-active
  `codex/webjam-zero-friction-recording-ultimate`
  branch. Late callbacks remain guarded by generation checks, and Studio exports
  continue to distinguish verified aligned alignment from unverified playback-only
  originals.
- Improved installer packaging reproducibility for Windows x64 and retained
  artifact identity controls for cross-platform packaging output.
- Added a force-restart recovery path when the Jamulus process stays alive but
  stops answering JSON-RPC heartbeats, so auto-reconnect now recovers stalls
  as well as hard process exits.

### Release boundary

- The primary GitHub release now points to `v0.16.3`, based on build ID
  `4d8c04684ee29ab2ea36ae38dc3be8ac6d612c7a`.
  Release assets have been intentionally cleaned to a single public artifact:
  `WebJam-v0.16.3-RC-4d8c046-windows-x64-setup.exe`.
- This is still a private test candidate: Windows is unsigned, both macOS
  builds are ad-hoc signed and not notarized, and physical two-machine,
  browser-quarantine, and managed-device gates remain outstanding.

### Native desktop packages

- Added native Windows x64, Intel macOS x64, and Ubuntu 22.04 x64 build gates
  from one source commit. Every deliverable is freshly installed, mounted, or
  extracted and checked for application/transport architecture, exact build
  provenance, transport hash and protocol lifecycle, required data, and a clean
  frozen UI launch.
- Added direct GitHub-ready desktop installers: a per-user Windows Setup `.exe`
  with Start-menu and optional desktop shortcuts plus clean uninstall, and
  drag-to-Applications `.dmg` files for Intel and Apple Silicon Macs. The
  portable ZIPs remain available as fallbacks.
- Added the first Linux client package. It carries the checksum-pinned official
  Jamulus 3.12.2 Ubuntu `.deb`, visible install instructions, lowercase binary
  discovery, x86-64 ELF validation, and a packaged-app smoke against a private
  JACK graph with authenticated Jamulus RPC and clean process shutdown. Linux
  and Windows remain truthfully Join-only; managed hosting remains macOS-only.
- Fixed fresh Windows installs: the Host/Join dialog now exposes the packaged
  Jamulus installer from PyInstaller's real `_internal` data root. WebJam
  requires the exact 3.12.2 filename and pinned SHA-256 both at discovery and
  immediately before launch.
- Tagged Windows builds now require valid Authenticode credentials and verify
  both payload executables, Setup, and the embedded uninstaller after a real
  fresh-install cycle. Branch builds remain usable for legacy v1/v2 testing but
  state that secure packaged v3 fails closed when unsigned. The upstream
  Jamulus installer has its own unsigned-publisher UAC limitation.
- Test Night evidence now records the actual desktop target, including Intel
  macOS, Windows x64, and Linux x64. Release automation refuses to mutate a
  previously published tag, creates new tag releases as drafts for exact
  hardware certification, and attaches a verified SHA-256 manifest covering
  the seven direct and portable release assets.
- Tagged Mac builds now fail before packaging until the full Developer ID and
  Apple notarization path is implemented and proven with release credentials.
  Branch artifacts remain explicitly ad-hoc test packages, and the prepared
  outer-app entitlement boundary includes hardened-runtime camera and direct
  audio-input access plus Qt WebEngine's separate microphone entitlement.

### Reliability

- Increased the packaged guest transport's cold-start allowance to 30 seconds
  before invitation enrollment. Slow first launch on Mac remains bounded and
  retry-safe because the startup timeout still occurs before the one-use
  capability is sent. Canceling during that wait is prompt, reaps the child,
  and sends no protocol command.
- Replaced an immediate JACK graph assertion with a bounded convergence check
  that retains process-health checks and reports every missing route on timeout.
  This removes an observed CI race without retrying or weakening the real
  Jamulus integration gate.

### Distribution boundary

- Intel/Apple Silicon macOS apps and DMGs remain ad-hoc signed and non-notarized
  private test builds. Windows publisher signing, physical interface audio,
  audible two-musician proof, and Ubuntu hardware audio remain release gates;
  automation does not claim human audibility.

### Source verification

- PR #2's description recorded **1,908 passed**; its later authoritative
  GitHub run `29546741915` recorded **1,909 passed**, 19 environment-bound
  skips, one dependency warning, and 6 subtests passed. Ruff, Go tests/vet,
  workflow YAML, and Actionlint passed; native archive evidence is recorded
  only after the matrix builds finish from the committed candidate.

---

## [0.16.2] — 2026-07-16 test-build release candidate

### Simple session flow and take safety

- Normal Host and Join now move to the session automatically after fresh,
  authenticated Jamulus connection and local-identity evidence. Musicians no
  longer have to finish a WebJam sound wizard, confirm a startup Webex choice,
  or press an extra Enter Jam action. Jamulus remains visible for its own audio
  setup, and WebJam never treats connection evidence as proof of audibility.
- Startup, retry, reconnect, invite, recording, and shutdown events now use a
  generation-guarded session snapshot. A late callback from a replaced or
  failed attempt cannot redraw or cancel the current session.
- Incoming Local Originals remain preserved first. Timing alignment is allowed
  only against the matching verified server reference with sufficient anchors,
  confidence, and residual evidence; otherwise Studio keeps the media visible
  but blocks it from a misleading aligned export.
- Recording maintenance now runs outside the Qt completion path and project
  manifests use a short per-take lock plus exact-revision replacement. Late or
  competing work retries safely rather than overwriting newer take truth.

### Release boundary

- The GitHub Latest release publishes the primary macOS Apple-Silicon test build
  from `c4bc5e8fd40f54efc85d0a4af504cf627ec44106`:
  `WebJam-v0.16.2-TEST-NIGHT-macos-arm64.zip`, SHA-256
  `5855af408c5182408d091c9029bdfa61d8f9abf96801822df319d55f649e688d`.
  A fresh extraction passed deep signature, bundled-engine executable, and
  arm64 transport build-ID/checksum verification.
- The build is ad-hoc signed and not notarized. Physical two-Mac audio,
  hardware change/recovery, long-recording recovery, and external-editor
  import remain **NOT RUN** until musicians perform and record those checks.

---

## [0.16.1] — 2026-07-15 private stabilization candidate

### Stability and truthful recovery

- Added the isolated Dual-Musician Rehearsal Lab. It exercises real WebJam
  host/guest peer sessions, loopback HTTP transfer, durable identities,
  deterministic capture fixtures, Studio state, Track Export, stale-invite
  rejection, and cleanup. Its optional Linux/JACK companion exercises real
  JamulusServer and two Jamulus clients without claiming human audibility or
  physical hardware proof.
- Private v2/v3 bearer invitations are no longer accepted from process
  arguments, and WebJam URLs are removed before Qt retains argv. Pasted and
  FileOpen invitations retain their typed ingress path.
- Fixed a peer-media collision where two musicians could reuse a local segment
  UUID and one verified original would be omitted from the host project.
- Transfer descriptors now preserve exact structured capture gaps, reject
  metadata changes after both partial and completed upload, and fail closed if
  a crash-orphaned published WAV has no authoritative descriptor sidecar.
- Host shutdown now cancels stale maintenance lifecycle work: an old worker
  cannot write a manifest or notify the UI after Leave/End or rapid restart.
  Incomplete peer HTTP uploads are also released during shutdown.
- Replaced the previous trinity glyph with the supplied three-loop, three-ring
  WebJam mark. It remains a simple native vector/icon in black, white, and
  burnt orange only.

### Verification

- Source gate: **1,798 passed**, 19 environment-bound skips, 1 known
  dependency warning, and 6 subtests with zero failures/errors.
- Built the private Apple-Silicon archive from
  `7c6e7e2533facdb6162d180d57256a5a101faad8`:
  `WebJam-v0.16.1-TEST-NIGHT-macos-arm64.zip`, SHA-256
  `a983b06781a6af9a9fb3ddff7a2f3852192fa044fdb116a7a73357c4f3546fdd`.
  The checksummed official Jamulus 3.12.2 DMG was freshly staged; a fresh
  extraction passed strict/deep outer and nested signature checks, arm64
  fabric build-ID/checksum verification, and a bounded frozen Host lifecycle
  smoke with no leftover owned process. The archive is ad-hoc signed for
  private testing, not notarized. Physical rehearsal evidence remains
  **NOT RUN**.

---

## [0.16.0] — 2026-07-15 test-night package

### Jamulus-first startup

- Replaced the startup device wizard with one simple choice: **Host a Jam** or
  **Join a Jam**. The private server starts before the visible Jamulus client
  for a host; a guest starts one visible Jamulus client from one parsed invite.
- Jamulus now owns its interface, input/output channels, buffer, jitter, and
  musician mix. WebJam launches the supported filename-only profile
  `WebJam-native-v0.16.ini`, never writes its contents, and leaves the normal
  `Jamulus.ini` untouched.
- Connection proof requires an owned process, authenticated Jamulus RPC, the
  expected connection, and exactly one local musician. Audibility remains an
  explicit human confirmation; Webex is optional only after music is ready.
- Startup recovery persists only allowlisted phase/profile facts and fails
  closed when that profile truth no longer matches.

### Recording, Studio, and identity

- The first host recording choice is now clear: record the shared Jamulus take
  only, or explicitly open Recording Setup to keep this Mac's Local Originals.
  This does not alter Jamulus audio settings.
- Studio remains a Logic-like multitrack review surface—not a Logic
  integration—and offers playback-output selection only while reviewing a take.
- Replaced the old WJ monogram with WebJam's native three-loop trinity mark and
  standardized the interface on black, white, neutral gray, and burnt orange.

### Verification

- Built the final private Apple-Silicon package from
  `a36789978efbaac5e85fbc5c6ef55abae4ed42e3`:
  `WebJam-v0.16.0-TEST-NIGHT-macos-arm64.zip`, SHA-256
  `3ad2da6eccd99eb3965cc0e637ff147198e19446b3d878e4631a689cd5c9bf7b`.
- The final source gate reported **1,783 passed**, 18 environment-bound skips,
  and 6 subtests with zero failures/errors. The ad-hoc-signed,
  non-notarized archive passed fresh-extraction strict/deep outer and nested
  Jamulus/JamulusServer 3.12.2 signature checks, transport verification, and a
  frozen Host smoke. v0.15.0 is preserved as the rollback ZIP and app.
- Physical two-Mac audio, hardware change/recovery, recording, and external
  editor import remain **NOT RUN** until musicians perform those checks.

---

## [0.15.0] — 2026-07-14 private test-night candidate

### Release verification

- Built the exact Apple-Silicon package from
  `30ece85eb6a555dbcb2ef35753e4c6c9e8679770`:
  `WebJam-v0.15.0-TEST-NIGHT-macos-arm64.zip`, SHA-256
  `58ff7a6071d319a11119547028f454b579fd149912d17dfc0fc20ef3cef10152`.
  The v0.14.0 ZIP remains the rollback package.
- The ad-hoc-signed, non-notarized archive passed fresh-extraction strict/deep
  signature checks, nested Jamulus/JamulusServer 3.12.2 checks, arm64 native
  fabric checksum/build-ID verification, and two isolated six-second launch
  and ordinary-cleanup cycles. The isolated launch machine had no default band
  input, which was truthfully reported as a route-setup block rather than an
  audio pass.
- The full source gate passed **1,752 tests**, with 18 environment-bound skips,
  1 known warning, and 6 subtests. `transport` `make check`, `go test ./...`,
  `go vet ./...`, `go mod verify`, and `go mod tidy -diff` passed on the
  release Mac.
- Physical CoreAudio, two-Mac audio, recording/recovery, outage/reconnect, and
  import in an external editor remain **NOT RUN**. No source or package check
  is presented as evidence of those observations.

### Simpler session and Studio

- Added the pure Session Conductor: one fact-derived musician-facing phase and
  one dominant action across host readiness, joining, reconnecting, recording,
  take validation, Studio review, Track Export, and cleanup. It rejects stale
  callbacks and never promotes a process, meter, button press, or file into
  false connection, audibility, saved-media, or import proof.
- Rebuilt the session shell around a quiet meeting layout: original three-path
  WebJam mark, restrained header, focused status HUD, responsive band tiles,
  one bottom control bar, and progressive **More** controls. Runtime color is
  black, white/neutral gray, and Longhorn burnt orange only.
- Renamed the Studio handoff to **Track Export**. It keeps familiar multitrack
  review cues—transport, elapsed-seconds ruler, track headers, mute/solo,
  gain, pan, and inspector—without adding DAW editing or any Logic integration.
  It produces a portable atomic WAV package, source reports, and checksums.

### Closed pilot evidence

- Added explicit `--test-night` operator mode. Normal musicians never see it;
  the hidden dialog owns no persistence and merely asks the controller to
  record safe observations.
- Added a private, bounded, hash-linked local ledger and sanitized report.
  Automatic facts and explicit human observations are separate, and evidence
  cannot include audio, invitations, credentials, addresses, device IDs,
  paths, names, or free-form notes. Interrupted runs restore paused.

---

## [0.14.0] — 2026-07-14 private test-night candidate

### Candidate verification

- Built the exact Apple-Silicon package from
  `045c5acb01687a4088b0bd618dab4d0ab6200804`:
  `WebJam-v0.14.0-TEST-NIGHT-macos-arm64.zip`, SHA-256
  `cbcbdc038ac3d663e15870990ae5fea2a09819cdd55adbaa7463a64405ef8321`.
- The candidate is arm64 and bundles official Jamulus/JamulusServer 3.12.2.
  Fresh extraction passed strict/deep signature checks, nested-app inspection,
  exact native-fabric build-ID verification, and two isolated six-second
  offscreen launch/TERM cycles. It is ad-hoc signed, not notarized.
- The source gate recorded 1,719 passed, 18 skipped, one known warning, and
  6 subtests. Native transport `go test ./...` and `go vet ./...` passed.
- Physical CoreAudio, two-Mac audio, roster, reconnect, recording/recovery,
  and Logic Pro import remain **NOT RUN**. The v0.13.0 ZIP is now retained only
  as a rollback artifact; its record below is historical.

### Studio take review

- Reworked Studio into a focused take-review workspace: a shared elapsed-time
  timeline, track lanes, selected-track inspector, compact level meter, and
  non-destructive gain, pan, mute, solo, and Logic-export controls. It does not
  claim tempo, bars, beats, beat editing, or a completed DAW import.
- Added an atomic per-take Studio sidecar for schema-v2 projects. It stores
  local mix and export choices separately from WAVs and `webjam-take.json`,
  rejects mismatched or unsafe state, and reconciles tracks by durable ID.
- Logic export now applies saved mix/export choices by durable schema-v2 track
  ID, so reordering or selecting a subset of tracks cannot remap those choices
  by position. Legacy projects retain positional compatibility only where no
  durable ID exists.

---

## [0.13.0] — 2026-07-14 historical rollback candidate

### Candidate verification

- Built the exact Apple-Silicon package from
  `4d09810d7fb3c7f7355ca1d88e8218bb8ea784dd`:
  `WebJam-v0.13.0-TEST-NIGHT-macos-arm64.zip`, SHA-256
  `6b32a1d85cb64eb0bc97fecb7dadcd527159420a675358176cd75745d6565b3b`.
- The candidate is arm64 and bundles official Jamulus/JamulusServer 3.12.2.
  Fresh extraction passed strict/deep signature checks, nested-app inspection,
  exact sidecar build/hash/IPC validation, and two isolated six-second
  offscreen launch/TERM cycles. It is ad-hoc signed, not notarized.
- The final source gate recorded 1,706 passed, 18 skipped, one known
  Starlette/httpx warning, and 6 subtests. Physical CoreAudio, two-Mac audio,
  roster, reconnect, recording recovery, and Logic import remain **NOT RUN**.

### Durable recovery and truthful takes

- Local isolated capture now checkpoints about once per second: it flushes the
  writer, synchronizes the audio file, and records opaque take/session IDs,
  durable frame count, gaps, and capture facts. Parent-directory synchronization
  closes the atomic-publication durability gap on supported POSIX filesystems.
- Startup recovery safely promotes abandoned hidden captures to visible recovery
  folders without following symlinks or adopting a live writer. A recovered
  project is **Needs Attention**, never a completed take. Audio beyond the last
  confirmed durable frame is disclosed as an unverified crash gap and blocks a
  false-complete export.
- A recovered guest original is preserved on that guest Mac for review. It is
  not silently re-uploaded or represented as having reached the host.

#### Conservative Logic handoff

- Studio's Logic export now refuses a selected track explicitly marked silent,
  and refuses an unaligned guest/local original. The musician can intentionally
  deselect a track, or retain the aligned Jamulus server track, rather than
  producing an apparently complete but misleading package.
- Per-track Logic-export selection is local Studio state; it does not mutate the
  take manifest. The UI gives a short safe explanation instead of exposing a
  path, credential, or other internal failure detail.

#### One-use remote invitation truth

- A v3 remote invitation may be retried only when the sidecar fails before
  `open_guest` begins enrollment. Once enrollment was attempted, WebJam clears
  the invitation and requires a fresh one; it does not fall through to a legacy
  LAN launch or imply that a consumed credential remains usable. The v3 profile
  remains a loopback/CI laboratory boundary, not a deployed remote service.

---

## [0.12.0] — 2026-07-14 private test-night candidate

### Candidate verification

- Built the exact Apple-Silicon package from
  `796e9a4ddebe79f430b0ded8cf8034bc27836dd0`:
  `WebJam-v0.12.0-TEST-NIGHT-macos-arm64.zip`, SHA-256
  `01427316820b884d61546d40a9327a49cedf43d6a60a4d88b5b29ab4c693a24c`.
- The candidate is arm64 and bundles official Jamulus/JamulusServer 3.12.2.
  Fresh extraction passed strict/deep signature checks, nested-app inspection,
  exact sidecar build/hash/IPC validation, and two isolated six-second
  offscreen launch/TERM cycles. It is ad-hoc signed, not notarized.
- The final source gate recorded 1,687 passed, 18 skipped, one known
  Starlette/httpx warning, and 6 subtests. Physical CoreAudio, two-Mac audio,
  roster, reconnect, recording recovery, and Logic import remain **NOT RUN**.

### Last-mile session trust

- Added one privacy-safe authoritative session lifecycle record for Host/Join,
  Band Check, launch, roster-confirmed connection, recovery, recording
  finalization, and shutdown. The support bundle now includes only its
  allowlisted/redacted transition timeline.
- Added fail-closed private-LAN pre-share readiness. A legacy v1/v2 host does
  not enable **Copy Invite** until WebJam observes its authenticated local
  server, expected UDP listener, and a private LAN address. This is not a
  public-Internet, NAT, or remote-home reachability claim.
- Recorded the current v1 last-mile acceptance boundary and manual gates in
  `docs/WEBJAM_V1_LAST_MILE_PLAN.md`.

### Recording safety

- Added a fail-closed recording-storage guard. Band Check checks the selected
  folder before the session starts, and **Record** rechecks free space using the
  actual roster before opening local capture or arming the server recorder.
  An unsafe result starts no take and gives one recovery path; low storage is a
  warning to make room before a long rehearsal, not a guarantee of one. This
  behavior is included in v0.12.0; its physical drive-full result remains a
  separate **NOT RUN** gate.

### Recording evidence and recovery

- The v0.12.0 schema-v2 take manifests retain optional recording-session
  evidence: start/end timestamps only after recorder-server confirmation, host
  identity and protocol label, plus a bounded redacted lifecycle/recovery
  timeline. Invitations, network addresses, credentials, and raw device
  identifiers are excluded.
- While a take is live, v0.12.0 writes that evidence to a private,
  crash-safe checkpoint below the chosen Takes folder. An untrusted or
  unfinished checkpoint is recovery-needed truth, never a completed-take
  claim; it is removed only after final manifest publication. The final Logic
  export copies nonempty evidence into `webjam-logic-export.json`. Physical
  recovery and Logic-import results remain **NOT RUN**.

### Simpler musician setup

- Added a one-screen v0.12.0 confirmation after Host/Join: musician name plus
  Band input and Band output & review are saved before Band Check.
- Reworked in-session **Settings** into a short musician-first page: name,
  Band input, Band output & review, and a collapsed optional conversation link.
  On macOS, a complete pair persists as CoreAudio UIDs and is staged for the
  next Jamulus session.
- Removed Band Check's empty technical-details disclosure. Private diagnostics
  remain available only through the quieter **Save Support Bundle** action;
  **Audio Settings** is now the obvious correction path.

### macOS Jamulus route ownership

- Added read-only native CoreAudio discovery without PyObjC or a helper binary.
  WebJam resolves persistent UIDs, rejects duplicate Jamulus selector names,
  missing channels, and non-48-kHz devices before launch.
- Added a protected WebJam-owned `WebJam-route-v1.ini` in Jamulus's allowed
  container. The macOS client receives only the filename with that directory as
  its working directory; WebJam never overwrites a musician's `Jamulus.ini`.
- Route configuration is deliberately not audibility proof. A frozen route plan
  is revalidated on reconnect instead of silently switching defaults; a local
  PortAudio meter is skipped while Jamulus owns the live pair.

---

## [0.11.0] — 2026-07-13 private test-night candidate

### Remote-session foundation — local and CI evidence only

- Added a strict v3 invitation boundary with opaque, expiring, revocable,
  one-use enrollment material. Remote invitations use a compiled profile ID,
  never a caller-supplied endpoint, and secret-bearing values have constant
  string representations and are excluded from ordinary diagnostics.
- Added the statically compiled `webjam-fabric` process boundary, bounded
  JSON-lines IPC, loopback Jamulus proxy primitives, mutually authenticated
  QUIC session core, and deterministic direct/relay laboratory coverage. CI
  now builds the sidecar for macOS arm64, macOS x64, and Windows x64 and stages
  it beside the packaged desktop executable.
- Frozen builds ignore environment path/build-ID overrides and require the
  packaged sidecar, a canonical package-generated SHA-256 manifest, the
  expected architecture, safe owner/mode, its native platform signature, and
  the exact embedded build ID before accepting it. On macOS the manifest is
  sealed as data under `Contents/Resources`; placing text in `Contents/MacOS`
  would make strict code-signature verification treat it as unsigned code.
- Remote Jamulus host and guest launches omit the musician name from process
  arguments. The name is applied only after authenticated loopback JSON-RPC is
  available; legacy v1/v2 launch behavior remains unchanged.
- Added a dependency-free, containerizable reference service with bounded
  in-memory registration, one-use enrollment, opaque signaling, and an
  authenticated exact-pair UDP relay. The service is a native WebJam protocol,
  not an HTTP/WebSocket signaling server or a stock TURN server.
- The native reference integration now distinguishes host registration from
  peer connection and proves sealed bootstrap/acknowledgment, mutual TLS with
  exact pins, bidirectional exporter proofs, pre-proof quarantine, peer pumps,
  live payloads through the real relay/loopback-proxy seam, reset, and bounded
  close against an independently spawned service process. Its endpoints are
  controlled UDP sockets, not real Jamulus processes or physical musicians.
- Band Check can retain explicit local, transport, remote-signal, decoded-
  fixture, and musician-confirmed evidence without treating a socket, packet,
  process, meter, or fixture as proof that a person heard the live route.
- Added deterministic impairment coverage for latency, jitter, loss, reorder,
  duplication, bandwidth limits, blackholes, path changes, relay failure,
  restart, and cleanup, while keeping physical hardware and public-network
  results separate.

### Release boundary

- No public rendezvous or relay is deployed or bundled. `reference-local` is a
  loopback-only lab profile; it is not an “anywhere” service and cannot be
  redirected through desktop input or IPC.
- The existing v1/v2 same-private-LAN path remains the ordinary musician flow.
  Public Internet deployment, two independent homes/NATs, two-musician
  acoustic audibility, physical interface routing, Logic Pro import, and
  packaged VoiceOver/NVDA review remain **NOT RUN**.
- The private Apple Silicon candidate is
  `WebJam-v0.11.0-TEST-NIGHT-macos-arm64.zip`, SHA-256
  `11bc573a28c9804163d34deb5fbf3779dd6aaa2338f3a25e6e70819776b41e4f`,
  built from `1a03927e3ea8eb76557617aa59e985a551c35e0b`. Fresh-extraction,
  installed-copy, strict/deep signature, sidecar integrity/IPC, and two
  no-input normal-close cleanup cycles pass. This Mac has no CoreAudio input,
  so packaged live-client/roster audio remains **NOT RUN**. The candidate is
  ad-hoc signed, not Developer ID signed or notarized.

---

## [0.10.0] — 2026-07-13 certification candidate

### Band Check before guesswork

- **Band Check is now the permanent readiness path.** It guides each musician
  through the music engine, owned host service, selected local input, headphone
  left/right output, a five-second PCM24 recording, explicit playback
  confirmation, Studio transport, and a plain-language result: **Ready to
  Jam**, **Ready with a Warning**, or **Action Needed**.
- Live Band Check never opens a second device or restarts a running music
  service. Its copy now distinguishes WebJam's separate PortAudio input from
  Jamulus observations, so a moving local meter is not presented as proof of
  what a bandmate hears.
- **Save Support Bundle** previews the same immutable allowlisted artifact that
  is saved as a ZIP. The separate diagnostics shortcut creates its own short,
  sanitized clipboard summary. The private archive excludes audio, notes,
  transcripts, Webex content, meeting/invite links, settings/environment dumps,
  secrets, home paths, and arbitrary personal files by default; bounded log
  excerpts are recursively redacted.

### Originals survive reconnects

- A schema-v2 take now uses durable session, take, participant, track, source,
  and segment IDs. Explicit project placement, device/rate/channel/format
  facts, SHA-256, media status, reconnect segments, and gap intervals replace
  filename/name inference.
- The host, and a guest connected through an active v2 private invite, can
  explicitly keep interface inputs 1 and 2 as separate local PCM24/48-kHz
  originals. A v1 guest still joins/plays and receives a server track, but has
  no WebJam-orchestrated local capture or delivery. Queue or write loss
  preserves absolute frame time by inserting disclosed silence instead of
  shortening the recording. Writer
  timeout, attach failure, crash, and shutdown preserve visible recoverable
  media and never steal a still-live writer's file handles.
- Each installation that uses a v2 private invite receives a stable
  session-participant identity. The invite is a reusable session-scoped bearer
  credential, not a one-use or one-guest token; anyone who has it on the trusted
  LAN can enroll until the host peer service restarts. Guest capture begins only
  after authenticated host recording state, continues while the peer control
  plane is unavailable, and uploads immutable segments in restartable chunks.
  Size, SHA-256, and PCM facts must agree before the host atomically attaches a
  copy; the guest original is never moved or deleted.
- End Session is blocked while a host take is recording or validating; the host
  presses **Stop Rec** and waits for **Take saved** first. **Leave Jam** finalizes
  active opted-in guest capture, persists the resumable queue, and attempts one
  final upload before disconnecting.
- The peer plane is intentionally limited to authenticated plain HTTP on the
  same RFC1918 IPv4 LAN. It does not claim TLS, IPv6, Internet, VPN, NAT
  traversal, or safe public exposure. Invite links now contain a private
  enrollment credential and should be shared only with the intended bandmate.

### Studio and Logic evidence

- Studio retains missing, partial, damaged, transferring, and failed-transfer
  truth. Playback and exact asynchronous waveforms support multi-segment,
  mixed-rate, reconnect-gap, and drift-adjusted projects; active seek reopens
  every reader and leaving Studio releases its output.
- Non-destructive alignment now measures repeated transients, signed start
  offset, long-take drift, mixed rates, gaps, residuals, and confidence. Manual
  nudge remains separate and can be restored to the automatic evidence.
- The Logic handoff now publishes common-origin numbered PCM24 stems, a server
  reference, Studio reference, marker/tempo/signature guidance, source
  manifest, alignment and recording reports, independent WAV analysis, and
  checksums. Missing or changed selected media blocks publication. The
  deterministic affine resampler is disclosed and is not claimed to be
  sample-perfect or mastering grade.

### Identity and certification boundary

- The placeholder **WJ** header has been replaced by an original three-part
  WebJam mark representing conversation, live music, and production. SVG, ICO,
  and ICNS assets use only black/white/neutral and Longhorn burnt orange; no
  purple or teal is part of the identity.
- A real Jamulus 3.12.2/JACK harness now measures two independently named
  clients at their hardware-boundary ports, checks cross-contamination,
  dropouts, server stems, Studio/export traversal, reconnect, resources, and
  owned-process cleanup. A separate longevity test refuses to count runs below
  3,600 seconds.
- Private peer-server startup now binds directly to the selected numeric LAN
  address instead of blocking the Qt thread on reverse DNS. A frozen-package
  regression and full Host lifecycle prove client/server/RPC startup, normal
  close, process cleanup, and port release.
- Automated evidence does not replace the final two-Mac musician and Logic Pro
  gates. At this changelog entry, bidirectional acoustic audibility and Logic
  import remain **NOT RUN**. The fresh private Apple Silicon ZIP is
  `WebJam-v0.10.0-TEST-NIGHT-macos-arm64.zip`, SHA-256
  `f955419909dc014b7172032b00524417983c09e8586c2217691c19838a0b3411`,
  built from `8ee89081802fe5998f71299c4755b21ae5218cb9`. Its fresh-extracted
  Host lifecycle passes twice. GitHub Actions run `29269188463` passed the
  exact-source 3,600-second native Jamulus/JACK certification with reconnect,
  recording cycles, bounded resources/xruns, and zero cleanup errors.

---

## [0.9.0] — 2026-07-13 test-night candidate

### A simpler first five seconds

- **Open WebJam. Choose Host or Join. Start playing.** The launch window is now
  one calm, responsive decision instead of a configuration surface. **Host a
  Jam** is the unmistakable primary action; **Join a Jam** opens one paste-ready
  invitation field with one Join action. Duplicate clicks are ignored while an
  operation is being submitted.
- An original, lightweight shared-signal graphic gives the launch screen a
  recognizable WebJam identity without delaying access, faking progress, or
  introducing motion that must be disabled.
- The normal path still starts the bundled server and music client
  automatically. Ports, process paths, recorder credentials, and routing
  internals remain outside the musician experience.

### Black, white, and burnt orange

- The entire Qt interface now uses a restrained near-black and white system
  with burnt orange (`#BF5700`) reserved for primary actions, focus, and
  meaningful emphasis. Purple, teal, neon glow, busy gradients, and the old
  color-coded control clutter are gone.
- Reusable tokens now govern surfaces, text hierarchy, borders, focus,
  semantic states, meters, buttons, inputs, dialogs, menus, tooltips, empty
  states, and recording surfaces. State meaning is always expressed in words
  or control labels, never by color alone.
- The live window adopts a familiar meeting hierarchy without copying Webex
  assets: a restrained header, a dominant musician stage, responsive tiles,
  one status surface, and one bottom control bar.

### Truthful live-session controls and recovery

- The bottom bar keeps only **Copy Invite**, **Record**, **More**, and the
  role-aware session action. A host sees **End Session** because it ends the jam
  for everyone; a bandmate sees **Leave Jam** because it disconnects only that
  Mac. **Ending…** and **Leaving…** remain visible until owned-process cleanup
  actually finishes.
- Connection recovery no longer treats a running process as proof of a live
  session. An interruption clears stale participant/audio truth, announces the
  recovery state, and returns to connected only after real local session
  evidence. A timed-out attempt presents one recovery action instead of
  competing Retry buttons.
- Invalid invitations, unavailable sessions, offline networks, microphone
  permission requirements and denials, recoverable failures, and fatal startup
  failures use plain-language states with a next action. Technical detail stays
  in logs or **More → Troubleshooting**.
- Ending a hosted jam and leaving a joined jam have distinct confirmations.
  Active recording is stopped and saved first; cleanup failure is reported
  instead of being replaced by a false success state.

### Responsive and accessible by construction

- The main session remains usable at 760×600. Participant tiles reflow from a
  focus tile to balanced multi-column layouts based on the actual viewport,
  and the four essential bottom controls remain visible in a narrow window.
- Keyboard order follows the task: title, participant mix controls, Copy
  Invite, Record, More, then End/Leave. Focus is visibly distinct, interactive
  targets are larger, controls have accessible names and descriptions, and
  changing connection/participant states are announced to assistive
  technology.
- Local mute is now described as **Mute Monitor** so it cannot be mistaken for
  muting the musician's outgoing audio. Permission and validation recovery do
  not rely on color.

### Multitrack Studio and Logic handoff

- **Recording is a musician-facing Studio, not a toolbar switch.** More →
  Multitrack Studio shows one lane per participant, a single Record action,
  live recording state, a take library, waveforms, transport/scrub, selectable
  stereo output, gain, pan, mute, and solo. Recording starts without pulling
  the host away from the simple live room.
- A hosted take keeps the server's isolated WAV for each musician and maps
  channel filenames to participant names plus the session title in the take
  manifest.
- **Export for Logic is aligned, atomic, and non-destructive.** It creates one
  numbered, musician-named 24-bit PCM WAV per track, padding or trimming every
  signed source offset onto a shared zero-based timeline. All stems have the
  same length, so they can be dragged into Logic together at `0:00` without
  manual offset math. A stereo rough mix reflects the current gain/pan/mute/
  solo state, while instructions and `webjam-logic-export.json` preserve the
  handoff evidence. Original recorder files are never modified and repeated
  exports never overwrite an earlier package. Unverified audio cannot be
  presented or exported as Logic-ready.
- **Recording Setup lives in Studio.** The first-run Host/Join experience stays
  focused, while the host can choose Studio's wired playback output and
  optionally capture interface inputs 1 and 2 as separate 24-bit/48 kHz stems.
  Explicit capture settings persist across host launches. Joining musicians
  cannot arm host-only local capture.
- Recording has explicit starting, recording, stopping, validating, complete,
  and needs-attention states. Partial recordings are preserved on attach or
  shutdown failure instead of being silently deleted.

### Test-night boundary

- v0.9.0 is a new private test artifact and must not overwrite or be confused
  with the earlier v0.8.2 ZIP. The exact packaged app still must pass the
  source, frozen-runtime, two-Mac audio, reconnect, multitrack, and cleanup
  gates in [`TEST_PROCEDURE.md`](TEST_PROCEDURE.md) and
  [`SUNDAY_TWO_MAC_PILOT.md`](SUNDAY_TWO_MAC_PILOT.md).
- The same-LAN invitation boundary remains intentional for tonight. Internet,
  VPN, NAT traversal, Windows, and Intel macOS are not part of the v0.9.0
  private-pilot claim.

---

## [0.8.2] — 2026-07-12 test-night candidate

### Host → Share → Join → Play

- **Every launch starts with two choices: Host a Jam or Join a Jam.** There is
  no setup wizard, Ready Check, server-address form, port picker, device-path
  field, or routing decision in the normal path.
- **Hosting is one click on the macOS test build.** WebJam selects safe
  defaults, starts its bundled dedicated server and background music client,
  and publishes the invitation only after the hosted service is actually
  alive.
- **Invitations are links, not network configuration.** Copy Invite produces a
  versioned `webjam://join?...` link containing only the host, port, and session
  name. A bandmate can open that link or paste it into the single Join field.
  Cold-start and already-running deep links use the same strict parser; malformed
  or ambiguous links, unsafe addresses, credentials, fragments, and unexpected
  parameters are rejected.
- **The session HUD says what WebJam knows.** It distinguishes starting,
  ready-to-share, connected, timed-out, and ended states. A local input meter
  means WebJam observed signal on this Mac; a remote meter means band audio was
  observed. Neither meter is presented as proof that the other musician heard
  the signal. A 30-second join timeout ends the unproductive attempt and offers
  one clear Try Again action.
- **End Session owns cleanup.** The host path stops and saves an active take,
  then stops the local client and the server WebJam started. A joined Mac stops
  its client. Shutdown follows the same ownership-aware order.

### Progressive session workspace

- The connected workspace keeps the invitation, readiness, participant cards,
  and **End Session** visible. Notes, Studio, optional video/conversation,
  Talk Break, Settings, and Troubleshooting live under one **More**
  menu instead of competing with the core path.
- Settings is now a small preferences dialog for the musician name and optional
  conversation link. It does not duplicate host/join or expose internal ports,
  secrets, executable paths, or recording folders.
- Webex/video is optional. When used, WebJam only launches the external
  conversation and reports that action; it does not claim meeting membership
  or control native Webex devices.

### Familiar meeting stage and packaged-runtime reliability

- The live session now follows the familiar Webex meeting hierarchy: a light
  header, dominant neutral stage, large automatically centered musician tiles,
  one compact readiness line, and a persistent bottom bar for Copy Invite,
  Record, More, and the red End Session action. One musician gets a large
  focus tile; two to six musicians form a balanced equal-view grid.
- Raw network links no longer occupy the live stage, and legacy hosted-server
  text can no longer become a stray top-level macOS window. Inter 4.1 remains
  bundled under the SIL Open Font License, with a platform-font fallback.
- The packaged app keeps private control secrets and multitrack takes in
  writable Application Support storage. Its official Jamulus client now runs
  headlessly, so musicians do not have to operate or dismiss a second audio
  application window.
- The bundled server and client were exercised together on isolated test ports:
  the server reported a real connected client, the client accepted a musician
  name over authenticated control, both stopped cleanly, and the test ports
  were released. Physical two-Mac audio, reconnect, and recording remain the
  release-candidate acceptance gate described in
  [`SUNDAY_TWO_MAC_PILOT.md`](SUNDAY_TWO_MAC_PILOT.md).
- The legacy UDP monitor is dormant in the product build. Enabling it made the
  Jamulus server count WebJam's monitor socket as another musician; the bundled
  3.12.2 client's authenticated interface already provides the authoritative
  roster, levels, mixer controls, chat, and mute state without that phantom
  connection.
- Local identity now follows the real Jamulus 3.12.2 control response. That
  response describes the local profile without returning a channel id, so
  WebJam reconciles it with the roster instead of mislabeling the host as a
  bandmate and timing out a healthy session. A remote-only roster can no
  longer produce a misleading “Bandmate connected” banner while this Mac is
  still reconnecting.
- The private macOS test artifact is ad-hoc signed and intentionally not
  notarized. The first launch may require Control-click → Open. The pilot is
  limited to two Macs on the same local network; it does not claim internet or
  NAT traversal.

### Recording integrity hardening

- Stem alignment offsets are now signed end-to-end. Local capture arms before
  the server recorder starts, so isolated stems normally *lead* the server
  take; the previous clamp forced those negative offsets to zero and every
  supplemental stem played late by the recorder-start latency. Take Deck now
  plays negative-offset stems sample-aligned and labels the trimmed lead-in.
- Alignment correlation uses an alias-free 100 Hz block-mean envelope plus a
  bounded full-rate refinement pass, replacing raw stride decimation. The
  reported confidence is the refined normalized correlation (≈1.0 for a
  genuine match, ≈0 for unrelated audio), making the 0.15 acceptance floor
  meaningful. Manifests record `alignment_method: envelope+refine-v2`.
- The supplemental-capture audio callback is real-time safe: it only copies
  blocks into a bounded queue and a dedicated writer thread does all disk
  writes. Device status flags and write errors are deduplicated, counted, and
  capped, so a sustained fault can no longer grow an unbounded error list into
  the manifest.
- Partial recordings are always preserved: a failed stem attach moves the
  audio to a visible `Recovered-local-…` folder instead of deleting it,
  attaching never overwrites an existing take file (collisions get a
  `-local` suffix), and quitting mid-recording salvages the capture into a
  `Recovered-…` take instead of discarding it. Capture hand-off between the
  validation worker, stop-failure handling, and shutdown is now atomic and
  idempotent.
- Take Deck reuses recorded manifest findings when reviewing a finished take
  instead of re-probing every WAV, and shows transient `validating` manifests
  as "Checking…" rather than "Unchecked".
- Ending a hosted session while recording now stops and saves the take before
  the client and owned server are shut down.
- Participant names and roles from the Jamulus roster render as plain text,
  so markup in a remote musician's name can no longer be interpreted as rich
  text in the mixer.
- Reconnect guidance now stays in the session HUD and offers one clear
  **Try Again** action after a timed-out attempt.

---

> Entries below v0.8.2 preserve earlier implementation history. References to
> Setup, Ready Check, raw endpoints, Start/Stop Audio, or a visible Jamulus
> window are not instructions for the current build.

## [0.8.0] — 2026-07-08

### Bundle Jamulus with downloadable builds (both platforms)

Removes the "leave WebJam, find jamulus.io, download, install, come back"
detour for most users. Both platforms bundle the same pinned Jamulus
version (`3.12.2` / tag `r3_12_2`) already used by the `integration-jamulus`
CI job, under GPL/AGPL "mere aggregation" terms — see the new
`THIRD_PARTY_NOTICES.md` for the full licensing rationale. Current macOS
packaging prepares and re-signs its nested copies ad hoc; it does not preserve a
notarized nested-app signature.

> Packaging note: 0.8.1 supersedes the original macOS signature-preservation
> approach below. The current test build prepares the same upstream app
> contents with ad-hoc, non-sandboxed signatures as documented above and in
> `THIRD_PARTY_NOTICES.md`.

- **macOS: zero-install.** The original 0.8.0 plan downloaded and
  checksum-verified the official Apple-signed/notarized
  `jamulus_3.12.2_mac.dmg`. Current private-candidate packaging extracts that
  release, prepares the nested client/server copies for WebJam's loopback-only
  orchestration, and re-signs them ad hoc. The nested copies and WebJam artifact
  are therefore not notarized. A fresh install still finds the pinned bundled
  client automatically with zero configuration.
- **Windows: bundled installer.** Jamulus only ships an NSIS installer on
  Windows (no portable binary), so CI downloads and checksum-verifies
  `jamulus_3.12.2_win.exe` and `webjam.spec`'s new `Jamulus/` datas block
  (mirroring the existing `VB/` block) ships it inside the WebJam install
  directory. The Setup Wizard's Jamulus page now shows an **"Install
  Jamulus now"** button when no install is found — it launches the bundled
  installer and polls (non-blocking, via `QTimer`) for completion, filling
  in the executable path automatically once it lands.
- Added `services.bridge_service._bundled_jamulus_candidate()` (macOS) and
  `_bundled_jamulus_installer()` (Windows) — both frozen-build-aware and
  no-ops in dev checkouts. `find_jamulus()` now falls back to the bundled
  macOS candidate as a last resort after all configured/default candidates
  are exhausted.
- The manual override (Browse button, `WEBJAM_JAMULUS_CANDIDATES` env var)
  is unchanged and remains the escape hatch for anyone who needs a
  different Jamulus install than the bundled one.
- Added `licenses/JAMULUS_COPYING.txt` (the exact GPL text from the pinned
  Jamulus release tag) and `THIRD_PARTY_NOTICES.md`; CI places a copy
  alongside the bundled Jamulus in every build (macOS:
  `WebJam.app/Contents/Resources/THIRD_PARTY_LICENSES/`; Windows:
  `Jamulus/` next to the installer).
- Updated the Setup Wizard's Welcome-page notice (no longer an "install
  this yourself" warning) and the Jamulus page (pre-fills + notes the
  bundled macOS copy; shows the install button on Windows).
- Updated README, README_SIMPLE, DEVELOPMENT, ARCHITECTURE, USER_GUIDE,
  FIRST_JAM, COHORT_VALIDATION_PLAYBOOK, TEST_PROCEDURE, and
  VISION_AND_ROADMAP to reflect per-platform bundling instead of a blanket
  "install Jamulus separately" requirement (still true for source
  checkouts, which don't go through the PyInstaller bundling step).
- Added `TestBundledJamulusCandidate`, `TestBundledJamulusInstaller`, and
  `TestJamulusPageBundling` test suites (28 + new wizard cases) covering
  frozen/non-frozen and platform-gating branches, the `find_jamulus()`
  fallback, and the install-button launch/poll/failure paths. Full suite:
  823 tests passing (0 regressions).
- Known trade-off, not blocking: bundling ties the shipped Jamulus version
  to WebJam's own release cadence — see `THIRD_PARTY_NOTICES.md`'s
  "Staying current" note.

---

## [0.7.3] — 2026-07-08

### Test isolation fix and doc cleanup

- Fixed a test-isolation bug in
  `tests/test_application_controller_demo_to_real_transition.py`: the
  audio "stopping" latch set by `AudioCoordinator.stop()` wasn't reset in
  `setUp()`, so a prior test's stop() could leak into the next test and
  make `apply_participants()` silently no-op.
- Fixed `DEVELOPMENT.md`'s "Adding a Jamulus JSON-RPC method call"
  tutorial, which still described the pre-rewrite RPC client (separate
  poll/SSE threads, a synchronous `_call()` helper) and referenced a
  nonexistent `GAIN_RANGE_MAX` attribute in its example code. Rewritten
  to match the current single-thread NDJSON reader and fire-and-forget
  `_send()`.

---

## [0.7.2] — 2026-07-06

### Pilot readiness hardening

- Added a session-health snapshot so the Conductor distinguishes a launched
  Jamulus process from proven RPC/participant/meter truth.
- Made Ready Check visible in the session strip and run it automatically after
  first-run setup completes.
- Hardened first-run setup: Jamulus executable presence is required, Webex
  links must be HTTPS `webex.com`, and setup completion copy no longer implies
  the rig is jam-ready before Ready Check passes.
- Made `Mute Me` truthful: it only changes local UI state after Jamulus RPC
  accepts `setMuted`, and reverts on failure.
- Tightened recorder status parsing, Webex permissions/token injection, log and
  diagnostics redaction, Companion API opt-in behavior, and Jamulus RPC secret
  fail-closed launch.
- CI desktop builds now wait for the real-Jamulus integration job.
- Restored an Intel Mac release artifact (`WebJam-macos-x64.zip`) using
  GitHub's current `macos-15-intel` hosted runner.

---

## [0.7.1] — 2026-07-05

### Deep code + logic review — hardening pass

A four-reviewer deep audit of the audio engine, RPC layer, and controller
state machines. Confirmed the security model is sound (0o600 secrets, no
command injection, loopback-only RPC + SSH tunnel, Host-header guard). Fixes:

- **Take Deck plays at the take's real samplerate.** A 44.1 kHz take no
  longer plays pitch-shifted / misaligned through a fixed 48 kHz device.
  Replaying a finished take rewinds instead of sitting silent, and finishing
  a take now releases the audio stream + file handles.
- **RPC framing is stall-proof.** Both the Record-button transport and the
  live client now frame NDJSON from raw sockets, so a response split across a
  network stall no longer hard-fails a call or drops notifications.
- **No zombie RPC reader** after a fast Stop Audio → Launch Audio; sends are
  serialised; channel meters map by channel id, not list position.
- **Record button polls** the server recorder until it actually arms/disarms
  (Jamulus does it asynchronously), and resets on Stop Audio.
- **Reconnect** shows a clear "couldn't reconnect after 5 tries" instead of
  hanging on "Reconnecting…" forever.
- **Practice mode** cleans up its private server if the client launch fails,
  and never freezes the UI during teardown.
- Webex button can't get stuck lying "Leave Video"; shutdown is re-entrant;
  companion-API reads are race-safe; diagnostics redaction is future-proofed.
- 12 regression tests added; suite at 754.

---

## [0.7.0] — 2026-07-05

### The Take Deck — play back and mix your jams, in-app

- **Take Deck (side-rail "Takes")** — the recordings the ● Record button
  captures are now reviewable *inside WebJam*: pick a take, hit play, and
  mix it with the very same console the live session uses (per-track
  faders, mute, solo, live meters, scrub). Musicians who connect mid-jam
  line up correctly — track start offsets are read from the take's
  Audacity `.lof`. This is the first half of the "Demo Deck": review now,
  overdub next.
- **Multitrack playback engine** (`core/take_player.py`) — streaming
  per-track mixing on a numpy bus with gain/mute/solo/offsets and a
  transport, behind a sink abstraction so the whole engine is unit-tested
  headless (no audio hardware in CI).
- **Take library** (`core/take_library.py`) — discovers take folders and
  parses `.lof` offsets; robust to missing/garbled metadata.
- **Review-only, on purpose** — no editing/plugins here; every take keeps
  its Reaper-project escape hatch for the DAW.
- New dependency: `soundfile`. New setting: `takes_directory`. Suite +34.

---

## [0.6.0] — 2026-07-05

### The Record Button

- **● Record in the Conductor** — one press arms the band server's
  multitrack recorder; one press stops it. Every musician gets their own
  track and every take lands as a ready-to-open Reaper project on the
  server. The whole band sees the red ● REC chip while tape rolls.
- **Band-server RPC transport** (`core/jamulus_server_rpc.py`) — reaches
  the server's loopback-only JSON-RPC through an SSH tunnel; new settings
  `server_rpc_port` (default 22240) and `server_rpc_secret_file` (a local
  copy of the server's jsonrpc.secret). Unconfigured? The button tells you
  exactly how to set it up.
- **Machine-verified against real Jamulus** — the Record cycle (arm →
  new-take → stop), roster query, and wrong-secret rejection all run
  against the shipping jamulus-headless binary in CI on every push.
- Suite at 719 (+16 unit, +3 real-binary integration).

---

## [0.5.0] — 2026-07-04

### The "make it amazing" release — practice mode, recording awareness, band server

- **Practice mode (Ctrl+P / Practice button)** — WebJam starts a private
  Jamulus server on your own machine and connects to it: hear yourself,
  watch your meter, test the mixer — zero internet, zero band-server
  dependency. Works on a fresh unconfigured install. Stop Audio tears the
  local server down with the client.
- **● REC indicator** — when the band server's multitrack recorder is
  rolling, every member sees a red ● REC chip in the status bar (wired to
  Jamulus `recorderState` notifications).
- **Stage cards v2** — cards now show each musician's skill level from
  their Jamulus profile alongside the instrument ("Bass · Intermediate").
- **Band server recipe (`server/`)** — one `docker compose up -d` gives the
  band a private server with multitrack recording armed: every take is one
  WAV per musician plus a ready-to-open Reaper project. JSON-RPC stays on
  loopback (SSH-tunnel only) — the foundation for the upcoming Record
  button.
- **Vision** — see VISION_AND_ROADMAP.md for the roadmap this release starts
  (Session Record concept, server browser, Webex intelligence).

- **Fresh installs start unconfigured** — the dead default Jamulus server
  (a private LAN IP) and sandbox Webex link are gone. The wizard requires
  real values; Launch Audio without a server now shows an actionable error
  instead of spawning `Jamulus --connect :22124`; the empty default no
  longer crashes the app at startup.
- **FIRST_JAM.md** — staged runbook for the band's first session (solo
  smoke test → two-person → full band) with a failure playbook.
- **Download & security-warning docs** — README_SIMPLE now covers grabbing
  release zips and getting past Gatekeeper/SmartScreen (builds are unsigned).
- **Legacy Tkinter app quarantined** — `webjam_app*.py`, the Tkinter `ui/`
  modules, `admin/`, `session_templates`, old installer scripts, and their
  tests moved to `legacy/` (see `legacy/README.md`). CI no longer needs
  tkinter to collect the active suite; `ui/services.py` (live MetricsService)
  stays. Active suite: 674 tests, zero collection errors.

---

## [0.4.10] — 2026-07-04

### First shippable v0.4.x build — release pipeline unblocked

- **CI: release pipeline fixed** — every tag run since v0.4.5 was killed at
  the 24h wall because the build matrix still listed `macos-13` (Intel), a
  runner type GitHub has retired; the release job never fired. The Intel
  entry is removed (Intel Macs: run from source) and jobs now carry real
  timeouts. This is the first v0.4.x tag whose build can actually publish.
- **Fix: routing-scan shutdown race** — the background audio-routing scan no
  longer dies with a `RuntimeError` traceback if the app shuts down while the
  scan is in flight (the status is quietly dropped instead).
- **Tests** — live-session engine coverage push: `application_controller`
  69%→86%, `jamulus_controller` 63%→88%, `bridge_service` →91%. New suites for
  the Join/Leave Video flow, Webex state machine, token refresh, Launch/Stop
  Audio toggle, crash-reconnect banner, settings wizard round-trip,
  diagnostics export, JamulusController lifecycle, and BridgeService launch
  failure paths + Jamulus command-line contract. Suite at 720.

---

## [0.4.9] — 2026-06-29

### Live-session features + build correctness

- **In-session chat both ways** — a chat box in the session canvas sends to the
  band (`jamulusclient/sendChatText`) and echoes locally; incoming chat appends
  to the shared canvas.
- **Name sync** — on connect, WebJam pushes your display name to Jamulus
  (`jamulusclient/setName`) so bandmates see a real name, not a blank.
- **Ready Check (F2)** — `core/preflight.py` reports what's missing before you
  jam (Jamulus installed, server/port set, virtual audio cable detected, Webex
  link), surfaced via an F2 shortcut + F1 help.
- **Build correctness** — macOS bundle version now tracks `__version__` (was
  pinned to 0.3.0); Windows builds bundle the VB-CABLE installers; added
  `api.local_bridge` / `core.file_io` to PyInstaller hiddenimports.
- **Tests** — suite at 620 (fake-Jamulus TCP server, preflight, chat send,
  build data-file guards). `__version__` → 0.4.9.

---

## [0.4.8] — 2026-06-29

### Real-world hardening, correct Jamulus control, and onboarding

The headline: WebJam's Jamulus control was rebuilt against the **actual** current
Jamulus JSON-RPC API, plus a multi-round audit fixed real bugs and the CI/release
pipeline. First release intended for live band use.

#### Jamulus integration (correctness)
- **Rebuilt the JSON-RPC client against shipping Jamulus (3.9–3.12).** The old
  client spoke an experimental HTTP+SSE fork (`jamulus/getChannelClients`,
  gain 0–10000) that never matched released Jamulus. It now uses
  newline-delimited JSON-RPC over **TCP**, the `jamulus/apiAuth` handshake
  (`--jsonrpcsecretfile`, generated at launch), and the real `jamulusclient/*`
  methods (`getClientList`, `setFaderLevel` 0–100, `setMuted`) and notifications
  (`clientListReceived`, `channelLevelListReceived` 0–9, `connected`/`disconnected`).
- **Real "Mute Me"** via `jamulusclient/setMuted` — previously it zeroed your own
  fader, which only muted you in your *own* monitor; the band still heard you.
- **In-session chat** — incoming Jamulus chat (`chatTextReceived`) is appended to
  the shared session canvas; `sendChatText` is wired.

#### Reliability / security fixes (from the audit rounds)
- RPC heartbeat no longer false-fires "Jamulus stopped responding" after a restart.
- Mix auto-save safety net no longer disarmed by a failed save.
- Background audio-routing scan no longer dies silently when PortAudio is missing.
- Companion API: added a loopback-only `Host`-header check (DNS-rebinding defense),
  redacted `sentry_dsn`, and **actually wired it into the app** (it was documented
  as auto-starting but never instantiated).
- Python 3.10 compatibility fix; unknown-msg-id log-flood cap; assorted Lows.

#### Pipeline / docs
- **CI no longer cancels branch/tag runs**, so `master` can go green and produce builds.
- **`README_SIMPLE.md` rewritten** as an accurate band onboarding guide for the Qt app.
- **`WEBJAM_NEXT_LEVEL.md`** added: engine evaluation (stay on Jamulus; SonoBus/JackTrip considered) + roadmap.

#### Tests
- Suite expanded to **600+** (incl. a fake-Jamulus TCP server verifying the real
  wire protocol). `__version__` → 0.4.8.

---

## [0.4.7] — 2026-04-24

### Round 4 deep-dive — controller refactor, telemetry expansion, multi-mix, audio device picker

6 parallel implementation agents in isolated worktrees, plus follow-up wiring and a user-journey audit.

#### Refactor
- **`ParticipantStateManager` extracted** from `JamulusController` (new `jamulus_state_manager.py`, 349 LOC).  Owns `participants`, `_pre_solo_mute`, and `_participants_lock` plus all mutator helpers (`set_fader_level`, `set_mute`, `set_solo`, `serialize_mix`, `apply_mix_data`, `sync_from_protocol`).  `JamulusController` shrinks 803 → 545 LOC and now delegates; backward-compat properties on the controller keep older test fixtures working.
- **`unregister_callback()`** added to `JamulusController`; `stop()` warns if monitor thread didn't exit, then clears the callbacks list to drop dangling references.

#### New features
- **Multi-mix save/load** — `Ctrl+Shift+S` ("Save Mix As…") and `Ctrl+Shift+O` ("Load Mix From…") open `QFileDialog`s so users can keep one mix per song / per band-mate.  New `MixManager.save_to(path)` / `load_from(path)` paired methods.
- **Audio input device picker** in the wizard's Routing page (`AppSettings.audio_input_device_index`).  `core/audio_engine.py::_resolve_device` now prefers an explicit setting over auto-detect, so users with multiple interfaces can pin the right one.

#### Telemetry expansion (7 new metrics)
- `metric_jamulus_hang_detected` — incremented when the RPC heartbeat first crosses the >15s silence threshold.
- `metric_webex_token_refresh_attempt` / `_success` — wired through `WebexEmbed.on_refresh_metric` callback.
- `metric_audio_device_blackhole_found` / `_audio_device_missing` — emitted from the routing-status apply path so we know how often the bundled BlackHole route succeeds.
- `metric_mix_corruption_recovered` — incremented on `JSONDecodeError` in `MixManager.load`.
- `metric_session_started` — first-time-this-session participant arrival, paired with a "Connected to {server}. Waiting for band members…" flash.

#### Memory + concurrency hardening
- **`_unknown_msg_ids_seen` capped** at 256 entries in `core/jamulus_protocol.py` so unknown-message logging can't grow without bound on a misconfigured server.
- **`_request_counter` reset** in `JamulusRpcClient.stop()` — prevents wraparound state leaking across reconnects.
- **47 new tests** across 7 files covering the state-manager extraction, multi-mix round-trip, telemetry expansion, audio device picker validation, and concurrency stress (RPC client + JamulusController under daemon-thread Barrier/Event harness).

#### User-journey polish
- **Jamulus install warning relocated** from the Done page (page 4) to the Welcome page (page 1) of the setup wizard, with an amber notice box — users now discover the prerequisite before configuring anything.

#### Versioning
- `__version__` 0.4.6 → 0.4.7.  Suite total: **647 pass, 12 skipped** (was 611; +36 net; 0 failures).

---

## [0.4.6] — 2026-04-25

### Round 3 deep-dive — refactors, new shortcuts, audit fixes

10 parallel agents (6 implementation, 4 investigative) plus follow-up fixes.

#### New features
- **Ctrl+Shift+R — Reset all faders to 0 dB** (`application_controller.py::_on_reset_all_faders`).  Confirmation dialog; saved mix on disk untouched (Ctrl+O still restores).
- **Ctrl+Shift+D — Copy diagnostics summary** (`webjam_qt/controllers/diagnostics.py`).  New 129-LOC `DiagnosticsExporter` builds a Markdown summary (versions, service state, server config, log paths, last 30 lines of `~/.webjam.log`, sanitised settings — `webex_guest_issuer_secret` redacted) and pastes to clipboard.
- **Auto-save mix on shutdown** when the user touched the mix and Jamulus was connected. `_mix_dirty` flag flips True on any fader/mute/solo change, False after explicit save. Shutdown auto-saves so mid-session tweaks survive even if the user forgets Ctrl+S.

#### Wizard polish
- **Live validation hints** in the Jamulus and Webex pages.  Type-as-you-go feedback ("Host shouldn't contain spaces", "Will auto-prepend https://", "URL needs a domain"), no Next-button bouncing.

#### Refactor
- **`MixManager` extracted** from `ApplicationController` (`webjam_qt/controllers/mix_manager.py`, 124 LOC).  Owns `~/.webjam_mix.json` save/load/auto-restore. `_on_save_mix`/`_on_load_mix`/`_restore_saved_mix` retained as thin delegates.

#### State machine + correctness
- **`JamulusState` str-enum** in `services/bridge_service.py` (8 raw string assignments converted).  `_set_jamulus_state` writes under `_reconnect_lock`; `jamulus_process` writes likewise locked.  Inheritance from `str` keeps existing equality checks working transparently.
- **Memory leak: signal disconnect** in `ParticipantGrid._remove_card`.  Without this, `card.fader_changed.connect(self.fader_changed)` connections from `_add_card` survived `deleteLater()` and accumulated over join/leave churn.
- **Missing METRIC_KEYS added** (`ui/services.py`): `metric_jamulus_stop`, `metric_jamulus_port_conflict`, `metric_webex_leave`, `metric_session_completed` were incremented in code but absent from the canonical list.

#### macOS shortcut consistency
- **Ctrl+Shift+R / Ctrl+Shift+D bind to literal Control on macOS** (Qt.MetaModifier), matching the existing macOS-safe pattern used for Ctrl+M / Ctrl+Shift+M.  Avoids any potential Cmd+key system conflicts.

#### Tests
- **46 new tests** across 11 new files — port conflict detection, log capture, UDP protocol robustness, RPC hang banner, atomic notes export, MixManager round-trip, mix-dirty auto-save, diagnostics summary, wizard live validation.
- Suite total: **611 pass, 12 skipped** (was 565; +46 net; 0 failures).

#### Versioning
- `__version__` 0.4.5 → 0.4.6, surfaced in title bar and F1 help.

---

## [0.4.5] — 2026-04-25

### Deep-dive pass — data integrity, accessibility, performance, robustness

Synthesised from 17 parallel investigative + implementation agents across
two rounds covering architecture, performance, tests, real-world failures,
accessibility, integrations, persistence, docs, state machines, network
protocol robustness, cross-platform pitfalls, and error UX.

#### Data integrity
- **Atomic writes** for all persistent JSON/text via new `core/file_io.py::atomic_write_text` (temp file + fsync + `os.replace`).  Five call sites converted: setup wizard config, mix file, session notes, session metadata, canvas notes export.  8 new tests in `tests/test_file_io.py`.
- **Config file mode `0o600`** for `~/.webjam_config.json` (which can hold the `webex_guest_issuer_secret`).  Was world-readable.

#### Reliability + leak fixes
- **Subprocess log file leak fixed** in `bridge_service.launch_jamulus` — new `_close_jamulus_log_file()` helper called on shutdown-mid-launch and exception paths; idempotent.
- **State-machine bug**: `jamulus_reconnect_inflight` now cleared on the manual-launch failure paths (Not Found, Port In Use), so subsequent reconnect ticks aren't stuck on a stale True flag.
- **Bounded `_levels` dict** in `RealAudioEngine` (cap 1024 entries via LRU-trim); new `clear_level_overrides()` called from `JamulusController.stop()` so stale per-channel meter data doesn't leak between sessions.
- **RPC heartbeat** detects hung Jamulus (process alive but RPC silent for >15s).  Surfaces "Jamulus stopped responding" banner; auto-clears when activity resumes.

#### Real-world failure handling
- **Port conflict detection** before launching Jamulus.  Bind-tests `127.0.0.1:RPC_PORT`; if in use, shows actionable error pointing at `WEBJAM_JAMULUS_RPC_PORT` env var instead of silently leaving an uncontrollable Jamulus running.
- **Mix save/load specificity**: distinguishes OSError ("Permission denied. Check folder permissions and disk space"), JSONDecodeError ("Mix file is corrupted. Save a fresh one with Ctrl+S"), and generic exceptions.  All three flash for 6s and log full traceback.

#### UDP protocol hardening
- **`_parse_level_list`** capped at 500 entries (was unbounded — a hostile/malformed `CLT_CHANNEL_LEVEL_LIST` could allocate tens of thousands of dict entries).
- **Unknown msg_id logs deduped** — each unknown msg_id is logged once per session, preventing log floods from packet storms.

#### Cross-platform fixes
- **Windows `CREATE_NO_WINDOW`** in `subprocess.Popen` so the launched Jamulus doesn't pop up a spurious console alongside its GUI.
- **macOS Cmd+M conflict resolved** — Ctrl+M / Ctrl+Shift+M now bind to literal Control on macOS (via `Qt.MetaModifier`) so they don't collide with Cmd+M = system minimize.  Other platforms unchanged.  F1 help and shortcut labels reflect this.
- **Font fallback chain reordered** — Inter is not bundled, so `-apple-system, 'Segoe UI', 'Helvetica Neue', Helvetica, Arial, Inter, sans-serif` resolves correctly per platform.

#### Accessibility
- **`TEXT_MUTED` `#5F6B85 → #7A8AA0`** (was 2.93:1 contrast on BG_CARD — WCAG AA fail).  `TEXT_SECONDARY` bumped for safety margin.
- **Fader keyboard step**: `setSingleStep(5)` / `setPageStep(15)` (was default 1, made keyboard nav unusable).
- **Participant-context accessible names**: "Volume fader for Alice (decibels)", "Mute Alice", "Solo Alice".  Fader's accessible description includes the current dB value and updates on each change.
- **Side-rail focus border** 1px → 2px for visible keyboard navigation.

#### Performance
- **Single global LevelMeter timer** (was N per-card).  20 participants: 500 events/sec → 25/sec (-95%).  `level_meter.py::external_tick` flag, `participant_grid.tick_all_meters()`, driven by ApplicationController's `_meter_tick_timer`.

#### Webex integration
- **Token refresh on TTL approach** — 5-min safety margin before 1-hour expiry, polled every 60s.  Long rehearsals no longer silently lose Webex auth.
- **`mute_webex_self()` JS bridge** — Mute Me / Ctrl+Shift+M now silences the user in BOTH Jamulus AND Webex (was Jamulus-only).
- **Auto-restore placeholder** when Webex URL fails to load (404/DNS/blocked) — emits `error` state, restores placeholder, shows hint pointing at "Open video call in browser" fallback.

#### Architecture refactor
- **`SessionPersistence` extracted** from `ApplicationController` — `webjam_qt/controllers/session_persistence.py` (111 lines) owns notes + title + mode I/O.  Public methods on ApplicationController retained as thin delegates so existing tests pass unchanged.

#### Developer experience
- **`DEVELOPMENT.md`** +191 lines: 3 contributor tutorials (add a `ParticipantPresentation` field, add a Jamulus JSON-RPC method, wire a new keyboard shortcut) + sections on running tests / ruff / smoke-gate locally.
- **`.github/ISSUE_TEMPLATE/`** — `bug_report.yaml` + `feature_request.yaml` + `config.yml` with structured fields for OS/version/log excerpts.
- **Public docstrings** on `JamulusController.set_fader_level / set_mute`, `BridgeService.launch_jamulus / attempt_auto_reconnects`.
- **Friendly Python version error** in `webjam_qt_main.py` instead of cryptic `SyntaxError` on Python 3.9.
- **Wizard hints at `directory.jamulus.io`** for users without a server.

#### Tests
- **30 new tests** across these new files: `test_file_io`, `test_jamulus_rpc_fallback`, `test_jamulus_concurrent_mixer`, `test_webex_embed_lifecycle`, `test_bridge_reconnect_max_attempts`, `test_repository_mix_migration`, `test_application_controller_demo_to_real_transition`, `test_application_controller_signal_wiring`, `test_settings_corruption_recovery`, `test_audio_engine_levels_bound`, `test_session_persistence`, `test_level_meter_external_tick`, `test_webex_token_refresh`, `test_rpc_heartbeat`.
- **Suite total: 565 pass, 12 skipped** (was 523 at v0.4.4 release; +42 net).

#### Versioning
- **`__version__` → 0.4.5**, surfaced in title bar and F1 help.

---

## [0.4.4] — 2026-04-24

### Fixed — Session-control completeness

#### Toggle launch/stop and join/leave
- **Stop Audio button** (`services/bridge_service.py::stop_jamulus`, `webjam_qt/controllers/application_controller.py::_on_launch_audio`): The "Launch Audio" button now toggles. After Jamulus is running, clicking it prompts to stop; Yes terminates the subprocess (graceful terminate, force-kill at 2s) and stops the RPC/UDP monitoring threads. The auto-reconnect intent is cleared so the next reconnect tick doesn't immediately relaunch. Without this the conductor had to kill the app to end a session.
- **Leave Video button** (`services/bridge_service.py::leave_webex`, `application_controller.py::_on_join_video`): Same toggle treatment for the video button. `WebexEmbed.leave_meeting()` already existed but was never called from the UI; now it is. Bridge state is reset to "Not opened" and reconnect intent cleared.
- **Button labels reflect state**: `_refresh_readiness` now shows "Stop Audio" / "Leave Video" when active, and `_on_webex_state` shows the action-oriented "Leave Video" on the button while keeping the descriptive label ("In Meeting", "Lobby") in the status bar.
- **5 new tests** in `tests/test_reconnect_manager_edge.py` cover the new stop/leave paths: graceful termination, force-kill on timeout, clearing reconnect intent, monitoring stopped, leave_webex state reset.

#### Crash recovery is now visible
- **Reconnect banner** (`application_controller.py::_on_reconnect_tick`): When `jamulus_process.poll() is not None` is detected mid-session (Jamulus crashed), a flash message appears: "Jamulus disconnected — auto-reconnecting (attempt N/5)…". When the connection recovers, "Jamulus reconnected." flashes once. Previously the auto-reconnect machinery was completely silent.

#### App-close cleanup
- **Jamulus subprocess no longer survives app close** (`application_controller.py::shutdown`): The previous shutdown only stopped `JamulusController` monitoring threads — the Jamulus subprocess kept running and the user had to manually quit it. Shutdown now calls `bridge.stop_jamulus()` which terminates the subprocess too.

#### Discoverability
- **Tooltips on Launch Audio / Join Video** (`webjam_qt/widgets/session_strip.py`): Each button now hovers with a one-sentence explanation including the toggle behavior and how to access settings.
- **Log file path in error dialogs** (`application_controller.py::_show_actionable_error`): The actionable-error dialog now appends `For details, see the log file: ~/.webjam.log` so users know where to look when something goes wrong.
- **jamulus.io link in "Jamulus Not Found"** (`bridge_service.py::launch_jamulus`): The next-action text now points new users directly at https://jamulus.io to download Jamulus before falling back to the custom-location instructions.
- **F1 in-app help dialog** (`webjam_qt/windows/conductor_window.py`): F1 now opens a small dialog listing every keyboard shortcut, the colour-coded launch-button semantics, and a 4-step getting-started flow. Useful when users forget shortcuts mid-rehearsal without leaving the app to consult the README.

#### Mid-session settings changes are now context-aware
- **Targeted "leave/relaunch to apply" hints** (`application_controller.py::_open_settings_wizard`): The wizard used to flash a generic "take effect on next Launch Audio / Join Video" message after every save. It now snapshots `webex_url` + `jamulus_server` before the wizard, compares after, and shows specific actions if needed: "Leave Video and re-join to apply the new Webex URL" and/or "Stop Audio and re-launch to connect to the new Jamulus server".

#### Audit-found bugfixes
- **Reconnect-banner latch** (`application_controller.py::_stop_audio`): The `_reconnect_banner_shown` flag was set True on Jamulus crash and reset only when state went back to "Running". If the user clicked "Stop Audio" during reconnect attempts, the latch stayed True and future crash banners were silent. Cleared in `_stop_audio` so subsequent crashes flash again.

#### Tests
- **`tests/test_application_controller_toggle.py`** — 15 tests for `_is_jamulus_running`, `_is_video_active` predicates, button-label transitions, server:port in status bar, and self-mute behaviour.
- **`tests/test_reconnect_manager_edge.py`** — 8 new tests for `stop_jamulus` (terminate, force-kill, idempotency, dead-process), `leave_webex` (state reset, swallow controller errors).
- **`tests/test_qt_setup_wizard.py`** — 3 new tests for forgiving Webex URL validation (auto-prepend, scheme-prefixed bare-word rejection) and skip_welcome.
- **`tests/test_application_controller_toggle.py`** also covers: alone-on-server status, multi-participant counting, muted-card Qt property, session metadata round-trip.
- Suite total: **523 pass, 12 skipped**.

#### More live-session quality-of-life
- **'Mute Me' button + Ctrl+Shift+M** (`webjam_qt/widgets/session_strip.py`, `application_controller.py::_on_mute_self`): A new ghost button between the mode picker and audio button toggles mute on the local user's channel, with a Ctrl+Shift+M keyboard shortcut. Useful when the conductor needs to silence themselves quickly (answering a phone, talking off-mic) without finding their card in the grid. The button syncs in both directions with the local-user card's MUTE button.
- **Restore demos after Stop Audio** (`application_controller.py::_reset_to_demo_state`): When the user clicks Stop Audio, the (now-stale) real-participant cards are replaced with the demo placeholders and the demo-level animation restarts. The status-bar latency label resets to "Not connected". Gives a clear visual signal that audio is off.
- **Forgiving Webex URL validation** (`webjam_qt/windows/setup_wizard.py::_WebexPage.validatePage`): If the user types `org.webex.com/meet/foo` without a scheme, the wizard auto-prepends `https://` rather than silently refusing to advance. Bare words like "not-a-url" still fail (the auto-prepend only triggers on inputs containing a dot before any slash, and a final netloc-dot check rejects scheme-prefixed bare words too).

#### Layout density + session persistence
- **Per-card video tile shrunk to 6px accent bar** (`participant_card.py`, `conductor.qss`): The 'Video arrives when Webex is connected' placeholder used to occupy 120px+ of vertical space on every card, even though per-channel video isn't implemented (Webex video shows in the embedded view at the bottom of the stage). The tile is now a fixed-height 6px accent bar in brand colours (teal for remote, gold for local user). Card minimum height drops from 220px to 150px, fitting roughly 40% more participants on screen.
- **In-session Settings skips Welcome page** (`webjam_qt/windows/setup_wizard.py`): `SetupWizard` accepts a new `skip_welcome=True` keyword arg. When the user reopens Settings via Ctrl+, mid-session, the wizard now starts at the Jamulus page (skipping the welcome) and the title becomes 'WebJam Settings'. First-run flow is unchanged.
- **Session title persists across launches** (`application_controller.py::_load_session_title` / `_save_session_title`): The session title (e.g. 'Tuesday Practice') was lost on every close and reset to 'Band Rehearsal' on next launch. Now persisted to `~/.webjam_session.json` on title change and on shutdown; restored on startup.

#### At-a-glance state visualization
- **Muted participant cards fade visually** (`participant_card.py`, `conductor.qss`): Previously only the MUTE button changed colour when a channel was muted. The card itself now sets a `muted="true"` Qt property when muted, and QSS dims the background to BG_INPUT and the name/role text to TEXT_MUTED — making it easy to scan a busy stage and see who's silent.
- **Friendlier 'alone on server' status** (`application_controller.py::_apply_jamulus_participants`): When the user is the only channel on the server, the Session label now shows "1 participant · waiting for others" instead of the cold "1 participant". 2+ participants show "{N} participants" as before.
- **Last blocking 'Already running' dialog removed** (`services/bridge_service.py::launch_jamulus`): Re-clicking Launch Audio while Jamulus is already running used to throw a modal QMessageBox.information; now flashes a non-blocking status banner.

#### Webex embed resilience
- **Auto-restore placeholder when Webex URL fails to load** (`webjam_qt/widgets/webex_embed.py::_on_view_load_finished`, `application_controller.py::_on_webex_state`): When `QWebEngineView.loadFinished(ok=False)` fires (404, DNS, blocked, network), the embed emits a new "error" state. The controller restores the placeholder, resets the button to "Join Video", and flashes a hint pointing at the 'Open video call in browser' fallback button. Skips false positives from about:blank/data: navigations.

#### Troubleshooting infrastructure
- **Jamulus stdout/stderr captured to `~/.webjam_jamulus.log`** (`services/bridge_service.py::launch_jamulus`): Used to be discarded via `subprocess.DEVNULL`. Now line-buffered, overwritten per launch, closed on `stop_jamulus`. Falls back to DEVNULL if the file can't be opened.
- **Both log paths surfaced in error dialogs** (`application_controller.py::_show_actionable_error`): Lists `~/.webjam.log` (always) and `~/.webjam_jamulus.log` (only when it exists, to avoid confusion in 'Not Found' errors).
- **F1 help dialog mentions log paths** so users can find them without triggering an error first.

#### Versioning + onboarding
- **Bumped `__version__` 0.1.0 → 0.4.4** in `webjam_qt/__init__.py` (was stale across 4 minor releases).
- **Version surfaced in window title** (`WebJam — Conductor (v0.4.4)`) and **F1 help dialog header** (`WebJam — Conductor UI v0.4.4`).
- **Wizard now hints at directory.jamulus.io** for users who don't yet have a Jamulus server.
- **Friendly Python version error** in `webjam_qt_main.py` instead of cryptic `SyntaxError` on Python 3.9.
- **Red 'Unmute Me' button** when self is muted — `QPushButton#GhostButton:checked` paints in danger red (was visually identical to unmuted state).
- **Session mode persists** alongside title in `~/.webjam_session.json`. Bands using the same mode no longer need to re-select it on every launch.

---

## [0.4.3] — 2026-04-24

### Fixed — Critical mixer reliability + 4 UX improvements

#### Critical: mixer commands no longer silently dropped
- **`_check_participants` bypassed when RPC is active** (`jamulus_controller.py`): The UDP monitor loop ran every second and used the protocol adapter's cached participant list, which is always empty when UDP is disabled. This wiped `JamulusController.participants` each second, causing fader/mute/solo commands to hit an empty dict and be silently dropped between 5-second RPC poll cycles. Added an early-return guard matching the existing guard in `_on_udp_participants`.

#### UX
- **Audio button is now gold, video button is teal** (`session_strip.py`, `conductor.qss`): Both action buttons previously used the same teal "PrimaryButton" style. The audio button now uses the `AudioButton` objectName, rendering gold — visually distinguishing "Launch Audio" from "Join Video" at a glance. The `AudioButton` QSS rule is extended with a full set of states (border, padding, focus, pressed, disabled) since QSS has no inheritance within selectors.
- **Embedded Webex join keeps "Video Active" label** (`application_controller.py`): `_refresh_readiness` checked for the bridge-state string `"Opened in browser"` only. After an embedded `QWebEngineView` join, the bridge state becomes `"In Meeting"`, `"Joining…"`, etc. The reconnect timer would then reset the video button to `"Join Video"`. The check now uses a frozen set of all active states.
- **SideRail selection restored after modal actions** (`side_rail.py`, `application_controller.py`): Clicking "Chat", "Roles", or "Settings" in the side rail used to leave that item checked even though the view didn't change, making the nav rail misleading. The controller now tracks the last active content key and restores the rail selection after any modal/placeholder action. `SideRail` gains `current_key()` and `set_active_key(key)` helpers.
- **Setup wizard routing scan uses Signal, not `QMetaObject.invokeMethod`** (`setup_wizard.py`): The background routing scan used `QMetaObject.invokeMethod(self, "_apply_routing", QueuedConnection)` to marshal back to the UI thread, which can silently fail in PySide6 for Python-defined slots. Replaced with a class-level `_scan_complete = Signal()` connected to `_apply_routing` — signal emission across threads is always safe.

---

## [0.4.0] — 2026-04-24

### Fixed — Jamulus mixer RPC signal chain
- **Mute and solo now reach Jamulus via JSON-RPC** (`jamulus_controller.py`): `set_mute()` and `set_solo()` previously only sent UDP; mute/solo state was silently lost when the JSON-RPC server was the primary interface. Both now call a new `_send_rpc_gain()` helper that translates mute/solo state to an effective gain level and forwards it over RPC.
- **All RPC calls moved off the UI thread**: `_send_rpc_gain()` spawns a daemon thread for every `set_channel_gain` call. A slow or unreachable RPC server no longer freezes the UI.

### Fixed — Production bugfixes
- **`WebJamEnhancedApp` constructor ordering**: Property-delegated attributes (`jamulus_state`, `webex_state`, `jamulus_process`, etc.) were assigned before `bridge_service` was created, causing `AttributeError` on startup. Removed the redundant early assignments; `BridgeService.__init__` already sets matching defaults.
- **`_on_theme_changed` callback**: `ThemeManager` registered this callback on `WebJamEnhancedApp` but the method was missing. Added implementation that updates `high_contrast_enabled` and calls `_apply_accessibility_mode()`.
- **`session_controller` initialization**: `SessionController` was referenced (e.g. in `quit_app`) but never instantiated in `__init__`. Added `self.session_controller = SessionController(self)` after `bridge_service` creation.
- **`MixerService._saved_mix_payload_for_load`**: `load_mix()` called this helper but it was not defined. Added implementation that checks signed-in user profile first, then falls back to local mix file.

### Added — Test suite (Part 2 of v0.4 sprint)
- **All 11 previously-ignored edge test files now pass** in CI. Methods that migrated to `MixerService`, `BridgeService`, or `ModeController` during the v0.3 refactor were re-tested against their new homes:
  - `test_listening_profiles_edge.py` → `MixerService` (17 tests)
  - `test_reconnect_manager_edge.py` → `BridgeService` (12 tests)
  - `test_help_and_permissions_edge.py` → `MixerService` + `WebJamEnhancedApp` (4 tests)
  - `test_startup_smoke_edge.py` → `MixerService._restore_startup_mix_default` (2 tests)
  - `test_app_polling_edge.py` → updated stubs for `bridge_service` / `session_controller` delegation (14 tests)
  - `test_jamulus_controller_edge.py` → added `rpc_client` stub (11 tests)
  - `test_mode_layout_edge.py` → rewritten against `ModeController` (8 tests)
  - `test_mode_templates_edge.py`, `test_diagnostics_bundle_export_edge.py`, `test_session_brief_export_edge.py`, `test_docs_parity_edge.py` → updated for `_save_notes` rename and new stubs
  - `test_setup_flow_edge.py` → 3 tests migrated to `MixerService`
- **`README_SIMPLE.md`** added — quick-start guide referenced by `test_docs_parity_edge.py`
- **CI `--ignore` flags removed** from `.github/workflows/ci.yml` — full test suite now runs with no exclusions (493 pass, 12 skip on macOS)

---

## [0.4.2] — 2026-04-24

### Fixed / Added — Qt Conductor usability pass 2

#### Navigation
- **SideRail buttons wired**: clicking "Stage" or "Mixer" expands the participant grid; clicking "Canvas" expands the session notes panel; "Chat" and "Roles" flash a friendly "coming in a future update" message. Previously all four buttons did nothing. `ConductorWindow.center_splitter` is now a named attribute; both panels set collapsible so `setSizes` can resize them.

#### Participant metadata
- **`is_local` from Jamulus RPC**: `JamulusParticipant.is_local` field added and propagated from `ChannelInfo.is_local` (which is resolved via `getClientInfo` RPC). `ApplicationController._apply_jamulus_participants` uses the real flag instead of the `channel_id == 0` heuristic. Existing participants also get `is_local` refreshed on every RPC poll.
- **Role label refreshes for existing participants**: when an existing participant's instrument changes (e.g. mid-session Jamulus settings update), the role label is now updated in `self.participants` before the grid refresh, so the card reflects the new instrument.

#### Session canvas
- **Notes persist across launches**: `_load_notes` runs on startup, reading `~/.webjam_notes.md` into the canvas; `_save_notes` runs in `shutdown()` to write it back. Notes survive app restarts.
- **Timestamp button + Ctrl+T**: inserts the current time as a Markdown heading (`## HH:MM:SS`) at the cursor — useful for logging key moments during a session.
- **Export… button**: opens a Save-file dialog so you can write the session notes as a dated `.md` file (e.g. `webjam_session_2026-04-24.md`).
- **Clear button**: clears all notes after a confirmation prompt.

#### Status bar
- **Participant count replaces "—"**: the Latency status label now shows the live participant count ("3 participants") once Jamulus connects, rather than the static "—". Shows "Not connected" before first Jamulus update.

---

## [0.4.1] — 2026-04-24

### Fixed — Qt Conductor runtime gaps (weekend-usability sprint)

#### Signal wiring
- **Duplicate signal connections eliminated**: `ParticipantGrid` now declares `fader_changed / mute_toggled / solo_toggled` re-emit signals and wires them once per card in `_add_card`. `ApplicationController._wire_signals` connects to the grid once; the per-card loop in `_push_participants_to_grid` is removed. Previously, every participant update stacked new connections → N× callbacks per fader move.

#### Auto-reconnect
- **Auto-reconnect timer wired**: `ApplicationController` now starts a 3-second `QTimer` that calls `BridgeService.attempt_auto_reconnects()` on every tick. Previously, `attempt_auto_reconnects()` existed but was never called — dropped Jamulus processes were never retried.

#### Mix save / restore
- **Saved mix auto-restored on Jamulus connect**: when `JamulusController` fires its first real participant update (`_jamulus_connected` flips `True`), `_restore_saved_mix()` loads `~/.webjam_mix.json` and applies it. Fader layout comes back without manual action.
- **Ctrl+S / Ctrl+O (Save/Load Mix)**: new shortcuts in `ConductorWindow`; `ApplicationController` handlers call `JamulusController.serialize_mix` / `apply_mix_data` and flash a status-bar confirmation.

#### Jamulus path detection
- **macOS + Linux default candidates added** to `AppSettings.jamulus_candidates`: `/Applications/Jamulus.app/Contents/MacOS/Jamulus`, `/usr/bin/Jamulus`, `/usr/local/bin/Jamulus`, `/opt/homebrew/bin/Jamulus` — alongside the existing Windows paths. `find_jamulus()` now resolves on first run on common macOS/Linux installs.
- **Jamulus executable field in setup wizard**: the Jamulus page gains a path text field (pre-populated from first existing candidate) and a Browse button that resolves `.app` bundles to the binary. The chosen path is persisted at the front of `jamulus_candidates` in `~/.webjam_config.json`.

#### Error handling
- **`NameError` in BridgeService error dialogs fixed**: lambdas capturing `exc` from `except` blocks (Python 3 deletes `exc` after the block) caused a `NameError` when the actionable-error dialog was shown after a Jamulus or Webex launch failure. Fixed with `lambda m=str(exc): ...` captures.
- **Video button re-enable**: in direct-URL Webex mode the `meeting_state_changed` signal emits `"joining"` and then nothing (no JS bridge). The "Join Video" button was permanently disabled. A 6-second `QTimer.singleShot` now re-enables it as "Video Active".

#### Participant metadata
- **Instrument pass-through**: `_on_rpc_participants` now builds an `instrument_map` from `ChannelInfo` objects and writes each participant's `instrument` field after `_sync_participants_from_protocol`. Role labels in `ParticipantCard` automatically show the instrument (e.g., "Guitar", "Piano") instead of the generic "Musician" fallback.

#### Code quality
- Removed unused `webbrowser`, `Callable`, `Any` imports from `bridge_service.py`; split two single-line compound statements that ruff flagged as E701.

---

## Historical post-v0.3.0 development notes

### Added — Post-v0.3.0 gap fixes
- **Qt widget test suite** (`tests/test_qt_widgets.py`): 45 headless smoke tests covering `LevelMeter`, `ParticipantCard`, `SessionStrip`, `ParticipantGrid`, `SideRail`, and `ConductorWindow`
- **Qt setup wizard tests** (`tests/test_qt_setup_wizard.py`): 18 tests covering `should_show_on_startup`, Jamulus/Webex page validation, settings save/round-trip
- **Ruff linting gate** added to CI (lint step runs before tests; 8 auto-fixed unused imports)
- **`python3-tk` added to CI** apt-get — unblocks 11 previously-ignored Tkinter edge test files; only `test_elevation_edge.py` remains ignored (Windows ctypes.windll)
- **`test_elevation_edge.py`**: Windows-only skip guard — deferred imports prevent `ImportError` on macOS/Linux
- **`ui/mixer_service.py`**: `MIX_FILE` TODO resolved — path now sourced from `AppSettings.mix_file` via `settings=` constructor param; default is `~/.webjam_mix.json`
- **Setup wizard Done page**: explicit "Jamulus must be installed separately" note with link to jamulus.io
- **README status table**: updated to reflect v0.3.0 shipped Qt UI, correct limitation descriptions, and links to Releases page

---

## [0.3.0] — 2026-04-21

### Added — Phase 6: Onboarding, Shortcuts & Build
- **Setup Wizard** (`webjam_qt/windows/setup_wizard.py`): 5-page first-run wizard (Welcome, Jamulus server, Webex URL, audio routing, Done). Saves to `~/.webjam_config.json`. Auto-shown on first run.
- **Keyboard shortcuts**: Ctrl+L (focus session title), F11 (fullscreen), Escape (leave fullscreen), Ctrl+, (open settings)
- **Accessibility**: `setAccessibleName()` on all major panels, focus rings in QSS, screen-reader-compatible labels
- **PyInstaller spec** (`webjam.spec`): Production macOS/Windows bundle with QSS + HTML assets, Info.plist camera/mic usage strings

### Added — Phase 5: Audio Device Auto-Detection
- **`core/audio_routing.py`**: `scan_loopback_devices()` auto-detects VB-CABLE, BlackHole, Loopback Audio, JACK, Soundflower
- **`AudioRoutingStatus`** / **`LoopbackDevice`** dataclasses with device metadata (name, index, channel counts)
- **Setup wizard routing page**: shows detected device name or install instructions with link
- **`RealAudioEngine._resolve_device()`**: uses loopback scan to prefer virtual cable over system mic

### Added — Phase 3: Embedded Webex Meeting Pane
- **`webjam_qt/widgets/webex_embed.py`**: `QWebEngineView` embedded meeting pane (lazy-init, Chromium only started on first join)
- **`webjam_qt/webex_widget.html`**: Local HTML template loading Webex Meetings Widget from CDN; dark theme; loading spinner
- **`_WebexBridge(QObject)`**: QWebChannel bridge for bidirectional JS↔Qt communication (`on_page_ready`, `on_state`)
- **Guest-widget mode**: generates HS256 JWT, exchanges for access token, loads widget in embedded view
- **Direct-URL mode**: fallback — loads meeting URL directly using Chrome user-agent + persistent `webjam_webex` profile
- **Auto-grants** camera, mic, screen capture, notification permissions
- **`core/webex_guest_token.py`**: `generate_guest_jwt()` (stdlib HMAC-SHA256) + `exchange_guest_jwt()` (httpx POST)

### Added — Phase 2: Jamulus Protocol Integration
- **`core/jamulus_rpc_client.py`**: HTTP JSON-RPC 2.0 client with polling loop + SSE stream; `set_channel_gain()`, `set_channel_mute()`; non-blocking `stop()` via `httpx.Client.close()`
- **`core/jamulus_protocol.py`**: Full binary UDP adapter — CRC-16-CCITT (poly=0x1021), CONN_CLIENTS_LIST parser, CHANNEL_GAIN/CHANNEL_PAN commands, CLT_CHANNEL_LEVEL_LIST
- **JSON-RPC launch flag**: `services/bridge_service.py` adds `--jsonrpcport 22222` to Jamulus startup command
- **`services/bridge_service.py`**: `threading.Lock` guards reconnect-in-flight flags; exponential backoff for Jamulus/Webex reconnection
- **Real fader dB math**: `20*log10(level/100)` for 1..100; `(level-100)/27*6` for 101..127; `−∞ dB` for 0
- **Gain wire range fixed**: UDP gain mapped correctly as `int(fader_level / 127.0 * 32767)` (was /100 causing scale error)

### Fixed
- `@Slot()` missing on `_RoutingPage._apply_routing` — wizard routing scan result was silently dropped
- `QWebEnginePage` parented to profile (not widget) — eliminates "profile requested but page not deleted" warning
- SSE stream `stop()` now calls `httpx.Client.close()` to immediately unblock the reader thread
- `QSS`: added `QLabel#BodyLabel`, `QWidget#WebexPlaceholder`, `:focus` and `:disabled` states for all interactive widgets

### Changed
- `RealAudioEngine.stop()` thread join timeout: 1.5s → 3.0s for cleaner shutdown
- `WebexEmbed.load_meeting_with_guest_token()`: stays on placeholder until token arrives (was racing to show page before token fetch)

---

## Historical reliability and hardening rollup

### Security and Data Integrity
- Added serialized lockout mutation flow in `WebJamRepository.authenticate_with_status()` to avoid race-driven counter drift under concurrent failed authentication attempts.
- Switched password hash comparison to constant-time `hmac.compare_digest()` during authentication checks.

### Stability and Runtime Safety
- Hardened `JamulusController.load_mix()` against malformed files and invalid payload shapes with bounded coercion/clamping.
- Added atomic mix save behavior (`tempfile` + replace) to reduce partial-write corruption risk.
- Added participant-state synchronization (`RLock`) across controller and monitor paths to avoid cross-thread mutation hazards.
- Fixed participant auto-ID allocation after removals to avoid channel ID collisions.
- Added explicit sqlite connection management helper to prevent lingering connection warnings and improve cleanup reliability.
- Added sqlite runtime defaults for local repository usage:
  - `busy_timeout=5000`
  - best-effort `journal_mode=WAL`
- Added bounded retention for cohort telemetry events (latest 1000 kept per cohort key).
- Updated settings increment and cohort event append paths to run atomically under concurrency.

### Local API Bridge Resilience
- Added explicit bridge shutdown signaling and thread join behavior.
- Wrapped `/participants` and `/diagnostics` callback errors into HTTP 500 responses with actionable details.
- Added lightweight app-construction helper used by integration tests.

### Configuration and Operational Updates
- Added admin endpoint validation for empty host and out-of-range/non-numeric port values.
- Added warning logging when settings JSON is malformed and defaults are used.
- Added env bounds validation for `WEBJAM_JAMULUS_PORT` (`1..65535`) and sanity checks for numeric audio env values.
- Added env-gated startup debug logging controls:
  - `WEBJAM_AGENT_DEBUG_LOG`
  - `WEBJAM_AGENT_DEBUG_LOG_PATH`
- Updated diagnostics timestamp generation to timezone-aware UTC.

### Tests and Verification
- Expanded modernization and integration coverage:
  - auth lockout behavior under concurrency
  - bounded cohort event retention
  - API bridge callback error wrapping
  - TestClient endpoint integration checks (`/health`, `/participants`, `/diagnostics`)
  - malformed mix payload resilience and clamping/coercion behavior
- Full regression suites pass:
  - `python -m unittest test_modernization`
  - `python -m unittest test_webjam`

### Legacy Launcher Maintenance
- Extracted low-risk shared installer helpers into `utils/installer_helpers.py`.
- Rewired legacy launcher paths to use shared helper implementations to reduce maintenance drift.

---

## Version 2.0 - Enhanced Edition (historical legacy release)

### 🎉 Major New Features

#### Virtual Mixing Console
- **Professional mixer interface** with individual channel strips for each musician
- **Vertical faders** with dB scale (-∞ to 0dB) for precise volume control
- **Real-time VU meters** showing audio levels with color-coded indicators (green/yellow/red)
- **Pan controls** for stereo positioning (L-C-R) of each musician
- **Mute/Solo buttons** for quick channel control
- **Channel status indicators** showing connection state

#### Modern GUI Application
- **Complete rewrite** with modern tkinter/customtkinter interface
- **Dark theme** optimized for studio environments
- **Intuitive layout** familiar to musicians and audio engineers
- **Responsive design** that works on various screen sizes
- **Professional typography** and visual hierarchy

#### Session Management
- **Save/Load mix presets** for different songs or configurations
- **Automatic settings persistence** across sessions
- **Mix profiles** stored in user directory
- **Quick reset functions** for faders, pans, and mutes
- **Configuration backup** and restore

#### Jamulus Integration
- **Real-time participant detection** (foundation for future implementation)
- **Per-channel level control** via intuitive faders
- **Audio monitoring system** with simulated levels (ready for actual audio analysis)
- **Automatic channel creation** when musicians join
- **Connection status tracking** with visual indicators

#### Webex Integration
- **Browser-based meeting access** with one-click launch
- **Participant synchronization** framework (ready for SDK integration)
- **Embedded view preparation** for future Webex SDK implementation
- **Configuration management** for meeting preferences

### 🛠️ Technical Improvements

#### Architecture
- **Modular design** with separate controllers for Jamulus and Webex
- **Event-driven updates** using callback system
- **Threading** for non-blocking audio monitoring
- **Clean separation** of UI and business logic
- **Extensible framework** for future enhancements

#### Installation System
- **Enhanced installer** (`webjam_installer.py`) with better error handling
- **Progress indicators** for long-running operations
- **Smart dependency detection** and installation
- **Desktop and Start Menu shortcuts** created automatically
- **Application directory** in LocalAppData for clean installation

#### Build System
- **Automated build script** (`build_webjam.py`) for creating executables
- **PyInstaller integration** with proper bundling
- **Distribution package creation** with all necessary files
- **ZIP archive generation** for easy distribution

### 📚 Documentation

#### New Documentation Files
- **README.md**: Complete project overview and quick start
- **USER_GUIDE.md**: Comprehensive 30+ page user manual
- **CHANGELOG.md**: This file, tracking all changes
- **Code documentation**: Extensive docstrings and comments

#### User Guide Includes
- Installation instructions with screenshots
- Step-by-step first session tutorial
- Mixer control reference
- Troubleshooting section
- Professional mixing tips
- Keyboard shortcuts
- Technical appendix

### 🎨 User Interface Enhancements

#### Visual Design
- **Color-coded controls**: Mute (red), Solo (green), Status (green/gray)
- **Professional meters**: VU meters with proper ballistics
- **Clear typography**: Arial font with appropriate sizing
- **Visual feedback**: Button states, hover effects, active indicators
- **Consistent spacing**: Professional layout with proper padding

#### Usability Features
- **Menu bar** with File, Session, and Help menus
- **Status bar** showing participant count and server info
- **Control bar** with quick-access buttons
- **Tooltips** and labels for all controls
- **Keyboard shortcuts** for common operations
- **Modal dialogs** for confirmations and errors

### 🔧 Developer Experience

#### Code Quality
- **Type hints** throughout codebase
- **Dataclasses** for clean data structures
- **Descriptive naming** following Python conventions
- **Error handling** with try-except blocks
- **Logging and debugging** print statements

#### Project Structure
```
WebJam/
├── webjam_app_enhanced.py      # Main GUI application (New)
├── webjam_app.py               # Basic GUI version
├── jamulus_controller.py       # Jamulus integration module (New)
├── webex_integration.py        # Webex integration module (New)
├── webjam_installer.py         # Enhanced installer (New)
├── build_webjam.py             # Build automation (New)
├── requirements.txt            # Python dependencies
├── README.md                   # Project documentation (Enhanced)
├── USER_GUIDE.md              # Comprehensive user manual (New)
├── CHANGELOG.md               # This file (New)
├── webjam_launch_session.py   # Legacy launcher
├── webjam_win_oneclick.py     # Legacy installer
└── VB/                        # VB-Cable drivers
```

---

## Version 1.0 - Initial Release

### Core Features

#### Basic Functionality
- **One-click installer** for Jamulus and VB-Cable
- **Automatic audio routing** setup
- **Desktop shortcut** creation
- **Simple launcher** script

#### Components
- VB-Cable installation with driver detection
- Jamulus installation with multiple installer support
- Audio device configuration via PowerShell
- Webex meeting launcher

#### Limitations of v1.0
- ❌ No mixer controls (used Jamulus built-in mixer)
- ❌ No GUI application (command-line only)
- ❌ No session management
- ❌ Manual participant management
- ❌ Limited configuration options

---

## Migration Guide: v1.0 → v2.0

### For End Users

#### What Changed
1. **New Application**: Launch "WebJam" instead of old launcher
2. **Mixer Interface**: Control levels in WebJam, not Jamulus window
3. **Better Integration**: Automatic participant detection

#### Migration Steps
1. Uninstall old WebJam (optional - won't conflict)
2. Run new WebJam_Installer.exe
3. Launch from new Desktop shortcut
4. Enjoy enhanced features!

#### Settings Migration
- Old settings are not migrated automatically
- Recreate your mix preferences in new interface
- Save your mix using the new Save Mix feature

### For Developers

#### API Changes
- `JamulusController` class replaces direct subprocess calls
- `WebexController` provides structured meeting access
- Event-driven architecture with callbacks
- Configuration via JSON files instead of constants

#### Code Migration
```python
# Old approach (v1.0)
subprocess.Popen([jamulus_path, "--connect", server])

# New approach (v2.0)
controller = JamulusController(server, port)
controller.start()
controller.add_participant("Musician", channel_id)
controller.set_fader_level(channel_id, 75)
```

---

## Roadmap - Future Versions

### Version 2.1 (Planned)

#### Features
- [ ] **Direct Jamulus Protocol**: Implement full Jamulus UDP protocol
- [ ] **Real audio monitoring**: Use PyAudio to analyze actual audio levels
- [ ] **Participant auto-detection**: Automatically discover musicians from Jamulus
- [ ] **Effects processing**: Per-channel EQ, compression, reverb
- [ ] **Recording**: Multi-track recording directly in WebJam

#### Improvements
- [ ] **Performance optimization**: Reduce CPU usage
- [ ] **Better error messages**: User-friendly error dialogs
- [ ] **Config GUI**: Settings panel for advanced options
- [ ] **Server selection**: Choose from multiple Jamulus servers

### Version 3.0 (Future)

#### Major Features
- [ ] **Webex SDK Integration**: Embedded video within WebJam window
- [ ] **MIDI Control**: Use physical faders/controllers
- [ ] **Mobile Companion**: iOS/Android remote control app
- [ ] **Cloud Sync**: Sync settings across devices
- [ ] **AI-Powered Mixing**: Automatic level balancing

#### Professional Features
- [ ] **VST Plugin Support**: Load audio effects plugins
- [ ] **Multi-server**: Connect to multiple Jamulus servers simultaneously
- [ ] **Advanced Routing**: Custom audio routing matrix
- [ ] **Metering**: Professional audio meters (PPM, RMS, LUFS)
- [ ] **Time Alignment**: Compensate for latency differences

### Community Wishlist

Vote for features you want to see:
- [ ] Linux and macOS support
- [ ] Standalone mode (Jamulus+Webex in one)
- [ ] Practice room scheduling
- [ ] Integrated chat
- [ ] Sheet music viewer
- [ ] Metronome with sync
- [ ] Latency testing tools
- [ ] Performance analytics

---

## Historical v2.0-era notes (archived)

The following notes were preserved from a 2024 planning document. They do not
describe the current v0.12 Host/Join UI, Webex handoff boundary, or packaging
claims; use the Unreleased entry, README, and v1 last-mile readiness record
above for current behavior.

### Current Limitations

#### Jamulus Integration
- ~~**Participant detection** is currently manual~~ — **Resolved** (Phase 2): Full Jamulus UDP protocol + JSON-RPC client auto-detects participants via CONN_CLIENTS_LIST
- ~~**Audio levels** are simulated~~ — **Resolved** (Phase 2): Real fader dB math and UDP gain wiring implemented in `core/jamulus_protocol.py`
- ~~**Mixer commands** don't yet control actual Jamulus mixer~~ — **Resolved** (Phase 2): `set_channel_gain()` and `set_channel_mute()` wired to live Jamulus JSON-RPC endpoint

#### Webex Integration
- ~~**Browser-based** video (not embedded in app)~~ — **Resolved** (Phase 3): `QWebEngineView` embedded meeting pane with `webex_widget.html` template
- ~~**Participant sync** is name-based matching only~~ — **Resolved** (Phase 3): Bidirectional JS↔Qt bridge via `_WebexBridge(QObject)` + QWebChannel
- **No video controls** from within WebJam — still managed via the embedded Webex widget UI

#### Audio Routing
- ~~**VB-Cable required**: No built-in virtual audio device~~ — **Resolved** (Phase 5): `scan_loopback_devices()` auto-detects VB-CABLE, BlackHole, Loopback Audio, JACK, and Soundflower
- ~~**Manual device setup**: May need manual configuration~~ — **Resolved** (Phase 5/6): Setup wizard routing page auto-detects and configures the preferred virtual device
- **Single audio stream**: Can't separate audio and video audio — still a system-level constraint

### Bug Reports

For a current issue, report it at:
1. Go to: https://github.com/rupret007/webjam/issues
2. Click "New Issue"
3. Describe the problem with steps to reproduce
4. Include your system info (Windows version, audio interface, etc.)

---

## Historical credits and acknowledgments

### WebJam Team
- **Development**: [Your Name]
- **UI/UX Design**: [Designer]
- **Testing**: [Testers]
- **Documentation**: [Writers]

### Open Source Projects
- **Jamulus**: Low-latency audio - [jamulus.io](https://jamulus.io)
- **VB-Audio**: Virtual audio cables - [vb-audio.com](https://vb-audio.com)
- **CustomTkinter**: Modern tkinter - [github.com/TomSchimansky/CustomTkinter](https://github.com/TomSchimansky/CustomTkinter)
- **PyInstaller**: Python packaging - [pyinstaller.org](https://pyinstaller.org)

### Special Thanks
- Jamulus community for inspiration
- Beta testers for valuable feedback
- Musicians who tried early versions
- Open source community for tools and libraries

---

## License

WebJam is released under the MIT License.

Copyright (c) 2024 WebJam Contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

---

**Historical metadata**: October 9, 2024 · Version 2.0.0 · Release Candidate

For current updates, visit: **[WebJam on GitHub](https://github.com/rupret007/webjam)**
