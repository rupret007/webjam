# WebJam v0.16 test procedure

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
6. Preserve v0.15.0 as rollback before installing the verified v0.16.0 app.

## v0.16 package evidence

- Source: `a36789978efbaac5e85fbc5c6ef55abae4ed42e3`.
- Final source gate: **1,807 passed**, 18 environment-bound skips, zero
  failures/errors, 53.745 seconds.
- Archive: `WebJam-v0.16.0-TEST-NIGHT-macos-arm64.zip`; SHA-256
  `3ad2da6eccd99eb3965cc0e637ff147198e19446b3d878e4631a689cd5c9bf7b`.
- A fresh extraction passed strict/deep outer and nested Jamulus/JamulusServer
  3.12.2 signature checks, transport verification, and a frozen Host smoke.
  The verified app is installed at `/Applications/WebJam.app`; the v0.15.0 app
  and ZIP are preserved as rollback.

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
