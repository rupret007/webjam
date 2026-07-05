import tkinter as tk
from typing import Dict, Any, Callable
from core.creative_modes import CREATIVE_MODES, get_mode_by_key_or_default

DEFAULT_MODE_LAYOUT = {
    "mixer_ratio": 0.68,
    "min_mixer": 820,
    "min_canvas": 360,
    "canvas_width": 420,
}

MODE_LAYOUT_PRESETS = {
    "music_jam": {
        "mixer_ratio": 0.74,
        "min_mixer": 900,
        "min_canvas": 320,
        "canvas_width": 360,
    },
    "visual_studio": {
        "mixer_ratio": 0.60,
        "min_mixer": 780,
        "min_canvas": 420,
        "canvas_width": 500,
    },
    "writers_room": {
        "mixer_ratio": 0.56,
        "min_mixer": 720,
        "min_canvas": 460,
        "canvas_width": 540,
    },
    "design_critique": {
        "mixer_ratio": 0.60,
        "min_mixer": 760,
        "min_canvas": 430,
        "canvas_width": 500,
    },
    "storyboard_film_room": {
        "mixer_ratio": 0.62,
        "min_mixer": 780,
        "min_canvas": 420,
        "canvas_width": 500,
    },
}

class ModeController:
    """Manages creative modes and their associated UI layouts"""
    
    def __init__(self, app):
        self.app = app
        self.root = app.root
        
    def get_layout_spec(self, mode_key: str) -> Dict[str, float]:
        spec = dict(DEFAULT_MODE_LAYOUT)
        spec.update(MODE_LAYOUT_PRESETS.get(str(mode_key).strip(), {}))
        return spec

    def compute_sash_x(self, total_width: int, mixer_ratio: float, min_mixer: int, min_canvas: int) -> int:
        width = max(1, int(total_width))
        ratio = max(0.1, min(float(mixer_ratio), 0.9))
        lower_bound = max(1, int(min_mixer))
        upper_bound = max(lower_bound, width - max(1, int(min_canvas)))
        desired = int(width * ratio)
        return max(lower_bound, min(desired, upper_bound))

    def apply_layout(self, mode_key: str):
        spec = self.get_layout_spec(mode_key)
        
        # Adjust session canvas if it exists
        if hasattr(self.app, "session_canvas"):
            try:
                self.app.session_canvas.configure(width=int(spec["canvas_width"]))
            except (tk.TclError, RuntimeError, ValueError):
                pass

        splitter = getattr(self.app, "main_splitter", None)
        if splitter is None:
            return

        try:
            if hasattr(self.app, "left_content"):
                splitter.paneconfig(self.app.left_content, minsize=int(spec["min_mixer"]))
            if hasattr(self.app, "right_content"):
                splitter.paneconfig(self.app.right_content, minsize=int(spec["min_canvas"]))
        except (tk.TclError, RuntimeError, ValueError):
            pass

        try:
            total_width = int(self.root.winfo_width())
            if total_width <= 1:
                total_width = int(self.root.winfo_reqwidth())
            if total_width <= 1:
                total_width = 1600
                
            sash_x = self.compute_sash_x(
                total_width=total_width,
                mixer_ratio=float(spec["mixer_ratio"]),
                min_mixer=int(spec["min_mixer"]),
                min_canvas=int(spec["min_canvas"]),
            )
            splitter.sash_place(0, sash_x, 1)
        except (tk.TclError, RuntimeError, ValueError, TypeError):
            return

    def schedule_refresh(self, mode_key: str):
        self.apply_layout(mode_key)
        try:
            self.root.after(120, lambda: self.apply_layout(mode_key))
        except (tk.TclError, RuntimeError, AttributeError):
            pass
