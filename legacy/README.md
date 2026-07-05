# legacy/ — the retired Tkinter app

Everything here is the pre-Qt WebJam, kept for reference only. The live
app is the Qt Conductor (`webjam_qt/`, entry `webjam_qt_main.py`).

**Do not add features here.** This folder is excluded from CI tests and
lint; the code still runs if you need it:

```bash
python legacy/webjam_app_enhanced.py   # needs python3-tk
pytest legacy/tests/                    # legacy test suite (needs tkinter)
```

Contents: `webjam_app_enhanced.py` / `webjam_app.py` (Tkinter UIs),
`ui/` (Tkinter widgets/services — the live `ui/services.py` stayed at the
repo root), `admin/` (admin panel + policy engine), `session_templates.py`,
the old installer/launcher scripts, and their tests.

Removal is planned once nobody has needed this folder for a while.
