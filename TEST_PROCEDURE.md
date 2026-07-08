# WebJam Standard Test Procedure

**Last Updated**: 2026
**Purpose**: Standardized procedure for testing WebJam application

---

## Current Canonical Workflow (2026)

Use this workflow for current development and release validation:

```bash
# Lint
ruff check webjam_qt/ core/ ui/ services/ api/

# UX smoke
QT_QPA_PLATFORM=offscreen python ux_smoke_test.py

# Full unit/UI suite
QT_QPA_PLATFORM=offscreen pytest tests/ -v

# Real Jamulus integration
WEBJAM_JAMULUS_BINARY=/path/to/jamulus-headless pytest tests/test_real_jamulus_integration.py -v

# Frozen app smoke build
python -m PyInstaller --clean --noconfirm webjam.spec
```

Success criteria for closed-pilot release readiness: all commands above pass, the three artifacts are produced (`WebJam-windows-x64.zip`, `WebJam-macos-arm64.zip`, `WebJam-macos-x64.zip`), each bundling Jamulus per platform (macOS: nested `Jamulus.app`; Windows: bundled installer — see `THIRD_PARTY_NOTICES.md`), and the real-hardware pilot gate checklist is complete on physical Windows x64, macOS ARM64, and macOS Intel x64 machines: clean install/first launch including unsigned-app warning, Ready Check visible and passing, Jamulus auto-found by the wizard (macOS bundled copy) or installed via the wizard's "Install Jamulus now" button (Windows), Ctrl+P real-audio smoke, two-person Jamulus, Record button, take retrieval, and Take Deck playback.

---

## Archived Procedure

The original October 2024 test procedure (written for the retired
`test_webjam.py` multi-run harness) has been moved to
[`legacy/TEST_PROCEDURE_2024.md`](legacy/TEST_PROCEDURE_2024.md) for
historical reference. It is not applicable to the current Qt Conductor app
or its `pytest`-based suite — use the **Current Canonical Workflow (2026)**
section above instead.
