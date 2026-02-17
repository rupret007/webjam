"""
WebJam Enhanced - Creative Collaboration Platform with Jamulus Integration
Integrates Jamulus low-latency audio with Webex video conferencing
Features a mode-based room flow and shared session canvas
"""

import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk, messagebox, simpledialog
import subprocess
import webbrowser
import logging
import time
import threading
import json
from pathlib import Path
from typing import Optional, Dict
import sys
import socket

# Import Jamulus controller
from jamulus_controller import JamulusController, JamulusAudioMonitor, JamulusParticipant
from webex_integration import WebexController
from admin.admin_panel import AdminPanel
from admin.policy import PolicyEngine, UserContext
from api.local_bridge import LocalApiBridge
from core.logging_config import configure_logging, configure_sentry
from core.settings import load_settings
from storage.repository import WebJamRepository
from ui.theme import DEFAULT_THEME
from ui.accessibility import clamp_scale, scaled_font_size, contrast_palette
from ui.auth_controller import AuthController
from ui.ux_status import classify_latency_ms, readiness_state, connection_summary
from ui.dialogs import show_bootstrap_admin_notice, show_usage_metrics_window
from ui.preferences import UiPreferencesService
from ui.services import MetricsService, RetryService
from ui.views.diagnostics_panel import show_diagnostics_panel
from ui.views.session_canvas import SessionCanvasPanel
from ui.views.setup_wizard import SetupWizard
from ui.views.tooltip import Tooltip
from core.creative_modes import CREATIVE_MODES, get_mode_by_label, get_mode_by_key

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
SETTINGS = load_settings()
LOGGER = configure_logging(SETTINGS).getChild("app")
configure_sentry(SETTINGS)
THEME = DEFAULT_THEME
JAMULUS_SERVER = SETTINGS.jamulus_server
JAMULUS_PORT = str(SETTINGS.jamulus_port)
WEBEX_URL = SETTINGS.webex_url
CONFIG_FILE = Path(SETTINGS.config_file)
JAMULUS_CANDIDATES = SETTINGS.jamulus_candidates


class EnhancedMixerChannel(ctk.CTkFrame if CTK_AVAILABLE else tk.Frame):
    """Enhanced mixer channel with real Jamulus integration"""
    
    def __init__(
        self,
        parent,
        participant: JamulusParticipant,
        controller: JamulusController,
        font_scale: float = 1.0,
        high_contrast: bool = False,
    ):
        super().__init__(parent)
        self.participant = participant
        self.controller = controller
        self.font_scale = font_scale
        self.high_contrast = high_contrast
        self._tooltips = []
        
        if CTK_AVAILABLE:
            self.configure(fg_color=THEME.bg_secondary, corner_radius=10)
        else:
            self.configure(bg=THEME.bg_secondary, relief=tk.RAISED, borderwidth=2)
        
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
        new_state = not self.participant.muted
        self.controller.set_mute(self.participant.channel_id, new_state)
        self.update_button_states()
    
    def toggle_solo(self):
        """Toggle solo state"""
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

    def apply_accessibility(self, font_scale: float, high_contrast: bool) -> None:
        self.font_scale = clamp_scale(font_scale)
        self.high_contrast = high_contrast
        palette = contrast_palette(high_contrast)
        self._apply_widget_style(self, palette)

    def _apply_widget_style(self, widget: tk.Misc, palette: dict[str, str]) -> None:
        try:
            current_font = widget.cget("font")
            if current_font:
                f = tkfont.Font(font=current_font)
                family = f.actual("family")
                size = scaled_font_size(abs(int(f.actual("size") or 10)), self.font_scale)
                weight = f.actual("weight")
                widget.configure(font=(family, size, weight))
        except Exception:
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
        except Exception:
            pass

        for child in widget.winfo_children():
            self._apply_widget_style(child, palette)


