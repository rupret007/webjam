# PR/Release Notes - Reliability Hardening Rollup

## Summary
- Improves authentication, sqlite persistence, API bridge resilience, and controller thread safety.
- Reduces operational risk from malformed inputs and stale launcher utility duplication.
- Adds focused regression coverage for lockout concurrency, API callback failures, and endpoint integration.

## Key Changes

### Security and Integrity
- Serialized lockout updates in `storage/repository.py` using `BEGIN IMMEDIATE`.
- Constant-time digest comparison via `hmac.compare_digest`.
- Bounded cohort event retention (latest 1000 events).

### Runtime and Concurrency
- Participant map access in `jamulus_controller.py` guarded with `RLock`.
- Auto-generated participant channel IDs now avoid remove/re-add collisions.
- `load_mix()` now validates payload shape, coerces types, and clamps ranges.
- `save_mix()` now uses atomic write/replace semantics to reduce corruption risk on write failures.
- Local API bridge now stops cleanly and isolates callback failures behind HTTP 500 responses.
- Repository now uses explicit managed sqlite connections, applies `busy_timeout`/best-effort WAL mode, and performs atomic settings/event updates under concurrent load.

### Configuration and Docs
- Admin endpoint host/port validation in `admin/admin_panel.py`.
- Malformed settings file warnings in `core/settings.py`.
- Env-driven Jamulus port now enforces `1..65535`; invalid values fall back safely.
- New env flags documented:
  - `WEBJAM_AGENT_DEBUG_LOG`
  - `WEBJAM_AGENT_DEBUG_LOG_PATH`
- SQLite runtime behavior documented in README/CHANGELOG.

### Launcher Maintenance
- Added `utils/installer_helpers.py` and moved low-risk shared helpers:
  - `run`
  - `is_admin`
  - `find_jamulus`
  - `vb_cable_present`
- Legacy launcher scripts now call shared helpers.

## Test Evidence

- Focused hardening tests:
  - API bridge callback wrapping and TestClient endpoint coverage
  - lockout concurrency stability
  - bounded cohort retention
  - malformed mix handling and clamped value loading
- Full suites:
  - `python -m unittest test_modernization` -> pass
  - `python -m unittest test_webjam` -> pass

## Commit Chunks
- `0530f01` Harden mixer input handling and runtime config safeguards.
- `3ec3d23` Strengthen repository concurrency and persistence boundaries.
- `6b2ae75` Harden local API bridge lifecycle and callback error isolation.
- `3b54447` Expand regression coverage for hardening and edge-case behavior.
- `328d189` Document runtime defaults and consolidate shared installer helpers.
- `a365bfa` Add changelog and PR release notes for hardening rollout.
- `be11e8a` Harden concurrency boundaries and atomicity across repository and controller paths.

