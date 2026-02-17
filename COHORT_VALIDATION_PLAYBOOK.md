# Cohort Validation Playbook

## Objective
Validate WebJam as a single creative collaboration app across mixed creator disciplines.

## Cohorts
- `visual_artists`
- `writers`
- `designers`
- `mixed_discipline`

## In-App Workflow
1. Set cohort tag in `Validation -> Set Cohort Name`.
2. Run a normal session using selected creative mode.
3. On completion, click `Validation -> Record Session Complete`.
4. Review counters in `Help -> View Usage Metrics`.
5. Export diagnostics snapshot for local analysis.

## Metrics to Track
- Activation: setup wizard completion + first session completion.
- Cross-mode adoption: `metric_mode_selected_*` values.
- Collaboration quality proxies:
  - session artifacts created
  - notes captured
  - review state transitions
- Retention proxy: repeated `metric_session_completed` over time.

## Reporting Cadence
- Weekly per cohort.
- Compare:
  - session completion count
  - mode mix distribution
  - recurring failure counters (launch failures, setup failures).

## Operational Notes
- Metrics are local-only and lightweight.
- Use diagnostics snapshots for archived checkpoints between pilot rounds.
