# WebJam documentation

This index is the front door for WebJam's documentation. Start with the
audience that matches what you are trying to do; the root [README](../README.md)
keeps the product story and five-minute demo intentionally short.

> **Current testing release:** immutable GitHub **Latest** remains v0.26.0
> until the verified publisher runs after this unpublished v0.27.0 source
> lands. Use only an exact release asset verified by
> `WebJam-v0.26.0-SHA256SUMS.txt`. This checkout is not that download. Windows
> is unsigned; macOS is ad-hoc signed and unnotarized; every physical test
> remains **NOT RUN** until recorded against those packages.

> **Current source:** v0.27.0 is an unpublished creator-profile and
> authoritative multitrack candidate. Do not use a checkout or branch artifact
> as a release. All v0.27.0 physical/hardware rows remain **NOT RUN**. GitHub
> **Latest** remains immutable v0.26.0.

## Start here

| Audience | Read | Outcome |
| --- | --- | --- |
| Evaluator or stakeholder | [Project brief](PROJECT_BRIEF.md) | Understand the product thesis, architecture, evidence, and roadmap |
| New creator | [Simple-language guide](../README_SIMPLE.md) | Understand WebJam in plain words before anything technical |
| Creator | [Creator guide](../USER_GUIDE.md) | Choose a profile, host/join, record, and follow that profile's Studio boundary |
| First-time demo | [First Session](../FIRST_JAM.md) | Follow the shortest profile-first live-session path |
| Reference Studio user | [Reference Studio guide](REFERENCE_STUDIO_MUSICIAN_GUIDE.md) | Write, arrange, record, and bounce a local project |
| Developer | [Development guide](../DEVELOPMENT.md) | Set up the repository, preserve ownership boundaries, and run checks |

## Product and architecture

- [Architecture](../ARCHITECTURE.md) — system boundaries and ownership between
  WebJam, Jamulus, provider-neutral meeting handoff, Reference Studio, and Pocket Stage.
- [Recording and Studio](../RECORDING_AND_STUDIO.md) — Record Session, Shared
  Track source identity, Local Originals, editing, export, recovery, and
  evidence boundaries.
- [Creator profile contract](../CREATIVE_MODES_MVP_SPEC.md) — Music and Podcast
  & Voice GA behavior plus the exact Review & Rehearsal Preview boundary.
- [Reference Studio decision record](adr/0006-standalone-reference-studio-projects.md)
  — project and migration invariants.
- [Reference Track decision record](adr/0005-reference-track-jamulus-participant.md)
  — host-controlled Jamulus-routed backing audio.
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
- [Merge and release map](MERGE_AND_RELEASE.md) — Art #19 is on `master`; #17 is the remaining product land, plus the required CI round and the gates that stay **NOT RUN**.
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
