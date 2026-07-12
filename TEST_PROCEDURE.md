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

# Hosted-server ownership/adoption/restart regression matrix
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_hosted_server.py -v

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
On the designated host, also require **Host & Start Audio**, authenticated RPC
22240, `Server: Hosting :22124`, recorder start/stop, Stop Audio without server
teardown, and clean owned-server shutdown. An already-running manual server
may show `Server: External :22124` only after recorder authentication and must
survive WebJam quit.
For each macOS artifact, require `codesign --verify --strict` on the completed
outer WebJam bundle and the nested Jamulus bundle. Confirm the nested Jamulus
CDHash is unchanged across the outer shallow-sign step, then test a
quarantined extraction: Gatekeeper may show the documented unsigned-pilot
warning, but must never report a missing/invalid sealed resource or a damaged
application.
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
