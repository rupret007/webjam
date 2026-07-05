import tkinter as tk
import tkinter.font as tkfont
from typing import Optional, Callable
from jamulus_controller import JamulusController, JamulusParticipant
from ui.theme import DEFAULT_THEME as THEME
from ui.accessibility import clamp_scale, scaled_font_size, contrast_palette
from ui.views.tooltip import Tooltip

# Try to use customtkinter for modern UI
try:
    import customtkinter as ctk
    CTK_AVAILABLE = True
except ImportError:
    CTK_AVAILABLE = False

class EnhancedMixerChannel(ctk.CTkFrame if CTK_AVAILABLE else tk.Frame):
    """Enhanced mixer channel with real Jamulus integration"""
    
    def __init__(
        self,
        parent,
        participant: JamulusParticipant,
        controller: JamulusController,
        font_scale: float = 1.0,
        high_contrast: bool = False,
        on_select: Callable[[int], None] | None = None,
    ):
        super().__init__(parent)
        self.participant = participant
        self.controller = controller
        self.font_scale = font_scale
        self.high_contrast = high_contrast
        self.on_select = on_select
        self._tooltips = []
        self._selected = False
        
        if CTK_AVAILABLE:
            self.configure(fg_color=THEME.bg_secondary, corner_radius=10, border_width=1, border_color=THEME.bg_secondary)
        else:
            self.configure(
                bg=THEME.bg_secondary,
                relief=tk.RAISED,
                borderwidth=2,
                highlightthickness=1,
                highlightbackground=THEME.bg_secondary,
                highlightcolor=THEME.bg_secondary,
            )
        
        self.setup_ui()
    
    def setup_ui(self):
        """Create enhanced channel strip UI"""
        padding = 5
        
        # Channel number indicator
        ch_num = self._create_label(f"CH {self.participant.channel_id + 1}", font_size=9)
        ch_num.pack(pady=(padding, 0))
        
        # Participant name
        name_label = self._create_label(self.participant.name, font_size=11, bold=True)
        name_label.pack(pady=2)
        
        # Connection status indicator
        status = "●" if self.participant.is_connected else "○"
        color = "#00ff00" if self.participant.is_connected else "#666666"
        self.status_label = self._create_label(status, font_size=16)
        if not CTK_AVAILABLE:
            self.status_label.configure(fg=color)
        self.status_label.pack()
        
        # VU Meter with peak hold
        vu_frame = ctk.CTkFrame(self) if CTK_AVAILABLE else tk.Frame(self, bg=THEME.bg_secondary)
        vu_frame.pack(pady=padding, padx=padding, fill=tk.X)
        
        if CTK_AVAILABLE:
            self.vu_meter = ctk.CTkProgressBar(vu_frame, height=15)
            self.vu_meter.set(0)
        else:
            self.vu_meter = tk.Canvas(vu_frame, height=15, bg=THEME.bg_primary, highlightthickness=0)
            self.vu_meter_level = 0
        self.vu_meter.pack(fill=tk.X)
        
        # Peak indicator
        self.peak_label = self._create_label("", font_size=8)
        self.peak_label.pack()
        
        # Vertical fader with dB scale
        fader_container = ctk.CTkFrame(self) if CTK_AVAILABLE else tk.Frame(self, bg=THEME.bg_secondary)
        fader_container.pack(pady=padding, fill=tk.BOTH, expand=True)
        
        # dB markers
        db_markers = ["+0", "-6", "-12", "-20", "-∞"]
        markers_frame = tk.Frame(fader_container, bg=THEME.bg_secondary)
        markers_frame.pack(side=tk.LEFT, fill=tk.Y)
        
        for marker in db_markers:
            lbl = self._create_label(marker, font_size=7)
            lbl.pack(side=tk.TOP, pady=8)
        
        # Fader
        if CTK_AVAILABLE:
            self.fader = ctk.CTkSlider(
                fader_container,
                from_=0,
                to=100,
                orientation="vertical",
                command=self.on_fader_change,
                height=180
            )
        else:
            self.fader = tk.Scale(
                fader_container,
                from_=100,
                to=0,
                resolution=1,
                orient=tk.VERTICAL,
                command=self.on_fader_change,
                bg=THEME.bg_secondary,
                fg=THEME.text_primary,
                highlightthickness=0,
                length=180,
                width=30
            )
        self.fader.set(self.participant.fader_level)
        self.fader.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5)
        
        # Exact dB value
        self.db_label = self._create_label("0.0 dB", font_size=9)
        self.db_label.pack()
        
        # Pan control with L-C-R indicator
        pan_frame = ctk.CTkFrame(self) if CTK_AVAILABLE else tk.Frame(self, bg=THEME.bg_secondary)
        pan_frame.pack(pady=padding, padx=padding, fill=tk.X)
        
        pan_label = self._create_label("Pan", font_size=8)
        pan_label.pack()
        
        if CTK_AVAILABLE:
            self.pan_slider = ctk.CTkSlider(
                pan_frame,
                from_=0,
                to=100,
                orientation="horizontal",
                command=self.on_pan_change
            )
        else:
            self.pan_slider = tk.Scale(
                pan_frame,
                from_=0,
                to=100,
                resolution=1,
                orient=tk.HORIZONTAL,
                command=self.on_pan_change,
                bg=THEME.bg_secondary,
                fg=THEME.text_primary,
                highlightthickness=0
            )
        self.pan_slider.set(self.participant.pan)
        self.pan_slider.pack(fill=tk.X)
        
        self.pan_label = self._create_label("C", font_size=8)
        self.pan_label.pack()
        
        # Mute and Solo buttons
        button_frame = ctk.CTkFrame(self) if CTK_AVAILABLE else tk.Frame(self, bg=THEME.bg_secondary)
        button_frame.pack(pady=padding, fill=tk.X, padx=5)
        
        if CTK_AVAILABLE:
            self.mute_btn = ctk.CTkButton(
                button_frame,
                text="MUTE",
                command=self.toggle_mute,
                width=60,
                height=28,
                fg_color=THEME.button_default,
                font=(THEME.font_family, scaled_font_size(10, self.font_scale), "bold")
            )
            self.solo_btn = ctk.CTkButton(
                button_frame,
                text="SOLO",
                command=self.toggle_solo,
                width=60,
                height=28,
                fg_color=THEME.button_default,
                font=(THEME.font_family, scaled_font_size(10, self.font_scale), "bold")
            )
        else:
            self.mute_btn = tk.Button(
                button_frame,
                text="MUTE",
                command=self.toggle_mute,
                bg=THEME.button_default,
                fg=THEME.text_primary,
                font=("Arial", scaled_font_size(9, self.font_scale), "bold"),
                width=6
            )
            self.solo_btn = tk.Button(
                button_frame,
                text="SOLO",
                command=self.toggle_solo,
                bg=THEME.button_default,
                fg=THEME.text_primary,
                font=("Arial", scaled_font_size(9, self.font_scale), "bold"),
                width=6
            )
        
        self.mute_btn.pack(pady=2, fill=tk.X)
        self.solo_btn.pack(pady=2, fill=tk.X)
        
        # Update button states
        self.update_button_states()
        self._attach_tooltips()
        self.apply_accessibility(self.font_scale, self.high_contrast)
        self._bind_selection_handlers(self)
        self.set_selected(False)
    
    def _create_label(self, text, font_size=10, bold=False):
        """Create a styled label"""
        actual_size = scaled_font_size(font_size, self.font_scale)
        if CTK_AVAILABLE:
            weight = "bold" if bold else "normal"
            return ctk.CTkLabel(self, text=text, font=(THEME.font_family, actual_size, weight))
        else:
            font = (THEME.font_family, actual_size, "bold" if bold else "normal")
            return tk.Label(self, text=text, font=font, bg=THEME.bg_secondary, fg=THEME.text_primary)
    
    def on_fader_change(self, value):
        """Handle fader movement"""
        value = int(float(value))
        self.controller.set_fader_level(self.participant.channel_id, value)
        
        # Convert to dB
        if value > 0:
            db = 20 * ((value / 100) - 1)
        else:
            db = -float('inf')
        
        db_str = f"{db:.1f} dB" if db != -float('inf') else "-∞ dB"
        self.db_label.configure(text=db_str)
    
    def on_pan_change(self, value):
        """Handle pan control change"""
        value = int(float(value))
        self.controller.set_pan(self.participant.channel_id, value)
        
        # Update pan indicator
        if value < 40:
            pan_text = "L"
        elif value > 60:
            pan_text = "R"
        else:
            pan_text = "C"
        self.pan_label.configure(text=pan_text)
    
    def toggle_mute(self):
        """Toggle mute state"""
        self._notify_selected()
        new_state = not self.participant.muted
        self.controller.set_mute(self.participant.channel_id, new_state)
        self.update_button_states()
    
    def toggle_solo(self):
        """Toggle solo state"""
        self._notify_selected()
        new_state = not self.participant.solo
        self.controller.set_solo(self.participant.channel_id, new_state)
        self.update_button_states()
    
    def update_button_states(self):
        """Update button colors based on state"""
        mute_color = THEME.accent_danger if self.participant.muted else THEME.button_default
        solo_color = THEME.accent_success if self.participant.solo else THEME.button_default
        
        if CTK_AVAILABLE:
            self.mute_btn.configure(fg_color=mute_color)
            self.solo_btn.configure(fg_color=solo_color)
        else:
            self.mute_btn.configure(bg=mute_color)
            self.solo_btn.configure(bg=solo_color)
    
    def update_vu_meter(self, level: float):
        """Update VU meter with audio level"""
        if CTK_AVAILABLE:
            self.vu_meter.set(level)
        else:
            # Draw custom VU meter
            width = self.vu_meter.winfo_width()
            height = self.vu_meter.winfo_height()
            
            # Skip if not rendered yet (winfo returns 1 before render)
            if width <= 1 or height <= 1:
                return
            
            self.vu_meter.delete("all")
            
            # Background
            self.vu_meter.create_rectangle(0, 0, width, height, fill=THEME.bg_primary, outline="")
            
            # Level bar
            bar_width = int(width * level)
            
            # Color gradient: green -> yellow -> red
            if level < 0.7:
                color = "#00ff00"
            elif level < 0.9:
                color = "#ffff00"
            else:
                color = "#ff0000"
            
            if bar_width > 0:
                self.vu_meter.create_rectangle(0, 0, bar_width, height, fill=color, outline="")
        
        # Update peak indicator
        if level > 0.95:
            self.peak_label.configure(text="PEAK!")
            if not CTK_AVAILABLE:
                self.peak_label.configure(fg="#ff0000")
        else:
            self.peak_label.configure(text="")

    def _attach_tooltips(self):
        self._tooltips.append(Tooltip(self.fader, "Adjust participant volume"))
        self._tooltips.append(Tooltip(self.pan_slider, "Position in stereo field (L/C/R)"))
        self._tooltips.append(Tooltip(self.mute_btn, "Mute this participant in your mix"))
        self._tooltips.append(Tooltip(self.solo_btn, "Solo this participant and mute others"))
        self._tooltips.append(Tooltip(self.vu_meter, "Real-time level meter; avoid sustained PEAK"))
        self._tooltips.append(Tooltip(self, "Click a channel to select it for keyboard mute/solo shortcuts"))

    def _notify_selected(self) -> None:
        if self.on_select is not None:
            self.on_select(self.participant.channel_id)
        try:
            self.focus_set()
        except (tk.TclError, AttributeError):
            pass

    def _bind_selection_handlers(self, widget: tk.Misc) -> None:
        try:
            widget.bind("<Button-1>", lambda _e: self._notify_selected(), add="+")
            widget.bind("<FocusIn>", lambda _e: self._notify_selected(), add="+")
        except (tk.TclError, AttributeError):
            return
        for child in widget.winfo_children():
            self._bind_selection_handlers(child)

    def set_selected(self, selected: bool) -> None:
        self._selected = bool(selected)
        if CTK_AVAILABLE:
            self.configure(
                border_width=2 if self._selected else 1,
                border_color=(THEME.accent_warning if self._selected else THEME.bg_secondary),
            )
            return
        border_color = THEME.accent_warning if self._selected else THEME.bg_secondary
        self.configure(highlightthickness=2 if self._selected else 1)
        self.configure(highlightbackground=border_color, highlightcolor=border_color)

    def apply_accessibility(self, font_scale: float, high_contrast: bool) -> None:
        self.font_scale = clamp_scale(font_scale)
        self.high_contrast = high_contrast
        palette = contrast_palette(high_contrast)
        self._apply_widget_style(self, palette)
        self.set_selected(self._selected)

    def _apply_widget_style(self, widget: tk.Misc, palette: dict[str, str]) -> None:
        try:
            current_font = widget.cget("font")
            if current_font:
                base_font = getattr(widget, "_webjam_base_font", None)
                if base_font is None:
                    f = tkfont.Font(font=current_font)
                    base_font = (
                        f.actual("family"),
                        abs(int(f.actual("size") or 10)),
                        f.actual("weight"),
                    )
                    setattr(widget, "_webjam_base_font", base_font)
                family, base_size, weight = base_font
                size = scaled_font_size(base_size, self.font_scale)
                widget.configure(font=(family, size, weight))
        except (tk.TclError, RuntimeError, ValueError):
            pass

        try:
            if CTK_AVAILABLE:
                if "text_color" in widget.configure():
                    widget.configure(text_color=palette["fg"])
                if "fg_color" in widget.configure() and self.high_contrast:
                    widget.configure(fg_color=palette["bg"])
            else:
                if "fg" in widget.configure():
                    widget.configure(fg=palette["fg"])
                if "bg" in widget.configure():
                    widget.configure(bg=palette["bg"])
        except tk.TclError:
            pass

        for child in widget.winfo_children():
            self._apply_widget_style(child, palette)
