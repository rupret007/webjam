"""
WebJam - Music Collaboration Platform
Integrates Jamulus low-latency audio with Webex video conferencing
Features a virtual mixer panel for real-time audio control
"""

import tkinter as tk
from tkinter import ttk, messagebox
import subprocess
import threading
import time
import json
import webbrowser
from pathlib import Path
from dataclasses import dataclass
from typing import List, Optional, Dict
import sys
import os

# Try to use customtkinter for modern UI, fallback to tkinter
try:
    import customtkinter as ctk
    CTK_AVAILABLE = True
except ImportError:
    CTK_AVAILABLE = False
    print("CustomTkinter not available, using standard tkinter")

# ====== CONFIG ======
JAMULUS_SERVER = "172.24.194.9"
JAMULUS_PORT = "22124"
WEBEX_URL = "https://webjam-sbx.webex.com/meet/webjam01"
CONFIG_FILE = Path.home() / ".webjam_config.json"

# Jamulus install locations
JAMULUS_CANDIDATES = [
    r"C:\Program Files\Jamulus\Jamulus.exe",
    r"C:\Program Files (x86)\Jamulus\Jamulus.exe",
]


@dataclass
class Participant:
    """Represents a musician in the session"""
    id: str
    name: str
    level: float = 1.0  # 0.0 to 1.0
    muted: bool = False
    solo: bool = False
    pan: float = 0.0  # -1.0 (left) to 1.0 (right)
    audio_level: float = 0.0  # Current audio level (for VU meter)


