# Creator profiles — v0.25.0 implemented contract

> Status: implemented in the immutable v0.25.0 GitHub **Latest** private test
> release. This document supersedes the earlier speculative cross-discipline
> MVP; it describes only the bounded behavior in v0.25.0 code. Physical and
> platform-trust results remain **NOT RUN** until separately observed against an
> exact checksum-verified release asset.

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

Any meeting platform may receive an explicit hardened public-HTTPS link
handoff. Native app verification and focus remain Webex-only. WebJam never
directly or automatically taps a meeting app, browser, or system output.
Record Session can include explicitly planned Local Originals from input
devices the user selects, so users must not route meeting or system-output
audio into those inputs.

## Explicit non-goals for v0.25.0

- no visual canvas or frame-accurate video review;
- no shared or network-synchronized notes;
- no direct or automatic meeting-app, browser, or system-output capture;
- no media timecode;
- no Review & Rehearsal standalone project, edit, comp, mix mutation, or export;
- no claim that a link handoff joined, muted, found participants, or recorded;
- no physical-audio, hardware, signing, notarization, or accessibility PASS
  without exact package evidence in the v0.25 checklist.
