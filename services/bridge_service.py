import logging
import subprocess
import sys
import threading
import time
from enum import Enum
from pathlib import Path
from typing import Optional

from webex_integration import WebexLaunchState

LOGGER = logging.getLogger("webjam.services.bridge")


def _bundled_jamulus_candidate() -> Optional[str]:
    """Path to the copy of Jamulus bundled inside WebJam's own app, if any.

    macOS builds nest an unmodified, Apple-signed/notarized ``Jamulus.app``
    at ``WebJam.app/Contents/Resources/Jamulus.app`` (see
    ``.github/workflows/ci.yml``'s macOS build steps), so a fresh install
    works with zero configuration. This returns the path to the executable
    inside that nested bundle when present.

    Windows has no portable Jamulus binary to bundle this way — Jamulus
    only ships an installer there (see ``_bundled_jamulus_installer``) —
    so this always returns ``None`` on Windows.

    Returns ``None`` when not running from a frozen (PyInstaller) build,
    on any platform other than macOS, or when the nested copy isn't
    present (e.g. dev checkouts, or a build that predates bundling).
    """
    if not getattr(sys, "frozen", False):
        return None
    if sys.platform != "darwin":
        return None
    try:
        # Frozen macOS layout: WebJam.app/Contents/MacOS/WebJam
        macos_dir = Path(sys.executable).resolve().parent
        candidate = (
            macos_dir.parent / "Resources" / "Jamulus.app"
            / "Contents" / "MacOS" / "Jamulus"
        )
    except OSError:
        return None
    return str(candidate) if candidate.is_file() else None


def _bundled_jamulus_server_candidate() -> Optional[str]:
    """Return the dedicated server nested in a frozen macOS build.

    Release artifacts copy the official, unmodified ``JamulusServer.app``
    next to the bundled client.  Resolve from ``sys.executable`` so this also
    works when Gatekeeper launches WebJam through App Translocation.
    """
    if not getattr(sys, "frozen", False) or sys.platform != "darwin":
        return None
    try:
        macos_dir = Path(sys.executable).resolve().parent
        candidate = (
            macos_dir.parent / "Resources" / "JamulusServer.app"
            / "Contents" / "MacOS" / "JamulusServer"
        )
    except OSError:
        return None
    return str(candidate) if candidate.is_file() else None


def _bundled_jamulus_installer() -> Optional[str]:
    """Path to the bundled Jamulus Windows installer, if present.

    Jamulus only publishes an NSIS installer on Windows (no portable
    binary), so "bundling" there means shipping that unmodified installer
    inside WebJam's own install directory at ``Jamulus/jamulus_*_win.exe``
    (see the ``Jamulus/`` datas block in ``webjam.spec``) and letting the
    Setup Wizard offer to run it on demand.

    Returns ``None`` when not running from a frozen (PyInstaller) build,
    on any platform other than Windows, or when the bundled installer
    isn't present.
    """
    if not getattr(sys, "frozen", False):
        return None
    if sys.platform != "win32":
        return None
    try:
        app_dir = Path(sys.executable).resolve().parent
        jamulus_dir = app_dir / "Jamulus"
        if not jamulus_dir.is_dir():
            return None
        candidates = sorted(jamulus_dir.glob("jamulus_*_win.exe"))
    except OSError:
        return None
    return str(candidates[0]) if candidates else None


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


PRACTICE_PORT = 22135  # local practice-server port (avoids the 22124 default)


