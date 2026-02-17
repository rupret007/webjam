# WebJam Modernization Baseline

Date: 2026-02-17
Platform: Windows 10 (build 26200)
Python: 3.11.9

## Baseline Health

- Existing automated tests pass consistently: 67/67 across 3 runs.
- Current architecture is stable but heavily scaffolded around future integrations.
- Main blockers for production fidelity are simulated audio metering and placeholder protocol/control paths.

## Measured Baseline

- Test runtime: ~19s per run (3-run harness ~60s).
- UI update cadence target in current app: 20 FPS (50ms loop).
- Mixer data path currently stores state reliably in memory + JSON persistence.

## Known Gaps Before Modernization

- `jamulus_controller.py`
  - monitor loop participant sync is placeholder.
  - mixer apply path is placeholder.
  - audio monitor uses random values instead of measured stream data.
- `webjam_app_enhanced.py`
  - hardcoded server and theme values.
  - monolithic UI file.
- `webex_integration.py`
  - browser-first path with placeholder monitoring.
- Cross-cutting
  - no centralized typed settings.
  - print-based logging instead of structured logging.
  - no local API surface for companion clients.
  - no SQLite-backed admin/policy layer.

## Modernization Acceptance Baseline

This baseline is considered improved when:

1. audio levels come from real capture path when available (with explicit fallback state),
2. protocol adapter exists and is wired into controller paths,
3. settings/logging/admin/API modules are integrated without regressing existing test stability.
