from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ui.accessibility import clamp_scale

_GEOMETRY_RE = re.compile(r"^(\d+)x(\d+)")

_MIN_WINDOW_DIM = 100


def _is_valid_geometry(value: str) -> bool:
    """
    Validate window geometry string. Only the WxH portion is checked;
    Tkinter returns full format (e.g. '1600x900+0+0') but we only validate
    width and height for minimum dimensions. Position (+X+Y) is ignored.
    """
    if not value:
        return False
    m = _GEOMETRY_RE.match(value)
    if not m:
        return False
    w, h = int(m.group(1)), int(m.group(2))
    return w >= _MIN_WINDOW_DIM and h >= _MIN_WINDOW_DIM


@dataclass
class UiPreferences:
    font_scale: float = 1.0
    high_contrast_enabled: bool = False
    auto_setup_enabled: bool = True
    window_geometry: str = "1600x900"


class UiPreferencesService:
    def __init__(self, repository: Any):
        self.repository = repository

    def load(self) -> UiPreferences:
        stored_scale = self.repository.get_setting("ui_font_scale", "1.0") or "1.0"
        stored_contrast = self.repository.get_setting("ui_high_contrast", "0") or "0"
        stored_auto_setup = self.repository.get_setting("ui_auto_setup_on_start", "1") or "1"
        stored_geometry = self.repository.get_setting("ui_window_geometry", "1600x900") or "1600x900"

        try:
            font_scale = clamp_scale(float(stored_scale))
        except (TypeError, ValueError):
            font_scale = 1.0

        return UiPreferences(
            font_scale=font_scale,
            high_contrast_enabled=stored_contrast.strip().lower() in {"1", "true", "yes", "on"},
            auto_setup_enabled=stored_auto_setup.strip().lower() in {"1", "true", "yes", "on"},
            window_geometry=stored_geometry if _is_valid_geometry(stored_geometry) else "1600x900",
        )

    def save_ui(
        self,
        font_scale: float,
        high_contrast_enabled: bool,
        auto_setup_enabled: bool,
    ) -> None:
        self.repository.set_setting("ui_font_scale", f"{font_scale:.2f}")
        self.repository.set_setting("ui_high_contrast", "1" if high_contrast_enabled else "0")
        self.repository.set_setting("ui_auto_setup_on_start", "1" if auto_setup_enabled else "0")

    def get_window_geometry(self) -> str:
        stored = self.repository.get_setting("ui_window_geometry", "1600x900")
        if _is_valid_geometry(stored or ""):
            return stored
        return "1600x900"

    def save_window_geometry(self, geometry: str) -> None:
        if geometry and _is_valid_geometry(geometry):
            self.repository.set_setting("ui_window_geometry", geometry)

    def reset_window_geometry(self) -> str:
        self.repository.set_setting("ui_window_geometry", "1600x900")
        return "1600x900"