class MixerChannel(ctk.CTkFrame if CTK_AVAILABLE else tk.Frame):
    """Individual mixer channel with fader, mute, solo, pan controls"""
    
    def __init__(self, parent, participant: Participant, on_change_callback=None):
        super().__init__(parent)
        self.participant = participant
        self.on_change = on_change_callback
        
        # Configure styling
        if CTK_AVAILABLE:
            self.configure(fg_color="#2b2b2b", corner_radius=10)
        else:
            self.configure(bg="#2b2b2b", relief=tk.RAISED, borderwidth=2)
        
        self.setup_ui()
    
    def setup_ui(self):
        """Create the channel strip UI"""
        padding = 5
        
        # Participant name label at top
        name_label = self._create_label(self.participant.name, font_size=12, bold=True)
        name_label.pack(pady=(padding, 0))
        
        # VU Meter (audio level indicator)
        self.vu_meter = self._create_vu_meter()
        self.vu_meter.pack(pady=padding, padx=padding, fill=tk.X)
        
        # Vertical fader (volume control)
        fader_frame = ctk.CTkFrame(self) if CTK_AVAILABLE else tk.Frame(self, bg="#2b2b2b")
        fader_frame.pack(pady=padding, fill=tk.BOTH, expand=True)
        
        self.fader = self._create_fader(fader_frame)
        self.fader.pack(pady=padding, fill=tk.BOTH, expand=True)
        
        # dB level display
        self.level_label = self._create_label("0 dB", font_size=10)
        self.level_label.pack()
        
        # Pan control
        pan_frame = ctk.CTkFrame(self) if CTK_AVAILABLE else tk.Frame(self, bg="#2b2b2b")
        pan_frame.pack(pady=padding, fill=tk.X)
        
        pan_label = self._create_label("Pan", font_size=9)
        pan_label.pack()
        
        self.pan_slider = self._create_pan_slider(pan_frame)
        self.pan_slider.pack(fill=tk.X, padx=padding)
        
        # Mute and Solo buttons
        button_frame = ctk.CTkFrame(self) if CTK_AVAILABLE else tk.Frame(self, bg="#2b2b2b")
        button_frame.pack(pady=padding, fill=tk.X)
        
        self.mute_btn = self._create_button(button_frame, "M", self.toggle_mute, "#ff4444")
        self.mute_btn.pack(side=tk.LEFT, padx=2, expand=True, fill=tk.X)
        
        self.solo_btn = self._create_button(button_frame, "S", self.toggle_solo, "#44ff44")
        self.solo_btn.pack(side=tk.RIGHT, padx=2, expand=True, fill=tk.X)
    
    def _create_label(self, text, font_size=10, bold=False):
        """Create a styled label"""
        if CTK_AVAILABLE:
            weight = "bold" if bold else "normal"
            return ctk.CTkLabel(self, text=text, font=("Arial", font_size, weight))
        else:
            font = ("Arial", font_size, "bold" if bold else "normal")
            return tk.Label(self, text=text, font=font, bg="#2b2b2b", fg="white")
    
    def _create_vu_meter(self):
        """Create VU meter display"""
        if CTK_AVAILABLE:
            meter = ctk.CTkProgressBar(self, height=10)
            meter.set(0)
            return meter
        else:
            meter = ttk.Progressbar(self, orient=tk.HORIZONTAL, length=100, mode='determinate')
            meter['value'] = 0
            return meter
    
    def _create_fader(self, parent):
        """Create vertical fader control"""
        if CTK_AVAILABLE:
            fader = ctk.CTkSlider(
                parent,
                from_=0,
                to=1,
                orientation="vertical",
                command=self.on_fader_change,
                height=200
            )
            fader.set(self.participant.level)
            return fader
        else:
            fader = tk.Scale(
                parent,
                from_=1,
                to=0,
                resolution=0.01,
                orient=tk.VERTICAL,
                command=self.on_fader_change,
                bg="#2b2b2b",
                fg="white",
                highlightthickness=0,
                length=200
            )
            fader.set(self.participant.level)
            return fader
    
    def _create_pan_slider(self, parent):
        """Create pan control slider"""
        if CTK_AVAILABLE:
            slider = ctk.CTkSlider(
                parent,
                from_=-1,
                to=1,
                orientation="horizontal",
                command=self.on_pan_change
            )
            slider.set(self.participant.pan)
            return slider
        else:
            slider = tk.Scale(
                parent,
                from_=-1,
                to=1,
                resolution=0.1,
                orient=tk.HORIZONTAL,
                command=self.on_pan_change,
                bg="#2b2b2b",
                fg="white",
                highlightthickness=0
            )
            slider.set(self.participant.pan)
            return slider
    
    def _create_button(self, parent, text, command, active_color):
        """Create mute/solo button"""
        if CTK_AVAILABLE:
            btn = ctk.CTkButton(
                parent,
                text=text,
                command=command,
                width=30,
                height=25,
                fg_color="#555555"
            )
        else:
            btn = tk.Button(
                parent,
                text=text,
                command=command,
                bg="#555555",
                fg="white",
                width=3
            )
        btn.active_color = active_color
        btn.inactive_color = "#555555"
        return btn
    
    def on_fader_change(self, value):
        """Handle fader movement"""
        value = float(value)
        self.participant.level = value
        
        # Convert to dB (logarithmic scale)
        if value > 0:
            db = 20 * (value - 1)  # Range: -20dB to 0dB
        else:
            db = -float('inf')
        
        db_str = f"{db:.1f} dB" if db != -float('inf') else "-∞ dB"
        self.level_label.configure(text=db_str)
        
        if self.on_change:
            self.on_change(self.participant)
    
    def on_pan_change(self, value):
        """Handle pan control change"""
        self.participant.pan = float(value)
        if self.on_change:
            self.on_change(self.participant)
    
    def toggle_mute(self):
        """Toggle mute state"""
        self.participant.muted = not self.participant.muted
        
        if CTK_AVAILABLE:
            color = self.mute_btn.active_color if self.participant.muted else self.mute_btn.inactive_color
            self.mute_btn.configure(fg_color=color)
        else:
            color = self.mute_btn.active_color if self.participant.muted else self.mute_btn.inactive_color
            self.mute_btn.configure(bg=color)
        
        if self.on_change:
            self.on_change(self.participant)
    
    def toggle_solo(self):
        """Toggle solo state"""
        self.participant.solo = not self.participant.solo
        
        if CTK_AVAILABLE:
            color = self.solo_btn.active_color if self.participant.solo else self.solo_btn.inactive_color
            self.solo_btn.configure(fg_color=color)
        else:
            color = self.solo_btn.active_color if self.participant.solo else self.solo_btn.inactive_color
            self.solo_btn.configure(bg=color)
        
        if self.on_change:
            self.on_change(self.participant)
    
    def update_vu_meter(self, level: float):
        """Update the VU meter with current audio level"""
        self.participant.audio_level = level
        if CTK_AVAILABLE:
            self.vu_meter.set(level)
        else:
            self.vu_meter['value'] = level * 100


