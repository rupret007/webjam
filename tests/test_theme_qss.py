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


def _relative_luminance(hex_color: str) -> float:
    channels = [int(hex_color[index:index + 2], 16) / 255.0 for index in (1, 3, 5)]
    linear = [
        value / 12.92
        if value <= 0.04045
        else ((value + 0.055) / 1.055) ** 2.4
        for value in channels
    ]
    return sum(weight * value for weight, value in zip((0.2126, 0.7152, 0.0722), linear))


def _contrast_ratio(first: str, second: str) -> float:
    brighter, darker = sorted(
        (_relative_luminance(first), _relative_luminance(second)), reverse=True
    )
    return (brighter + 0.05) / (darker + 0.05)


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

    def test_core_text_pairs_meet_wcag_aa_for_normal_text(self):
        from webjam_qt.theme.tokens import Color

        pairs = (
            (Color.TEXT_PRIMARY, Color.BG_BASE),
            (Color.TEXT_SECONDARY, Color.BG_CARD),
            (Color.TEXT_MUTED, Color.BG_BASE),
            (Color.TEXT_PRIMARY, Color.ACCENT_PRIMARY),
        )
        for foreground, background in pairs:
            self.assertGreaterEqual(
                _contrast_ratio(foreground, background),
                4.5,
                f"{foreground} on {background} must meet WCAG AA",
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


class TestMessageBoxDefaultButtonIsNeutral(unittest.TestCase):
    """A confirmation's safe answer must not look like the recommended one.

    WebJam paints the burnt-orange accent on the action a screen is asking
    you to take. QMessageBox defaults are chosen for safety, not intent, so
    on "End this jam for everyone?" the accent landed on "No" -- making the
    cautious answer read as the recommended action. Message boxes keep
    neutral buttons; the default still holds focus and Return.
    """

    def test_accent_default_rule_does_not_select_message_boxes(self) -> None:
        # Strip block comments first; prose about QMessageBox is not a rule.
        stylesheet = re.sub(r"/\*.*?\*/", "", load_stylesheet(), flags=re.S)

        accent_default_rules = [
            block
            for block in stylesheet.split("}")
            if ":default" in block and "background-color" in block
        ]

        self.assertTrue(
            accent_default_rules,
            "expected at least one styled :default rule to guard",
        )
        for block in accent_default_rules:
            selector = block.split("{")[0]
            self.assertNotIn(
                "QMessageBox",
                selector,
                "QMessageBox default buttons must stay neutral so a "
                "confirmation's safe answer is not styled as the "
                "recommended action",
            )
