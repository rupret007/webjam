# WebJam documentation

This index is the front door for WebJam's documentation. Start with the
audience that matches what you are trying to do; the root [README](../README.md)
keeps the product story and five-minute demo intentionally short.

> **Source identity:** v0.29.0 unsigned testing candidate.
> **Current download and publication status:** [GitHub Latest](https://github.com/rupret007/webjam/releases/latest)
> identifies the published tag, packages, and checksum manifest.
> **Historical checkpoint, checked 2026-09-29 before the v0.29.0 release round:**
> [v0.28.4](https://github.com/rupret007/webjam/releases/tag/v0.28.4), immutable
> release `396603181`, published `2026-09-25T12:57:13Z`. Annotated tag object
> `dc494f0450ccc6f690ba7c9768ff4b575fe2da6c` peels to
> `ce52e9c9302cb3f28510a3e3b0e9edfff8b31111`. Its seven packages plus
> `WebJam-v0.28.4-SHA256SUMS.txt` are the download evidence. Windows is unsigned;
> macOS is ad-hoc signed and unnotarized. Physical, signing, and platform-trust
> gates remain **NOT RUN**. A source checkout is not a published package.

## Start here

| Audience | Read | Outcome |
| --- | --- | --- |
| Manager demo | [Show one-pager](../SHOW_ONEPAGER.md) and [demo script](../DEMO_SCRIPT.md) | Run the prepared four-part arc in under ten minutes; record the rehearsal result |
| Evaluator or stakeholder | [Project brief](PROJECT_BRIEF.md) | Understand the product thesis, architecture, evidence, and roadmap |
| Showing someone the door | [Two-minute demo script](../DEMO.md) | Show the Art/Music first screen without running a live session |
| New creator | [Simple-language guide](../README_SIMPLE.md) | Understand WebJam in plain words before anything technical |
| Creator | [Creator guide](../USER_GUIDE.md) | Choose a profile, host/join, record, and follow that profile's Studio boundary |
| First-time demo | [First Session](../FIRST_JAM.md) | Follow the shortest profile-first live-session path |
| Follow-along pilot | [First test](FOLLOW_ALONG_FIRST_TEST.md) and [pilot record](FOLLOW_ALONG_PILOT_RECORD.md) | Two-person same-LAN Paint along / Play along; physical rows stay **NOT RUN** until observed |
| Reference Studio user | [Reference Studio guide](REFERENCE_STUDIO_MUSICIAN_GUIDE.md) | Write, arrange, record, and bounce a local project |
| Developer | [Development guide](../DEVELOPMENT.md) | Set up the repository, preserve ownership boundaries, and run checks |

## Keep working between sessions

v0.29.0 adds **File → Session library…** before a room and
**More → Session library…** inside it. Save local notes, reuse a Music
**Rehearsal plan**, or keep an **Art project** with references and next steps.
**Continue** restores its context without starting audio or a room.
Music moments are plain notes unless a recording position is confirmed.
See [the saved-work walkthrough](../USER_GUIDE.md#save-a-session-and-continue-later).
Unreleased builds add **Back up…** / **Import backup…**; see
[the backup walkthrough](../USER_GUIDE.md#back-up-and-import-a-workspace-unreleased)
and [follow-along first test](FOLLOW_ALONG_FIRST_TEST.md).

## Product and architecture

- [Architecture](../ARCHITECTURE.md) — system boundaries and ownership between
  WebJam, Jamulus, provider-neutral meeting handoff, Reference Studio, and Pocket Stage.
- [Recording and Studio](../RECORDING_AND_STUDIO.md) — Record Session, Shared
  Track source identity, Local Originals, editing, export, recovery, and
  evidence boundaries.
- [Creator profile contract](../CREATIVE_MODES_MVP_SPEC.md) — Music and Podcast
  & Voice GA behavior plus the exact Art and Review & Rehearsal Preview
  boundaries, including Paint along.
- [Reference Studio decision record](adr/0006-standalone-reference-studio-projects.md)
  — project and migration invariants.
- [Reference Track decision record](adr/0005-reference-track-jamulus-participant.md)
  — host-controlled Jamulus-routed backing audio.
- [Mobile Art companion](MOBILE.md) — Phase 1 native iPhone/iPad same-LAN guest
  Join. Source and unsigned simulator evidence; physical/signing **NOT RUN**.
  Separate from Pocket Stage.
- [Pocket Stage plan](plans/webjam-pocket-stage-v1.md) and [threat model](security/pocket-stage-mobile-threat-model.md)
  — iPhone owner-device preview and its trust model.
- [Webex decision record](adr/0004-webex-external-launch-and-future-oauth.md)
  — external handoff today; OAuth or an embedded companion remains future work.
- [Conversation companion guidance](../WEBEX_AUDIO_MODES.md) — the canonical
  description of provider-neutral meeting handoff plus the separate Webex-only
  native controls and their claim boundary.
- [Quick help map](../QUICK_HELP_MAP.md) and
  [help routing map](../HELP_ROUTING_MAP.md) — need→action and
  musician-question→answer tables for support conversations.
- [Session help preview](SESSION_HELP_PREVIEW.md) — development-gated temporary
  troubleshooting text after secure peer proof, separate from Jamulus chat and
  saved notes; source evidence and unperformed physical gates.
- [Workspace backup draft plan](WORKSPACE_BACKUP_PLAN.md) and
  [under-4,000-character goal prompt](WORKSPACE_BACKUP_GOAL.md) — the
  portability milestone after workflow continuity. Metadata and
  selected-media backup, explicit verification/relink and local restart tests exist;
  see the draft PR for exact-build evidence and the
  [portability pilot](WORKSPACE_PORTABILITY_PILOT.md) for owner observations.

- [Windows portability diagnostics](WINDOWS_PORTABILITY_DIAGNOSTICS.md) and
  [next goal prompt](WINDOWS_PORTABILITY_RELIABILITY_GOAL.md) — independent native
  crash controls, exact package/source identity and one bounded workflow matrix.
  Historical Windows crash causes and physical observations remain unproven.

## Evidence, releases, and operations

- [Changelog](../CHANGELOG.md) — released history plus the clearly separated
  `Unreleased` development line.
- [Test procedure](../TEST_PROCEDURE.md) — automated evidence and the physical /
  credentialed ledger. **NOT RUN** is not a claim of failure; it means evidence
  has not yet been collected against an exact package.
- [Dual-musician and exact multitrack proof lab](../DUAL_MUSICIAN_REHEARSAL_LAB.md)
  — deterministic host/guest, ARM/ACK, mono/stereo, repeat-lane, export, and
  20-process source evidence with explicit hardware/Jamulus limitations.
- [v0.26 creator-multitrack physical checklist — release identity verified; physical rows NOT RUN](../V026_CREATOR_MULTITRACK_PHYSICAL_TEST_CHECKLIST.md)
- [v0.25 creator-multitrack physical checklist](../V025_CREATOR_MULTITRACK_PHYSICAL_TEST_CHECKLIST.md)
- [v0.24 recording-first physical checklist](../V024_RECORDING_FIRST_PHYSICAL_TEST_CHECKLIST.md)
- [Historical v0.23 Shared Track and recording checklist](../V023_SHARED_TRACK_RECORDING_PHYSICAL_TEST_CHECKLIST.md)
  — exact multi-machine, macOS/BlackHole, Linux/JACK, hardware, recording,
  Studio, accessibility, and recovery observations. Every row begins
  **NOT RUN**.
- [v0.22.5 demo readiness](../WEBJAM_V0225_DEMO_READINESS.md) — the exact
  two-musician Reference Track/Webex scorecard in musician order.
- [Merge and release map](MERGE_AND_RELEASE.md) — historical landing and release
  rules; use the changelog and the [post-#50 handoff](POST_50_HANDOFF.md) for
  current `master` state and gates that remain **NOT RUN**.
- [Post-#50 handoff](POST_50_HANDOFF.md) — exact merged commit, hosted build
  evidence, completed Paint along behavior, and the remaining human/release
  boundaries for the next agent.
- [Desktop release runbook](DESKTOP_RELEASE_RUNBOOK.md) — draft-first,
  checksum-bound, immutable release process.
- [Jamulus component catalog runbook](JAMULUS_COMPONENT_RELEASE_RUNBOOK.md) —
  signed, expiring component authorization and its versioned-channel boundary.
- [Webex sandbox gate](plans/webjam-webex-sandbox-demo-gate.md) — external
  meeting behavior and privacy-safe evidence capture.

## Project participation

- [Contributing](../CONTRIBUTING.md) — focused changes, evidence, and release
  boundaries.
- [Security policy](../SECURITY.md) — private vulnerability reporting and
  safe evidence handling.
- [Support](../SUPPORT.md) — musician troubleshooting and issue routing.
- [Code of Conduct](../CODE_OF_CONDUCT.md) — collaboration expectations.

## Historical release evidence

The superseded v0.28.1 download record is release `393030220`, published
`2026-09-21T14:30:22Z`: annotated tag object
`db44247a3ceefb97f4cac6e623deef1bd32648a8` peels to
`200cac9eb04d01611696cdc147957b36daef257f`. Its seven packages and
`WebJam-v0.28.1-SHA256SUMS.txt` remain evidence for that historical release.
Older release entries (v0.28.0, v0.27.2, Jamulus catalog v1–v3) were deleted by
owner; git tags remain. Those records do not verify the current show candidate.
Physical observations remain **NOT RUN** until recorded against exact bytes.

## Documentation rules

1. State whether a behavior is implemented, planned, automated-only, physical,
   credentialed, or **NOT RUN**.
2. Distinguish the immutable published release from `master`'s `Unreleased`
   work. Never describe a source checkout as a downloadable release.
3. Keep one canonical document per subject and link to it instead of copying
   competing instructions into several guides.
4. Never put passwords, meeting links, tokens, private paths, raw exceptions, or
   private release-key material in documentation or evidence.
5. Update the audience-facing guide and the relevant decision/runbook together;
   add a focused test when documentation asserts a release or privacy contract.
