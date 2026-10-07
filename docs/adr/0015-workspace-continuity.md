# ADR 0015: Retire workspaces before returning to launch

- Status: Accepted for the Unreleased workflow-continuity candidate
- Date: 2026-10-02
- Related: [ADR 0002](0002-unified-musician-guidance.md),
  [ADR 0006](0006-standalone-reference-studio-projects.md)

## Context

The v0.29.0 live and local-project routes capture different ownership and
profile assumptions at construction. Moving between them previously required
quitting WebJam. Merely changing an offline flag would retain callbacks,
invitations, services and Studio owners from the old route.

## Decision

`WorkspaceNavigator` retains one QApplication and routes application-wide
events to exactly one current launch dialog or workspace. **File → Return to
launch…** is explicit navigation. A live room must finish its existing End/Leave
flow first; the navigator does not authorize an implicit disconnect or new
session. A completed transition creates a fresh window and controller using
the profile chosen at launch and the latest saved settings.

`ApplicationController.prepare_return_to_launch` reads existing owners. Live
recording/finalization, staged-take recovery, End/Leave cleanup, active peers,
startup ownership and take export can veto departure. Reference Studio checks
its recording, Save As, bounce result, recovery and dirty-project state. A
failed save or canceled choice retains the current workspace. The final
shutdown remains the authority for proving all owned services have stopped.

The next launch view is allocated before terminal cleanup. If cleanup fails,
the old window remains available with a retry action and no replacement
controller. Constructor aborts release allocated owners without persisting
uninitialized Notes/title defaults. An unproved constructor cleanup remains
visible and retryable. Playback and worker retirement have distinct started
and completed states; a failed close cannot become a successful retry simply
because teardown began.

## Event and data boundaries

- One set of application-wide invitation slots selects the current destination.
  Queued delivery is generation-bound, and retired bootstrap callbacks do
  nothing. Offline invitations remain declined; returning clears them and
  requires a new explicit invitation action.
- Changing views never starts playback, recording, a reference, an external
  application or a connection. Selecting Host/Join at launch retains its
  existing explicit startup authorization.
- Jamulus owns live audio. Reference Studio owns local playback/recording.
  A destination cannot overlap a previous audio owner.
- Library continuation, take review and portable local projects retain their
  distinct identities and non-destructive persistence. Recreating a controller
  does not migrate, reinterpret or overwrite saved evidence.
- Staged-recording recovery remains owned until its worker and queued result
  have completed. A retired callback cannot update a replacement workspace.

## Help

Modeless, searchable Help uses packaged profile-specific text. Selecting a
topic is read-only. Its finite navigation actions recheck profile, mode and
cleanup state before reaching an existing control. Help does not invent a
second status authority, select a take, execute an export or start playback.
The existing conductor and recording/export owners remain authoritative.

## Verification boundary

Automated checks cover repeated round trips, canceled/failed saves, pending
recording and exports, failed cleanup, constructor failure, stale delivery and
fresh-process saved-work identity. Frozen-package checks exercise the same
navigation and Help with isolated temporary data and controlled external
audio boundaries. Physical audibility, device routing, subjective usability
and real editor import remain observations in the
[exact-build pilot](../WORKFLOW_CONTINUITY_PILOT.md).
