from __future__ import annotations

import tkinter as tk
import os
import json
import logging
import threading
from typing import Optional, Dict, Any, Callable
from pathlib import Path

# UI imports
from ui.theme import DEFAULT_THEME
from ui.accessibility import apply_widget_style
from ui.mixer_service import EnhancedMixerService
from ui.mode_controller import ModeController

class WebJamMainWindow:
    """
    Main Application Window and Layout Controller for WebJam Enhanced.
    """
    def __init__(self, root: tk.Tk, config: Dict[str, Any]):
        self.root = root
        self.config = config
        self.logger = logging.getLogger("webjam.ui")
        
        self.setup_ui()
        
    def setup_ui(self) -> None:
        """Initialize main UI layout and components."""
        self.root.title("WebJam Enhanced")
        self.root.geometry(self.config.get("window_geometry", "1200x800"))
        
        # Main layout containers
        self.main_frame = tk.Frame(self.root, bg=DEFAULT_THEME.bg_primary)
        self.main_frame.pack(fill=tk.BOTH, expand=True)
        
        # Left sidebar - Mode selection
        self.sidebar = tk.Frame(self.main_frame, width=200, bg=DEFAULT_THEME.bg_secondary)
        self.sidebar.pack(side=tk.LEFT, fill=tk.Y)
        self.sidebar.pack_propagate(False)
        
        # Center area - Main content
        self.content_area = tk.Frame(self.main_frame, bg=DEFAULT_THEME.bg_primary)
        self.content_area.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        # Status bar
        self.status_bar = tk.Frame(self.root, height=24, bg=DEFAULT_THEME.bg_secondary)
        self.status_bar.pack(side=tk.BOTTOM, fill=tk.X)
        
    def show_error(self, message: str) -> None:
        from tkinter import messagebox
        messagebox.showerror("WebJam Error", message)

    def set_status(self, text: str) -> None:
        self.logger.info(f"Status: {text}")
        # Update status bar label (to be implemented)
