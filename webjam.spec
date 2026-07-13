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
#   CAVEAT: CI stages the official Jamulus client/server apps under Resources,
#   then deep ad-hoc signs each nested app without the upstream App Sandbox
#   entitlement so WebJam can provision their command-line RPC files. Sign the
#   nested bundles first (bottom-up), then sign only the outer WebJam.app
#   without `--deep`; see .github/workflows/ci.yml and THIRD_PARTY_NOTICES.md.
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

# Give the Windows executable the same authoritative version metadata as the
# macOS bundle. CI reads ProductVersion from the built PE before packaging.
_windows_version_info = None
if sys.platform == "win32":
    from PyInstaller.utils.win32.versioninfo import (
        FixedFileInfo,
        StringFileInfo,
        StringStruct,
        StringTable,
        VarFileInfo,
        VarStruct,
        VSVersionInfo,
    )

    _version_parts = [int(part) for part in VERSION.split(".")[:4]]
    _version_parts.extend([0] * (4 - len(_version_parts)))
    _version_tuple = tuple(_version_parts)
    _windows_version_info = VSVersionInfo(
        ffi=FixedFileInfo(
            filevers=_version_tuple,
            prodvers=_version_tuple,
            mask=0x3F,
            flags=0x0,
            OS=0x40004,
            fileType=0x1,
            subtype=0x0,
            date=(0, 0),
        ),
        kids=[
            StringFileInfo([
                StringTable("040904B0", [
                    StringStruct("CompanyName", "WebJam"),
                    StringStruct("FileDescription", "WebJam"),
                    StringStruct("FileVersion", VERSION),
                    StringStruct("InternalName", "WebJam"),
                    StringStruct("OriginalFilename", "WebJam.exe"),
                    StringStruct("ProductName", "WebJam"),
                    StringStruct("ProductVersion", VERSION),
                ]),
            ]),
            VarFileInfo([VarStruct("Translation", [1033, 1200])]),
        ],
    )

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
        # Bundled Inter typeface (OFL — licenses/INTER_OFL.txt)
        (str(ROOT / "webjam_qt" / "theme" / "fonts"), "webjam_qt/theme/fonts"),
        (str(ROOT / "licenses" / "INTER_OFL.txt"), "THIRD_PARTY_LICENSES"),
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
        "core.take_export",
        "core.jamulus_server_rpc",
        "webjam_qt.windows.take_deck",
        "webjam_qt.widgets.recording_studio",
        "webjam_qt.windows.recording_setup",
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
    version=_windows_version_info,
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
    # NOTE: the bundled Jamulus client/server apps (macOS zero-install —
    # see THIRD_PARTY_NOTICES.md) is NOT added here as a datas/BUNDLE entry.
    # PyInstaller's BUNDLE() copies file *contents* it controls; Jamulus.app
    # must be staged with `ditto` after this call produces dist/WebJam.app.
    # CI then prepares each nested signature as documented above and finally
    # shallow-signs WebJam.app so the added resources enter its outer seal.
    app = BUNDLE(
        coll,
        name="WebJam.app",
        # icon="webjam_qt/theme/webjam.icns",
        bundle_identifier="com.webjam.app",
        info_plist={
            "NSMicrophoneUsageDescription":
                "WebJam uses your microphone or audio interface so your "
                "bandmates can hear you, and to show your input level or "
                "record when you choose.",
            "NSHighResolutionCapable": True,
            # The bundled Jamulus 3.12.2 client/server declare macOS 13.
            # Match that real floor so Finder never offers a launch that the
            # background music engine cannot complete.
            "LSMinimumSystemVersion": "13.0",
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "LSMultipleInstancesProhibited": True,
            "CFBundleURLTypes": [
                {
                    "CFBundleURLName": "com.webjam.invite",
                    "CFBundleTypeRole": "Viewer",
                    "CFBundleURLSchemes": ["webjam"],
                }
            ],
        },
    )
