# WebJam documentation

This index is the front door for WebJam's documentation. Start with the
audience that matches what you are trying to do; the root [README](../README.md)
keeps the product story and five-minute demo intentionally short.

## Start here

| Audience | Read | Outcome |
| --- | --- | --- |
| Evaluator or stakeholder | [Project brief](PROJECT_BRIEF.md) | Understand the product thesis, architecture, evidence, and roadmap |
| Musician | [Musician guide](../USER_GUIDE.md) | Host, join, use Webex, record, and recover a session |
| First-time demo | [First Jam](../FIRST_JAM.md) | Follow the shortest live-rehearsal path |
| Reference Studio user | [Reference Studio musician guide](REFERENCE_STUDIO_MUSICIAN_GUIDE.md) | Write, arrange, record, and bounce a local project |
| Developer | [Development guide](../DEVELOPMENT.md) | Set up the repository, preserve ownership boundaries, and run checks |

## Product and architecture

- [Architecture](../ARCHITECTURE.md) — system boundaries and ownership between
  WebJam, Jamulus, Webex, Reference Studio, and Pocket Stage.
- [Recording and Studio](../RECORDING_AND_STUDIO.md) — local capture, editing,
  export, recovery, and evidence boundaries.
- [Reference Studio decision record](adr/0006-standalone-reference-studio-projects.md)
  — project and migration invariants.
- [Reference Track decision record](adr/0005-reference-track-jamulus-participant.md)
  — host-controlled Jamulus-routed backing audio.
- [Pocket Stage plan](plans/webjam-pocket-stage-v1.md) and [threat model](security/pocket-stage-mobile-threat-model.md)
  — iPhone owner-device preview and its trust model.
- [Webex decision record](adr/0004-webex-external-launch-and-future-oauth.md)
  — external handoff today; OAuth or an embedded companion remains future work.

## Evidence, releases, and operations

- [Changelog](../CHANGELOG.md) — released history plus the clearly separated
  `Unreleased` development line.
- [Test procedure](../TEST_PROCEDURE.md) — automated evidence and the physical /
  credentialed ledger. **NOT RUN** is not a claim of failure; it means evidence
  has not yet been collected against an exact package.
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
