# Worth-Building gate

**PASS — W1–W10.** Evaluated clean source commit `49acdb5b7f4293bb3ad95fd53c3125a4bf7a8fbb` on 2026-09-04 CT. This is an automated source-product gate. Human first-use timing, physical audio and release trust remain NOT RUN. Final draft-tip verification is recorded separately in the PR and Bob handoff.

| Gate | Result | Direct evidence |
| --- | --- | --- |
| W1 — clear next click | PASS | `core/creative_modes.py`, `webjam_qt/windows/launch_dialog.py`, `tests/test_art_start_ux.py`, `tests/test_feel_pass.py`: two Art starts then Host/Join; Music Host/Join; every visible control harvested. |
| W2 — Host/Join success and failure | PASS | `tests/test_host_share_join_flow.py`, `tests/test_jamulus_native_startup.py`, `tests/test_controller_remote_transport.py`: readiness-bound invite, failed settings save, consumed invitation, startup cleanup and offline File dispatch. |
| W3 — no banned door chrome | PASS | `tests/support/start_ux.py`, `tests/test_art_start_ux.py`, `tests/test_ui_redesign_regressions.py`: actual visible/a11y text, exact button inventories, compact geometry, Windows installer on its explicit Help page. |
| W4 — invitation privacy | PASS | `tests/test_controller_remote_transport.py`, `tests/test_host_share_join_flow.py`, `tests/test_unified_musician_guidance.py`: safe public projection, secret-free failures and exact semantic replacement action. Existing redaction and session-help tests also pass in the full suite. |
| W5 — one shared next action | PASS | `core/session_conductor.py`, `core/musician_guidance.py`, `tests/test_unified_musician_guidance.py`: HUD, stage, Notes and Pocket agree; stale revisions cannot reintroduce retry, and cleanup history makes no safe-retry claim. |
| W6 — UX smoke | PASS | `.venv/bin/python ux_smoke_test.py`: UX smoke gate passed. |
| W7 — full local suite | PASS | `.venv/bin/pytest -q` with Qt offscreen: 6821 passed, 25 skipped, 3 warnings, 99 subtests passed in 207.97s (0:03:27). |
| W8 — honest physical/release limits | PASS | `TEST_PROCEDURE.md` unchanged; `USER_GUIDE.md`, `README_SIMPLE.md` retain NOT RUN. No signing, tag, release or master merge performed. |
| W9 — Art maturity and Music continuity | PASS | `core/creative_modes.py`, `CREATIVE_MODES_MVP_SPEC.md`, `README_SIMPLE.md`: Art remains Preview; Music/Podcast GA classifications retained. Art owns no automatic canvas or meeting capture. Music/Studio/Shared Track suites pass. |
| W10 — real product value | PASS | Notes retain failed drafts with useful recovery; Studio retries retain original bytes/history and reject other writers; blocked recording opens existing Setup only after retiring the exact pending plan. Source regressions demonstrate each behavior. |

Additional proof: 2339 passed, 2 skipped, 3 warnings, 2 subtests passed in 49.74s. Real Swift/Python pinned-WSS test: 1 passed. Swift protocol/transport: 20 passed. Synthetic multitrack qualification: 20/20 fresh-process iterations, 380 test executions, clean source and cleanup verified. These are source results, not installed-device or physical-output observations.

Hosted builds and the exact final draft SHA are required later gates; this file does not claim them complete.
