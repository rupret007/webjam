# WebJam project brief

**Review date:** 2026-08-27
**Status:** unsigned/ad-hoc v0.27.1 is GitHub Latest release `377614785`.
Current v0.27.2 source is post-tag and is not that package. Live Jamulus
Host/Join reuses the existing exact 3.12.2 and 3.12.3 records; every v0.27
physical/hardware gate is **NOT RUN**.

## Executive summary

WebJam is a creator-first desktop conductor for collaborative audio work and
review. It coordinates a private session, invitation, recording, and
recovery experience around Jamulus's low-latency audio path; optionally opens
any meeting platform whose public HTTPS link passes WebJam's safety policy for
conversation/video; and provides a standalone Reference Studio for
writing, voice production, arranging, overdubbing, and exporting ideas.

Music and Podcast & Voice are GA creator profiles. Review & Rehearsal is
Preview: it supports live WebJam-audio Host/Join, Record Session, a local-only
scratchpad, and playback/read-only review of completed session takes. It blocks
standalone projects, take editing/comp/mix mutation, track export,
shared notes, visual sync, and media timecode. Art is Preview and is for
artists rather than musicians: a live WebJam-audio room to talk in while
painting, drawing, sculpting, or building. It offers two visible starts and no
more: **Make together**, where people work locally and the host may open one
shared Drawpile canvas from inside the room, or **Paint along**, with one
optional reference video the host plays while each computer shows its own copy
of the same local file. Inside a session it can also open Krita's AI plugin to
make or edit one local image. It blocks standalone projects, session recording
and every take capability, a Jamulus reference-audio route, any canvas surface
or image generator of its own, any cloud image service, and any frame-accurate
or media-timecode claim. No profile directly or
automatically taps a meeting app, browser, or system output.

The product thesis is simple: creators should have one clear place to conduct
the session while each specialist system keeps the responsibility it is good at.
WebJam does not replace Jamulus's audio engine, a meeting service's state, or
the operating system's security decisions.

## The user problem

Remote creative-audio tools are powerful but fragmented. A group must coordinate
an audio client, a meeting application, recording state, invitations, recovery,
and creative follow-up while trying to stay in the moment. WebJam turns
those transitions into a shared, inspectable workflow with explicit next steps
and truthful failure states.

## Product surfaces

| Surface | Purpose | Owner of the critical truth |
| --- | --- | --- |
| Creator profile | Select Music, Podcast & Voice, Review & Rehearsal, or Art presentation and capability gates | WebJam profile registry |
| Paint along | Art only: a large in-WebJam workspace with host-clocked play/pause/seek over one local file each computer holds its own proven copy of | WebJam reference video controller |
| Shared Canvas | Art only: one Drawpile session the host chooses and WebJam's invitation carries; Drawpile draws every stroke and WebJam cannot see the canvas | Drawpile |
| Room clock | One named pulse any surface may read: a song form, a reference video position, or none. Exactly one owner at a time; readers cannot move it | Whoever owns the pulse |
| AI Image | Art only, in session: Make a new image or Edit one the artist owns, in Krita's AI plugin against a loopback backend; results are local files, nothing is published to the room | Krita AI Diffusion + local ComfyUI |
| Host / Join | Start or enter a private live session | WebJam session conductor + Jamulus |
| Conversation | Provider-neutral public-HTTPS meeting handoff; friendly labels for Webex/Zoom/Teams/Meet/FaceTime; separate native Webex focus guidance | Selected meeting service |
| Record Session | Capture and finalize a session with durable identity | WebJam recorder + Jamulus evidence |
| Reference Studio | Local writing, arrangement, take lanes, overdub, and bounce | WebJam Studio backend |
| Shared Track | Host-controlled backing audio routed as a separate Jamulus participant | WebJam lifecycle + Jamulus |
| Pocket Stage | Owner's iPhone as a focused second screen and remote | Desktop remains authoritative |

## Architecture in one view

```text
Creator
   │
   ▼
WebJam desktop conductor ── session / Shared Track / recording / Studio / guidance
   │                 │
   │                 ├── Jamulus client/server ── live audio, devices, mix, jitter
   │                 ├── Meeting app/browser ─── conversation and video
   │                 └── Pocket Stage iPhone ─── owner-device control preview
   │
   └── Reference Studio stays separate from live Shared Track audio ownership
```

The architecture is intentionally process- and evidence-oriented: external
apps remain externally owned, destructive shortcuts are rejected, and support
projections allow only bounded facts. See [Architecture](../ARCHITECTURE.md) for
the full contract.

## Verified status

- The published v0.26.0 release contains Windows x64, Ubuntu 22.04 x64, Intel
  Mac, and Apple-silicon Mac packages plus an exact checksum manifest.
- `cryptography` 50.0.0 remediates the three audited runtime CVEs; the Intel
  Mac source-build exception is explicit, hash-locked, and separately verified.
- CI covers Python, transport, reference service, Pocket Stage compilation,
  real Jamulus inputs, package assembly, frozen-package checks, and release
  promotion.
- Physical two-Mac audibility, hardware recovery, meeting handoff, Pocket Stage
  owner-device pairing, signing/notarization, and long-session evidence remain
  **NOT RUN**. Automated green does not mean those gates passed.
