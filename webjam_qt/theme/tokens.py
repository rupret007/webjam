"""
Design tokens for the Conductor UI.

A single source of truth for color, spacing, radius, and typography.
Widgets should reference these constants so the whole UI moves when a token moves.
"""

from __future__ import annotations


class Color:
    # Base surfaces — dark studio
    BG_BASE = "#0B0E14"         # window background
    BG_PANEL = "#141922"        # side rail, canvas, status bar
    BG_CARD = "#1D2332"         # participant card
    BG_CARD_HOVER = "#242B3E"
    BG_INPUT = "#0F131C"

    # Borders
    BORDER_SUBTLE = "#242A3A"
    BORDER_STRONG = "#363D50"
    BORDER_FOCUS = "#3DD3D3"

    # Text
    TEXT_PRIMARY = "#E8ECF4"
    TEXT_SECONDARY = "#9AA4B8"
    TEXT_MUTED = "#5F6B85"
    TEXT_INVERSE = "#0B0E14"

    # Accents
    ACCENT_VIDEO = "#3DD3D3"    # Webex teal — video presence
    ACCENT_AUDIO = "#F5B041"    # Jamulus gold — audio presence
    ACCENT_RECORD = "#E8435B"   # record red, clipping
    ACCENT_SUCCESS = "#3DD968"
    ACCENT_WARN = "#F5B041"
    ACCENT_DANGER = "#E8435B"

    # Meters (gradient stops)
    METER_GREEN = "#3DD968"
    METER_YELLOW = "#F5B041"
    METER_RED = "#E8435B"

    # Latency classes
    LATENCY_GOOD = "#3DD968"    # <30ms
    LATENCY_FAIR = "#F5B041"    # 30-60ms
    LATENCY_POOR = "#E8435B"    # >60ms

    # Overlays
    OVERLAY_DIM = "rgba(11, 14, 20, 0.75)"


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
    FAMILY_SANS = "Inter, -apple-system, 'Segoe UI', Helvetica, Arial, sans-serif"
    FAMILY_MONO = "'SF Mono', 'JetBrains Mono', 'Consolas', monospace"

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
