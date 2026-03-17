# WebJam (Simple Guide)

WebJam lets creators collaborate in one app for music, visual art, writing, design, and storyboarding with:
- **Jamulus** for low-latency audio
- **Webex** for video
- A built-in **mixer** for personal volume control
- A shared **Session Canvas** for artifacts, links, and live notes

## 1) Install

1. **Get the app**: Download `WebJam.exe` (Windows) or the macOS build from [GitHub Actions](https://github.com/rupret007/webjam/actions) or [Releases](https://github.com/rupret007/webjam/releases). Or clone the repo and run the Python installer (`webjam_installer.py`) to set up VB-Cable, Jamulus, and Webex.
2. If prompted, restart your PC after installing drivers.
3. Launch WebJam (desktop shortcut or run the app).

## 2) First Launch

1. Open `Help -> Run Setup Wizard`.
2. Follow the checks and fix anything marked as failed.
3. Run `Session -> Run Ready Check` before your first live room.
4. If you need first-time admin access, use `Admin -> Sign In`; WebJam will point you to the local one-time bootstrap credential file and then require a password change.

## 3) Start a Session

0. Choose a **Creative Mode** at the top (Music Jam, Visual Studio, Writer's Room, Design Critique, Storyboard/Film Room).
0. The layout auto-adjusts by mode (more mixer space for music, more canvas space for critique/writing).
1. Set a **Template** and **Session Goal**.
1. Click **Launch Jamulus**.
2. Click **Launch Webex**. Your browser opens to the meeting.
3. Wait for participants to appear in the mixer.
4. Adjust faders/pan/mute/solo as needed.
5. Use the **Shared Session Canvas** to pin references, track review state, and capture notes.
6. Use **Insert Timestamp** in notes for time-linked callouts during playback/discussion.

## 4) Save Your Mix

- Click **Save Mix** to keep your settings.
- If you are signed in, that default mix is saved to your WebJam profile.
- If you are not signed in, WebJam saves a local default mix on this computer.
- Saved default mixes restore automatically on next sign-in or launch.
- Use **Load Mix** if you want to reload the current default manually during a session.
- Use **File -> Save Listening Profile** to keep named presets like `Rehearsal`, `Focus on Drums`, or `Critique Balance`.

## 5) If Something Fails

Use this order:
1. `Session -> Run Ready Check`
2. `Session -> Open Diagnostics Panel`
3. `Help -> Run Setup Wizard`
4. Retry launch actions

From Diagnostics Panel, use:
- `Export Snapshot` for quick JSON state export
- `Export Bundle` for full ZIP diagnostics package

## 6) Session Handoff

- Use `Session -> Export Session Brief` to generate a markdown handoff with mode/template/goal, participants, artifacts, and notes.
- Share the exported brief with your team before the next room.

## Validation Cohorts

Use `Validation` menu:
- Set Cohort Name
- Record Session Complete

This helps track activation and cross-mode adoption during pilot programs.

## Accessibility

Use `View` menu:
- High Contrast Mode
- Large Text Mode
- Increase/Decrease Text Size

## Startup Options

Use `Startup` menu:
- Run Setup Wizard on startup
- Auto reconnect services
- Reset All UI Preferences

## More Detailed Docs

- Full README: `README.md`
- Full user manual: `USER_GUIDE.md`
- UX checklist: `UX_ACCEPTANCE_CHECKLIST.md`
