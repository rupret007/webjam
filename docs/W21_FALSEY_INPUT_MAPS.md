# W21 — Falsey input maps disable supplemental capture

Implemented in `/Users/jeffstory/Documents/webjam-24h/W21`, branch
`webjam-24h/W21-falsey-input-maps-disable-supplemental-c`, based on `220eb52`.
This report replaces the campaign W21 report for its earlier, unrelated local
video assignment. This change addresses only the current input-map assignment.

## What changed

Malformed saved `{}`, `false`, `0`, and `""` input maps now disable supplemental
Local Original capture instead of silently selecting device channels 1–2.
One type/shape predicate handles both initial coercion and the retained invalid
map check after environment overrides. Modern and legacy environment opt-ins
cannot re-enable a rejected saved map. Unrelated settings remain intact.

Missing maps, explicit `null`, and `[]` deliberately retain existing legacy
compatibility when capture is enabled. Valid mono/stereo maps retain their
behavior. No UI copy or controls changed: copy alone cannot fix authorization.

## Files

- `core/settings.py`: shared invalid-map predicate and both coercion guards.
- `core/session_recording_plan.py`: clarify the existing fallback contract in
  comments/docstring; no resolver behavior change.
- `tests/test_input_map_settings.py`: 36 new parameterized cases covering
  rejected maps, direct coercion, environment overrides, compatibility, valid
  stereo capture, and preservation of unrelated preferences.
- `RECORDING_AND_STUDIO.md`, `CHANGELOG.md`: behavior and compatibility notes.
- `tools/gates/w21_falsey_input_maps.sh`: reproducible offline focused and
  touched-area Python gate, copied verbatim to campaign `items/W21/gate.sh`.
- `docs/W21_FALSEY_INPUT_MAPS.md`: this report, copied to campaign `items/W21/impl.md`.

## Regression evidence

Before the product change, ran:

```bash
QT_QPA_PLATFORM=offscreen PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest \
  -q -p no:cacheprovider tests/test_input_map_settings.py \
  -k 'falsey_malformed or legacy_empty_input_maps or valid_input_maps_preserve'
```

**20 failed, 16 passed, 8 deselected in 0.37s**: all 16 malformed-map loader
cases and all four direct-coercion cases failed. Compatibility and valid-map
controls passed. Log: `items/W21/before-falsey-input-maps.log`.

An earlier draft of the new fixture incorrectly assumed the retired saved
bridge flag alone enabled capture. That draft also returned 20 failed / 16
passed / 8 deselected, but four failures were incorrect test expectations.
The fixture was corrected to include explicit capture opt-in before the
before-change run above. No existing tests or gates were relaxed.

After the fix, the complete input-map module passed: **44 passed in 0.27s**.

## Gate results

Ran from the worktree root:

```bash
bash /Users/jeffstory/Documents/bob-overnight-inject/local-webjam-24h-1010/items/W21/gate.sh
```

**Exit 1 — gate not fully passing.** Seven existing fake-server tests could
not bind `127.0.0.1` in this sandbox: `PermissionError: [Errno 1] Operation not
permitted` at `tests/test_server_rpc_and_record_button.py:107`. The script keeps
these tests and fails on them. No retries, new skips, xfails, or weakened
assertions were introduced. Log: `items/W21/gate-falsey-input-maps.log`.

Focused selection: **36 passed, 8 deselected in 0.27s**.
Standard production-tree Ruff: **passed**. Compileall for
`core webjam_qt ui services api tests`: **passed**. Pip check:
**No broken requirements found** (pip reported its unwritable cache disabled).
`ux_smoke_test.py`: **UX smoke gate passed**.

Full modules executed by the gate, in fresh interpreters:

| Module | Exact result |
| --- | --- |
| test_input_map_settings.py | 44 passed in 0.28s |
| test_settings_edge.py | 48 passed, 13 subtests passed in 0.18s |
| test_settings_corruption_recovery.py | 4 passed in 0.07s |
| test_recording_format_settings.py | 8 passed in 0.51s |
| test_session_recording_plan.py | 49 passed in 0.23s |
| test_local_capture.py | 33 passed in 0.59s |
| test_recording_format_setup.py | 17 passed in 2.08s |
| test_recording_format_controller.py | 19 passed in 8.10s |
| test_recording_setup_compact_recovery.py | 8 passed in 3.05s |
| test_recording_readiness.py | 8 passed in 0.56s |
| test_recording_readiness_ui.py | 16 passed in 3.10s |
| test_server_rpc_and_record_button.py | 7 failed, 133 passed, 13 subtests passed in 8.41s |

After that gate failure, independently executed its remaining modules with
`QT_QPA_PLATFORM=offscreen PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest
-q -p no:cacheprovider tests/<module>` (no reruns of failed tests):

| Module | Exact result |
| --- | --- |
| test_art_start_ux.py | 66 passed in 1.52s |
| test_creative_modes_edge.py | 39 passed in 0.09s |
| test_dual_musician_rehearsal_lab.py | 1 skipped in 0.20s |
| test_multitrack_proof_lab.py | 1 skipped in 0.21s; pytest exit 5, no tests collected |

The two lab modules' existing loopback-availability guards caused those skips;
their integration checks are **NOT RUN**, not passes. Logs are in campaign
`items/W21/` with `falsey-` prefixes. Across complete modules: **492 passed,
7 failed, 2 existing skips**, plus **26 subtests passed**.

Also executed the documented proof runner with the same environment:
`.venv/bin/python tools/run_multitrack_proof_lab.py --report
/tmp/webjam-w21-multitrack-proof-lab.json`. **Exit 1**, `proof_report_missing`:
iteration 1 had 17 passed / 1 skipped, but the skipped lab produced no proof
report. Qualification is false; iterations 2–20 **NOT RUN**. Its temporary
artifacts were cleaned successfully. Copied report:
`items/W21/falsey-multitrack-proof-lab.json`.

Additional checks: Ruff on the changed test module, `bash -n` on the gate,
identical campaign/repository gate copies, and `git diff --check`: **passed**.
The executed gate and independent remaining checks finished within two minutes.

## UX and evidence limits

Read the campaign UX gate, launch copy, creative-mode registry, and Art-start
tests. Existing visible copy remains **Make together** — “Paint, sculpt, 3D
print, or just talk.”; **Paint along** — “Follow a silent video from a file or
lesson link.”; **Host** / **Join**; and “Paste the invite or the whole message.”
The 66 Art-start tests pass, including door count and banned-word checks.
No doors, controls, Preview labels, or fail-closed recovery copy changed.

New screenshots, interactive visual/keyboard review, the full 512-module repo
suite, physical audio, real audio-device capture, two-Mac sessions, real codec
playback, and Webex: **NOT RUN**. Synthetic/offscreen results do not establish
live behavior. No network sends, paid APIs, builds, releases, pushes, or merges
were performed. Free disk remained about **21 GiB**; no large build outputs
were created. Commit is local only.
