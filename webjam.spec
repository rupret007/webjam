# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for WebJam
#
# Build a single-directory app bundle:
#   pyinstaller webjam.spec
#
# Build a one-file executable (slower startup):
#   pyinstaller webjam.spec --onefile
#
# macOS — sign and notarize after building:
#   codesign --deep --force --verify --verbose \
#     --sign "Developer ID Application: Your Name (TEAMID)" \
#     dist/WebJam.app
#   xcrun altool --notarize-app --primary-bundle-id "com.webjam.app" \
#     --username your@apple.id --password @keychain:AC_PASSWORD \
#     --file dist/WebJam.app
#
#   CAVEAT: dist/WebJam.app/Contents/Resources/Jamulus.app (bundled by CI,
#   see .github/workflows/ci.yml) is ALREADY signed and notarized by the
#   Jamulus project itself. `codesign --deep` re-signs every nested bundle
#   it finds, which would overwrite that existing Developer ID signature
#   with your own and invalidate Jamulus's notarization ticket. If signing
#   locally (CI does not currently automate macOS signing), either drop
#   `--deep` and sign nested non-Apple binaries individually first
#   (bottom-up), or re-`ditto` a fresh unsigned-by-you copy of Jamulus.app
#   back in after your codesign step completes.
#
# Windows — sign after building:
#   signtool sign /a /fd SHA256 /tr http://timestamp.sectigo.com /td SHA256 \
#     dist\WebJam\WebJam.exe

import re
import sys
from pathlib import Path

ROOT = Path(SPECPATH)

block_cipher = None

# Keep the bundle version in sync with webjam_qt.__version__ (single source of
# truth) instead of hardcoding it — the macOS Info.plist below used to be stuck
# at 0.3.0.
_init_src = (ROOT / "webjam_qt" / "__init__.py").read_text(encoding="utf-8")
_m = re.search(r'__version__\s*=\s*"([^"]+)"', _init_src)
VERSION = _m.group(1) if _m else "0.0.0"

# On Windows, bundle the VB-CABLE installers so the app folder really does
# contain the virtual-audio-cable setup the band guide points users to.
# (Useless on macOS — that uses BlackHole — so only include it on Windows.)
_extra_datas = []
if sys.platform == "win32":
    _vb_dir = ROOT / "VB"
    if _vb_dir.is_dir():
        for _p in _vb_dir.iterdir():
            if _p.is_file():
                _extra_datas.append((str(_p), "VB"))

    # Bundle the official Jamulus Windows installer (staged by CI at build
    # time into a repo-root Jamulus/ dir — see .github/workflows/ci.yml —
    # absent in plain dev checkouts, matching the VB/ pattern above) so the
    # Setup Wizard can offer an "Install Jamulus now" button instead of
    # sending users to jamulus.io. Jamulus has no portable Windows binary
    # to bundle directly (unlike macOS's Jamulus.app — see BUNDLE below),
    # so this ships the unmodified upstream installer, not a runnable copy.
    _jamulus_dir = ROOT / "Jamulus"
    if _jamulus_dir.is_dir():
        for _p in _jamulus_dir.iterdir():
            if _p.is_file():
                _extra_datas.append((str(_p), "Jamulus"))
        _jamulus_license = ROOT / "licenses" / "JAMULUS_COPYING.txt"
        if _jamulus_license.is_file():
            _extra_datas.append((str(_jamulus_license), "Jamulus"))

a = Analysis(
    [str(ROOT / "webjam_qt_main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        # QSS stylesheet and theme assets
        (str(ROOT / "webjam_qt" / "theme" / "conductor.qss"), "webjam_qt/theme"),
        # Webex widget HTML template
        (str(ROOT / "webjam_qt" / "webex_widget.html"), "webjam_qt"),
        *_extra_datas,
    ],
    hiddenimports=[
        # PySide6 WebEngine (not always auto-discovered)
        "PySide6.QtWebEngineWidgets",
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebChannel",
        # Core modules
        "core.settings",
        "core.audio_engine",
        "core.audio_routing",
        "core.jamulus_protocol",
        "core.jamulus_rpc_client",
        "core.webex_guest_token",
        "services.bridge_service",
        "storage.repository",
        "ui.services",
        "core.file_io",
        "api.local_bridge",
        "core.take_library",
        "core.take_player",
        "core.jamulus_server_rpc",
        "webjam_qt.windows.take_deck",
        "soundfile",
        # Optional heavy deps — suppress import errors if absent
        "sounddevice",
        "numpy",
        "httpx",
        "sentry_sdk",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Keep bundle lean
        "tkinter",
        "customtkinter",
        "matplotlib",
        "PIL",
        "IPython",
        "jupyter",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="WebJam",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # icon="webjam_qt/theme/webjam.icns" if sys.platform == "darwin" else
    #      "webjam_qt/theme/webjam.ico",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="WebJam",
)

if sys.platform == "darwin":
    # NOTE: the bundled Jamulus.app (macOS's zero-install Jamulus bundling —
    # see THIRD_PARTY_NOTICES.md) is NOT added here as a datas/BUNDLE entry.
    # PyInstaller's BUNDLE() copies file *contents* it controls; Jamulus.app
    # must be `ditto`'d in whole and unmodified (to preserve its Apple code
    # signature) *after* this BUNDLE() call produces dist/WebJam.app — see
    # the "Stage bundled Jamulus.app" + "Build desktop artifact" steps in
    # .github/workflows/ci.yml.
    app = BUNDLE(
        coll,
        name="WebJam.app",
        # icon="webjam_qt/theme/webjam.icns",
        bundle_identifier="com.webjam.app",
        info_plist={
            "NSMicrophoneUsageDescription":
                "WebJam uses the microphone through Jamulus for low-latency audio.",
            "NSCameraUsageDescription":
                "WebJam uses the camera for the embedded Webex video conference.",
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "12.0",
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
        },
    )
