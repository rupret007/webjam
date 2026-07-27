# WebJam v0.20.0 source and physical test procedure

> The source tree reports v0.20.0. Published candidates remain immutable
> rollback/reference evidence; they do not certify the v0.20.0 external Webex
> handoff, Reference Track pilot, or matching iPhone setup kit.

## Scope

This procedure distinguishes automated source/package evidence from physical
musician evidence. A passing source suite does not certify two-Mac audibility,
hardware changes, sleep/wake, interruption recovery, or external-editor import.

## Source gate

Run from the repository root:

```bash
.venv/bin/ruff check webjam_qt/ core/ ui/ services/ api/
.venv/bin/python -m compileall -q core webjam_qt ui services api tests
.venv/bin/python -m pip check
git diff --check
git diff --check origin/master...HEAD
.venv/bin/python -m pip_audit --progress-spinner off
.venv/bin/python ux_smoke_test.py
QT_QPA_PLATFORM=offscreen .venv/bin/pytest -q
```

Audit the four native release locks separately. Windows/Linux use the exact
lock with no dependency resolution; the narrowly documented macOS ignore is
for sdist creation while native release jobs install checksum-pinned wheels:

```bash
.venv/bin/python -m pip_audit --progress-spinner off --disable-pip --no-deps -r requirements-lock/windows-x64.txt
.venv/bin/python -m pip_audit --progress-spinner off --disable-pip --no-deps -r requirements-lock/linux-x64.txt
.venv/bin/python -m pip_audit --progress-spinner off --disable-pip --no-deps --ignore-vuln PYSEC-2026-3447 -r requirements-lock/macos-x64.txt
.venv/bin/python -m pip_audit --progress-spinner off --disable-pip --no-deps --ignore-vuln PYSEC-2026-3447 -r requirements-lock/macos-arm64.txt
```

The GitHub `build-desktop` matrix is the authoritative native package gate.
A local PyInstaller bundle is useful smoke evidence only when its Python,
setuptools, hashed release lock, staged Jamulus payload, and transport match
the target job; it is not a substitute for fresh native matrix evidence.

The transport and reference-service jobs are separate from the root pytest
collection. Reproduce their practical local portions with:

```bash
(cd transport && make check)
(cd transport && go test -race -count=1 ./...)
(cd transport && go mod verify && go mod tidy -diff)
(cd transport && go run golang.org/x/vuln/cmd/govulncheck@v1.6.0 ./...)
(cd transport && make build-all VERSION="$(git rev-parse HEAD)")
.venv/bin/ruff check reference_service
.venv/bin/python -m compileall -q reference_service/webjam_reference reference_service/tests
PYTHONPATH=reference_service .venv/bin/python -m pytest -q reference_service/tests
.venv/bin/python -m build reference_service
```

Record local tool-version differences. GitHub CI pins Go and Python versions,
runs the Linux/JACK real-Jamulus path, health-checks the restricted reference
container, and remains authoritative for those platform-specific gates.

Review at minimum:

- One accepted conductor snapshot drives HUD, passive stage, Session Canvas,
  recording/Studio feedback, diagnostics, and Companion guidance without
  contradictory phase or action copy.
- Host, guest, and practice roles cover setup, Band Check, invite/join, live,
  record/stop/validation, guest media, Studio, export, reconnect, cleanup,
  final failure, and indeterminate restart recovery.
- Notes and Creative Pulse cannot change operational phase/action/output facts
  and never enter diagnostics or Companion output.
- Guidance generation/revision guards reject stale observations; identical
  semantic snapshots do not repeat accessibility updates.
- Meter, waveform, playhead, animation, audio, capture, and playback callbacks
  do not derive or announce guidance.
- Companion participants are anonymous slots. Diagnostics/public errors contain
  no names, channel IDs, invitations, addresses, devices, paths, tokens,
  authored content, or raw exceptions.
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
- Enabled cycle ranges loop on exact frames across output-block boundaries.
  Cycles of four frames or more apply deterministic seam smoothing across
  short and multi-wrap blocks; one- through three-frame pathological cycles
  remain sample-exact and non-silent and may retain a raw seam. Physical
  click-free loop playback remains a separate **NOT RUN** gate.
- Studio checksum/reader preparation is cancellable background work; a
  generation test rejects stale completion, and callback tests allow no
  pathname open/stat/fstat work.
- Playback and export use the same arranged source catalog and render rules.
- On macOS/Linux with the required secure directory APIs, Studio export is an
  atomic, descriptor-relative, equal-length 24-bit package containing edited
  and original stems, rough mix, markers, import instructions, exact
  arrangement, source manifests, provenance, and SHA-256 checksums.
- On Windows or another unsupported runtime, the button must read **Export
  Aligned Originals**. Its reference mix may apply current trim, fader, pan,
  mute, and solo, but arrangement edits, fades, comps, sections, master
  processing, and attached/repeated take lanes must be explicitly excluded.
  A failed edited Studio export must not silently enter this fallback.
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
   invalid/recovery state at 760×600, 1024×768, and 1440×900. On macOS/Linux,
   inspect the edited Studio package. On Windows, verify the separately
   labelled aligned-originals/reference-mix boundary above.
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

## v0.18.0 physical and credentialed ledger

The source suite does not replace this ledger. Every item remains **NOT RUN**
for v0.18.0 until a dated package/build identity and evidence location are
recorded.

| Gate | v0.18.0 status |
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

Use [V018_UNIFIED_GUIDANCE_PILOT.md](V018_UNIFIED_GUIDANCE_PILOT.md) for the
musician-facing cross-surface observations and exact evidence to record during
those physical runs.
