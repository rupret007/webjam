"""
Webex Integration Module for WebJam
Provides interface to embed or control Webex meetings within the application

Note: This is a foundation for future Webex SDK integration.
Currently uses browser-based approach with future extensibility.
"""

import webbrowser
import time
import json
from pathlib import Path
from typing import Optional, List, Dict, Callable
from dataclasses import dataclass
import threading

from core.logging_config import configure_logging
from core.settings import load_settings


@dataclass
class WebexParticipant:
    """Represents a participant in the Webex meeting"""
    id: str
    name: str
    email: str = ""
    is_video_on: bool = True
    is_audio_on: bool = True
    is_host: bool = False
    is_presenting: bool = False


class WebexController:
    """
    Controller for Webex meeting integration
    
    Current Implementation:
    - Opens Webex in default browser
    - Tracks meeting state
    - Provides hooks for future SDK integration
    
    Future Enhancements:
    - Webex Embedded App SDK integration
    - Direct video embedding in WebJam window
    - Programmatic control of meeting features
    - Real-time participant updates
    """
    
    def __init__(self, meeting_url: str):
        self.settings = load_settings()
        self.logger = configure_logging(self.settings).getChild("webex_controller")
        self.meeting_url = meeting_url
        self.is_connected = False
        self.participants: Dict[str, WebexParticipant] = {}
        self.callbacks: List[Callable] = []
        self.monitor_thread: Optional[threading.Thread] = None
        self.running = False
        self.last_error: str = ""
    
    def start(self):
        """Start Webex monitoring"""
        if self.running:
            return
        
        self.running = True
        self.monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.monitor_thread.start()
    
    def stop(self):
        """Stop Webex monitoring"""
        self.running = False
        if self.monitor_thread:
            self.monitor_thread.join(timeout=2)
    
    def _monitor_loop(self):
        """Monitor Webex meeting state"""
        while self.running:
            try:
                # In a full implementation:
                # - Use Webex SDK to get real-time meeting data
                # - Track participant join/leave events
                # - Monitor video/audio state
                # - Get screen sharing status
                
                # For now, this is a placeholder
                time.sleep(5)
                
            except Exception as e:
                self.logger.warning("Webex monitoring error: %s", e)
                time.sleep(10)
    
    def join_meeting(self, name: str = None, email: str = None):
        """
        Join Webex meeting
        
        Args:
            name: Display name for the meeting
            email: Email address (if required)
        """
        try:
            # Open meeting in browser
            opened = webbrowser.open(self.meeting_url)
            if not opened:
                raise RuntimeError("browser refused meeting URL")
            self.is_connected = True
            self.last_error = ""
            self.logger.info("Webex meeting opened: %s", self.meeting_url)
            
            # Future: Use Webex SDK to join programmatically
            # webex_sdk.join_meeting(self.meeting_url, name, email)
            
            return True
        except Exception as e:
            self.is_connected = False
            self.last_error = str(e)
            self.logger.warning("Failed to join Webex meeting: %s", e)
            return False
    
    def leave_meeting(self):
        """Leave Webex meeting"""
        self.is_connected = False
        # Future: webex_sdk.leave_meeting()
        return True
    
    def add_participant(self, participant: WebexParticipant):
        """Add a participant to the tracking"""
        self.participants[participant.id] = participant
        self._notify_callbacks()
    
    def remove_participant(self, participant_id: str):
        """Remove a participant from tracking"""
        if participant_id in self.participants:
            del self.participants[participant_id]
            self._notify_callbacks()
    
    def get_participants(self) -> List[WebexParticipant]:
        """Get list of all participants"""
        return list(self.participants.values())
    
    def register_callback(self, callback: Callable):
        """Register callback for participant updates"""
        self.callbacks.append(callback)
    
    def _notify_callbacks(self):
        """Notify all registered callbacks"""
        for callback in self.callbacks:
            try:
                callback(self.get_participants())
            except Exception as e:
                self.logger.warning("Callback error: %s", e)
    
    def mute_audio(self, participant_id: str = None):
        """
        Mute audio (self or specific participant if host)
        
        Args:
            participant_id: ID of participant to mute, or None for self
        """
        if participant_id:
            if participant_id in self.participants:
                self.participants[participant_id].is_audio_on = False
        # Future: webex_sdk.mute_participant(participant_id)
        return True
    
    def unmute_audio(self, participant_id: str = None):
        """Unmute audio"""
        if participant_id:
            if participant_id in self.participants:
                self.participants[participant_id].is_audio_on = True
        # Future: webex_sdk.unmute_participant(participant_id)
        return True
    
    def enable_video(self, participant_id: str = None):
        """Enable video"""
        if participant_id:
            if participant_id in self.participants:
                self.participants[participant_id].is_video_on = True
        # Future: webex_sdk.enable_video(participant_id)
        return True
    
    def disable_video(self, participant_id: str = None):
        """Disable video"""
        if participant_id:
            if participant_id in self.participants:
                self.participants[participant_id].is_video_on = False
        # Future: webex_sdk.disable_video(participant_id)
        return True
    
    def start_screen_share(self):
        """Start screen sharing"""
        # Future: webex_sdk.start_screen_share()
        self.logger.info("Screen sharing delegated to browser Webex controls.")
        return True
    
    def stop_screen_share(self):
        """Stop screen sharing"""
        # Future: webex_sdk.stop_screen_share()
        return True


