# Developing WebJam v0.17.0

## Local setup

Use the repository virtual environment:

```bash
.venv/bin/ruff check webjam_qt/ core/ ui/ services/ api/
.venv/bin/python -m py_compile webjam_qt/controllers/application_controller.py
.venv/bin/pytest -q
```

For Studio arrangement changes, run the focused model, persistence, history,
controller, renderer, comping, waveform, export, and Qt integration modules in
addition to the full suite. The physical-output and external-editor gates in
`TEST_PROCEDURE.md` are separate and currently **NOT RUN** for v0.17.0.

Normal app development starts from Host/Join. Do not make a new startup path
that asks WebJam to choose Jamulus devices, channels, sample rate, buffers, or
jitter settings.

## Dual-musician rehearsal lab

Run the hardware-free source gate when changing host/guest recording,
transfer, Studio, export, or cleanup behavior:

```bash
.venv/bin/python -m pytest -q tests/test_dual_musician_rehearsal_lab.py
```

It uses isolated pytest artifacts and synthetic capture only; the separate
Linux/JACK real-Jamulus companion and its evidence boundary are documented in
[Dual-musician rehearsal lab](DUAL_MUSICIAN_REHEARSAL_LAB.md).

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

## Build and release hygiene

The source tree reports `0.17.0`; no package with that version is promoted.
The current published rollback/reference package remains:

- Primary published artifact in GitHub Releases:
  [`v0.16.3`](https://github.com/rupret007/webjam/releases/tag/v0.16.3) /
  `WebJam-v0.16.3-RC-4d8c046-windows-x64-setup.exe`.

The authoritative local build command remains:

```bash
.venv/bin/python -m PyInstaller --clean --noconfirm webjam.spec
```

Use the macOS staging/signing/transport verification in `.github/workflows/ci.yml`.
Do not use the retired `build_webjam.py` release path. Package and visual
verification are required before replacing the installed test-night app. The
cross-platform support boundary, automated gates, and physical-hardware release
checklist are in [Desktop release runbook](docs/DESKTOP_RELEASE_RUNBOOK.md).
