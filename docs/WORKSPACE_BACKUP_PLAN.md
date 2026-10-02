# Workspace backup and restore — next draft

Status: core foundation implemented in a dependent draft, based on verified workflow-continuity draft [#171](https://github.com/rupret007/webjam/pull/171) at `4eae2be`. Local focused checks pass; hosted native checks remain pending. The metadata backup actions described below are not available in the interface yet. Keep this work in one sequential coding lane and keep every PR draft. Jeff owns Latest, feel, tags, releases and merges. Barker/Wildflower are outside scope.

## Assessment

The Session library already persists notes, rehearsal plans, Art projects, take links and history. Export summary provides a readable Markdown handoff; local Music project Save As already clones its own project. The missing capability is an explicit portable backup of a library workspace, with safe preview and a new identity on import. Build on the existing atomic persistence, recovery and identity validation.

The preceding candidate passes both complete source suites, both real Jamulus versions and all four desktop package checks. Its exact arm64 test build is delivered separately as `WebJam-TEST-4eae2be`. The highest-value owner check remains that build's physical Music/Art pilot: heard two-Mac audio, two distinct takes, reconnect, restart, editor import and feel. Those observations remain NOT RUN.

The [detailed goal prompt](WORKSPACE_BACKUP_GOAL.md) is fewer than 4,000 characters. This draft begins its metadata foundation; copied-media packages, interface integration and the complete packaged journey remain subsequent work on this lane.

## First implementation slice

- Use a bounded, versioned JSON backup containing only supported workspace metadata and a canonical payload checksum. No archive extraction, linked-file reads, media copies or network fetches occur. Checksums detect content mismatch; they do not authenticate a sender.
- Keep notes and other user-authored text literal. Reject unsupported structured fields rather than silently dropping them or promising secret scrubbing. A backup can contain private text and local paths; do not collect application settings, invitations, credentials or unrelated files.
- Expose an immutable validated preview. Import consumes those validated bytes, so replacing the input file after preview cannot change the accepted workspace. Preview does not create library records or instantiate an editor.
- Publish export only at a new filename. Preserve existing files, links and original workspace bytes on refusal/failure. Make uncertainty after atomic publication observable, so callers can reconcile a known destination before retry.
- Create a fresh imported workspace ID. Retain original workspace/source identities, revision/timestamps and backup digest as durable bounded provenance. Ordinary records retain their existing v1 representation; imported records need explicit provenance without hiding it in notes or recaps. Provenance remains immutable through ordinary saves.
- Preserve historical take/song/bookmark identities, but detach recording-owner fields from imported links. Import must not duplicate the active `(take_id, recording_session_id)` ownership used by recovery completion. Media, validation state, Studio sidecars and old export destinations are not reverified by metadata import.

Core validation must cover Music/Art round trips in fresh processes, repeat import with distinct IDs, provenance across re-export, immutable previews, original recording ownership, unsupported/malformed/oversized/checksum input, existing-destination preservation and failures before/after publication.

### Core contract

`core.workspace_backup` provides `export_workspace_backup(record, destination)`, `preview_workspace_backup(path)` and `import_workspace_backup(library, preview)`. A `WorkspaceBackupPreview` retains canonical validated bytes, returns independent decoded records and can report prior imports of the same source ID and payload digest. An incomplete library scan reports uncertainty instead of an empty duplicate result.

The JSON envelope has exactly `format`, `version`, `content`, `payload` and `payload_sha256`; format is `webjam.workspace-backup`, envelope version is `1`, and content is `metadata-only`. Its limit is the existing 8 MiB record limit plus 1 KiB. The checksum covers canonical UTF-8 payload bytes, not the envelope's whitespace. This is a metadata document, not an archive.

Existing records keep the v1 codec. Imported records use v2 with at most 16 flat `import_provenance` entries: source workspace ID, profile, revision, created/updated timestamps, original source key and payload digest. Imported take links move recording session/run IDs, validation and status into bounded `historical_origins`. Paths and historical take/song/bookmark identities remain literal. `SessionLibraryImportUnconfirmed` exposes the intended workspace ID and exact stored-byte SHA-256 after uncertain publication; reconcile that identity before an explicit retry. No automatic retry creates another record.

### Foundation validation

Local checks on 2026-10-02: **244 passed, one Windows-only skip, zero failures** across nine modules. These include 89 core backup cases, the real Library coordinator's pending/completion ownership case, 126 existing library/coordinator/dialog/launch/Art/rehearsal cases and 28 desktop workflow contracts. The new core and coordinator tests run with resource warnings treated as errors. Ruff, compilation and diff checks pass; the core imports without site packages. The coordinator case does not claim the full recorder validation pipeline or physical audio.

The desktop matrix now runs the pure core backup module on all four targets before packaging, including native Windows exclusive staging, retained-handle cleanup and transfer-failure checks. Those hosted results remain pending for this draft. Local POSIX results do not verify the Windows native API path. The full backup/restore feature and packaged user journey remain incomplete.

## Interface integration before this becomes a user feature

Proposed actions are **Back up workspace…** and **Import backup…**, while **Export summary…** remains available. Preview should show title/profile, saved date, content counts, source/duplicate information and **Metadata only; media files are not included**, with explicit **Import as new workspace** and **Cancel**. Import leaves the current workspace/runtime owner unchanged; Continue remains a separate choice.

The audit found these existing boundaries to handle before adding those actions:

1. `SessionLibraryDialog.save_current()` returns early for a clean editor. A backup snapshot hook must capture current Notes and reconcile the base/editor/latest workspace even when clean, including late take/recap facts. Competing edits remain with their owners and block export rather than backing up a stale snapshot.
2. Import must not emit `copy_saved`, acknowledge another recovery draft, set `selected_record`, accept the Library, start Studio, or connect/play/record. Existing selection and Continue save/conflict gates remain authoritative.
3. Take rendering currently uses `Path.is_dir()`; Art rendering and reference selection use `Path.is_file()`. Imported paths, including network paths, must remain **stored links — not checked** until deliberate verification/open/relink. The policy must survive restart and copying. Art normalization drops unknown reference fields, so a temporary flag there is insufficient.
4. Studio checks take/source identity only when evidence is provided. Incomplete imported references must not open a substitute or look verified. Pending imported take links also must not look like a currently finalizing recording.
5. The core permits titles of 512 UTF-8 bytes; the editor currently limits titles to 200 characters. Valid imported titles must not be silently truncated when loaded or saved.
6. The existing Save as copy route passes an explicit field list that omits import provenance. Preserve its historical context and reference-verification policy before enabling imported workspaces in the interface.

Reuse the existing Library/coordinator/Art/launch fixtures for canceled preview, dirty/conflicting draft retention, no automatic signals or media access, missing/changed sources, restart/copy behavior, compact windows and enlarged-text keyboard access. Extend the packaged workflow hook only after these actual user routes exist.

## Completion of the larger round

After the interface and any separately implemented optional media-copy slice are ready, update Help and actual-click guides, verify the full exact-head source suite, both real Jamulus versions and all four frozen desktop packages, and deliver another identified test build and pilot. Preserve failed evidence and physical NOT RUN rows. Do not label this foundation or a pending CI run as the complete backup/restore milestone.