class BridgeService:
    """
    Service layer for Jamulus lifecycle and truthful external Webex launch.

    # Lock invariants
    # ----------------
    # `_reconnect_lock` serialises *writes* to:
    #   - `self.jamulus_state`   (via `_set_jamulus_state`)
    #   - `self.jamulus_process` (assigned alongside the state in `_do_launch`)
    #   - `self.jamulus_reconnect_inflight`
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
        self.webex_state = WebexLaunchState.NOT_OPENED.value
        
        self.jamulus_launch_intended = False
        
        self.jamulus_reconnect_attempts = 0
        self.jamulus_next_reconnect_at = 0.0
        
        self.jamulus_reconnect_inflight = False
        self._reconnect_lock = threading.Lock()
        # Serialises stop_jamulus() vs launch _do_launch() so a rapid Stop→Launch
        # cannot race the old process's port release.
        self._jamulus_lifecycle_lock = threading.Lock()

        # Practice mode: a private Jamulus server on this machine so a
        # musician can validate audio routing and hear themselves with zero
        # internet dependency. `practice_server_process` is the local
        # `Jamulus --server --nogui` subprocess; `practice_mode` makes
        # launch/reconnect target 127.0.0.1 instead of the band server.
        self.practice_mode = False
        self.practice_server_process: Optional[subprocess.Popen] = None

        # File handle for capturing Jamulus stdout+stderr — closed in stop_jamulus.
        # Captures to ~/.webjam_jamulus.log, overwritten on each launch so the
        # user can inspect the CURRENT session's Jamulus output when troubleshooting.
        self._jamulus_log_file: Optional[object] = None
        self._practice_log_file: Optional[object] = None

        # Hosted band server: when settings.host_server_enabled, WebJam
        # supervises the official JamulusServer.app (recording + loopback
        # RPC) instead of the manual server/start_macos_pilot.sh Terminal
        # step. Its lifecycle is deliberately decoupled from the client:
        # Stop Audio never stops the band's server.
        self.hosted_server_process: Optional[subprocess.Popen] = None
        # True only when WebJam authenticated an already-running external
        # JamulusServer through the configured recorder secret. Adopted
        # servers are observed, never terminated by WebJam.
        self._hosted_server_adopted = False
        self._hosted_caffeinate_process: Optional[subprocess.Popen] = None
        self._hosted_log_file: Optional[object] = None
        self._hosted_lifecycle_lock = threading.RLock()
        self._hosted_restart_inflight = False

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

    def _hosting_enabled(self) -> bool:
        """Return the concrete persisted hosting flag, never truthy sentinels."""
        return getattr(self.settings, "host_server_enabled", False) is True

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
        macOS/Linux paths were added still works, and finally falls back to
        the copy of Jamulus bundled inside WebJam's own app on macOS (see
        `_bundled_jamulus_candidate`) so a fresh install works with zero
        configuration.
        """
        from core.settings import AppSettings
        checked: set[str] = set()

        def _resolve(path: str) -> Optional[str]:
            candidate = Path(path).expanduser()
            if candidate.is_file():
                return str(candidate)
            return None

        for path in self.settings.jamulus_candidates:
            if path not in checked:
                checked.add(path)
                resolved = _resolve(path)
                if resolved:
                    return resolved
        # Fallback: check any default candidate not already tried
        for path in AppSettings().jamulus_candidates:
            if path not in checked:
                checked.add(path)
                resolved = _resolve(path)
                if resolved:
                    return resolved
        # Last resort: the copy bundled inside WebJam's own app (macOS only).
        return _bundled_jamulus_candidate()

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

        # No server configured (fresh install where the wizard was skipped,
        # or a hand-edited config).  Without this guard we'd launch Jamulus
        # with "--connect :22124" and fail in a way that looks like a crash.
        # Practice mode is exempt — it supplies its own local target, so a
        # fresh install can practice before the band server even exists.
        server_host = str(self.settings.jamulus_server or "").strip()
        if not server_host and not self.practice_mode:
            with self._reconnect_lock:
                self.jamulus_reconnect_inflight = False
            # Don't keep auto-reconnecting into a missing config.
            self.jamulus_launch_intended = False
            self._set_jamulus_state(JamulusState.NOT_RUNNING)
            self.schedule_ui_callback(self.refresh_readiness)
            if reconnect:
                self.metrics_service.increment("metric_jamulus_reconnect_failed")
                LOGGER.warning("Jamulus reconnect skipped: no server configured.")
                return
            self.metrics_service.increment("metric_jamulus_launch_failed")
            self.show_actionable_error(
                "No Jamulus Server Configured",
                what_failed="WebJam doesn't know which Jamulus server to connect to.",
                likely_cause="The setup wizard was skipped, or the config file was edited.",
                next_action=(
                    "Open Settings (Ctrl+, or the gear in the side rail) and enter "
                    "your band's Jamulus server address, then press Launch Audio again."
                ),
                retry_callback=None,
            )
            return

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
        if self._is_rpc_port_in_use():
            with self._reconnect_lock:
                self.jamulus_reconnect_inflight = False
            self._set_jamulus_state(JamulusState.PORT_IN_USE)
            port = self.settings.jamulus_rpc_port
            if manual:
                self.metrics_service.increment("metric_jamulus_port_conflict")
                self.schedule_ui_callback(self.refresh_readiness)
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
            else:
                self.metrics_service.increment("metric_jamulus_reconnect_failed")
                self.schedule_ui_callback(self.refresh_readiness)
                LOGGER.warning(
                    "Jamulus reconnect skipped: JSON-RPC port %s already in use.", port
                )
            return

        banner_text = "Launching Jamulus..." if not reconnect else "Auto-reconnecting Jamulus..."
        if self.practice_mode:
            banner_text = "Starting practice session..."
        self.set_status_banner(banner_text, color="#ffcc00")

        server = self.effective_server()

        def _do_launch() -> None:
            with self._jamulus_lifecycle_lock:
                try:
                    if self.shutdown_requested():
                        with self._reconnect_lock:
                            self.jamulus_reconnect_inflight = False
                        return

                    if (
                        self._hosting_enabled()
                        and not self.practice_mode
                    ):
                        hosted_ok, hosted_detail = self.ensure_hosted_server()
                        if not hosted_ok:
                            self._set_jamulus_state(JamulusState.STOPPED)
                            with self._reconnect_lock:
                                self.jamulus_reconnect_inflight = False
                            self.show_actionable_error(
                                "Band Server Could Not Start",
                                what_failed=hosted_detail,
                                likely_cause=(
                                    "This Mac hosts the band server, and it "
                                    "must be running before the client joins."
                                ),
                                next_action=(
                                    "Fix the issue above, then press Start "
                                    "Audio again. Hosting can be turned off "
                                    "in Settings if another Mac runs the "
                                    "server."
                                ),
                                retry_callback=None,
                            )
                            self.schedule_ui_callback(self.refresh_readiness)
                            return

                    import secrets as _secrets
                    from core.file_io import atomic_write_text
                    from core.jamulus_rpc_client import DEFAULT_SECRET_PATH
                    jsonrpc_secret_args: list[str] = []
                    try:
                        atomic_write_text(
                            DEFAULT_SECRET_PATH,
                            _secrets.token_urlsafe(24) + "\n",
                            mode=0o600,
                        )
                        jsonrpc_secret_args = [
                            "--jsonrpcsecretfile", str(DEFAULT_SECRET_PATH)
                        ]
                    except OSError as exc:
                        raise RuntimeError(
                            "Could not create Jamulus JSON-RPC secret file; "
                            "refusing to launch without RPC authentication."
                        ) from exc

                    cmd = [
                        jamulus_path,
                        "--connect", server,
                        "--jsonrpcport", str(self.settings.jamulus_rpc_port),
                        *jsonrpc_secret_args,
                    ]
                    log_file = None
                    stdout_dest = subprocess.DEVNULL
                    try:
                        log_path = Path.home() / ".webjam_jamulus.log"
                        log_file = open(log_path, "w", buffering=1)
                        if self._jamulus_log_file is not None:
                            try:
                                self._jamulus_log_file.close()
                            except Exception:
                                pass
                        self._jamulus_log_file = log_file
                        stdout_dest = log_file
                    except OSError as exc:
                        LOGGER.debug("Could not open Jamulus log file: %s", exc)

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

                    def _start_monitoring():
                        time.sleep(2.0)
                        try:
                            self.jamulus_controller.start()
                        except Exception as exc:
                            LOGGER.warning("JamulusController.start() failed: %s", exc)

                    threading.Thread(target=_start_monitoring, daemon=True).start()

                    self.schedule_ui_callback(self.refresh_readiness)
                    if manual:
                        msg = (
                            f"Jamulus launched — connecting to {server}. "
                            "Participants will appear shortly."
                        )
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
                    # If this launch was the client half of a practice session,
                    # the private local server is now orphaned — kill it and exit
                    # practice mode so it doesn't linger until app close.
                    if self.practice_mode:
                        self._terminate_practice_server()
                        self.practice_mode = False
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

    def effective_server(self) -> str:
        """The host:port Jamulus is (or would be) connected to right now —
        the local practice server when practicing, the band server otherwise."""
        if self.practice_mode:
            return f"127.0.0.1:{PRACTICE_PORT}"
        if self._hosting_enabled():
            # The hosting Mac must use loopback. A stale public/LAN address in
            # an older profile would otherwise make the all-in-one action
            # start a local server and connect its client somewhere else.
            return f"127.0.0.1:{int(self.settings.jamulus_port)}"
        host = str(self.settings.jamulus_server or "").strip()
        port = int(self.settings.jamulus_port)
        if ":" in host:
            return host
        return f"{host}:{port}"

    def launch_practice_session(self) -> bool:
        """Start a private local Jamulus server and connect to it.

        Practice mode lets a musician validate their whole audio path —
        interface, virtual cable, levels, mixer control — alone, before the
        first band session, with zero internet dependency.  Returns True if
        the practice server spawned and the client launch was kicked off.
        """
        if self.shutdown_requested():
            return False
        if self.jamulus_process is not None and self.jamulus_process.poll() is None:
            self.schedule_ui_callback(
                lambda: self.set_status_banner(
                    "Stop Audio first, then start a practice session."
                )
            )
            return False

        jamulus_path = self.find_jamulus()
        if not jamulus_path:
            self.metrics_service.increment("metric_practice_launch_failed")
            self.show_actionable_error(
                "Jamulus Not Found",
                what_failed="WebJam could not locate the Jamulus executable.",
                likely_cause="Jamulus is not installed or is in a non-default location.",
                next_action=(
                    "Download Jamulus (free) from https://jamulus.io and install it. "
                    "If it's already installed in a custom location, open Settings "
                    "(Ctrl+,) and set the Jamulus executable path."
                ),
                retry_callback=None,
            )
            return False

        # Spawn the private local server (headless).  Its output goes to a
        # dedicated log for troubleshooting.
        cmd = [jamulus_path, "--server", "--nogui", "--port", str(PRACTICE_PORT)]
        stdout_dest = subprocess.DEVNULL
        practice_log = None
        try:
            log_path = Path.home() / ".webjam_practice_server.log"
            practice_log = open(log_path, "w", buffering=1)
            stdout_dest = practice_log
            self._close_practice_log_file()
            self._practice_log_file = practice_log
        except OSError:
            pass
        popen_kwargs: dict = {
            "stdout": stdout_dest,
            "stderr": subprocess.STDOUT if stdout_dest is not subprocess.DEVNULL
                      else subprocess.DEVNULL,
        }
        import sys
        if sys.platform == "win32":
            popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            self.practice_server_process = subprocess.Popen(cmd, **popen_kwargs)
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("Practice server failed to start: %s", exc)
            self.metrics_service.increment("metric_practice_launch_failed")
            exc_msg = str(exc)
            self.show_actionable_error(
                "Practice Server Failed",
                what_failed=f"The local practice server could not start ({exc_msg}).",
                likely_cause="Jamulus path invalid, or the practice port is blocked.",
                next_action="Check the Jamulus path in Settings, then retry.",
                retry_callback=None,
            )
            return False

        self.practice_mode = True
        self.metrics_service.increment("metric_practice_mode_started")
        # Connect the regular client to the local server.
        self.launch_jamulus(manual=True, reconnect=False)
        return True

    def _close_practice_log_file(self) -> None:
        """Close the practice-server log file if open. Idempotent."""
        if self._practice_log_file is not None:
            try:
                self._practice_log_file.close()
            except Exception:
                pass
            self._practice_log_file = None

    def _terminate_practice_server(self) -> None:
        """Terminate the private local practice server if running. Idempotent."""
        proc = self.practice_server_process
        self.practice_server_process = None
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    proc.kill()
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning("Failed to terminate practice server: %s", exc)
        self._close_practice_log_file()

    def _end_practice_if_server_died(self) -> bool:
        """Reconnect-tick guard: if the local practice server died, end the
        practice session instead of reconnect-looping into a dead port."""
        proc = self.practice_server_process
        if not self.practice_mode or proc is None or proc.poll() is None:
            return False
        LOGGER.warning("Practice server exited — ending practice session")
        # Publish the terminal state synchronously so the reconnect tick and UI
        # cannot observe a dead practice server as still active while teardown
        # continues on the worker below.
        self.practice_mode = False
        self.jamulus_launch_intended = False
        # stop_jamulus() blocks up to ~4s on proc.wait() joins; this runs on
        # the UI-thread reconnect tick, so do the teardown on a worker thread
        # to keep the GUI responsive.
        threading.Thread(
            target=self.stop_jamulus, daemon=True, name="practice-end",
        ).start()
        self.schedule_ui_callback(
            lambda: self.set_status_banner(
                "Practice session ended — the local practice server stopped."
            )
        )
        return True

    # ------------------------------------------------------------------
    # Hosted band server (host_server_enabled)
    # ------------------------------------------------------------------
    JAMULUS_SERVER_BINARY = (
        "/Applications/JamulusServer.app/Contents/MacOS/JamulusServer"
    )
    HOSTED_SERVER_VERSION = "3.12.2"

    def find_jamulus_server_with_source(self) -> tuple[Optional[str], str]:
        """Locate the installed or bundled dedicated server and its source."""
        candidate = Path(self.JAMULUS_SERVER_BINARY)
        if candidate.is_file():
            return str(candidate), "installed"
        bundled = _bundled_jamulus_server_candidate()
        if bundled:
            return bundled, "bundled"
        return None, "missing"

    def find_jamulus_server(self) -> Optional[str]:
        """Locate the dedicated JamulusServer.app binary (macOS pilot)."""
        return self.find_jamulus_server_with_source()[0]

    def hosted_server_owned(self) -> bool:
        """Whether WebJam owns a live server subprocess it may terminate."""
        proc = self.hosted_server_process
        return proc is not None and proc.poll() is None

    def hosted_server_adopted(self) -> bool:
        """Whether an authenticated external server is currently adopted."""
        return bool(self._hosted_server_adopted)

    def hosted_server_alive(self) -> bool:
        """Whether an owned or authenticated adopted server is available."""
        return self.hosted_server_owned() or self.hosted_server_adopted()

    def _probe_hosted_server_rpc(self) -> tuple[bool, str]:
        """Authenticate and verify the configured endpoint is a recorder server."""
        secret_path = str(
            getattr(self.settings, "server_rpc_secret_file", "") or ""
        ).strip()
        if not secret_path:
            return False, "the recorder secret is not configured"
        try:
            from core.jamulus_server_rpc import JamulusServerRpc, read_secret_file

            secret = read_secret_file(secret_path)
            rpc = JamulusServerRpc(
                port=int(self.settings.server_rpc_port), secret=secret
            )
            rpc.CONNECT_TIMEOUT_S = 0.5
            rpc.CALL_TIMEOUT_S = 1.0
            with rpc:
                status = rpc.get_recorder_status()
            if not status.get("initialised", False):
                return False, "the recorder is not initialised"
            return True, "ready"
        except Exception as exc:  # noqa: BLE001
            LOGGER.debug("Hosted server RPC probe failed: %s", exc)
            return False, "authentication or recorder verification failed"

    def _port_free(self, port: int, *, udp: bool = False) -> bool:
        import socket
        kind = socket.SOCK_DGRAM if udp else socket.SOCK_STREAM
        sock = socket.socket(socket.AF_INET, kind)
        try:
            sock.bind(("0.0.0.0" if udp else "127.0.0.1", port))
            return True
        except OSError:
            return False
        finally:
            try:
                sock.close()
            except OSError:
                pass

    def ensure_hosted_server(self) -> tuple[bool, str]:
        """Start (or adopt) the band server on this Mac. Returns (ok, detail).

        Mirrors server/start_macos_pilot.sh: exact 3.12.2 version gate,
        port preflight, 0600 secret, recordings in the server app's sandbox
        container, and a caffeinate power assertion for the server's
        lifetime so the host Mac cannot sleep mid-session.
        """
        with self._hosted_lifecycle_lock:
            if self.hosted_server_owned():
                return True, "already running"
            if self._hosted_server_adopted:
                verified, _reason = self._probe_hosted_server_rpc()
                if verified:
                    return True, "authenticated external server adopted"
                self._hosted_server_adopted = False
            # Clear a dead owned process plus its stale caffeinate/log handles
            # before attempting a replacement.
            if self.hosted_server_process is not None:
                self.stop_hosted_server()
            rpc_port = int(self.settings.server_rpc_port)
            udp_port = int(self.settings.jamulus_port)
            if not self._port_free(rpc_port):
                # A server may already be listening (e.g. the manual Terminal
                # script). Adopt it only after authenticating and exercising
                # the recorder API; an arbitrary listener must never be
                # presented as a healthy band server.
                verified, reason = self._probe_hosted_server_rpc()
                if not verified:
                    return False, (
                        f"TCP {rpc_port} is already in use, but WebJam could "
                        "not verify a Jamulus recorder there ("
                        f"{reason}). Stop the conflicting process or correct "
                        "the recorder secret in Settings."
                    )
                if self._port_free(udp_port, udp=True):
                    return False, (
                        f"The recorder on TCP {rpc_port} authenticated, but "
                        f"the expected Jamulus audio port UDP {udp_port} is "
                        "not listening. Verify the manual server's --port "
                        "setting before retrying."
                    )
                self._hosted_server_adopted = True
                LOGGER.info(
                    "Hosted server: authenticated external server on TCP %s "
                    "— adopting without taking process ownership.", rpc_port,
                )
                return True, "authenticated external server adopted"

            binary, server_source = self.find_jamulus_server_with_source()
            if not binary:
                return False, (
                    "JamulusServer.app 3.12.2 is not available. Downloadable "
                    "macOS builds include it; source builds can use the "
                    "official app in /Applications. Reinstall WebJam or "
                    "install the server, then press Start Audio again."
                )
            try:
                probe = subprocess.run(
                    [binary, "--version"], capture_output=True, text=True,
                    timeout=10,
                )
                version_text = (probe.stdout or "") + (probe.stderr or "")
            except Exception as exc:  # noqa: BLE001
                return False, f"Could not read the server version: {exc}"
            if f"Version {self.HOSTED_SERVER_VERSION}" not in version_text:
                return False, (
                    "The pilot requires JamulusServer.app "
                    f"{self.HOSTED_SERVER_VERSION} exactly; the installed "
                    "copy reports a different version."
                )
            if not self._port_free(udp_port, udp=True):
                return False, (
                    f"UDP port {udp_port} is already in use by another "
                    "application. Quit it, then press Start Audio again."
                )

            from core.file_io import atomic_write_text
            from core.settings import (
                hosted_server_recordings_dir,
                hosted_server_secret_path,
            )
            secret_path = Path(
                (self.settings.server_rpc_secret_file or "").strip()
                or hosted_server_secret_path()
            ).expanduser()
            recordings = Path(
                (self.settings.takes_directory or "").strip()
                or hosted_server_recordings_dir()
            ).expanduser()
            try:
                recordings.mkdir(parents=True, exist_ok=True)
                secret_path.parent.mkdir(parents=True, exist_ok=True)
                if not secret_path.is_file() or not secret_path.stat().st_size:
                    import secrets as _secrets
                    atomic_write_text(
                        secret_path, _secrets.token_hex(32) + "\n", mode=0o600,
                    )
                # Correct an older/manual file with permissive mode before the
                # server reads it. The recorder credential must remain local
                # to this account even when it already existed.
                secret_path.chmod(0o600)
            except OSError as exc:
                return False, (
                    f"Could not prepare the server secret/recordings: {exc}"
                )

            cmd = [
                binary, "--nogui",
                "--port", str(udp_port),
                "--recording", str(recordings), "--norecord",
                "--jsonrpcbindip", "127.0.0.1",
                "--jsonrpcport", str(rpc_port),
                "--jsonrpcsecretfile", str(secret_path),
                "--welcomemessage", "WebJam private band server",
            ]
            stdout_dest = subprocess.DEVNULL
            try:
                log_dir = Path.home() / "Library" / "Logs" / "WebJam"
                log_dir.mkdir(parents=True, exist_ok=True)
                self._close_hosted_log_file()
                self._hosted_log_file = open(
                    log_dir / "jamulus-server.log", "a", buffering=1,
                )
                stdout_dest = self._hosted_log_file
            except OSError:
                pass
            try:
                self.hosted_server_process = subprocess.Popen(
                    cmd,
                    stdout=stdout_dest,
                    stderr=subprocess.STDOUT
                    if stdout_dest is not subprocess.DEVNULL
                    else subprocess.DEVNULL,
                )
            except Exception as exc:  # noqa: BLE001
                LOGGER.exception("Hosted band server failed to start")
                self._close_hosted_log_file()
                return False, f"The band server could not start: {exc}"

            self._hosted_server_adopted = False
            self._start_hosted_caffeinate()

            # Wait for the recorder RPC listener so the client (and the
            # Record button) never race a half-started server.
            deadline = time.monotonic() + 6.0
            while time.monotonic() < deadline:
                if not self.hosted_server_owned():
                    self.stop_hosted_server()
                    return False, (
                        "The band server exited immediately — see "
                        "~/Library/Logs/WebJam/jamulus-server.log."
                    )
                verified, _reason = self._probe_hosted_server_rpc()
                if verified:
                    LOGGER.info(
                        "Hosted band server (%s) ready on UDP %s / RPC %s",
                        server_source, udp_port, rpc_port,
                    )
                    self.metrics_service.increment("metric_host_server_started")
                    return True, f"started from {server_source} app"
                time.sleep(0.15)
            self.stop_hosted_server()
            return False, (
                "The band server started but its control port never became "
                "authenticated and ready — see "
                "~/Library/Logs/WebJam/jamulus-server.log."
            )

    def _start_hosted_caffeinate(self) -> None:
        """Hold a sleep assertion for exactly the server's lifetime."""
        import sys
        if sys.platform != "darwin" or not self.hosted_server_alive():
            return
        try:
            self._hosted_caffeinate_process = subprocess.Popen(
                ["/usr/bin/caffeinate", "-dimsu", "-w",
                 str(self.hosted_server_process.pid)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("caffeinate unavailable: %s", exc)

    def _close_hosted_log_file(self) -> None:
        if self._hosted_log_file is not None:
            try:
                self._hosted_log_file.close()
            except Exception:  # noqa: BLE001
                pass
            self._hosted_log_file = None

    def stop_hosted_server(self) -> None:
        """Terminate the hosted band server. Idempotent; quit-time only —
        Stop Audio deliberately never calls this."""
        with self._hosted_lifecycle_lock:
            # Detach from an externally managed server without sending it a
            # signal. Only the subprocess in hosted_server_process is owned.
            self._hosted_server_adopted = False
            proc = self.hosted_server_process
            self.hosted_server_process = None
            if proc is not None and proc.poll() is None:
                try:
                    proc.terminate()
                    try:
                        proc.wait(timeout=3.0)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                except Exception as exc:  # noqa: BLE001
                    LOGGER.warning("Failed to terminate hosted server: %s", exc)
            caff = self._hosted_caffeinate_process
            self._hosted_caffeinate_process = None
            if caff is not None and caff.poll() is None:
                try:
                    caff.terminate()
                except Exception:  # noqa: BLE001
                    pass
            self._close_hosted_log_file()

    def _restart_hosted_server_if_died(self) -> None:
        """Reconnect-tick supervision: revive a dead hosted server.

        The client keeps running when only the server dies, so the normal
        client-reconnect logic never fires; restart the server directly.
        """
        if not self._hosting_enabled():
            return
        if not self.jamulus_launch_intended:
            return
        lost_adopted_server = False
        if self._hosted_server_adopted:
            verified, _reason = self._probe_hosted_server_rpc()
            if verified:
                return
            LOGGER.warning("Adopted hosted server is no longer reachable")
            self._hosted_server_adopted = False
            lost_adopted_server = True
        proc = self.hosted_server_process
        if proc is not None and proc.poll() is None:
            return
        # Do not turn a user-correctable initial launch failure (missing app,
        # bad version, port conflict) into a noisy retry every three seconds.
        # Supervision starts only after an owned process existed or an adopted
        # server was previously verified.
        if proc is None and not lost_adopted_server:
            return
        if self._hosted_restart_inflight:
            return
        self._hosted_restart_inflight = True
        LOGGER.warning("Hosted band server died — restarting it")
        self.schedule_ui_callback(
            lambda: self.set_status_banner(
                "Band server stopped unexpectedly — restarting it…",
                color="#ffcc00",
            )
        )

        def _restart() -> None:
            try:
                with self._hosted_lifecycle_lock:
                    self.hosted_server_process = None
                ok, detail = self.ensure_hosted_server()
                if not ok:
                    self.schedule_ui_callback(
                        lambda: self.set_status_banner(
                            f"Band server restart failed: {detail}",
                            color="#ff6666",
                        )
                    )
            finally:
                self._hosted_restart_inflight = False

        threading.Thread(
            target=_restart, daemon=True, name="hosted-server-restart",
        ).start()

    def stop_jamulus(self) -> bool:
        """Terminate the Jamulus process, stop monitoring, and clear reconnect state.

        Returns True if a process was actually terminated, False if Jamulus
        was not running.  After calling this, ``jamulus_state`` becomes
        ``"Stopped"`` and the auto-reconnect logic is disabled (because
        ``jamulus_launch_intended`` is set to False).
        """
        with self._jamulus_lifecycle_lock:
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
                    try:
                        proc.wait(timeout=2.0)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                    terminated = True
                except Exception as exc:
                    LOGGER.warning("Failed to terminate Jamulus: %s", exc)

            with self._reconnect_lock:
                self.jamulus_process = None
                self.jamulus_state = JamulusState.STOPPED.value

            self._close_jamulus_log_file()
            self._terminate_practice_server()
            self.practice_mode = False

            self.metrics_service.increment("metric_jamulus_stop")
            self.schedule_ui_callback(self.refresh_readiness)
            return terminated

    def launch_webex(self, manual: bool = True, reconnect: bool = False):
        """Open Webex externally and report only the launch result.

        ``reconnect`` remains in the signature for one compatibility cycle but
        is intentionally ignored: WebJam cannot observe an external meeting
        disconnect and therefore must not invent reconnection behavior.
        """
        if self.shutdown_requested():
            return
            
        if manual:
            self.metrics_service.increment("metric_webex_open_attempt")
            
        self.webex_state = WebexLaunchState.OPENING.value
        self.set_status_banner("Opening Webex externally…", color="#ffcc00")
        self.schedule_ui_callback(self.refresh_readiness)

        def _do_open() -> None:
            try:
                if self.shutdown_requested():
                    self.webex_state = WebexLaunchState.NOT_OPENED.value
                    return

                if not self.webex_controller.join_meeting():
                    raise RuntimeError(
                        self.webex_controller.last_error or "external launch refused"
                    )

                if self.shutdown_requested():
                    self.webex_state = WebexLaunchState.NOT_OPENED.value
                    return

                self.webex_state = WebexLaunchState.OPENED_EXTERNALLY.value
                self.metrics_service.increment("metric_webex_open_success")
                    
                self.schedule_ui_callback(self.refresh_readiness)
                if manual:
                    self.schedule_ui_callback(
                        lambda: self.set_status_banner(
                            "Webex opened externally — finish joining there."
                        )
                    )
            except Exception as exc:
                LOGGER.warning("External Webex launch failed: %s", type(exc).__name__)
                self.webex_state = WebexLaunchState.OPEN_FAILED.value
                self.metrics_service.increment("metric_webex_open_failed")
                self.schedule_ui_callback(self.refresh_readiness)
                self.schedule_ui_callback(
                    lambda: self.show_actionable_error(
                        "Webex Open Failed",
                        what_failed="The configured Webex meeting could not be opened.",
                        likely_cause="Default browser issue, network filtering, invalid meeting URL, or transient launch issue.",
                        next_action="Verify URL in diagnostics/setup wizard and retry.",
                        retry_callback=lambda: self.launch_webex(manual=True),
                        copy_text=self.settings.webex_url,
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
        """Auto-reconnect tick — retries only a dropped Jamulus process.

        Called every ~3 seconds from `ApplicationController._on_reconnect_tick`.
        Per service:

        - **Jamulus**: if `jamulus_launch_intended=True` (user clicked Launch
          Audio at some point and didn't click Stop), and the subprocess has
          died (`poll() is not None`), schedules a relaunch with exponential
          backoff (cap 5 attempts, 45s max delay).
        Reads the `auto_reconnect_enabled` repository setting; returns
        immediately if disabled.  Both retries set `*_inflight=True` to
        prevent double-fire while a relaunch worker thread is in flight.
        """
        if self.shutdown_requested():
            return

        # Hosted-server supervision is a separate reliability promise from
        # client auto-reconnect. Disabling client reconnect must not silently
        # disable the band server's crash recovery.
        self._restart_hosted_server_if_died()
        
        # Check if auto-reconnect is globally enabled in repository
        raw_auto_reconnect = self.repository.get_setting("auto_reconnect_enabled", "1")
        auto_reconnect_enabled = str(raw_auto_reconnect).strip().lower() in {"1", "true", "yes", "on"}
        
        if not auto_reconnect_enabled:
            return
            
        if self._end_practice_if_server_died():
            return

        now = time.monotonic()
        self._attempt_auto_reconnect_jamulus(now)

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
