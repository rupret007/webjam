# Portable Music and Art workspaces

Status: active implementation on draft [#172](https://github.com/rupret007/webjam/pull/172), dependent on draft [#171](https://github.com/rupret007/webjam/pull/171). One WebJam coding lane. Jeff owns merges, tags, releases, Latest and feel. Barker/Wildflower are outside scope. The [goal prompt](WORKSPACE_BACKUP_GOAL.md) is the full milestone; a smaller foundation is not its completion.

## Verified starting point — 2026-10-02

Head `f3ddd58` has metadata-only backup/import, immutable preview, fresh IDs, v2 import provenance and Library actions. It also reconciles clean editor snapshots before backup, retains long UTF-8 titles and carries provenance through Save as copy. Both exact-head [push](https://github.com/rupret007/webjam/actions/runs/37017701341) and [PR](https://github.com/rupret007/webjam/actions/runs/37017706451) runs passed, including source, both real Jamulus versions and all four desktop builds.

That evidence does not cover the full portable-media milestone. Inspection found that import changes Library selection, imported paths are still probed during passive rendering, some import persistence errors escape the UI handler, and Save as copy drops the original source key. The earlier “core only / CI pending” description is stale. Physical acceptance remains NOT RUN.

Local checkpoint `7554980` addresses those starting import-safety findings. Its frozen source passed 261 tests with one Windows-only skip across 11 relevant modules, plus Ruff, compilation and diff checks. Coverage includes separate-process recovery, six journal/publication/acknowledgement failure boundaries, retained conflicting drafts, no passive imported-link access and compact 480×500 layouts at 13/22px. The local receipt is `build/workspace-portability/import-safety-validation.json`; these results do not claim media-package, final-head CI or physical acceptance. Selected-media implementation is in progress on the same lane.

The selected-media core now has local validation: 284 tests passed, one skipped, and eight subtests passed across the media package, metadata backup, import recovery, Session library, take library and take review suites. Ruff and diff checks passed. The receipt is `build/workspace-portability/media-core-validation.json`; it records the working tree based on `7554980`, not final-head CI. Coverage includes source stability, safe archive inventories, durable media recovery, fresh-process crash/resume, storage faults, exact content proofs and take/Art round trips. Library choices, asynchronous progress, prepared Studio activation and the complete recorder-to-restored-workspace journey remain pending.

## Ordered implementation

1. **Import safety.** Retain current selection, search, editor/drafts and runtime owner through import/cancel. Introduce a bounded private prepared-import journal and exact-ID/stored-byte-checksum reconciliation. Retry the same prepared identity only after confirmed absence; ambiguous or conflicting storage blocks duplicate retries. Survive dialog and process restart. Imported take/Art links stay “stored link - not checked” until deliberate verification/open/relink; copy/restart retain the policy. Distinguish newly recorded pending takes from historical imported reservations.
2. **Content identity and selection.** Keep metadata-only as the default. Offer explicit completed-take and local-Art selections with counts, estimated size, excluded/missing sources and supported review/edit data. Add a strict v3 record field for portable media provenance while preserving existing v1/v2 bytes and decoders. Expected content proofs survive copy, metadata-only re-export and restart; never persist a timeless “verified” claim.
3. **Package and recovery.** Use a bounded versioned package with canonical relative names, declared inventories and checksums. Stream stable source reads, copying, hashing and validation with progress/cancel away from the UI thread. Publish to new destinations only. Bind preview to exact metadata/package inventory. Import journaling must distinguish prepared media, complete media and published workspace; recheck both exact record bytes and expected media before retry/cleanup.
4. **Usable restoration.** Restore a new workspace identity with historical provenance and exact take/song/bookmark IDs. Rebind included links and rehearsal bookmark paths to the restored files. Verify/relink deliberately using expected content proofs and existing Studio validation. Supply only declared, verified alternate-take dependencies to Studio; do not scan parents or change the recording destination.
5. **Whole-journey evidence.** Exercise Music and Art through actual controls, separate processes and private folders; then extend the frozen hook and verify the exact final head on all required CI gates.
6. **Delivery.** Update actual-click Help/README/guides and PR description, deliver an exact-source test build with hashes and pilot, and remove only proven obsolete/owned temporary content.

## Media contract

The metadata JSON format stays supported. Optional media packages must contain only the workspace snapshot, explicit asset inventory and selected supported files. User-authored text and stored locations remain literal; no secret-scrubbing promise. Application settings, invitations, credentials, arbitrary directory contents, lock files and unrelated exports are not collected.

A portable content proof is keyed by durable take ID or Art reference ID and records expected relative file names, byte sizes and SHA-256 values. Take proofs also bind exact manifest bytes. Current locators remain ordinary take links and Art reference locators; a deliberate relink may change a location only after validating the expected content. A path or machine-local inode is not portable identity.

Selected takes must have a valid completed v2 manifest and validated declared media. Preserve the exact manifest, relative segment layout and original audio bytes. Legacy/missing/incomplete takes remain metadata links with a clear exclusion reason. Keep supported review/Studio sidecar bytes and report recovery/unsupported state instead of synthesizing a clean edit. Studio cross-take edits require their selected dependencies; absent dependencies must be reported without claiming a complete restored arrangement.

Art copies include explicitly selected local files only; URL references remain inert links. Preserve reference/bookmark IDs and Art brief/progress/next steps. No automatic browser/player/editor handoff occurs on import or resume.

Reject duplicate/colliding member names, traversal, drive/absolute paths, unsafe links or special files, extra entries, incompatible schemas, malformed or oversized metadata/data and checksum mismatches. Validate before constructing destination paths; do not use general archive extraction. Cancellation and storage errors retain existing work. Once publication may have succeeded, retain evidence and reconcile rather than blindly deleting or repeating.

## Completion evidence

| Requirement | Required proof | Current state |
| --- | --- | --- |
| Draft/selection/owner preservation | Actual dialog/coordinator tests: dirty and conflicting drafts, canceled preview, successful import, late recording completion, zero automatic media/runtime actions | Metadata slice passed at `7554980`; asynchronous media flow pending |
| Restart-safe uncertain import | Fault injection before/after journal and record/media publication; fresh-process reconciliation of same ID/hash; conflict refusal | Metadata slice passed at `7554980`; media core checks passed locally; UI recovery pending |
| Passive imported-link policy | Forbidden filesystem/network calls during render/continue/restart/copy, plus deliberate verification/relink routes | Passive metadata-link checks passed at `7554980`; content-proof core checks passed locally; explicit UI routes pending |
| Metadata and selected-media choices | Actual dialogs, estimated/count/exclusion previews, metadata-only default, cancellation/progress, keyboard/compact/enlarged text | Pending |
| v1/v2/v3 compatibility and identity | Independent old-byte fixtures, strict new schema, preserved provenance/IDs and expected content across save/copy/re-export | Core checks passed locally; full UI/restart journey pending |
| Package integrity and failure behavior | Hostile/oversized/archive-race inputs, changed sources, disk-full, cancellation and exclusive publication; unchanged originals/manifests | Core checks passed locally; native platform gates pending |
| Music restoration | Two distinct takes through production controlled recorder/finalizer; selected-media backup/import in another root; fresh-process Continue, explicit A/B audition, independent reviews/edits and verified export | Pending |
| Art restoration | Actual saved project/reference/bookmark; backup/import elsewhere; fresh-process resume and deliberate verification/relink/open; unchanged originals | Pending |
| Frozen and hosted gates | Exact-head full source suite, real Jamulus 3.12.2 and 3.12.3, four native desktop/frozen checks including portable-workspace journey | Starting-head green; final head pending |
| Docs/test-build/pilot | Actual clicks and limits, complete draft PR body, exact source/package hashes and downloadable/local test build, ten-minute Music/Art pilot | Pending |
| Physical acceptance | Two-Mac audibility, reconnect, real-editor import and Jeff's feel recorded against delivered build | NOT RUN; owner pilot prepared at delivery |
| Storage cleanup | Identity-bound obsolete artifacts/owned temporary files only; retain source, current environments, user media and useful evidence | Previous cleanup complete; new cleanup at delivery |

Reused source evidence: `tests/support/controlled_recording_journey.py` executes production recording/finalization with synthetic external RPC/audio. `tests/support/run_saved_work_restart.py` provides separate writer/reopen processes. The packaged session/workflow smoke already provides explicit A/B playback into a memory sink and verified export. Synthetic results are not physical sound or feel observations.

Do not mark the milestone complete while any required software gate or deliverable remains unverified. Preserve failure evidence and investigate actual causes; do not relax deadlines/assertions to obtain green results.