class MixerPanel(ctk.CTkFrame if CTK_AVAILABLE else tk.Frame):
    """Main mixer panel containing all channel strips"""
    
    def __init__(self, parent, on_mixer_change=None):
        super().__init__(parent)
        self.on_mixer_change = on_mixer_change
        self.channels: Dict[str, MixerChannel] = {}
        
        if CTK_AVAILABLE:
            self.configure(fg_color="#1a1a1a")
        else:
            self.configure(bg="#1a1a1a")
        
        # Create scrollable frame for channels
        self.setup_ui()
    
    def setup_ui(self):
        """Setup the mixer panel UI"""
        # Title
        title = self._create_label("Virtual Mixer", font_size=16, bold=True)
        title.pack(pady=10)
        
        # Master controls frame
        master_frame = ctk.CTkFrame(self) if CTK_AVAILABLE else tk.Frame(self, bg="#1a1a1a")
        master_frame.pack(fill=tk.X, padx=10, pady=5)
        
        # Add master controls
        self._create_button(master_frame, "Save Mix", self.save_mix).pack(side=tk.LEFT, padx=5)
        self._create_button(master_frame, "Load Mix", self.load_mix).pack(side=tk.LEFT, padx=5)
        self._create_button(master_frame, "Reset All", self.reset_all).pack(side=tk.LEFT, padx=5)
        
        # Channels container with scrollbar
        channels_container = ctk.CTkScrollableFrame(self, height=400) if CTK_AVAILABLE else tk.Frame(self, bg="#1a1a1a")
        channels_container.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        if not CTK_AVAILABLE:
            # Add scrollbar for standard tkinter
            canvas = tk.Canvas(channels_container, bg="#1a1a1a", highlightthickness=0)
            scrollbar = tk.Scrollbar(channels_container, orient="horizontal", command=canvas.xview)
            self.channels_frame = tk.Frame(canvas, bg="#1a1a1a")
            
            self.channels_frame.bind(
                "<Configure>",
                lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
            )
            
            canvas.create_window((0, 0), window=self.channels_frame, anchor="nw")
            canvas.configure(xscrollcommand=scrollbar.set)
            
            canvas.pack(side="top", fill="both", expand=True)
            scrollbar.pack(side="bottom", fill="x")
        else:
            self.channels_frame = channels_container
    
    def _create_label(self, text, font_size=10, bold=False):
        """Create a styled label"""
        if CTK_AVAILABLE:
            weight = "bold" if bold else "normal"
            return ctk.CTkLabel(self, text=text, font=("Arial", font_size, weight))
        else:
            font = ("Arial", font_size, "bold" if bold else "normal")
            return tk.Label(self, text=text, font=font, bg="#1a1a1a", fg="white")
    
    def _create_button(self, parent, text, command):
        """Create a styled button"""
        if CTK_AVAILABLE:
            return ctk.CTkButton(parent, text=text, command=command)
        else:
            return tk.Button(parent, text=text, command=command, bg="#555555", fg="white")
    
    def add_participant(self, participant: Participant):
        """Add a new participant channel to the mixer"""
        if participant.id in self.channels:
            return
        
        channel = MixerChannel(
            self.channels_frame,
            participant,
            on_change_callback=self.on_channel_change
        )
        channel.pack(side=tk.LEFT, padx=5, pady=5, fill=tk.BOTH, expand=False)
        self.channels[participant.id] = channel
    
    def remove_participant(self, participant_id: str):
        """Remove a participant channel from the mixer"""
        if participant_id in self.channels:
            self.channels[participant_id].destroy()
            del self.channels[participant_id]
    
    def on_channel_change(self, participant: Participant):
        """Called when any channel control changes"""
        if self.on_mixer_change:
            self.on_mixer_change(participant)
    
    def save_mix(self):
        """Save current mixer settings"""
        settings = {}
        for pid, channel in self.channels.items():
            p = channel.participant
            settings[pid] = {
                'name': p.name,
                'level': p.level,
                'muted': p.muted,
                'solo': p.solo,
                'pan': p.pan
            }
        
        try:
            with open(CONFIG_FILE, 'w') as f:
                json.dump(settings, f, indent=2)
            messagebox.showinfo("Saved", "Mixer settings saved successfully!")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save settings: {e}")
    
    def load_mix(self):
        """Load saved mixer settings"""
        if not CONFIG_FILE.exists():
            messagebox.showwarning("No Settings", "No saved mixer settings found.")
            return
        
        try:
            with open(CONFIG_FILE, 'r') as f:
                settings = json.load(f)
            
            # Apply settings to existing channels
            for pid, channel_settings in settings.items():
                if pid in self.channels:
                    channel = self.channels[pid]
                    p = channel.participant
                    
                    p.level = channel_settings.get('level', 1.0)
                    p.muted = channel_settings.get('muted', False)
                    p.solo = channel_settings.get('solo', False)
                    p.pan = channel_settings.get('pan', 0.0)
                    
                    # Update UI
                    channel.fader.set(p.level)
                    channel.pan_slider.set(p.pan)
                    
                    if CTK_AVAILABLE:
                        mute_color = channel.mute_btn.active_color if p.muted else channel.mute_btn.inactive_color
                        channel.mute_btn.configure(fg_color=mute_color)
                        solo_color = channel.solo_btn.active_color if p.solo else channel.solo_btn.inactive_color
                        channel.solo_btn.configure(fg_color=solo_color)
                    else:
                        mute_color = channel.mute_btn.active_color if p.muted else channel.mute_btn.inactive_color
                        channel.mute_btn.configure(bg=mute_color)
                        solo_color = channel.solo_btn.active_color if p.solo else channel.solo_btn.inactive_color
                        channel.solo_btn.configure(bg=solo_color)
            
            messagebox.showinfo("Loaded", "Mixer settings loaded successfully!")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load settings: {e}")
    
    def reset_all(self):
        """Reset all channels to default values"""
        for channel in self.channels.values():
            p = channel.participant
            p.level = 1.0
            p.muted = False
            p.solo = False
            p.pan = 0.0
            
            channel.fader.set(1.0)
            channel.pan_slider.set(0.0)
            
            # Reset button colors
            if CTK_AVAILABLE:
                channel.mute_btn.configure(fg_color=channel.mute_btn.inactive_color)
                channel.solo_btn.configure(fg_color=channel.solo_btn.inactive_color)
            else:
                channel.mute_btn.configure(bg=channel.mute_btn.inactive_color)
                channel.solo_btn.configure(bg=channel.solo_btn.inactive_color)


