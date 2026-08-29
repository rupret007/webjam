"""Focused regressions for WebJam's native trinity mark and app icon."""

from __future__ import annotations

import hashlib
import json
import os
import re
import struct
import sys
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

from core.settings import AppSettings  # noqa: E402
from webjam_qt.theme.brand import (  # noqa: E402
    BRAND_DESCRIPTION,
    BRAND_MARK_PATH,
    BRAND_NAME,
    BrandMark,
    make_brand_icon,
    render_brand_pixmap,
    trinity_svg_path_data,
)
from webjam_qt.windows.conductor_window import ConductorWindow  # noqa: E402
from webjam_qt.theme.generate_brand_icons import (  # noqa: E402
    _svg_source,
    generate_brand_assets,
)
from webjam_qt.theme.tokens import Color  # noqa: E402
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


def _assert_same_render(generated: Path, checked_in: Path) -> None:
    """Compare icon content without depending on an OS PNG compressor.

    Qt's PNG byte stream is deterministic within one runtime (checked above),
    but zlib and raster backends differ slightly across macOS and Linux. The
    checked-in asset contract is the visible identity: exact dimensions and
    background token, plus a tightly bounded pixel delta for antialiasing.
    """

    first = QImage(str(generated)).convertToFormat(QImage.Format.Format_RGBA8888)
    second = QImage(str(checked_in)).convertToFormat(QImage.Format.Format_RGBA8888)
    assert not first.isNull(), generated
    assert not second.isNull(), checked_in
    assert first.size() == second.size()
    background_sample = (max(1, first.width() // 8), first.height() // 2)
    assert first.pixelColor(*background_sample).name().upper() == Color.BG_PANEL
    assert second.pixelColor(*background_sample).name().upper() == Color.BG_PANEL

    first_bytes = bytes(first.bits())
    second_bytes = bytes(second.bits())
    total_delta = sum(abs(a - b) for a, b in zip(first_bytes, second_bytes))
    strong_delta_pixels = sum(
        1
        for offset in range(0, len(first_bytes), 4)
        if max(
            abs(first_bytes[offset + channel] - second_bytes[offset + channel])
            for channel in range(4)
        )
        > 16
    )
    assert total_delta / len(first_bytes) <= 3.0
    assert strong_delta_pixels / (first.width() * first.height()) <= 0.10


def test_svg_is_a_portable_warm_trinity_companion():
    source = BRAND_MARK_PATH.read_text(encoding="utf-8")
    assert len(re.findall(r"<path\b", source)) == 1
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
    assert "One continuous three-loop trefoil for musicians playing together" in source
    assert source == _svg_source()
    assert f'd="{trinity_svg_path_data()}"' in source


def test_canonical_curve_is_the_approved_historical_continuous_trefoil():
    path_data = trinity_svg_path_data()
    assert hashlib.sha256(path_data.encode("ascii")).hexdigest() == (
        "2d10ad9120c753091289c628b6506a0d048fd28f24c757a55b3a2ba6a6c1bea5"
    )
    assert path_data.count("M") == 1
    assert path_data.count("L") == 72
    assert path_data.endswith(" Z")


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
        assert all(pixel.red() > pixel.green() > pixel.blue() for pixel in saturated)


def test_mark_has_three_circular_dark_centered_nodes():
    _qapp()
    size = 96
    image = render_brand_pixmap(size).toImage()
    for x_ratio, y_ratio in ((0.5, 0.145), (0.826, 0.693), (0.174, 0.693)):
        pixel = image.pixelColor(round(size * x_ratio), round(size * y_ratio))
        assert pixel.alpha() == 255
        assert max(pixel.red(), pixel.green(), pixel.blue()) <= 16


def test_mark_is_retina_safe_and_does_not_depend_on_the_svg_at_runtime(
    monkeypatch, tmp_path
):
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
        label.text() not in {"WJ", "WEBJAM"} for label in dialog.findChildren(QLabel)
    )
    dialog.close()


def test_help_and_about_use_the_canonical_trefoil_not_a_generic_icon():
    _qapp()
    window = ConductorWindow(
        mode_entries=[("music_jam", "Music Jam")],
        initial_mode_key="music_jam",
        initial_title="Band Rehearsal",
    )
    expected = render_brand_pixmap(64).toImage()
    for show_dialog in (window.show_help, window.show_about):
        with (
            mock.patch(
                "PySide6.QtWidgets.QMessageBox.exec",
                return_value=0,
            ),
            mock.patch(
                "PySide6.QtWidgets.QMessageBox.setIconPixmap",
            ) as set_icon,
        ):
            show_dialog()
        set_icon.assert_called_once()
        supplied = set_icon.call_args.args[0]
        assert not supplied.isNull()
        assert supplied.size().width() == 64
        assert supplied.size().height() == 64
        assert supplied.toImage() == expected
    window.close()


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


def test_generator_is_deterministic_and_emits_pocket_stage_identity(tmp_path):
    _qapp()
    first_desktop = tmp_path / "first" / "desktop"
    first_ios = tmp_path / "first" / "Assets.xcassets"
    second_desktop = tmp_path / "second" / "desktop"
    second_ios = tmp_path / "second" / "Assets.xcassets"
    generate_brand_assets(
        desktop_output_dir=first_desktop,
        ios_asset_catalog=first_ios,
    )
    generate_brand_assets(
        desktop_output_dir=second_desktop,
        ios_asset_catalog=second_ios,
    )

    expected = (
        Path("desktop/webjam-mark.svg"),
        Path("desktop/webjam.ico"),
        Path("desktop/webjam.icns"),
        Path("Assets.xcassets/Contents.json"),
        Path("Assets.xcassets/AppIcon.appiconset/Contents.json"),
        Path("Assets.xcassets/AppIcon.appiconset/AppIcon-1024.png"),
        Path("Assets.xcassets/WebJamMark.imageset/Contents.json"),
        Path("Assets.xcassets/WebJamMark.imageset/webjam-mark.svg"),
    )
    for relative in expected:
        first = tmp_path / "first" / relative
        second = tmp_path / "second" / relative
        assert first.is_file(), relative
        assert first.read_bytes() == second.read_bytes(), relative

    checked_in = {
        Path("desktop/webjam-mark.svg"): ASSETS / "webjam-mark.svg",
        Path("desktop/webjam.ico"): ASSETS / "webjam.ico",
        Path("desktop/webjam.icns"): ASSETS / "webjam.icns",
    }
    ios_catalog = ROOT / "ios" / "PocketStage" / "Assets.xcassets"
    for relative in expected:
        if str(relative).startswith("Assets.xcassets/"):
            checked_in[relative] = ios_catalog / relative.relative_to("Assets.xcassets")
    assert set(checked_in) == set(expected)
    for relative, canonical in checked_in.items():
        assert canonical.is_file(), relative
        generated = tmp_path / "first" / relative
        if relative.suffix.lower() in {".ico", ".icns", ".png"}:
            _assert_same_render(generated, canonical)
        else:
            assert generated.read_bytes() == canonical.read_bytes(), relative

    app_icon = QImage(str(first_ios / "AppIcon.appiconset" / "AppIcon-1024.png"))
    assert not app_icon.isNull()
    assert not app_icon.hasAlphaChannel()
    assert app_icon.pixelColor(0, 0).alpha() == 255
    assert (first_ios / "WebJamMark.imageset" / "webjam-mark.svg").read_text(
        encoding="utf-8"
    ) == _svg_source()


def test_checked_in_pocket_stage_assets_and_consumers_use_canonical_mark():
    ios_root = ROOT / "ios"
    catalog = ios_root / "PocketStage" / "Assets.xcassets"
    app_icon = catalog / "AppIcon.appiconset" / "AppIcon-1024.png"
    mark = catalog / "WebJamMark.imageset" / "webjam-mark.svg"
    assert app_icon.is_file()
    assert mark.read_text(encoding="utf-8") == BRAND_MARK_PATH.read_text(
        encoding="utf-8"
    )
    app_icon_manifest = json.loads(
        (app_icon.parent / "Contents.json").read_text(encoding="utf-8")
    )
    assert app_icon_manifest["images"] == [
        {
            "filename": "AppIcon-1024.png",
            "idiom": "universal",
            "platform": "ios",
            "size": "1024x1024",
        }
    ]
    project = (ios_root / "project.yml").read_text(encoding="utf-8")
    kit_builder = (
        ROOT / "packaging" / "ios" / "prepare-pocket-stage-kit.sh"
    ).read_text(encoding="utf-8")
    header = (ios_root / "PocketStage" / "WebJamBrandHeader.swift").read_text(
        encoding="utf-8"
    )
    pair_view = (ios_root / "PocketStage" / "PocketStageTabView.swift").read_text(
        encoding="utf-8"
    )
    assert "ASSETCATALOG_COMPILER_APPICON_NAME: AppIcon" in project
    assert 'Image("WebJamMark")' in header
    assert "WebJamBrandHeader()" in pair_view
    assert '"WJ"' not in header
    for relative in (
        "PocketStage/Assets.xcassets/AppIcon.appiconset/AppIcon-1024.png",
        "PocketStage/Assets.xcassets/WebJamMark.imageset/webjam-mark.svg",
        "PocketStage/WebJamBrandHeader.swift",
    ):
        assert f'"{relative}"' in kit_builder


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


def test_linux_candidate_template_does_not_claim_an_unshipped_launcher():
    readme = (ROOT / "packaging" / "linux" / "README-LINUX.txt").read_text(
        encoding="utf-8"
    )
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    readme_words = " ".join(readme.split())
    assert "The future artifact is a portable ZIP, not a distro package." in readme
    assert "does not install an application-menu entry" in readme_words
    assert ".desktop launcher" in readme_words
    desktop_build = workflow.split(
        "      - name: Build desktop artifact",
        1,
    )[1].split(
        "\n      - name:",
        1,
    )[0]
    assert 'elif [[ "${{ matrix.target }}" == "linux-x64" ]]; then' in desktop_build
    assert 'zip -qr "../out/WebJam-${{ matrix.target }}.zip" WebJam/' in desktop_build
    assert ".desktop" not in desktop_build
