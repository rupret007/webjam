"""
Design tokens for the Conductor UI.

A single source of truth for color, spacing, radius, and typography.
Widgets should reference these constants so the whole UI moves when a token moves.
"""

from __future__ import annotations


class Color:
    # WebJam uses one brand accent: The University of Texas at Austin's
    # official burnt orange.  Every other color is a neutral.  Keeping state
    # in words, icons, and shape (rather than adding "success green" or
    # "danger red") makes the interface calmer and keeps it understandable
    # for people who cannot distinguish status colors.

    # Base surfaces
    # These neutrals are intentionally not pure black. The narrow luminance
    # ladder gives stacked surfaces readable depth on ordinary displays
    # without turning the room into a collection of high-contrast boxes.
    BG_BASE = "#0A0A0A"         # window background
    BG_PANEL = "#121212"        # header, rails, status surfaces
    BG_CARD = "#1B1B1B"         # participant card
    BG_CARD_HOVER = "#272727"
    BG_INPUT = "#0F0F0F"

    # Borders
    BORDER_SUBTLE = "#303030"
    BORDER_STRONG = "#585858"
    BORDER_FOCUS = "#BF5700"

    # Text
    # Pure white is deliberate here: it keeps normal-size labels on the
    # official burnt-orange primary controls above the WCAG AA threshold.
    TEXT_PRIMARY = "#FFFFFF"
    TEXT_SECONDARY = "#D8D8D8"
    TEXT_MUTED = "#9D9D9D"
    TEXT_INVERSE = "#FFFFFF"

    # Accents
    ACCENT_PRIMARY = "#BF5700"  # official UT Austin burnt orange
    ACCENT_HOVER = "#A94F00"
    ACCENT_PRESSED = "#8F3E00"
    ACCENT_VIDEO = ACCENT_PRIMARY
    ACCENT_AUDIO = ACCENT_PRIMARY
    ACCENT_RECORD = ACCENT_PRIMARY
    ACCENT_SUCCESS = ACCENT_PRIMARY
    ACCENT_WARN = ACCENT_PRIMARY
    ACCENT_DANGER = ACCENT_PRIMARY

    # Meters (gradient stops)
    METER_GREEN = "#A0A0A0"
    METER_YELLOW = ACCENT_PRIMARY
    METER_RED = "#FFFFFF"

    # Latency classes
    LATENCY_GOOD = "#D0D0D0"    # <30ms
    LATENCY_FAIR = ACCENT_PRIMARY  # 30-60ms
    LATENCY_POOR = "#FFFFFF"     # >60ms; text also names the state

    # Overlays
    OVERLAY_DIM = "rgba(0, 0, 0, 0.78)"


class Space:
    XS = 4
    SM = 8
    MD = 12
    LG = 16
    XL = 24
    XXL = 32
    XXXL = 48


class Radius:
    SM = 6
    MD = 10
    LG = 14
    PILL = 999


class Font:
    # Inter ships with the app (webjam_qt/theme/fonts, loaded in
    # app._configure_default_font); the rest of the chain is the fallback
    # for a missing/corrupt bundle: Segoe UI on Windows, -apple-system on
    # macOS, sans-serif elsewhere.
    FAMILY_SANS = "'Inter', -apple-system, 'Segoe UI', 'Helvetica Neue', Helvetica, Arial, sans-serif"
    FAMILY_MONO = "'SF Mono', 'JetBrains Mono', 'Consolas', 'Courier New', monospace"

    SIZE_XS = 10
    SIZE_SM = 11
    SIZE_BASE = 13
    SIZE_MD = 15
    SIZE_LG = 18
    SIZE_XL = 24
    SIZE_DISPLAY = 32

    WEIGHT_NORMAL = 400
    WEIGHT_MEDIUM = 500
    WEIGHT_SEMIBOLD = 600
    WEIGHT_BOLD = 700
