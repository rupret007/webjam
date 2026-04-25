# WebJam Development Setup

Guide for setting up a development environment on Windows (or macOS/Linux).

## Prerequisites

| Tool | Version | Notes |
|------|---------|-------|
| Python | 3.10+ | Download from https://www.python.org/downloads/ |
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

All dependencies are listed in `requirements.txt`. Key dependencies:
- `PySide6>=6.6.0` -- Qt framework for the Conductor UI
- `httpx` -- async HTTP client for API calls
- `customtkinter` -- modern themed UI widgets (legacy Tkinter UI)
- `sounddevice` / `numpy` -- audio level monitoring
- `fastapi` / `uvicorn` -- companion localhost API

## Run the Application

```bash
python webjam_qt_main.py          # Qt Conductor UI (current)
python webjam_app_enhanced.py     # Legacy Tkinter UI (fallback)
```

On first launch a setup wizard runs automatically to configure your Jamulus server, Webex URL, and audio routing. You can reopen it any time with **Ctrl+,** or the ⚙ Settings button in the left rail.

## Run Tests

The project uses `pytest`. Run the full suite:

```bash
python -m pytest tests/ -v
```

Expected result: all tests pass (493 pass, 12 skip as of v0.4). The skips are Windows-only elevation tests that auto-skip on macOS/Linux.

### Running tests locally (CI-equivalent)

CI runs the suite quietly on every push. To match the CI gate exactly
(see `.github/workflows/ci.yml` line 60):

```bash
python3 -m pytest tests/ -q
```

A single failing test fails the CI job, so always run this before pushing.

### Code style (ruff)

CI runs `ruff check` against the three first-party source roots
(`.github/workflows/ci.yml` line 51). Match that gate locally:

```bash
python3 -m ruff check webjam_qt/ core/ ui/mixer_service.py
```

Fix every warning before you commit — the lint job is the first to fail
on a dirty PR, and it blocks the build / release jobs that follow.

### Running the UX smoke gate

`ux_smoke_test.py` boots the Qt Conductor window headlessly and asserts
the shell wires up without raising. It runs in CI between lint and the
test suite (workflow line 54):

```bash
python3 ux_smoke_test.py
```

On macOS / Linux this needs a working Qt platform plugin — set
`QT_QPA_PLATFORM=offscreen` in the environment if you don't have a
display attached (the CI workflow does the same).

## Build a Standalone Executable

```bash
pip install pyinstaller

pyinstaller webjam.spec
# Produces dist/WebJam/WebJam.exe (Windows) or dist/WebJam.app (macOS)
```

## Environment Variables

Override defaults without editing code:

| Variable | Default | Description |
|----------|---------|-------------|
| `WEBJAM_JAMULUS_SERVER` | `172.24.194.9` | Jamulus server hostname or IP |
| `WEBJAM_JAMULUS_PORT` | `22124` | Jamulus server port |
| `WEBJAM_WEBEX_URL` | `https://webjam-sbx.webex.com/meet/webjam01` | Webex meeting URL |
| `WEBJAM_JAMULUS_CANDIDATES` | (macOS + Windows default paths) | Semicolon-separated Jamulus executable paths |
| `WEBJAM_ENABLE_SENTRY` | `false` | Enable Sentry error reporting |
| `WEBJAM_LOG_LEVEL` | `INFO` | Logging level |

## Project Structure

```
webjam_qt_main.py          Primary entry point — Qt Conductor UI
webjam_qt/                 Qt application (windows, widgets, controllers)
webjam_app_enhanced.py     Legacy Tkinter/customtkinter UI (fallback)
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

## Tutorials

End-to-end recipes for the changes that come up most often when extending
the Conductor UI. Each tutorial points at the real files, real method
names, and shows the exact code change needed.

### Tutorial 1: Adding a field to `ParticipantPresentation`

The view-model that drives every participant card is the
`ParticipantPresentation` dataclass in
`webjam_qt/widgets/participant_card.py`. Adding a new field — say, an
`instrument_color` accent shown on the card — is a four-file change.

**1. Add the field to the dataclass** (`webjam_qt/widgets/participant_card.py`):

```python
@dataclass
class ParticipantPresentation:
    channel_id: int
    name: str
    role: str = ""
    fader_level: int = 100
    muted: bool = False
    solo: bool = False
    is_connected: bool = True
    is_local: bool = False
    video_connected: bool = False
    audio_level: float = 0.0
    instrument_color: str = ""   # NEW — hex like "#E6B800"
```

**2. Add the parallel field on `JamulusParticipant`** in `jamulus_controller.py`
(top of the file, around line 22). The shape mirrors the view-model so
`_apply_jamulus_participants` can copy 1:1:

```python
@dataclass
class JamulusParticipant:
    ...
    instrument: str = ""
    instrument_color: str = ""   # NEW
