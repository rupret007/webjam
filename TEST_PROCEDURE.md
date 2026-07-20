# WebJam v0.17.0 source and physical test procedure

> The source tree reports v0.17.0. No v0.17.0 package has been promoted. The
> v0.16.3 package record below remains immutable rollback/reference evidence;
> it does not certify the new Studio implementation.

## Scope

This procedure distinguishes automated source/package evidence from physical
musician evidence. A passing source suite does not certify two-Mac audibility,
hardware changes, sleep/wake, interruption recovery, or external-editor import.

## Source gate

Run from the repository root:

```bash
.venv/bin/ruff check webjam_qt/ core/ ui/ services/ api/
.venv/bin/python -m compileall -q core services webjam_qt
.venv/bin/pip check
git diff --check
QT_QPA_PLATFORM=offscreen .venv/bin/pytest -q
```

Review at minimum:

- Host starts a private server before launching the client.
- Guest launches native Jamulus from one parsed invite.
- The normal launch has no WebJam input/output form or Band Check gate.
- Jamulus receives filename-only `--inifile WebJam-native-v0.16.ini`, no
  `--nogui`, and WebJam writes no profile content.
- Connection proof requires owned process, authenticated RPC, intended path,
  and exactly one local participant; human audibility remains explicit.
- Restart recovery fails closed when profile truth changes.
- Restart recovery handles stalled but alive Jamulus processes by force-restarting
  the process after repeated heartbeat timeout, then continuing regular auto-reconnect.
- Webex is optional and a failure does not stop music.
- First Record offers shared-only versus Local Originals.
- Studio output appears only in Studio review.
- Studio Arrange edits, take-lane comps, undo/redo, save/reopen, and autosave
  failure/retry never change the take manifest or source WAV bytes.
- Named-section drag ripple-reorders every track as one revision/undo, preserves
  affine source mapping through seam splits, moves contained arrangement
  metadata, reloads playback, and fails atomically for unsafe seam crossings.
- Waveform work is viewport-bounded, progressive, gap-aware, identity-checked,
  and cancelled when its generation becomes stale.
- The deterministic sparse 12-track/60-minute workspace gate passes
  load/edit/save/reopen, Arrange viewport/zoom, bounded waveform scheduling,
  cancelled export cleanup, and unchanged source hashes without treating that
  fixture as a real session.
- Enabled cycle ranges loop on exact frames across output-block boundaries and
  apply deterministic seam smoothing for short and multi-wrap blocks; automated
  samples prove a zero-amplitude seam while physical click-free loop playback
  remains a separate **NOT RUN** gate.
- Studio checksum/reader preparation is cancellable background work; a
  generation test rejects stale completion, and callback tests allow no
  pathname open/stat/fstat work.
- Playback and export use the same arranged source catalog and render rules.
- Export is an atomic, equal-length 24-bit package containing edited and
  original stems, rough mix, markers, import instructions, exact arrangement,
  source manifests, provenance, and SHA-256 checksums.
- Export fails closed when the saved Studio state, a source, a source take
  manifest, or cross-take identity changes before publication.
- Cancel/End/Leave clean up owned processes safely.

## Package gate

1. Commit the source implementation and record its SHA.
2. Build with PyInstaller from `webjam.spec`.
3. Stage checksum-verified Jamulus 3.12.2 and JamulusServer 3.12.2 exactly as
   `.github/workflows/ci.yml` does.
4. Sign nested apps, sidecar, and outer app; verify transport, Info.plist,
   signatures, fresh extraction, and archive SHA-256.
5. Launch the fresh app and inspect Host, Join, optional Webex, Recording
   Setup, Studio Arrange/comp/undo/autosave/export, End/Leave, and an
   invalid/recovery state at 760×600, 1024×768, and 1440×900.
6. Preserve the current rollback package before installing any freshly verified
   candidate app.

## Historical v0.16.3 package evidence

- Source: `4d8c04684ee29ab2ea36ae38dc3be8ac6d612c7a` (canonical build source
  for that historical package).
- Release page: https://github.com/rupret007/webjam/releases/tag/v0.16.3
- Installer: `WebJam-v0.16.3-RC-4d8c046-windows-x64-setup.exe`; SHA-256
  `9f1468d2903cbb2648b34d8f98b31d4509ae1f1b9321fe03b0f9424859f266c7`.
- A fresh install and run should pass strict/deep outer and nested
  Jamulus/JamulusServer verification, transport checks, and a frozen Host smoke.
  Keep a rollback package on hand before installing this verified candidate.

## v0.17.0 physical and credentialed ledger

The source suite does not replace this ledger. Every item remains **NOT RUN**
for v0.17.0 until a dated package/build identity and evidence location are
recorded.

| Gate | v0.17.0 status |
| --- | --- |
| Two Macs hear each other through physical Jamulus interfaces | **NOT RUN** |
| Host/guest native setup and returning path on both Macs | **NOT RUN** |
| Interface disconnect/reconnect while the session stays truthful | **NOT RUN** |
| Sleep/wake and recoverable Jamulus interruption | **NOT RUN** |
| Shared take, Local Originals, transfer, and host finalization | **NOT RUN** |
| Long recording plus interruption/recovery with source hashes preserved | **NOT RUN** |
| Studio Arrange playback, take-lane audition, and comp through real outputs | **NOT RUN** |
| Import edited/original stems, markers, and provenance in an external editor | **NOT RUN** |
| Webex optional behavior without duplicated music | **NOT RUN** |
| Signed clean install, quarantine/SmartScreen, trust, and notarization | **NOT RUN** |
