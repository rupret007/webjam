import logging
import subprocess
import threading
import time
from enum import Enum
from pathlib import Path
from typing import Optional

LOGGER = logging.getLogger("webjam.services.bridge")


class JamulusState(str, Enum):
    """Canonical Jamulus subprocess lifecycle states.

    Inherits from `str` so that legacy `if state == "Running":` style
    comparisons keep working transparently — the enum members compare
    equal to their `.value` strings.
    """
    NOT_LAUNCHED   = "Not launched"
    NOT_RUNNING    = "Not running"
    NOT_FOUND      = "Not found"
    ALREADY        = "Already running"
    PORT_IN_USE    = "Port in use"
    RUNNING        = "Running"
    LAUNCH_FAILED  = "Launch failed"
    STOPPED        = "Stopped"


class BridgeService:
    """
    Service layer for managing external integrations: Jamulus and Webex.
    Handles launching, monitoring, and reconnection logic.

    # Lock invariants
    # ----------------
    # `_reconnect_lock` serialises *writes* to:
    #   - `self.jamulus_state`   (via `_set_jamulus_state`)
    #   - `self.jamulus_process` (assigned alongside the state in `_do_launch`)
    #   - `self.jamulus_reconnect_inflight` / `webex_reconnect_inflight`
    #
    # Reads are intentionally *not* under the lock. On CPython, attribute
    # reads of a single pointer-typed value (a `str` subclass like
    # `JamulusState`, or `None`/a `Popen` reference) are atomic by virtue
    # of the GIL — adding a lock around every read site (e.g. in
    # `refresh_readiness`) would be pure overhead with no correctness
    # benefit. Code that needs a *consistent* multi-attribute snapshot
    # (e.g. `_attempt_auto_reconnect_jamulus`) takes the lock explicitly.
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
        self.jamulus_state: str = JamulusState.NOT_LAUNCHED.value
        self.webex_state = "Not opened"
        
        self.jamulus_launch_intended = False
        self.webex_launch_intended = False
        
        self.jamulus_reconnect_attempts = 0
        self.webex_reconnect_attempts = 0
        self.jamulus_next_reconnect_at = 0.0
        self.webex_next_reconnect_at = 0.0
        
        self.jamulus_reconnect_inflight = False
        self.webex_reconnect_inflight = False
        self._reconnect_lock = threading.Lock()

        # File handle for capturing Jamulus stdout+stderr — closed in stop_jamulus.
        # Captures to ~/.webjam_jamulus.log, overwritten on each launch so the
        # user can inspect the CURRENT session's Jamulus output when troubleshooting.
        self._jamulus_log_file: Optional[object] = None

    def _set_jamulus_state(self, state: "JamulusState | str") -> None:
        """Atomically update `jamulus_state` under `_reconnect_lock`.

        Accepts either a `JamulusState` enum member or a raw string for
        backwards compatibility with any external caller. Always stores
        the underlying `.value` string so existing equality checks
        against string literals (`if jamulus_state == "Running":`)
        continue to work.
        """
        value = state.value if isinstance(state, JamulusState) else state
        with self._reconnect_lock:
            self.jamulus_state = value

    def _is_rpc_port_in_use(self) -> bool:
        """Return True if the configured Jamulus JSON-RPC port is already bound.

        Detects the common 'second WebJam instance' case where a previous
        Jamulus is still running and holding the port.  Without this check,
        Popen would succeed but Jamulus would silently fail to bind RPC,
        leaving the user with a running subprocess that can't be controlled.
        """
        import socket
        port = self.settings.jamulus_rpc_port
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            # Bind-test: if we can bind, the port is free.  Use SO_REUSEADDR=False
            # to mirror Jamulus's binding intent.
            sock.bind(("127.0.0.1", port))
            return False
        except OSError:
            return True
        finally:
            try:
                sock.close()
            except OSError:
                pass

    def find_jamulus(self):
        """Find Jamulus installation.

        Checks user-configured candidates first, then falls back to the
        AppSettings default candidates so that a config file written before
        macOS/Linux paths were added still works.
        """
        from core.settings import AppSettings
        checked: set[str] = set()
        for path in self.settings.jamulus_candidates:
            if path not in checked:
                checked.add(path)
                if Path(path).exists():
                    return path
        # Fallback: check any default candidate not already tried
        for path in AppSettings().jamulus_candidates:
            if path not in checked:
                checked.add(path)
                if Path(path).exists():
                    return path
        return None

    def launch_jamulus(self, manual: bool = True, reconnect: bool = False):
        """Launch the Jamulus client subprocess and connect to the band's server.

        Args:
            manual: True when triggered by the user clicking 'Launch Audio'.
                Sets `jamulus_launch_intended=True` so the auto-reconnect
                tick will retry on crash.  False when called from
                `attempt_auto_reconnects` itself (avoids resetting state).
            reconnect: True when this is an auto-reconnect attempt.  Skips
                the actionable-error dialog on failure (would be too noisy)
                and emits reconnect-specific metrics.

        Side effects:
            - Resolves the Jamulus binary via `find_jamulus()` and shows
              the 'Jamulus Not Found' actionable error if missing.
            - Tests if the JSON-RPC port is already bound (port-conflict
              detection) and shows an actionable error if so.
            - Spawns a daemon thread that runs `subprocess.Popen` (with
              up to 3 retries) and starts `JamulusController.start()`
              after a 2s settle delay.
            - Captures Jamulus stdout+stderr to `~/.webjam_jamulus.log`.
            - Updates `self.jamulus_state` ('Running' / 'Launch failed'
              / 'Not found' / 'Port in use') and calls `refresh_readiness`
              to update the UI.
        """
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
                self._set_jamulus_state(JamulusState.NOT_RUNNING)
                self.schedule_ui_callback(self.refresh_readiness)
                LOGGER.warning("Jamulus reconnect skipped: executable not found.")
                return

            # Audit-found bug: previously this manual-launch failure path didn't
            # clear `jamulus_reconnect_inflight`, leaving a stale True flag from
            # any earlier reconnect attempt. The QTimer-driven reconnect tick
            # would then skip the retry indefinitely.
            with self._reconnect_lock:
                self.jamulus_reconnect_inflight = False
            self.metrics_service.increment("metric_jamulus_launch_failed")
            self._set_jamulus_state(JamulusState.NOT_FOUND)
            self.schedule_ui_callback(self.refresh_readiness)
            self.show_actionable_error(
                "Jamulus Not Found",
                what_failed="WebJam could not locate the Jamulus executable.",
                likely_cause="Jamulus is not installed or is in a non-default location.",
                next_action=(
                    "Download Jamulus (free) from https://jamulus.io and install it. "
                    "If it's already installed in a custom location, open Settings (Ctrl+,) "
                    "and set the Jamulus executable path."
                ),
                retry_callback=None,
            )
            return

        if self.jamulus_process and self.jamulus_process.poll() is None:
            self._set_jamulus_state(JamulusState.ALREADY)
            self.jamulus_reconnect_attempts = 0
            self.jamulus_next_reconnect_at = 0.0
            self.jamulus_reconnect_inflight = False
            self.schedule_ui_callback(self.refresh_readiness)
            if manual:
                # Non-blocking flash instead of a modal dialog
                self.schedule_ui_callback(
                    lambda: self.set_status_banner("Jamulus is already running.")
                )
            return

        # Detect port conflict before launching Jamulus.  If the JSON-RPC port
        # is already in use (typically: another WebJam instance, or a previous
        # Jamulus process that didn't shut down cleanly), Popen would succeed
        # but Jamulus would silently fail to bind — leaving a running
        # subprocess we can't control via RPC.
        if manual and self._is_rpc_port_in_use():
            with self._reconnect_lock:
                self.jamulus_reconnect_inflight = False
            self._set_jamulus_state(JamulusState.PORT_IN_USE)
            self.metrics_service.increment("metric_jamulus_port_conflict")
            self.schedule_ui_callback(self.refresh_readiness)
            port = self.settings.jamulus_rpc_port
            self.show_actionable_error(
                "Jamulus Port In Use",
                what_failed=f"Port {port} (Jamulus JSON-RPC) is already in use on this machine.",
                likely_cause=(
                    "Another WebJam instance is already running, or a previous "
                    "Jamulus process didn't shut down cleanly."
                ),
                next_action=(
                    "Close any other WebJam instances and quit any running Jamulus, "
                    "then retry. To use a different port, set the "
                    "WEBJAM_JAMULUS_RPC_PORT environment variable."
                ),
                retry_callback=lambda: self.launch_jamulus(manual=True),
            )
            return

        banner_text = "Launching Jamulus..." if not reconnect else "Auto-reconnecting Jamulus..."
        self.set_status_banner(banner_text, color="#ffcc00")
        
        server_host = self.settings.jamulus_server
        server_port = self.settings.jamulus_port
        server = f"{server_host}:{server_port}"

        def _do_launch() -> None:
            try:
                if self.shutdown_requested():
                    with self._reconnect_lock:
                        self.jamulus_reconnect_inflight = False
                    return

                # Launch Jamulus with JSON-RPC port so WebJam can query it
                cmd = [
                    jamulus_path,
                    "--connect", server,
                    "--jsonrpcport", str(self.settings.jamulus_rpc_port),
                ]
                # Capture Jamulus stdout+stderr to ~/.webjam_jamulus.log for
                # post-hoc troubleshooting. Best-effort — fall back to DEVNULL
                # if we can't open the file (e.g. read-only home directory).
                log_file = None
                stdout_dest = subprocess.DEVNULL
                try:
                    log_path = Path.home() / ".webjam_jamulus.log"
                    log_file = open(log_path, "w", buffering=1)
                    # Close any previous log file before reassigning
                    if self._jamulus_log_file is not None:
                        try:
                            self._jamulus_log_file.close()
                        except Exception:
                            pass
                    self._jamulus_log_file = log_file
                    stdout_dest = log_file
                except OSError as exc:
                    LOGGER.debug("Could not open Jamulus log file: %s", exc)

                # Windows: hide the spurious console window that subprocess.Popen
                # would otherwise pop up alongside the Jamulus GUI.
                # POSIX: no-op (CREATE_NO_WINDOW doesn't exist there).
                popen_kwargs: dict = {
                    "stdout": stdout_dest,
                    "stderr": subprocess.STDOUT if log_file else subprocess.DEVNULL,
                }
                import sys
                if sys.platform == "win32":
                    popen_kwargs["creationflags"] = (
                        getattr(subprocess, "CREATE_NO_WINDOW", 0)
                    )

                proc = None
                for i in range(3):
                    try:
                        proc = subprocess.Popen(cmd, **popen_kwargs)
                        break
                    except Exception:
                        if i == 2:
                            raise
                        time.sleep(0.5)

                if self.shutdown_requested():
                    if proc:
                        proc.terminate()
                    self._close_jamulus_log_file()
                    with self._reconnect_lock:
                        self.jamulus_reconnect_inflight = False
                    return

                # Atomically publish: process handle, state, and inflight flag.
                # The audit found that `jamulus_process` was being written
                # from this worker thread while the QTimer reconnect tick
                # could read it on the UI thread without coordination — fold
                # all three writes into a single locked section so that any
                # observer either sees pre-launch state (process=None,
                # state="Not launched"/etc.) or post-launch state
                # (process=proc, state="Running"), never an inconsistent mix.
                with self._reconnect_lock:
                    self.jamulus_process = proc
                    self.jamulus_state = JamulusState.RUNNING.value
                    self.jamulus_reconnect_inflight = False
                self.jamulus_reconnect_attempts = 0
                self.jamulus_next_reconnect_at = 0.0

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
                    msg = f"Jamulus launched — connecting to {server}. Participants will appear shortly."
                    self.schedule_ui_callback(
                        lambda m=msg: self.set_status_banner(m)
                    )

            except Exception as exc:
                LOGGER.exception("Failed to launch Jamulus: %s", exc)
                # Close the log file we opened before Popen — Jamulus never
                # started, nothing's writing to it.
                self._close_jamulus_log_file()
                self._set_jamulus_state(
                    JamulusState.LAUNCH_FAILED if not reconnect else JamulusState.NOT_RUNNING
                )
                with self._reconnect_lock:
                    self.jamulus_reconnect_inflight = False
                
                if reconnect:
                    self.metrics_service.increment("metric_jamulus_reconnect_failed")
                    self.schedule_ui_callback(self.refresh_readiness)
                    return
                    
                self.metrics_service.increment("metric_jamulus_launch_failed")
                self.schedule_ui_callback(self.refresh_readiness)
                exc_msg = str(exc)
                self.schedule_ui_callback(
                    lambda m=exc_msg: self.show_actionable_error(
                        "Jamulus Launch Failed",
                        what_failed=f"Jamulus could not start ({m}).",
                        likely_cause="Invalid path, blocked launch, missing dependency, or transient process startup failure.",
                        next_action="Open diagnostics, verify path/server, then retry.",
                        retry_callback=lambda: self.launch_jamulus(manual=True)
                    )
                )

        threading.Thread(target=_do_launch, daemon=True).start()

    def _close_jamulus_log_file(self) -> None:
        """Close the Jamulus stdout/stderr log file if it's open. Idempotent."""
        if self._jamulus_log_file is not None:
            try:
                self._jamulus_log_file.close()
            except Exception:
                pass
            self._jamulus_log_file = None

    def stop_jamulus(self) -> bool:
        """Terminate the Jamulus process, stop monitoring, and clear reconnect state.

        Returns True if a process was actually terminated, False if Jamulus
        was not running.  After calling this, ``jamulus_state`` becomes
        ``"Stopped"`` and the auto-reconnect logic is disabled (because
        ``jamulus_launch_intended`` is set to False).
        """
        # Disable any pending reconnect attempts — user explicitly asked to stop
        self.jamulus_launch_intended = False
        self.jamulus_reconnect_attempts = 0
        self.jamulus_next_reconnect_at = 0.0
        with self._reconnect_lock:
            self.jamulus_reconnect_inflight = False

        # Stop monitoring (RPC + UDP) so we don't keep polling a dead process
        try:
            self.jamulus_controller.stop()
        except Exception as exc:
            LOGGER.warning("JamulusController.stop() failed: %s", exc)

        terminated = False
        proc = self.jamulus_process
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                # Give Jamulus 2 seconds to exit gracefully, then force-kill
                try:
                    proc.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    proc.kill()
                terminated = True
            except Exception as exc:
                LOGGER.warning("Failed to terminate Jamulus: %s", exc)

        # Pair the `jamulus_process` clear with the state write under the
        # lock — same invariant as `_do_launch`'s success path.
        with self._reconnect_lock:
            self.jamulus_process = None
            self.jamulus_state = JamulusState.STOPPED.value

        # Close the Jamulus log file if we opened one.  The contents are
        # preserved on disk for post-hoc inspection.
        self._close_jamulus_log_file()

        self.metrics_service.increment("metric_jamulus_stop")
        self.schedule_ui_callback(self.refresh_readiness)
        return terminated

    def leave_webex(self) -> None:
        """Disable Webex auto-reconnect and reset state to 'Not opened'.

        For the embedded path this is paired with ``WebexEmbed.leave_meeting()``
        which is called by the controller; for the browser-fallback path the
        user must close the browser tab themselves (we have no handle on it).
        """
        self.webex_launch_intended = False
        self.webex_reconnect_attempts = 0
        self.webex_next_reconnect_at = 0.0
        with self._reconnect_lock:
            self.webex_reconnect_inflight = False
        self.webex_state = "Not opened"
        try:
            # Best-effort: if the WebexController has a leave_meeting hook, use it
            self.webex_controller.leave_meeting()
        except Exception as exc:
            LOGGER.debug("webex_controller.leave_meeting failed: %s", exc)
        self.metrics_service.increment("metric_webex_leave")
        self.schedule_ui_callback(self.refresh_readiness)

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
                    with self._reconnect_lock:
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
                    with self._reconnect_lock:
                        self.webex_reconnect_inflight = False
                    return

                self.webex_state = "Opened in browser"
                self.webex_reconnect_attempts = 0
                self.webex_next_reconnect_at = 0.0
                with self._reconnect_lock:
                    self.webex_reconnect_inflight = False
                
                if reconnect:
                    self.metrics_service.increment("metric_webex_reconnect_success")
                else:
                    self.metrics_service.increment("metric_webex_open_success")
                    
                self.schedule_ui_callback(self.refresh_readiness)
                if manual:
                    self.schedule_ui_callback(
                        lambda: self.set_status_banner("Webex opened in your browser — join the meeting there to connect with your band.")
                    )
            except Exception as exc:
                LOGGER.exception("Failed to open Webex: %s", exc)
                self.webex_state = "Open failed"
                with self._reconnect_lock:
                    self.webex_reconnect_inflight = False
                
                if reconnect:
                    self.metrics_service.increment("metric_webex_reconnect_failed")
                    self.schedule_ui_callback(self.refresh_readiness)
                    return
                    
                self.metrics_service.increment("metric_webex_open_failed")
                self.schedule_ui_callback(self.refresh_readiness)
                exc_msg = str(exc)
                self.schedule_ui_callback(
                    lambda m=exc_msg: self.show_actionable_error(
                        "Webex Open Failed",
                        what_failed=f"Webex URL could not be opened ({m}).",
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
        """Auto-reconnect tick — retries dropped Jamulus and Webex sessions.

        Called every ~3 seconds from `ApplicationController._on_reconnect_tick`.
        Per service:

        - **Jamulus**: if `jamulus_launch_intended=True` (user clicked Launch
          Audio at some point and didn't click Stop), and the subprocess has
          died (`poll() is not None`), schedules a relaunch with exponential
          backoff (cap 5 attempts, 45s max delay).
        - **Webex**: if `webex_launch_intended=True` and `webex_state` is
          'Open failed' or 'Not opened', schedules a relaunch (same backoff).

        Reads the `auto_reconnect_enabled` repository setting; returns
        immediately if disabled.  Both retries set `*_inflight=True` to
        prevent double-fire while a relaunch worker thread is in flight.
        """
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
        with self._reconnect_lock:
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
        with self._reconnect_lock:
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