class WebJamEnhancedApp:
    """Enhanced WebJam application with full Jamulus integration"""
    
    def __init__(self):
        # #region agent log
        self._debug_run_id = f"run-{int(time.time() * 1000)}"
        # #endregion
        # Setup main window
        if CTK_AVAILABLE:
            self.root = ctk.CTk()
        else:
            self.root = tk.Tk()
            self.root.configure(bg=THEME.bg_primary)
        
        self.root.title("WebJam - Creative Collaboration Platform")
        self.root.geometry("1600x900")
        self.root.minsize(1280, 760)
        
        # Initialize Jamulus controller
        self.jamulus_controller = JamulusController(JAMULUS_SERVER, int(JAMULUS_PORT))
        self.audio_monitor = JamulusAudioMonitor(self.jamulus_controller)
        self.webex_controller = WebexController(WEBEX_URL)
        
        self.jamulus_process: Optional[subprocess.Popen] = None
        self.mixer_channels: Dict[int, EnhancedMixerChannel] = {}
        self.jamulus_state = "Not launched"
        self.webex_state = "Not opened"
        self.network_latency_ms: float | None = None
        self._latency_probe_inflight = False
        self._service_bootstrapped = False
        self._startup_started_at = time.perf_counter()
        self._tooltips = []
        self.font_scale = 1.0
        self.high_contrast_enabled = False
        self.auto_setup_enabled = True
        self.repository = WebJamRepository()
        self.repository.ensure_default_admin()
        self.room_key = "default_room"
        saved_room = self.repository.get_room_context(self.room_key)
        self.mode_key = saved_room.get("mode_key", "music_jam")
        active_mode = get_mode_by_key(self.mode_key)
        self.template_name = saved_room.get("template_name", active_mode.default_template)
        self.session_goal_text = saved_room.get("session_goal", active_mode.default_goal)
        self.review_state = saved_room.get("review_state", "draft")
        self.cohort_name = self.repository.get_setting("cohort_name", "mixed_discipline") or "mixed_discipline"
        self.preferences_service = UiPreferencesService(self.repository)
        self.metrics_service = MetricsService(self.repository)
        self._load_accessibility_preferences()
        self.root.geometry(self.preferences_service.get_window_geometry())
        self.policy = PolicyEngine()
        self.auth_controller = AuthController(self.repository, self.policy)
        self.current_user: Optional[UserContext] = None
        self.api_bridge = LocalApiBridge(
            get_participants=self._bridge_participants,
            get_diagnostics=self.jamulus_controller.get_audio_diagnostics,
        )
        # #region agent log
        self._debug_log(
            hypothesis_id="H3",
            location="webjam_app_enhanced.py:__init__",
            message="init_pre_ui",
            data={"ctk_available": CTK_AVAILABLE},
        )
        # #endregion
        
        self.setup_ui()
        # #region agent log
        self._debug_log(
            hypothesis_id="H3",
            location="webjam_app_enhanced.py:__init__",
            message="ui_setup_complete",
            data={"elapsed_ms": int((time.perf_counter() - self._startup_started_at) * 1000)},
        )
        # #endregion
        self.save_room_context()
        self._bind_shortcuts()
        self._apply_accessibility_mode()
        self._show_bootstrap_admin_notice()
        
        # Register callback for participant updates
        self.jamulus_controller.register_callback(self.on_participants_updated)
        
        # Start UI update loop
        self.update_vu_meters()
        self._refresh_readiness()
        self._poll_connection_health()
        self._defer_service_start()
        self._show_setup_once()

    def _debug_log(self, hypothesis_id: str, location: str, message: str, data: dict) -> None:
        try:
            payload = {
                "sessionId": "65adf8",
                "runId": self._debug_run_id,
                "hypothesisId": hypothesis_id,
                "location": location,
                "message": message,
                "data": data,
                "timestamp": int(time.time() * 1000),
            }
            with open(r"c:\Users\rupret\Desktop\WebJam\debug-65adf8.log", "a", encoding="utf-8") as f:
                f.write(json.dumps(payload, separators=(",", ":")) + "\n")
        except Exception:
            pass
    
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
        file_menu.add_command(label="Exit", command=self.quit_app)
        
        # Session menu
        session_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Session", menu=session_menu)
        session_menu.add_command(label="Launch Jamulus", command=self.launch_jamulus)
        session_menu.add_command(label="Launch Webex", command=self.launch_webex)
        session_menu.add_separator()
        session_menu.add_command(label="Audio Diagnostics", command=self.show_audio_diagnostics)
        session_menu.add_command(label="Open Diagnostics Panel", command=self.open_diagnostics_panel)
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
        startup_menu.add_checkbutton(
            label="Run Setup Wizard on startup",
            variable=self.auto_setup_var,
            command=self.toggle_auto_setup,
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
        self.mode_var = tk.StringVar(value=get_mode_by_key(self.mode_key).label)
        mode_menu = tk.OptionMenu(mode_frame, self.mode_var, *[m.label for m in CREATIVE_MODES], command=self.on_mode_selected)
        mode_menu.configure(width=18)
        mode_menu.pack(anchor="w")

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

        hint_text = "Tip: pick mode + goal first. Launch is now deferred for faster startup on lower-end PCs."
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
            get_mode=lambda: get_mode_by_key(self.mode_key),
            get_room_context=lambda: self.repository.get_room_context(self.room_key),
            on_review_state_change=self.on_review_state_change,
            list_artifacts=lambda: self.repository.list_session_artifacts(self.room_key),
            add_artifact=lambda title, artifact_type, reference: self.repository.add_session_artifact(self.room_key, title, artifact_type, reference),
            remove_artifact=self.repository.remove_session_artifact,
            load_notes=lambda: self.repository.get_session_notes(self.room_key),
            save_notes=lambda notes: self.repository.save_session_notes(self.room_key, notes),
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
        
        self.server_info = self._create_label(status_bar, f"Server: {JAMULUS_SERVER}:{JAMULUS_PORT}", font_size=9)
        self.server_info.pack(side=tk.RIGHT, padx=10, pady=5)

        self._tooltips.extend(
            [
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
            # #region agent log
            self._debug_log(
                hypothesis_id="H5",
                location="webjam_app_enhanced.py:_show_setup_once",
                message="setup_skipped_autosetup_disabled",
                data={},
            )
            # #endregion
            return
        setup_seen = self.repository.get_setting("setup_completed", "0")
        # #region agent log
        self._debug_log(
            hypothesis_id="H5",
            location="webjam_app_enhanced.py:_show_setup_once",
            message="setup_flag_checked",
            data={"setup_seen": setup_seen},
        )
        # #endregion
        if setup_seen == "1":
            return

        self.show_setup_wizard(mark_complete=True)

    def _show_bootstrap_admin_notice(self) -> None:
        bootstrap_password = self.repository.get_bootstrap_admin_password()
        if bootstrap_password:
            show_bootstrap_admin_notice(bootstrap_password)

    def show_setup_wizard(self, mark_complete: bool = False):
        self.metrics_service.increment("metric_setup_wizard_opened")
        active_mode = get_mode_by_key(self.mode_key)

        def on_complete() -> None:
            if mark_complete:
                self.repository.set_setting("setup_completed", "1")
            self.metrics_service.increment("metric_setup_wizard_completed")

        SetupWizard(
            self.root,
            on_complete=on_complete,
            settings=SETTINGS,
            find_jamulus=self.find_jamulus,
            diagnostics_provider=self.jamulus_controller.get_audio_diagnostics,
            mode_label=active_mode.label,
            mode_help=active_mode.quick_help,
        ).show()

    def on_mode_selected(self, mode_label: str) -> None:
        selected = get_mode_by_label(mode_label)
        self.mode_key = selected.key
        if not self.template_var.get().strip():
            self.template_var.set(selected.default_template)
        if not self.session_goal_var.get().strip():
            self.session_goal_var.set(selected.default_goal)
        self.save_room_context()
        self.session_canvas.refresh()
        self.metrics_service.increment(f"metric_mode_selected_{selected.key}")
        self.repository.append_cohort_event(
            self.cohort_name,
            "mode_selected",
            {"mode_key": selected.key, "template_name": self.template_var.get().strip()},
        )

    def on_review_state_change(self, state: str) -> None:
        self.review_state = state
        self.save_room_context()
        self.repository.append_cohort_event(self.cohort_name, "review_state_changed", {"state": state, "mode_key": self.mode_key})

    def save_room_context(self) -> None:
        template_name = (self.template_var.get() if hasattr(self, "template_var") else self.template_name).strip()
        session_goal = (self.session_goal_var.get() if hasattr(self, "session_goal_var") else self.session_goal_text).strip()
        active_mode = get_mode_by_key(self.mode_key)
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

    def _set_status_banner(self, text: str, color: str = "#ffffff") -> None:
        self.status_label.configure(text=text)
        try:
            if CTK_AVAILABLE:
                self.status_indicator.configure(text_color=color)
            else:
                self.status_indicator.configure(fg=color)
        except Exception:
            pass

    def _defer_service_start(self) -> None:
        # #region agent log
        self._debug_log(
            hypothesis_id="H1",
            location="webjam_app_enhanced.py:_defer_service_start",
            message="defer_service_start",
            data={"elapsed_ms": int((time.perf_counter() - self._startup_started_at) * 1000)},
        )
        # #endregion
        self._set_status_banner("Initializing background services...", color="#ffcc00")
        self.root.after(120, self._start_background_services)

    def _start_background_services(self) -> None:
        if self._service_bootstrapped:
            return
        self._service_bootstrapped = True
        # #region agent log
        t0 = time.perf_counter()
        self._debug_log(
            hypothesis_id="H1",
            location="webjam_app_enhanced.py:_start_background_services",
            message="service_start_begin",
            data={},
        )
        # #endregion

        bridge_started = self.api_bridge.start()
        if not bridge_started:
            LOGGER.warning("Local companion API bridge unavailable (install fastapi + uvicorn).")

        try:
            self.jamulus_controller.start()
            self.audio_monitor.start()
            self.webex_controller.start()
        except Exception as exc:
            LOGGER.exception("Service startup failed: %s", exc)
            self._set_status_banner("Service startup issue (see diagnostics)", color="#ff5555")
            return

        startup_elapsed = time.perf_counter() - self._startup_started_at
        # #region agent log
        self._debug_log(
            hypothesis_id="H1",
            location="webjam_app_enhanced.py:_start_background_services",
            message="service_start_complete",
            data={
                "bridge_started": bridge_started,
                "service_elapsed_ms": int((time.perf_counter() - t0) * 1000),
                "startup_elapsed_ms": int(startup_elapsed * 1000),
            },
        )
        # #endregion
        self._set_status_banner(f"Ready ({startup_elapsed:.1f}s startup)", color="#00cc66")
        self._refresh_readiness()

    def _safe_invoke(self, callback):
        try:
            callback()
        except Exception as exc:
            LOGGER.exception("Shortcut action failed: %s", exc)
            messagebox.showwarning("Action Failed", f"Shortcut action failed:\n{exc}")

    def _retry_action(self, action, attempts: int = 3, base_delay: float = 0.4):
        return RetryService.retry_action(action, attempts=attempts, base_delay=base_delay)

    def _bind_shortcuts(self) -> None:
        self.root.bind("<Control-s>", lambda _e: self._safe_invoke(self.save_mix))
        self.root.bind("<Control-o>", lambda _e: self._safe_invoke(self.load_mix))
        self.root.bind("<Control-q>", lambda _e: self._safe_invoke(self.quit_app))
        self.root.bind("<F1>", lambda _e: self._safe_invoke(self.show_help))
        self.root.bind("<Control-j>", lambda _e: self._safe_invoke(self.launch_jamulus))
        self.root.bind("<Control-w>", lambda _e: self._safe_invoke(self.launch_webex))
        self.root.bind("<Control-r>", lambda _e: self._safe_invoke(self.reset_all_faders))
        self.root.bind("<Control-p>", lambda _e: self._safe_invoke(self.center_all_pans))
        self.root.bind("<space>", lambda _e: self._safe_invoke(self.unmute_all))
        self.root.bind("<Control-plus>", lambda _e: self._safe_invoke(self.increase_text_size))
        self.root.bind("<Control-minus>", lambda _e: self._safe_invoke(self.decrease_text_size))
        self.root.bind("<Control-h>", lambda _e: self._safe_invoke(self.toggle_high_contrast))

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
                f = tkfont.Font(font=current_font)
                family = f.actual("family")
                size = scaled_font_size(abs(int(f.actual("size") or 10)), self.font_scale)
                weight = f.actual("weight")
                widget.configure(font=(family, size, weight))
        except Exception:
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
        except Exception:
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

    def _save_window_geometry(self) -> None:
        try:
            geometry = self.root.winfo_geometry()
            if geometry:
                self.preferences_service.save_window_geometry(geometry)
        except Exception as exc:
            LOGGER.warning("Failed to persist window geometry: %s", exc)

    def reset_window_geometry(self) -> None:
        self.root.geometry(self.preferences_service.reset_window_geometry())
        messagebox.showinfo("Window Reset", "Window size and position reset to default.")

    def reset_all_ui_preferences(self) -> None:
        confirmed = messagebox.askokcancel(
            "Reset UI Preferences",
            "This will reset text size, contrast mode, startup toggles, and window position.\n\nContinue?",
        )
        if not confirmed:
            return

        self.font_scale = 1.0
        self.high_contrast_enabled = False
        self.auto_setup_enabled = True

        self.large_text_var.set(False)
        self.high_contrast_var.set(False)
        self.auto_setup_var.set(True)

        self.root.geometry("1600x900")
        self._apply_accessibility_mode()
        self.preferences_service.reset_window_geometry()
        self.repository.set_setting("setup_completed", "0")

        messagebox.showinfo(
            "UI Preferences Reset",
            "UI preferences were reset.\n\nSetup wizard will show again on next launch.",
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
        )
        if not confirmed:
            return
        self.metrics_service.reset_with_prefix("metric_")
        messagebox.showinfo("Metrics Reset", "Local usage metrics were reset.")

    def set_cohort_name(self) -> None:
        cohort = simpledialog.askstring(
            "Validation Cohort",
            "Set creator cohort tag (visual_artists, writers, designers, mixed_discipline):",
            parent=self.root,
            initialvalue=self.cohort_name,
        )
        if not cohort:
            return
        self.cohort_name = cohort.strip().lower().replace(" ", "_")
        self.repository.set_setting("cohort_name", self.cohort_name)
        self.metrics_service.increment(f"metric_cohort_tagged_{self.cohort_name}")
        messagebox.showinfo("Cohort Updated", f"Current validation cohort: {self.cohort_name}")

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
        messagebox.showinfo("Session Recorded", "Session completion was logged for cohort validation metrics.")

    def export_diagnostics_snapshot(self) -> None:
        try:
            out_path = self.metrics_service.export_snapshot(
                home_dir=Path.home(),
                jamulus_state=self.jamulus_state,
                webex_state=self.webex_state,
                latency_ms=self.network_latency_ms,
                server=f"{JAMULUS_SERVER}:{JAMULUS_PORT}",
                webex_url=WEBEX_URL,
                audio_diagnostics=self.jamulus_controller.get_audio_diagnostics(),
            )
            messagebox.showinfo("Snapshot Exported", f"Diagnostics snapshot written to:\n{out_path}")
        except Exception as exc:
            messagebox.showerror("Export Failed", f"Could not export diagnostics snapshot:\n{exc}")

    def _update_latency_widget(self) -> None:
        label, color = classify_latency_ms(self.network_latency_ms)
        self.latency_label.configure(text=label)
        try:
            if CTK_AVAILABLE:
                self.latency_label.configure(text_color=color)
            else:
                self.latency_label.configure(fg=color)
        except Exception:
            pass

    def _measure_server_latency_async(self) -> None:
        if self._latency_probe_inflight:
            return
        self._latency_probe_inflight = True

        def _probe() -> None:
            measured: float | None = None
            start = time.perf_counter()
            try:
                with socket.create_connection((JAMULUS_SERVER, int(JAMULUS_PORT)), timeout=0.45):
                    measured = max(0.0, (time.perf_counter() - start) * 1000.0)
            except Exception:
                measured = None
            # #region agent log
            self._debug_log(
                hypothesis_id="H2",
                location="webjam_app_enhanced.py:_measure_server_latency_async",
                message="latency_probe_finished",
                data={
                    "latency_ms": None if measured is None else int(measured),
                    "probe_elapsed_ms": int((time.perf_counter() - start) * 1000),
                },
            )
            # #endregion
            self.root.after(0, lambda: self._complete_latency_probe(measured))

        threading.Thread(target=_probe, daemon=True).start()

    def _complete_latency_probe(self, measured: float | None) -> None:
        self._latency_probe_inflight = False
        self.network_latency_ms = measured
        self._update_latency_widget()

    def _refresh_readiness(self) -> None:
        participant_count = len(self.jamulus_controller.get_participants())
        readiness_text, readiness_color = readiness_state(participant_count)
        mode_label = get_mode_by_key(self.mode_key).label

        self.readiness_label.configure(text=readiness_text)
        self.connection_summary.configure(text=f"{connection_summary(self.jamulus_state, self.webex_state)} | Mode: {mode_label}")
        self._set_status_banner(f"{mode_label}: {self.jamulus_state} | {self.webex_state}", color=readiness_color)

    def _poll_connection_health(self) -> None:
        if self.jamulus_process is not None:
            if self.jamulus_process.poll() is None and "Not launched" in self.jamulus_state:
                self.jamulus_state = "Running"
            elif self.jamulus_process.poll() is not None and self.jamulus_state in {"Connected", "Running"}:
                self.jamulus_state = "Not running"

        if self.webex_controller.is_connected and self.webex_state in {"Not opened", "Open failed"}:
            self.webex_state = "Opened in browser"

        self._measure_server_latency_async()
        self._refresh_readiness()
        self.root.after(2500, self._poll_connection_health)

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
        retry = messagebox.askyesno(title, message)
        if retry and retry_callback:
            retry_callback()
            return
        self.show_help(topic="troubleshooting")

    def sign_in(self) -> bool:
        user = self.auth_controller.sign_in_interactive()
        if user is None:
            return False
        self.current_user = user
        return True

    def open_admin_panel(self):
        if not self.auth_controller.authorize(self.current_user, "view_diagnostics", require_sign_in=True):
            signed_in = self.sign_in()
            if not signed_in:
                return
        AdminPanel(self.root, self.repository, self.current_user).show()

    def show_audio_diagnostics(self):
        if not self.auth_controller.authorize(self.current_user, "view_diagnostics", require_sign_in=False):
            return
        self.metrics_service.increment("metric_audio_diagnostics_opened")
        diag = self.jamulus_controller.get_audio_diagnostics()
        text = "\n".join(f"{k}: {v}" for k, v in diag.items())
        messagebox.showinfo("Audio Diagnostics", text)

    def open_diagnostics_panel(self):
        self.metrics_service.increment("metric_diagnostics_panel_opened")
        diag = self.jamulus_controller.get_audio_diagnostics()
        host_ok, host_detail = SetupWizard.check_tcp_hint(JAMULUS_SERVER, int(JAMULUS_PORT))
        jamulus_path = self.find_jamulus() or "Not found"
        show_diagnostics_panel(
            root=self.root,
            jamulus_path=jamulus_path,
            jamulus_server=JAMULUS_SERVER,
            jamulus_port=JAMULUS_PORT,
            host_ok=host_ok,
            host_detail=host_detail,
            webex_url=WEBEX_URL,
            webex_last_error=self.webex_controller.last_error,
            audio_diagnostics=diag,
            on_run_setup=lambda: self.show_setup_wizard(mark_complete=False),
            on_open_help=lambda: self.show_help(topic="troubleshooting"),
            on_export_snapshot=self.export_diagnostics_snapshot,
            on_reset_metrics=self.reset_usage_metrics,
        )
    
    def on_participants_updated(self, participants):
        """Called when participants list changes"""
        # Update participants count
        self.participants_count.configure(text=f"Participants: {len(participants)}")
        
        # Add/remove mixer channels as needed
        current_ids = set(self.mixer_channels.keys())
        new_ids = set(p.channel_id for p in participants)
        
        # Remove channels for disconnected participants
        for channel_id in current_ids - new_ids:
            self.mixer_channels[channel_id].destroy()
            del self.mixer_channels[channel_id]
        
        # Add channels for new participants
        for participant in participants:
            if participant.channel_id not in self.mixer_channels:
                channel = EnhancedMixerChannel(
                    self.channels_container,
                    participant,
                    self.jamulus_controller,
                    font_scale=self.font_scale,
                    high_contrast=self.high_contrast_enabled,
                )
                channel.pack(side=tk.LEFT, padx=8, pady=10, fill=tk.BOTH)
                self.mixer_channels[participant.channel_id] = channel

        self._refresh_readiness()
    
    def update_vu_meters(self):
        """Update VU meters periodically"""
        for channel_id, channel in self.mixer_channels.items():
            level = self.audio_monitor.get_level(channel_id)
            channel.update_vu_meter(level)
        
        # Lower idle refresh rate to reduce background CPU.
        interval_ms = 50 if self.mixer_channels else 220
        # #region agent log
        if not hasattr(self, "_vu_debug_count"):
            self._vu_debug_count = 0
        if self._vu_debug_count < 2:
            self._debug_log(
                hypothesis_id="H4",
                location="webjam_app_enhanced.py:update_vu_meters",
                message="vu_schedule",
                data={"participants": len(self.mixer_channels), "interval_ms": interval_ms},
            )
            self._vu_debug_count += 1
        # #endregion
        self.root.after(interval_ms, self.update_vu_meters)
    
    def add_test_participants(self):
        """Add demo participants to preview mixer controls"""
        test_names = ["You (Local)", "Guitarist", "Bassist", "Drummer", "Vocalist", "Keys"]
        for i, name in enumerate(test_names):
            if i not in self.jamulus_controller.participants:
                self.jamulus_controller.add_participant(name, i)
        self.jamulus_state = "Demo mode"
        self._refresh_readiness()
    
    def launch_jamulus(self):
        """Launch Jamulus client"""
        self.metrics_service.increment("metric_jamulus_launch_attempt")
        jamulus_path = self.find_jamulus()
        if not jamulus_path:
            self.metrics_service.increment("metric_jamulus_launch_failed")
            self._show_actionable_error(
                "Jamulus Not Found",
                what_failed="WebJam could not locate Jamulus.exe.",
                likely_cause="Jamulus is not installed in a default location.",
                next_action="Run setup wizard and install Jamulus, then retry launch.",
                retry_callback=lambda: self.show_setup_wizard(mark_complete=False),
            )
            return
        
        try:
            if self.jamulus_process and self.jamulus_process.poll() is None:
                self.jamulus_state = "Already running"
                self._refresh_readiness()
                messagebox.showinfo("Jamulus", "Jamulus is already running.")
                return
            server = f"{JAMULUS_SERVER}:{JAMULUS_PORT}"
            self.jamulus_process = self._retry_action(
                lambda: subprocess.Popen([jamulus_path, "--connect", server]),
                attempts=3,
                base_delay=0.5,
            )
            self.jamulus_state = f"Connecting ({server})"
            self._refresh_readiness()
            messagebox.showinfo("Success", f"Jamulus launched!\n\nConnecting to: {server}\n\nWait a moment for participants to appear in the mixer.")
            
            # Add local user
            self.jamulus_controller.add_participant("You (Local)", 0)
            self.jamulus_state = "Connected"
            self.metrics_service.increment("metric_jamulus_launch_success")
            self._refresh_readiness()
            
        except Exception as e:
            LOGGER.exception("Failed to launch Jamulus: %s", e)
            self.jamulus_state = "Launch failed"
            self.metrics_service.increment("metric_jamulus_launch_failed")
            self._refresh_readiness()
            self._show_actionable_error(
                "Jamulus Launch Failed",
                what_failed=f"Jamulus could not start ({e}).",
                likely_cause="Invalid path, blocked launch, missing dependency, or transient process startup failure.",
                next_action="Open diagnostics, verify path/server, then retry (launch uses automatic backoff retries).",
                retry_callback=self.launch_jamulus,
            )
    
    def launch_webex(self):
        """Launch Webex meeting"""
        self.metrics_service.increment("metric_webex_open_attempt")
        try:
            def _open_once() -> bool:
                opened_once = self.webex_controller.join_meeting()
                if not opened_once:
                    raise RuntimeError(getattr(self.webex_controller, "last_error", "Unknown browser launch failure"))
                return True

            opened = self._retry_action(
                _open_once,
                attempts=3,
                base_delay=0.4,
            )
            self.webex_state = "Opened in browser"
            self.metrics_service.increment("metric_webex_open_success")
            self._refresh_readiness()
            messagebox.showinfo("Webex Opened", f"Webex meeting opened in your browser:\n\n{WEBEX_URL}\n\nJoin the meeting to see and hear other participants.")
        except Exception as e:
            LOGGER.exception("Failed to open Webex: %s", e)
            self.webex_state = "Open failed"
            self.metrics_service.increment("metric_webex_open_failed")
            self._refresh_readiness()
            self._show_actionable_error(
                "Webex Open Failed",
                what_failed=f"Webex URL could not be opened ({e}).",
                likely_cause="Default browser issue, network filtering, invalid meeting URL, or transient launch issue.",
                next_action="Verify URL in diagnostics/setup wizard and retry (open uses automatic retries).",
                retry_callback=self.launch_webex,
            )
    
    def find_jamulus(self):
        """Find Jamulus installation"""
        for path in JAMULUS_CANDIDATES:
            if Path(path).exists():
                return path
        return None
    
    def save_mix(self):
        """Save current mix settings"""
        self.metrics_service.increment("metric_save_mix_attempt")
        if not self.auth_controller.authorize(self.current_user, "save_mix", require_sign_in=False):
            return
        try:
            self.jamulus_controller.save_mix(str(CONFIG_FILE))
            self.metrics_service.increment("metric_save_mix_success")
            self.repository.add_audit("save_mix", self.current_user.username if self.current_user else "anonymous", str(CONFIG_FILE))
            messagebox.showinfo("Saved", f"Mix settings saved to:\n{CONFIG_FILE}")
        except Exception as e:
            LOGGER.exception("save_mix failed: %s", e)
            self.metrics_service.increment("metric_save_mix_failed")
            self._show_actionable_error(
                "Save Mix Failed",
                what_failed=f"Could not write mix file ({e}).",
                likely_cause="Permission issue or invalid config path.",
                next_action="Run setup wizard to verify config paths, then retry save.",
                retry_callback=self.save_mix,
            )
    
    def load_mix(self):
        """Load saved mix settings"""
        self.metrics_service.increment("metric_load_mix_attempt")
        if not self.auth_controller.authorize(self.current_user, "load_mix", require_sign_in=False):
            return
        if not CONFIG_FILE.exists():
            messagebox.showwarning("No Settings", "No saved mix settings found.")
            return
        
        try:
            self.jamulus_controller.load_mix(str(CONFIG_FILE))
            # Update UI
            for channel_id, channel in self.mixer_channels.items():
                p = channel.participant
                channel.fader.set(p.fader_level)
                channel.pan_slider.set(p.pan)
                channel.update_button_states()
            
            messagebox.showinfo("Loaded", f"Mix settings loaded from:\n{CONFIG_FILE}")
            self.metrics_service.increment("metric_load_mix_success")
        except Exception as e:
            LOGGER.exception("load_mix failed: %s", e)
            self.metrics_service.increment("metric_load_mix_failed")
            self._show_actionable_error(
                "Load Mix Failed",
                what_failed=f"Could not load mix file ({e}).",
                likely_cause="Corrupted or incompatible JSON format.",
                next_action="Save a fresh mix preset after confirming participants are connected.",
                retry_callback=self.load_mix,
            )
    
    def reset_all_faders(self):
        """Reset all faders to unity (0dB)"""
        if not self.auth_controller.authorize(self.current_user, "bulk_reset", require_sign_in=False):
            return
        for participant in self.jamulus_controller.get_participants():
            self.jamulus_controller.set_fader_level(participant.channel_id, 100)
            if participant.channel_id in self.mixer_channels:
                self.mixer_channels[participant.channel_id].fader.set(100)
        self.repository.add_audit("bulk_reset", self.current_user.username if self.current_user else "anonymous", "reset_all_faders")
    
    def unmute_all(self):
        """Unmute all channels"""
        if not self.auth_controller.authorize(self.current_user, "bulk_mute", require_sign_in=False):
            return
        for participant in self.jamulus_controller.get_participants():
            self.jamulus_controller.set_mute(participant.channel_id, False)
            if participant.channel_id in self.mixer_channels:
                self.mixer_channels[participant.channel_id].update_button_states()
        self.repository.add_audit("bulk_mute", self.current_user.username if self.current_user else "anonymous", "unmute_all")
    
    def center_all_pans(self):
        """Center all pan controls"""
        if not self.auth_controller.authorize(self.current_user, "bulk_reset", require_sign_in=False):
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
        
        messagebox.showinfo("About WebJam", about_text)
    
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
            messagebox.showinfo("Troubleshooting", help_text)
            return

        help_text = """Quick Start Guide

Choose your mode in the top bar (Music Jam, Visual Studio, Writer's Room, Design Critique, Storyboard/Film Room).
Set a template and session goal before launch.

1. Launch Jamulus
   Click 'Launch Jamulus' to connect to the audio server.
   Wait for other participants to connect.

2. Launch Webex
   Click 'Launch Webex' to join the video meeting.

3. Collaborate in Session Canvas
   • Add links/artifacts for references
   • Capture live notes and critique prompts
   • Track review state (draft/review/final)

4. Mix Your Session
   • Use vertical faders to adjust volume
   • Use pan controls for stereo positioning
   • Click MUTE to silence a channel
   • Click SOLO to hear only that channel

5. Save Your Mix
   Click 'Save Mix' to save your settings for next time.

Tips:
• Keep all faders near 0dB for best quality
• Use headphones to prevent feedback
• Pan instruments left/right for clarity
• Watch the VU meters to avoid clipping

For troubleshooting, use Help -> Run Setup Wizard or Session -> Open Diagnostics Panel."""
        
        messagebox.showinfo("Quick Start Guide", help_text)
    
    def quit_app(self):
        """Cleanup and quit"""
        if messagebox.askokcancel("Quit", "Are you sure you want to quit WebJam?"):
            self._save_window_geometry()
            self.cleanup()
            self.root.quit()
    
    def cleanup(self):
        """Cleanup on exit"""
        self.audio_monitor.stop()
        self.jamulus_controller.stop()
        self.webex_controller.stop()
        if self.jamulus_process:
            try:
                self.jamulus_process.terminate()
            except:
                pass
    
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

