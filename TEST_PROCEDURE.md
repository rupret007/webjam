# WebJam Standard Test Procedure

**Last Updated**: 2026-07-11
**Purpose**: Standardized procedure for testing WebJam application

---

## Current Canonical Workflow (2026)

Use this workflow for current development and release validation:

```bash
# Lint
.venv/bin/python -m ruff check webjam_qt/ core/ ui/ services/ api/

# Import/bytecode and installed-dependency consistency
.venv/bin/python -m compileall -q webjam_qt core ui services api
.venv/bin/python -m pip check

# UX smoke
QT_QPA_PLATFORM=offscreen .venv/bin/python ux_smoke_test.py

# Full unit/UI suite
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/ -v

# Real Jamulus integration
WEBJAM_JAMULUS_BINARY=/path/to/jamulus-headless .venv/bin/python -m pytest tests/test_real_jamulus_integration.py -v

# Frozen app smoke build
.venv/bin/python -m PyInstaller --clean --noconfirm webjam.spec
```

Success criteria for closed-pilot release readiness: all commands above pass,
the three artifacts are produced (`WebJam-windows-x64.zip`,
`WebJam-macos-arm64.zip`, `WebJam-macos-x64.zip`), CI verifies their version,
UI resources, and bundled Jamulus payload. The current physical publication
gate is the two-Apple-Silicon-Mac workflow in `SUNDAY_TWO_MAC_PILOT.md`:
clean install/first launch, Ready Check including its manual Webex `VERIFY`
rows, Ctrl+P real audio, two-person Jamulus, muted-by-default native Webex
talkback, Talk Break fail-safe behavior, Session Pulse export, Record, take
playback, Logic/WAV-stem inspection, reconnect, and a 45–60 minute soak.
Windows x64 and Intel macOS still require clean-artifact startup inspection;
they are not prerequisites for the private two-Mac pilot. Tag builds remain
draft releases until the exact artifacts pass their declared gates.

Run Qt workflows one at a time. Concurrent offscreen GUI test processes can
compete for shared Qt state and create failures that do not reproduce in
isolation. The official Jamulus 3.12.2 CI integration is required when no
compatible local headless binary is configured.

---

## Archived Procedure

The original October 2024 test procedure (written for the retired
`test_webjam.py` multi-run harness) has been moved to
[`legacy/TEST_PROCEDURE_2024.md`](legacy/TEST_PROCEDURE_2024.md) for
historical reference. It is not applicable to the current Qt Conductor app
or its `pytest`-based suite — use the **Current Canonical Workflow (2026)**
section above instead.
