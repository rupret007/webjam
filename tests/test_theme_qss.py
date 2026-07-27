"""Theme stylesheet integrity (webjam_qt/theme)."""
from __future__ import annotations

import colorsys
import re
import unittest
from pathlib import Path

from webjam_qt.theme import load_stylesheet


ROOT = Path(__file__).resolve().parents[1]
UI_ROOT = ROOT / "webjam_qt"
HEX_COLOR = re.compile(r"(?<![0-9A-Fa-f])#([0-9A-Fa-f]{8}|[0-9A-Fa-f]{6})(?![0-9A-Fa-f])")
RGB_COLOR = re.compile(
    r"rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})(?:\s*,[^)]*)?\)",
    re.IGNORECASE,
)


def _approved_product_rgb(rgb: tuple[int, int, int]) -> bool:
    """Only grayscale neutrals or a true burnt-orange hue are authored."""
    red, green, blue = rgb
    if red == green == blue:
        return True
    hue, saturation, _value = colorsys.rgb_to_hsv(
        red / 255.0, green / 255.0, blue / 255.0
    )
    # Burnt orange and its dark surface tints sit around 20–30 degrees.  The
    # wider bounds allow antialias-safe brand variants while excluding red,
    # yellow, green, cyan/teal, blue, violet, and magenta.
    return 15.0 <= hue * 360.0 <= 35.0 and saturation >= 0.50


class TestStylesheet(unittest.TestCase):
    def setUp(self):
        self.qss = load_stylesheet()

    def test_all_tokens_substituted(self):
        """A typo'd ${TOKEN} would reach Qt as literal text and silently
        disable every rule after it."""
        self.assertNotIn("${", self.qss)

    def test_settings_wizard_widgets_are_styled(self):
        # The SetupWizard QWizard previously rendered native chrome; keep it
        # inside the dark theme.
        for selector in ("QWizard", "QSpinBox", "QLabel#WizardError"):
            self.assertIn(selector, self.qss)

    def test_black_orange_theme_coverage_selectors_present(self):
        for selector in (
            "QMenu",
            "QToolTip",
            "QMessageBox",
            "QListWidget#TakeList",
            "QLabel#TakeTitle",
            "QLabel#ParticipantAvatar",
            "QLineEdit#CanvasChatInput",
        ):
            self.assertIn(selector, self.qss)


class TestStrictProductPalette(unittest.TestCase):
    """Prevent one forgotten painter/dialog from reviving the old palette."""

    def test_brand_anchor_and_semantic_aliases_use_longhorn_burnt_orange(self):
        from webjam_qt.theme.tokens import Color

        self.assertEqual(Color.ACCENT_PRIMARY.upper(), "#BF5700")
        for name in (
            "BORDER_FOCUS",
            "ACCENT_VIDEO",
            "ACCENT_AUDIO",
            "ACCENT_RECORD",
            "ACCENT_SUCCESS",
            "ACCENT_WARN",
            "ACCENT_DANGER",
        ):
            self.assertEqual(
                getattr(Color, name).upper(),
                "#BF5700",
                f"{name} must communicate through wording/iconography, not a new hue",
            )

    def test_every_authored_ui_color_is_neutral_or_burnt_orange(self):
        offenders: list[str] = []
        files = sorted(
            path
            for path in UI_ROOT.rglob("*")
            if path.suffix.lower() in {".py", ".qss", ".html"}
        )
        for path in files:
            source = path.read_text(encoding="utf-8")
            for line_number, line in enumerate(source.splitlines(), start=1):
                for match in HEX_COLOR.finditer(line):
                    value = match.group(1)
                    rgb = tuple(
                        int(value[index:index + 2], 16) for index in (0, 2, 4)
                    )
                    if not _approved_product_rgb(rgb):
                        offenders.append(
                            f"{path.relative_to(ROOT)}:{line_number} #{value}"
                        )
                for match in RGB_COLOR.finditer(line):
                    rgb = tuple(int(match.group(index)) for index in (1, 2, 3))
                    if any(channel > 255 for channel in rgb) or not _approved_product_rgb(rgb):
                        offenders.append(
                            f"{path.relative_to(ROOT)}:{line_number} rgb{rgb}"
                        )
        self.assertEqual(offenders, [], "Forbidden authored UI colors:\n" + "\n".join(offenders))

    def test_stylesheets_do_not_use_named_hues_outside_the_palette(self):
        forbidden = re.compile(
            r"\b(?:red|green|yellow|lime|cyan|teal|blue|navy|purple|violet|"
            r"magenta|pink|rose|mint)\b",
            re.IGNORECASE,
        )
        offenders: list[str] = []
        for relative in ("theme/conductor.qss",):
            path = UI_ROOT / relative
            for line_number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                if forbidden.search(line):
                    offenders.append(f"{relative}:{line_number}: {line.strip()}")
        self.assertEqual(offenders, [], "Forbidden named UI hues:\n" + "\n".join(offenders))


class TestBundledFonts(unittest.TestCase):
    def test_inter_ttfs_ship_with_the_theme(self):
        from pathlib import Path
        import webjam_qt.theme as theme
        fonts = Path(theme.__file__).parent / "fonts"
        ttfs = sorted(p.name for p in fonts.glob("Inter-*.ttf"))
        self.assertIn("Inter-Regular.ttf", ttfs)
        self.assertGreaterEqual(len(ttfs), 4)


if __name__ == "__main__":
    unittest.main()