class WebexEmbeddedView:
    """
    Future implementation: Embedded Webex view within WebJam window
    
    This would use the Webex Embedded App SDK to show video
    directly in the application instead of a browser window.
    
    Requirements:
    - Webex Embedded App SDK
    - OAuth authentication
    - Custom tkinter video widget
    """
    
    def __init__(self, parent_widget):
        self.parent = parent_widget
        self.embedded_widget = None
        
    def create_embedded_view(self):
        """Create embedded Webex view"""
        # Future implementation:
        # 1. Initialize Webex SDK
        # 2. Create video rendering widget
        # 3. Embed in parent widget
        # 4. Handle video streams
        
        logging_msg = "Embedded view not yet implemented; using browser-based approach."
        try:
            from core.logging_config import configure_logging
            from core.settings import load_settings
            configure_logging(load_settings()).getChild("webex_embedded").info(logging_msg)
        except Exception:
            pass
        
        return None
    
    def show(self):
        """Show embedded view"""
        if self.embedded_widget:
            self.embedded_widget.pack(fill='both', expand=True)
    
    def hide(self):
        """Hide embedded view"""
        if self.embedded_widget:
            self.embedded_widget.pack_forget()


class WebexParticipantSync:
    """
    Synchronize Webex participants with Jamulus participants
    
    This helps match video participants with audio channels
    for a unified experience.
    """
    
    def __init__(self, webex_controller: WebexController, jamulus_controller):
        self.webex = webex_controller
        self.jamulus = jamulus_controller
        self.participant_map: Dict[str, str] = {}  # webex_id -> jamulus_id
    
    def sync_participants(self):
        """
        Attempt to match Webex participants with Jamulus participants
        based on names
        """
        webex_participants = self.webex.get_participants()
        jamulus_participants = self.jamulus.get_participants()
        
        # Simple name matching
        for wp in webex_participants:
            for jp in jamulus_participants:
                # Match by name (case-insensitive, partial match)
                if wp.name.lower() in jp.name.lower() or jp.name.lower() in wp.name.lower():
                    self.participant_map[wp.id] = str(jp.channel_id)
                    try:
                        from core.logging_config import configure_logging
                        from core.settings import load_settings
                        configure_logging(load_settings()).getChild("webex_sync").info(
                            "Matched %s (Webex) -> %s (Jamulus)", wp.name, jp.name
                        )
                    except Exception:
                        pass
        
        return self.participant_map
    
    def get_jamulus_id(self, webex_id: str) -> Optional[str]:
        """Get Jamulus channel ID for a Webex participant"""
        return self.participant_map.get(webex_id)
    
    def get_webex_id(self, jamulus_id: str) -> Optional[str]:
        """Get Webex ID for a Jamulus participant"""
        for wid, jid in self.participant_map.items():
            if jid == jamulus_id:
                return wid
        return None


