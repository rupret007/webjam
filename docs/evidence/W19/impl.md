# W19 — Block track export if source changes mid-render

Implemented in `/Users/jeffstory/Documents/webjam-24h/W19`, branch
`webjam-24h/W19-block-track-export-if-source-changes-mid`.

Implementation commit: `73be838` (`Block track export when source recordings change during render`).

## What changed

- `core/take_export.py`: schema-v2 aligned-originals exports now open each source
  once, hash that handle in bounded 1 MiB blocks, and render through SoundFile
  virtual I/O on the same handle. Size, declared digest, file identity and
  modification metadata are checked around hashing/open/render. A second digest
  on the same handle checks the completed read. Path and descriptor ctime are
  compared within their respective APIs on Windows, using the existing helpers.
- The evidence manifest uses the digest of the pinned rendered source. A declared
  digest mismatch blocks export. Source identities are checked again while the
  existing take-manifest publication lock is held, immediately before rename.
  Every failure uses existing unpublished-staging cleanup. Previous exports and
  source recordings are not rewritten or deleted by WebJam.
- `webjam_qt/widgets/recording_studio.py`: an exact, fixed error mapping gives
  source-change recovery guidance instead of suggesting disk-space repair.
  Unknown errors retain existing safe-copy behavior.
- `tests/test_take_export.py`: seven injected race cases cover same-shape
  replacement before decoder open, in-place mutation while rendering, mutation
  followed by restoration of bytes/mtime, replacement after render, replacement
  before publication, identical-byte replacement, and removal before publication.
  Tests verify staging cleanup, preservation of an earlier export, and source
  contents reflecting only the injected mutation. Positive receipt/digest and
  source-handle closure tests cover successful export and output failure.
- `tests/test_recording_studio.py`: verifies the actual failure hint, failure
  signal, and restored take/export controls.
- `tests/test_send_to_logic_ui.py`: adds temporary-home isolation for the existing
  default-destination tests. Their assertions and production default-path branch
  remain intact; they no longer write into the developer's Music folder.
- `CHANGELOG.md`, `RECORDING_AND_STUDIO.md`: document the behavior and recovery.
- `docs/evidence/W19/gate.sh`: committed offline gate; copied verbatim to the
  requested campaign `items/W19/gate.sh`. This report is also copied there.

## UX gate

Read campaign UX_GATE.md, W19 backlog details, audit-logic.md and audit-ux.md;
checked launch_dialog.py, creative_modes.py and the complete start-card tests.
Music still presents **Host / Join**. Art still presents **Make together / Paint
along**, followed by **Host / Join**. No doors or controls were added. Preview,
NOT RUN and fail-closed decisions remain intact.

Actual changed in-room hint:

> Track export stopped because a source recording changed. Reopen the take and review it before exporting again.

Maker-tool vocabulary reference consulted before writing copy:
[Audacity's Managing Audacity Projects](https://manual.audacityteam.org/man/audacity_projects.html).
The gate itself runs offline. No physical or new screenshot validation is claimed.

## Tests and exact results

Regression proof before the product fix:

- `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_take_export.py -q -k 'source_changes_mid_render or unchanged_sources_have_verified_receipt' --tb=short`
  — **5 failed, 1 passed, 46 deselected in 0.36s**. All five original race cases
  failed with `DID NOT RAISE TakeExportError`; unchanged export passed.
- `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_recording_studio.py -q -k source_change_gives_reopen --tb=short`
  — **1 failed, 134 deselected in 2.17s**, because Studio showed generic disk-space
  guidance. After the copy fix: **1 passed, 134 deselected in 0.67s**.

Final gate command: `bash docs/evidence/W19/gate.sh` — **exit 0**.
Full output: campaign `items/W19/gate.log`.

The gate runs `.venv/bin/python`, uses offscreen Qt, and runs each complete module
in a fresh interpreter as required by CONTRIBUTING.md / DEVELOPMENT.md. No test
was skipped, deleted, xfailed, or relaxed.

| Check | Final exact result |
| --- | --- |
| Focused export regressions | 10 passed, 46 deselected in 0.26s |
| Focused Studio recovery | 1 passed, 134 deselected in 1.24s |
| Repository Ruff gate | All checks passed |
| Ruff on all three touched test modules | All checks passed |
| Standard compileall over core/webjam_qt/ui/services/api/tests | exit 0 |
| pip check | No broken requirements found |
| ux_smoke_test.py | UX smoke gate passed |
| test_take_export.py | 56 passed in 0.46s |
| test_recording_studio.py | 135 passed in 5.63s |
| test_export_receipt.py | 9 passed in 0.11s |
| test_take_project.py | 18 passed in 0.15s |
| test_take_library.py | 73 passed, 8 subtests passed in 0.83s |
| test_studio_export.py | 49 passed in 0.90s |
| test_studio_renderer.py | 41 passed in 0.99s |
| test_studio_project.py | 11 passed in 0.17s |
| test_studio_retired_callbacks.py | 2 passed in 1.32s |
| test_studio_compact_workspace.py | 19 passed in 5.80s |
| test_send_to_logic_ui.py | 9 passed in 0.62s |
| test_logic_handoff.py | 79 passed in 0.49s |
| test_logic_handoff_adapter.py | 30 passed in 0.18s |
| test_art_start_ux.py | 66 passed in 1.56s |
| git diff --check | exit 0 |

Complete-module total: **597 passed, 8 subtests passed**. The 11 focused checks
are repeated within that total, not additional distinct tests. The gate finished
well within 25 minutes. pip emitted an unwritable-cache warning, then passed.

The first broader gate stopped with **5 failed, 4 passed in 0.45s** in
`test_send_to_logic_ui.py`: sandbox access to the real default Music folder was
denied. `items/W19/gate-first.log` preserves this run. The temporary-home fixture
resolved that test-isolation defect; the entire gate was rerun successfully.
During test development, an invalid small chunk size and a wrong evidence key
were corrected before the reported baseline regression run. A temporary syntax
error during implementation was fixed before the passing runs.

## NOT RUN / limits

- Real audio capture or listening, two-Mac sessions, Webex, live services,
  external-editor/Logic physical import: **NOT RUN**.
- Native Windows execution: **NOT RUN**. Existing modeled Windows path/descriptor
  ctime tests pass; virtual I/O avoids cross-runtime descriptor transfer.
- New screenshots and physical visual review: **NOT RUN**. Existing supplied
  before screenshots are unchanged; recovery copy is covered by offscreen Qt.
- Full unrelated repository suite, builds and packaging: **NOT RUN**. The gate
  covers the touched exporter/Studio areas and their adjacent consumers.
- Legacy exports without a project manifest retain their existing behavior;
  W19's audited hash/open/render race is in the schema-v2 exporter.
- Publication checks use filesystem identity/size/mtime/ctime; no exclusive lock
  over arbitrary external source writers is claimed. Hashing remains bounded in
  memory and adds one post-render source read.

Disk check: approximately **21 GiB free**. No large build outputs were created.
Local commits only; no push, merge, PR, tag, release, publication, or Latest change.
