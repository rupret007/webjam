from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ui.accessibility import clamp_scale


@dataclass
class UiPreferences:
    font_scale: float = 1.0
    high_contrast_enabled: bool = False
    auto_setup_enabled: bool = True
    auto_tour_enabled: bool = True
    window_geometry: str = "1600x900"


class UiPreferencesService:
    def __init__(self, repository: Any):
        self.repository = repository

    def load(self) -> UiPreferences:
        stored_scale = self.repository.get_setting("ui_font_scale", "1.0") or "1.0"
        stored_contrast = self.repository.get_setting("ui_high_contrast", "0") or "0"
        stored_auto_setup = self.repository.get_setting("ui_auto_setup_on_start", "1") or "1"
        stored_auto_tour = self.repository.get_setting("ui_auto_tour_on_start", "1") or "1"
        stored_geometry = self.repository.get_setting("ui_window_geometry", "1600x900") or "1600x900"

        try:
            font_scale = clamp_scale(float(stored_scale))
        except ValueError:
            font_scale = 1.0

        return UiPreferences(
            font_scale=font_scale,
            high_contrast_enabled=stored_contrast.strip() in {"1", "true", "yes", "on"},
            auto_setup_enabled=stored_auto_setup.strip() in {"1", "true", "yes", "on"},
            auto_tour_enabled=stored_auto_tour.strip() in {"1", "true", "yes", "on"},
            window_geometry=stored_geometry if "x" in stored_geometry else "1600x900",
        )

    def save_ui(
        self,
        font_scale: float,
        high_contrast_enabled: bool,
        auto_setup_enabled: bool,
        auto_tour_enabled: bool,
    ) -> None:
        self.repository.set_setting("ui_font_scale", f"{font_scale:.2f}")
        self.repository.set_setting("ui_high_contrast", "1" if high_contrast_enabled else "0")
        self.repository.set_setting("ui_auto_setup_on_start", "1" if auto_setup_enabled else "0")
        self.repository.set_setting("ui_auto_tour_on_start", "1" if auto_tour_enabled else "0")

    def get_window_geometry(self) -> str:
        stored = self.repository.get_setting("ui_window_geometry", "1600x900")
        if stored and "x" in stored:
            return stored
        return "1600x900"

    def save_window_geometry(self, geometry: str) -> None:
        if geometry:
            self.repository.set_setting("ui_window_geometry", geometry)

    def reset_window_geometry(self) -> str:
        self.repository.set_setting("ui_window_geometry", "1600x900")
        return "1600x900"
