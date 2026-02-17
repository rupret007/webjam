# WebJam UX Acceptance Checklist

Use this checklist before each UX-focused release.

## Install and First Run

- [ ] Fresh install succeeds and app launches.
- [ ] First run automatically opens Setup Wizard.
- [ ] Setup Wizard preflight checks execute without crash.
- [ ] Failing preflight checks show actionable guidance.
- [ ] Setup Wizard can be re-opened from `Help -> Run Setup Wizard`.

## Session Launch and Status Clarity

- [ ] `Launch Jamulus` updates status and readiness labels.
- [ ] `Launch Webex` updates status and readiness labels.
- [ ] Participant count updates when participants are added/removed.
- [ ] `Session -> Add Demo Participants` clearly indicates demo mode behavior.
- [ ] Bottom status bar reflects Jamulus/Webex/mixer readiness states.

## Error Recovery

- [ ] Jamulus missing-path flow shows actionable retry/help dialog.
- [ ] Webex open failure shows actionable retry/help dialog.
- [ ] Save mix failure shows actionable retry/help dialog.
- [ ] Load mix failure shows actionable retry/help dialog.

## Diagnostics and Help Routing

- [ ] `Session -> Open Diagnostics Panel` opens and displays endpoint/audio context.
- [ ] Diagnostics panel links to Setup Wizard and Help.
- [ ] `Help -> Quick Start Guide` includes troubleshooting path.
- [ ] `HELP_ROUTING_MAP.md` matches current menu routes.

## Regression and Validation

- [ ] `python -m unittest test_modernization.py` passes.
- [ ] `python -m unittest test_webjam.py` passes.
- [ ] No new linter errors in touched files.