class WebJamApp:
    """Main WebJam application"""
    
    def __init__(self):
        # Setup main window
        if CTK_AVAILABLE:
            ctk.set_appearance_mode("dark")
            ctk.set_default_color_theme("blue")
            self.root = ctk.CTk()
        else:
            self.root = tk.Tk()
            self.root.configure(bg="#1a1a1a")
        
        self.root.title("WebJam - Music Collaboration Platform")
        self.root.geometry("1400x800")
        
        self.participants: Dict[str, Participant] = {}
        self.jamulus_process: Optional[subprocess.Popen] = None
        self._audio_monitor_stop = threading.Event()

        self.root.protocol("WM_DELETE_WINDOW", self.quit_app)
        self.setup_ui()
        self.start_audio_monitoring()
    
    def setup_ui(self):
        """Setup the main application UI"""
        # Top control bar
        control_bar = ctk.CTkFrame(self.root) if CTK_AVAILABLE else tk.Frame(self.root, bg="#2b2b2b")
        control_bar.pack(fill=tk.X, padx=10, pady=10)
        
        # Title
        title = self._create_label(control_bar, "WebJam Music Collaboration", font_size=18, bold=True)
        title.pack(side=tk.LEFT, padx=10)
        
        # Control buttons
        self._create_button(control_bar, "Launch Webex", self.launch_webex).pack(side=tk.RIGHT, padx=5)
        self._create_button(control_bar, "Launch Jamulus", self.launch_jamulus).pack(side=tk.RIGHT, padx=5)
        
        # Status label
        self.status_label = self._create_label(control_bar, "Ready", font_size=10)
        self.status_label.pack(side=tk.RIGHT, padx=20)
        
        # Main content area with two panels
        content = ctk.CTkFrame(self.root) if CTK_AVAILABLE else tk.Frame(self.root, bg="#1a1a1a")
        content.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # Left panel: Session info and Webex integration
        left_panel = ctk.CTkFrame(content) if CTK_AVAILABLE else tk.Frame(content, bg="#2b2b2b", relief=tk.RAISED, borderwidth=2)
        left_panel.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 5))
        
        session_label = self._create_label(left_panel, "Session Info", font_size=14, bold=True)
        session_label.pack(pady=10)
        
        # Server info
        info_text = f"Jamulus Server: {JAMULUS_SERVER}:{JAMULUS_PORT}\nWebex Meeting: {WEBEX_URL}"
        info_label = self._create_label(left_panel, info_text, font_size=10)
        info_label.pack(pady=10)
        
        # Participants list
        participants_label = self._create_label(left_panel, "Connected Musicians", font_size=12, bold=True)
        participants_label.pack(pady=(20, 5))
        
        if CTK_AVAILABLE:
            self.participants_list = ctk.CTkTextbox(left_panel, height=200)
        else:
            self.participants_list = tk.Text(left_panel, height=10, bg="#3b3b3b", fg="white")
        self.participants_list.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # Instructions
        instructions = """
        Quick Start:
        1. Click 'Launch Jamulus' to connect to the audio server
        2. Click 'Launch Webex' to join the video meeting
        3. Use the mixer panel to control each musician's audio level
        4. Adjust pan, mute, or solo individual channels as needed
        5. Save your mix settings for future sessions
        """
        
        instructions_label = self._create_label(left_panel, instructions, font_size=9)
        instructions_label.pack(pady=10, padx=10)
        
        # Right panel: Mixer
        right_panel = ctk.CTkFrame(content) if CTK_AVAILABLE else tk.Frame(content, bg="#2b2b2b", relief=tk.RAISED, borderwidth=2)
        right_panel.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(5, 0))
        
        self.mixer_panel = MixerPanel(right_panel, on_mixer_change=self.on_mixer_change)
        self.mixer_panel.pack(fill=tk.BOTH, expand=True)
        
        # Add some demo participants (in real app, these would come from Jamulus)
        self.add_demo_participants()
    
    def _create_label(self, parent, text, font_size=10, bold=False):
        """Create a styled label"""
        if CTK_AVAILABLE:
            weight = "bold" if bold else "normal"
            return ctk.CTkLabel(parent, text=text, font=("Arial", font_size, weight))
        else:
            font = ("Arial", font_size, "bold" if bold else "normal")
            bg = parent.cget("bg") if isinstance(parent, (tk.Frame, tk.Tk)) else "#2b2b2b"
            return tk.Label(parent, text=text, font=font, bg=bg, fg="white", justify=tk.LEFT)
    
    def _create_button(self, parent, text, command):
        """Create a styled button"""
        if CTK_AVAILABLE:
            return ctk.CTkButton(parent, text=text, command=command)
        else:
            return tk.Button(parent, text=text, command=command, bg="#4444ff", fg="white", padx=10, pady=5)
    
    def add_demo_participants(self):
        """Add demo participants for testing"""
        demo_names = ["You", "Guitarist", "Bassist", "Drummer", "Vocalist"]
        for i, name in enumerate(demo_names):
            participant = Participant(
                id=f"demo_{i}",
                name=name,
                level=0.8,
                muted=False,
                solo=False,
                pan=0.0
            )
            self.participants[participant.id] = participant
            self.mixer_panel.add_participant(participant)
        
        self.update_participants_list()
    
    def update_participants_list(self):
        """Update the participants list display"""
        if CTK_AVAILABLE:
            self.participants_list.delete("0.0", "end")
        else:
            self.participants_list.delete("1.0", tk.END)
        
        text = ""
        for p in self.participants.values():
            status = "🔇" if p.muted else "🔊"
            solo = "⭐" if p.solo else ""
            text += f"{status} {p.name} {solo}\n"
        
        self.participants_list.insert("0.0" if CTK_AVAILABLE else "1.0", text)
    
    def on_mixer_change(self, participant: Participant):
        """Handle mixer changes"""
        self.update_participants_list()
        # In a real implementation, send these changes to Jamulus
        # to actually control the audio levels
    
    def launch_jamulus(self):
        """Launch Jamulus client"""
        jamulus_path = self.find_jamulus()
        if not jamulus_path:
            messagebox.showerror(
                "Jamulus Not Found",
                "Jamulus is not installed. Please run the WebJam installer first."
            )
            return
        
        try:
            server = f"{JAMULUS_SERVER}:{JAMULUS_PORT}"
            self.jamulus_process = subprocess.Popen([jamulus_path, "--connect", server])
            self.status_label.configure(text=f"Jamulus connected to {server}")
            messagebox.showinfo("Success", f"Jamulus launched and connecting to {server}")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to launch Jamulus: {e}")
    
    def launch_webex(self):
        """Launch Webex meeting"""
        try:
            webbrowser.open(WEBEX_URL)
            self.status_label.configure(text="Webex meeting opened in browser")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to open Webex: {e}")
    
    def find_jamulus(self):
        """Find Jamulus installation"""
        for path in JAMULUS_CANDIDATES:
            if Path(path).exists():
                return path
        return None
    
    def start_audio_monitoring(self):
        """Start audio level monitoring thread"""
        def monitor():
            import random
            while not self._audio_monitor_stop.wait(timeout=0.1):
                # Simulate audio levels (in real app, read from Jamulus)
                for channel in self.mixer_panel.channels.values():
                    if not channel.participant.muted:
                        level = random.random() * 0.8
                        channel.update_vu_meter(level)
        
        thread = threading.Thread(target=monitor, daemon=True)
        thread.start()
    
    def quit_app(self) -> None:
        """Handle window close; cleanup before quitting."""
        self.cleanup()
        self.root.quit()
        self.root.destroy()

    def run(self):
        """Run the application"""
        self.root.mainloop()
    
    def cleanup(self):
        """Cleanup on exit"""
        self._audio_monitor_stop.set()
        if self.jamulus_process:
            self.jamulus_process.terminate()
            try:
                self.jamulus_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.jamulus_process.kill()


def main():
    """Main entry point"""
    # Check for customtkinter
    if not CTK_AVAILABLE:
        print("Note: For a better UI experience, install customtkinter:")
        print("  pip install customtkinter")
    
    app = WebJamApp()
    app.run()


if __name__ == "__main__":
    main()

