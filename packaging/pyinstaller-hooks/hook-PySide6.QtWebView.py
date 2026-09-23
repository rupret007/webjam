"""Collect Paint along's platform backend; PyInstaller 6.21 has no hook yet."""
import sys
from PyInstaller.utils.hooks.qt import pyside6_library_info

hiddenimports, binaries, datas = pyside6_library_info.collect_module("PySide6.QtWebView")
backend = "qtwebview_darwin" if sys.platform == "darwin" else "qtwebview_webengine"
binaries = [(source, target) for source, target in binaries
            if "webview" not in target.lower() or backend in source.lower()]
if not any(backend in source.lower() for source, _ in binaries):
    raise RuntimeError("Paint along's Qt WebView backend is missing: " + backend)
if sys.platform != "darwin":
    # The WebView plugin links these natively; explicit hooks collect its
    # subprocess, resources and locale files as well as the Python extension.
    hiddenimports += ["PySide6.QtWebEngineCore", "PySide6.QtWebEngineQuick"]
