"""
WebJam Enhanced - Creative Collaboration Platform with Jamulus Integration
Integrates Jamulus low-latency audio with Webex video conferencing
Features a mode-based room flow and shared session canvas
"""

import tkinter as tk
import tkinter.font as tkfont
from tkinter import messagebox, simpledialog
import subprocess
import webbrowser
import logging
import json
import os
import time
import threading
from dataclasses import asdict, replace
from pathlib import Path
from typing import Optional, Dict, Any, Callable
import socket

# Import Jamulus controller
from jamulus_controller import JamulusController, JamulusAudioMonitor, JamulusParticipant
from webex_integration import WebexController
from admin.admin_panel import AdminPanel
from admin.policy import PolicyEngine, UserContext
from api.local_bridge import LocalApiBridge
from core.logging_config import configure_logging, configure_sentry
from core.settings import AppSettings, load_settings
from storage.repository import WebJamRepository
from ui.theme import DEFAULT_THEME
from ui.accessibility import clamp_scale, scaled_font_size, contrast_palette
from ui.auth_controller import AuthController
from ui.ux_status import classify_latency_ms, readiness_state, connection_summary
from ui.dialogs import show_usage_metrics_window
from ui.preferences import UiPreferencesService, _is_valid_geometry
from ui.services import MetricsService, RetryService
from ui.views.diagnostics_panel import show_diagnostics_panel
from ui.views.ready_check_panel import show_ready_check_panel
from ui.views.session_canvas import SessionCanvasPanel
from ui.views.setup_wizard import SetupWizard
from ui.views.tooltip import Tooltip
from core.creative_modes import CREATIVE_MODES, get_mode_by_label_or_default, get_mode_by_key_or_default
from core.session_templates import get_templates_for_mode, SESSION_TEMPLATES

# Try to use customtkinter for modern UI
try:
    import customtkinter as ctk
    CTK_AVAILABLE = True
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")
except ImportError:
    CTK_AVAILABLE = False
    print("Note: Install customtkinter for better UI (pip install customtkinter)")

# ====== CONFIG ======
BASE_SETTINGS = load_settings()
LOGGER = configure_logging(BASE_SETTINGS).getChild("app")
configure_sentry(BASE_SETTINGS)
THEME = DEFAULT_THEME
DEFAULT_JAMULUS_SERVER = BASE_SETTINGS.jamulus_server
DEFAULT_JAMULUS_PORT = int(BASE_SETTINGS.jamulus_port)
DEFAULT_WEBEX_URL = BASE_SETTINGS.webex_url
CONFIG_FILE = Path(BASE_SETTINGS.config_file)
MIX_FILE = Path(BASE_SETTINGS.mix_file)
JAMULUS_CANDIDATES = BASE_SETTINGS.jamulus_candidates
CUSTOM_TEMPLATE_OPTION = "— Custom —"
VALID_REVIEW_STATES = {"draft", "review", "final"}
RECONNECT_MAX_ATTEMPTS = 5
RECONNECT_BASE_DELAY_SECONDS = 1.5
RECONNECT_MAX_DELAY_SECONDS = 45.0
SERVICE_START_MAX_ATTEMPTS = 3
SERVICE_START_RETRY_DELAY_MS = 1500
LOCAL_PARTICIPANT_NAME = "You (Local)"
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


