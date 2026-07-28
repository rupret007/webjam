# ADR 0006: Standalone Reference Studio projects

- Status: Accepted for the v0.21.0 private test candidate
- Date: 2026-07-28
- Supersedes: none
- Related: [ADR 0005](0005-reference-track-jamulus-participant.md)

## Context

WebJam's original Studio is downstream of a completed rehearsal take. Its
source manifests and WAVs are evidence, and its schema-2 sidecar describes
non-destructive choices over that evidence. That is the right boundary for
session recording, but it cannot represent a song that begins with a local
backing track, empty audio tracks, repeated local recording passes, or a
portable writing project.

Putting songwriting state in session settings would couple ordinary project
work to Jamulus lifecycle and make it easy to confuse local playback with live
audio. Reusing an external media path would make projects fragile and could
leak private paths. Treating imported audio as mutable would undermine relink,
recovery, bounce, and provenance checks.

Reference Studio therefore needs its own durable project identity, media
catalog, arrangement schema, save/recovery contract, and local audio ownership.
It must coexist with legacy session Studio without silently migrating or
reinterpreting historical evidence.

## Decision

### Separate product and audio lifecycle

Reference Studio is a standalone workflow under WebJam's Studio rail. It owns
only its project, workers, and local playback/recording backend. It does not:

- start, join, stop, or reconfigure Jamulus;
- route its backing or monitor mix into a live session;
- claim a Webex join or own Webex audio;
- unlock the separately capability-gated Reference Track pilot.

The application controller includes Reference Studio in close/shutdown
coordination so unfinished saves, recordings, bounce workers, analysis workers,
and local audio are handled before process exit.

### Two linked, versioned documents

A standalone project folder contains:

```text
<Song Project>/
  webjam-project.json
  .webjam-project.json.bak
  .webjam-project.autosave.json
  .webjam-song-studio.json
  .webjam-song-studio.json.bak
  .webjam-song-studio.autosave.json
  Media/
    <media UUID>.<validated suffix>
```

The schema-1 `SongProject` manifest owns:

- the project UUID, name, revision, sample rate, tempo, and time signature;
- ordered audio-track identities and portable input-mapping intent;
- collected-media identities, normalized `Media/<filename>` locations,
  size/hash/audio identity, source basename, and bounded provenance;
- the first-class backing-media identity.

The schema-3 `StudioDocument` owns:

- the same project UUID;
- backing, audio, bus, and master signal-flow state;
- regions whose `source_media_id` resolves through the sealed project catalog;
- markers, named sections, take lanes, comp ranges, fades, crossfades, cycle,
  snap, mix, effects, sends, and volume/pan/mute automation.

Neither document stores an absolute media path. `SongMediaCatalog` resolves
only a manifest-declared file directly below the project `Media/` directory and
seals its file identity, size, checksum, format, sample rate, channel count,
and frame count before a renderer, player, recorder commit, analyzer, waveform
worker, or bounce worker may use it.

### Immutable collected media

Import, relink, recording commit, and Save As use staged files and atomic
publication. A source chosen by a musician is read-only input. Once collected,
project media is never opened for write by Arrange, playback, waveform, tempo
analysis, mixing, automation, or bounce.

The arrangement refers to media UUIDs and integer frames. Removing or replacing
backing audio tombstones or reconciles arrangement references without editing
the old bytes. Lossy track reconciliation is rejected when active arrangement
content would be discarded.

### Persistence, autosave, and recovery

Project and Studio stores use bounded, strict JSON, exact-byte compare-and-swap
tokens, locks, atomic replace, and last-known-good backups. Autosaves are
separate recovery candidates; they are never silently promoted during open.
The user must explicitly recover or discard a valid newer candidate.

An invalid, oversized, conflicting, or externally changed document fails
closed. A failed save remains dirty and retryable. Close can be refused if a
final save cannot make current work durable.

**Save As** is a cross-document transaction:

1. Revalidate the open source project and Studio tokens.
2. Reject aliasing, nested, occupied, linked, or special-file destinations.
3. Stage a complete new bundle with copied and reverified immutable media.
4. Assign a fresh project identity and remap deterministic project-derived
   backing/arrangement IDs while preserving user edits and stable source
   meaning.
5. Verify both staged documents and their sealed media catalog.
6. Publish the complete folder atomically.

On any pre-publication failure, no destination project becomes visible and the
source remains unchanged.

### Recording commit and recovery

The physical recorder publishes bounded WAV results outside the bundle first.
A separate commit service is the only bridge that collects them into a
project. It validates the capture result, schedule, track/input mapping, audio
identity, and project/Studio tokens before staging media and new arrangement
state.

