import hashlib
import hmac
import logging
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Optional

from webex_integration import WebexLaunchState
from core.jamulus_profile import (
    JamulusNativeProfileError,
    JamulusNativeProfileManager,
)
from core.settings import AppSettings

LOGGER = logging.getLogger("webjam.services.bridge")

_PINNED_WINDOWS_JAMULUS_INSTALLER = "jamulus_3.12.2_win.exe"
_PINNED_WINDOWS_JAMULUS_SHA256 = (
    "4e7cef6a70fe4525f0e7ea1f1c3301d7298047d9456283b7e12035f3ab5ba7b9"
)
_REFERENCE_HEADLESS_MANIFEST_NAME = "JamulusHeadlessClient.sha256"
_REFERENCE_HEADLESS_MANIFEST_TARGET = (
    "JamulusHeadlessClient.app/Contents/MacOS/JamulusHeadlessClient"
)
_HASH_CHUNK_BYTES = 1024 * 1024


def _bundled_jamulus_candidate() -> Optional[str]:
    """Path to the copy of Jamulus bundled inside WebJam's own app, if any.

    macOS builds nest the official ``Jamulus.app`` at
    ``WebJam.app/Contents/Resources/Jamulus.app`` and prepare it as part of
    the enclosing candidate's ad-hoc signature (see
    ``.github/workflows/ci.yml``). This returns the executable inside that
    nested bundle when present.

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


def _bundled_reference_track_jamulus_candidate() -> Optional[str]:
    """Return only the verified, true-HEADLESS Reference Track companion.

    The ordinary interactive Jamulus client cannot apply hidden mixer-fader
    RPC commands in Jamulus 3.12.2, even when launched with ``--nogui``.
    Reference Track therefore has no configured-path or interactive-client
    fallback. The adjacent manifest is checked again at resolution time so a
    replaced companion cannot inherit WebJam's playback affordance.
    """

    if not getattr(sys, "frozen", False) or sys.platform != "darwin":
        return None
    try:
        macos_dir = Path(sys.executable).resolve().parent
        resources = macos_dir.parent / "Resources"
        candidate = resources / _REFERENCE_HEADLESS_MANIFEST_TARGET
        manifest = resources / _REFERENCE_HEADLESS_MANIFEST_NAME
        if (
            not candidate.is_file()
            or candidate.is_symlink()
            or not os.access(candidate, os.X_OK)
            or not manifest.is_file()
            or manifest.is_symlink()
        ):
            return None
        line = manifest.read_text(encoding="ascii").strip()
        pieces = line.split()
        if (
            len(pieces) != 2
            or len(pieces[0]) != 64
            or any(character not in "0123456789abcdef" for character in pieces[0])
            or pieces[1] != _REFERENCE_HEADLESS_MANIFEST_TARGET
        ):
            return None
        digest = hashlib.sha256()
        with candidate.open("rb") as stream:
            for chunk in iter(lambda: stream.read(_HASH_CHUNK_BYTES), b""):
                digest.update(chunk)
        if not hmac.compare_digest(digest.hexdigest(), pieces[0]):
            return None
    except (OSError, UnicodeError):
        return None
    return str(candidate)


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
    binary), so "bundling" there means shipping that unmodified installer in
    PyInstaller's frozen data root at ``Jamulus/jamulus_3.12.2_win.exe`` (normally
    ``WebJam/_internal/Jamulus``) and letting Setup offer to run it on demand.

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
        # PyInstaller 6 one-directory builds place collected data below
        # ``_internal`` and expose that directory as ``sys._MEIPASS``. Keep
        # the executable directory fallback for older/alternate layouts.
        roots = []
        frozen_data = str(getattr(sys, "_MEIPASS", "") or "").strip()
        if frozen_data:
            roots.append(Path(frozen_data).resolve())
        roots.append(app_dir)
        checked: set[Path] = set()
        for root in roots:
            if root in checked:
                continue
            checked.add(root)
            jamulus_dir = root / "Jamulus"
            if not jamulus_dir.is_dir():
                continue
            installer = jamulus_dir / _PINNED_WINDOWS_JAMULUS_INSTALLER
            if _is_pinned_jamulus_installer(installer):
                return str(installer)
    except OSError:
        return None
    return None


def _is_pinned_jamulus_installer(path: str | Path) -> bool:
    """Verify the exact upstream Windows installer before it is exposed.

    The WebJam executable signature does not seal adjacent PyInstaller data.
    Re-hashing here, and again immediately before launch, prevents a renamed
    or replaced unsigned installer from inheriting WebJam's install affordance.
    """

    candidate = Path(path)
    if candidate.name != _PINNED_WINDOWS_JAMULUS_INSTALLER:
        return False
    try:
        if not candidate.is_file():
            return False
        digest = hashlib.sha256()
        with candidate.open("rb") as stream:
            for chunk in iter(lambda: stream.read(_HASH_CHUNK_BYTES), b""):
                digest.update(chunk)
    except OSError:
        return False
    return hmac.compare_digest(digest.hexdigest(), _PINNED_WINDOWS_JAMULUS_SHA256)


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
RECONNECT_MAX_ATTEMPTS = 5
RECONNECT_HANG_THRESHOLD_SECONDS = 15.0


@dataclass(frozen=True)
class HostedServerCertification:
    """Truthful result of a temporary production server lifecycle proof.

    ``warning`` is reserved for an authenticated server that WebJam did not
    start.  Such a server is useful evidence, but Band Check cannot truthfully
    claim its binary version or clean-stop behavior.
    """

    ok: bool
    warning: bool
    detail: str
    technical_details: tuple[str, ...] = ()
    started_owned_server: bool = False
    adopted_external_server: bool = False
    recorder_authenticated: bool = False
    secret_private: bool = False
    owned_stop_confirmed: bool | None = None
    ports_released: bool | None = None


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
        # Production retries must re-enter the controller so connection
        # timers and the optional v2 peer are restored with the client.
        self.retry_audio_launch = ui_callbacks.get(
            "retry_audio_launch",
            lambda: self.launch_jamulus(manual=True),
        )

        # State
        self.jamulus_process: Optional[subprocess.Popen] = None
        self.jamulus_state: str = JamulusState.NOT_LAUNCHED.value
        self.webex_state = WebexLaunchState.NOT_OPENED.value
        # External Webex handoff is asynchronous. A settings change or a
        # newer Open request invalidates every older worker so its eventual
        # success/failure cannot overwrite the currently configured link.
        self._webex_launch_lock = threading.Lock()
        self._webex_launch_generation = 0
        
        self.jamulus_launch_intended = False
        # A launch is intentionally asynchronous, while Stop/Leave is allowed
        # immediately.  Keep a cancellable request token so a queued worker
        # can never open Jamulus after its originating startup was cancelled.
        # The control lock is never held while acquiring a lifecycle lock:
        # cancellation first marks the request, then waits for any in-flight
        # worker to release the process lifecycle lock for normal cleanup.
        self._jamulus_launch_control_lock = threading.Lock()
        self._pending_jamulus_launch_cancel: threading.Event | None = None
        
        self.jamulus_reconnect_attempts = 0
        self.jamulus_next_reconnect_at = 0.0
        
        self.jamulus_reconnect_inflight = False
        self._reconnect_lock = threading.Lock()
        # Serialises stop_jamulus() vs launch _do_launch() so a rapid Stop→Launch
        # cannot race the old process's port release.
        self._jamulus_lifecycle_lock = threading.Lock()
        # The dedicated profile belongs to Jamulus, not WebJam's CoreAudio
        # layer.  We only provide the supported filename-only --inifile launch
        # contract; Jamulus writes its own device/channel/buffer choices.
        self._active_native_profile = None
        self._native_profile_manager = (
            JamulusNativeProfileManager()
            if sys.platform == "darwin" and isinstance(settings, AppSettings)
            else None
        )

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
        # Remote v3 hosting is an ephemeral launch constraint, never a saved
        # setting.  Legacy v1/v2 hosts intentionally keep JamulusServer's LAN
        # binding; a v3 owner must opt in before this service starts a server.
        self._remote_host_mode = False
        # A v3 guest also needs a process-local marker so its musician name is
        # applied only through authenticated loopback RPC, never exposed in
        # process arguments. This is independent from saved settings.
        self._remote_guest_mode = False

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

    def _set_live_audio_route_owned(self, owned: bool) -> None:
        """Keep WebJam's optional PortAudio meter off a Jamulus-owned route."""

        setter = getattr(self.jamulus_controller, "set_live_audio_route_owned", None)
        if not callable(setter):
            return
        try:
            setter(bool(owned))
        except Exception as exc:  # noqa: BLE001 - monitoring must not block music
            LOGGER.debug("Could not update local-meter route ownership: %s", exc)

    @property
    def remote_host_mode_enabled(self) -> bool:
        """Whether the next owned host server is constrained to loopback.

        This value exists only for the lifetime of this ``BridgeService``.  It
        is deliberately independent from ``AppSettings`` so legacy LAN hosts
        retain their existing bind behavior and the v3 constraint cannot leak
        into a later session through persistence.
        """

        return self._remote_host_mode

    def enable_remote_host_mode(self) -> None:
        """Require loopback-only binding for a v3 server launched next.

        Activation is rejected after any hosted server is live because WebJam
        cannot retrofit or prove the bind address of an existing process.
        Repeated activation for the same remote session is harmless and keeps
        reconnect/restart paths on the same constraint.
        """

        with self._hosted_lifecycle_lock:
            if self._remote_host_mode:
                return
            if self.hosted_server_alive():
                raise RuntimeError(
                    "remote host mode must be enabled before server launch"
                )
            self._remote_host_mode = True

    def disable_remote_host_mode(self) -> None:
        """Clear the ephemeral v3 constraint after its server has stopped."""

        with self._hosted_lifecycle_lock:
            if not self._remote_host_mode:
                return
            if self.hosted_server_alive():
                raise RuntimeError(
                    "remote host mode cannot be cleared while its server is live"
                )
            self._remote_host_mode = False

    @property
    def remote_guest_mode_enabled(self) -> bool:
        return self._remote_guest_mode

    def enable_remote_guest_mode(self) -> None:
        """Arm privacy-safe Jamulus launch for an authenticated v3 guest."""

        with self._jamulus_lifecycle_lock:
            if self._remote_guest_mode:
                return
            if self._hosting_enabled():
                raise RuntimeError("remote guest mode cannot host a server")
            if self.jamulus_process is not None and self.jamulus_process.poll() is None:
                raise RuntimeError("remote guest mode must be enabled before launch")
            self._remote_guest_mode = True

    def disable_remote_guest_mode(self) -> None:
        """Clear the v3 guest marker after the owned Jamulus client stops."""

        with self._jamulus_lifecycle_lock:
            if not self._remote_guest_mode:
                return
            if self.jamulus_process is not None and self.jamulus_process.poll() is None:
                raise RuntimeError("remote guest mode cannot be cleared while audio is live")
            self._remote_guest_mode = False

    def _is_rpc_port_in_use(self) -> bool:
        """Return True if the configured Jamulus JSON-RPC port is already bound.

        Detects the common 'second WebJam instance' case where a previous
        Jamulus is still running and holding the port.  Without this check,
        Popen would succeed but Jamulus would silently fail to bind RPC,
        leaving the user with a running subprocess that can't be controlled.

        macOS keeps a recently closed listener's port unavailable to a strict
        bind while its accepted connection is in ``TIME_WAIT``.  Jamulus can
        safely replace that listener, so after a strict bind failure we use a
        Darwin-only ``SO_REUSEADDR`` probe.  Both the loopback and wildcard
        addresses must be bindable; a real listener or merely bound socket on
        either address therefore remains a fail-closed conflict.
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
            if sys.platform == "darwin":
                return not self._macos_rpc_port_is_rebindable(port)
            return True
        finally:
            try:
                sock.close()
            except OSError:
                pass

    @staticmethod
    def _macos_rpc_port_is_rebindable(port: int) -> bool:
        """Return whether macOS reports only reusable stale TCP state.

        This helper is called only after a strict loopback bind failed.  A
        successful reusable bind to *both* loopback and wildcard excludes
        active sockets bound in either form while allowing a prior Jamulus
        listener's ``TIME_WAIT`` connection to drain in the background.
        """
        import socket

        probes: list[socket.socket] = []
        try:
            for host in ("127.0.0.1", "0.0.0.0"):
                probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                probes.append(probe)
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                probe.bind((host, port))
            return True
        except OSError:
            return False
        finally:
            for probe in probes:
                try:
                    probe.close()
                except OSError:
                    pass

    def find_jamulus(self):
        """Find Jamulus installation.

        Frozen macOS builds prefer their known-good bundled client. Source
        runs check user-configured candidates, then AppSettings defaults.
        """
        from core.settings import AppSettings
        checked: set[str] = set()

        def _resolve(path: str) -> Optional[str]:
            candidate = Path(path).expanduser()
            if candidate.is_file():
                return str(candidate)
            return None

        bundled = _bundled_jamulus_candidate()
        if bundled:
            return bundled

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
        return None

    def find_reference_track_jamulus(self) -> Optional[str]:
        """Resolve the packaged HEADLESS companion with no GUI fallback."""

        return _bundled_reference_track_jamulus_candidate()

    @property
    def native_profile_plan(self):
        """Current Jamulus-owned profile facts, if this client prepared one."""

        return self._active_native_profile

    def refresh_native_profile_plan(self):
        """Read Jamulus's profile fingerprint without writing configuration.

        Used only after an explicit human sound confirmation so returning-user
        evidence describes the profile Jamulus itself just saved.
        """

        plan = self._active_native_profile
        manager = self._native_profile_manager
        if plan is None or manager is None:
            return plan
        refreshed = manager.plan(jamulus_version=plan.jamulus_version)
        self._active_native_profile = refreshed
        return refreshed

    def bring_jamulus_forward(self) -> bool:
        """Best-effort normal app activation after direct owned launch.

        This deliberately does not launch a second client, click controls, or
        scrape Jamulus UI.  The musician chooses Audio/Network Settings inside
        the real Jamulus window.
        """

        proc = self.jamulus_process
        if proc is None or proc.poll() is not None:
            return False
        if sys.platform != "darwin":
            return True
        try:
            subprocess.Popen(
                [
                    "/usr/bin/osascript",
                    "-e",
                    'tell application id "app.jamulussoftware.Jamulus" to activate',
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            return False
        return True

    def launch_jamulus(
        self,
        manual: bool = True,
        reconnect: bool = False,
        force_restart: bool = False,
    ) -> bool:
        """Accept a Jamulus launch and connect to the band's server.

        Returns ``False`` when a synchronous preflight rejects the launch and
        ``True`` once an already-running client or a new launch worker owns the
        request. Later worker failures are reported through normal session UI.

        Args:
            manual: True when triggered by the user clicking 'Launch Audio'.
                Sets `jamulus_launch_intended=True` so the auto-reconnect
                tick will retry on crash.  False when called from
                `attempt_auto_reconnects` itself (avoids resetting state).
            reconnect: True when this is an auto-reconnect attempt.  Skips
                the actionable-error dialog on failure (would be too noisy)
                and emits reconnect-specific metrics.
            force_restart: True when an alive process should be replaced, used
                for hung-process recovery where restart requires a restart of a
                live-but-unresponsive Jamulus process.

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
            return False
            
        with self._jamulus_launch_control_lock:
            previous_launch = self._pending_jamulus_launch_cancel
            if previous_launch is not None:
                previous_launch.set()
            launch_cancel = threading.Event()
            self._pending_jamulus_launch_cancel = launch_cancel
            if manual:
                self.jamulus_launch_intended = True
        if manual:
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
                return False
            self.metrics_service.increment("metric_jamulus_launch_failed")
            self.show_actionable_error(
                "This jam needs a new invite",
                what_failed="WebJam doesn’t have a band session to join.",
                likely_cause="The saved invitation is missing or incomplete.",
                next_action=(
                    "Close WebJam, open it again, and choose Host a Jam or paste "
                    "a fresh invitation from your host."
                ),
                retry_callback=None,
            )
            return False

        jamulus_path = self.find_jamulus()
        if not jamulus_path:
            if reconnect:
                self.jamulus_reconnect_inflight = False
                self.metrics_service.increment("metric_jamulus_reconnect_failed")
                self._set_jamulus_state(JamulusState.NOT_RUNNING)
                self.schedule_ui_callback(self.refresh_readiness)
                LOGGER.warning("Jamulus reconnect skipped: executable not found.")
                return False

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
                "A music component is missing",
                what_failed="WebJam couldn’t start the band audio on this Mac.",
                likely_cause="The WebJam installation is incomplete.",
                next_action="Reinstall the latest WebJam build, then try again.",
                retry_callback=None,
            )
            return False

        if (
            self.jamulus_process is not None
            and self.jamulus_process.poll() is None
        ):
            if force_restart:
                # Recovery path only: keep intent and lifecycle state but replace
                # the existing Jamulus process so UI can recover from a
                # "still alive but not answering" condition.
                old_proc = self.jamulus_process
                try:
                    self.jamulus_controller.stop()
                except Exception as exc:  # noqa: BLE001 - defensive recovery
                    LOGGER.debug("Could not stop old Jamulus controller: %s", exc)
                if old_proc is not None:
                    try:
                        old_proc.terminate()
                        try:
                            old_proc.wait(timeout=2.0)
                        except subprocess.TimeoutExpired:
                            old_proc.kill()
                            old_proc.wait(timeout=2.0)
                    except Exception as exc:  # noqa: BLE001
                        raise RuntimeError(
                            "Could not replace a hung Jamulus process."
                        ) from exc
                with self._reconnect_lock:
                    self.jamulus_process = None
            else:
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
                return True

        # Detect port conflict before launching Jamulus.  If the JSON-RPC port
        # is already in use (typically: another WebJam instance, or a previous
        # Jamulus process that didn't shut down cleanly), Popen would succeed
        # but Jamulus would silently fail to bind — leaving a running
        # subprocess we can't control via RPC.
        if self._is_rpc_port_in_use():
            # A synchronous manual preflight rejection never established a
            # Jamulus process (or, on macOS, an active native profile), so it
            # must not leave crash-recovery intent behind.  Retire only this
            # request generation: a newer Launch click may have superseded us
            # while the port probe was running, and this stale result must not
            # clear that newer request's intent or publish its own failure.
            with self._jamulus_launch_control_lock:
                if self._pending_jamulus_launch_cancel is not launch_cancel:
                    return False
                # Stop/shutdown can cancel this same request without replacing
                # its generation token.  In that case cancellation won the
                # race, so preserve the stopped state and do not surface a
                # stale port-conflict error after the user has left.
                if launch_cancel.is_set() or self.shutdown_requested():
                    self._pending_jamulus_launch_cancel = None
                    return False
                launch_cancel.set()
                self._pending_jamulus_launch_cancel = None
                if manual:
                    self.jamulus_launch_intended = False
            with self._reconnect_lock:
                self.jamulus_reconnect_inflight = False
            self._set_jamulus_state(JamulusState.PORT_IN_USE)
            port = self.settings.jamulus_rpc_port
            if manual:
                self.metrics_service.increment("metric_jamulus_port_conflict")
                self.schedule_ui_callback(self.refresh_readiness)
                self.show_actionable_error(
                    "Another audio session is open",
                    what_failed="WebJam can’t start a second music connection on this Mac.",
                    likely_cause=(
                        "Another WebJam window is open, or the last session is "
                        "still finishing."
                    ),
                    next_action="Close the other WebJam window, wait a moment, then try again.",
                    retry_callback=(
                        None if self.practice_mode else self.retry_audio_launch
                    ),
                )
            else:
                self.metrics_service.increment("metric_jamulus_reconnect_failed")
                self.schedule_ui_callback(self.refresh_readiness)
                LOGGER.warning(
                    "Jamulus reconnect skipped: JSON-RPC port %s already in use.", port
                )
            return False

        banner_text = "Starting your band audio…" if not reconnect else "Reconnecting band audio…"
        if self.practice_mode:
            banner_text = "Starting practice session..."
        self.set_status_banner(banner_text, color="#BF5700")

        server = self.effective_server()

        def _do_launch() -> None:
            with self._jamulus_lifecycle_lock:
                try:
                    def cancelled(proc: Optional[subprocess.Popen] = None) -> bool:
                        """Discard a stale queued launch without publishing it."""

                        if not (launch_cancel.is_set() or self.shutdown_requested()):
                            return False
                        if proc is not None and proc.poll() is None:
                            try:
                                proc.terminate()
                                proc.wait(timeout=2.0)
                            except Exception:  # noqa: BLE001 - Stop will retry safely
                                LOGGER.debug("Could not stop cancelled Jamulus launch", exc_info=True)
                        self._close_jamulus_log_file()
                        self._active_native_profile = None
                        self._set_live_audio_route_owned(False)
                        with self._reconnect_lock:
                            self.jamulus_reconnect_inflight = False
                        return True

                    # A second click/deep-link can queue another launch while
                    # the first worker is still starting. Re-check only after
                    # acquiring the lifecycle lock so two clients can never be
                    # spawned and one silently lose process ownership.
                    if cancelled():
                        return
                    if (
                        self.jamulus_process is not None
                        and self.jamulus_process.poll() is None
                    ):
                        if force_restart:
                            try:
                                self.jamulus_controller.stop()
                            except Exception as exc:  # noqa: BLE001 - defensive recovery
                                LOGGER.debug("Could not stop old Jamulus controller: %s", exc)
                            try:
                                self.jamulus_process.terminate()
                                try:
                                    self.jamulus_process.wait(timeout=2.0)
                                except subprocess.TimeoutExpired:
                                    self.jamulus_process.kill()
                                    self.jamulus_process.wait(timeout=2.0)
                            except Exception as exc:  # noqa: BLE001
                                raise RuntimeError(
                                    "Could not replace a hung Jamulus process."
                                ) from exc
                            with self._reconnect_lock:
                                self.jamulus_process = None
                        else:
                            self._set_jamulus_state(JamulusState.ALREADY)
                            with self._reconnect_lock:
                                self.jamulus_reconnect_inflight = False
                            self.schedule_ui_callback(self.refresh_readiness)
                            return
                    if cancelled():
                        return

                    native_profile = None
                    if self._native_profile_manager is not None:
                        if reconnect:
                            native_profile = self._active_native_profile
                            if native_profile is None:
                                raise JamulusNativeProfileError(
                                    "WebJam couldn't restore its Jamulus profile. "
                                    "Start the jam again."
                                )
                            self._native_profile_manager.validate_active(native_profile)
                        else:
                            native_profile = self._native_profile_manager.prepare(
                                self.settings,
                                jamulus_path,
                            )
                            self._active_native_profile = native_profile
                    # A live Jamulus client owns the hardware route on every
                    # supported platform. WebJam's optional PortAudio meter
                    # must not contend with the musician's native setup.
                    self._set_live_audio_route_owned(True)

                    if (
                        self._hosting_enabled()
                        and not self.practice_mode
                    ):
                        hosted_ok, hosted_detail = self.ensure_hosted_server()
                        if not hosted_ok:
                            LOGGER.error("Hosted server could not start: %s", hosted_detail)
                            self._active_native_profile = None
                            self._set_live_audio_route_owned(False)
                            self._set_jamulus_state(JamulusState.STOPPED)
                            with self._reconnect_lock:
                                self.jamulus_reconnect_inflight = False
                            self.schedule_ui_callback(
                                lambda: self.show_actionable_error(
                                    "This jam couldn’t start",
                                    what_failed="This Mac couldn’t create the band session.",
                                    likely_cause=(
                                        "Another session may still be open, or a required "
                                        "audio component may be unavailable."
                                    ),
                                    next_action=(
                                        "Close any other WebJam window, wait a moment, "
                                        "then try hosting again."
                                    ),
                                    retry_callback=None,
                                )
                            )
                            self.schedule_ui_callback(self.refresh_readiness)
                            return

                    # Cancellation may have arrived while profile/server
                    # preparation was running.  It must win before a process
                    # is created, even if this worker already owns the
                    # lifecycle lock.
                    if cancelled():
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

                    remote_identity = bool(
                        self._remote_host_mode or self._remote_guest_mode
                    )
                    identity_args = [] if remote_identity else [
                        "--clientname",
                        str(
                            getattr(
                                self.settings,
                                "musician_name",
                                "WebJam Musician",
                            )
                            or "WebJam Musician"
                        ),
                    ]
                    cmd = [
                        jamulus_path,
                        # Show Jamulus normally: it is the authoritative native
                        # sound setup. WebJam never recreates its device UI.
                        *(
                            native_profile.arguments
                            if native_profile is not None
                            else ()
                        ),
                        "--connect", server,
                        # Legacy LAN sessions keep their pre-RPC identity.
                        # V3 applies the real name after authenticated local
                        # RPC connects so it never appears in argv.
                        *identity_args,
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
                    # Qt's offscreen platform is useful for WebJam's automated
                    # widget tests, but the official macOS Jamulus.app ships
                    # only the Cocoa platform plugin. Never leak that
                    # test-only parent setting into the native musician app.
                    # Normal interactive launches leave the environment alone.
                    child_environment = os.environ.copy()
                    if (
                        sys.platform == "darwin"
                        and child_environment.get("QT_QPA_PLATFORM", "").strip().lower()
                        == "offscreen"
                    ):
                        child_environment.pop("QT_QPA_PLATFORM", None)
                    if sys.platform == "darwin":
                        # Jamulus 3.12.2's bundled Qt 6.10.2 can emit a final
                        # default-category qWarning after AppleUnifiedLogger's
                        # static state has already been destroyed, aborting
                        # during an otherwise clean shutdown.  Appending the
                        # narrow last-match rule prevents that late warning
                        # from reaching the dead handler while preserving
                        # inherited category rules and stronger diagnostics.
                        logging_rules = (
                            child_environment.get("QT_LOGGING_RULES", "")
                            .strip()
                            .rstrip(";")
                        )
                        child_environment["QT_LOGGING_RULES"] = (
                            f"{logging_rules};default.warning=false"
                            if logging_rules
                            else "default.warning=false"
                        )
                    popen_kwargs["env"] = child_environment
                    if native_profile is not None:
                        popen_kwargs["cwd"] = str(native_profile.working_directory)
                    if sys.platform == "win32":
                        popen_kwargs["creationflags"] = (
                            getattr(subprocess, "CREATE_NO_WINDOW", 0)
                        )

                    proc = None
                    for i in range(3):
                        if cancelled():
                            return
                        try:
                            proc = subprocess.Popen(cmd, **popen_kwargs)
                            break
                        except Exception:
                            if i == 2:
                                raise
                            time.sleep(0.5)

                    # Popen only proves the executable was found.  Jamulus can
                    # still exit immediately (for example when its sandbox
                    # cannot read the RPC secret).  Do not publish Running or
                    # a success metric until it survives the startup boundary.
                    time.sleep(0.4)
                    return_code = proc.poll() if proc is not None else -1
                    if return_code is not None:
                        raise RuntimeError(
                            "Jamulus exited during startup "
                            f"(code {return_code}); see ~/.webjam_jamulus.log"
                        )

                    if cancelled(proc):
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
                        msg = "Band audio started — connecting everyone now."
                        self.schedule_ui_callback(
                            lambda m=msg: self.set_status_banner(m)
                        )

                except JamulusNativeProfileError as exc:
                    was_practice = self.practice_mode
                    LOGGER.info("Jamulus native-profile preflight failed: %s", exc)
                    self._close_jamulus_log_file()
                    self._set_live_audio_route_owned(False)
                    self._active_native_profile = None
                    self._set_jamulus_state(
                        JamulusState.LAUNCH_FAILED
                        if not reconnect
                        else JamulusState.NOT_RUNNING
                    )
                    with self._reconnect_lock:
                        self.jamulus_reconnect_inflight = False
                    if reconnect:
                        # A reconnect must never rewrite a musician's native
                        # Jamulus setup behind their back.
                        self.jamulus_launch_intended = False
                        self.metrics_service.increment("metric_jamulus_reconnect_failed")
                    else:
                        self.metrics_service.increment("metric_jamulus_launch_failed")
                    if was_practice:
                        self._terminate_practice_server()
                        self.practice_mode = False
                    self.schedule_ui_callback(self.refresh_readiness)
                    self.schedule_ui_callback(
                        lambda message=str(exc): self.show_actionable_error(
                            "Band audio needs attention",
                            what_failed=message,
                            likely_cause=(
                                "Jamulus could not open its native sound profile."
                            ),
                            next_action=(
                                "Open Jamulus Audio Settings, check your interface, "
                                "then try again."
                            ),
                            retry_callback=(
                                None if was_practice else self.retry_audio_launch
                            ),
                        )
                    )
                except Exception as exc:
                    was_practice = self.practice_mode
                    LOGGER.exception("Failed to launch Jamulus: %s", exc)
                    # Close the log file we opened before Popen — Jamulus never
                    # started, nothing's writing to it.
                    self._close_jamulus_log_file()
                    if not reconnect:
                        self._active_native_profile = None
                        self._set_live_audio_route_owned(False)
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
                    LOGGER.debug("Music connection launch detail: %s", exc)
                    self.schedule_ui_callback(
                        lambda: self.show_actionable_error(
                            "Band audio couldn’t start",
                            what_failed="WebJam couldn’t open the music connection.",
                            likely_cause=(
                                "A required component may be blocked, incomplete, or "
                                "still closing from the last session."
                            ),
                            next_action=(
                                "Close this message, then choose Practice Solo "
                                "again."
                                if was_practice
                                else "Wait a moment, then choose Try Again."
                            ),
                            retry_callback=(
                                None if was_practice else self.retry_audio_launch
                            ),
                        )
                    )

        threading.Thread(target=_do_launch, daemon=True).start()
        return True

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
            self.show_actionable_error(
                "Practice Server Failed",
                what_failed="The local practice server could not start.",
                likely_cause="Jamulus path invalid, or the practice port is blocked.",
                next_action="Check the Jamulus path in Settings, then retry.",
                retry_callback=None,
            )
            return False

        self.practice_mode = True
        # Connect the regular client to the local server.
        accepted = self.launch_jamulus(manual=True, reconnect=False)
        if not accepted:
            self._terminate_practice_server()
            self.practice_mode = False
            self.metrics_service.increment("metric_practice_launch_failed")
            return False
        self.metrics_service.increment("metric_practice_mode_started")
        return True

    def _close_practice_log_file(self) -> None:
        """Close the practice-server log file if open. Idempotent."""
        if self._practice_log_file is not None:
            try:
                self._practice_log_file.close()
            except Exception:
                pass
            self._practice_log_file = None

    def _terminate_practice_server(self) -> bool:
        """Terminate the private server and report confirmed cleanup."""
        proc = self.practice_server_process
        stopped = True
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=2.0)
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning("Failed to terminate practice server: %s", exc)
                stopped = False
        if stopped:
            self.practice_server_process = None
            self._close_practice_log_file()
        return stopped

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
        bundled = _bundled_jamulus_server_candidate()
        if bundled:
            return bundled, "bundled"
        candidate = Path(self.JAMULUS_SERVER_BINARY)
        if candidate.is_file():
            return str(candidate), "installed"
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

    def _hosted_secret_is_private(self) -> tuple[bool, str]:
        """Harden and verify the configured recorder credential as mode 0600."""

        secret_path = str(
            getattr(self.settings, "server_rpc_secret_file", "") or ""
        ).strip()
        if not secret_path:
            return False, "the recorder secret is not configured"
        path = Path(secret_path).expanduser()
        try:
            if not path.is_file() or not path.stat().st_size:
                return False, "the recorder secret is missing or empty"
            path.chmod(0o600)
            mode = path.stat().st_mode & 0o777
        except OSError as exc:
            return False, f"the recorder secret could not be secured ({exc})"
        if mode != 0o600:
            return False, f"the recorder secret mode is {mode:04o}, not 0600"
        return True, "recorder secret mode 0600"

    def _wait_for_hosted_ports_release(self, timeout_s: float = 2.0) -> bool:
        """Confirm that both production ports can be rebound after owned stop."""

        rpc_port = int(self.settings.server_rpc_port)
        udp_port = int(self.settings.jamulus_port)
        deadline = time.monotonic() + max(0.0, float(timeout_s))
        while True:
            if self._port_free(rpc_port) and self._port_free(udp_port, udp=True):
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.05)

    def certify_hosted_server_lifecycle(self) -> HostedServerCertification:
        """Exercise the real host-server path without harming external state.

        The normal production launcher remains the authority for the exact
        binary, port preflight, secret creation, process launch, and recorder
        authentication.  Band Check adds the missing proof: a server it starts
        is stopped again and both intended ports are confirmed released.

        An already-running listener is adopted only through the normal
        authenticated recorder probe.  It is never signalled.  Because WebJam
        cannot prove that process's binary version or stop behavior, the result
        is explicitly a warning rather than a full pass.
        """

        with self._hosted_lifecycle_lock:
            was_owned = self.hosted_server_owned()
            was_adopted = self.hosted_server_adopted()
            started_by_check = False
            adopted_by_check = False
            recorder_authenticated = False
            secret_private = False
            owned_stop_confirmed: bool | None = None
            ports_released: bool | None = None
            lifecycle_ok = False
            lifecycle_detail = ""
            technical: list[str] = [
                f"required_version={self.HOSTED_SERVER_VERSION}",
                f"udp_port={int(self.settings.jamulus_port)}",
                f"rpc_port={int(self.settings.server_rpc_port)}",
            ]

            try:
                ok, detail = self.ensure_hosted_server()
                started_by_check = not was_owned and self.hosted_server_owned()
                adopted_by_check = (
                    not was_adopted and self.hosted_server_adopted()
                )
                technical.append(f"production_launcher={detail}")
                if not ok:
                    lifecycle_detail = detail
                else:
                    recorder_authenticated, rpc_detail = (
                        self._probe_hosted_server_rpc()
                    )
                    technical.append(
                        f"recorder_authenticated={recorder_authenticated}"
                    )
                    if not recorder_authenticated:
                        lifecycle_detail = (
                            "The band server started, but its recorder could not "
                            f"be authenticated ({rpc_detail})."
                        )
                    else:
                        secret_private, secret_detail = (
                            self._hosted_secret_is_private()
                        )
                        technical.append(f"recorder_secret={secret_detail}")
                        if not secret_private:
                            lifecycle_detail = (
                                "The recorder authenticated, but its local secret "
                                f"is not private ({secret_detail})."
                            )
                        elif self._port_free(
                            int(self.settings.jamulus_port), udp=True
                        ):
                            lifecycle_detail = (
                                "The recorder authenticated, but the intended "
                                f"Jamulus audio port UDP {self.settings.jamulus_port} "
                                "is not listening."
                            )
                        else:
                            lifecycle_ok = True
            except Exception as exc:  # noqa: BLE001
                LOGGER.exception("Hosted server certification failed")
                lifecycle_detail = f"The band server check failed: {exc}"
            finally:
                # Re-evaluate ownership in case ensure_hosted_server raised
                # after spawning.  A process handle here is WebJam-owned; an
                # arbitrary external listener never becomes this attribute.
                newly_owned = not was_owned and self.hosted_server_owned()
                if newly_owned:
                    started_by_check = True
                    owned_stop_confirmed = self.stop_hosted_server()
                    if owned_stop_confirmed:
                        ports_released = self._wait_for_hosted_ports_release()
                    else:
                        ports_released = False
                # Detach a server adopted only for this proof.  stop_hosted_server
                # sends no signal when there is no owned Popen handle.
                if adopted_by_check and not was_adopted:
                    self.stop_hosted_server()

            if started_by_check:
                technical.extend(
                    (
                        "server_source=production JamulusServer.app",
                        f"version_verified={lifecycle_ok}",
                        f"owned_stop_confirmed={owned_stop_confirmed}",
                        f"ports_released={ports_released}",
                    )
                )
                if owned_stop_confirmed is not True:
                    lifecycle_ok = False
                    lifecycle_detail = (
                        "Band Check started the server but could not confirm that "
                        "its owned process stopped. Close WebJam before retrying."
                    )
                elif ports_released is not True:
                    lifecycle_ok = False
                    lifecycle_detail = (
                        "The server stopped, but its UDP/RPC ports were not "
                        "released. Close WebJam before retrying."
                    )
                elif lifecycle_ok:
                    lifecycle_detail = (
                        "WebJam started JamulusServer 3.12.2 on the intended "
                        "audio and control ports, authenticated its recorder, "
                        "then stopped it cleanly and confirmed both ports were "
                        "released."
                    )

            external = was_adopted or adopted_by_check
            existing_owned = was_owned and not external
            if lifecycle_ok and external:
                lifecycle_detail = (
                    "An externally managed Jamulus server authenticated on the "
                    "expected recorder and audio ports. Band Check did not "
                    "version-check or stop that external server."
                )
            elif lifecycle_ok and existing_owned:
                lifecycle_detail = (
                    "The existing WebJam-owned server authenticated on the "
                    "expected recorder and audio ports. Band Check did not stop "
                    "a server it did not start."
                )

            warning = bool(lifecycle_ok and (external or existing_owned))
            return HostedServerCertification(
                ok=lifecycle_ok,
                warning=warning,
                detail=lifecycle_detail or "The hosted band server could not be verified.",
                technical_details=tuple(technical),
                started_owned_server=started_by_check,
                adopted_external_server=external,
                recorder_authenticated=recorder_authenticated,
                secret_private=secret_private,
                owned_stop_confirmed=owned_stop_confirmed,
                ports_released=ports_released,
            )

    def ensure_hosted_server(
        self,
        *,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> tuple[bool, str]:
        """Start (or adopt) the band server on this Mac. Returns (ok, detail).

        Mirrors server/start_macos_pilot.sh: exact 3.12.2 version gate,
        port preflight, 0600 secret, recordings in the server app's sandbox
        container, and a caffeinate power assertion for the server's
        lifetime so the host Mac cannot sleep mid-session.
        """
        def cancelled() -> bool:
            if cancel_requested is None:
                return False
            try:
                return bool(cancel_requested())
            except Exception:  # noqa: BLE001 - cancellation must fail closed
                return True

        if cancelled():
            return False, "Startup was cancelled."

        with self._hosted_lifecycle_lock:
            if cancelled():
                return False, "Startup was cancelled."
            remote_host_mode = self._remote_host_mode
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
                if remote_host_mode:
                    # Authentication proves recorder ownership, not the UDP
                    # bind address.  V3 therefore cannot adopt an arbitrary
                    # existing server whose loopback-only launch was not owned
                    # by this service.
                    return False, (
                        "A remote jam requires WebJam to start its own "
                        "loopback-only band server. Stop the existing server "
                        "or other WebJam window, then try again."
                    )
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
                binary,
                "--nogui",
                "--port",
                str(udp_port),
            ]
            if remote_host_mode:
                cmd.extend(("--serverbindip", "127.0.0.1"))
            cmd.extend([
                "--recording", str(recordings), "--norecord",
                "--jsonrpcbindip", "127.0.0.1",
                "--jsonrpcport", str(rpc_port),
                "--jsonrpcsecretfile", str(secret_path),
                "--welcomemessage", "WebJam private band server",
            ])
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
            if cancelled():
                return False, "Startup was cancelled."
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

            if cancelled():
                self.stop_hosted_server()
                return False, "Startup was cancelled."

            # Wait for the recorder RPC listener so the client (and the
            # Record button) never race a half-started server.
            deadline = time.monotonic() + 6.0
            while time.monotonic() < deadline:
                if cancelled():
                    self.stop_hosted_server()
                    return False, "Startup was cancelled."
                if not self.hosted_server_owned():
                    self.stop_hosted_server()
                    return False, (
                        "The band server exited immediately — see "
                        "~/Library/Logs/WebJam/jamulus-server.log."
                    )
                verified, _reason = self._probe_hosted_server_rpc()
                if verified:
                    if cancelled():
                        self.stop_hosted_server()
                        return False, "Startup was cancelled."
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

    def stop_hosted_server(self) -> bool:
        """Terminate an owned server and report whether it is confirmed stopped."""
        with self._hosted_lifecycle_lock:
            # Detach from an externally managed server without sending it a
            # signal. Only the subprocess in hosted_server_process is owned.
            self._hosted_server_adopted = False
            proc = self.hosted_server_process
            stopped = True
            if proc is not None and proc.poll() is None:
                try:
                    proc.terminate()
                    try:
                        proc.wait(timeout=3.0)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=2.0)
                except Exception as exc:  # noqa: BLE001
                    LOGGER.warning("Failed to terminate hosted server: %s", exc)
                    stopped = False
            if stopped:
                self.hosted_server_process = None
            caff = self._hosted_caffeinate_process
            if stopped:
                self._hosted_caffeinate_process = None
                if caff is not None and caff.poll() is None:
                    try:
                        caff.terminate()
                    except Exception:  # noqa: BLE001
                        pass
                self._close_hosted_log_file()
            return stopped

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
                color="#BF5700",
            )
        )

        def _restart() -> None:
            try:
                with self._hosted_lifecycle_lock:
                    self.hosted_server_process = None
                ok, detail = self.ensure_hosted_server()
                if not ok:
                    LOGGER.error("Hosted server restart failed: %s", detail)
                    self.schedule_ui_callback(
                        lambda: self.set_status_banner(
                            "The band session couldn’t restart. Close WebJam and open it again.",
                            color="#BF5700",
                        )
                    )
            finally:
                self._hosted_restart_inflight = False

        threading.Thread(
            target=_restart, daemon=True, name="hosted-server-restart",
        ).start()

    def stop_jamulus(self) -> bool:
        """Terminate the Jamulus process, stop monitoring, and clear reconnect state.

        Returns True only when monitoring and the subprocess are confirmed
        stopped (including an already-stopped subprocess). A failed process
        remains owned so the UI cannot claim cleanup succeeded.
        """
        # Signal first so a queued worker that has not acquired the lifecycle
        # lock yet exits before Popen.  Release the control lock before taking
        # the lifecycle lock so an in-flight worker can observe the signal and
        # finish its cleanup without a lock-order cycle.
        with self._jamulus_launch_control_lock:
            self.jamulus_launch_intended = False
            pending_launch = self._pending_jamulus_launch_cancel
            if pending_launch is not None:
                pending_launch.set()

        with self._jamulus_lifecycle_lock:
            # Disable any pending reconnect attempts — user explicitly asked to stop
            self.jamulus_reconnect_attempts = 0
            self.jamulus_next_reconnect_at = 0.0
            with self._reconnect_lock:
                self.jamulus_reconnect_inflight = False

            # Stop transport monitoring so we don't keep polling a dead process.
            monitoring_stopped = True
            try:
                self.jamulus_controller.stop()
            except Exception as exc:
                LOGGER.warning("JamulusController.stop() failed: %s", exc)
                monitoring_stopped = False

            proc = self.jamulus_process
            process_stopped = True
            if proc is not None and proc.poll() is None:
                try:
                    proc.terminate()
                    try:
                        proc.wait(timeout=2.0)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=2.0)
                except Exception as exc:
                    LOGGER.warning("Failed to terminate Jamulus: %s", exc)
                    process_stopped = False

            with self._reconnect_lock:
                if process_stopped:
                    self.jamulus_process = None

            if self.jamulus_process is None:
                self._close_jamulus_log_file()
            practice_stopped = self._terminate_practice_server()
            self.practice_mode = False
            stopped = monitoring_stopped and process_stopped and practice_stopped
            if stopped:
                self._active_native_profile = None
                self._set_live_audio_route_owned(False)
            with self._reconnect_lock:
                self.jamulus_state = (
                    JamulusState.STOPPED.value if stopped else "Stop failed"
                )

            self.metrics_service.increment("metric_jamulus_stop")
            self.schedule_ui_callback(self.refresh_readiness)
            return stopped

    def invalidate_webex_launch(self) -> None:
        """Retire any in-flight external handoff without owning its browser."""

        with self._webex_launch_lock:
            self._webex_launch_generation += 1

    def _begin_webex_launch(self) -> int:
        with self._webex_launch_lock:
            self._webex_launch_generation += 1
            return self._webex_launch_generation

    def _webex_launch_is_current(self, generation: int) -> bool:
        with self._webex_launch_lock:
            return generation == self._webex_launch_generation

    def _publish_webex_state_if_current(
        self,
        generation: int,
        state: WebexLaunchState,
    ) -> bool:
        """Atomically publish state only for the latest external handoff."""

        with self._webex_launch_lock:
            if generation != self._webex_launch_generation:
                return False
            self.webex_state = state.value
            return True

    def _schedule_webex_ui_if_current(
        self,
        generation: int,
        callback: Callable[[], None],
    ) -> None:
        """Drop queued launch UI work if its URL/request was superseded."""

        def _guarded() -> None:
            if self._webex_launch_is_current(generation):
                callback()

        self.schedule_ui_callback(_guarded)

    def launch_webex(self, manual: bool = True, reconnect: bool = False):
        """Open Webex externally and report only the launch result.

        ``reconnect`` remains in the signature for one compatibility cycle but
        is intentionally ignored: WebJam cannot observe an external meeting
        disconnect and therefore must not invent reconnection behavior.
        """
        if self.shutdown_requested():
            return

        launch_generation = self._begin_webex_launch()
        launch_url = str(getattr(self.settings, "webex_url", "") or "").strip()
            
        if manual:
            self.metrics_service.increment("metric_webex_open_attempt")
            
        if not self._publish_webex_state_if_current(
            launch_generation,
            WebexLaunchState.OPENING,
        ):
            return
        self.set_status_banner("Opening Webex externally…", color="#BF5700")
        self._schedule_webex_ui_if_current(
            launch_generation,
            self.refresh_readiness,
        )

        def _do_open() -> None:
            try:
                if self.shutdown_requested():
                    self._publish_webex_state_if_current(
                        launch_generation,
                        WebexLaunchState.NOT_OPENED,
                    )
                    return
                if not self._webex_launch_is_current(launch_generation):
                    return

                if not self.webex_controller.join_meeting():
                    raise RuntimeError(
                        self.webex_controller.last_error or "external launch refused"
                    )

                if self.shutdown_requested():
                    self._publish_webex_state_if_current(
                        launch_generation,
                        WebexLaunchState.NOT_OPENED,
                    )
                    return
                if not self._publish_webex_state_if_current(
                    launch_generation,
                    WebexLaunchState.OPENED_EXTERNALLY,
                ):
                    return

                self.metrics_service.increment("metric_webex_open_success")
                    
                self._schedule_webex_ui_if_current(
                    launch_generation,
                    self.refresh_readiness,
                )
                if manual:
                    self._schedule_webex_ui_if_current(
                        launch_generation,
                        lambda: self.set_status_banner(
                            "Opened externally—finish joining in Webex."
                        ),
                    )
            except Exception as exc:
                if self.shutdown_requested():
                    self._publish_webex_state_if_current(
                        launch_generation,
                        WebexLaunchState.NOT_OPENED,
                    )
                    return
                if not self._publish_webex_state_if_current(
                    launch_generation,
                    WebexLaunchState.OPEN_FAILED,
                ):
                    return
                LOGGER.warning("External Webex launch failed: %s", type(exc).__name__)
                self.metrics_service.increment("metric_webex_open_failed")
                self._schedule_webex_ui_if_current(
                    launch_generation,
                    self.refresh_readiness,
                )
                self._schedule_webex_ui_if_current(
                    launch_generation,
                    lambda: self.show_actionable_error(
                        "Webex Open Failed",
                        what_failed="The configured Webex meeting could not be opened.",
                        likely_cause="Default browser issue, network filtering, invalid meeting URL, or transient launch issue.",
                        next_action=(
                            "Open Settings, verify the Meeting or Personal "
                            "Room link, then try again."
                        ),
                        retry_callback=lambda: self.launch_webex(manual=True),
                        copy_text=launch_url,
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

    def _jamulus_rpc_activity_age(self) -> float | None:
        """Return finite positive RPC age in seconds, or None when unusable."""
        rpc_client = getattr(self.jamulus_controller, "rpc_client", None)
        if rpc_client is None:
            return None
        try:
            age = rpc_client.last_activity_age()
        except Exception:  # noqa: BLE001
            return None
        if not isinstance(age, (int, float)) or age < 0.0:
            return None
        if age == float("inf"):
            return None
        return float(age)

    def _jamulus_process_is_stalled(self) -> bool:
        """Return True when Jamulus is alive but RPC heartbeat appears stuck."""
        proc = self.jamulus_process
        if proc is None or proc.poll() is not None:
            return False
        age = self._jamulus_rpc_activity_age()
        if age is None:
            return False
        return age >= RECONNECT_HANG_THRESHOLD_SECONDS

    def attempt_auto_reconnects(self):
        """Auto-reconnect tick — retries dropped or stalled Jamulus processes.

        Called every ~3 seconds from `ApplicationController._on_reconnect_tick`.
        Per service:

        - **Jamulus**: if `jamulus_launch_intended=True` (user clicked Launch
          Audio at some point and didn't click Stop), and the subprocess has
          died (`poll() is not None`) or is unresponsive for too long, it
          schedules a relaunch with exponential
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
            is_running = (
                self.jamulus_process is not None
                and self.jamulus_process.poll() is None
            )
            is_stalled = self._jamulus_process_is_stalled()

            if is_running and not is_stalled:
                self.jamulus_reconnect_attempts = 0
                self.jamulus_next_reconnect_at = 0.0
                self.jamulus_reconnect_inflight = False
                return

            if self.jamulus_reconnect_inflight:
                return

            if self.jamulus_reconnect_attempts >= RECONNECT_MAX_ATTEMPTS:
                return

            if now < self.jamulus_next_reconnect_at:
                return

            self.jamulus_reconnect_attempts += 1
            self.jamulus_next_reconnect_at = now + self._reconnect_delay_seconds(self.jamulus_reconnect_attempts)
            self.jamulus_reconnect_inflight = True
        self.metrics_service.increment("metric_jamulus_reconnect_attempt")
        self.launch_jamulus(
            manual=False,
            reconnect=True,
            force_restart=is_stalled,
        )