class WebJamEnhancedApp:
    """Enhanced WebJam application with full Jamulus integration"""
    
    def __init__(self):
        # Setup main window
        if CTK_AVAILABLE:
            self.root = ctk.CTk()
        else:
            self.root = tk.Tk()
            self.root.configure(bg=THEME.bg_primary)
        
        self.root.title("WebJam - Creative Collaboration Platform")
        self.root.geometry("1600x900")
        self.root.minsize(1280, 760)
        
        self.jamulus_process: Optional[subprocess.Popen] = None
        self.mixer_channels: Dict[int, EnhancedMixerChannel] = {}
        self.jamulus_state = "Not launched"
        self.webex_state = "Not opened"
        self.network_latency_ms: float | None = None
        self._latency_probe_inflight = False
        self._service_bootstrapped = False
        self._service_start_inflight = False
        self._service_start_attempts = 0
        self._startup_started_at = time.perf_counter()
        self._tooltips = []
        self._poll_after_id: str | None = None
        self._vu_after_id: str | None = None
        self._service_start_after_id: str | None = None
        self.font_scale = 1.0
        self.high_contrast_enabled = False
        self.auto_setup_enabled = True
        self.auto_reconnect_enabled = True
        self._shutdown_requested = False
        self._jamulus_launch_intended = False
        self._webex_launch_intended = False
        self._jamulus_reconnect_attempts = 0
        self._webex_reconnect_attempts = 0
        self._jamulus_next_reconnect_at = 0.0
        self._webex_next_reconnect_at = 0.0
        self._jamulus_reconnect_inflight = False
        self._webex_reconnect_inflight = False
        self._ready_check_inflight = False
        self._diagnostics_panel_inflight = False
        self.repository = WebJamRepository()
        self.repository.ensure_default_admin()
        raw_auto_reconnect = self.repository.get_setting("auto_reconnect_enabled", "1")
        self.auto_reconnect_enabled = str(raw_auto_reconnect).strip().lower() in {"1", "true", "yes", "on"}
        self.jamulus_server = DEFAULT_JAMULUS_SERVER
        self.jamulus_port = DEFAULT_JAMULUS_PORT
        self.webex_url = DEFAULT_WEBEX_URL
        self._refresh_endpoint_state()
        # Initialize controllers after endpoint values are resolved.
        self.jamulus_controller = JamulusController(self.jamulus_server, self.jamulus_port)
        self.audio_monitor = JamulusAudioMonitor(self.jamulus_controller)
        self.webex_controller = WebexController(self.webex_url)
        self.room_key = "default_room"
        saved_room = self.repository.get_room_context(self.room_key)
        raw_mode_key = saved_room.get("mode_key", "music_jam")
        mode_key = str(raw_mode_key).strip() if raw_mode_key is not None else ""
        active_mode = get_mode_by_key_or_default(mode_key)
        self.mode_key = active_mode.key
        raw_template_name = saved_room.get("template_name", active_mode.default_template)
        self.template_name = str(raw_template_name).strip() if raw_template_name is not None else ""
        if not self.template_name:
            self.template_name = active_mode.default_template
        raw_session_goal = saved_room.get("session_goal", active_mode.default_goal)
        self.session_goal_text = str(raw_session_goal).strip() if raw_session_goal is not None else ""
        if not self.session_goal_text:
            self.session_goal_text = active_mode.default_goal
        self.review_state = self._normalize_review_state(saved_room.get("review_state", "draft"))
        self.cohort_name = self.repository.get_setting("cohort_name", "mixed_discipline") or "mixed_discipline"
        self.preferences_service = UiPreferencesService(self.repository)
        self.metrics_service = MetricsService(self.repository)
        self._load_accessibility_preferences()
        self.root.geometry(self.preferences_service.get_window_geometry())
        self.policy = PolicyEngine()
        self.auth_controller = AuthController(self.repository, self.policy)
        self.current_user: Optional[UserContext] = None
        self.selected_channel_id: int | None = None
        self._pending_mix_restore_payload: dict[str, Any] | None = None
        self._pending_mix_restore_source: str | None = None
        self.api_bridge = LocalApiBridge(
            get_participants=self._bridge_participants,
            get_diagnostics=self.jamulus_controller.get_audio_diagnostics,
        )
        self.setup_ui()
        self.save_room_context()
        self._bind_shortcuts()
        self._apply_accessibility_mode()
        
        # Register callback for participant updates
        self.jamulus_controller.register_callback(self.on_participants_updated)
        
        # Start UI update loop
        self.update_vu_meters()
        self._refresh_readiness()
        self._poll_connection_health()
        self._defer_service_start()
        self._show_setup_once()
        self._restore_startup_mix_default()

    def setup_ui(self):
        """Setup the main application UI"""
        # Top menu bar
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)
        
        # File menu
        file_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="File", menu=file_menu)
        file_menu.add_command(label="Save Mix", command=self.save_mix)
        file_menu.add_command(label="Load Mix", command=self.load_mix)
        file_menu.add_separator()
        file_menu.add_command(label="Save Listening Profile", command=self.save_listening_profile)
        file_menu.add_command(label="Load Listening Profile", command=self.load_listening_profile)
        file_menu.add_command(label="Delete Listening Profile", command=self.delete_listening_profile)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.quit_app)
        
        # Session menu
        session_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Session", menu=session_menu)
        session_menu.add_command(label="Launch Jamulus", command=self.launch_jamulus)
        session_menu.add_command(label="Launch Webex", command=self.launch_webex)
        session_menu.add_separator()
        session_menu.add_command(label="Run Ready Check", command=self.run_ready_check)
        session_menu.add_command(label="Audio Diagnostics", command=self.show_audio_diagnostics)
        session_menu.add_command(label="Open Diagnostics Panel", command=self.open_diagnostics_panel)
        session_menu.add_command(label="Export Diagnostics Bundle", command=self.export_diagnostics_bundle)
        session_menu.add_command(label="Export Session Brief", command=self.export_session_brief)
        session_menu.add_separator()
        session_menu.add_command(label="Add Demo Participants", command=self.add_test_participants)

        admin_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Admin", menu=admin_menu)
        admin_menu.add_command(label="Sign In", command=self.sign_in)
        admin_menu.add_command(label="Open Admin Panel", command=self.open_admin_panel)

        view_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="View", menu=view_menu)
        self.high_contrast_var = tk.BooleanVar(value=self.high_contrast_enabled)
        self.large_text_var = tk.BooleanVar(value=self.font_scale > 1.05)
        view_menu.add_checkbutton(
            label="High Contrast Mode",
            variable=self.high_contrast_var,
            command=self.toggle_high_contrast,
        )
        view_menu.add_checkbutton(
            label="Large Text Mode",
            variable=self.large_text_var,
            command=self.toggle_large_text,
        )
        view_menu.add_separator()
        view_menu.add_command(label="Increase Text Size", command=self.increase_text_size)
        view_menu.add_command(label="Decrease Text Size", command=self.decrease_text_size)

        startup_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Startup", menu=startup_menu)
        self.auto_setup_var = tk.BooleanVar(value=self.auto_setup_enabled)
        self.auto_reconnect_var = tk.BooleanVar(value=self.auto_reconnect_enabled)
        startup_menu.add_checkbutton(
            label="Run Setup Wizard on startup",
            variable=self.auto_setup_var,
            command=self.toggle_auto_setup,
        )
        startup_menu.add_checkbutton(
            label="Auto reconnect services",
            variable=self.auto_reconnect_var,
            command=self.toggle_auto_reconnect,
        )
        startup_menu.add_separator()
        startup_menu.add_command(label="Reset All UI Preferences", command=self.reset_all_ui_preferences)
        startup_menu.add_command(label="Reset Window Position", command=self.reset_window_geometry)

        validation_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Validation", menu=validation_menu)
        validation_menu.add_command(label="Set Cohort Name", command=self.set_cohort_name)
        validation_menu.add_command(label="Record Session Complete", command=self.record_session_complete)
        
        # Help menu
        help_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Help", menu=help_menu)
        help_menu.add_command(label="About", command=self.show_about)
        help_menu.add_command(label="Quick Start Guide", command=self.show_help)
        help_menu.add_command(label="Run Ready Check", command=self.run_ready_check)
        help_menu.add_command(label="Run Setup Wizard", command=self.show_setup_wizard)
        help_menu.add_separator()
        help_menu.add_command(label="View Usage Metrics", command=self.show_usage_metrics)
        
        # Top control bar
        control_bar = ctk.CTkFrame(self.root) if CTK_AVAILABLE else tk.Frame(self.root, bg="#2b2b2b", relief=tk.RAISED, borderwidth=2)
        control_bar.pack(fill=tk.X, padx=10, pady=10)
        
        # Logo/Title
        title_text = "WebJam Creative"
        title = self._create_label(control_bar, title_text, font_size=20, bold=True)
        title.pack(side=tk.LEFT, padx=15)
        
        # Status indicator
        self.status_indicator = self._create_label(control_bar, "●", font_size=16)
        self.status_indicator.pack(side=tk.LEFT, padx=5)
        
        self.status_label = self._create_label(control_bar, "Ready to connect", font_size=11)
        self.status_label.pack(side=tk.LEFT)
        
        mode_frame = tk.Frame(control_bar, bg="#2b2b2b" if not CTK_AVAILABLE else None)
        mode_frame.pack(side=tk.LEFT, padx=12)
        self._create_label(mode_frame, "Mode", font_size=9, bold=True).pack(anchor="w")
        self.mode_var = tk.StringVar(value=get_mode_by_key_or_default(self.mode_key).label)
        mode_menu = tk.OptionMenu(mode_frame, self.mode_var, *[m.label for m in CREATIVE_MODES], command=self.on_mode_selected)
        mode_menu.configure(width=18)
        mode_menu.pack(anchor="w")

        quick_frame = tk.Frame(control_bar, bg="#2b2b2b" if not CTK_AVAILABLE else None)
        quick_frame.pack(side=tk.LEFT, padx=8)
        self._create_label(quick_frame, "Quick templates", font_size=9, bold=True).pack(anchor="w")
        template_labels = [CUSTOM_TEMPLATE_OPTION] + [t.label for t in get_templates_for_mode(self.mode_key)]
        self.quick_template_var = tk.StringVar(value=CUSTOM_TEMPLATE_OPTION)
        self.quick_template_menu = tk.OptionMenu(quick_frame, self.quick_template_var, *template_labels, command=self._on_quick_template_selected)
        self.quick_template_menu.configure(width=16)
        self.quick_template_menu.pack(anchor="w")

        template_frame = tk.Frame(control_bar, bg="#2b2b2b" if not CTK_AVAILABLE else None)
        template_frame.pack(side=tk.LEFT, padx=8)
        self._create_label(template_frame, "Template", font_size=9, bold=True).pack(anchor="w")
        self.template_var = tk.StringVar(value=self.template_name)
        self.template_entry = tk.Entry(template_frame, textvariable=self.template_var, width=28)
        self.template_entry.pack(anchor="w")
        self.template_entry.bind("<FocusOut>", lambda _e: self.save_room_context())

        goal_frame = tk.Frame(control_bar, bg="#2b2b2b" if not CTK_AVAILABLE else None)
        goal_frame.pack(side=tk.LEFT, padx=8, fill=tk.X, expand=True)
        self._create_label(goal_frame, "Session Goal", font_size=9, bold=True).pack(anchor="w")
        self.session_goal_var = tk.StringVar(value=self.session_goal_text)
        self.session_goal_entry = tk.Entry(goal_frame, textvariable=self.session_goal_var, width=74)
        self.session_goal_entry.pack(anchor="w", fill=tk.X)
        self.session_goal_entry.bind("<FocusOut>", lambda _e: self.save_room_context())

        # Control buttons
        btn_frame = tk.Frame(control_bar, bg="#2b2b2b" if not CTK_AVAILABLE else None)
        btn_frame.pack(side=tk.RIGHT, padx=10)
        
        self.launch_jamulus_btn = self._create_button(btn_frame, "🎵 Launch Jamulus", self.launch_jamulus)
        self.launch_jamulus_btn.pack(side=tk.LEFT, padx=5)
        self.launch_webex_btn = self._create_button(btn_frame, "📹 Launch Webex", self.launch_webex)
        self.launch_webex_btn.pack(side=tk.LEFT, padx=5)
        self.save_mix_btn = self._create_button(btn_frame, "💾 Save Mix", self.save_mix)
        self.save_mix_btn.pack(side=tk.LEFT, padx=5)

        hint_text = "Tip: pick mode + goal first. Launch is deferred for faster startup. We're making something together."
        self.hint_label = self._create_label(control_bar, hint_text, font_size=9)
        self.hint_label.pack(side=tk.BOTTOM, fill=tk.X, padx=15, pady=(4, 0))
        
        # Main content area
        content = ctk.CTkFrame(self.root) if CTK_AVAILABLE else tk.Frame(self.root, bg="#1a1a1a")
        content.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))
        
        splitter = tk.PanedWindow(content, orient=tk.HORIZONTAL, sashrelief=tk.RAISED, sashwidth=8, bg="#1a1a1a")
        splitter.pack(fill=tk.BOTH, expand=True)
        left_content = ctk.CTkFrame(splitter) if CTK_AVAILABLE else tk.Frame(splitter, bg="#1a1a1a")
        right_content = tk.Frame(splitter, bg="#1a1a1a")
        splitter.add(left_content, minsize=820)
        splitter.add(right_content, minsize=360)
        self.main_splitter = splitter
        self.left_content = left_content
        self.right_content = right_content

        # Mixer panel (left side)
        mixer_frame = ctk.CTkFrame(left_content) if CTK_AVAILABLE else tk.Frame(left_content, bg="#2b2b2b", relief=tk.RAISED, borderwidth=2)
        mixer_frame.pack(fill=tk.BOTH, expand=True)
        
        # Mixer title
        mixer_title = self._create_label(mixer_frame, "Virtual Mixing Console", font_size=16, bold=True)
        mixer_title.pack(pady=10)
        
        # Master controls
        master_controls = ctk.CTkFrame(mixer_frame) if CTK_AVAILABLE else tk.Frame(mixer_frame, bg="#2b2b2b")
        master_controls.pack(fill=tk.X, padx=10, pady=5)
        
        self.reset_all_btn = self._create_button(master_controls, "Reset All Faders", self.reset_all_faders)
        self.reset_all_btn.pack(side=tk.LEFT, padx=5)
        self.unmute_all_btn = self._create_button(master_controls, "Unmute All", self.unmute_all)
        self.unmute_all_btn.pack(side=tk.LEFT, padx=5)
        self.center_pans_btn = self._create_button(master_controls, "Center All Pans", self.center_all_pans)
        self.center_pans_btn.pack(side=tk.LEFT, padx=5)
        
        # Scrollable mixer channels
        if CTK_AVAILABLE:
            self.channels_container = ctk.CTkScrollableFrame(mixer_frame, height=600, orientation="horizontal")
        else:
            canvas_frame = tk.Frame(mixer_frame, bg="#2b2b2b")
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

        self.session_canvas = SessionCanvasPanel(
            right_content,
            get_mode=lambda: get_mode_by_key_or_default(self.mode_key),
            get_room_context=lambda: self.repository.get_room_context(self.room_key),
            on_review_state_change=self.on_review_state_change,
            list_artifacts=lambda: self.repository.list_session_artifacts(self.room_key),
            add_artifact=lambda title, artifact_type, reference: self.repository.add_session_artifact(self.room_key, title, artifact_type, reference),
            remove_artifact=self.repository.remove_session_artifact,
            load_notes=lambda: self.repository.get_session_notes(self.room_key),
            save_notes=lambda notes: self.repository.save_session_notes(self.room_key, notes),
            bg_color=THEME.bg_secondary,
            fg_color=THEME.text_primary,
        )
        self.session_canvas.configure(width=420)
        self.session_canvas.pack(fill=tk.BOTH, expand=True)
        
        # Bottom status bar
        status_bar = ctk.CTkFrame(self.root) if CTK_AVAILABLE else tk.Frame(self.root, bg="#2b2b2b", relief=tk.SUNKEN, borderwidth=1)
        status_bar.pack(fill=tk.X, padx=10, pady=(0, 10))
        
        self.participants_count = self._create_label(status_bar, "Participants: 0", font_size=9)
        self.participants_count.pack(side=tk.LEFT, padx=10, pady=5)

        self.connection_summary = self._create_label(status_bar, "Jamulus: Not launched | Webex: Not opened", font_size=9)
        self.connection_summary.pack(side=tk.LEFT, padx=10, pady=5)

        self.readiness_label = self._create_label(status_bar, "Room: waiting for participants", font_size=9)
        self.readiness_label.pack(side=tk.LEFT, padx=10, pady=5)

        self.latency_label = self._create_label(status_bar, "Latency: n/a", font_size=9)
        self.latency_label.pack(side=tk.LEFT, padx=10, pady=5)
        
        self.server_info = self._create_label(status_bar, f"Server: {self.jamulus_server}:{self.jamulus_port}", font_size=9)
        self.server_info.pack(side=tk.RIGHT, padx=10, pady=5)

        self._tooltips.extend(
            [
                Tooltip(self.quick_template_menu, "One-click presets for template and goal"),
                Tooltip(self.launch_jamulus_btn, "Start Jamulus and connect to configured server"),
                Tooltip(self.launch_webex_btn, "Open the Webex meeting URL in your browser"),
                Tooltip(self.save_mix_btn, "Save current channel faders/pan/mute state"),
                Tooltip(self.reset_all_btn, "Set all faders to unity gain"),
                Tooltip(self.unmute_all_btn, "Clear mute state on all channels"),
                Tooltip(self.center_pans_btn, "Center all pan controls"),
                Tooltip(self.status_label, "Overall session state and readiness"),
                Tooltip(self.connection_summary, "Current Jamulus and Webex connection states"),
                Tooltip(self.readiness_label, "Mixer readiness based on participant availability"),
                Tooltip(self.latency_label, "Estimated latency quality to Jamulus endpoint"),
            ]
        )
        self._schedule_mode_layout_refresh()
    
    def _create_label(self, parent, text, font_size=10, bold=False):
        """Create a styled label"""
        actual_size = scaled_font_size(font_size, self.font_scale)
        if CTK_AVAILABLE:
            weight = "bold" if bold else "normal"
            return ctk.CTkLabel(parent, text=text, font=("Arial", actual_size, weight))
        else:
            font = ("Arial", actual_size, "bold" if bold else "normal")
            bg = parent.cget("bg") if hasattr(parent, 'cget') else "#2b2b2b"
            return tk.Label(parent, text=text, font=font, bg=bg, fg="white", justify=tk.LEFT)
    
    def _create_button(self, parent, text, command):
        """Create a styled button"""
        button_size = scaled_font_size(11, self.font_scale)
        if CTK_AVAILABLE:
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

    def _runtime_endpoint(self) -> tuple[str, int]:
        raw_host = self.repository.get_setting("jamulus_server", DEFAULT_JAMULUS_SERVER)
        host = str(raw_host).strip() if raw_host is not None else DEFAULT_JAMULUS_SERVER
        if not host:
            host = DEFAULT_JAMULUS_SERVER
        raw_port = self.repository.get_setting("jamulus_port", str(DEFAULT_JAMULUS_PORT))
        try:
            port = int(raw_port) if raw_port is not None else DEFAULT_JAMULUS_PORT
        except (TypeError, ValueError):
            port = DEFAULT_JAMULUS_PORT
        if not (1 <= port <= 65535):
            port = DEFAULT_JAMULUS_PORT
        return host, port

    def _refresh_endpoint_state(self) -> None:
        self.jamulus_server, self.jamulus_port = self._runtime_endpoint()
        if hasattr(self, "jamulus_controller"):
            self.jamulus_controller.host = self.jamulus_server
            self.jamulus_controller.port = self.jamulus_port
            self.jamulus_controller.protocol.host = self.jamulus_server
            self.jamulus_controller.protocol.port = self.jamulus_port
        if hasattr(self, "server_info"):
            self.server_info.configure(text=f"Server: {self.jamulus_server}:{self.jamulus_port}")

    def _settings_for_checks(self) -> AppSettings:
        self._refresh_endpoint_state()
        return replace(
            BASE_SETTINGS,
            jamulus_server=self.jamulus_server,
            jamulus_port=self.jamulus_port,
            webex_url=self.webex_url,
        )

    def _refresh_quick_template_menu(self) -> None:
        if not hasattr(self, "quick_template_menu"):
            return
        labels = [CUSTOM_TEMPLATE_OPTION] + [t.label for t in get_templates_for_mode(self.mode_key)]
        menu = self.quick_template_menu["menu"]
        menu.delete(0, tk.END)
        for label in labels:
            menu.add_command(
                label=label,
                command=tk._setit(self.quick_template_var, label, self._on_quick_template_selected),
            )
        self.quick_template_var.set(CUSTOM_TEMPLATE_OPTION)

    @staticmethod
    def _mode_layout_spec(mode_key: str) -> dict[str, float]:
        spec = dict(DEFAULT_MODE_LAYOUT)
        spec.update(MODE_LAYOUT_PRESETS.get(str(mode_key).strip(), {}))
        return spec

    @staticmethod
    def _compute_sash_x(total_width: int, mixer_ratio: float, min_mixer: int, min_canvas: int) -> int:
        width = max(1, int(total_width))
        ratio = max(0.1, min(float(mixer_ratio), 0.9))
        lower_bound = max(1, int(min_mixer))
        upper_bound = max(lower_bound, width - max(1, int(min_canvas)))
        desired = int(width * ratio)
        return max(lower_bound, min(desired, upper_bound))

    def _apply_mode_layout(self) -> None:
        spec = self._mode_layout_spec(self.mode_key)
        if hasattr(self, "session_canvas"):
            try:
                self.session_canvas.configure(width=int(spec["canvas_width"]))
            except (tk.TclError, RuntimeError, ValueError):
                pass

        splitter = getattr(self, "main_splitter", None)
        if splitter is None:
            return

        try:
            if hasattr(self, "left_content"):
                splitter.paneconfig(self.left_content, minsize=int(spec["min_mixer"]))
            if hasattr(self, "right_content"):
                splitter.paneconfig(self.right_content, minsize=int(spec["min_canvas"]))
        except (tk.TclError, RuntimeError, ValueError):
            pass

        try:
            total_width = int(self.root.winfo_width())
            if total_width <= 1:
                total_width = int(self.root.winfo_reqwidth())
            if total_width <= 1:
                total_width = 1600
            sash_x = self._compute_sash_x(
                total_width=total_width,
                mixer_ratio=float(spec["mixer_ratio"]),
                min_mixer=int(spec["min_mixer"]),
                min_canvas=int(spec["min_canvas"]),
            )
            splitter.sash_place(0, sash_x, 1)
        except (tk.TclError, RuntimeError, ValueError, TypeError):
            return

    def _schedule_mode_layout_refresh(self) -> None:
        self._apply_mode_layout()
        root = getattr(self, "root", None)
        if root is None:
            return
        try:
            root.after(120, self._apply_mode_layout)
        except (tk.TclError, RuntimeError, AttributeError):
            return

    def _available_profile_entries(self) -> list[dict[str, str]]:
        current_mode_profiles = self.repository.list_mix_profiles(self.mode_key)
        seen_names = {profile["profile_name"] for profile in current_mode_profiles}
        all_profiles = self.repository.list_mix_profiles()
        other_profiles = [profile for profile in all_profiles if profile["profile_name"] not in seen_names]
        return current_mode_profiles + other_profiles

    def _profile_prompt_text(self, action: str, profiles: list[dict[str, str]]) -> str:
        lines = [f"{action} listening profile", ""]
        if not profiles:
            lines.append("No profiles saved yet.")
            return "\n".join(lines)
        lines.append("Available profiles:")
        for profile in profiles[:10]:
            mode_label = get_mode_by_key_or_default(profile.get("mode_key", "")).label
            lines.append(f"- {profile['profile_name']} ({mode_label})")
        if len(profiles) > 10:
            lines.append(f"...and {len(profiles) - 10} more")
        lines.append("")
        lines.append("Enter the profile name exactly as shown.")
        return "\n".join(lines)

    @staticmethod
    def _resolve_profile_name(name: str, profiles: list[dict[str, str]]) -> str | None:
        normalized = str(name or "").strip()
        if not normalized:
            return None
        by_casefold = {profile["profile_name"].casefold(): profile["profile_name"] for profile in profiles}
        return by_casefold.get(normalized.casefold())

    def _refresh_mixer_controls_from_participants(self) -> None:
        for channel_id, channel in list(getattr(self, "mixer_channels", {}).items()):
            try:
                p = channel.participant
                channel.fader.set(p.fader_level)
                channel.pan_slider.set(p.pan)
                channel.update_button_states()
            except (KeyError, tk.TclError, AttributeError):
                continue

    def _select_mixer_channel(self, channel_id: int | None) -> None:
        if channel_id is not None and channel_id not in self.mixer_channels:
            channel_id = None
        self.selected_channel_id = channel_id
        for current_id, channel in list(getattr(self, "mixer_channels", {}).items()):
            try:
                channel.set_selected(current_id == channel_id)
            except (tk.TclError, AttributeError):
                continue

    def _selected_mixer_channel(self) -> EnhancedMixerChannel | None:
        if self.selected_channel_id is None:
            return None
        return self.mixer_channels.get(self.selected_channel_id)

    def _default_mix_user(self) -> str | None:
        if self.current_user is None:
            return None
        username = str(getattr(self.current_user, "username", "") or "").strip()
        return username or None

    def _load_mix_payload_from_file(self, mix_file: Path | None = None) -> Optional[dict[str, Any]]:
        mix_path = mix_file or MIX_FILE
        try:
            with Path(mix_path).open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            LOGGER.warning("Could not read mix payload from %s: %s", mix_path, exc)
            return None
        if not isinstance(payload, dict):
            LOGGER.warning("Mix payload from %s was not a JSON object.", mix_path)
            return None
        return payload

    def _queue_mix_restore(self, payload: dict[str, Any], source_label: str) -> None:
        self._pending_mix_restore_payload = dict(payload)
        self._pending_mix_restore_source = str(source_label)
        self._attempt_pending_mix_restore()

    def _attempt_pending_mix_restore(self) -> None:
        payload = getattr(self, "_pending_mix_restore_payload", None)
        if not isinstance(payload, dict):
            return
        applied_channels = self.jamulus_controller.apply_mix_data(payload)
        if applied_channels is None:
            LOGGER.warning(
                "Discarding invalid pending mix restore payload from %s.",
                self._pending_mix_restore_source or "unknown source",
            )
            self._pending_mix_restore_payload = None
            self._pending_mix_restore_source = None
            return
        if applied_channels <= 0:
            return
        self._pending_mix_restore_payload = None
        restored_from = self._pending_mix_restore_source or "saved mix"
        self._pending_mix_restore_source = None
        self._refresh_mixer_controls_from_participants()
        self._refresh_readiness()
        try:
            self._set_status_banner(f"Restored mix from {restored_from}", color="#00cc66")
        except (tk.TclError, RuntimeError, AttributeError):
            pass

    def _restore_startup_mix_default(self) -> None:
        if not MIX_FILE.exists():
            return
        payload = self._load_mix_payload_from_file()
        if payload is None:
            return
        self._queue_mix_restore(payload, str(MIX_FILE))

    def _restore_signed_in_mix_default(self) -> None:
        username = self._default_mix_user()
        if not username:
            return
        saved_mix = self.repository.get_user_mix_default(username)
        if saved_mix is None:
            return
        payload = saved_mix.get("payload")
        if not isinstance(payload, dict):
            LOGGER.warning("Saved user mix for '%s' was not a mapping payload.", username)
            return
        self._queue_mix_restore(payload, f"{username} profile")

    def _bridge_participants(self):
        return [
            {
                "channel_id": p.channel_id,
                "name": p.name,
                "fader_level": p.fader_level,
                "pan": p.pan,
                "muted": p.muted,
                "solo": p.solo,
            }
            for p in self.jamulus_controller.get_participants()
        ]

    def _show_setup_once(self):
        if not self.auto_setup_enabled:
            return
        setup_seen = self.repository.get_setting("setup_completed", "0")
        if str(setup_seen).strip().lower() in {"1", "true", "yes", "on"}:
            return

        self.show_setup_wizard(mark_complete=True)

    def show_setup_wizard(self, mark_complete: bool = False):
        self.metrics_service.increment("metric_setup_wizard_opened")
        active_mode = get_mode_by_key_or_default(self.mode_key)

        def on_complete() -> None:
            if mark_complete:
                self.repository.set_setting("setup_completed", "1")
            self.metrics_service.increment("metric_setup_wizard_completed")

        SetupWizard(
            self.root,
            on_complete=on_complete,
            settings=self._settings_for_checks(),
            find_jamulus=self.find_jamulus,
            diagnostics_provider=self.jamulus_controller.get_audio_diagnostics,
            mode_label=active_mode.label,
            mode_help=active_mode.quick_help,
        ).show()

    @staticmethod
    def _summarize_ready_check(
        check_results: list[tuple[str, bool, str]],
        latency_ms: float | None,
        participant_count: int,
    ) -> dict[str, object]:
        total = len(check_results)
        passed = sum(1 for _name, ok, _detail in check_results if ok)
        failed = [name for name, ok, _detail in check_results if not ok]
        latency_label, _latency_color = classify_latency_ms(latency_ms)
        if failed:
            summary = (
                f"Not ready ({passed}/{total} checks passed). "
                f"Fix: {', '.join(failed)}."
            )
        else:
            summary = f"Ready ({passed}/{total} checks passed)."
        return {
            "summary": summary,
            "passed": passed,
            "total": total,
            "failed": failed,
            "latency_label": latency_label,
            "participant_count": participant_count,
        }

    def run_ready_check(self) -> None:
        self.metrics_service.increment("metric_ready_check_run")
        settings = self._settings_for_checks()
        latency_ms = self.network_latency_ms
        participant_count = len(self.jamulus_controller.get_participants())

        def _task() -> list[tuple[str, bool, str]]:
            return SetupWizard.run_preflight_checks(
                settings=settings,
                find_jamulus=self.find_jamulus,
                diagnostics_provider=self.jamulus_controller.get_audio_diagnostics,
            )

        def _show_panel(check_results: list[tuple[str, bool, str]]) -> None:
            report = self._summarize_ready_check(
                check_results=check_results,
                latency_ms=latency_ms,
                participant_count=participant_count,
            )
            show_ready_check_panel(
                root=self.root,
                checks=check_results,
                latency_label=str(report["latency_label"]),
                participant_count=int(report["participant_count"]),
                on_run_setup=lambda: self.show_setup_wizard(mark_complete=False),
                on_open_diagnostics=self.open_diagnostics_panel,
                on_export_bundle=self.export_diagnostics_bundle,
                bg_color=THEME.bg_secondary,
                fg_color=THEME.text_primary,
            )

        self._run_background_task(
            inflight_attr="_ready_check_inflight",
            banner_text="Running ready check...",
            task=_task,
            on_success=_show_panel,
            error_title="Ready Check Failed",
            what_failed="Ready check could not complete.",
            likely_cause="A diagnostics callback or network probe failed unexpectedly.",
            next_action="Retry the ready check or open diagnostics for more detail.",
            retry_callback=self.run_ready_check,
        )

    def _on_quick_template_selected(self, choice: str) -> None:
        if choice == CUSTOM_TEMPLATE_OPTION:
            return
        for t in SESSION_TEMPLATES:
            if t.label == choice:
                self.template_var.set(t.template_name)
                self.session_goal_var.set(t.session_goal)
                if t.mode_key and t.mode_key != self.mode_key:
                    self.mode_key = t.mode_key
                    self.mode_var.set(get_mode_by_key_or_default(t.mode_key).label)
                    self._refresh_quick_template_menu()
                self.save_room_context()
                self.session_canvas.refresh()
                self._schedule_mode_layout_refresh()
                self.metrics_service.increment(f"metric_quick_template_{t.id}")
                break

    def on_mode_selected(self, mode_label: str) -> None:
        selected = get_mode_by_label_or_default(mode_label)
        self.mode_key = selected.key
        self._refresh_quick_template_menu()
        if not self.template_var.get().strip():
            self.template_var.set(selected.default_template)
        if not self.session_goal_var.get().strip():
            self.session_goal_var.set(selected.default_goal)
        self.save_room_context()
        self.session_canvas.refresh()
        self._schedule_mode_layout_refresh()
        self.metrics_service.increment(f"metric_mode_selected_{selected.key}")
        self.repository.append_cohort_event(
            self.cohort_name,
            "mode_selected",
            {"mode_key": selected.key, "template_name": self.template_var.get().strip()},
        )

    def on_review_state_change(self, state: str) -> None:
        self.review_state = self._normalize_review_state(state)
        self.save_room_context()
        self.repository.append_cohort_event(
            self.cohort_name,
            "review_state_changed",
            {"state": self.review_state, "mode_key": self.mode_key},
        )

    @staticmethod
    def _normalize_review_state(value: object) -> str:
        normalized = str(value).strip().lower() if value is not None else "draft"
        if normalized not in VALID_REVIEW_STATES:
            return "draft"
        return normalized

    def save_room_context(self) -> None:
        active_mode = get_mode_by_key_or_default(self.mode_key)
        self.mode_key = active_mode.key
        self.review_state = self._normalize_review_state(self.review_state)
        template_value = self.template_var.get() if hasattr(self, "template_var") else self.template_name
        session_goal_value = self.session_goal_var.get() if hasattr(self, "session_goal_var") else self.session_goal_text
        template_name = str(template_value).strip() if template_value is not None else ""
        session_goal = str(session_goal_value).strip() if session_goal_value is not None else ""
        active_mode = get_mode_by_key_or_default(self.mode_key)
        if not template_name:
            template_name = active_mode.default_template
            if hasattr(self, "template_var"):
                self.template_var.set(template_name)
        if not session_goal:
            session_goal = active_mode.default_goal
            if hasattr(self, "session_goal_var"):
                self.session_goal_var.set(session_goal)
        self.template_name = template_name
        self.session_goal_text = session_goal
        self.repository.upsert_room_context(
            self.room_key,
            mode_key=self.mode_key,
            template_name=self.template_name,
            session_goal=self.session_goal_text,
            review_state=self.review_state,
        )
        self._refresh_readiness()

    def _flush_live_room_state(self) -> bool:
        try:
            self.save_room_context()
        except Exception as exc:
            messagebox.showerror("Save Failed", f"Could not save room details:\n{exc}", parent=self.root)
            return False

        session_canvas = getattr(self, "session_canvas", None)
        if session_canvas is None:
            return True
        save_notes_if_dirty = getattr(session_canvas, "save_notes_if_dirty", None)
        if save_notes_if_dirty is None:
            return True
        try:
            result = save_notes_if_dirty()
        except Exception as exc:
            messagebox.showerror("Save Failed", f"Could not save session notes:\n{exc}", parent=self.root)
            return False
        return result is not False

    def _set_status_banner(self, text: str, color: str = "#ffffff") -> None:
        self.status_label.configure(text=text)
        try:
            if CTK_AVAILABLE:
                self.status_indicator.configure(text_color=color)
            else:
                self.status_indicator.configure(fg=color)
        except tk.TclError:
            pass

    def _schedule_ui_callback(self, callback, delay_ms: int = 0) -> bool:
        root = getattr(self, "root", None)
        if root is None:
            return False
        if hasattr(root, "winfo_exists"):
            try:
                if not root.winfo_exists():
                    return False
            except (tk.TclError, RuntimeError):
                return False
        try:
            root.after(delay_ms, callback)
            return True
        except (AttributeError, tk.TclError, RuntimeError):
            return False

    def _shutdown_requested_active(self) -> bool:
        return bool(getattr(self, "_shutdown_requested", False))

    def _clear_launch_intent(self) -> None:
        self._jamulus_launch_intended = False
        self._webex_launch_intended = False
        self._jamulus_reconnect_attempts = 0
        self._webex_reconnect_attempts = 0
        self._jamulus_next_reconnect_at = 0.0
        self._webex_next_reconnect_at = 0.0
        self._jamulus_reconnect_inflight = False
        self._webex_reconnect_inflight = False

    def _request_shutdown(self) -> None:
        self._shutdown_requested = True
        self._clear_launch_intent()

    @staticmethod
    def _terminate_process(process) -> None:
        if process is None:
            return
        try:
            process.terminate()
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
                process.wait(timeout=1)
            except (OSError, ProcessLookupError, subprocess.TimeoutExpired):
                LOGGER.warning("Jamulus process did not exit cleanly after termination request.")
        except (OSError, ProcessLookupError):
            pass

    def _defer_service_start(self) -> None:
        self._set_status_banner("Initializing background services...", color="#ffcc00")
        self._service_start_after_id = self.root.after(120, self._kick_background_services)

    def _kick_background_services(self) -> None:
        self._service_start_after_id = None
        if self._shutdown_requested_active():
            return
        if self._service_bootstrapped or self._service_start_inflight:
            return
        self._service_start_inflight = True
        threading.Thread(target=self._start_background_services, daemon=True).start()

    def _start_background_services(self) -> None:
        if self._shutdown_requested_active():
            self._service_start_inflight = False
            return
        attempt = int(getattr(self, "_service_start_attempts", 0)) + 1
        self._service_start_attempts = attempt
        bridge_started = self.api_bridge.start()
        if not bridge_started:
            LOGGER.warning("Local companion API bridge unavailable (install fastapi + uvicorn).")
        jamulus_started = False
        audio_monitor_started = False
        webex_started = False

        def _rollback_started_services() -> None:
            for stop_callback, _label, started in (
                (self.webex_controller.stop, "webex controller", webex_started),
                (self.audio_monitor.stop, "audio monitor", audio_monitor_started),
                (self.jamulus_controller.stop, "jamulus controller", jamulus_started),
                (self.api_bridge.stop, "local api bridge", bridge_started),
            ):
                if not started:
                    continue
                try:
                    stop_callback()
                except Exception:
                    pass

        try:
            if self._shutdown_requested_active():
                raise RuntimeError("shutdown requested")
            self.jamulus_controller.start()
            jamulus_started = True
            if self._shutdown_requested_active():
                raise RuntimeError("shutdown requested")
            self.audio_monitor.start()
            audio_monitor_started = True
            if self._shutdown_requested_active():
                raise RuntimeError("shutdown requested")
            self.webex_controller.start()
            webex_started = True
        except Exception as exc:
            if not self._shutdown_requested_active():
                LOGGER.exception("Service startup failed: %s", exc)
            _rollback_started_services()
            self._service_bootstrapped = False
            self._service_start_inflight = False
            if self._shutdown_requested_active():
                return
            should_retry = attempt < SERVICE_START_MAX_ATTEMPTS

            def _handle_failure() -> None:
                if should_retry:
                    self._set_status_banner(
                        f"Service startup issue; retrying ({attempt}/{SERVICE_START_MAX_ATTEMPTS})",
                        color="#ff5555",
                    )
                    self._service_start_after_id = self.root.after(
                        SERVICE_START_RETRY_DELAY_MS,
                        self._kick_background_services,
                    )
                    return
                self._set_status_banner("Service startup issue (see diagnostics)", color="#ff5555")

            self._schedule_ui_callback(_handle_failure)
            return

        if self._shutdown_requested_active():
            _rollback_started_services()
            self._service_bootstrapped = False
            self._service_start_inflight = False
            return

        self._service_bootstrapped = True
        self._service_start_inflight = False
        self._service_start_attempts = 0
        startup_elapsed = time.perf_counter() - self._startup_started_at
        self._schedule_ui_callback(lambda: self._set_status_banner(f"Ready ({startup_elapsed:.1f}s startup)", color="#00cc66"))
        self._schedule_ui_callback(self._refresh_readiness)

    def _safe_invoke(self, callback):
        try:
            callback()
        except Exception as exc:
            LOGGER.exception("Shortcut action failed: %s", exc)
            messagebox.showwarning("Action Failed", f"Shortcut action failed:\n{exc}", parent=self.root)

    def _text_input_has_focus(self) -> bool:
        focused = self.root.focus_get()
        if isinstance(focused, (tk.Entry, tk.Text)):
            return True
        try:
            return bool(CTK_AVAILABLE and hasattr(focused, "_entry"))
        except tk.TclError:
            return False

    def _selected_channel_shortcut_handler(self, action: str):
        if self._text_input_has_focus():
            return
        channel = self._selected_mixer_channel()
        if channel is None:
            return "break"
        if action == "mute":
            channel.toggle_mute()
        elif action == "solo":
            channel.toggle_solo()
        else:
            return
        self._refresh_readiness()
        return "break"

    def _run_background_task(
        self,
        *,
        inflight_attr: str,
        banner_text: str,
        task: Callable[[], Any],
        on_success: Callable[[Any], None],
        error_title: str,
        what_failed: str,
        likely_cause: str,
        next_action: str,
        retry_callback: Callable[[], None] | None = None,
    ) -> None:
        if bool(getattr(self, inflight_attr, False)):
            return
        setattr(self, inflight_attr, True)
        try:
            self._set_status_banner(banner_text, color="#ffcc00")
        except (tk.TclError, RuntimeError, AttributeError):
            pass

        def _worker() -> None:
            result: Any = None
            error: Exception | None = None
            try:
                result = task()
            except Exception as exc:
                error = exc
                LOGGER.exception("%s", what_failed)

            def _finish() -> None:
                setattr(self, inflight_attr, False)
                if error is not None:
                    self._show_actionable_error(
                        error_title,
                        what_failed=f"{what_failed} ({error}).",
                        likely_cause=likely_cause,
                        next_action=next_action,
                        retry_callback=retry_callback,
                    )
                    self._refresh_readiness()
                    return
                on_success(result)

            try:
                self.root.after(0, _finish)
            except (tk.TclError, RuntimeError, AttributeError):
                setattr(self, inflight_attr, False)

        threading.Thread(target=_worker, daemon=True).start()

    def _retry_action(self, action, attempts: int = 3, base_delay: float = 0.4):
        return RetryService.retry_action(action, attempts=attempts, base_delay=base_delay)

    @staticmethod
    def _reconnect_delay_seconds(attempt: int) -> float:
        bounded_attempt = max(1, int(attempt))
        return min(
            RECONNECT_MAX_DELAY_SECONDS,
            RECONNECT_BASE_DELAY_SECONDS * (2 ** (bounded_attempt - 1)),
        )

    def _attempt_auto_reconnects(self, now: float | None = None) -> None:
        if self._shutdown_requested_active():
            return
        if not getattr(self, "auto_reconnect_enabled", True):
            return
        if now is None:
            now = time.monotonic()
        self._attempt_auto_reconnect_jamulus(now)
        self._attempt_auto_reconnect_webex(now)

    def _attempt_auto_reconnect_jamulus(self, now: float) -> None:
        if not getattr(self, "_jamulus_launch_intended", False):
            return
        process = getattr(self, "jamulus_process", None)
        is_running = process is not None and process.poll() is None
        if is_running:
            self._jamulus_reconnect_attempts = 0
            self._jamulus_next_reconnect_at = 0.0
            self._jamulus_reconnect_inflight = False
            return
        if getattr(self, "_jamulus_reconnect_inflight", False):
            return
        attempts = int(getattr(self, "_jamulus_reconnect_attempts", 0))
        if attempts >= RECONNECT_MAX_ATTEMPTS:
            return
        if now < float(getattr(self, "_jamulus_next_reconnect_at", 0.0)):
            return
        attempts += 1
        self._jamulus_reconnect_attempts = attempts
        self._jamulus_next_reconnect_at = now + self._reconnect_delay_seconds(attempts)
        self._jamulus_reconnect_inflight = True
        self.metrics_service.increment("metric_jamulus_reconnect_attempt")
        self._launch_jamulus(manual=False, reconnect=True)

    def _attempt_auto_reconnect_webex(self, now: float) -> None:
        if not getattr(self, "_webex_launch_intended", False):
            return
        controller = getattr(self, "webex_controller", None)
        if controller is None:
            return
        if getattr(controller, "is_connected", False):
            self._webex_reconnect_attempts = 0
            self._webex_next_reconnect_at = 0.0
            self._webex_reconnect_inflight = False
            return
        if getattr(self, "_webex_reconnect_inflight", False):
            return
        if self.webex_state not in {"Open failed", "Not opened"}:
            return
        attempts = int(getattr(self, "_webex_reconnect_attempts", 0))
        if attempts >= RECONNECT_MAX_ATTEMPTS:
            return
        if now < float(getattr(self, "_webex_next_reconnect_at", 0.0)):
            return
        attempts += 1
        self._webex_reconnect_attempts = attempts
        self._webex_next_reconnect_at = now + self._reconnect_delay_seconds(attempts)
        self._webex_reconnect_inflight = True
        self.metrics_service.increment("metric_webex_reconnect_attempt")
        self._launch_webex(manual=False, reconnect=True)

    def _bind_shortcuts(self) -> None:
        self.root.bind("<Control-s>", lambda _e: self._safe_invoke(self.save_mix))
        self.root.bind("<Control-o>", lambda _e: self._safe_invoke(self.load_mix))
        self.root.bind("<Control-q>", lambda _e: self._safe_invoke(self.quit_app))
        self.root.bind("<F1>", lambda _e: self._safe_invoke(self.show_help))
        self.root.bind("<Control-j>", lambda _e: self._safe_invoke(self.launch_jamulus))
        self.root.bind("<Control-w>", lambda _e: self._safe_invoke(self.launch_webex))
        self.root.bind("<Control-r>", lambda _e: self._safe_invoke(self.reset_all_faders))
        self.root.bind("<Control-p>", lambda _e: self._safe_invoke(self.center_all_pans))
        self.root.bind("<space>", self._space_key_handler)
        self.root.bind("<Key-m>", lambda _e: self._selected_channel_shortcut_handler("mute"))
        self.root.bind("<Key-s>", lambda _e: self._selected_channel_shortcut_handler("solo"))
        self.root.bind("<Control-plus>", lambda _e: self._safe_invoke(self.increase_text_size))
        self.root.bind("<Control-minus>", lambda _e: self._safe_invoke(self.decrease_text_size))
        self.root.bind("<Control-h>", lambda _e: self._safe_invoke(self.toggle_high_contrast))

    def _space_key_handler(self, event) -> None:
        if self._text_input_has_focus():
            return
        self._safe_invoke(self.unmute_all)
        return "break"

    def toggle_high_contrast(self) -> None:
        self.high_contrast_enabled = bool(self.high_contrast_var.get())
        self._apply_accessibility_mode()

    def toggle_large_text(self) -> None:
        enabled = bool(self.large_text_var.get())
        self.font_scale = 1.2 if enabled else 1.0
        self._apply_accessibility_mode()

    def increase_text_size(self) -> None:
        self.font_scale = clamp_scale(self.font_scale + 0.1)
        self.large_text_var.set(self.font_scale > 1.05)
        self._apply_accessibility_mode()

    def decrease_text_size(self) -> None:
        self.font_scale = clamp_scale(self.font_scale - 0.1)
        self.large_text_var.set(self.font_scale > 1.05)
        self._apply_accessibility_mode()

    def _apply_accessibility_mode(self) -> None:
        palette = contrast_palette(self.high_contrast_enabled)
        self._apply_widget_style(self.root, palette)
        for channel in self.mixer_channels.values():
            channel.apply_accessibility(self.font_scale, self.high_contrast_enabled)
        self._save_accessibility_preferences()

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
                if "fg_color" in widget.configure() and self.high_contrast_enabled:
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

    def _load_accessibility_preferences(self) -> None:
        prefs = self.preferences_service.load()
        self.font_scale = prefs.font_scale
        self.high_contrast_enabled = prefs.high_contrast_enabled
        self.auto_setup_enabled = prefs.auto_setup_enabled

    def _save_accessibility_preferences(self) -> None:
        self.preferences_service.save_ui(
            font_scale=self.font_scale,
            high_contrast_enabled=self.high_contrast_enabled,
            auto_setup_enabled=self.auto_setup_enabled,
        )

    def toggle_auto_setup(self) -> None:
        self.auto_setup_enabled = bool(self.auto_setup_var.get())
        self._save_accessibility_preferences()

    def toggle_auto_reconnect(self) -> None:
        self.auto_reconnect_enabled = bool(self.auto_reconnect_var.get())
        self.repository.set_setting("auto_reconnect_enabled", "1" if self.auto_reconnect_enabled else "0")

    def _save_window_geometry(self) -> None:
        try:
            geometry = self.root.winfo_geometry()
            if geometry and _is_valid_geometry(geometry) and not geometry.startswith("1x1"):
                self.preferences_service.save_window_geometry(geometry)
        except Exception as exc:
            LOGGER.warning("Failed to persist window geometry: %s", exc)

    def reset_window_geometry(self) -> None:
        self.root.geometry(self.preferences_service.reset_window_geometry())
        messagebox.showinfo("Window Reset", "Window size and position reset to default.", parent=self.root)

    def reset_all_ui_preferences(self) -> None:
        confirmed = messagebox.askokcancel(
            "Reset UI Preferences",
            "This will reset text size, contrast mode, startup toggles, and window position.\n\nContinue?",
            parent=self.root,
        )
        if not confirmed:
            return

        self.font_scale = 1.0
        self.high_contrast_enabled = False
        self.auto_setup_enabled = True
        self.auto_reconnect_enabled = True

        self.large_text_var.set(False)
        self.high_contrast_var.set(False)
        self.auto_setup_var.set(True)
        if hasattr(self, "auto_reconnect_var"):
            self.auto_reconnect_var.set(True)
        self.repository.set_setting("auto_reconnect_enabled", "1")

        self.root.geometry("1600x900")
        self._apply_accessibility_mode()
        self.preferences_service.reset_window_geometry()
        self.repository.set_setting("setup_completed", "0")

        messagebox.showinfo(
            "UI Preferences Reset",
            "UI preferences were reset.\n\nSetup wizard will show again on next launch.",
            parent=self.root,
        )

    def show_usage_metrics(self) -> None:
        show_usage_metrics_window(
            self.root,
            self._collect_usage_metrics(),
            on_export=self.export_diagnostics_snapshot,
            on_reset=self.reset_usage_metrics,
            refresh_metrics=self._collect_usage_metrics,
        )

    def _collect_usage_metrics(self) -> Dict[str, str]:
        return self.metrics_service.collect()

    def reset_usage_metrics(self) -> None:
        confirmed = messagebox.askokcancel(
            "Reset Usage Metrics",
            "This will clear all local metric counters. Continue?",
            parent=self.root,
        )
        if not confirmed:
            return
        self.metrics_service.reset_with_prefix("metric_")
        messagebox.showinfo("Metrics Reset", "Local usage metrics were reset.", parent=self.root)

    def set_cohort_name(self) -> None:
        cohort = simpledialog.askstring(
            "Validation Cohort",
            "Set creator cohort tag (visual_artists, writers, designers, mixed_discipline):",
            parent=self.root,
            initialvalue=self.cohort_name,
        )
        if not cohort:
            return
        normalized = cohort.strip().lower().replace(" ", "_")
        if not normalized:
            messagebox.showwarning("Invalid Cohort", "Cohort tag cannot be empty.", parent=self.root)
            return
        self.cohort_name = normalized
        self.repository.set_setting("cohort_name", self.cohort_name)
        self.metrics_service.increment(f"metric_cohort_tagged_{self.cohort_name}")
        messagebox.showinfo("Cohort Updated", f"Current validation cohort: {self.cohort_name}", parent=self.root)

    def record_session_complete(self) -> None:
        self.metrics_service.increment("metric_session_completed")
        self.metrics_service.increment(f"metric_mode_session_completed_{self.mode_key}")
        self.repository.append_cohort_event(
            self.cohort_name,
            "session_completed",
            {
                "mode_key": self.mode_key,
                "template_name": self.template_var.get().strip(),
                "review_state": self.review_state,
            },
        )
        messagebox.showinfo("Session Recorded", "Session completion was logged for cohort validation metrics.", parent=self.root)

    def export_diagnostics_snapshot(self) -> None:
        try:
            self._refresh_endpoint_state()
            out_path = self.metrics_service.export_snapshot(
                home_dir=Path.home(),
                jamulus_state=self.jamulus_state,
                webex_state=self.webex_state,
                latency_ms=self.network_latency_ms,
                server=f"{self.jamulus_server}:{self.jamulus_port}",
                webex_url=self.webex_url,
                audio_diagnostics=self.jamulus_controller.get_audio_diagnostics(),
            )
            messagebox.showinfo("Snapshot Exported", f"Diagnostics snapshot written to:\n{out_path}", parent=self.root)
        except Exception as exc:
            messagebox.showerror("Export Failed", f"Could not export diagnostics snapshot:\n{exc}", parent=self.root)

    def export_diagnostics_bundle(self) -> None:
        try:
            if not self._flush_live_room_state():
                self.metrics_service.increment("metric_diagnostics_bundle_failed")
                return
            self._refresh_endpoint_state()
            settings_snapshot = asdict(self._settings_for_checks())
            log_file = str(settings_snapshot.get("log_file", "") or "").strip()
            log_candidates: list[str] = []
            if log_file:
                log_candidates.append(log_file)
                for idx in range(1, 4):
                    log_candidates.append(f"{log_file}.{idx}")

            support_files = [
                settings_snapshot.get("config_file", ""),
                settings_snapshot.get("mix_file", ""),
                settings_snapshot.get("webex_config_file", ""),
            ]
            room_context = self.repository.get_room_context(self.room_key)
            support_snapshot = self.repository.export_support_snapshot(room_key=self.room_key)

            out_path = self.metrics_service.export_diagnostics_bundle(
                output_dir=Path.home(),
                jamulus_state=self.jamulus_state,
                webex_state=self.webex_state,
                latency_ms=self.network_latency_ms,
                server=f"{self.jamulus_server}:{self.jamulus_port}",
                webex_url=self.webex_url,
                audio_diagnostics=self.jamulus_controller.get_audio_diagnostics(),
                settings_payload=settings_snapshot,
                room_context=room_context,
                webex_last_error=self.webex_controller.last_error,
                jamulus_path=self.find_jamulus() or "",
                log_files=log_candidates,
                support_files=support_files,
                extra_json_files={"support_snapshot.json": support_snapshot},
            )
            self.metrics_service.increment("metric_diagnostics_bundle_exported")
            messagebox.showinfo("Bundle Exported", f"Diagnostics bundle written to:\n{out_path}", parent=self.root)
        except Exception as exc:
            self.metrics_service.increment("metric_diagnostics_bundle_failed")
            messagebox.showerror("Export Failed", f"Could not export diagnostics bundle:\n{exc}", parent=self.root)

    def export_session_brief(self) -> None:
        try:
            if not self._flush_live_room_state():
                self.metrics_service.increment("metric_session_brief_failed")
                return
            room_context = self.repository.get_room_context(self.room_key)
            artifacts = self.repository.list_session_artifacts(self.room_key)
            notes = self.repository.get_session_notes(self.room_key)
            participants = self.jamulus_controller.get_participants()
            mode_label = get_mode_by_key_or_default(self.mode_key).label
            out_path = self.metrics_service.export_session_brief(
                output_dir=Path.home(),
                room_context=room_context,
                artifacts=artifacts,
                notes=notes,
                participants=participants,
                mode_label=mode_label,
            )
            self.metrics_service.increment("metric_session_brief_exported")
            messagebox.showinfo("Session Brief Exported", f"Session brief written to:\n{out_path}", parent=self.root)
        except Exception as exc:
            self.metrics_service.increment("metric_session_brief_failed")
            messagebox.showerror("Export Failed", f"Could not export session brief:\n{exc}", parent=self.root)

    def _update_latency_widget(self) -> None:
        try:
            if not self.root.winfo_exists():
                return
            label, color = classify_latency_ms(self.network_latency_ms)
            self.latency_label.configure(text=label)
            if CTK_AVAILABLE:
                self.latency_label.configure(text_color=color)
            else:
                self.latency_label.configure(fg=color)
        except tk.TclError:
            pass

    def _measure_server_latency_async(self) -> None:
        if self._latency_probe_inflight:
            return
        self._refresh_endpoint_state()
        host = self.jamulus_server
        port = self.jamulus_port
        self._latency_probe_inflight = True

        def _probe() -> None:
            measured: float | None = None
            start = time.perf_counter()
            try:
                with socket.create_connection((host, port), timeout=0.45):
                    measured = max(0.0, (time.perf_counter() - start) * 1000.0)
            except (socket.error, OSError, TimeoutError):
                measured = None
            try:
                self.root.after(0, lambda: self._complete_latency_probe(measured))
            except (tk.TclError, RuntimeError):
                self._latency_probe_inflight = False
                self.network_latency_ms = measured

        threading.Thread(target=_probe, daemon=True).start()

    def _complete_latency_probe(self, measured: float | None) -> None:
        self._latency_probe_inflight = False
        self.network_latency_ms = measured
        try:
            if self.root.winfo_exists():
                self._update_latency_widget()
        except tk.TclError:
            return

    def _refresh_readiness(self) -> None:
        participants = self.jamulus_controller.get_participants()
        participant_count = len(participants)
        placeholder_count = sum(
            1 for participant in participants
            if str(getattr(participant, "name", "")).strip() == LOCAL_PARTICIPANT_NAME
        )
        readiness_text, readiness_color = readiness_state(
            participant_count,
            placeholder_count=placeholder_count,
        )
        mode_label = get_mode_by_key_or_default(self.mode_key).label

        if self.review_state in ("review", "final"):
            self.readiness_label.configure(
                text=f"Room: in {self.review_state} – use Session Canvas to continue or reset to draft"
            )
        else:
            self.readiness_label.configure(text=readiness_text)
        self.connection_summary.configure(text=f"{connection_summary(self.jamulus_state, self.webex_state)} | Mode: {mode_label}")
        self._set_status_banner(f"{mode_label}: {self.jamulus_state} | {self.webex_state}", color=readiness_color)

    def _poll_connection_health(self) -> None:
        if self._shutdown_requested_active():
            return
        try:
            if not self.root.winfo_exists():
                return
        except tk.TclError:
            return
        self._refresh_endpoint_state()
        if self.jamulus_process is not None:
            if self.jamulus_process.poll() is None and "Not launched" in self.jamulus_state:
                self.jamulus_state = "Running"
            elif self.jamulus_process.poll() is not None and self.jamulus_state in {"Connected", "Running"}:
                self.jamulus_state = "Not running"

        if self.webex_controller.is_connected and self.webex_state in {"Not opened", "Open failed"}:
            self.webex_state = "Opened in browser"

        self._attempt_auto_reconnects()
        self._measure_server_latency_async()
        self._refresh_readiness()
        try:
            self._poll_after_id = self.root.after(2500, self._poll_connection_health)
        except tk.TclError:
            self._poll_after_id = None

    def _show_actionable_error(
        self,
        title: str,
        what_failed: str,
        likely_cause: str,
        next_action: str,
        retry_callback=None,
    ) -> None:
        message = (
            f"What failed:\n{what_failed}\n\n"
            f"Likely cause:\n{likely_cause}\n\n"
            f"Next step:\n{next_action}\n\n"
            "Choose Yes to retry now.\n"
            "Choose No to open quick troubleshooting."
        )
        retry = messagebox.askyesno(title, message, parent=self.root)
        if retry and retry_callback:
            retry_callback()
            return
        self.show_help(topic="troubleshooting")

    def sign_in(self) -> bool:
        user = self.auth_controller.sign_in_interactive(parent=self.root)
        if user is None:
            return False
        self.current_user = user
        self._restore_signed_in_mix_default()
        return True

    def open_admin_panel(self):
        if self.current_user is None:
            signed_in = self.sign_in()
            if not signed_in:
                return
        if not self.auth_controller.authorize(
            self.current_user,
            "view_diagnostics",
            require_sign_in=True,
            parent=self.root,
        ):
            return
        AdminPanel(self.root, self.repository, self.current_user, self.policy).show()

    def show_audio_diagnostics(self):
        if not self.auth_controller.authorize(
            self.current_user,
            "view_diagnostics",
            require_sign_in=False,
            parent=self.root,
        ):
            return
        self.metrics_service.increment("metric_audio_diagnostics_opened")
        diag = self.jamulus_controller.get_audio_diagnostics()
        text = "\n".join(f"{k}: {v}" for k, v in diag.items())
        messagebox.showinfo("Audio Diagnostics", text, parent=self.root)

    def open_diagnostics_panel(self):
        self.metrics_service.increment("metric_diagnostics_panel_opened")
        self._refresh_endpoint_state()
        diag = self.jamulus_controller.get_audio_diagnostics()
        jamulus_path = self.find_jamulus() or "Not found"

        def _task() -> tuple[bool, str]:
            return SetupWizard.check_tcp_hint(self.jamulus_server, self.jamulus_port)

        def _show_panel(host_result: tuple[bool, str]) -> None:
            host_ok, host_detail = host_result
            show_diagnostics_panel(
                root=self.root,
                jamulus_path=jamulus_path,
                jamulus_server=self.jamulus_server,
                jamulus_port=str(self.jamulus_port),
                host_ok=host_ok,
                host_detail=host_detail,
                webex_url=self.webex_url,
                webex_last_error=self.webex_controller.last_error,
                audio_diagnostics=diag,
                on_run_setup=lambda: self.show_setup_wizard(mark_complete=False),
                on_open_help=lambda: self.show_help(topic="troubleshooting"),
                on_export_snapshot=self.export_diagnostics_snapshot,
                on_export_bundle=self.export_diagnostics_bundle,
                on_reset_metrics=self.reset_usage_metrics,
                bg_color=THEME.bg_secondary,
                fg_color=THEME.text_primary,
            )

        self._run_background_task(
            inflight_attr="_diagnostics_panel_inflight",
            banner_text="Running diagnostics...",
            task=_task,
            on_success=_show_panel,
            error_title="Diagnostics Failed",
            what_failed="Diagnostics panel could not finish the endpoint probe.",
            likely_cause="The network probe failed unexpectedly or the window was closing.",
            next_action="Retry diagnostics or run the setup wizard to verify the endpoint.",
            retry_callback=self.open_diagnostics_panel,
        )
    
    def on_participants_updated(self, participants):
        """Called from monitor thread -- schedule actual UI work on the main thread."""
        try:
            if self.root.winfo_exists():
                self.root.after(0, lambda p=list(participants): self._do_participants_update(p))
        except (tk.TclError, RuntimeError):
            pass

    def _do_participants_update(self, participants):
        """Main-thread handler for participant list changes."""
        self.participants_count.configure(text=f"Participants: {len(participants)}")

        current_ids = set(self.mixer_channels.keys())
        new_ids = set(p.channel_id for p in participants)

        for channel_id in current_ids - new_ids:
            if channel_id in self.mixer_channels:
                self.mixer_channels[channel_id].destroy()
                del self.mixer_channels[channel_id]
                if self.selected_channel_id == channel_id:
                    self.selected_channel_id = None

        for participant in participants:
            if participant.channel_id not in self.mixer_channels:
                channel = EnhancedMixerChannel(
                    self.channels_container,
                    participant,
                    self.jamulus_controller,
                    font_scale=self.font_scale,
                    high_contrast=self.high_contrast_enabled,
                    on_select=self._select_mixer_channel,
                )
                channel.pack(side=tk.LEFT, padx=8, pady=10, fill=tk.BOTH)
                self.mixer_channels[participant.channel_id] = channel
        if self.selected_channel_id is None and participants:
            self._select_mixer_channel(participants[0].channel_id)
        else:
            self._select_mixer_channel(self.selected_channel_id)
        self._attempt_pending_mix_restore()

        self._refresh_readiness()
    
    def update_vu_meters(self):
        """Update VU meters periodically"""
        for channel_id, channel in self.mixer_channels.items():
            level = self.audio_monitor.get_level(channel_id)
            channel.update_vu_meter(level)
        
        # Lower idle refresh rate to reduce background CPU.
        interval_ms = 50 if self.mixer_channels else 220
        self._vu_after_id = self.root.after(interval_ms, self.update_vu_meters)
    
    def add_test_participants(self):
        """Add demo participants to preview mixer controls"""
        test_names = [LOCAL_PARTICIPANT_NAME, "Guitarist", "Bassist", "Drummer", "Vocalist", "Keys"]
        existing_ids = {p.channel_id for p in self.jamulus_controller.get_participants()}
        for i, name in enumerate(test_names):
            if i not in existing_ids:
                self.jamulus_controller.add_participant(name, i)
        self.jamulus_state = "Demo mode"
        self._refresh_readiness()
    
    def launch_jamulus(self):
        """Launch Jamulus client"""
        if self._shutdown_requested_active():
            return
        self._jamulus_launch_intended = True
        self._jamulus_reconnect_attempts = 0
        self._jamulus_next_reconnect_at = 0.0
        self._launch_jamulus(manual=True, reconnect=False)

    def _launch_jamulus(self, manual: bool, reconnect: bool) -> None:
        if self._shutdown_requested_active():
            self._jamulus_reconnect_inflight = False
            return
        if manual:
            self.metrics_service.increment("metric_jamulus_launch_attempt")
        self._refresh_endpoint_state()
        jamulus_path = self.find_jamulus()
        if not jamulus_path:
            if reconnect:
                self._jamulus_reconnect_inflight = False
                self.metrics_service.increment("metric_jamulus_reconnect_failed")
                self.jamulus_state = "Not running"
                self._schedule_ui_callback(self._refresh_readiness)
                LOGGER.warning("Jamulus reconnect skipped: executable not found.")
                return
            self.metrics_service.increment("metric_jamulus_launch_failed")
            self._show_actionable_error(
                "Jamulus Not Found",
                what_failed="WebJam could not locate Jamulus.exe.",
                likely_cause="Jamulus is not installed in a default location.",
                next_action="Run setup wizard and install Jamulus, then retry launch.",
                retry_callback=lambda: self.show_setup_wizard(mark_complete=False),
            )
            return

        if self.jamulus_process and self.jamulus_process.poll() is None:
            self.jamulus_state = "Already running"
            self._jamulus_reconnect_attempts = 0
            self._jamulus_next_reconnect_at = 0.0
            self._jamulus_reconnect_inflight = False
            self._refresh_readiness()
            if manual:
                messagebox.showinfo("Jamulus", "Jamulus is already running.", parent=self.root)
            return

        banner_text = "Launching Jamulus..." if manual else "Auto-reconnecting Jamulus..."
        self._set_status_banner(banner_text, color="#ffcc00")
        server = f"{self.jamulus_server}:{self.jamulus_port}"

        def _do_launch() -> None:
            try:
                if self._shutdown_requested_active():
                    self._jamulus_reconnect_inflight = False
                    return
                proc = self._retry_action(
                    lambda: subprocess.Popen([jamulus_path, "--connect", server]),
                    attempts=3,
                    base_delay=0.5,
                )
                if self._shutdown_requested_active():
                    self._terminate_process(proc)
                    self._jamulus_reconnect_inflight = False
                    return
                self.jamulus_process = proc
                self.jamulus_state = f"Connecting ({server})"
                self._schedule_ui_callback(self._refresh_readiness)
                if manual:
                    self._schedule_ui_callback(
                        lambda: messagebox.showinfo(
                            "Success",
                            f"Jamulus launched!\n\nConnecting to: {server}\n\nWait a moment for participants to appear in the mixer.",
                            parent=self.root,
                        )
                    )
                self.jamulus_controller.add_participant(LOCAL_PARTICIPANT_NAME, 0)
                # A started process is not the same as a confirmed server join.
                self.jamulus_state = "Running"
                self._jamulus_reconnect_attempts = 0
                self._jamulus_next_reconnect_at = 0.0
                self._jamulus_reconnect_inflight = False
                if reconnect:
                    self.metrics_service.increment("metric_jamulus_reconnect_success")
                else:
                    self.metrics_service.increment("metric_jamulus_launch_success")
                self._schedule_ui_callback(self._refresh_readiness)
            except Exception as exc:
                LOGGER.exception("Failed to launch Jamulus: %s", exc)
                self.jamulus_state = "Launch failed" if manual else "Not running"
                self._jamulus_reconnect_inflight = False
                if reconnect:
                    self.metrics_service.increment("metric_jamulus_reconnect_failed")
                    self._schedule_ui_callback(self._refresh_readiness)
                    return
                self.metrics_service.increment("metric_jamulus_launch_failed")
                self._schedule_ui_callback(self._refresh_readiness)
                self._schedule_ui_callback(
                    lambda launch_exc=exc: self._show_actionable_error(
                        "Jamulus Launch Failed",
                        what_failed=f"Jamulus could not start ({launch_exc}).",
                        likely_cause="Invalid path, blocked launch, missing dependency, or transient process startup failure.",
                        next_action="Open diagnostics, verify path/server, then retry (launch uses automatic backoff retries).",
                        retry_callback=self.launch_jamulus,
                    )
                )

        threading.Thread(target=_do_launch, daemon=True).start()
    
    def launch_webex(self):
        """Launch Webex meeting"""
        if self._shutdown_requested_active():
            return
        self._webex_launch_intended = True
        self._webex_reconnect_attempts = 0
        self._webex_next_reconnect_at = 0.0
        self._launch_webex(manual=True, reconnect=False)

    def _launch_webex(self, manual: bool, reconnect: bool) -> None:
        if self._shutdown_requested_active():
            self._webex_reconnect_inflight = False
            return
        if manual:
            self.metrics_service.increment("metric_webex_open_attempt")
        banner_text = "Opening Webex..." if manual else "Auto-reconnecting Webex..."
        self._set_status_banner(banner_text, color="#ffcc00")

        def _do_open() -> None:
            try:
                if self._shutdown_requested_active():
                    self._webex_reconnect_inflight = False
                    return
                def _open_once() -> bool:
                    opened_once = self.webex_controller.join_meeting()
                    if not opened_once:
                        raise RuntimeError(getattr(self.webex_controller, "last_error", "Unknown browser launch failure"))
                    return True

                self._retry_action(_open_once, attempts=3, base_delay=0.4)
                if self._shutdown_requested_active():
                    self._webex_reconnect_inflight = False
                    return
                self.webex_state = "Opened in browser"
                self._webex_reconnect_attempts = 0
                self._webex_next_reconnect_at = 0.0
                self._webex_reconnect_inflight = False
                if reconnect:
                    self.metrics_service.increment("metric_webex_reconnect_success")
                else:
                    self.metrics_service.increment("metric_webex_open_success")
                self._schedule_ui_callback(self._refresh_readiness)
                if manual:
                    self._schedule_ui_callback(
                        lambda: messagebox.showinfo(
                            "Webex Opened",
                            f"Webex meeting opened in your browser:\n\n{self.webex_url}\n\nJoin the meeting to see and hear other participants.",
                            parent=self.root,
                        )
                    )
            except Exception as exc:
                LOGGER.exception("Failed to open Webex: %s", exc)
                self.webex_state = "Open failed"
                self._webex_reconnect_inflight = False
                if reconnect:
                    self.metrics_service.increment("metric_webex_reconnect_failed")
                    self._schedule_ui_callback(self._refresh_readiness)
                    return
                self.metrics_service.increment("metric_webex_open_failed")
                self._schedule_ui_callback(self._refresh_readiness)
                self._schedule_ui_callback(
                    lambda open_exc=exc: self._show_actionable_error(
                        "Webex Open Failed",
                        what_failed=f"Webex URL could not be opened ({open_exc}).",
                        likely_cause="Default browser issue, network filtering, invalid meeting URL, or transient launch issue.",
                        next_action="Verify URL in diagnostics/setup wizard and retry (open uses automatic retries).",
                        retry_callback=self.launch_webex,
                    )
                )

        threading.Thread(target=_do_open, daemon=True).start()
    
    def find_jamulus(self):
        """Find Jamulus installation"""
        for path in JAMULUS_CANDIDATES:
            if Path(path).exists():
                return path
        return None

    def _saved_mix_payload_for_load(self) -> tuple[dict[str, Any], str] | None:
        username = self._default_mix_user()
        if username:
            saved_mix = self.repository.get_user_mix_default(username)
            if saved_mix is not None:
                payload = saved_mix.get("payload")
                if isinstance(payload, dict):
                    return payload, f"{username} profile"
        if MIX_FILE.exists():
            payload = self._load_mix_payload_from_file()
            if isinstance(payload, dict):
                return payload, str(MIX_FILE)
        return None
    
    def save_mix(self):
        """Save current mix settings"""
        self.metrics_service.increment("metric_save_mix_attempt")
        if not self.auth_controller.authorize(
            self.current_user,
            "save_mix",
            require_sign_in=False,
            allow_anonymous=True,
            parent=self.root,
        ):
            return
        participants = self.jamulus_controller.get_participants()
        if not participants:
            messagebox.showwarning(
                "No Participants",
                "Connect or add participants before saving mix settings.",
                parent=self.root,
            )
            return
        try:
            username = self._default_mix_user()
            audit_actor = self.current_user.username if self.current_user else "anonymous"
            if username:
                mix_payload = self.jamulus_controller.serialize_mix()
                self.repository.save_user_mix_default(username, mix_payload)
                audit_detail = f"user:{username}"
                success_message = (
                    f"Mix settings saved to your WebJam profile for {username}.\n\n"
                    "This default mix restores automatically when you sign in again."
                )
            else:
                self.jamulus_controller.save_mix(str(MIX_FILE))
                audit_detail = str(MIX_FILE)
                success_message = (
                    f"Mix settings saved locally to:\n{MIX_FILE}\n\n"
                    "This default mix restores automatically the next time WebJam starts."
                )
            self._pending_mix_restore_payload = None
            self._pending_mix_restore_source = None
            self.metrics_service.increment("metric_save_mix_success")
            self.repository.add_audit("save_mix", audit_actor, audit_detail)
            messagebox.showinfo("Saved", success_message, parent=self.root)
        except Exception as e:
            LOGGER.exception("save_mix failed: %s", e)
            self.metrics_service.increment("metric_save_mix_failed")
            self._show_actionable_error(
                "Save Mix Failed",
                what_failed=f"Could not save the default mix ({e}).",
                likely_cause="Repository write failed, the mix payload was invalid, or the local mix file path is unavailable.",
                next_action="Verify the current profile/path in diagnostics, then retry save.",
                retry_callback=self.save_mix,
            )

    def save_listening_profile(self) -> None:
        if not self.auth_controller.authorize(
            self.current_user,
            "save_mix",
            require_sign_in=False,
            allow_anonymous=True,
            parent=self.root,
        ):
            return
        participants = self.jamulus_controller.get_participants()
        if not participants:
            messagebox.showwarning("No Participants", "Connect or add participants before saving a listening profile.", parent=self.root)
            return

        suggested_name = f"{get_mode_by_key_or_default(self.mode_key).label} Profile"
        profile_name = simpledialog.askstring(
            "Save Listening Profile",
            "Enter a name for this listening profile:",
            parent=self.root,
            initialvalue=suggested_name,
        )
        if profile_name is None:
            return
        normalized_name = profile_name.strip()
        if not normalized_name:
            messagebox.showwarning("Invalid Name", "Profile name cannot be empty.", parent=self.root)
            return

        existing = self.repository.get_mix_profile(normalized_name)
        if existing is not None:
            overwrite = messagebox.askokcancel(
                "Overwrite Listening Profile",
                f"A listening profile named '{existing['profile_name']}' already exists.\n\nOverwrite it?",
                parent=self.root,
            )
            if not overwrite:
                return

        try:
            self.repository.save_mix_profile(
                normalized_name,
                self.mode_key,
                self.jamulus_controller.serialize_mix(),
            )
            self.metrics_service.increment("metric_listening_profile_save_success")
            self.repository.add_audit(
                "save_mix_profile",
                self.current_user.username if self.current_user else "anonymous",
                normalized_name,
            )
            messagebox.showinfo(
                "Listening Profile Saved",
                f"Saved listening profile:\n{normalized_name}",
                parent=self.root,
            )
        except Exception as exc:
            LOGGER.exception("save_listening_profile failed: %s", exc)
            self.metrics_service.increment("metric_listening_profile_save_failed")
            messagebox.showerror(
                "Save Failed",
                f"Could not save listening profile:\n{exc}",
                parent=self.root,
            )

    def load_mix(self):
        """Load saved mix settings"""
        self.metrics_service.increment("metric_load_mix_attempt")
        if not self.auth_controller.authorize(
            self.current_user,
            "load_mix",
            require_sign_in=False,
            allow_anonymous=True,
            parent=self.root,
        ):
            return
        try:
            saved_mix = self._saved_mix_payload_for_load()
            if saved_mix is None:
                if MIX_FILE.exists() and self._load_mix_payload_from_file() is None:
                    self.metrics_service.increment("metric_load_mix_failed")
                    self._show_actionable_error(
                        "Load Mix Failed",
                        what_failed="The saved mix file could not be parsed or was missing the expected participant payload.",
                        likely_cause="The file is corrupted, incomplete, or from an unsupported format.",
                        next_action="Save a fresh mix preset after confirming participants are connected.",
                        retry_callback=self.load_mix,
                    )
                    return
                messagebox.showwarning("No Settings", "No saved mix settings found.", parent=self.root)
                return

            mix_payload, mix_source = saved_mix
            applied_channels = self.jamulus_controller.apply_mix_data(mix_payload)
            if applied_channels is None:
                self.metrics_service.increment("metric_load_mix_failed")
                self._show_actionable_error(
                    "Load Mix Failed",
                    what_failed="The saved mix payload could not be parsed or was missing the expected participant data.",
                    likely_cause="The saved mix is corrupted, incomplete, or from an unsupported format.",
                    next_action="Save a fresh mix preset after confirming participants are connected.",
                    retry_callback=self.load_mix,
                )
                return
            if applied_channels == 0:
                self.metrics_service.increment("metric_load_mix_failed")
                messagebox.showwarning(
                    "No Matching Participants",
                    "Saved mix was read, but none of the current participants matched the saved channels.\n\n"
                    "Reconnect the expected participants and try again.",
                    parent=self.root,
                )
                return
            self._pending_mix_restore_payload = None
            self._pending_mix_restore_source = None
            self._refresh_mixer_controls_from_participants()
            self._refresh_readiness()
            messagebox.showinfo("Loaded", f"Mix settings loaded from:\n{mix_source}", parent=self.root)
            self.metrics_service.increment("metric_load_mix_success")
        except Exception as e:
            LOGGER.exception("load_mix failed: %s", e)
            self.metrics_service.increment("metric_load_mix_failed")
            self._show_actionable_error(
                "Load Mix Failed",
                what_failed=f"Could not load the saved mix ({e}).",
                likely_cause="Corrupted or incompatible saved mix data.",
                next_action="Save a fresh mix preset after confirming participants are connected.",
                retry_callback=self.load_mix,
            )

    def load_listening_profile(self) -> None:
        if not self.auth_controller.authorize(
            self.current_user,
            "load_mix",
            require_sign_in=False,
            allow_anonymous=True,
            parent=self.root,
        ):
            return
        profiles = self._available_profile_entries()
        if not profiles:
            messagebox.showinfo("No Profiles", "No listening profiles are saved yet.", parent=self.root)
            return

        profile_name = simpledialog.askstring(
            "Load Listening Profile",
            self._profile_prompt_text("Load", profiles),
            parent=self.root,
        )
        if profile_name is None:
            return
        resolved_name = self._resolve_profile_name(profile_name, profiles)
        if resolved_name is None:
            messagebox.showwarning("Profile Not Found", "Enter one of the saved listening profile names.", parent=self.root)
            return

        profile = self.repository.get_mix_profile(resolved_name)
        if profile is None:
            self.metrics_service.increment("metric_listening_profile_load_failed")
            messagebox.showerror("Load Failed", f"Listening profile '{resolved_name}' could not be read.", parent=self.root)
            return

        try:
            applied_channels = self.jamulus_controller.apply_mix_data(profile["payload"])
            if applied_channels is None:
                self.metrics_service.increment("metric_listening_profile_load_failed")
                messagebox.showerror(
                    "Load Failed",
                    f"Listening profile '{resolved_name}' is invalid or corrupted.",
                    parent=self.root,
                )
                return
            if applied_channels == 0:
                self.metrics_service.increment("metric_listening_profile_load_failed")
                messagebox.showwarning(
                    "No Matching Participants",
                    f"Listening profile '{resolved_name}' did not match any current participants.",
                    parent=self.root,
                )
                return
            self._refresh_mixer_controls_from_participants()
            self._refresh_readiness()
            self.metrics_service.increment("metric_listening_profile_load_success")
            self.repository.add_audit(
                "load_mix_profile",
                self.current_user.username if self.current_user else "anonymous",
                resolved_name,
            )
            messagebox.showinfo(
                "Listening Profile Loaded",
                f"Loaded listening profile:\n{resolved_name}",
                parent=self.root,
            )
        except Exception as exc:
            LOGGER.exception("load_listening_profile failed: %s", exc)
            self.metrics_service.increment("metric_listening_profile_load_failed")
            messagebox.showerror("Load Failed", f"Could not load listening profile:\n{exc}", parent=self.root)

    def delete_listening_profile(self) -> None:
        if not self.auth_controller.authorize(
            self.current_user,
            "save_mix",
            require_sign_in=False,
            allow_anonymous=True,
            parent=self.root,
        ):
            return
        profiles = self._available_profile_entries()
        if not profiles:
            messagebox.showinfo("No Profiles", "No listening profiles are saved yet.", parent=self.root)
            return

        profile_name = simpledialog.askstring(
            "Delete Listening Profile",
            self._profile_prompt_text("Delete", profiles),
            parent=self.root,
        )
        if profile_name is None:
            return
        resolved_name = self._resolve_profile_name(profile_name, profiles)
        if resolved_name is None:
            messagebox.showwarning("Profile Not Found", "Enter one of the saved listening profile names.", parent=self.root)
            return

        confirmed = messagebox.askokcancel(
            "Delete Listening Profile",
            f"Delete listening profile '{resolved_name}'?",
            parent=self.root,
        )
        if not confirmed:
            return

        deleted = self.repository.delete_mix_profile(resolved_name)
        if not deleted:
            self.metrics_service.increment("metric_listening_profile_delete_failed")
            messagebox.showerror("Delete Failed", f"Listening profile '{resolved_name}' was not found.", parent=self.root)
            return

        self.metrics_service.increment("metric_listening_profile_delete_success")
        self.repository.add_audit(
            "delete_mix_profile",
            self.current_user.username if self.current_user else "anonymous",
            resolved_name,
        )
        messagebox.showinfo("Listening Profile Deleted", f"Deleted listening profile:\n{resolved_name}", parent=self.root)

    def reset_all_faders(self):
        """Reset all faders to unity (0dB)"""
        if not self.auth_controller.authorize(
            self.current_user,
            "bulk_reset",
            require_sign_in=False,
            parent=self.root,
        ):
            return
        if not messagebox.askokcancel("Confirm", "Reset all faders to default?", parent=self.root):
            return
        for participant in self.jamulus_controller.get_participants():
            self.jamulus_controller.set_fader_level(participant.channel_id, 100)
            if participant.channel_id in self.mixer_channels:
                self.mixer_channels[participant.channel_id].fader.set(100)
        self.repository.add_audit("bulk_reset", self.current_user.username if self.current_user else "anonymous", "reset_all_faders")
    
    def unmute_all(self):
        """Unmute all channels"""
        if not self.auth_controller.authorize(
            self.current_user,
            "bulk_mute",
            require_sign_in=False,
            parent=self.root,
        ):
            return
        if not messagebox.askokcancel("Confirm", "Unmute all channels?", parent=self.root):
            return
        for participant in self.jamulus_controller.get_participants():
            self.jamulus_controller.set_mute(participant.channel_id, False)
            if participant.channel_id in self.mixer_channels:
                self.mixer_channels[participant.channel_id].update_button_states()
        self.repository.add_audit("bulk_mute", self.current_user.username if self.current_user else "anonymous", "unmute_all")
    
    def center_all_pans(self):
        """Center all pan controls"""
        if not self.auth_controller.authorize(
            self.current_user,
            "bulk_reset",
            require_sign_in=False,
            parent=self.root,
        ):
            return
        if not messagebox.askokcancel("Confirm", "Center all pan controls?", parent=self.root):
            return
        for participant in self.jamulus_controller.get_participants():
            self.jamulus_controller.set_pan(participant.channel_id, 50)
            if participant.channel_id in self.mixer_channels:
                self.mixer_channels[participant.channel_id].pan_slider.set(50)
        self.repository.add_audit("bulk_reset", self.current_user.username if self.current_user else "anonymous", "center_all_pans")
    
    def show_about(self):
        """Show about dialog"""
        about_text = """WebJam Enhanced
Creative Collaboration Platform

The app that knows we're making something together.

Version 2.0

Integrates Jamulus low-latency audio
with Webex plus shared session canvas.

Features:
• Mode-based creative rooms
• Shared session canvas for artifacts and notes
• Virtual mixing console
• Real-time audio level monitoring
• Individual channel control
• Save/load mix settings

© 2024 WebJam"""
        
        messagebox.showinfo("About WebJam", about_text, parent=self.root)
    
    def show_help(self, topic: str = "quickstart"):
        """Show quick start guide or troubleshooting guidance."""
        if topic == "troubleshooting":
            help_text = """Troubleshooting Quick Actions

1. Open Session -> Open Diagnostics Panel.
2. Verify Jamulus path and endpoint check.
3. Run Help -> Run Setup Wizard and re-run checks.
4. Launch Jamulus, then Launch Webex.

Common fixes:
- No participants: confirm Jamulus connected and wait up to 30 seconds.
- Audio issues: open Audio Diagnostics and verify backend/active status.
- Launch failures: retry after verifying install paths and network.
"""
            messagebox.showinfo("Troubleshooting", help_text, parent=self.root)
            return

        bootstrap_note = ""
        try:
            bootstrap_path = self.repository.get_bootstrap_admin_credentials_path()
        except Exception:
            bootstrap_path = None
        if isinstance(bootstrap_path, str) and bootstrap_path.strip():
            bootstrap_note = (
                "\nAdmin first sign-in:\n"
                "   Use Admin -> Sign In.\n"
                f"   Bootstrap credentials are stored at:\n   {bootstrap_path}\n"
                "   WebJam requires an immediate password change and then removes that file.\n"
            )

        help_text = """Quick Start Guide

Choose your mode in the top bar (Music Jam, Visual Studio, Writer's Room, Design Critique, Storyboard/Film Room).
The workspace layout adjusts by mode so music gets more mixer space and critique/writing gets more canvas space.
Set a template and session goal before launch.""" + bootstrap_note + """

1. Launch Jamulus
   Click 'Launch Jamulus' to connect to the audio server.
   Wait for other participants to connect.

2. Launch Webex
   Click 'Launch Webex' to join the video meeting.

3. Collaborate in Session Canvas
   • Add links/artifacts for references
   • Capture live notes and critique prompts
   • Use Insert Timestamp for time-linked notes
   • Track review state (draft/review/final)

4. Mix Your Session
   • Use vertical faders to adjust volume
   • Use pan controls for stereo positioning
   • Click MUTE to silence a channel
   • Click SOLO to hear only that channel

5. Save Your Mix
   Click 'Save Mix' to save your default mix.
   Signed-in users save to their WebJam profile.
   Anonymous use saves a local default mix on this computer.
   Saved defaults restore automatically on next sign-in or launch.

6. Save a Listening Profile
   Use File -> Save Listening Profile for named local presets.

7. Export a Session Brief
   Use Session -> Export Session Brief for handoff notes.

Tips:
• Keep all faders near 0dB for best quality
• Use headphones to prevent feedback
• Pan instruments left/right for clarity
• Watch the VU meters to avoid clipping

For troubleshooting, use Help -> Run Setup Wizard or Session -> Open Diagnostics Panel."""
        
        messagebox.showinfo("Quick Start Guide", help_text, parent=self.root)
    
    def quit_app(self):
        """Cleanup and quit"""
        if messagebox.askokcancel("Quit", "Are you sure you want to quit WebJam?", parent=self.root):
            if not self._flush_live_room_state():
                return
            self._request_shutdown()
            self._save_window_geometry()
            self.cleanup()
            self.root.quit()

    def cleanup(self):
        """Cleanup on exit"""
        self._request_shutdown()
        if self._poll_after_id is not None:
            try:
                self.root.after_cancel(self._poll_after_id)
            except (tk.TclError, ValueError):
                pass
            self._poll_after_id = None
        if self._vu_after_id is not None:
            try:
                self.root.after_cancel(self._vu_after_id)
            except (tk.TclError, ValueError):
                pass
            self._vu_after_id = None
        if getattr(self, "_service_start_after_id", None) is not None:
            try:
                self.root.after_cancel(self._service_start_after_id)
            except (tk.TclError, ValueError):
                pass
            self._service_start_after_id = None
        self.audio_monitor.stop()
        self.jamulus_controller.stop()
        self.webex_controller.stop()
        self.api_bridge.stop()
        process = self.jamulus_process
        self.jamulus_process = None
        self._terminate_process(process)
    
    def run(self):
        """Run the application"""
        self.root.protocol("WM_DELETE_WINDOW", self.quit_app)
        self.root.mainloop()


def main():
    """Main entry point"""
    if not CTK_AVAILABLE:
        print("\n" + "="*60)
        print("WebJam Enhanced - Creative Collaboration Platform")
        print("="*60)
        print("\nNote: For an enhanced UI experience, install customtkinter:")
        print("  pip install customtkinter")
        print("\nStarting with standard tkinter...\n")
    
    app = WebJamEnhancedApp()
    app.run()


if __name__ == "__main__":
    main()

