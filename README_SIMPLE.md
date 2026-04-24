# WebJam — Quick Start (Tkinter UI)

> **Note:** The primary WebJam UI is the **Qt Conductor** (`python webjam_qt_main.py`). This guide covers the legacy Tkinter UI (`python webjam_app_enhanced.py`), which remains available as a fallback. The keyboard shortcuts and mix-save workflow described below apply to the Tkinter UI.

WebJam combines Jamulus (live audio) and Webex (video meeting) in a single launcher so your creative sessions start in seconds.

## Getting Started

1. **Sign in** (optional) to save your settings to your WebJam profile across sessions.
2. Click **Launch Jamulus**. The Jamulus mixer opens and connects to your configured server.
3. Click **Launch Webex**. Your browser opens to the meeting.
4. Use the mixer faders, mute, and solo controls to shape your personal monitor mix.

## Mix Defaults

- **Save Mix** (Ctrl+S) saves your current fader/mute/solo state as your default.
- **Load Mix** (Ctrl+O) restores a saved mix.
- Saved default mixes restore automatically on next sign-in or launch.
- Signed-in users save to their WebJam profile. Anonymous use saves a local default mix on this computer.

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| Ctrl + S | Save Current Mix |
| Ctrl + O | Load Mix |
| Ctrl + Q | Quit WebJam |
| F1 | Open Help |
| Space | Unmute All |
| Ctrl + R | Reset All Faders |
| M | Mute Selected Channel |
| S | Solo Selected Channel |
| Ctrl + J | Launch Jamulus |
| Ctrl + W | Launch Webex |

## Session Tools

- **Export Session Brief** — save a Markdown summary of today's session.
- **Export Diagnostics Bundle** — package logs and config for support.
- **Run Ready Check** — verify Jamulus + Webex are connected before going live.

Run Session -> Run Ready Check before your first live room to confirm everything is working.
