"""Focused regressions for WebJam's original triad mark and app icon."""

from __future__ import annotations

import os
import re
import struct
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

from core.settings import AppSettings  # noqa: E402
from webjam_qt.theme.brand import (  # noqa: E402
    BRAND_DESCRIPTION,
    BRAND_MARK_PATH,
    BRAND_NAME,
    BrandMark,
    make_brand_icon,
    render_brand_pixmap,
)
from webjam_qt.theme.tokens import Color  # noqa: E402
from webjam_qt.widgets.session_strip import SessionStrip  # noqa: E402
from webjam_qt.windows.launch_dialog import LaunchDialog  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "webjam_qt" / "theme" / "assets"


def _qapp() -> QApplication:
    return QApplication.instance() or QApplication(sys.argv[:1])


def test_svg_is_an_original_three_path_one_color_mark():
    source = BRAND_MARK_PATH.read_text(encoding="utf-8")
    assert len(re.findall(r"<path\b", source)) == 3
    assert set(re.findall(r"#[0-9A-Fa-f]{6}", source)) == {"#BF5700"}
    assert "<image" not in source
    assert "webex" not in source.lower()
    assert "jamulus" not in source.lower()
    assert "logic" not in source.lower()
    assert "<title>WebJam</title>" in source
    assert "conversation, live music, and production" in source


def test_mark_remains_legible_and_one_color_at_small_sizes():
    _qapp()
    expected = QColor(Color.ACCENT_PRIMARY)
    for size in (16, 24, 32):
        pixmap = render_brand_pixmap(size)
        assert not pixmap.isNull()
        image = pixmap.toImage()
        visible = []
        opaque = []
        for y in range(size):
            for x in range(size):
                pixel = image.pixelColor(x, y)
                if pixel.alpha() > 0:
                    visible.append(pixel)
                if pixel.alpha() == 255:
                    opaque.append(pixel)
        assert len(visible) >= size
        assert opaque
        assert image.pixelColor(size // 2, size // 2).alpha() == 0
        # Partially transparent antialias-edge pixels are stored premultiplied
        # and can round by a few RGB values when Qt expands them again. The
        # fully covered stroke pixels must remain the exact authored color.
        for pixel in opaque:
            assert (pixel.red(), pixel.green(), pixel.blue()) == (
                expected.red(),
                expected.green(),
                expected.blue(),
            )


def test_brand_widget_has_accessible_identity_and_plain_text_fallback():
    _qapp()
    mark = BrandMark(28)
    assert mark.has_vector_mark()
    assert mark.text() == ""
    assert mark.accessibleName() == BRAND_NAME
    assert mark.accessibleDescription() == BRAND_DESCRIPTION
    assert mark.focusPolicy().name == "NoFocus"

    fallback = BrandMark(28, _svg_data=b"not an svg")
    assert not fallback.has_vector_mark()
    assert fallback.text() == BRAND_NAME
    assert fallback.accessibleName() == BRAND_NAME


def test_session_header_replaces_the_wj_placeholder_with_the_mark():
    _qapp()
    strip = SessionStrip(
        mode_entries=[("music_jam", "Music Jam")],
        initial_mode_key="music_jam",
        initial_title="Band Rehearsal",
    )
    assert isinstance(strip._logo, BrandMark)
    assert strip._logo.has_vector_mark()
    assert strip._logo.accessibleName() == "WebJam"
    assert strip._logo.text() != "WJ"


def test_launch_dialog_uses_the_three_path_mark_without_an_abbreviation(tmp_path):
    _qapp()
    dialog = LaunchDialog(AppSettings(config_file=str(tmp_path / "settings.json")))
    assert isinstance(dialog._logo, BrandMark)
    assert dialog._logo.has_vector_mark()
    assert dialog._logo.accessibleName() == "WebJam"
    assert dialog._logo.text() not in {"WJ", "WEBJAM"}
    assert all(
        label.text() not in {"WJ", "WEBJAM"}
        for label in dialog.findChildren(QLabel)
    )
    dialog.close()


def test_runtime_and_packaged_icons_share_the_brand_asset():
    _qapp()
    icon = make_brand_icon()
    assert not icon.isNull()
    available = {size.width() for size in icon.availableSizes()}
    assert {16, 32, 128, 256}.issubset(available)

    ico = ASSETS / "webjam.ico"
    icns = ASSETS / "webjam.icns"
    assert ico.read_bytes()[:4] == struct.pack("<HH", 0, 1)
    assert icns.read_bytes()[:4] == b"icns"


def test_pyinstaller_bundles_svg_and_uses_platform_icons():
    spec = (ROOT / "webjam.spec").read_text(encoding="utf-8")
    assert '"webjam_qt" / "theme" / "assets"' in spec
    assert '"PySide6.QtSvg"' in spec
    assert '"webjam.ico"' in spec
    assert '"webjam.icns"' in spec
