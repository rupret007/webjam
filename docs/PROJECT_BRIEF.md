# WebJam project brief

**Review date:** 2026-08-04
**Status:** Active development; v0.22.4 is the immutable GitHub Latest private
test candidate. `master` is the current test line.

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
| Recording | Capture and finalize a session with durable identity | WebJam recorder + Jamulus evidence |
| Reference Studio | Local writing, arrangement, take lanes, overdub, and bounce | WebJam Studio backend |
| Reference Track | Host-controlled backing audio routed as a separate Jamulus participant | WebJam lifecycle + Jamulus |
| Pocket Stage | Owner's iPhone as a focused second screen and remote | Desktop remains authoritative |

## Architecture in one view

```text
Musician
   │
   ▼
WebJam desktop conductor ── session / recording / Studio / privacy-safe guidance
   │                 │
   │                 ├── Jamulus client/server ── live music, devices, mix, jitter
   │                 ├── Webex app/browser ───── conversation and video
   │                 └── Pocket Stage iPhone ─── owner-device control preview
   │
   └── Reference Studio / Reference Track remain separate, bounded workflows
```

The architecture is intentionally process- and evidence-oriented: external
apps remain externally owned, destructive shortcuts are rejected, and support
projections allow only bounded facts. See [Architecture](../ARCHITECTURE.md) for
the full contract.

## Verified status

- The published v0.22.4 release contains Windows x64, Ubuntu 22.04 x64, Intel
  Mac, and Apple-silicon Mac packages plus an exact checksum manifest.
- `cryptography` 50.0.0 remediates the three audited runtime CVEs; the Intel
  Mac source-build exception is explicit, hash-locked, and separately verified.
- CI covers Python, transport, reference service, Pocket Stage compilation,
  real Jamulus inputs, package assembly, frozen-package checks, and release
  promotion.
- Physical two-Mac audibility, hardware recovery, Webex joining, Pocket Stage
  owner-device pairing, signing/notarization, and long-session evidence remain
  **NOT RUN**. Automated green does not mean those gates passed.

## Current development line

The current `master` line adds DAW-style multi-region editing and first-class
loop Overdub in Reference Studio. The changes are covered by machine tests but
are part of the immutable v0.22.4 test download. Future changes should be
treated as candidates for a new versioned release after product review and
physical validation, not as silent patches to the published artifact.

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
2. Download the exact [v0.22.4 Latest release](https://github.com/rupret007/webjam/releases/tag/v0.22.4)
   and verify its checksum manifest.
3. Run one Host/Join rehearsal with wired headphones and Jamulus.
4. Open Webex Controls without joining, then test the explicit Join/Open action
   only with an approved sandbox account.
5. Open Reference Studio and inspect the non-destructive arrangement workflow.
6. Record every physical result against the exact artifact name, build ID, and
   SHA-256; leave anything not physically observed as **NOT RUN**.

## Constraints and next decisions

- No Apple Developer account is currently available, so macOS remains ad-hoc
  signed and unnotarized.
- The v2 Jamulus component catalog is sealed for v0.22.4; a future renewal
  needs a new, versioned channel boundary rather than replacing sequence 5 in
  place. The v1/v0.22.3 channel remains historical.
- A v0.23 release cut should first decide the DAW feature scope, complete the
  physical Studio/Reference Track gates, choose the Webex integration boundary,
  and establish protected-branch and release-environment ownership.
