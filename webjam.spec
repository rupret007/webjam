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
# macOS production release: the protected CI job takes the tested ad-hoc source
# bundle, re-signs every collected Mach-O plus the staged Jamulus and transport
# code bottom-up with Developer ID and component-specific entitlements, then
# shallow-signs WebJam.app last. It packages with ditto and a final DMG, uses
# modern notarytool, and staples the accepted tickets:
#   xcrun notarytool submit WebJam-macos-x64.zip \
#     --key AuthKey_ID.p8 --key-id KEY_ID --issuer ISSUER_ID --wait
#   xcrun stapler staple dist/WebJam.app
#   xcrun stapler validate dist/WebJam.app
# Candidate tags publish only the tested unsigned/ad-hoc artifacts. Protected
# Developer ID signing remains an explicit, environment-gated manual rehearsal.
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

import os
import re
import subprocess
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
MACOS_APP_DATA_USAGE_DESCRIPTION = (
    "WebJam accesses Jamulus app data only for dedicated WebJam profiles and "
    "private Reference Track audio-route and control files. It never reads or "
    "changes your regular Jamulus profile."
)

# Capture one non-personal provenance value for privacy-safe support bundles.
# CI can provide WEBJAM_BUILD_ID explicitly; local builds use the exact Git
# HEAD.  The generated file lives under build/ and is bundled as data rather
# than mutating tracked source files.
_build_id = str(os.environ.get("WEBJAM_BUILD_ID", "") or "").strip()
if not re.fullmatch(r"[0-9a-fA-F]{7,64}", _build_id):
    try:
        _probe = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            check=False,
            text=True,
            timeout=3,
        )
        _build_id = _probe.stdout.strip() if _probe.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        _build_id = ""
_build_info_path = ROOT / "build" / "webjam-build-id.txt"
_build_info_path.parent.mkdir(parents=True, exist_ok=True)
_build_info_path.write_text(
    (_build_id.lower() if re.fullmatch(r"[0-9a-fA-F]{7,64}", _build_id) else "")
    + "\n",
    encoding="ascii",
)

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
# The Windows Jamulus installer is PyInstaller data because the Launch dialog
# resolves it through ``sys._MEIPASS``. Linux gets a visible archive-root
# ``Jamulus/`` distribution dependency after PyInstaller finishes; macOS gets
# runnable nested app bundles after PyInstaller finishes.
_extra_datas = []
if sys.platform == "win32":
    _vb_dir = ROOT / "VB"
    if _vb_dir.is_dir():
        for _p in _vb_dir.iterdir():
            if _p.is_file():
                _extra_datas.append((str(_p), "VB"))

