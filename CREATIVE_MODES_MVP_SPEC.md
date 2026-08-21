# Creator profiles — v0.26.0 release contract

> Status: Music, Podcast & Voice, and Review & Rehearsal are implemented in
> immutable v0.26.0, the GitHub **Latest** private test release. The exact tag,
> packages, checksum manifest, and protected publication are verified release
> evidence; no physical PASS is claimed. **Studio Visit is added after v0.26.0
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
| Studio Visit | Preview | Host Studio Visit/Join Studio Visit, Session Check, optional host-clocked reference video | None — the session is not recorded | Blocked |

Review & Rehearsal also blocks take editing, comping, mix mutation, track
export, shared notes, visual synchronization, and media timecode. No profile
directly or automatically taps a meeting app, browser, or system output.
Scratchpads are profile-scoped on one computer, stored through fixed
private mode-0600 files with regular-file/no-follow reads bounded to 1 MiB.
They are never shared, session-synchronized, or media-timecoded.

## Studio Visit (added after v0.26.0, no release evidence)

Studio Visit is a room where artists in any medium — painting, drawing,
sculpture, anything on a table — talk while they work. Conversation uses the
same WebJam audio path and the same optional external meeting handoff as every
other profile. The profile adds exactly one capability of its own: an
**optional** host-clocked reference video.

**A room with no video is the first-class path**, not a degraded empty player.
Nothing about the profile requires anyone to share anything.

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
**not** frame-accurate review and carries **no media timecode**. Studio Visit's
capability set asserts `media_timecode=False`, and the registry refuses to load
a Studio Visit profile that claims otherwise.

Because the reference video is not bound into the recording plan's source
identity, **Studio Visit does not record a session**. Rather than fake a take
whose sources cannot be proven, the profile disables session recording, and
therefore take review, take editing, and track export. Its conductor offers no
Record action.

### Explicit non-goals for Studio Visit

- no shared drawing canvas or collaborative surface;
- no camera-on-the-easel feed;
- no shipped, bundled, or downloadable video catalog, and no ripped or ingested
  third-party lesson content;
- no frame-accurate video review and no media timecode;
- no Jamulus reference-audio route;
- no recorded take, take review, take editing, or track export;
- no standalone Studio Visit project.

## Persistence and migration

- The selected profile key persists in settings and new session metadata.
- New standalone projects and recorded session evidence retain their profile.
- A legacy project, take, session, or settings record without a profile key
  migrates to Music.
- Previously persisted mode aliases map explicitly to one current profile;
  malformed or unknown keys fail safely to Music rather than inventing a mode.
- Opening a Review & Rehearsal standalone project is refused before mutation.
- `studio_visit` is a new canonical key. It could not reuse `visual_studio`,
  because the registry refuses a canonical key that is also a legacy alias.
- The legacy `visual_studio` mode now migrates to **Studio Visit**. It was the
  visual-arts mode and only pointed at Review & Rehearsal because no artist
  profile existed; someone whose last saved workflow was Visual Studio opens
  into a room for making things rather than a review Preview. The other legacy
  modes — `writers_room`, `design_critique`, `storyboard_film_room` — are
  discussion and planning rooms and stay on Review & Rehearsal.
- `visual_studio` remains a valid legacy *mode* key in its own registry, so
  session metadata that records it keeps resolving. Only the profile it
  migrates to changed.
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

Studio Visit is deliberately outside that plan. Its reference video is not a
recorded source and is not bound into any take, so the profile disables session
recording entirely rather than producing a take whose sources cannot be proven.
There is one session truth, and Studio Visit does not invent a second one.

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
- no Studio Visit canvas, camera feed, shipped video catalog, ingested
  third-party lesson content, or recorded take;
- no claim that a link handoff joined, muted, found participants, or recorded;
- no physical-audio, hardware, signing, notarization, or accessibility PASS
  without exact package evidence in the v0.26 checklist;
- no physical two-computer PASS for Studio Visit's reference video: its
  host-clocked follow behavior is proven by automated tests only.

Studio Visit synchronizes one host-clocked reference video, which is the single
exception to the blanket "no visual synchronization" statement elsewhere in
this document. That exception does not extend to frame accuracy or timecode,
which remain non-goals for every profile.
