# Release round on master `5ca6ba5`

Date: 2026-08-22 (America/Chicago)
Canonical checkout: `~/Documents/WebJam` (WebJam 2 archived; master is the best line)

## Codebase choice

`master` @ `5ca6ba5` is the best product line. Codex `codex/v027-multitrack-proof-lab` unique commits match the work already squash-merged as [#14](https://github.com/rupret007/webjam/pull/14); proof-lab blobs are identical. Master also carries [#19](https://github.com/rupret007/webjam/pull/19), [#20](https://github.com/rupret007/webjam/pull/20), [#18](https://github.com/rupret007/webjam/pull/18), and [#17](https://github.com/rupret007/webjam/pull/17).

## Required CI (same tip, run [32578847050](https://github.com/rupret007/webjam/actions/runs/32578847050))

| Job | Result |
| --- | --- |
| test | success |
| Integration (real Jamulus 3.12.2 / 3.12.3) | success |
| Jamulus 3.12.3 update input (windows-x64 / macos-universal) | success |
| Build Desktop (windows / macos-arm64 / macos-x64 / linux-x64) | success |
| Pocket Stage (iOS app) | success |
| Transport (Go) | success |
| Reference service | success |

## Ten-second UX (source)

- Art: Talk & make / Paint together / Paint along then Host/Join (`core/creative_modes.py`, `tests/test_art_start_ux.py`)
- Music: Host/Join only; profile picker and local studio hidden on live door (`launch_dialog.py`, `tests/test_legacy_mode_picker_retired.py`)
- Helper copy includes "Play live together."

## NOT RUN (fail closed — not PASS)

- Certify Jamulus/JACK one-hour soak
- Windows / macOS Release Trust (signing rehearsals)
- Jamulus 3.12.3 HEADLESS evidence
- Two-Mac Art/Drawpile physical
- Live Music AI credentialed runs
- Physical checklist rows for an exact package

## Publish

Bob does not tag or publish. After Jeff merges any docs PR for Unreleased completeness, Jeff decides whether to cut **v0.27.0** unsigned private test candidate from this tip, following `docs/DESKTOP_RELEASE_RUNBOOK.md` (new publisher lane; do not mutate v0.26.0).
