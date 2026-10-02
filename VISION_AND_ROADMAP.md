# WebJam vision and roadmap — v0.29.0 baseline and Unreleased work

## Product direction

WebJam helps creators start together, keep their work, and return to it without
repeating setup. Music keeps **Host or Join, set up sound in Jamulus, and play**
as the quick path. Art supports people using their own tools; a shared canvas
or Paint along lesson is optional.

WebJam owns session coordination, local workspaces, recording evidence,
Studio review and export. Jamulus owns live music and audio configuration.
Conversation/video stays an explicit external handoff. Studio preserves
original media and does not claim integration with a particular editor.

## Shipped v0.29.0 baseline

The published release and its package checksums are recorded on
[GitHub](https://github.com/rupret007/webjam/releases/tag/v0.29.0).
The current baseline includes:

- Music and Art launch choices, profile-specific Host/Join, and standalone
  Music or Podcast & Voice projects without a live room.
- A local Session library for notes, summaries and verified take links;
  continuing saved work does not automatically connect, record or share.
- Music rehearsal plans with separate song drafts, progress and moment notes;
  a take bookmark needs confirmed recording identity and position.
- Studio favorites, review notes and A/B audition without changing edits,
  plus receipts that bind successful exports to their sources and settings.
- Art projects with briefs, progress, references and manually entered lesson
  bookmarks; opening a reference or relinking a moved file is deliberate.
- The existing non-destructive Arrange/comp workspace, bounded recovery and
  source-verified playback/export. Review & Rehearsal remains Preview with
  its narrower read-only take workflow.

These are software capabilities, not proof of physical audibility, hardware
recovery, external-editor import, or Jeff's feel/Final Build judgment.

## Current milestone: workflow continuity — Unreleased

This work is a PR and test-build milestone after v0.29.0. The source version
stays v0.29.0; identify new evidence by commit, package and hash. No release or
tag is requested.

1. **Return safely.** File → Return to launch works from live and local Studio
   workspaces after explicit End/Leave where required, saved work, recording
   finalization and owned cleanup. A failed prerequisite retains the current
   workspace. A fresh launch must not replay an invitation or start audio.
2. **Find the next action.** Modeless, searchable local Help explains saved
   work, rehearsal, review/export and recovery. Reading is passive; explicit
   buttons navigate existing workspaces without taking over their actions.
3. **Keep failure evidence.** Atomic-save descriptor ownership and malformed
   recent-path handling protect saved bytes and preserve recovery. Bounded
   Jamulus CI supervision records the first failure and cleanup result before
   the job deadline, without treating a retry as a diagnosis.

No new audio engine, cloud account, model provider, paid API, mobile expansion,
signing or notarization project is part of this milestone.

## Next evidence: one identified build, real creator workflows

Use the [workflow continuity pilot](docs/WORKFLOW_CONTINUITY_PILOT.md), whose
physical rows begin **NOT RUN**. It records the exact package and hash before
two-Mac audio, two distinct takes, reconnect, restart, review/export and real
editor import. The ten-minute Music and Art routes check discoverability;
they do not replace a longer physical rehearsal.

Automated source tests, packaged synthetic checks and physical observations
remain separate. Jeff retains usefulness, feel and Final Build judgment.
The older v0.18.0 roadmap's physical and credentialed gates were **NOT RUN**;
the historical checklists retain their own versions and results. This new
pilot does not retroactively pass them.
