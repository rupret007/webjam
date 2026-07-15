# Developing WebJam v0.16

## Local setup

Use the repository virtual environment:

```bash
.venv/bin/ruff check webjam_qt/ core/ ui/ services/ api/
.venv/bin/python -m py_compile webjam_qt/controllers/application_controller.py
.venv/bin/pytest -q
```

Normal app development starts from Host/Join. Do not make a new startup path
that asks WebJam to choose Jamulus devices, channels, sample rate, buffers, or
jitter settings.

## Integration rules

- Launch Jamulus directly and visibly; do not use `--nogui` for the musician
  client.
- Use the supported dedicated `--inifile WebJam-native-v0.16.ini` contract.
- Never write that profile’s content or the musician’s normal `Jamulus.ini`.
- Do not automate Jamulus through screen coordinates, pixel inspection, or
  window-text scraping.
- JSON-RPC is for process, authentication, roster, connection, chat, and
  recorder facts—not device configuration.
- Keep Webex external and truthful: opening a URL is not a joined/muted claim.
- Keep Local Originals behind explicit Recording Setup and Studio output in
  Studio.

## UI rules

Use black, white, neutral gray, and burnt orange only. The native three-loop
brand mark lives in `webjam_qt/theme/brand.py`; regenerate `.icns` and `.ico`
with:

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m webjam_qt.theme.generate_brand_icons
```

The normal session surface has one dominant next action. Avoid adding device
forms, server fields, or technical diagnostics to Host/Join.

## Packaging

The authoritative build is:

```bash
.venv/bin/python -m PyInstaller --clean --noconfirm webjam.spec
```

Use the macOS staging/signing/transport verification in `.github/workflows/ci.yml`.
Do not use the retired `build_webjam.py` release path. Package and visual
verification are required before replacing the installed test-night app.