- v0.26.0 publication passed its exact tag, package inventory, checksum
  manifest, fallback proof, protected promotion, and public redownload checks.
  The sealed v0.22.5 catalog was explicitly rejected for v0.26.0 and is not
  managed-update authorization for the changed line.
- Immutable v0.25.0 remains historical release evidence with its original tag,
  assets, checksums, and protected promotion.
- Immutable v0.24.0 remains historical release evidence with its original tag,
  assets, checksums, and protected promotion.
- v0.26.0 binds each take to the exact roster/server stems,
  Shared Track identity/generation, host mono/stereo topology, guest Local
  Original obligations, storage verdict, and expected source count. One stereo
  row remains one true two-channel file through recovery, Studio, and export.

## Current product line

Current v0.27.2 source builds on the published v0.27.1 line with an accessible,
path-free Record Session Readiness sheet and stable logical-source identity
from the frozen plan through capture, transfer, manifest, recovery, Studio, and
exact repeated-take lanes. Every server, host, guest, and Shared Track source is
bound as exact mono or stereo topology; changed, missing, duplicate, or
ambiguous evidence fails closed. Music and Podcast & Voice automatically stack
only exact same-session take matches. Podcast & Voice proves its 48 kHz
Host-mono + Guest-stereo record/overdub/chapter/save/reopen/stereo-PCM-24-bounce
journey. Review Preview remains read-only and blocks local projects, automatic
lanes, mutation, sidecars, and export.

It retains a provider-neutral handoff for any hardened public HTTPS meeting
link. WebJam never directly or automatically taps a meeting app, browser, or
system output. Local Originals record explicitly selected input devices, so
users must not route meeting or system-output audio into those inputs. Known
services receive friendly labels; generic providers remain neutral and receive
no native-verification claim. Exact Jamulus correlation, bounded guest
observation, and fail-closed take/export evidence remain the authority.

This is a new source and package identity, not a rebuild or replacement of
v0.25.0. Familiar DAW interactions are used for clarity and musical flow
without copying Apple
artwork, exact layouts, assets, or trade dress. Physical audibility, isolation,
alignment, recovery, output, and packaged UX remain **NOT RUN**.

## Why this may matter to Cisco

WebJam is not a Cisco product or an embedded Webex integration today. Its
potential relevance is as a concrete collaboration workflow that connects
real-time media, meeting context, shared state, and creative output while
keeping security and ownership boundaries explicit. A future Cisco-oriented
exploration could investigate an approved Webex Embedded App companion, OAuth,
enterprise policy, and hosted collaboration services—but none of those are
claimed as implemented in this repository.

## Five-minute evaluation

1. Read the [root README](../README.md) and [creator guide](../USER_GUIDE.md).
2. Use [GitHub Latest](https://github.com/rupret007/webjam/releases/latest) for
   the current downloadable candidate and verify its checksum manifest. Use
   immutable [v0.23.0](https://github.com/rupret007/webjam/releases/tag/v0.23.0)
   only when evaluating that historical baseline.
3. Confirm Latest resolves to the unsigned/ad-hoc
   [v0.27.1 release](https://github.com/rupret007/webjam/releases/tag/v0.27.1)
   and use only its exact checksum-verified assets; a branch artifact is not a
   release substitute.
4. Choose a profile first, then exercise Shared Track, the exact-source
   readiness sheet, Record Session, finalization, automatic repeated-take
   lanes, and the profile's permitted Studio path with wired headphones and
   Jamulus. Record this as physical evidence only against the exact package.
5. Open **Conversation** without joining, then test the explicit Join/Open
   action only with an approved sandbox account.
6. In Music or Podcast & Voice, inspect the separate non-destructive local
   project workflow. In Review & Rehearsal, verify that standalone create/open,
   edit/comp/mix mutation, and export remain blocked while completed-session
   take playback stays read-only.
7. Record every physical result against the exact artifact name, build ID, and
   SHA-256; leave anything not physically observed as **NOT RUN**. Publication
   and automated release identity do not turn any physical row into PASS.

## Constraints and next decisions

- No Apple Developer account is currently available, so macOS remains ad-hoc
  signed and unnotarized.
- The v3 Jamulus component catalog is sealed at exact sequence 6 for v0.22.5.
  The v1/v0.22.3 and v2/v0.22.4 channels remain immutable historical evidence.
- The fallback-only v0.26.0 private testing release has complete desktop
  asset/checksum evidence and passed its protected release-environment gate. A
  new exact-target component authorization remains mandatory before managed
  3.12.3 download can be enabled. Nothing may move or replace the sealed
  v0.22.5 catalog, tag, or assets. Immutable v0.25.0 and v0.24.0 release
  evidence remains in their historical ledgers, including the
  [v0.24 checklist](../V024_RECORDING_FIRST_PHYSICAL_TEST_CHECKLIST.md);
  v0.25.0 package observations belong only in the
  [v0.25 checklist](../V025_CREATOR_MULTITRACK_PHYSICAL_TEST_CHECKLIST.md).
  v0.26.0 physical observations belong only in the
  [v0.26 checklist](../V026_CREATOR_MULTITRACK_PHYSICAL_TEST_CHECKLIST.md),
  whose automated release identity is verified while every physical and
  decision row remains **NOT RUN**.