if sys.platform == "win32":
    # Bundle the checksum-pinned official installer staged by CI. PyInstaller
    # places it below ``_internal/Jamulus`` in the onedir build.
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
        # Portable WebJam vector companion plus packaged OS icon containers.
        (str(ROOT / "webjam_qt" / "theme" / "assets"), "webjam_qt/theme/assets"),
        # Bundled Inter typeface (OFL — licenses/INTER_OFL.txt)
        (str(ROOT / "webjam_qt" / "theme" / "fonts"), "webjam_qt/theme/fonts"),
        (str(ROOT / "licenses" / "INTER_OFL.txt"), "THIRD_PARTY_LICENSES"),
        (
            str(ROOT / "licenses" / "CRYPTOGRAPHY_LICENSE.txt"),
            "THIRD_PARTY_LICENSES",
        ),
        (
            str(ROOT / "licenses" / "WEBSOCKETS_LICENSE.txt"),
            "THIRD_PARTY_LICENSES",
        ),
        (
            str(ROOT / "licenses" / "SEGNO_LICENSE.txt"),
            "THIRD_PARTY_LICENSES",
        ),
        # Deterministic, lock-derived Python/runtime attribution and CycloneDX
        # inventory. The release gate regenerates and compares both before
        # PyInstaller runs, then verifies these exact files in the final bundle.
        (
            str(ROOT / "THIRD_PARTY_NOTICES.md"),
            "THIRD_PARTY_LICENSES",
        ),
        (
            str(ROOT / "THIRD_PARTY_NOTICES_RUNTIME.md"),
            "THIRD_PARTY_LICENSES",
        ),
        (
            str(ROOT / "packaging" / "WebJam-runtime-sbom.cdx.json"),
            "THIRD_PARTY_LICENSES",
        ),
        (
            str(ROOT / "packaging" / "Jamulus-component-sbom.cdx.json"),
            "THIRD_PARTY_LICENSES",
        ),
        (
            str(ROOT / "packaging" / "runtime-dependency-policy.json"),
            "THIRD_PARTY_LICENSES",
        ),
        (
            str(ROOT / "licenses" / "JAMULUS_COPYING-r3_12_3.txt"),
            "THIRD_PARTY_LICENSES",
        ),
        (
            str(ROOT / "licenses" / "SOUNDFILE_LICENSE.txt"),
            "THIRD_PARTY_LICENSES",
        ),
        (
            str(ROOT / "licenses" / "SOUNDFILE_WHEEL_LICENSE_NOTES.md"),
            "THIRD_PARTY_LICENSES",
        ),
        # The native transport is staged beside the main executable after
        # PyInstaller so it can be process-owned without PATH lookup. Its
        # license inventory is ordinary bundle data and ships on every target.
        (str(ROOT / "transport" / "NOTICE.md"), "THIRD_PARTY_LICENSES"),
        (
            str(ROOT / "transport" / "DEPENDENCIES.md"),
            "THIRD_PARTY_LICENSES",
        ),
        (
            str(ROOT / "transport" / "licenses"),
            "THIRD_PARTY_LICENSES/transport",
        ),
        (str(_build_info_path), "."),
        *_extra_datas,
    ],
    hiddenimports=[
        # Core modules
        "core.settings",
        "core.audio_engine",
        "core.audio_routing",
        "core.jamulus_protocol",
        "core.jamulus_rpc_client",
        "services.bridge_service",
        "services.native_remote_transport",
        "services.remote_invitation_owner",
        "services.remote_session_runtime",
        "services.transport_runtime",
        "storage.repository",
        "ui.services",
        "core.file_io",
        "api.local_bridge",
        "core.take_library",
        "core.take_player",
        "core.take_export",
        "core.studio_state",
        # Reference Studio is reached through a function-local import from the
        # application controller. Keep its complete project/audio graph
        # explicit so frozen builds cannot silently omit a late-imported
        # dialog, persistence service, recording bridge, or mix engine.
        "core.song_project",
        "core.song_project_store",
        "core.song_project_controller",
        "core.song_media_catalog",
        "core.song_studio_store",
        "core.song_studio_controller",
        "core.song_studio_reconcile",
        "core.song_studio_clone",
        "core.project_audio",
        "core.project_playback",
        "core.project_recording",
        "core.project_recording_commit",
        "core.project_tempo_analysis",
        "core.song_bounce",
        "core.studio_tempo",
        "core.studio_mixer",
        "webjam_qt.controllers.reference_studio_application",
        "webjam_qt.widgets.reference_studio_shell",
        "webjam_qt.widgets.reference_studio_workspace",
        "webjam_qt.widgets.studio_project_home",
        "webjam_qt.widgets.studio_waveforms",
        "webjam_qt.windows.reference_studio_tools",
        "webjam_qt.windows.reference_studio_mixer",
        "services.reference_studio_packaged_smoke",
        "services.jamulus_component_packaged_smoke",
        # The conductor is imported at normal startup; the private Test Night
        # ledger and dialog are intentionally imported only when an operator
        # invokes that hidden workflow.  Keep all three explicit so a frozen
        # candidate includes the complete v0.16 path even if module-graph
        # analysis changes how it follows function-local imports.
        "core.session_conductor",
        "core.pocket_stage",
        "core.reference_track",
        "core.pilot_evidence",
        "core.jamulus_server_rpc",
        "webjam_qt.windows.take_deck",
        "webjam_qt.widgets.recording_studio",
        "webjam_qt.windows.recording_setup",
        "webjam_qt.windows.test_night",
        "services.pocket_stage_gateway",
        "services.pocket_stage_packaged_smoke",
        "services.pocket_stage_tls",
        "services.reference_track_backend",
        "webjam_qt.windows.pocket_stage_pairing",
        "webjam_qt.windows.reference_track",
        "soundfile",
        # The frozen Jamulus updater constructs HTTPS trust from Certifi's
        # packaged CA bytes; do not let module-graph changes omit its hook/data.
        "certifi",
        # Optional heavy deps — suppress import errors if absent
        "sounddevice",
        "numpy",
        "sentry_sdk",
        # Pocket Stage's TLS identity, Uvicorn WSS backend, and pairing QR are
        # imported behind opt-in desktop flows. Keep them discoverable in a
        # frozen app; the maintained cryptography/websockets hooks collect
        # their compiled bindings and lazily loaded submodules.
        "cryptography",
        "websockets",
        "websockets.sync.client",
        "segno",
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
        # Webex is external-only. Do not ship an unused Chromium runtime,
        # WebChannel bridge, embedded meeting page, or Guest Issuer surface.
        "PySide6.QtWebChannel",
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineQuick",
        "PySide6.QtWebEngineWidgets",
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
    icon=(
        str(ROOT / "webjam_qt" / "theme" / "assets" / "webjam.ico")
        if sys.platform == "win32"
        else None
    ),
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
    # NOTE: the bundled Jamulus client/server apps and the separately built
    # true-HEADLESS Reference Track client (macOS zero-install — see
    # THIRD_PARTY_NOTICES.md) are NOT added here as datas/BUNDLE entries.
    # PyInstaller's BUNDLE() copies file *contents* it controls; CI must stage
    # the three complete bundles with `ditto` after this call produces
    # dist/WebJam.app. CI then verifies each nested signature and the HEADLESS
    # companion's provenance/checksum. Ordinary branch builds refresh the
    # ad-hoc outer seal; the protected release path re-signs every final code
    # object inside-out with Developer ID and seals WebJam.app last.
    app = BUNDLE(
        coll,
        name="WebJam.app",
        icon=str(ROOT / "webjam_qt" / "theme" / "assets" / "webjam.icns"),
        bundle_identifier="com.webjam.app",
        info_plist={
            "NSMicrophoneUsageDescription":
                "WebJam uses your microphone or audio interface so your "
                "bandmates can hear you, and to show your input level or "
                "record when you choose.",
            "NSLocalNetworkUsageDescription":
                "WebJam connects your iPhone Pocket Stage and band session "
                "devices on your private local network when you choose.",
            "NSAppDataUsageDescription": MACOS_APP_DATA_USAGE_DESCRIPTION,
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