Commit evidence preserves punch bounds, count-in, cycle bounds and passes,
input channels, latency compensation, dropout intervals, recovered state, and
the resulting media/region/lane identities. A failure before the transaction's
commit point rolls back. A failure after durable capture evidence exists
creates an explicit recovery candidate; it is not reported as a completed
arrangement commit.

### One rendering and mix graph

`StudioRenderer` resolves schema-3 regions through `SongMediaCatalog`.
Progressive waveforms and local playback use the same frame-domain arrangement.
`StudioMixEngine` adds deterministic, bounded channel strips, acyclic buses,
sends, exact-frame volume/pan/mute automation, high-pass, EQ, compressor, gate,
shared reverb, master gain, and safety limiter.

Playback, bounce, and stems use the same validated routing and DSP semantics.
Device callback code remains bounded; file decoding, checksum work, waveform
generation, tempo analysis, and bounce publication run outside the UI/audio
callback.

Tempo analysis reads bounded windows from a sealed catalog and proposes one
constant tempo with confidence. It never edits audio and cannot apply itself
without musician review. Tempo maps with changing segments remain supported by
the underlying musical-time model; automatic multi-tempo inference and
downbeat/meter detection are not claimed.

Bounce supports atomic PCM24 WAV and FLAC mixes and processed stems, selectable
track/backing/range policy, SHA-256, peak, clipped-sample count, and
deterministic RMS dBFS. RMS is not LUFS. MP3 is capability-gated behind a
caller-supplied adapter that must identify itself, pass an encode/decode
self-test, and satisfy license policy; no default MP3 encoder ships.

## Compatibility and migration

| Existing data | v0.21 behavior |
| --- | --- |
| Session/take manifests and immutable session WAVs | Unchanged; remain recording evidence |
| Schema-1 legacy mix sidecar | Continues through the existing in-memory migration to session Studio schema 2 |
| Schema-2 session Studio sidecar | Opens only in session/take Studio; not rewritten as a standalone song |
| Schema-1 standalone `webjam-project.json` plus schema-3 song Studio sidecar | Opens in Reference Studio after project-ID, token, catalog, and cross-document validation |
| v0.20 settings and recents | Retained; Reference Studio recents use their own bounded path store |
| Old release tags/assets | Immutable; v0.21 receives a new tag and never moves v0.20.0 |

There is deliberately no automatic “convert rehearsal to Reference Studio”
command in this decision. A musician may explicitly import a supported audio
file or future code may implement a reviewed copy transaction, but session
evidence must never be reclassified by inference.

Unknown future schema versions are rejected rather than guessed. Migrations
must be deterministic, bounded, idempotent, and covered by fixtures that retain
the original bytes until an explicit successful save.

## Privacy and safety invariants

- Media paths, recording source paths, and bounce destinations are not stored
  in logs, settings, or user-visible error evidence.
- Media identifiers, checksums, and bounded source basenames may be persisted;
  source file bytes stay in the project only after explicit collection.
- No project operation writes an original imported file.
- No bounce destination may be inside the project bundle.
- Cancellation or stale-generation detection cannot publish a partial
  waveform, analysis, recording commit, Save As bundle, or bounce.
- Reference Studio never represents software start, meter activity, decoded
  samples, or synthetic tests as human audibility.
- The continuous trefoil (“trinity”) identity is reused from the canonical
  product artwork; Reference Studio does not create a divergent logo asset.

## Release consequence

v0.21.0 remains a private test candidate on all four target builds:

- Windows x64 Setup and portable ZIP are unsigned.
- Intel and Apple-silicon Mac DMGs and ZIPs are ad-hoc signed and unnotarized.
- Ubuntu 22.04 x64 is delivered as a ZIP.

Tag CI must first create a non-prerelease draft containing exactly those seven
packages plus their checksum manifest. The separate manual publisher verifies
the immutable tag/version, exact inventory, and every package checksum before
publishing with an explicit GitHub **Latest** selection, then verifies the
`/releases/latest` endpoint. An Actions artifact, a successful matrix, or a
draft release is not the Latest release.

Physical Reference Studio audio, interface mapping, latency calibration,
recording/recovery, long-session behavior, Windows driver behavior, Intel Mac
hardware, and clean-download trust prompts remain **NOT RUN** until recorded
against the exact released hashes. Windows code signing, Apple Developer ID,
and notarization remain optional protected rehearsal paths and are not implied
by candidate publication.

## Consequences

The product gains a portable, testable songwriting model without making
Host/Join more complex or weakening session-recording provenance. Media
duplication consumes additional disk space, and project folders must be copied
as a unit. The strict catalog and two-document transaction add implementation
cost, but make recovery, relink, rendering, and delivery claims inspectable.

Third-party plug-in hosting, MP3 delivery, automatic meter/downbeat detection,
automatic conversion from session Studio, cloud project sync, and routing
standalone playback into Jamulus are outside this decision.
