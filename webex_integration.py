"""
Webex Integration Module for WebJam
Provides interface to embed or control Webex meetings within the application

Note: This is a foundation for future Webex SDK integration.
Currently uses browser-based approach with future extensibility.
"""

import json
import os
import tempfile
import threading
import time
import webbrowser
from pathlib import Path
from typing import Optional, List, Dict, Callable
from dataclasses import dataclass

from core.logging_config import configure_logging
from core.settings import load_settings
from core.webex_url import is_allowed_webex_url, normalize_webex_url


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
        self._lock = threading.Lock()
        self.monitor_thread: Optional[threading.Thread] = None
        self.running = False
        self.browser_opened = False
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
            self.meeting_url = normalize_webex_url(self.meeting_url)
            if not is_allowed_webex_url(self.meeting_url):
                raise RuntimeError("invalid or untrusted Webex meeting URL")
            # Open meeting in browser
            opened = webbrowser.open(self.meeting_url)
            if not opened:
                raise RuntimeError("browser refused meeting URL")
            # Browser launch does not confirm the user joined a meeting.
            self.browser_opened = True
            self.is_connected = False
            self.last_error = ""
            self.logger.info("Webex meeting opened: %s", self.meeting_url)
            
            # Future: Use Webex SDK to join programmatically
            # webex_sdk.join_meeting(self.meeting_url, name, email)
            
            return True
        except Exception as e:
            self.browser_opened = False
            self.is_connected = False
            self.last_error = str(e)
            self.logger.warning("Failed to join Webex meeting: %s", e)
            return False
    
    def leave_meeting(self):
        """Leave Webex meeting"""
        self.browser_opened = False
        self.is_connected = False
        # Future: webex_sdk.leave_meeting()
        return True
    
    def add_participant(self, participant: WebexParticipant):
        """Add a participant to the tracking"""
        with self._lock:
            self.participants[participant.id] = participant
        self._notify_callbacks()
    
    def remove_participant(self, participant_id: str):
        """Remove a participant from tracking"""
        with self._lock:
            if participant_id not in self.participants:
                return
            del self.participants[participant_id]
        self._notify_callbacks()
    
    def get_participants(self) -> List[WebexParticipant]:
        """Get list of all participants"""
        with self._lock:
            return list(self.participants.values())
    
    def register_callback(self, callback: Callable):
        """Register callback for participant updates"""
        with self._lock:
            self.callbacks.append(callback)
    
    def _notify_callbacks(self):
        """Notify all registered callbacks. Snapshot under lock, invoke without holding it."""
        with self._lock:
            snapshot = list(self.callbacks)
            participants = list(self.participants.values())
        for callback in snapshot:
            try:
                callback(participants)
            except Exception as e:
                self.logger.warning("Callback error: %s", e)
    
    def mute_audio(self, participant_id: str = None) -> bool:
        """Mute audio (self or specific participant if host)"""
        if participant_id:
            with self._lock:
                if participant_id not in self.participants:
                    return False
                self.participants[participant_id].is_audio_on = False
        return True

    def unmute_audio(self, participant_id: str = None) -> bool:
        """Unmute audio"""
        if participant_id:
            with self._lock:
                if participant_id not in self.participants:
                    return False
                self.participants[participant_id].is_audio_on = True
        return True

    def enable_video(self, participant_id: str = None) -> bool:
        """Enable video"""
        if participant_id:
            with self._lock:
                if participant_id not in self.participants:
                    return False
                self.participants[participant_id].is_video_on = True
        return True

    def disable_video(self, participant_id: str = None) -> bool:
        """Disable video"""
        if participant_id:
            with self._lock:
                if participant_id not in self.participants:
                    return False
                self.participants[participant_id].is_video_on = False
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
        except Exception as exc:
            import logging
            logging.getLogger(__name__).debug("Could not log embedded view message: %s", exc)
        
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

        def _normalized_name(value: str) -> str:
            return (value or "").strip().lower()

        new_map: Dict[str, str] = {}
        matched_jamulus_ids: set[int] = set()

        # Deterministic one-to-one matching; rebuild each run to avoid stale links.
        for wp in webex_participants:
            webex_name = _normalized_name(wp.name)
            if not webex_name:
                continue
            for jp in jamulus_participants:
                jamulus_name = _normalized_name(getattr(jp, "name", ""))
                if not jamulus_name:
                    continue
                if jp.channel_id in matched_jamulus_ids:
                    continue
                if webex_name == jamulus_name or webex_name in jamulus_name or jamulus_name in webex_name:
                    new_map[wp.id] = str(jp.channel_id)
                    matched_jamulus_ids.add(jp.channel_id)
                    try:
                        from core.logging_config import configure_logging
                        from core.settings import load_settings
                        configure_logging(load_settings()).getChild("webex_sync").info(
                            "Matched %s (Webex) -> %s (Jamulus)", wp.name, jp.name
                        )
                    except Exception as exc:
                        import logging
                        logging.getLogger(__name__).debug("Could not log sync match: %s", exc)
                    break

        self.participant_map = new_map
        return dict(self.participant_map)
    
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
        default = {
            'default_meeting_url': '',
            'auto_join': False,
            'default_name': '',
            'embedded_mode': False,
            'sync_with_jamulus': True,
        }
        if self.config_file.exists():
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    merged = dict(default)
                    merged.update(loaded)
                    return merged
            except (json.JSONDecodeError, OSError):
                pass
        return default

    def save_config(self) -> bool:
        """Save Webex configuration. Returns True on success, False on failure."""
        try:
            self.config_file.parent.mkdir(parents=True, exist_ok=True)
            parent = self.config_file.parent or Path(".")
            fd, temp_name = tempfile.mkstemp(
                prefix=f"{self.config_file.stem}.",
                suffix=".tmp",
                dir=str(parent),
            )
            temp_path = Path(temp_name)
            try:
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as f:
                        json.dump(self.config, f, indent=2)
                except Exception:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
                    raise
                temp_path.replace(self.config_file)
                return True
            finally:
                if temp_path.exists():
                    try:
                        temp_path.unlink()
                    except OSError:
                        pass
        except (OSError, PermissionError, TypeError, ValueError) as exc:
            try:
                from core.logging_config import configure_logging
                from core.settings import load_settings
                configure_logging(load_settings()).getChild("webex_config").warning(
                    "Failed to save Webex config to %s: %s", self.config_file, exc
                )
            except Exception as log_exc:
                import logging
                logging.getLogger(__name__).debug("Could not log save failure: %s", log_exc)
            return False
    
    def get(self, key: str, default=None):
        """Get configuration value"""
        return self.config.get(key, default)
    
    def set(self, key: str, value):
        """Set configuration value and persist it atomically."""
        current_config = self.config
        updated_config = dict(current_config)
        updated_config[key] = value
        self.config = updated_config
        if self.save_config():
            return True
        self.config = current_config
        return False


# Utility functions
def open_webex_meeting(url: str):
    """Simple utility to open Webex meeting in browser"""
    try:
        meeting_url = normalize_webex_url(url)
        if not is_allowed_webex_url(meeting_url):
            return False
        return bool(webbrowser.open(meeting_url))
    except Exception as e:
        try:
            from core.logging_config import configure_logging
            from core.settings import load_settings
            configure_logging(load_settings()).getChild("webex_util").warning("Failed to open Webex: %s", e)
        except Exception as log_exc:
            import logging
            logging.getLogger(__name__).debug("Could not log open failure: %s", log_exc)
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
