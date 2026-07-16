"""Focused regressions for WebJam's native trinity mark and app icon."""

from __future__ import annotations

import os
import re
import struct
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

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
from webjam_qt.widgets.session_strip import SessionStrip  # noqa: E402
from webjam_qt.windows.launch_dialog import LaunchDialog  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "webjam_qt" / "theme" / "assets"
_APP: QApplication | None = None


def _qapp() -> QApplication:
    # Keep the Python wrapper alive for the whole module. PySide can destroy
    # the native application with the last temporary wrapper, leaving a later
    # widget construction to segfault instead of reporting a normal test error.
    global _APP
    _APP = QApplication.instance() or QApplication(sys.argv[:1])
    return _APP


def test_svg_is_a_portable_warm_trinity_companion():
    source = BRAND_MARK_PATH.read_text(encoding="utf-8")
    assert len(re.findall(r"<path\b", source)) == 3
    assert "linearGradient" in source
    assert "radialGradient" in source
    assert len(re.findall(r"<circle\b", source)) == 6
    colors = set(re.findall(r"#[0-9A-Fa-f]{6}", source))
    assert {"#BF5700", "#F06A00", "#E87900", "#0A0A0A"}.issubset(colors)
    assert "<image" not in source
    assert "webex" not in source.lower()
    assert "jamulus" not in source.lower()
    assert "logic" not in source.lower()
    assert "<title>WebJam</title>" in source
    assert "Three linked loops for musicians playing together" in source


def test_mark_remains_legible_and_warm_at_small_sizes():
    _qapp()
    for size in (16, 24, 28, 32):
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
        saturated = [
            pixel
            for pixel in opaque
            if max(pixel.red(), pixel.green(), pixel.blue())
            - min(pixel.red(), pixel.green(), pixel.blue())
            > 28
        ]
        assert saturated
        # Orange ribbons have red as their dominant channel and blue as the
        # quietest one. Neutral node centers are intentionally excluded.
        assert all(
            pixel.red() > pixel.green() > pixel.blue()
            for pixel in saturated
        )


def test_mark_has_three_circular_dark_centered_nodes():
    _qapp()
    size = 96
    image = render_brand_pixmap(size).toImage()
    for x_ratio, y_ratio in ((0.50, 0.15), (0.846, 0.75), (0.154, 0.75)):
        pixel = image.pixelColor(round(size * x_ratio), round(size * y_ratio))
        assert pixel.alpha() == 255
        assert max(pixel.red(), pixel.green(), pixel.blue()) <= 16


def test_mark_is_retina_safe_and_does_not_depend_on_the_svg_at_runtime(monkeypatch, tmp_path):
    _qapp()
    import webjam_qt.theme.brand as brand

    monkeypatch.setattr(brand, "BRAND_MARK_PATH", tmp_path / "missing.svg")
    pixmap = brand.render_brand_pixmap(28, device_pixel_ratio=2.0)
    assert not pixmap.isNull()
    assert pixmap.width() == 56
    assert pixmap.height() == 56
    assert pixmap.devicePixelRatio() == 2.0


def test_brand_widget_has_accessible_identity_and_native_vector_rendering():
    _qapp()
    mark = BrandMark(28)
    assert mark.has_vector_mark()
    assert mark.text() == ""
    assert mark.accessibleName() == BRAND_NAME
    assert mark.accessibleDescription() == BRAND_DESCRIPTION
    assert mark.focusPolicy().name == "NoFocus"
    assert mark.width() == 28
    assert mark.height() == 28


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


def test_launch_dialog_uses_the_trinity_mark_without_an_abbreviation(tmp_path):
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


def test_packaging_keeps_the_vector_companion_and_platform_icons():
    spec = (ROOT / "webjam.spec").read_text(encoding="utf-8")
    brand_source = (ROOT / "webjam_qt" / "theme" / "brand.py").read_text(
        encoding="utf-8"
    )
    assert '"webjam_qt" / "theme" / "assets"' in spec
    assert "draw_brand_mark" in brand_source
    assert "QSvgRenderer" not in brand_source
    assert '"webjam.ico"' in spec
    assert '"webjam.icns"' in spec
