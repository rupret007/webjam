# WebJam Development Setup

Guide for setting up a development environment on Windows (or macOS/Linux).

## Prerequisites

| Tool | Version | Notes |
|------|---------|-------|
| Python | 3.10+ | Download from https://www.python.org/downloads/ |
| Git | Latest | https://git-scm.com/downloads |
| Jamulus | 3.9+ | **Install separately for development** — free at [jamulus.io](https://jamulus.io). Downloadable release *builds* bundle Jamulus (macOS: zero-install nested app; Windows: bundled installer via the Setup Wizard — see `THIRD_PARTY_NOTICES.md`), but that bundling happens at PyInstaller build time and has no effect when running from source with `python webjam_qt_main.py`. |
| VB-Cable | Latest | Optional, Windows only — advanced audience-bridge mode; not musician talkback |

When installing Python on Windows, check **"Add python.exe to PATH"** during the installer.

## Clone the Repository

**From GitHub:**

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
python legacy/webjam_app_enhanced.py  # Legacy Tkinter UI (archive/fallback)
```

On first launch a setup wizard runs automatically to configure the Jamulus
server, Webex URL, Webex audio role, and optional supplemental local capture.
Only audience-bridge mode scans for a loopback device. You can reopen Setup
with **Ctrl+,** or the Settings button in the left rail.

Testing macOS in-app hosting additionally requires the official dedicated
`/Applications/JamulusServer.app` 3.12.2. It is not bundled in WebJam. Use
`tests/test_hosted_server.py` for the ownership/adoption matrix; the manual
hardware lifecycle in `TEST_PROCEDURE.md` must use the server app's sandbox
container for its secret and recordings.

## Run Tests

The project uses `pytest`. Run the full suite:

```bash
python -m pytest tests/ -v
```

Expected result: all tests pass (800+ pass, plus platform-dependent skips). The skips are mostly platform-specific tests that auto-skip when the host cannot run them. See `CHANGELOG.md` for the exact count as of the latest release — it grows with nearly every change, so treat any hardcoded number as approximate.

### Running tests locally (CI-equivalent)

CI runs the suite headlessly on every push. To match the `test` job's
"Run test suite" step exactly (see `.github/workflows/ci.yml`):

```bash
QT_QPA_PLATFORM=offscreen python3 -m pytest tests/ -v
```

A single failing test fails the CI job, so always run this before pushing.

### Code style (ruff)

CI runs `ruff check` against the first-party source roots. Match that gate locally:

```bash
python3 -m ruff check webjam_qt/ core/ ui/ services/ api/
```

Fix every warning before you commit — the lint job is the first to fail
on a dirty PR, and it blocks the build / release jobs that follow.

### Running the UX smoke gate

`ux_smoke_test.py` boots the Qt Conductor window headlessly and asserts
the shell wires up without raising. It runs in CI between lint and the
test suite (the `test` job's "Run UX smoke gate" step):

```bash
python3 ux_smoke_test.py
```

On macOS / Linux this needs a working Qt platform plugin — set
`QT_QPA_PLATFORM=offscreen` in the environment if you don't have a
display attached (the CI workflow does the same).

## Build a Standalone Executable

```bash
pip install pyinstaller

python -m PyInstaller --clean --noconfirm webjam.spec
# Produces dist/WebJam/WebJam.exe (Windows) or dist/WebJam.app (macOS)
```

`build_webjam.py` is legacy installer tooling and is not the pilot-release build path.

## Environment Variables

Override defaults without editing code:

| Variable | Default | Description |
|----------|---------|-------------|
| `WEBJAM_JAMULUS_SERVER` | empty | Jamulus server hostname or IP |
| `WEBJAM_JAMULUS_PORT` | `22124` | Jamulus server port |
| `WEBJAM_WEBEX_URL` | empty | HTTPS `webex.com` meeting URL |
| `WEBJAM_WEBEX_AUDIO_MODE` | `talkback` | `talkback`, `video_only`, or `audience_bridge` |
| `WEBJAM_LOCAL_CAPTURE_ENABLED` | `false` | Enable supplemental local input capture independently of Webex mode |
| `WEBJAM_JAMULUS_CANDIDATES` | (macOS + Windows default paths) | Semicolon-separated Jamulus executable paths |
| `WEBJAM_ENABLE_SENTRY` | `false` | Enable Sentry error reporting |
| `WEBJAM_LOG_LEVEL` | `INFO` | Logging level |

## Project Structure

```
webjam_qt_main.py          Primary entry point — Qt Conductor UI
webjam_qt/                 Qt application (windows, widgets, controllers)
legacy/                    Quarantined Tkinter/customtkinter UI and old installer
core/                      Settings, models, creative modes, templates, protocol
storage/                   SQLite repository for users, settings, canvas, audit
ui/                        Auth controller, services, dialogs, views, theme
api/                       Optional FastAPI companion API
tests/                     Unit and edge-case test modules
VB/                        VB-Cable driver INFs (Windows audio routing)
.github/workflows/ci.yml   CI: lint, UX smoke, tests, real Jamulus, desktop builds
```

## Windows-Specific Notes

- **VB-Cable**: bundled installers support advanced audience-bridge mode only.
  Normal musician talkback uses native Webex plus Jamulus and needs no virtual cable.
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

The RPC client lives in `core/jamulus_rpc_client.py`. It speaks real
Jamulus JSON-RPC: newline-delimited JSON-RPC 2.0 over a single raw TCP
socket, authenticated with `jamulus/apiAuth`. `start()` spawns one
background reader thread (`_run_loop` → `_serve_once`) that connects,
authenticates, then loops reading NDJSON lines and dispatching them;
callbacks (`on_participants_changed`, `on_levels`, etc.) fire on that
worker thread, so anything UI-bound has to hop back via
`UiThreadInvoker`.

Commands are fire-and-forget: `_send(method, params)` writes one
JSON-RPC request and returns the request id it assigned (or `None` if
there's no live socket) — it does **not** wait for or return the
response. Failures are silenced with a debug log — by design, so a
missing/older Jamulus client doesn't spam the UI.

**1. Add the new command** alongside `set_channel_gain`:

```python
def set_channel_pan(self, channel_id: int, pan_0_to_100: int) -> bool:
    """Set stereo pan for ``channel_id``. 0=left, 50=center, 100=right."""
    pan = max(0, min(100, int(pan_0_to_100)))
    return self._send("jamulusclient/setChannelPan", {
        "channelIndex": channel_id,
        "pan": pan,
    }) is not None
```

(Confirm the exact method name and param shape against
[JSON-RPC.md](https://github.com/jamulussoftware/jamulus/blob/main/docs/JSON-RPC.md)
before shipping — `setChannelPan` is illustrative here, not a method
WebJam currently calls.)

**2. Fire-and-forget from the controller.** UI-triggered calls must not
block the Qt thread. Mirror `_send_rpc_gain` in `jamulus_controller.py`
— wrap the call in a daemon thread:

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
`_send_rpc_gain`. Failures are silenced with a debug log inside `_send`;
the UI keeps moving. This is intentional — Jamulus may not be running yet.

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
self._reset_faders_shortcut = QShortcut(QKeySequence("Ctrl+Shift+R"), self)
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
"&nbsp;&nbsp;<b>Ctrl+Shift+R</b> — Reset all faders to 0 dB<br>"
```

**Update the README table.** The user-facing list lives at the
"Qt Conductor Keyboard Shortcuts" section of `README.md` (line 75) —
add a row there too so the docs match the help dialog.
