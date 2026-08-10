# WebJam project brief

**Review date:** 2026-08-10
**Status:** v0.23.0 is the immutable GitHub Latest Shared Track and native
multitrack private test candidate; its physical-musician gates remain **NOT
RUN**. v0.22.5 remains immutable historical evidence.

## Executive summary

WebJam is a musician-first desktop conductor for collaborative rehearsal and
creative review. It coordinates a private session, invitation, recording, and
recovery experience around Jamulus's low-latency audio path; optionally exposes
Webex for conversation/video; and provides a standalone Reference Studio for
writing, arranging, overdubbing, and exporting ideas.

The product thesis is simple: musicians should have one clear place to conduct
the session while each specialist system keeps the responsibility it is good at.
WebJam does not replace Jamulus's audio engine, Webex's meeting state, or the
operating system's security decisions.

## The user problem

Remote music tools are powerful but fragmented. A band must coordinate an audio
client, a meeting application, recording state, invitations, recovery, and
creative follow-up while trying to stay in the musical moment. WebJam turns
those transitions into a shared, inspectable workflow with explicit next steps
and truthful failure states.

## Product surfaces

| Surface | Purpose | Owner of the critical truth |
| --- | --- | --- |
| Host / Join | Start or enter a private rehearsal | WebJam session conductor + Jamulus |
| Conversation | Optional meeting handoff and native Webex focus guidance | Webex |
| Record Session | Capture and finalize a session with durable identity | WebJam recorder + Jamulus evidence |
| Reference Studio | Local writing, arrangement, take lanes, overdub, and bounce | WebJam Studio backend |
| Shared Track | Host-controlled backing audio routed as a separate Jamulus participant | WebJam lifecycle + Jamulus |
| Pocket Stage | Owner's iPhone as a focused second screen and remote | Desktop remains authoritative |

## Architecture in one view

```text
Musician
   │
   ▼
WebJam desktop conductor ── session / Shared Track / recording / Studio / guidance
   │                 │
   │                 ├── Jamulus client/server ── live music, devices, mix, jitter
   │                 ├── Webex app/browser ───── conversation and video
   │                 └── Pocket Stage iPhone ─── owner-device control preview
   │
   └── Reference Studio stays separate from live Shared Track audio ownership
```

The architecture is intentionally process- and evidence-oriented: external
apps remain externally owned, destructive shortcuts are rejected, and support
projections allow only bounded facts. See [Architecture](../ARCHITECTURE.md) for
the full contract.

## Verified status

- The published v0.22.5 release contains Windows x64, Ubuntu 22.04 x64, Intel
  Mac, and Apple-silicon Mac packages plus an exact checksum manifest.
- `cryptography` 50.0.0 remediates the three audited runtime CVEs; the Intel
  Mac source-build exception is explicit, hash-locked, and separately verified.
- CI covers Python, transport, reference service, Pocket Stage compilation,
  real Jamulus inputs, package assembly, frozen-package checks, and release
  promotion.
- Physical two-Mac audibility, hardware recovery, Webex joining, Pocket Stage
  owner-device pairing, signing/notarization, and long-session evidence remain
  **NOT RUN**. Automated green does not mean those gates passed.
- v0.23.0 publication requires its exact tag, package inventory, checksum
  manifest, fallback proof, and protected promotion. The sealed v0.22.5 catalog
  is explicitly rejected for v0.23.0 and is not evidence for the changed line.

## Current product line

v0.23.0 turns the established Reference Track route engine into one canonical
musician-facing **Shared Track** experience on the live surface: add/drop,
path-free identity, progressive waveform, count-in, transport, loop, route and
dropout truth, stopped-only replacement/removal, and bounded guest observation
without guest authority. It promotes **Record Session** through explicit
preparing/count-in/recording/stopping/finalizing/ready states, retains exact
musician correlation and optional Local Originals, and carries the distinct
Shared Track source into the existing non-destructive Studio.

This is a new versioned source identity, not a patch to v0.22.5. Familiar DAW
interactions are used for clarity and musical flow without copying Apple
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

1. Read the [root README](../README.md) and [musician guide](../USER_GUIDE.md).
2. Use [GitHub Latest](https://github.com/rupret007/webjam/releases/latest) for
   the current downloadable candidate and verify its checksum manifest. Use
   immutable [v0.22.5](https://github.com/rupret007/webjam/releases/tag/v0.22.5)
   only when evaluating that historical baseline.
3. If no v0.23.0 release exists yet, build it from one recorded source commit
   for evaluation without representing that branch build as published.
4. Run one Host/Join rehearsal with wired headphones and Jamulus, then exercise
   Shared Track, Record Session, finalization, and Studio.
5. Open Webex Controls without joining, then test the explicit Join/Open action
   only with an approved sandbox account.
6. Open Reference Studio and inspect the separate non-destructive project workflow.
7. Record every physical result against the exact artifact name, build ID, and
   SHA-256; leave anything not physically observed as **NOT RUN**.

## Constraints and next decisions

- No Apple Developer account is currently available, so macOS remains ad-hoc
  signed and unnotarized.
- The v3 Jamulus component catalog is sealed at exact sequence 6 for v0.22.5.
  The v1/v0.22.3 and v2/v0.22.4 channels remain immutable historical evidence.
- A fallback-only v0.23.0 private testing release requires complete desktop
  asset/checksum evidence and protected release-environment ownership. A new
  exact-target component authorization remains mandatory before managed 3.12.3
  download can be enabled. Nothing may move or replace the sealed v0.22.5
  catalog, tag, or assets; physical results stay in the
  [v0.23 checklist](../V023_SHARED_TRACK_RECORDING_PHYSICAL_TEST_CHECKLIST.md).