```

**3. Propagate it** in
`webjam_qt/controllers/application_controller.py::_apply_jamulus_participants`.
The upsert branch already maps Jamulus → presentation; add one line in
both the new-participant and existing-participant paths:

```python
self.participants[jp.channel_id] = ParticipantPresentation(
    ...,
    is_local=getattr(jp, "is_local", jp.channel_id == 0),
    instrument_color=getattr(jp, "instrument_color", ""),
)
```

**4. Surface it in the card body** inside `ParticipantCard._compose_layout`:

```python
self._color_chip = QLabel(self._presentation.instrument_color or "")
self._color_chip.setObjectName("InstrumentColorChip")
identity_col.addWidget(self._color_chip)
```

Mirror it in `update_presentation` so live updates flow through.

**5. Update the demo data.** `_DEMO_PARTICIPANTS` at the top of
`application_controller.py` (line 42) seeds the grid before Jamulus
connects — give each entry an `instrument_color` so the demo state shows
the new field too.

### Tutorial 2: Adding a Jamulus JSON-RPC method call

The RPC client lives in `core/jamulus_rpc_client.py`. It owns its own
poll thread (`_poll_loop`, line 142) and SSE thread (`_sse_loop`, line
209); both fire callbacks on the worker thread, so anything UI-bound has
to hop back via `UiThreadInvoker`.

The synchronous helper `_call(method, params)` (line 296) returns the
parsed JSON-RPC response or `None` on any failure (connect error, HTTP
error, JSON decode error, timeout). Errors are silenced with a debug
log — by design, so a missing Jamulus 3.9 client doesn't spam the UI.

**1. Add the new command** alongside `set_channel_gain` (line 116):

```python
def set_channel_pan(self, channel_id: int, pan_0_to_100: int) -> bool:
    """Set stereo pan for ``channel_id``. 0=left, 50=center, 100=right."""
    pan_rpc = max(0, min(self.GAIN_RANGE_MAX,
                         int(pan_0_to_100 / 100.0 * self.GAIN_RANGE_MAX)))
    result = self._call("jamulus/setChannelPan", {
        "channelId": channel_id,
        "pan": pan_rpc,
    })
    return result is not None
```

**2. Fire-and-forget from the controller.** UI-triggered calls must not
block the Qt thread. Mirror `_send_rpc_gain` in `jamulus_controller.py`
(line 336) — wrap the call in a daemon thread:

```python
def _send_rpc_pan(self, channel_id: int, pan: int) -> None:
    if not self.rpc_client.available:
        return
    def _go() -> None:
        try:
            self.rpc_client.set_channel_pan(channel_id, pan)
        except Exception:
            pass
    threading.Thread(target=_go, daemon=True).start()
```

Call `_send_rpc_pan` from `set_pan` the same way `set_fader_level` calls
`_send_rpc_gain`. Errors propagate as a debug log inside `_call`; the UI
keeps moving. This is intentional — Jamulus may not be running yet.

### Tutorial 3: Wiring a new keyboard shortcut

Window-bound shortcuts and controller-bound shortcuts use slightly
different patterns. Both are registered in
`webjam_qt/windows/conductor_window.py::_setup_shortcuts` (line 139).

**Pattern A — handled inside the window** (e.g. F11 toggles fullscreen).
Pass the callback directly to the `QShortcut` constructor:

```python
QShortcut(QKeySequence("Ctrl+X"), self, self._do_something)
```

**Pattern B — consumed by the controller** (e.g. Ctrl+S for save mix).
Store the shortcut as `self._whatever_shortcut`, then connect its
`activated` signal in `application_controller.py::_wire_signals`
(line 154):

```python
# conductor_window.py — _setup_shortcuts
self._reset_faders_shortcut = QShortcut(QKeySequence("Ctrl+R"), self)
```

```python
# application_controller.py — _wire_signals
self.window._reset_faders_shortcut.activated.connect(self._on_reset_faders)
```

Both patterns are already in use side-by-side in `_setup_shortcuts`:
F11/F1/Esc use Pattern A; Ctrl+S, Ctrl+O, Ctrl+M, Ctrl+Shift+M, Ctrl+,
all use Pattern B.

**Update the F1 help dialog.** Add a row to the body string in
`ConductorWindow._show_help` (line 164) so users discover the new key:

```python
"&nbsp;&nbsp;<b>Ctrl+R</b> — Reset all faders to 0 dB<br>"
```

**Update the README table.** The user-facing list lives at the
"Qt Conductor Keyboard Shortcuts" section of `README.md` (line 75) —
add a row there too so the docs match the help dialog.
