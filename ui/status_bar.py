"""
Status Bar - Manages status banner, latency indicator, and connection summary.

This module handles:
- Status banner display (messages, colors, timing)
- Latency indicator (color-coded, async updates)
- Connection summary (Jamulus + Webex state)
- Participants count display
"""

import logging
import time
import threading
from typing import Optional, Tuple, Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class StatusConfig:
    """Configuration for status bar elements"""
    font_size: int = 9
    padding_x: int = 10
    padding_y: int = 5


class StatusBar:
    """
    Manages status bar widgets and their updates.
    
    Provides:
    - Status banner with color coding
    - Latency indicator with quality classification
    - Connection summary (Jamulus/Webex)
    - Participants count
    """
    
    def __init__(self, app, ctk_available: bool = True):
        self.app = app
        self.ctk_available = ctk_available
        self.config = StatusConfig()
        
        # Widget references - will be set during setup
        self.status_label: Optional[Any] = None
        self.status_indicator: Optional[Any] = None
        self.participants_count: Optional[Any] = None
        self.connection_summary: Optional[Any] = None
        self.readiness_label: Optional[Any] = None
        self.latency_label: Optional[Any] = None
        self.server_info: Optional[Any] = None
        self.synthetic_banner: Optional[Any] = None
        
        # Internal state
        self._latency_probe_inflight = False
        self._synthetic_audio_shown = False
        
    def create_status_bar(self, parent, theme: dict) -> None:
        """Create the status bar widgets"""
        from ui.ux_status import classify_latency_ms, readiness_state, connection_summary
        
        # Main status bar frame
        status_bar = self._create_frame(parent)
        
        # Participants count
        self.participants_count = self._create_label(
            status_bar, "Participants: 0", font_size=self.config.font_size
        )
        self.participants_count.pack(side='left', padx=self.config.padding_x, pady=self.config.padding_y)
        
        # Connection summary
        jamulus_state = getattr(self.app, 'jamulus_state', 'Not launched')
        webex_state = getattr(self.app, 'webex_state', 'Not opened')
        summary_text = connection_summary(jamulus_state, webex_state)
        self.connection_summary = self._create_label(
            status_bar, summary_text, font_size=self.config.font_size
        )
        self.connection_summary.pack(side='left', padx=self.config.padding_x, pady=self.config.padding_y)
        
        # Readiness label
        ready_text, _ = readiness_state(0)
        self.readiness_label = self._create_label(
            status_bar, ready_text, font_size=self.config.font_size
        )
        self.readiness_label.pack(side='left', padx=self.config.padding_x, pady=self.config.padding_y)
        
        # Latency label
        latency_text, _ = classify_latency_ms(None)
        self.latency_label = self._create_label(
            status_bar, latency_text, font_size=self.config.font_size
        )
        self.latency_label.pack(side='left', padx=self.config.padding_x, pady=self.config.padding_y)
        
        # Server info
        jamulus_server = getattr(self.app, 'jamulus_server', '127.0.0.1')
        jamulus_port = getattr(self.app, 'jamulus_port', 22124)
        self.server_info = self._create_label(
            status_bar, f"Server: {jamulus_server}:{jamulus_port}", font_size=self.config.font_size
        )
        self.server_info.pack(side='left', padx=self.config.padding_x, pady=self.config.padding_y)
        
        # Store reference
        self.status_bar = status_bar
        
    def _create_frame(self, parent) -> Any:
        """Create a status bar frame"""
        if self.ctk_available:
            try:
                import customtkinter as ctk
                return ctk.CTkFrame(parent)
            except ImportError:
                pass
        import tkinter as tk
        return tk.Frame(parent, bg="#2b2b2b", relief='sunken', borderwidth=1)
        
    def _create_label(self, parent, text: str, font_size: int = 10, bold: bool = False) -> Any:
        """Create a status label"""
        if self.ctk_available:
            try:
                import customtkinter as ctk
                return ctk.CTkLabel(
                    parent, 
                    text=text, 
                    font=(self._get_font_family(), font_size, 'bold' if bold else 'normal')
                )
            except ImportError:
                pass
        import tkinter as tk
        return tk.Label(
            parent,
            text=text,
            font=("Arial", font_size, 'bold' if bold else 'normal'),
            bg="#2b2b2b",
            fg="white"
        )
        
    def _get_font_family(self) -> str:
        """Get the default font family"""
        return getattr(self.app, 'THEME', {}).get('font_family', 'Arial')
        
    def update_participants(self, count: int) -> None:
        """Update participants count display"""
        if self.participants_count:
            try:
                text = f"Participants: {count}"
                if hasattr(self.participants_count, 'configure'):
                    self.participants_count.configure(text=text)
                else:
                    self.participants_count.config(text=text)
            except Exception as e:
                logger.debug("Failed to update participants: %s", e)
                
    def update_connection_summary(self, jamulus_state: str, webex_state: str) -> None:
        """Update connection summary text"""
        if self.connection_summary:
            try:
                from ui.ux_status import connection_summary
                text = connection_summary(jamulus_state, webex_state)
                if hasattr(self.connection_summary, 'configure'):
                    self.connection_summary.configure(text=text)
                else:
                    self.connection_summary.config(text=text)
            except Exception as e:
                logger.debug("Failed to update connection summary: %s", e)
                
    def update_latency(self, latency_ms: Optional[float]) -> None:
        """Update latency indicator with color coding"""
        if self.latency_label:
            try:
                from ui.ux_status import classify_latency_ms
                label, color = classify_latency_ms(latency_ms)
                if hasattr(self.latency_label, 'configure'):
                    self.latency_label.configure(text=label)
                    if hasattr(self.latency_label, 'configure'):
                        try:
                            self.latency_label.configure(text_color=color)
                        except:
                            pass
                else:
                    self.latency_label.config(text=label)
            except Exception as e:
                logger.debug("Failed to update latency: %s", e)
                
    def update_readiness(self, participant_count: int, placeholder_count: int = 0) -> None:
        """Update readiness indicator"""
        if self.readiness_label:
            try:
                from ui.ux_status import readiness_state
                text, color = readiness_state(participant_count, placeholder_count)
                if hasattr(self.readiness_label, 'configure'):
                    self.readiness_label.configure(text=text)
                    try:
                        self.readiness_label.configure(text_color=color)
                    except:
                        pass
                else:
                    self.readiness_label.config(text=text)
            except Exception as e:
                logger.debug("Failed to update readiness: %s", e)
                
    def update_server_info(self, server: str, port: int) -> None:
        """Update server info display"""
        if self.server_info:
            try:
                text = f"Server: {server}:{port}"
                if hasattr(self.server_info, 'configure'):
                    self.server_info.configure(text=text)
                else:
                    self.server_info.config(text=text)
            except Exception as e:
                logger.debug("Failed to update server info: %s", e)
                
    def set_status_banner(self, message: str, color: str = "#ffcc00", duration_ms: int = 0) -> None:
        """Show a status banner message"""
        if self.app and hasattr(self.app, '_set_status_banner'):
            self.app._set_status_banner(message, color)
            
        if duration_ms > 0:
            def clear_banner():
                time.sleep(duration_ms / 1000)
                self.clear_status_banner()
            thread = threading.Thread(target=clear_banner, daemon=True)
            thread.start()
            
    def clear_status_banner(self) -> None:
        """Clear the status banner (restore to default)"""
        if self.app and hasattr(self.app, '_set_status_banner'):
            self.app._set_status_banner("Ready to connect", "#00cc66")
            
    def show_synthetic_audio_banner(self) -> bool:
        """Show banner indicating synthetic audio is in use. Returns True if shown."""
        if self._synthetic_audio_shown:
            return False
            
        self._synthetic_audio_shown = True
        
        if self.app and hasattr(self.app, '_set_status_banner'):
            self.app._set_status_banner(
                "⚠️ Audio engine running in demo mode — no actual audio input detected",
                "#ff5555"
            )
            return True
        return False
        
    def clear_synthetic_audio_banner(self) -> None:
        """Clear synthetic audio banner"""
        self._synthetic_audio_shown = False
        
    def measure_latency_async(self) -> None:
        """Start async latency measurement"""
        if self._latency_probe_inflight:
            return
            
        self._latency_probe_inflight = True
        
        def do_measure():
            try:
                latency = self._do_measure_latency()
                self.app.root.after(0, lambda: self._complete_latency_probe(latency))
            except Exception as e:
                logger.debug("Latency measurement failed: %s", e)
            finally:
                self._latency_probe_inflight = False
                
        thread = threading.Thread(target=do_measure, daemon=True)
        thread.start()
        
    def _do_measure_latency(self) -> Optional[float]:
        """Perform actual latency measurement"""
        import socket
        host = getattr(self.app, 'jamulus_server', '127.0.0.1')
        port = getattr(self.app, 'jamulus_port', 22124)
        
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(1.0)
            start = time.perf_counter()
            sock.sendto(b'\x00', (host, port))
            sock.recvfrom(1024)
            latency = (time.perf_counter() - start) * 1000
            return latency
        except:
            return None
        finally:
            try:
                sock.close()
            except:
                pass
                
    def _complete_latency_probe(self, latency: Optional[float]) -> None:
        """Complete latency measurement and update UI"""
        self.app.network_latency_ms = latency
        self.update_latency(latency)