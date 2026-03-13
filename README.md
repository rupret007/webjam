# WebJam - Creative Collaboration Platform

*The app that knows we're making something together.*

**Merge the power of Jamulus low-latency audio with Webex video conferencing!**

New here? Start with the quick guide: `README_SIMPLE.md`

Planning and rollout docs:
- [VISION_AND_ROADMAP.md](VISION_AND_ROADMAP.md) – North star, themes, and phased roadmap
- `CREATIVE_MODES_MVP_SPEC.md`
- `COHORT_VALIDATION_PLAYBOOK.md`

WebJam is a single-brand creative collaboration app with a flagship music mode. It supports live creative sessions across music, visual art, writing, design critique, and storyboard/film planning through shared collaboration primitives.

## 🎵 Features

### Creative Modes (Single App)
- **Music Jam** (flagship): low-latency performance and mixer control
- **Visual Studio**: references + critique loops for art sessions
- **Writer's Room**: draft checkpoints and revision prompts
- **Design Critique**: structured review and decision tracking
- **Storyboard/Film Room**: shot planning and continuity notes

Each mode configures defaults and prompts, without creating separate products.
The workspace layout now also adapts by mode (mixer-heavy for Music Jam, canvas-heavy for critique and writing modes).

### Virtual Mixer Panel
- **Individual Channel Faders**: Control the volume of each musician independently
- **VU Meters**: Real-time audio level visualization for each channel
- **Mute/Solo**: Quickly mute or isolate specific musicians
- **Pan Controls**: Position each musician in the stereo field
- **Save/Load Mixes**: Save your preferred mix settings for different sessions

### Dual Integration
- **Jamulus**: Ultra-low latency audio (<50ms) for real-time musical collaboration
- **Webex**: Full-featured video conferencing with screen sharing, chat, and recording
- **VB-Cable**: Virtual audio routing to merge audio streams

### User-Friendly
- Modern, intuitive GUI with dark theme
- One-click installer handles all dependencies
- Automatic audio device configuration
- Session management and presets
- Hover tooltips on controls and live latency quality indicator
- Shared Session Canvas for artifacts, references, and collaborative notes
- Room template + session goal entry in the main launch flow

### Accessibility
WebJam is designed for inclusive creative collaboration. High contrast mode, scalable text, and keyboard shortcuts let musicians and creators of all abilities participate fully. Use `View -> High Contrast Mode`, `View -> Large Text Mode`, and `View -> Increase/Decrease Text Size`; shortcuts: `Ctrl+H`, `Ctrl++`, `Ctrl+-`.

## 🚀 Quick Start

### Installation

