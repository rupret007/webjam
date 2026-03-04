# WebJam Development Setup

Guide for setting up a development environment on Windows (or macOS/Linux).

## Prerequisites

| Tool | Version | Notes |
|------|---------|-------|
| Python | 3.11+ | Download from https://www.python.org/downloads/ |
| Git | Latest | https://git-scm.com/downloads |
| VB-Cable | Latest | Optional, Windows only -- for audio routing between Jamulus and Webex |

When installing Python on Windows, check **"Add python.exe to PATH"** during the installer.

## Clone the Repository

**From Cisco GitHub Enterprise:**

```bash
git clone https://wwwin-github.cisco.com/jestory/WebJam.git
cd WebJam
```

**From public GitHub:**

```bash
git clone https://github.com/rupret007/webjam.git
cd webjam
```

## Create a Virtual Environment

### Windows (PowerShell)

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

### Windows (Command Prompt)

```cmd
python -m venv .venv
.venv\Scripts\activate.bat
```

### macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
```

## Install Dependencies

```bash
pip install -r requirements.txt
```

All dependencies are listed in `requirements.txt`. Optional runtime dependencies (the app gracefully degrades without them):
- `customtkinter` -- modern themed UI widgets
- `sounddevice` / `numpy` -- audio level monitoring
- `fastapi` / `uvicorn` -- companion localhost API

## Run the Application

```bash
python webjam_app_enhanced.py
```

On first launch, use **Help > Run Setup Wizard** to configure Jamulus path, server, and Webex URL.

## Run Tests

The project uses Python's built-in `unittest` framework. Run the full suite:

```bash
# Tests in tests/ directory (unit + edge cases)
python -m unittest discover -s tests -v

# Root-level integration/smoke tests
python -m unittest test_webjam test_modernization -v
```

Expected result: 291 tests passing, 1 skipped (Windows-only test on non-Windows).

## Build a Standalone Executable

```bash
pip install pyinstaller

pyinstaller --onefile --windowed --name WebJam ^
  --hidden-import=customtkinter ^
  --hidden-import=numpy ^
  --hidden-import=sounddevice ^
  --hidden-import=fastapi ^
  --hidden-import=uvicorn ^
  webjam_app_enhanced.py
```

The executable appears in `dist/WebJam.exe` (Windows) or `dist/WebJam.app` (macOS).

On macOS, omit `--onefile` and use `ditto` to create a zip:

```bash
pyinstaller --windowed --name WebJam \
  --hidden-import=customtkinter --hidden-import=numpy \
  --hidden-import=sounddevice --hidden-import=fastapi \
  --hidden-import=uvicorn webjam_app_enhanced.py
ditto -c -k --sequesterRsrc --keepParent dist/WebJam.app WebJam-macos.zip
```

## Environment Variables

Override defaults without editing code:

| Variable | Default | Description |
|----------|---------|-------------|
| `WEBJAM_JAMULUS_SERVER` | `172.24.194.9` | Jamulus server hostname or IP |
| `WEBJAM_JAMULUS_PORT` | `22124` | Jamulus server port |
| `WEBJAM_WEBEX_URL` | `https://webjam-sbx.webex.com/meet/webjam01` | Webex meeting URL |
| `WEBJAM_JAMULUS_CANDIDATES` | (Windows default paths) | Semicolon-separated Jamulus executable paths |
| `WEBJAM_ENABLE_SENTRY` | `false` | Enable Sentry error reporting |
| `WEBJAM_LOG_LEVEL` | `INFO` | Logging level |

## Project Structure

```
webjam_app_enhanced.py     Main GUI application (tkinter/customtkinter)
webjam_app.py              Legacy basic GUI
core/                      Settings, models, creative modes, templates, protocol
storage/                   SQLite repository for users, settings, canvas, audit
admin/                     RBAC policy engine and admin panel
ui/                        Auth controller, services, dialogs, views, theme
api/                       Optional FastAPI companion API
utils/                     Platform helpers (installer, audio routing detection)
tests/                     Unit and edge-case test modules
VB/                        VB-Cable driver INFs (Windows audio routing)
.github/workflows/ci.yml   CI: test on Windows, build for Windows + macOS
```

## Windows-Specific Notes

- **VB-Cable**: The `VB/` directory contains INF files for the virtual audio cable driver. The installer (`webjam_installer.py`) handles VB-Cable setup automatically.
- **Admin detection**: `utils/installer_helpers.py` uses `ctypes.windll` to check for admin privileges -- this only activates on Windows.
- **SmartScreen**: Downloaded `.exe` files trigger a "Windows protected your PC" warning. Click "More info" then "Run anyway".

## Cursor IDE Setup

When you open this project in Cursor, the `.cursor/rules/webjam.mdc` file provides automatic context about the project architecture, conventions, and commands. No additional Cursor configuration is needed.

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `ModuleNotFoundError: No module named 'tkinter'` | Reinstall Python with the "tcl/tk" option checked |
| `python` not found (Windows) | Reinstall Python and check "Add python.exe to PATH" |
| Tests hang or timeout | Ensure no other Jamulus/WebJam instance is running |
| `ImportError` on startup | Run `pip install -r requirements.txt` to ensure all deps are installed |
