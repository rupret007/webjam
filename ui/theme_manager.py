from dataclasses import dataclass
from typing import Dict

@dataclass(frozen=True)
class Theme:
    name: str
    bg_primary: str = "#1a1a1a"
    bg_secondary: str = "#2b2b2b"
    bg_tertiary: str = "#3d3d3d"
    button_default: str = "#555555"
    button_primary: str = "#4444ff"
    accent_success: str = "#44ff44"
    accent_warning: str = "#ffcc00"
    accent_danger: str = "#ff4444"
    text_primary: str = "white"
    text_secondary: str = "#cccccc"
    text_muted: str = "#888888"

    font_family: str = "Arial"
    font_size_small: int = 9
    font_size_body: int = 11
    font_size_title: int = 20

CLASSIC_THEME = Theme(
    name="Classic",
    bg_primary="#1a1a1a",
    bg_secondary="#2b2b2b",
    bg_tertiary="#3d3d3d",
    button_default="#555555",
    button_primary="#4444ff",
    accent_success="#44ff44",
    accent_warning="#ffcc00",
    accent_danger="#ff4444",
    text_primary="white",
    text_secondary="#cccccc",
    text_muted="#888888"
)

HIGH_CONTRAST_THEME = Theme(
    name="High Contrast",
    bg_primary="#000000",
    bg_secondary="#111111",
    bg_tertiary="#222222",
    button_default="#333333",
    button_primary="#ffffff",
    accent_success="#00ff00",
    accent_warning="#ffff00",
    accent_danger="#ff0000",
    text_primary="#ffffff",
    text_secondary="#eeeeee",
    text_muted="#aaaaaa"
)

THEMES: Dict[str, Theme] = {
    "Classic": CLASSIC_THEME,
    "High Contrast": HIGH_CONTRAST_THEME
}

class ThemeManager:
    def __init__(self, initial_theme: str = "Classic"):
        self._current_theme_name = initial_theme
        self._callbacks = []

    @property
    def current_theme(self) -> Theme:
        return THEMES.get(self._current_theme_name, CLASSIC_THEME)

    def set_theme(self, theme_name: str):
        if theme_name in THEMES:
            self._current_theme_name = theme_name
            for callback in self._callbacks:
                callback(self.current_theme)

    def register_callback(self, callback):
        self._callbacks.append(callback)

    def toggle_high_contrast(self, enabled: bool):
        self.set_theme("High Contrast" if enabled else "Classic")