# Configuration for Webex integration
class WebexConfig:
    """Configuration for Webex integration"""
    
    def __init__(self):
        self.config_file = Path.home() / ".webjam_webex_config.json"
        self.config = self.load_config()
    
    def load_config(self) -> Dict:
        """Load Webex configuration"""
        if self.config_file.exists():
            with open(self.config_file, 'r') as f:
                return json.load(f)
        return {
            'default_meeting_url': '',
            'auto_join': False,
            'default_name': '',
            'embedded_mode': False,  # Future feature
            'sync_with_jamulus': True
        }
    
    def save_config(self):
        """Save Webex configuration"""
        with open(self.config_file, 'w') as f:
            json.dump(self.config, f, indent=2)
    
    def get(self, key: str, default=None):
        """Get configuration value"""
        return self.config.get(key, default)
    
    def set(self, key: str, value):
        """Set configuration value"""
        self.config[key] = value
        self.save_config()


# Utility functions
def open_webex_meeting(url: str):
    """Simple utility to open Webex meeting in browser"""
    try:
        webbrowser.open(url)
        return True
    except Exception as e:
        try:
            from core.logging_config import configure_logging
            from core.settings import load_settings
            configure_logging(load_settings()).getChild("webex_util").warning("Failed to open Webex: %s", e)
        except Exception:
            pass
        return False


def create_webex_controller(meeting_url: str) -> WebexController:
    """Factory function to create and start Webex controller"""
    controller = WebexController(meeting_url)
    controller.start()
    return controller


# Future SDK integration helpers
def install_webex_sdk():
    """
    Instructions for installing Webex SDK when available
    
    The Webex Embedded App SDK would enable:
    - Direct video embedding
    - Programmatic meeting control
    - Real-time participant data
    - Webhook integration
    """
    print("""
Webex SDK Integration (Future Feature)
======================================

To enable advanced Webex features:

1. Install Webex SDK:
   pip install webexteamssdk

2. Register Webex App:
   - Go to developer.webex.com
   - Create a new integration
   - Note your Client ID and Secret

3. Configure OAuth:
   - Set redirect URI
   - Request necessary scopes
   - Implement auth flow

4. Update WebJam configuration:
   - Add Webex credentials
   - Enable embedded mode
   - Restart WebJam

For now, WebJam uses browser-based Webex access.
    """)


if __name__ == "__main__":
    # Test the Webex controller
    print("Testing Webex Controller...")
    
    meeting_url = "https://webjam-sbx.webex.com/meet/webjam01"
    controller = WebexController(meeting_url)
    controller.start()
    
    # Add test participants
    controller.add_participant(WebexParticipant(
        id="user1",
        name="John Doe",
        email="john@example.com",
        is_video_on=True,
        is_audio_on=True
    ))
    
    controller.add_participant(WebexParticipant(
        id="user2",
        name="Jane Smith",
        email="jane@example.com",
        is_video_on=False,
        is_audio_on=True
    ))
    
    print(f"\nParticipants: {len(controller.get_participants())}")
    for p in controller.get_participants():
        video = "📹" if p.is_video_on else "📷"
        audio = "🔊" if p.is_audio_on else "🔇"
        print(f"  {video} {audio} {p.name}")
    
    # Test joining
    print(f"\nJoining meeting: {meeting_url}")
    # controller.join_meeting("Test User")  # Uncomment to actually open browser
    
    controller.stop()
    print("\nTest complete!")

