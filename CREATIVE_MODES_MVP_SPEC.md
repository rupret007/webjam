# Creator profiles — v0.26.0 source-candidate contract

> Status: implemented in the unpublished v0.26.0 source candidate. Immutable
> v0.25.0 remains GitHub **Latest**; no v0.26.0 tag, package, checksum manifest,
> physical PASS, or GitHub release is claimed. This document supersedes the
> earlier speculative cross-discipline MVP and describes only bounded current
> source behavior. Physical and platform-trust results remain **NOT RUN**.

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

Review & Rehearsal also blocks take editing, comping, mix mutation, track
export, shared notes, visual synchronization, and media timecode. No profile
directly or automatically taps a meeting app, browser, or system output.
Scratchpads are profile-scoped on one computer, stored through fixed
private mode-0600 files with regular-file/no-follow reads bounded to 1 MiB.
They are never shared, session-synchronized, or media-timecoded.

## Persistence and migration

- The selected profile key persists in settings and new session metadata.
- New standalone projects and recorded session evidence retain their profile.
- A legacy project, take, session, or settings record without a profile key
  migrates to Music.
- Previously persisted mode aliases map explicitly to one current profile;
  malformed or unknown keys fail safely to Music rather than inventing a mode.
- Opening a Review & Rehearsal standalone project is refused before mutation.

## Shared evidence rules

Every profile uses the same authoritative recording plan. It binds the exact
take, roster/server stems, Shared Track fingerprint and playback generation,
host mono/stereo input topology, guest Local Original obligations, storage
verdict, count-in/pre-roll, and expected source count. Finalization rechecks
that identity and fails closed on missing, extra, changed, or substituted
sources.

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

## Explicit non-goals for v0.26.0

- no visual canvas or frame-accurate video review;
- no shared or network-synchronized notes;
- no direct or automatic meeting-app, browser, or system-output capture;
- no media timecode;
- no Review & Rehearsal standalone project, edit, comp, mix mutation, or export;
- no claim that a link handoff joined, muted, found participants, or recorded;
- no physical-audio, hardware, signing, notarization, or accessibility PASS
  without exact package evidence in the v0.26 checklist.
