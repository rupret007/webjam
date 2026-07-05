import tkinter as tk
from typing import Dict, Optional, Callable
try:
    import customtkinter as ctk
    CTK_AVAILABLE = True
except ImportError:
    CTK_AVAILABLE = False

from ui.theme import DEFAULT_THEME
from ui.accessibility import scaled_font_size
from ui.mixer_channel import EnhancedMixerChannel

THEME = DEFAULT_THEME

class MixerPanel:
    """
    Composite widget managing the mixing console UI.
    Extracted from WebJamEnhancedApp for maintainability.
    """
    def __init__(
        self, 
        parent, 
        ctk_available: bool, 
        font_scale: float,
        mixer_service,
        mixer_channels: Dict[int, EnhancedMixerChannel]
    ):
        self.parent = parent
        self.ctk_available = ctk_available
        self.font_scale = font_scale
        self.mixer_service = mixer_service
        self.mixer_channels = mixer_channels
        
        self.container = None
        self.channels_container = None
        
        self.setup_ui()

    def setup_ui(self):
        # Mixer panel container
        self.container = ctk.CTkFrame(self.parent) if self.ctk_available else tk.Frame(self.parent, bg="#2b2b2b", relief=tk.RAISED, borderwidth=2)
        self.container.pack(fill=tk.BOTH, expand=True)
        
        # Mixer title
        mixer_title = self._create_label(self.container, "Virtual Mixing Console", font_size=16, bold=True)
        mixer_title.pack(pady=10)
        
        # Master controls
        master_controls = ctk.CTkFrame(self.container) if self.ctk_available else tk.Frame(self.container, bg="#2b2b2b")
        master_controls.pack(fill=tk.X, padx=10, pady=5)
        
        self.reset_all_btn = self._create_button(master_controls, "Reset All Faders", self.mixer_service.reset_all_faders)
        self.reset_all_btn.pack(side=tk.LEFT, padx=5)
        self.unmute_all_btn = self._create_button(master_controls, "Unmute All", self.mixer_service.unmute_all)
        self.unmute_all_btn.pack(side=tk.LEFT, padx=5)
        self.center_pans_btn = self._create_button(master_controls, "Center All Pans", self.mixer_service.center_all_pans)
        self.center_pans_btn.pack(side=tk.LEFT, padx=5)
        
        # Scrollable mixer channels
        if self.ctk_available:
            self.channels_container = ctk.CTkScrollableFrame(self.container, height=600, orientation="horizontal")
        else:
            canvas_frame = tk.Frame(self.container, bg="#2b2b2b")
            canvas_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
            
            canvas = tk.Canvas(canvas_frame, bg="#2b2b2b", highlightthickness=0)
            h_scrollbar = tk.Scrollbar(canvas_frame, orient="horizontal", command=canvas.xview)
            self.channels_container = tk.Frame(canvas, bg="#2b2b2b")
            
            self.channels_container.bind(
                "<Configure>",
                lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
            )
            
            canvas.create_window((0, 0), window=self.channels_container, anchor="nw")
            canvas.configure(xscrollcommand=h_scrollbar.set)
            
            canvas.pack(side="top", fill="both", expand=True)
            h_scrollbar.pack(side="bottom", fill="x")
        
        self.channels_container.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

    def _create_label(self, parent, text, font_size=10, bold=False):
        actual_size = scaled_font_size(font_size, self.font_scale)
        if self.ctk_available:
            weight = "bold" if bold else "normal"
            return ctk.CTkLabel(parent, text=text, font=("Arial", actual_size, weight))
        else:
            font = ("Arial", actual_size, "bold" if bold else "normal")
            bg = parent.cget("bg") if hasattr(parent, 'cget') else "#2b2b2b"
            return tk.Label(parent, text=text, font=font, bg=bg, fg="white", justify=tk.LEFT)
    
    def _create_button(self, parent, text, command):
        button_size = scaled_font_size(11, self.font_scale)
        if self.ctk_available:
            return ctk.CTkButton(parent, text=text, command=command, font=(THEME.font_family, button_size))
        else:
            return tk.Button(
                parent,
                text=text,
                command=command,
                bg=THEME.button_primary,
                fg=THEME.text_primary,
                font=(THEME.font_family, scaled_font_size(10, self.font_scale)),
                padx=10,
                pady=5,
            )
