# PM2 storage audit at 78a24d7

Read-only audit. No source, branch, PR, lease, or external messages changed.

## Proven result

Two independent Studio owners load one real schema-v2 take. The visible owner makes an unsaved pan edit. A second owner saves another pan edit. The visible owner's flush and the current Retry Save action both fail the exact-byte compare-and-swap protection. Original WAV bytes, the external owner's sidecar, and the visible owner's in-memory arrangement all remain unchanged. Close is vetoed.

The UI still says: “WebJam couldn't save your latest Studio choices. Check file access and free space, then choose Retry Save.” Its enabled Retry Save button is not a resolution for this external-document conflict. The footer says only “Studio couldn't save those review choices. The recorded take is safe.” This is an existing known limitation, already recorded by #147 in `docs/evidence/manager-demo-recovery-20260922/README.md:82`, not a newly discovered regression in that PR.

- `conflict-before.json`: structured evidence from the real concurrent-writer probe.
- `conflict-before.png`: 1000×740 rendered fixture of that state.
- `test_storage_audit.py`: reproducible probe using existing isolated controller fixtures.

## Proving commands

From `/Users/jeffstory/Documents/WebJam`:

```sh
env PYTHONDONTWRITEBYTECODE=1 QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -p no:cacheprovider -p tests.conftest /private/tmp/webjam-pm2-audit-storage/test_storage_audit.py -q
```

Result: **1 passed in 1.35s**. The first probe attempt reached and saved all BEFORE evidence, then a probe-only cleanup typo (`discard_unsaved` instead of `discard_dirty`) left its teardown on the normal quit-veto dialog. That isolated process was stopped; cleanup was corrected and the full probe passed. No product change was needed.

```sh
env PYTHONDONTWRITEBYTECODE=1 QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -p no:cacheprovider tests/test_studio_store.py tests/test_studio_controller.py tests/test_studio_retry_api.py tests/test_studio_demo_recovery_actions.py -q
```

Result: **53 passed in 3.31s**. These cover exact-token conflicts, unconfirmed-save retries, stale owner/generation/document actions, originals preservation, retained edits, quit/selection vetoes, and real HUD retry dispatch.

## Boundaries and ranked follow-on

1. External-document conflict resolution is the highest remaining storage slice. Preserve both versions and the current source-audio guarantees; provide a real Save Recovery Copy/explicit reload or equivalent reviewed workflow. A new sentence or closing another app cannot make the old durable token match the external save. Keep this as a separately scoped product change needing Jeff feel.
2. Typed permission/space/unconfirmed-save presentation can narrow each safe next action without showing raw errors. Classify exception types / chained errno at the core boundary, not error-message substrings. Preserve the existing published-but-unconfirmed exact-byte retry behavior.

There is no reusable recovery-copy control in embedded schema-v2 Studio: `recording_studio.py:3219` flushes before Export Tracks; `core/song_studio_clone.py:141` limits Song Studio Save As to schema-3; its controller also demands clean, verified, saved source state (`reference_studio_application.py:4137`). Core reload exists (`studio_controller.py:241`) but requires explicit discard and would lose unsaved edits if directly exposed as the sole resolution. Expanding persistence just for this audit would reopen a known separate slice, so no implementation is recommended in the Notes clarity PR.

Presentation loss occurs at `recording_studio.py:1212` (only `save_failed` crosses the UI facts boundary), `studio_arrangement_workflow.py:1184` (all saves use the same footer), and `session_conductor.py:1132` (all failed/blocked edits receive storage guidance + Retry Save). Current CAS protection is in `studio_store.py:590`; controller retains the conflict state at `studio_controller.py:425`.