1. **Download WebJam**
   - **Windows**: Download `WebJam.exe` (or the Windows build) from the [GitHub Actions artifacts](https://github.com/rupret007/webjam/actions) after a successful CI run, or from [Releases](https://github.com/rupret007/webjam/releases) when published.
   - **macOS**: Download the matching build from the same place (e.g. `WebJam-macos-x64.zip` or `WebJam-macos-arm64.zip`).
   - **From source**: Clone this repository and run with Python (see Development below).

2. **Run the Installer or App**
   - If using the **Python installer** (`webjam_installer.py`): run it to set up VB-Cable, Jamulus, Webex, and shortcuts.
   - If using a **built executable** (`WebJam.exe` or macOS app): no installer required; run the app and use **Help -> Run Setup Wizard** on first launch.
   - **Windows – first-run security prompt:** When you run the downloaded `WebJam-windows-x64.exe` (or `WebJam.exe`) for the first time, Windows may show **"Windows protected your PC"** (SmartScreen) because the app is unsigned. This is expected when downloading from GitHub. To run the app: click **"More info"**, then **"Run anyway"**. Only download the executable from the [official repo](https://github.com/rupret007/webjam) (Actions artifacts or Releases).
   - The installer (when used) will set up:
     - VB-Cable (virtual audio device)
     - Jamulus client (latest available installer, with bundled fallback)
     - Webex desktop app (official Cisco MSI by architecture)
     - Desktop shortcuts
     - Audio device configuration

3. **Launch WebJam**
   - Use the desktop shortcut or run:
     ```bash
     python webjam_app_enhanced.py
     ```

### First Session

1. **Start the Application**
   - Launch WebJam from your desktop
   - On first run, complete **Help -> Run Setup Wizard**
   - Run **Session -> Run Ready Check** before going live
   - Choose your **Creative Mode**, session template, and goal from the top control bar

2. **Connect Audio**
   - Click "Launch Jamulus" to connect to the audio server
   - Jamulus will connect to: `172.24.194.9:22124`

3. **Join Video Meeting**
   - Click "Launch Webex" to open the video conference
   - Join meeting: `https://webjam-sbx.webex.com/meet/webjam01`

4. **Use Shared Session Canvas**
   - Add links/artifacts/references
   - Capture live notes for decisions and next actions
   - Use **Insert Timestamp** in notes for time-linked callouts
   - Track review state: draft/review/final

5. **Mix Your Session**
   - Adjust individual faders for each musician
   - Use pan controls to position musicians in stereo
   - Mute or solo channels as needed
   - Save your mix for next time!

### In-App Help & Diagnostics

- **Ready Check**: `Session -> Run Ready Check` (also available in `Help`)
  - Shows an at-a-glance readiness summary (pass/fail checks, current latency class, participant count)
  - Provides direct actions to run Setup Wizard, open diagnostics, and export a full diagnostics bundle
- **Setup Wizard**: `Help -> Run Setup Wizard`
  - Runs preflight checks (Jamulus path, server reachability hint, Webex URL, audio diagnostics)
- **Diagnostics Panel**: `Session -> Open Diagnostics Panel`
  - Shows current endpoint checks, audio backend state, and recovery hints
  - Includes both **Export Snapshot** (JSON) and **Export Bundle** (ZIP with logs/settings/context)
- **Troubleshooting Shortcut**: `Help -> Quick Start Guide` includes a troubleshooting mode used by actionable error dialogs
- **Usage Metrics**: `Help -> View Usage Metrics`
  - Shows local-only counters (wizard usage, launch success/failure, diagnostics usage, save/load outcomes, mode adoption)
  - Includes quick actions to reset metrics and export diagnostics snapshot
- **Session Brief Export**: `Session -> Export Session Brief`
  - Exports a markdown brief with mode/template/goal, participant list, artifacts, and current notes
  - Useful for handoff, async review, and next-session kickoff

### Cohort Validation

- Use `Validation -> Set Cohort Name` to tag pilot groups (visual_artists, writers, designers, mixed_discipline).
- Use `Validation -> Record Session Complete` at the end of creator sessions.
- Review outcomes in the usage metrics panel and diagnostics snapshot export.

### Accessibility Options

- `View -> High Contrast Mode`
- `View -> Large Text Mode`
- `View -> Increase/Decrease Text Size`
- Keyboard shortcuts: `Ctrl+H`, `Ctrl++`, `Ctrl+-`

### Startup Preferences

- `Startup -> Run Setup Wizard on startup` (toggle)
- `Startup -> Auto reconnect services` (toggle with bounded backoff retries)
- `Startup -> Reset All UI Preferences` (restores defaults and re-enables first-run guidance)
- `Startup -> Reset Window Position`
- Window size/position and startup preferences persist between launches

### Security Notes

- First-time admin setup now uses a one-time bootstrap password generated locally.
- Admin sign-in requires password rotation on first successful login.
- Repeated failed sign-ins trigger temporary lockout.

## 🎛️ Using the Mixer

### Channel Strip Controls

Each musician gets their own channel strip with:

- **Fader**: Vertical slider for volume control (0dB to -∞)
- **VU Meter**: Shows current audio level in real-time
- **Pan**: Horizontal slider for stereo positioning (L-C-R)
- **Mute Button (M)**: Silence this channel (red when active)
- **Solo Button (S)**: Hear only this channel (green when active)

### Master Controls

- **Save Mix**: Store current settings to disk
- **Load Mix**: Restore previously saved settings
- **Reset All**: Return all channels to default values

### Tips for Great Mixes

1. **Start with all faders at 0dB** and adjust down for balance
2. **Use pan controls** to create stereo separation (drums center, guitars L/R)
3. **Save multiple mixes** for different song arrangements
4. **Watch VU meters** to avoid clipping (keep peaks below 0dB)
5. **Use solo** to check individual instruments during setup

## 🔧 Technical Details

### Audio Routing

```
Musician → Jamulus Client → VB-Cable Output → Webex Input
Webex Audio → VB-Cable Input → Your Speakers/Headphones
```

### System Requirements

- **OS**: Windows 10/11 (64-bit)
- **RAM**: 4GB minimum, 8GB recommended
- **Network**: Broadband internet with <30ms latency to server
- **Audio**: ASIO-compatible audio interface recommended (not required)

### Companion API

WebJam exposes an optional localhost API so external tools (DAWs, editors, scripts) can read live session state. See **[COMPANION_API.md](COMPANION_API.md)** for endpoints (`/health`, `/participants`, `/diagnostics`), usage, and dependencies.

### Latency Optimization

For best results:
1. Use wired Ethernet (not WiFi)
2. Close other network applications
3. Use ASIO drivers if available
4. Set Jamulus buffer to 64 samples or lower
5. Keep video resolution at 720p or lower

## 🛠️ Development

### Setup Development Environment

```bash
# Clone the repository
git clone https://github.com/rupret007/webjam.git
cd webjam

# Install dependencies
pip install -r requirements.txt

# Run the application
python webjam_app_enhanced.py
```

### Project Structure

```
WebJam/
├── webjam_app_enhanced.py     # Main GUI application
├── webjam_app.py              # Legacy/basic GUI
├── webjam_launch_session.py   # Session launcher (legacy)
├── webjam_win_oneclick.py     # One-click installer
├── requirements.txt            # Python dependencies
├── VB/                        # VB-Cable installer files
├── jamulus_3.11.0_win.exe    # Jamulus installer
└── README.md                  # This file
```

### Building the Installer

```bash
# Install PyInstaller
pip install pyinstaller

# Build the application (entry point is webjam_app_enhanced.py)
pyinstaller --onefile --windowed --name WebJam --hidden-import=customtkinter webjam_app_enhanced.py

# The executable will be in dist/WebJam.exe (Windows)
```

CI builds (see `.github/workflows/ci.yml`) produce Windows and macOS artifacts on push; download them from the Actions run or from Releases.

**Code signing (optional):** To sign the Windows executable so SmartScreen shows your publisher instead of "Unknown publisher", add a code-signing certificate (PFX) as repository secrets and the CI will sign the built exe automatically. In GitHub: **Settings → Secrets and variables → Actions**, add `WINDOWS_CODESIGN_PFX` (base64-encoded PFX file) and `WINDOWS_CODESIGN_PASSWORD` (certificate password). The workflow signs the Windows build when these secrets are present.

### Repository Hygiene

- Build artifacts and large binaries are intentionally ignored in git.
- Publish installer/exe outputs through **GitHub Releases** instead of committing them to source control.

## 🔮 Roadmap

WebJam is being built to be **unlike any collaboration app before or after**: one room, one goal, one shared canvas, with sessions that have shape and context that carries forward. See **[VISION_AND_ROADMAP.md](VISION_AND_ROADMAP.md)** for the full vision and phased plan.

### Upcoming (selected)

- **Phase 1:** One-click session templates, review states driving next session, documented Companion API, accessibility as a differentiator.
- **Phase 2:** Mode-specific layouts, room sound & shared metronome, listening profiles, in-session rituals, time-linked notes, exportable session brief, offline-first notes, cohort analytics.
- **Phase 3:** Effects per channel, E2E encrypted canvas, recording + timeline, community template gallery.

### Also on the list

- [ ] Direct Jamulus integration (programmatic mixer control)
- [ ] Webex embedded view (video in-app)
- [ ] Recording: capture multitrack audio in the app
- [ ] Multiple servers: quick switching between Jamulus servers
- [ ] MIDI control: physical faders/controllers for mixing
- [ ] Mobile companion: remote control app

## 📝 Configuration

### Custom Server Settings

Core defaults are loaded from the app config file and environment variables.
Mixer state is stored separately (see below). To change defaults, edit the config file or set:

- `WEBJAM_JAMULUS_SERVER` – Jamulus server host
- `WEBJAM_JAMULUS_PORT` – Jamulus port (default 22124)
- `WEBJAM_WEBEX_URL` – Webex meeting URL

You can also edit `core/settings.py` to change default values used when no config exists.

### Saved Settings Location

App settings and mix settings use separate files:

- **Config file**: `%USERPROFILE%\.webjam_config.json` (Windows) or `~/.webjam_config.json` (macOS/Linux)
- **Mix file**: `%USERPROFILE%\.webjam_mix.json` (Windows) or `~/.webjam_mix.json` (macOS/Linux)

Optional override via environment:

```
WEBJAM_JAMULUS_CANDIDATES="C:\Path\Jamulus.exe;D:\Alt\Jamulus.exe"
```

SQLite runtime defaults (local repository):
- `busy_timeout` is set to 5000ms to reduce lock errors under concurrent writes.
- `journal_mode` is set to WAL when supported.

### Legacy Launchers

- `webjam_launch_session.py` and `webjam_win_oneclick.py` are legacy launcher paths.
- Prefer `webjam_installer.py` for current install/launch behavior.

## 🤝 Contributing

Contributions welcome! Please:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## 📄 License

This project is licensed under the MIT License - see LICENSE file for details.

## 🙏 Acknowledgments

- **Jamulus**: Open-source low-latency audio software
- **Webex**: Professional video conferencing platform
- **VB-Audio Software**: Virtual audio cable technology
- **CustomTkinter**: Modern UI components for Python

## 📞 Support

Having issues? Contact us:

- 📧 Email: support@webjam.io
- 💬 Discord: [WebJam Community](https://discord.gg/webjam)
- 🐛 Issues: [GitHub Issues](https://github.com/rupret007/webjam/issues)

---

**Made with ❤️ for musicians, by musicians**

*WebJam - Where music and video meet in perfect harmony* 🎵🎥

