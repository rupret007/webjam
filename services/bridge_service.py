import logging
import subprocess
import threading
import time
import webbrowser
from pathlib import Path
from typing import Optional, Callable, Any

LOGGER = logging.getLogger("webjam.services.bridge")

class BridgeService:
    """
    Service layer for managing external integrations: Jamulus and Webex.
    Handles launching, monitoring, and reconnection logic.
    """
    
    def __init__(
        self,
        jamulus_controller,
        webex_controller,
        metrics_service,
        repository,
        settings,
        ui_callbacks: dict,
    ):
        self.jamulus_controller = jamulus_controller
        self.webex_controller = webex_controller
        self.metrics_service = metrics_service
        self.repository = repository
        self.settings = settings
        
        # UI Callbacks
        self.set_status_banner = ui_callbacks.get("set_status_banner")
        self.refresh_readiness = ui_callbacks.get("refresh_readiness")
        self.show_actionable_error = ui_callbacks.get("show_actionable_error")
        self.show_message = ui_callbacks.get("show_message")
        self.shutdown_requested = ui_callbacks.get("shutdown_requested", lambda: False)
        self.schedule_ui_callback = ui_callbacks.get("schedule_ui_callback", lambda f: f())

        # State
        self.jamulus_process: Optional[subprocess.Popen] = None
        self.jamulus_state = "Not launched"
        self.webex_state = "Not opened"
        
        self.jamulus_launch_intended = False
        self.webex_launch_intended = False
        
        self.jamulus_reconnect_attempts = 0
        self.webex_reconnect_attempts = 0
        self.jamulus_next_reconnect_at = 0.0
        self.webex_next_reconnect_at = 0.0
        
        self.jamulus_reconnect_inflight = False
        self.webex_reconnect_inflight = False

    def find_jamulus(self):
        """Find Jamulus installation based on candidate paths in settings."""
        for path in self.settings.jamulus_candidates:
            if Path(path).exists():
                return path
        return None

    def launch_jamulus(self, manual: bool = True, reconnect: bool = False):
        """Launch Jamulus client and connect to the configured server."""
        if self.shutdown_requested():
            self.jamulus_reconnect_inflight = False
            return
            
        if manual:
            self.jamulus_launch_intended = True
            self.jamulus_reconnect_attempts = 0
            self.jamulus_next_reconnect_at = 0.0
            self.metrics_service.increment("metric_jamulus_launch_attempt")

        jamulus_path = self.find_jamulus()
        if not jamulus_path:
            if reconnect:
                self.jamulus_reconnect_inflight = False
                self.metrics_service.increment("metric_jamulus_reconnect_failed")
                self.jamulus_state = "Not running"
                self.schedule_ui_callback(self.refresh_readiness)
                LOGGER.warning("Jamulus reconnect skipped: executable not found.")
                return
                
            self.metrics_service.increment("metric_jamulus_launch_failed")
            self.show_actionable_error(
                "Jamulus Not Found",
                what_failed="WebJam could not locate Jamulus.exe.",
                likely_cause="Jamulus is not installed in a default location.",
                next_action="Run setup wizard and install Jamulus, then retry launch.",
                retry_callback=None, # Caller should handle wizard trigger
            )
            return

        if self.jamulus_process and self.jamulus_process.poll() is None:
            self.jamulus_state = "Already running"
            self.jamulus_reconnect_attempts = 0
            self.jamulus_next_reconnect_at = 0.0
            self.jamulus_reconnect_inflight = False
            self.schedule_ui_callback(self.refresh_readiness)
            if manual:
                self.show_message("Jamulus", "Jamulus is already running.")
            return

        banner_text = "Launching Jamulus..." if not reconnect else "Auto-reconnecting Jamulus..."
        self.set_status_banner(banner_text, color="#ffcc00")
        
        server_host = self.settings.jamulus_server
        server_port = self.settings.jamulus_port
        server = f"{server_host}:{server_port}"

        def _do_launch() -> None:
            try:
                if self.shutdown_requested():
                    self.jamulus_reconnect_inflight = False
                    return
                
                # Launch Jamulus with JSON-RPC port so WebJam can query it
                cmd = [
                    jamulus_path,
                    "--connect", server,
                    "--jsonrpcport", str(self.settings.jamulus_rpc_port),
                ]
                proc = None
                for i in range(3):
                    try:
                        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        break
                    except Exception:
                        if i == 2: raise
                        time.sleep(0.5)

                if self.shutdown_requested():
                    if proc: proc.terminate()
                    self.jamulus_reconnect_inflight = False
                    return

                self.jamulus_process = proc
                self.jamulus_state = "Running"
                self.jamulus_reconnect_attempts = 0
                self.jamulus_next_reconnect_at = 0.0
                self.jamulus_reconnect_inflight = False

                if reconnect:
                    self.metrics_service.increment("metric_jamulus_reconnect_success")
                else:
                    self.metrics_service.increment("metric_jamulus_launch_success")

                # Start monitoring (JSON-RPC + audio engine) after brief startup delay
                def _start_monitoring():
                    time.sleep(2.0)  # give Jamulus time to bind its RPC port
                    try:
                        self.jamulus_controller.start()
                    except Exception as exc:
                        LOGGER.warning("JamulusController.start() failed: %s", exc)

                threading.Thread(target=_start_monitoring, daemon=True).start()

                self.schedule_ui_callback(self.refresh_readiness)
                if manual:
                    self.schedule_ui_callback(
                        lambda: self.show_message(
                            "Success",
                            f"Jamulus launched!\n\nConnecting to: {server}\n\nParticipants will appear in the mixer as they join."
                        )
                    )
                
            except Exception as exc:
                LOGGER.exception("Failed to launch Jamulus: %s", exc)
                self.jamulus_state = "Launch failed" if not reconnect else "Not running"
                self.jamulus_reconnect_inflight = False
                
                if reconnect:
                    self.metrics_service.increment("metric_jamulus_reconnect_failed")
                    self.schedule_ui_callback(self.refresh_readiness)
                    return
                    
                self.metrics_service.increment("metric_jamulus_launch_failed")
                self.schedule_ui_callback(self.refresh_readiness)
                self.schedule_ui_callback(
                    lambda: self.show_actionable_error(
                        "Jamulus Launch Failed",
                        what_failed=f"Jamulus could not start ({exc}).",
                        likely_cause="Invalid path, blocked launch, missing dependency, or transient process startup failure.",
                        next_action="Open diagnostics, verify path/server, then retry.",
                        retry_callback=lambda: self.launch_jamulus(manual=True)
                    )
                )

        threading.Thread(target=_do_launch, daemon=True).start()

    def launch_webex(self, manual: bool = True, reconnect: bool = False):
        """Open the Webex meeting URL in the default browser."""
        if self.shutdown_requested():
            self.webex_reconnect_inflight = False
            return
            
        if manual:
            self.webex_launch_intended = True
            self.webex_reconnect_attempts = 0
            self.webex_next_reconnect_at = 0.0
            self.metrics_service.increment("metric_webex_open_attempt")
            
        banner_text = "Opening Webex..." if not reconnect else "Auto-reconnecting Webex..."
        self.set_status_banner(banner_text, color="#ffcc00")

        def _do_open() -> None:
            try:
                if self.shutdown_requested():
                    self.webex_reconnect_inflight = False
                    return
                
                # Retry logic
                success = False
                last_err = "Unknown"
                for i in range(3):
                    try:
                        if self.webex_controller.join_meeting():
                            success = True
                            break
                    except Exception as e:
                        last_err = str(e)
                    time.sleep(0.4)
                
                if not success:
                    raise RuntimeError(last_err)

                if self.shutdown_requested():
                    self.webex_reconnect_inflight = False
                    return
                    
                self.webex_state = "Opened in browser"
                self.webex_reconnect_attempts = 0
                self.webex_next_reconnect_at = 0.0
                self.webex_reconnect_inflight = False
                
                if reconnect:
                    self.metrics_service.increment("metric_webex_reconnect_success")
                else:
                    self.metrics_service.increment("metric_webex_open_success")
                    
                self.schedule_ui_callback(self.refresh_readiness)
                if manual:
                    url = self.settings.webex_url
                    self.schedule_ui_callback(
                        lambda: self.show_message(
                            "Webex Opened",
                            f"Webex meeting opened in your browser:\n\n{url}\n\nJoin the meeting to see and hear other participants."
                        )
                    )
            except Exception as exc:
                LOGGER.exception("Failed to open Webex: %s", exc)
                self.webex_state = "Open failed"
                self.webex_reconnect_inflight = False
                
                if reconnect:
                    self.metrics_service.increment("metric_webex_reconnect_failed")
                    self.schedule_ui_callback(self.refresh_readiness)
                    return
                    
                self.metrics_service.increment("metric_webex_open_failed")
                self.schedule_ui_callback(self.refresh_readiness)
                self.schedule_ui_callback(
                    lambda: self.show_actionable_error(
                        "Webex Open Failed",
                        what_failed=f"Webex URL could not be opened ({exc}).",
                        likely_cause="Default browser issue, network filtering, invalid meeting URL, or transient launch issue.",
                        next_action="Verify URL in diagnostics/setup wizard and retry.",
                        retry_callback=lambda: self.launch_webex(manual=True)
                    )
                )

        threading.Thread(target=_do_open, daemon=True).start()

    def _reconnect_delay_seconds(self, attempts: int) -> float:
        """Calculate exponential backoff delay."""
        # Constants from main app for consistency
        RECONNECT_BASE_DELAY_SECONDS = 1.5
        RECONNECT_MAX_DELAY_SECONDS = 45.0
        
        delay = RECONNECT_BASE_DELAY_SECONDS * (2 ** (attempts - 1))
        return min(delay, RECONNECT_MAX_DELAY_SECONDS)

    def attempt_auto_reconnects(self):
        """Run the auto-reconnection logic for both Jamulus and Webex."""
        if self.shutdown_requested():
            return
        
        # Check if auto-reconnect is globally enabled in repository
        raw_auto_reconnect = self.repository.get_setting("auto_reconnect_enabled", "1")
        auto_reconnect_enabled = str(raw_auto_reconnect).strip().lower() in {"1", "true", "yes", "on"}
        
        if not auto_reconnect_enabled:
            return
            
        now = time.monotonic()
        self._attempt_auto_reconnect_jamulus(now)
        self._attempt_auto_reconnect_webex(now)

    def _attempt_auto_reconnect_jamulus(self, now: float):
        if not self.jamulus_launch_intended:
            return
            
        is_running = self.jamulus_process is not None and self.jamulus_process.poll() is None
        if is_running:
            self.jamulus_reconnect_attempts = 0
            self.jamulus_next_reconnect_at = 0.0
            self.jamulus_reconnect_inflight = False
            return
            
        if self.jamulus_reconnect_inflight:
            return
            
        # Max attempts constant from main app
        RECONNECT_MAX_ATTEMPTS = 5
        
        if self.jamulus_reconnect_attempts >= RECONNECT_MAX_ATTEMPTS:
            return
            
        if now < self.jamulus_next_reconnect_at:
            return
            
        self.jamulus_reconnect_attempts += 1
        self.jamulus_next_reconnect_at = now + self._reconnect_delay_seconds(self.jamulus_reconnect_attempts)
        self.jamulus_reconnect_inflight = True
        self.metrics_service.increment("metric_jamulus_reconnect_attempt")
        self.launch_jamulus(manual=False, reconnect=True)

    def _attempt_auto_reconnect_webex(self, now: float):
        if not self.webex_launch_intended:
            return
            
        if self.webex_controller.is_connected:
            self.webex_reconnect_attempts = 0
            self.webex_next_reconnect_at = 0.0
            self.webex_reconnect_inflight = False
            return
            
        if self.webex_reconnect_inflight:
            return
            
        if self.webex_state not in ("Open failed", "Not opened"):
            # If it's already "Opened in browser", we usually don't auto-reconnect 
            # unless we have a way to detect the browser tab was closed.
            return
            
        RECONNECT_MAX_ATTEMPTS = 5
        
        if self.webex_reconnect_attempts >= RECONNECT_MAX_ATTEMPTS:
            return
            
        if now < self.webex_next_reconnect_at:
            return
            
        self.webex_reconnect_attempts += 1
        self.webex_next_reconnect_at = now + self._reconnect_delay_seconds(self.webex_reconnect_attempts)
        self.webex_reconnect_inflight = True
        self.metrics_service.increment("metric_webex_reconnect_attempt")
        self.launch_webex(manual=False, reconnect=True)
