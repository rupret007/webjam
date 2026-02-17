from __future__ import annotations


def clamp_scale(scale: float, minimum: float = 0.8, maximum: float = 1.6) -> float:
    if scale < minimum:
        return minimum
    if scale > maximum:
        return maximum
    return scale


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
