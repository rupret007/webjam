# WebJam In-App Help Routing Map

## Primary Routes

- **First Run**
  - `Help -> Run Setup Wizard`
  - Use for initial checks, missing prerequisites, and baseline readiness.

- **Connection Problems**
  - `Session -> Open Diagnostics Panel`
  - Use for Jamulus endpoint checks, current audio diagnostics, and runtime error context.

- **General Usage**
  - `Help -> Quick Start Guide`
  - Use for launch order, mixer basics, and operating tips.

## Error Dialog Routing

- **Jamulus Launch Failed**
  - Retry from dialog -> then Setup Wizard -> then Diagnostics Panel.

- **Webex Open Failed**
  - Retry from dialog -> verify URL and browser -> open Diagnostics Panel.

- **Save/Load Mix Failed**
  - Retry from dialog -> run Setup Wizard to verify paths -> save a fresh preset.

## Recommended Support Sequence

1. Capture current state in Diagnostics Panel.
2. Re-run Setup Wizard checks.
3. Retry action from main controls.
4. Escalate with diagnostics details if unresolved.
