# WebJam - Music Collaboration Platform

**Merge the power of Jamulus low-latency audio with Webex video conferencing!**

New here? Start with the quick guide: `README_SIMPLE.md`

WebJam is a revolutionary music collaboration application that provides musicians with a professional mixing experience while maintaining video communication. Perfect for remote band rehearsals, music lessons, jam sessions, and collaborative recording.

## 🎵 Features

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

## 🚀 Quick Start

### Installation

1. **Download WebJam Installer**
   - For Windows: `WebJam_Windows_Install.exe`
   - For development: Clone this repository

2. **Run the Installer**
   - The installer will automatically set up:
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

2. **Connect Audio**
   - Click "Launch Jamulus" to connect to the audio server
   - Jamulus will connect to: `172.24.194.9:22124`

3. **Join Video Meeting**
   - Click "Launch Webex" to open the video conference
   - Join meeting: `https://webjam-sbx.webex.com/meet/webjam01`

4. **Mix Your Session**
   - Adjust individual faders for each musician
   - Use pan controls to position musicians in stereo
   - Mute or solo channels as needed
   - Save your mix for next time!

### In-App Help & Diagnostics

- **Setup Wizard**: `Help -> Run Setup Wizard`
  - Runs preflight checks (Jamulus path, server reachability hint, Webex URL, audio diagnostics)
- **Guided Tour**: `Help -> Start Guided Tour`
  - Walks first-time users through launch order, status interpretation, recovery flow, and saving mixes
- **Diagnostics Panel**: `Session -> Open Diagnostics Panel`
  - Shows current endpoint checks, audio backend state, and recovery hints
- **Troubleshooting Shortcut**: `Help -> Quick Start Guide` includes a troubleshooting mode used by actionable error dialogs
- **Usage Metrics**: `Help -> View Usage Metrics`
  - Shows local-only counters (wizard usage, launch success/failure, diagnostics usage, save/load outcomes)
  - Includes quick actions to reset metrics and export diagnostics snapshot

### Accessibility Options

- `View -> High Contrast Mode`
- `View -> Large Text Mode`
- `View -> Increase/Decrease Text Size`
- Keyboard shortcuts: `Ctrl+H`, `Ctrl++`, `Ctrl+-`

### Startup Preferences

- `Startup -> Run Setup Wizard on startup` (toggle)
- `Startup -> Run Guided Tour on startup` (toggle)
- `Startup -> Reset All UI Preferences` (restores defaults and re-enables first-run guidance)
- `Startup -> Reset Window Position`
- Window size/position and startup toggles persist between launches

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
git clone https://github.com/yourusername/webjam.git
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

# Build the application
pyinstaller --onefile --windowed --name WebJam webjam_app.py

# The executable will be in dist/WebJam.exe
```

### Repository Hygiene

- Build artifacts and large binaries are intentionally ignored in git.
- Publish installer/exe outputs through **GitHub Releases** instead of committing them to source control.

## 🔮 Roadmap

### Upcoming Features

- [ ] **Direct Jamulus Integration**: Control Jamulus mixer programmatically
- [ ] **Webex Embedded View**: Show video participants within the app
- [ ] **Recording**: Capture multitrack audio directly in the app
- [ ] **Effects Processing**: Add reverb, compression, EQ per channel
- [ ] **Multiple Servers**: Quick switching between Jamulus servers
- [ ] **MIDI Control**: Use physical faders/controllers for mixing
- [ ] **Automated Mixing**: AI-powered level balancing
- [ ] **Mobile Companion**: iOS/Android app for remote control

## 📝 Configuration

### Custom Server Settings

Edit `webjam_app.py` to change default server:

```python
JAMULUS_SERVER = "your.server.address"
JAMULUS_PORT = "22124"
WEBEX_URL = "https://your-webex-meeting-url"
```

### Saved Settings Location

Mix settings are saved to:
```
Windows: %USERPROFILE%\.webjam_config.json
```

General app settings are loaded from:
```
Windows: %USERPROFILE%\.webjam_settings.json
```

Jamulus candidate paths can also be provided with:
```
WEBJAM_JAMULUS_CANDIDATES="C:\Path\Jamulus.exe;D:\Alt\Jamulus.exe"
```

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
- 🐛 Issues: [GitHub Issues](https://github.com/yourusername/webjam/issues)

---

**Made with ❤️ for musicians, by musicians**

*WebJam - Where music and video meet in perfect harmony* 🎵🎥

