from __future__ import annotations

import math


def clamp_scale(scale: float, minimum: float = 0.8, maximum: float = 1.6) -> float:
    default = max(minimum, min(maximum, 1.0))
    try:
        value = float(scale)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(value):
        return default
    if value < minimum:
        return minimum
    if value > maximum:
        return maximum
    return value


def scaled_font_size(base_size: int, scale: float) -> int:
    clamped = clamp_scale(scale)
    return max(8, int(round(base_size * clamped)))


def contrast_palette(enabled: bool) -> dict[str, str]:
    if enabled:
        return {
            "bg": "#000000",
            "fg": "#ffffff",
            "accent": "#00ffff",
            "warn": "#ffcc00",
        }
    return {
        "bg": "#2b2b2b",
        "fg": "#ffffff",
        "accent": "#00cc66",
        "warn": "#ffcc00",
    }
