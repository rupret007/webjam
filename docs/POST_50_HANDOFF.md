# WebJam post-#50 handoff

**Recorded:** 2026-08-27  
**Authoritative branch:** `master`  
**Merged commit:** `c2718640f6e0ced38b46c60e75916ec0bdc61f12`  
**Pull request:** [#50 — Art: make Paint along the video workspace](https://github.com/rupret007/webjam/pull/50)  
**Post-merge CI:** [run 33105770642](https://github.com/rupret007/webjam/actions/runs/33105770642) — success

## Completed

- Paint along is a large video-first surface embedded in WebJam's existing
  top-level window. It no longer behaves like a small settings dialog or adds
  a third window beside WebJam and an optional meeting app.
- A host who chose Paint along reaches that surface once the authenticated room
  exists. A guest reaches it when authenticated room truth says the host shared
  a video. The room's persistent Paint along line is the quiet return path.
- The surface presents one primary action for the current state. Host:
  **Share…**, then **Play** or **Pause**. Guest: **Open my copy…**, then
  **Hide video** or **Show video**. Secondary utilities live under **More**.
- **Back to room** and Escape hide the surface without ending the WebJam room,
  withdrawing the file, or closing an optional meeting app.
- The local player is forced silent. Same-file proof remains session-scoped;
  mismatches, changed/unreadable files, stale host position, and loss of
  authority still fail closed.
- The reviewed branch tree and merged `master` tree are identical. The
  post-merge workflow passed Python, transport, reference-service, Pocket
  Stage, real Jamulus 3.12.2/3.12.3 integration, update-input checks, and all
  four desktop package jobs: Linux x64, Windows x64, macOS arm64, and macOS
  x64.

## Boundaries still open

- The successful #50 workflow artifacts are CI evidence, not release evidence.
  GitHub Latest is the earlier unsigned/ad-hoc v0.27.1 release; #50 did not
  create or alter it, and #50 is not included in those tagged packages.
- Windows signing, Apple notarization, the manual one-hour certification, and
  HEADLESS evidence remain unclaimed/skipped as their workflows require.
- Two-computer Paint along behavior and real meeting coexistence remain
  **NOT RUN** until observed on exact physical machines and recorded against
  exact package bytes. Automated green must not be rewritten as physical PASS.
- #37 and #49 were not modified or restacked as part of #50.

## Continuation rule

Do not rebuild or reimplement #50. Start new WebJam work from current
`origin/master`, check this handoff and the changelog first, and keep any
physical, signing, tagging, release, credentialed, or live-production step
behind explicit owner approval.
