# WebJam UX Acceptance Checklist (Qt Conductor)

Use this checklist before each UX-focused Qt Conductor release. The legacy Tkinter menu paths (`Help -> …`, `Session -> …`) no longer apply — see `legacy/UX_ACCEPTANCE_CHECKLIST_TKINTER.md` if you need the old checklist.

## Install and First Run

- [ ] Fresh install succeeds and `python webjam_qt_main.py` launches.
- [ ] First run automatically opens the Qt Setup Wizard.
- [ ] Setup Wizard preflight checks execute without crash.
- [ ] Failing preflight checks show actionable guidance.
- [ ] Setup Wizard can be re-opened from the side rail **Settings** or **Ctrl+,**.

## Session Launch and Status Clarity

- [ ] **Start Audio** updates status and readiness labels.
- [ ] **Open Webex** reports only opening/opened-externally truth and offers Open Again.
- [ ] Talk Break mutes only Jamulus; Resume Music defaults to cancel until Webex mute is confirmed.
- [ ] Participant count updates when real Jamulus participants arrive.
- [ ] Demo participants show **Preview** names before Jamulus connects.
- [ ] Bottom status bar reflects Jamulus/Webex states and participant count.

## Error Recovery

- [ ] Jamulus missing-path flow shows actionable error dialog.
- [ ] Webex open failure offers Retry, Copy Meeting Link, and Open Settings.
- [ ] Save mix failure shows actionable flash message.
- [ ] Load mix failure shows actionable flash message.

## Diagnostics and Help

- [ ] **Ctrl+Shift+D** copies diagnostics summary (secrets redacted).
- [ ] **F1** shows in-app shortcut reference.
- [ ] `HELP_ROUTING_MAP.md` matches Qt Conductor flows (not Tkinter menus).

## Regression and Validation

- [ ] `QT_QPA_PLATFORM=offscreen pytest tests/ -q` passes.
- [ ] No new linter errors in touched files.
