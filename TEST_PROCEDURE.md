# WebJam v0.16.3 test procedure

> The package evidence in this procedure is tracked for the current v0.16.3
> candidate stream. Keep earlier archive records (for example v0.16.0) for
> historical comparison only and do not treat them as current certification.

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
- Webex is optional and a failure does not stop music.
- First Record offers shared-only versus Local Originals.
- Studio output appears only in Studio review.
- Cancel/End/Leave clean up owned processes safely.

## Package gate

1. Commit the source implementation and record its SHA.
2. Build with PyInstaller from `webjam.spec`.
3. Stage checksum-verified Jamulus 3.12.2 and JamulusServer 3.12.2 exactly as
   `.github/workflows/ci.yml` does.
4. Sign nested apps, sidecar, and outer app; verify transport, Info.plist,
   signatures, fresh extraction, and archive SHA-256.
5. Launch the fresh app and inspect Host, Join, optional Webex, Recording
   Setup, Studio, End/Leave, and an invalid/recovery state at 760×600,
   1024×768, and 1440×900.
6. Preserve the current rollback package before installing any freshly verified
   candidate app.

## v0.16.3 package evidence

- Source: `bb70bca22f26467b9a86d5287ee781a70da2e360` (current branch head).
- Archive: `WebJam-v0.16.3-RC-4d8c046-windows-x64-setup.exe`; SHA-256
  `9f1468d2903cbb2648b34d8f98b31d4509ae1f1b9321fe03b0f9424859f266c7`.
- A fresh extraction and run should pass strict/deep outer and nested
  Jamulus/JamulusServer verification, transport checks, and a frozen Host smoke.
  Keep a rollback package on hand before installing this verified candidate.

## Physical pilot ledger

Mark each item PASS, FAIL, or NOT RUN:

- two Macs hear each other through Jamulus;
- host/guest native Jamulus setup and returning fast path;
- interface disconnect/reconnect mid-session;
- sleep/wake and a recoverable Jamulus interruption;
- shared take, Local Originals, and host finalization;
- Studio review and import of exported stems into an external editor;
- Webex optional behavior without duplicated music.

This package evidence does not replace the physical ledger below.
