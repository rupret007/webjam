from dataclasses import dataclass


@dataclass(frozen=True)
class Theme:
    bg_primary: str = "#1a1a1a"
    bg_secondary: str = "#2b2b2b"
    button_default: str = "#555555"
    button_primary: str = "#4444ff"
    accent_success: str = "#44ff44"
    accent_danger: str = "#ff4444"
    text_primary: str = "white"

    font_family: str = "Arial"
    font_size_small: int = 9
    font_size_body: int = 11
    font_size_title: int = 20


DEFAULT_THEME = Theme()

