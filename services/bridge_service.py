import hashlib
import hmac
import logging
import math
import os
import stat
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from core.component_lock import (
    RUNTIME_ACTIVE_LOCK_NAME,
    ComponentLockError,
    ComponentLockTimeout,
    InterProcessComponentLock,
)
from core.component_store import default_component_store_root
from core.jamulus_compatibility import (
    ActivationMode,
    ArtifactKind,
    ComponentTarget,
    JamulusCompatibility,
    JamulusCompatibilityRegistry,
    JamulusRole,
    SourceProvenance,
    official_jamulus_compatibility_registry,
)
from core.jamulus_component_resolver import ValidatedExternalComponent
from core.jamulus_name import validate_jamulus_name
from core.jamulus_profile import (
    JamulusNativeProfileError,
    JamulusNativeProfileManager,
    default_jamulus_version_probe,
)
from core.jamulus_rpc_client import (
    DEFAULT_SECRET_PATH,
    JamulusRpcMonitorSnapshot,
)
from core.meeting_link import (
    GENERIC_MEETING_SERVICE_KEY,
    identify_meeting_service,
    meeting_service_label,
)
from core.private_log import (
    open_private_append_text_log,
    open_private_text_log,
)
from core.secure_runtime import (
    RuntimePathProof,
    SecureRuntimeDirectory,
    SecureRuntimeError,
)
from core.settings import AppSettings
from services.jamulus_component_platform import (
    JamulusPlatformError,
    macos_integrated_runtime_entry_is_eligible,
    platform_component_target,
    sanitized_jamulus_child_environment,
)
from services.macos_process_activation import (
    JamulusForegroundOutcome,
    JamulusForegroundReason,
    activate_running_macos_application_outcome,
)
from webex_integration import WebexLaunchState

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
_EMBEDDED_JAMULUS_VERSION = "3.12.2"
_AUDIO_FEEDBACK_SCAN_TIMEOUT_SECONDS = 0.5
_AUDIO_FEEDBACK_SCAN_LOCK = threading.Lock()


def _meeting_service_name(url: object) -> str:
    service = identify_meeting_service(str(url or ""))
    if service in {None, GENERIC_MEETING_SERVICE_KEY}:
        return ""
    return meeting_service_label(service)


def _bounded_audio_feedback_scan(scanner: Callable[[], object]) -> object | None:
    """Run one advisory CoreAudio scan without ever holding the UI open.

    A misbehaving HAL driver can block a device query. The worker is daemonized
    and globally serialized so a timed-out scan cannot create an unbounded
    collection of additional scans on repeated clicks. Timeout and failure
    both mean unknown; neither is evidence that the route is safe.
    """

    results: list[object | None] = []

    def worker() -> None:
        if not _AUDIO_FEEDBACK_SCAN_LOCK.acquire(blocking=False):
            results.append(None)
            return
        try:
            try:
                results.append(scanner())
            except Exception:  # noqa: BLE001 - advisory evidence stays optional
                results.append(None)
        finally:
            _AUDIO_FEEDBACK_SCAN_LOCK.release()

    thread = threading.Thread(
        target=worker,
        daemon=True,
        name="webjam-audio-feedback-scan",
    )
    thread.start()
    thread.join(_AUDIO_FEEDBACK_SCAN_TIMEOUT_SECONDS)
    return results[0] if results else None


@dataclass(frozen=True, slots=True)
class ResolvedJamulusRuntime:
    """One role-specific, registry-approved Jamulus executable selection."""

    executable_path: Path
    role: JamulusRole
    version: str
    source: str
    catalog_entry: JamulusCompatibility | None = None

    def public_details(self) -> dict[str, str]:
        """Return support-safe component identity without a private path."""

        return {
            "role": self.role.value,
            "version": self.version,
            "source": self.source,
        }


@dataclass(slots=True)
class _OwnedJamulusRuntimePaths:
    """Descriptor-retained proof for paths consumed by one owned child."""

    secret_directory: SecureRuntimeDirectory
    secret_proof: RuntimePathProof
    recordings_directory: SecureRuntimeDirectory | None = None

    def matches(self) -> bool:
        return bool(
            self.secret_directory.path_matches()
            and self.secret_proof.matches()
            and (
                self.recordings_directory is None
                or self.recordings_directory.path_matches()
            )
        )

    def close(self, *, remove_secret: bool) -> bool:
        removed = not remove_secret
        if remove_secret:
            try:
                removed = self.secret_directory.remove_owned_file(
                    self.secret_proof
                )
            except (AttributeError, OSError, SecureRuntimeError):
                # Never fall back to a path-based unlink. A changed leaf must
                # remain untouched even after the owned child has stopped.
                removed = False
            if not removed:
                # Keep the retained directory proof reachable so a later
                # ordered cleanup can retry without reopening an attacker-
                # replaceable pathname. The bounded latch is released by the
                # operating system if WebJam exits.
                return False
        closed = True
        if self.recordings_directory is not None:
            try:
                self.recordings_directory.close()
            except SecureRuntimeError:
                closed = False
        try:
            self.secret_directory.close()
        except SecureRuntimeError:
            closed = False
        return bool(removed and closed)


@dataclass(frozen=True, slots=True)
class _AmbiguousJamulusRuntimeCleanup:
    """Fail-closed latch when preparation failed before a proof was returned."""

    def matches(self) -> bool:
        return False

    def close(self, *, remove_secret: bool) -> bool:
        del remove_secret
        return False


_JamulusRuntimePathState = (
    _OwnedJamulusRuntimePaths | _AmbiguousJamulusRuntimeCleanup
)


class _JamulusRuntimePreparationError(SecureRuntimeError):
    """Path-free preparation failure carrying any retained cleanup owner."""

    def __init__(
        self,
        retained_state: _JamulusRuntimePathState | None,
    ) -> None:
        super().__init__(
            "WebJam could not establish private Jamulus runtime data."
        )
        self.retained_state = retained_state


def _jamulus_child_environment(
    *,
    catalog_verified: bool,
    executable: str | Path | None = None,
) -> dict[str, str]:
    """Build a native child environment without runtime-code injection."""

    del catalog_verified  # retained for call-site/source compatibility
    child_environment = sanitized_jamulus_child_environment(
        os.environ,
        platform_name=sys.platform,
        executable=executable or sys.executable,
    )
    if sys.platform == "darwin":
        # Jamulus 3.12.2's bundled Qt can emit a late default-category warning
        # after its logger has been destroyed. Inherited Qt controls have
        # already been removed; add only this reviewed literal rule.
        child_environment["QT_LOGGING_RULES"] = "default.warning=false"
    return child_environment


def _bundled_jamulus_candidate() -> str | None:
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


def _bundled_reference_track_jamulus_candidate() -> str | None:
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


def _bundled_jamulus_server_candidate() -> str | None:
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


def _bundled_jamulus_installer() -> str | None:
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
    STARTING       = "Starting"
    RUNNING        = "Running"
    LAUNCH_FAILED  = "Launch failed"
    STOPPED        = "Stopped"


PRACTICE_PORT = 22135  # local practice-server port (avoids the 22124 default)
RECONNECT_MAX_ATTEMPTS = 5
RECONNECT_HANG_THRESHOLD_SECONDS = 15.0
RECONNECT_RPC_STARTUP_GRACE_SECONDS = 30.0
NATIVE_SOUND_SETUP_GRACE_SECONDS = 10 * 60.0
# A replacement is not recovered merely because its JSON-RPC socket answers.
# The application must also receive this exact process generation's local
# roster row.  Bound that second proof so a fresh-but-wrong/stuck client cannot
# hold recovery open forever.
RECONNECT_LOCAL_ROSTER_GRACE_SECONDS = 30.0


class JamulusRpcFreshness(str, Enum):
    """Finite client-RPC health used by reconnect and application gating."""

    NO_PROCESS = "no_process"
    STARTING = "starting"
    FRESH = "fresh"
    STALE = "stale"


@dataclass(frozen=True, slots=True)
class JamulusRecoverySnapshot:
    """Immutable, support-safe view of the current primary-client recovery."""

    generation: int
    recovery_generation: int
    launch_intended: bool
    pending: bool
    active: bool
    attempts_started: int
    max_attempts: int
    inflight: bool
    exhausted: bool
    next_attempt_at: float
    process_id: int
    process_alive: bool
    rpc_freshness: JamulusRpcFreshness
    rpc_age_seconds: float | None
    launch_request_generation: int = 0
    rpc_monitor_epoch: int = 0
    native_setup_grace_configured: bool = False
    native_setup_grace_active: bool = False


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
    #   - reconnect attempt/backoff and recovery-generation fields
    #   - published process generation/start-time fields
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
        component_store_root: str | Path | None = None,
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
        # The callback receives only finite action/result labels. It must
        # never receive the configured Webex URL or provider error text.
        self.webex_event = ui_callbacks.get(
            "webex_event",
            lambda _action, _result: None,
        )
        # Production retries must re-enter the controller so connection
        # timers and the optional v2 peer are restored with the client.
        self.retry_audio_launch = ui_callbacks.get(
            "retry_audio_launch",
            lambda: self.launch_jamulus(manual=True),
        )

        # State
        self.jamulus_process: subprocess.Popen | None = None
        self.jamulus_state: str = JamulusState.NOT_LAUNCHED.value
        self._jamulus_foreground_reason = (
            JamulusForegroundReason.NOT_REQUESTED
        )
        self.webex_state = WebexLaunchState.NOT_OPENED.value
        # External Webex handoff is asynchronous. A settings change or a
        # newer Open request invalidates every older worker so its eventual
        # success/failure cannot overwrite the currently configured link.
        self._webex_launch_lock = threading.Lock()
        self._webex_launch_generation = 0
        self._webex_launch_inflight = False
        
        self.jamulus_launch_intended = False
        # A launch is intentionally asynchronous, while Stop/Leave is allowed
        # immediately.  Keep a cancellable request token so a queued worker
        # can never open Jamulus after its originating startup was cancelled.
        # Ordinary Stop is signal-first (control, release, then lifecycle).
        # Workers and identity-bound cleanup already own lifecycle before
        # taking control, then reconnect. No path may hold control while
        # waiting to acquire lifecycle.
        self._jamulus_launch_control_lock = threading.RLock()
        self._pending_jamulus_launch_cancel: threading.Event | None = None
        self._pending_jamulus_launch_identity: (
            tuple[str, str, bool, bool, float] | None
        ) = None
        self._jamulus_launch_request_generation_counter = 0
        self._jamulus_launch_request_generation = 0
        
        self.jamulus_reconnect_attempts = 0
        self.jamulus_next_reconnect_at = 0.0

        self.jamulus_reconnect_inflight = False
        self._reconnect_lock = threading.Lock()
        # One recovery generation spans every bounded replacement attempt
        # caused by the same lost/stalled primary client. A replacement Popen
        # is not success: only an authenticated acknowledgement for the
        # current generation and PID may retire this state.
        self._jamulus_recovery_generation = 0
        self._jamulus_recovery_active = False
        self._jamulus_recovery_exhausted = False
        self._jamulus_process_started_at = 0.0
        self._jamulus_process_generation_counter = 0
        self._jamulus_process_generation = 0
        self._jamulus_process_recovery_generation = 0
        # A first-run native profile may need a human to choose an interface,
        # channels, headphones, and buffer before RPC can authenticate.  This
        # absolute deadline is attached to an exact published process
        # generation; it never turns a stale callback or a different PID into
        # live evidence.
        self._jamulus_native_setup_deadline = 0.0
        self._jamulus_native_setup_process_generation = 0
        # Serialises stop_jamulus() vs launch _do_launch() so a rapid Stop→Launch
        # cannot race the old process's port release.
        self._jamulus_lifecycle_lock = threading.RLock()
        component_root = (
            Path(component_store_root)
            if component_store_root is not None
            else default_component_store_root()
        )
        self._runtime_component_lock_path = (
            component_root / RUNTIME_ACTIVE_LOCK_NAME
        )
        self._runtime_component_lease_guard = threading.RLock()
        self._runtime_component_lease: InterProcessComponentLock | None = None
        self._runtime_component_lease_claims: set[str] = set()
        # Managed component providers are supplied by the updater only while
        # every Jamulus role is idle. Each provider performs its platform
        # signature/content validation again whenever it is called; Bridge
        # then independently probes the runtime version and checks the central
        # compatibility registry before selecting it.
        self._managed_jamulus_client_provider: (
            Callable[[], str | Path | None] | None
        ) = None
        self._managed_jamulus_server_provider: (
            Callable[[], str | Path | None] | None
        ) = None
        self._verified_jamulus_client_provider: (
            Callable[[], ValidatedExternalComponent | None] | None
        ) = None
        self._verified_jamulus_server_provider: (
            Callable[[], ValidatedExternalComponent | None] | None
        ) = None
        self._jamulus_compatibility_registry: JamulusCompatibilityRegistry = (
            official_jamulus_compatibility_registry()
        )
        try:
            self._jamulus_component_target: ComponentTarget | None = (
                platform_component_target()
            )
        except JamulusPlatformError:
            self._jamulus_component_target = None
        self._last_resolved_client_component: ResolvedJamulusRuntime | None = None
        self._last_resolved_server_component: ResolvedJamulusRuntime | None = None
        # A session keeps the exact client it started with through a hung
        # restart or reconnect. A newly activated component is considered only
        # after Stop Audio clears this pin.
        self._active_client_component: ResolvedJamulusRuntime | None = None
        self._active_server_component: ResolvedJamulusRuntime | None = None
        # The dedicated profile belongs to Jamulus, not WebJam's CoreAudio
        # layer.  We only provide the supported filename-only --inifile launch
        # contract; Jamulus writes its own device/channel/buffer choices.
        self._active_native_profile = None
        self._native_profile_manager = (
            JamulusNativeProfileManager()
            if sys.platform == "darwin" and isinstance(settings, AppSettings)
            else None
        )
        # Production macOS launches retain no-follow directory descriptors and
        # inode proofs for every private path passed to an owned native child.
        # Compatible mocks used by portable unit tests keep the legacy branch;
        # the desktop application always supplies AppSettings.
        self._secure_macos_runtime_enabled = bool(
            sys.platform == "darwin" and isinstance(settings, AppSettings)
        )
        self._client_runtime_paths: _JamulusRuntimePathState | None = None

        # Practice mode: a private Jamulus server on this machine so a
        # musician can validate audio routing and hear themselves with zero
        # internet dependency. `practice_server_process` is the local
        # `Jamulus --server --nogui` subprocess; `practice_mode` makes
        # launch/reconnect target 127.0.0.1 instead of the band server.
        self.practice_mode = False
        self.practice_server_process: subprocess.Popen | None = None

        # File handle for capturing Jamulus stdout+stderr — closed in stop_jamulus.
        # Captures to ~/.webjam_jamulus.log, overwritten on each launch so the
        # user can inspect the CURRENT session's Jamulus output when troubleshooting.
        self._jamulus_log_file: object | None = None
        self._practice_log_file: object | None = None

        # Hosted band server: when settings.host_server_enabled, WebJam
        # supervises the official JamulusServer.app (recording + loopback
        # RPC) instead of the manual server/start_macos_pilot.sh Terminal
        # step. Its lifecycle is deliberately decoupled from the client:
        # Stop Audio never stops the band's server.
        self.hosted_server_process: subprocess.Popen | None = None
        # True only when WebJam authenticated an already-running external
        # JamulusServer through the configured recorder secret. Adopted
        # servers are observed, never terminated by WebJam.
        self._hosted_server_adopted = False
        self._hosted_caffeinate_process: subprocess.Popen | None = None
        self._hosted_log_file: object | None = None
        self._hosted_lifecycle_lock = threading.RLock()
        self._hosted_restart_control_lock = threading.Lock()
        self._hosted_restart_inflight = False
        self._pending_hosted_restart_cancel: threading.Event | None = None
        self._hosted_runtime_paths: _JamulusRuntimePathState | None = None
        # Remote v3 hosting is an ephemeral launch constraint, never a saved
        # setting.  Legacy v1/v2 hosts intentionally keep JamulusServer's LAN
        # binding; a v3 owner must opt in before this service starts a server.
        self._remote_host_mode = False
        # A v3 guest also needs a process-local marker so its musician name is
        # applied only through authenticated loopback RPC, never exposed in
        # process arguments. This is independent from saved settings.
        self._remote_guest_mode = False

    def __del__(self) -> None:
        """Best-effort descriptor cleanup for an entirely idle Bridge.

        Normal application shutdown releases role claims only after confirmed
        process cleanup.  A finalizer must never make component replacement
        possible underneath an owned process or reconnect lifecycle, so every
        ambiguous condition below fails closed and leaves the operating system
        to close the descriptor when the WebJam process exits.
        """

        if self._runtime_component_lifecycle_is_active():
            return
        try:
            self._release_client_runtime_paths(confirmed_stopped=True)
            self._release_hosted_runtime_paths(confirmed_stopped=True)
        except Exception:
            pass
        lease = getattr(self, "_runtime_component_lease", None)
        if lease is None:
            return
        self._runtime_component_lease = None
        try:
            lease.__exit__(None, None, None)
        except Exception:
            pass

    def _macos_runtime_home(self) -> Path:
        """Return the sole trusted root for private native-child paths."""

        return Path.home()

    def _prepare_owned_runtime_paths(
        self,
        *,
        secret_path: Path,
        secret_payload: bytes,
        recordings_path: Path | None = None,
    ) -> _OwnedJamulusRuntimePaths:
        """Create and prove private paths without exposing their names."""

        secret_directory: SecureRuntimeDirectory | None = None
        recordings_directory: SecureRuntimeDirectory | None = None
        secret_proof: RuntimePathProof | None = None
        paths: _OwnedJamulusRuntimePaths | None = None
        secret_write_started = False
        try:
            home = self._macos_runtime_home()
            secret_directory = SecureRuntimeDirectory.open(
                home=home,
                directory=secret_path.parent,
                mode=0o700,
            )
            secret_write_started = True
            secret_proof = secret_directory.write_private_file(
                secret_path.name,
                secret_payload,
                mode=0o600,
            )
            paths = _OwnedJamulusRuntimePaths(
                secret_directory=secret_directory,
                secret_proof=secret_proof,
            )
            if recordings_path is not None:
                recordings_directory = SecureRuntimeDirectory.open(
                    home=home,
                    directory=recordings_path,
                    mode=0o700,
                )
                paths.recordings_directory = recordings_directory
            if not paths.matches():
                raise SecureRuntimeError(
                    "WebJam refused changed private Jamulus runtime data."
                )
            return paths
        except (OSError, SecureRuntimeError):
            retained_state: _JamulusRuntimePathState | None = None
            if paths is not None:
                try:
                    if not paths.close(remove_secret=True):
                        retained_state = paths
                except (OSError, SecureRuntimeError):
                    retained_state = paths
            else:
                cleanup_confirmed = not secret_write_started
                if recordings_directory is not None:
                    try:
                        recordings_directory.close()
                    except SecureRuntimeError:
                        cleanup_confirmed = False
                if secret_directory is not None:
                    try:
                        secret_directory.close()
                    except SecureRuntimeError:
                        cleanup_confirmed = False
                if not cleanup_confirmed:
                    retained_state = _AmbiguousJamulusRuntimeCleanup()
            raise _JamulusRuntimePreparationError(retained_state) from None

    @staticmethod
    def _runtime_paths_match(
        paths: _JamulusRuntimePathState | None,
    ) -> bool:
        try:
            return bool(paths is not None and paths.matches())
        except (OSError, SecureRuntimeError):
            return False

    def _release_client_runtime_paths(self, *, confirmed_stopped: bool) -> bool:
        if not confirmed_stopped:
            return False
        paths = self._client_runtime_paths
        if paths is None:
            return True
        cleaned = paths.close(remove_secret=True)
        if cleaned:
            self._client_runtime_paths = None
        else:
            LOGGER.warning(
                "WebJam retained failed primary credential cleanup state; "
                "another launch is blocked until restart."
            )
        return cleaned

    def _release_hosted_runtime_paths(self, *, confirmed_stopped: bool) -> bool:
        if not confirmed_stopped:
            return False
        paths = self._hosted_runtime_paths
        if paths is None:
            return True
        cleaned = paths.close(remove_secret=True)
        if cleaned:
            self._hosted_runtime_paths = None
        else:
            LOGGER.warning(
                "WebJam retained failed server credential cleanup state; "
                "another launch is blocked until restart."
            )
        return cleaned

    def _validate_primary_launch_paths(
        self,
        native_profile: object | None,
    ) -> object | None:
        """Revalidate all path contracts immediately around primary Popen."""

        if (
            native_profile is not None
            and self._native_profile_manager is not None
        ):
            refreshed = self._native_profile_manager.validate_active(
                native_profile
            )
            # Legacy test doubles returned None before validate_active began
            # refreshing immutable fingerprints. Production managers return
            # the refreshed plan.
            if refreshed is not None:
                native_profile = refreshed
            self._active_native_profile = native_profile
        if (
            self._secure_macos_runtime_enabled
            and not self._runtime_paths_match(self._client_runtime_paths)
        ):
            raise SecureRuntimeError(
                "WebJam refused changed private Jamulus runtime data."
            )
        return native_profile

    def _validate_hosted_launch_paths(self) -> None:
        """Revalidate the server credential and recording directory."""

        if (
            self._secure_macos_runtime_enabled
            and not self._runtime_paths_match(self._hosted_runtime_paths)
        ):
            raise SecureRuntimeError(
                "WebJam refused changed private Jamulus runtime data."
            )

    def _runtime_component_lifecycle_is_active(self) -> bool:
        """Return ``True`` unless every lease-owning lifecycle is idle.

        This helper is intentionally conservative because it is also the
        safety boundary for finalizer cleanup.  Poll failures and partially
        initialized state are considered active.
        """

        for attribute in (
            "jamulus_process",
            "practice_server_process",
            "hosted_server_process",
        ):
            process = getattr(self, attribute, None)
            if process is None:
                continue
            try:
                if process.poll() is None:
                    return True
            except Exception:
                return True
        for attribute in (
            "jamulus_launch_intended",
            "jamulus_reconnect_inflight",
            "_hosted_restart_inflight",
            "_hosted_server_adopted",
            "practice_mode",
        ):
            try:
                if getattr(self, attribute, False) is True:
                    return True
            except Exception:
                return True
        if getattr(self, "_pending_jamulus_launch_cancel", None) is not None:
            return True
        if getattr(self, "_active_client_component", None) is not None:
            return True
        return getattr(self, "_active_server_component", None) is not None

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
            LOGGER.debug(
                "Could not update local-meter route ownership (%s).",
                type(exc).__name__,
            )

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

    def _acquire_runtime_component_lease(
        self,
        claim: str,
    ) -> tuple[bool, str]:
        """Claim the shared updater/runtime lock without blocking the UI."""

        if claim not in {"client", "practice", "server"}:
            raise ValueError("runtime component lease claim is invalid")
        with self._runtime_component_lease_guard:
            if claim in self._runtime_component_lease_claims:
                return True, "already held"
            lease = self._runtime_component_lease
            if lease is None:
                lease = InterProcessComponentLock(
                    self._runtime_component_lock_path,
                    timeout=0.0,
                )
                try:
                    lease.__enter__()
                except ComponentLockTimeout:
                    return False, (
                        "Another WebJam window or a Jamulus update is using "
                        "the audio components. Finish it, then try again."
                    )
                except ComponentLockError:
                    return False, (
                        "WebJam could not reserve its audio components safely. "
                        "Close other WebJam windows, then try again."
                    )
                self._runtime_component_lease = lease
            self._runtime_component_lease_claims.add(claim)
            return True, "reserved"

    def _release_runtime_component_lease(self, claim: str) -> None:
        """Release one role and unlock only after every owned role is clean."""

        if claim not in {"client", "practice", "server"}:
            raise ValueError("runtime component lease claim is invalid")
        with self._runtime_component_lease_guard:
            self._runtime_component_lease_claims.discard(claim)
            if self._runtime_component_lease_claims:
                return
            lease = self._runtime_component_lease
            self._runtime_component_lease = None
            if lease is None:
                return
            try:
                lease.__exit__(None, None, None)
            except OSError:
                LOGGER.warning(
                    "The Jamulus runtime lease could not be released cleanly."
                )

    def _release_unestablished_client_lease(self) -> None:
        process_alive = (
            self.jamulus_process is not None
            and self.jamulus_process.poll() is None
        )
        if not process_alive and self._active_client_component is None:
            self._release_runtime_component_lease("client")

    def _release_unestablished_server_lease(self) -> None:
        if not self.hosted_server_owned() and self._active_server_component is None:
            self._release_runtime_component_lease("server")

    def _retire_jamulus_launch_request(
        self,
        launch_cancel: threading.Event,
    ) -> None:
        """Clear one completed preflight/worker without touching a newer one."""

        with self._jamulus_launch_control_lock:
            if self._pending_jamulus_launch_cancel is launch_cancel:
                self._pending_jamulus_launch_cancel = None
                self._pending_jamulus_launch_identity = None

    def _schedule_jamulus_launch_ui_if_current(
        self,
        launch_request_generation: int,
        callback: Callable[[], None],
    ) -> None:
        """Deliver launch UI only while its monotonic request still owns it."""

        def guarded() -> None:
            with self._jamulus_launch_control_lock:
                still_current = (
                    self._jamulus_launch_request_generation
                    == launch_request_generation
                )
            if still_current:
                callback()

        self.schedule_ui_callback(guarded)

    def _invalidate_jamulus_launch_callbacks_locked(self) -> None:
        """Tombstone queued launch UI while the caller holds launch control."""

        self._jamulus_launch_request_generation_counter = max(
            self._jamulus_launch_request_generation_counter,
            self._jamulus_launch_request_generation,
        ) + 1
        self._jamulus_launch_request_generation = (
            self._jamulus_launch_request_generation_counter
        )

    def _clear_native_setup_grace_for_request(
        self,
        launch_cancel: threading.Event,
        deadline: float,
    ) -> None:
        """Clear only this request's optimistic first-run setup ownership."""

        if deadline <= 0.0:
            return
        with self._jamulus_launch_control_lock:
            self._clear_native_setup_grace_for_request_locked(
                launch_cancel,
                deadline,
            )

    def _clear_native_setup_grace_for_request_locked(
        self,
        launch_cancel: threading.Event,
        deadline: float,
    ) -> None:
        """Clear request grace while the caller holds launch control."""

        if deadline <= 0.0:
            return
        if self._pending_jamulus_launch_cancel is not launch_cancel:
            return
        with self._reconnect_lock:
            if (
                self._jamulus_native_setup_deadline == deadline
                and self._jamulus_native_setup_process_generation == 0
            ):
                self._jamulus_native_setup_deadline = 0.0

    @property
    def runtime_component_lease_active(self) -> bool:
        """Whether this process currently excludes component replacement."""

        with self._runtime_component_lease_guard:
            return self._runtime_component_lease is not None

    def set_managed_jamulus_paths(
        self,
        client_provider: Callable[[], str | Path | None] | None,
        server_provider: Callable[[], str | Path | None] | None,
    ) -> None:
        """Attach legacy role-specific managed-path providers.

        This compatibility API retains the baked-registry version boundary.
        New updater integrations should use
        :meth:`set_managed_jamulus_components`, which carries the signed
        catalog identity and all verification facts with the executable.
        """

        if client_provider is not None and not callable(client_provider):
            raise TypeError("client_provider must be callable or None")
        if server_provider is not None and not callable(server_provider):
            raise TypeError("server_provider must be callable or None")
        with self._jamulus_lifecycle_lock, self._hosted_lifecycle_lock:
            if (
                self._runtime_component_lifecycle_is_active()
                or self.runtime_component_lease_active
            ):
                raise RuntimeError(
                    "managed Jamulus providers can change only while audio "
                    "is stopped"
                )
            self._managed_jamulus_client_provider = client_provider
            self._managed_jamulus_server_provider = server_provider
            self._last_resolved_client_component = None
            self._last_resolved_server_component = None

    def set_managed_jamulus_components(
        self,
        client_provider: (
            Callable[[], ValidatedExternalComponent | None] | None
        ),
        server_provider: (
            Callable[[], ValidatedExternalComponent | None] | None
        ),
    ) -> None:
        """Attach signed-catalog-backed, fully verified component providers.

        The provider is invoked on every resolution/revalidation. Bridge does
        not require the entry to exist in its baked fallback registry; it
        independently checks the supplied immutable identity's role, target,
        WebJam range, capabilities, executable, and live-reported version.
        """

        if client_provider is not None and not callable(client_provider):
            raise TypeError("client_provider must be callable or None")
        if server_provider is not None and not callable(server_provider):
            raise TypeError("server_provider must be callable or None")
        with self._jamulus_lifecycle_lock, self._hosted_lifecycle_lock:
            if (
                self._runtime_component_lifecycle_is_active()
                or self.runtime_component_lease_active
            ):
                raise RuntimeError(
                    "managed Jamulus providers can change only while audio "
                    "is stopped"
                )
            self._verified_jamulus_client_provider = client_provider
            self._verified_jamulus_server_provider = server_provider
            self._last_resolved_client_component = None
            self._last_resolved_server_component = None

    def _runtime_webjam_version(self) -> str:
        try:
            from webjam_qt import __version__

            return str(__version__)
        except Exception:  # noqa: BLE001 - compatibility must fail closed
            return "unverified"

    @staticmethod
    def _required_component_capabilities(role: JamulusRole) -> frozenset[str]:
        if role is JamulusRole.CLIENT:
            return frozenset(
                {
                    "audio-client",
                    "json-rpc-client",
                    "native-gui",
                    "webjam-route-profile",
                }
            )
        if role is JamulusRole.SERVER:
            return frozenset(
                {"audio-server", "json-rpc-server", "recording"}
            )
        return frozenset()

    def _required_embedded_component_capabilities(
        self,
        role: JamulusRole,
    ) -> frozenset[str]:
        """Return the policy for WebJam's already-integrated app-bundle copy.

        Upstream macOS DMGs are sandboxed source evidence and deliberately no
        longer claim WebJam-owned profile/recording integration.  The bundled
        3.12.2 copy was normalized and signed inside WebJam by release CI, so
        it remains the only macOS fallback permitted to satisfy the native
        client/server protocol capabilities until the dedicated integrated
        updater contract is implemented.
        """

        if self._jamulus_component_target in {
            ComponentTarget.MACOS_ARM64,
            ComponentTarget.MACOS_X64,
        }:
            if role is JamulusRole.CLIENT:
                return frozenset(
                    {"audio-client", "json-rpc-client", "native-gui"}
                )
            if role is JamulusRole.SERVER:
                return frozenset({"audio-server", "json-rpc-server"})
            return frozenset()
        return self._required_component_capabilities(role)

    def _approved_runtime_versions(self, role: JamulusRole) -> frozenset[str]:
        target = self._jamulus_component_target
        if target is None:
            return frozenset()
        if target in {
            ComponentTarget.MACOS_ARM64,
            ComponentTarget.MACOS_X64,
        }:
            # An upstream official Mac bundle is source evidence only.  Never
            # turn it into an executable WebJam runtime merely because a
            # baked, cached, or future catalog claims integration capabilities.
            # The separately verified bundled path has its own narrower policy
            # in `_approved_embedded_runtime_versions`.
            return frozenset()
        try:
            entries = self._jamulus_compatibility_registry.compatible(
                role=role,
                target=target,
                webjam_version=self._runtime_webjam_version(),
                required_capabilities=self._required_component_capabilities(role),
            )
        except Exception:  # noqa: BLE001 - registry failures reject the component
            return frozenset()
        return frozenset(
            entry.version
            for entry in entries
            if entry.component_id == "jamulus" and entry.variant == "official"
        )

    def _approved_embedded_runtime_versions(
        self,
        role: JamulusRole,
    ) -> frozenset[str]:
        """Return versions release CI may have integrated into WebJam itself."""

        target = self._jamulus_component_target
        if target is None:
            return frozenset()
        try:
            entries = self._jamulus_compatibility_registry.compatible(
                role=role,
                target=target,
                webjam_version=self._runtime_webjam_version(),
                required_capabilities=(
                    self._required_embedded_component_capabilities(role)
                ),
            )
        except Exception:  # noqa: BLE001 - registry failures reject the component
            return frozenset()
        return frozenset(
            entry.version
            for entry in entries
            if entry.component_id == "jamulus" and entry.variant == "official"
        )

    def _approved_versions_for_resolved_component(
        self,
        component: ResolvedJamulusRuntime,
    ) -> frozenset[str]:
        """Include one reverified signed-catalog version in launch policy."""

        versions = set(
            self._approved_embedded_runtime_versions(component.role)
            if component.source == "bundled"
            else self._approved_runtime_versions(component.role)
        )
        if component.catalog_entry is not None:
            versions.add(component.version)
        return frozenset(versions)

    def _runtime_component(
        self,
        path: str | Path,
        *,
        role: JamulusRole,
        source: str,
        managed: bool = False,
    ) -> ResolvedJamulusRuntime | None:
        """Validate one candidate against filesystem and registry truth."""

        try:
            candidate = Path(path).expanduser()
            if not candidate.is_file():
                return None
            if managed and candidate.is_symlink():
                return None
            resolved = candidate.resolve(strict=True)
            if not resolved.is_file():
                return None
            if sys.platform != "win32" and not os.access(resolved, os.X_OK):
                return None
        except (OSError, RuntimeError, TypeError, ValueError):
            return None
        version = default_jamulus_version_probe(str(resolved))
        if version not in self._approved_runtime_versions(role):
            return None
        return ResolvedJamulusRuntime(
            executable_path=resolved,
            role=role,
            version=version,
            source=source,
        )

    def _embedded_runtime_component(
        self,
        path: str | Path,
        *,
        role: JamulusRole,
    ) -> ResolvedJamulusRuntime | None:
        """Apply registry policy after a bundled-path helper verified presence."""

        if (
            _EMBEDDED_JAMULUS_VERSION
            not in self._approved_embedded_runtime_versions(role)
        ):
            return None
        try:
            candidate = Path(path).expanduser()
        except (TypeError, ValueError):
            return None
        return ResolvedJamulusRuntime(
            executable_path=candidate,
            role=role,
            version=_EMBEDDED_JAMULUS_VERSION,
            source="bundled",
        )

    def _managed_runtime_component(
        self,
        *,
        role: JamulusRole,
    ) -> ResolvedJamulusRuntime | None:
        verified_provider = (
            self._verified_jamulus_client_provider
            if role is JamulusRole.CLIENT
            else self._verified_jamulus_server_provider
        )
        if verified_provider is not None:
            return self._verified_managed_runtime_component(
                role=role,
                provider=verified_provider,
            )
        provider = (
            self._managed_jamulus_client_provider
            if role is JamulusRole.CLIENT
            else self._managed_jamulus_server_provider
        )
        if provider is None:
            return None
        try:
            path = provider()
        except Exception:  # noqa: BLE001 - external store validation fails closed
            return None
        if path is None:
            return None
        return self._runtime_component(
            path,
            role=role,
            source="managed",
            managed=True,
        )

    def _verified_managed_runtime_component(
        self,
        *,
        role: JamulusRole,
        provider: Callable[[], ValidatedExternalComponent | None],
    ) -> ResolvedJamulusRuntime | None:
        """Validate a signed-catalog identity without consulting baked pins."""

        try:
            validated = provider()
        except Exception:  # noqa: BLE001 - provider failures fall back safely
            return None
        if (
            not isinstance(validated, ValidatedExternalComponent)
            or not self._validated_component_trust_is_approved(validated)
        ):
            return None
        entry = validated.entry
        target = self._jamulus_component_target
        is_macos = (
            target
            in {
                ComponentTarget.MACOS_ARM64,
                ComponentTarget.MACOS_X64,
            }
        )
        if (
            target is None
            or entry.component_id != "jamulus"
            or entry.role is not role
            or entry.target is not target
            or not entry.capabilities.includes(
                self._required_component_capabilities(role)
            )
        ):
            return None
        if is_macos:
            if (
                not validated.execution_contract_verified
                or not macos_integrated_runtime_entry_is_eligible(entry)
            ):
                return None
        elif (
            entry.variant != "official"
            or entry.activation_mode
            not in {
                ActivationMode.MANAGED,
                ActivationMode.PLATFORM_APPROVAL,
            }
        ):
            return None
        try:
            if not entry.supports_webjam(self._runtime_webjam_version()):
                return None
            candidate = Path(validated.executable_path).expanduser()
            before = candidate.lstat()
            if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
                return None
            if os.name == "posix" and not before.st_mode & 0o111:
                return None
            resolved = candidate.resolve(strict=True)
            if not resolved.is_file():
                return None
            after = resolved.stat()
            before_identity = (
                before.st_dev,
                before.st_ino,
                before.st_mode,
                before.st_size,
                before.st_mtime_ns,
            )
            after_identity = (
                after.st_dev,
                after.st_ino,
                after.st_mode,
                after.st_size,
                after.st_mtime_ns,
            )
            if before_identity != after_identity:
                return None
        except (OSError, RuntimeError, TypeError, ValueError):
            return None
        return ResolvedJamulusRuntime(
            executable_path=resolved,
            role=role,
            version=entry.version,
            source="managed",
            catalog_entry=entry,
        )

    @staticmethod
    def _validated_component_trust_is_approved(
        validated: ValidatedExternalComponent,
    ) -> bool:
        """Apply truthful publisher/package policy to exact runtime proof."""

        if not (
            validated.content_verified
            and validated.version_verified
            and validated.architecture_verified
        ):
            return False
        if validated.publisher_verified:
            return True
        entry = validated.entry
        if (
            entry.target
            in {
                ComponentTarget.MACOS_ARM64,
                ComponentTarget.MACOS_X64,
            }
            and validated.execution_contract_verified
            and macos_integrated_runtime_entry_is_eligible(entry)
        ):
            return validated.trust_policy_verified
        if (
            entry.source.provenance is not SourceProvenance.OFFICIAL_RELEASE
            or entry.activation_mode is not ActivationMode.PLATFORM_APPROVAL
        ):
            return False
        if entry.target is ComponentTarget.WINDOWS_X64:
            return (
                validated.trust_policy_verified
                and entry.publisher
                == "Unsigned upstream installer; exact WebJam-approved SHA-256"
                and entry.artifact.kind is ArtifactKind.INSTALLER
            )
        if entry.target is ComponentTarget.LINUX_X64:
            return (
                validated.trust_policy_verified
                and entry.publisher == "Debian package jamulus"
                and entry.artifact.kind is ArtifactKind.PACKAGE
            )
        return False

    def _revalidate_runtime_component(
        self,
        component: ResolvedJamulusRuntime,
    ) -> ResolvedJamulusRuntime | None:
        if component.source == "managed":
            current = self._managed_runtime_component(role=component.role)
        elif component.source == "bundled":
            bundled = (
                _bundled_jamulus_candidate()
                if component.role is JamulusRole.CLIENT
                else _bundled_jamulus_server_candidate()
            )
            if bundled is None:
                return None
            current = self._embedded_runtime_component(
                bundled,
                role=component.role,
            )
            if (
                current is not None
                and default_jamulus_version_probe(
                    str(current.executable_path)
                )
                != current.version
            ):
                return None
        else:
            current = self._runtime_component(
                component.executable_path,
                role=component.role,
                source=component.source,
            )
        if (
            current is None
            or current.executable_path != component.executable_path
            or current.version != component.version
            or current.catalog_entry != component.catalog_entry
        ):
            return None
        return current

    @property
    def active_jamulus_component(self) -> dict[str, str] | None:
        """Privacy-safe identity of the session-pinned client component."""

        component = self._active_client_component
        return None if component is None else component.public_details()

    @property
    def resolved_jamulus_server_component(self) -> dict[str, str] | None:
        component = self._last_resolved_server_component
        return None if component is None else component.public_details()

    def find_jamulus(self) -> str | None:
        """Resolve client as managed, embedded, explicit, then system.

        Every non-embedded candidate must report a role-compatible version
        present in the central registry. A reconnect or forced recovery keeps
        the component pinned by the original session and fails closed if that
        exact selection no longer revalidates.
        """

        self._last_resolved_client_component = None
        active = self._active_client_component
        process_alive = (
            self.jamulus_process is not None
            and self.jamulus_process.poll() is None
        )
        if active is not None and (process_alive or self.jamulus_launch_intended):
            current = self._revalidate_runtime_component(active)
            if current is None:
                return None
            self._last_resolved_client_component = current
            return str(current.executable_path)

        managed = self._managed_runtime_component(role=JamulusRole.CLIENT)
        if managed is not None:
            self._last_resolved_client_component = managed
            return str(managed.executable_path)

        bundled = _bundled_jamulus_candidate()
        if bundled:
            component = self._embedded_runtime_component(
                bundled,
                role=JamulusRole.CLIENT,
            )
            if component is not None:
                self._last_resolved_client_component = component
                return str(component.executable_path)

        checked: set[str] = set()
        from core.settings import AppSettings as DefaultAppSettings

        groups = (
            ("explicit", self.settings.jamulus_candidates),
            ("system", DefaultAppSettings().jamulus_candidates),
        )
        for source, paths in groups:
            for raw_path in paths:
                path = str(raw_path)
                if path in checked:
                    continue
                checked.add(path)
                component = self._runtime_component(
                    path,
                    role=JamulusRole.CLIENT,
                    source=source,
                )
                if component is not None:
                    self._last_resolved_client_component = component
                    return str(component.executable_path)
        return None

    def find_reference_track_jamulus(self) -> str | None:
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

    def prelaunch_audio_feedback_assessment(self):
        """Return an advisory, name-free assessment of the saved Mac route.

        Jamulus remains the device owner. This read-only check warns only when
        its WebJam-owned profile clearly selects a built-in microphone and
        built-in speakers. Unsupported platforms and uncertain evidence stay
        unknown and never become a false safety claim.
        """

        from core.audio_feedback_guard import (
            AudioFeedbackAssessment,
            assess_audio_feedback_risk,
        )

        if sys.platform != "darwin" or self._native_profile_manager is None:
            return AudioFeedbackAssessment()
        try:
            from core.coreaudio_devices import (
                CoreAudioDirection,
                CoreAudioScan,
                scan_coreaudio_devices,
            )
            from core.jamulus_profile import read_native_audio_device_selector

            active = self._active_native_profile
            component = getattr(self, "_last_resolved_client_component", None)
            version = str(
                getattr(active, "jamulus_version", "")
                or getattr(component, "version", "")
                or _EMBEDDED_JAMULUS_VERSION
            )
            plan = self._native_profile_manager.plan(jamulus_version=version)
            selector = (
                read_native_audio_device_selector(plan)
                if plan.profile_exists
                else None
            )
            if selector is None or selector.uses_system_defaults:
                scan = _bounded_audio_feedback_scan(scan_coreaudio_devices)
                if not isinstance(scan, CoreAudioScan) or not scan.available:
                    return AudioFeedbackAssessment()

                def default_name(direction: CoreAudioDirection) -> str:
                    uid = scan.default_uid(direction)
                    matches = [
                        device
                        for device in scan.devices
                        if uid
                        and device.uid == uid
                        and device.supports(direction)
                    ]
                    return matches[0].name if len(matches) == 1 else ""

                input_name = default_name(CoreAudioDirection.INPUT)
                output_name = default_name(CoreAudioDirection.OUTPUT)
            else:
                input_name = selector.input_name
                output_name = selector.output_name
            return assess_audio_feedback_risk(input_name, output_name)
        except Exception as exc:  # noqa: BLE001 - advisory evidence is optional
            LOGGER.debug(
                "Prelaunch audio feedback assessment was unavailable (%s).",
                type(exc).__name__,
            )
            return AudioFeedbackAssessment()

    @property
    def jamulus_foreground_reason_code(self) -> str:
        """Return the last bounded foreground result without process identity."""

        with self._reconnect_lock:
            reason = getattr(
                self,
                "_jamulus_foreground_reason",
                JamulusForegroundReason.NOT_REQUESTED,
            )
        try:
            return JamulusForegroundReason(reason).value
        except (TypeError, ValueError):
            return JamulusForegroundReason.NOT_REQUESTED.value

    def _remember_jamulus_foreground_outcome(
        self,
        outcome: JamulusForegroundOutcome,
    ) -> JamulusForegroundOutcome:
        if not isinstance(outcome, JamulusForegroundOutcome):
            outcome = JamulusForegroundOutcome(
                False,
                JamulusForegroundReason.NATIVE_ACTIVATION_UNAVAILABLE,
            )
        with self._reconnect_lock:
            self._jamulus_foreground_reason = outcome.reason
        return outcome

    def bring_jamulus_forward_outcome(self) -> JamulusForegroundOutcome:
        """Activate the exact owned Jamulus child without launching another.

        Multiple installed WebJam builds contain Jamulus bundles with the same
        bundle identifier.  Bundle-ID AppleScript activation can therefore
        select a different copy.  Bind AppKit activation to this Popen's exact
        PID and the pinned runtime bundle instead.  The musician still chooses
        Audio/Network Settings inside the real Jamulus window.
        """

        with self._reconnect_lock:
            proc = self.jamulus_process
            process_generation = self._jamulus_process_generation
            process_identifier = self._jamulus_process_id(proc)
            component = self._active_client_component
            process_running = self._jamulus_process_poll_evidence(proc)
        if proc is None or process_running is False:
            return self._remember_jamulus_foreground_outcome(
                JamulusForegroundOutcome(
                    False,
                    JamulusForegroundReason.NOT_RUNNING,
                )
            )
        if (
            process_running is not True
            or process_generation <= 0
            or process_identifier <= 0
        ):
            return self._remember_jamulus_foreground_outcome(
                JamulusForegroundOutcome(
                    False,
                    JamulusForegroundReason.IDENTITY_UNVERIFIED,
                )
            )
        if sys.platform != "darwin":
            return self._remember_jamulus_foreground_outcome(
                JamulusForegroundOutcome(
                    True,
                    JamulusForegroundReason.PLATFORM_NOT_MANAGED,
                )
            )
        if component is None:
            return self._remember_jamulus_foreground_outcome(
                JamulusForegroundOutcome(
                    False,
                    JamulusForegroundReason.IDENTITY_UNVERIFIED,
                )
            )
        try:
            executable = Path(component.executable_path)
        except (AttributeError, TypeError, ValueError):
            return self._remember_jamulus_foreground_outcome(
                JamulusForegroundOutcome(
                    False,
                    JamulusForegroundReason.IDENTITY_UNVERIFIED,
                )
            )
        bundle = next(
            (
                candidate
                for candidate in (executable, *executable.parents)
                if candidate.suffix.casefold() == ".app"
            ),
            None,
        )
        if bundle is None or process_identifier <= 0:
            return self._remember_jamulus_foreground_outcome(
                JamulusForegroundOutcome(
                    False,
                    JamulusForegroundReason.IDENTITY_UNVERIFIED,
                )
            )
        outcome = activate_running_macos_application_outcome(
            process_identifier,
            bundle,
        )
        if not outcome:
            return self._remember_jamulus_foreground_outcome(outcome)
        with self._reconnect_lock:
            current_process = self.jamulus_process
            identity_current = bool(
                current_process is proc
                and self._jamulus_process_generation == process_generation
                and self._jamulus_process_id(current_process) == process_identifier
                and self._jamulus_process_poll_evidence(current_process) is True
            )
        if not identity_current:
            outcome = JamulusForegroundOutcome(
                False,
                JamulusForegroundReason.PROCESS_CHANGED,
            )
        return self._remember_jamulus_foreground_outcome(outcome)

    def bring_jamulus_forward(self) -> bool:
        """Boolean compatibility wrapper for existing UI integrations."""

        return bool(self.bring_jamulus_forward_outcome())

    def launch_jamulus(
        self,
        manual: bool = True,
        reconnect: bool = False,
        force_restart: bool = False,
        native_setup_timeout_seconds: float | None = None,
        practice_request: bool = False,
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
            native_setup_timeout_seconds: Optional first-run sound-setup
                allowance. macOS manual launches apply the same bounded
                allowance automatically; the worker retains it only when the
                dedicated profile was genuinely missing before launch.
            practice_request: True only for the private Practice client. The
                accepted role and target are pinned for single-flight reuse so
                a rapid mode switch cannot connect Practice to a band server
                or a normal session to the private loopback server.

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
            with self._reconnect_lock:
                self.jamulus_reconnect_inflight = False
            return False
        if not isinstance(practice_request, bool):
            return False

        requested_native_setup_deadline = 0.0
        requested_native_setup_seconds = 0.0
        if native_setup_timeout_seconds is not None:
            try:
                native_setup_seconds = float(native_setup_timeout_seconds)
            except (TypeError, ValueError):
                return False
            if (
                isinstance(native_setup_timeout_seconds, bool)
                or not math.isfinite(native_setup_seconds)
                or native_setup_seconds <= 0.0
                or not manual
                or reconnect
            ):
                return False
            requested_native_setup_seconds = native_setup_seconds
            requested_native_setup_deadline = time.monotonic() + native_setup_seconds
        elif (
            manual
            and not reconnect
            and sys.platform == "darwin"
            and self._native_profile_manager is not None
        ):
            requested_native_setup_seconds = NATIVE_SOUND_SETUP_GRACE_SECONDS
            requested_native_setup_deadline = (
                time.monotonic() + NATIVE_SOUND_SETUP_GRACE_SECONDS
            )
        requested_server = self._effective_server_for_mode(practice_request)
        requested_launch_identity = (
            "practice" if practice_request else "session",
            requested_server,
            bool(reconnect),
            bool(force_restart),
            requested_native_setup_seconds,
        )

        reconnect_deferred = False
        reconnect_retired = False
        manual_launch_already_pending = False
        manual_launch_cleanup_pending = False
        manual_launch_role_mismatch = False
        launch_cancel: threading.Event | None = None
        launch_request_generation = 0
        prior_native_setup_deadline = 0.0
        prior_native_setup_generation = 0
        with self._jamulus_launch_control_lock:
            if reconnect and not self.jamulus_launch_intended:
                # A reconnect tick deliberately releases its state lock before
                # entering this launch boundary. Stop/Leave may win in that
                # interval. Refuse to create a new cancellation token after
                # launch intent has been retired; otherwise Stop has no token
                # to cancel and the queued worker can relaunch afterward.
                reconnect_retired = True
            else:
                previous_launch = self._pending_jamulus_launch_cancel
                if reconnect and previous_launch is not None:
                    # Automatic recovery is lower priority than an accepted
                    # launch request. In particular, a hosted startup can
                    # spend longer than one reconnect-timer interval preparing
                    # the server. The timer must not cancel that manual
                    # request and replace it with a profile-less reconnect.
                    reconnect_deferred = True
                    launch_cancel = previous_launch
                elif manual and previous_launch is not None:
                    # Manual launch is single-flight. Cancelling an accepted
                    # worker here creates two unsafe windows: its child may
                    # exist before publication while a replacement preflight
                    # releases the updater lease, or it may be published just
                    # before the replacement prevents its RPC monitor from
                    # starting. Reuse the active request instead. A token that
                    # is already cancelled still owns cleanup and must finish
                    # before another launch can be accepted.
                    launch_cancel = previous_launch
                    if previous_launch.is_set():
                        manual_launch_cleanup_pending = True
                    elif (
                        self._pending_jamulus_launch_identity
                        != requested_launch_identity
                    ):
                        manual_launch_role_mismatch = True
                    else:
                        manual_launch_already_pending = True
                else:
                    if previous_launch is not None:
                        previous_launch.set()
                    launch_cancel = threading.Event()
                    self._pending_jamulus_launch_cancel = launch_cancel
                    self._pending_jamulus_launch_identity = (
                        requested_launch_identity
                    )
                    if manual:
                        self._jamulus_launch_request_generation_counter += 1
                        self._jamulus_launch_request_generation = (
                            self._jamulus_launch_request_generation_counter
                        )
                        launch_request_generation = (
                            self._jamulus_launch_request_generation
                        )
                        self.jamulus_launch_intended = True
                        with self._reconnect_lock:
                            prior_native_setup_deadline = (
                                self._jamulus_native_setup_deadline
                            )
                            prior_native_setup_generation = (
                                self._jamulus_native_setup_process_generation
                            )
                            self._reset_jamulus_recovery_locked()
                            # First-run grace is not configured until the
                            # worker proves the pre-launch profile is missing.
                            self._jamulus_native_setup_deadline = 0.0
                            self._jamulus_native_setup_process_generation = 0
                    else:
                        launch_request_generation = (
                            self._jamulus_launch_request_generation
                        )
        if reconnect_retired:
            with self._reconnect_lock:
                self.jamulus_reconnect_inflight = False
            return False
        if launch_cancel is None:
            # Defensive fail-closed guard for any future request-state branch.
            if reconnect:
                self._terminalize_jamulus_recovery()
            else:
                with self._jamulus_launch_control_lock:
                    self.jamulus_launch_intended = False
                with self._reconnect_lock:
                    self._finish_jamulus_reconnect_attempt_locked(failed=False)
            LOGGER.error("Jamulus launch request was not created.")
            return False
        if reconnect_deferred:
            with self._reconnect_lock:
                self.jamulus_reconnect_inflight = False
            LOGGER.debug(
                "Jamulus reconnect deferred while an accepted launch is pending."
            )
            return False
        if manual_launch_cleanup_pending:
            LOGGER.debug(
                "Jamulus launch deferred while the previous request cleans up."
            )
            return False
        if manual_launch_role_mismatch:
            self.schedule_ui_callback(
                lambda: self.set_status_banner(
                    "Another audio mode is already starting. Wait for it to "
                    "finish or choose End before switching modes."
                )
            )
            return False
        if manual_launch_already_pending:
            LOGGER.debug("Jamulus launch reused the accepted pending request.")
            return True
        launch_recovery_generation = 0
        launch_native_setup_deadline = requested_native_setup_deadline
        if manual:
            self.metrics_service.increment("metric_jamulus_launch_attempt")
        elif reconnect:
            with self._reconnect_lock:
                launch_recovery_generation = (
                    self._begin_jamulus_recovery_locked()
                )
                launch_native_setup_deadline = (
                    self._jamulus_native_setup_deadline
                )

        def publish_preflight_failure(
            *,
            state: JamulusState,
            metric: str,
            ui_callback: Callable[[], None] | None = None,
            release_client_lease: bool = False,
            terminal_reconnect: bool = True,
        ) -> bool:
            """Retire one exact pre-worker request without stale publication."""

            with self._jamulus_launch_control_lock:
                if (
                    self._pending_jamulus_launch_cancel is not launch_cancel
                ):
                    return False
                if launch_cancel.is_set() or self.shutdown_requested():
                    # Stop/shutdown may tombstone the request generation while
                    # a synchronous preflight probe is in flight. Clear only
                    # this exact cancelled token; a superseding request owns a
                    # different token and is left untouched.
                    self._pending_jamulus_launch_cancel = None
                    self._pending_jamulus_launch_identity = None
                    if release_client_lease:
                        self._release_unestablished_client_lease()
                    return False
                if (
                    self._jamulus_launch_request_generation
                    != launch_request_generation
                ):
                    return False
                launch_cancel.set()
                self._pending_jamulus_launch_cancel = None
                self._pending_jamulus_launch_identity = None
                if not reconnect or terminal_reconnect:
                    self.jamulus_launch_intended = False
                with self._reconnect_lock:
                    self._jamulus_native_setup_deadline = 0.0
                    self._jamulus_native_setup_process_generation = 0
                    if reconnect:
                        if terminal_reconnect:
                            self._begin_jamulus_recovery_locked()
                            self.jamulus_reconnect_inflight = False
                            self._jamulus_recovery_exhausted = True
                        else:
                            self._finish_jamulus_reconnect_attempt_locked(
                                failed=True
                            )
                    else:
                        self._finish_jamulus_reconnect_attempt_locked(
                            failed=False
                        )
                self._set_jamulus_state(state)
                self.metrics_service.increment(metric)
                if release_client_lease:
                    self._release_unestablished_client_lease()
            self._schedule_jamulus_launch_ui_if_current(
                launch_request_generation,
                self.refresh_readiness,
            )
            if ui_callback is not None:
                self._schedule_jamulus_launch_ui_if_current(
                    launch_request_generation,
                    ui_callback,
                )
            return True

        # No server configured (fresh install where the wizard was skipped,
        # or a hand-edited config).  Without this guard we'd launch Jamulus
        # with "--connect :22124" and fail in a way that looks like a crash.
        # Practice mode is exempt — it supplies its own local target, so a
        # fresh install can practice before the band server even exists.
        server_host = str(self.settings.jamulus_server or "").strip()
        if not server_host and not practice_request:
            published = publish_preflight_failure(
                state=JamulusState.NOT_RUNNING,
                metric=(
                    "metric_jamulus_reconnect_failed"
                    if reconnect
                    else "metric_jamulus_launch_failed"
                ),
                ui_callback=(
                    None
                    if reconnect
                    else lambda: self.show_actionable_error(
                        "This jam needs a new invite",
                        what_failed="WebJam doesn’t have a band session to join.",
                        likely_cause="The saved invitation is missing or incomplete.",
                        next_action=(
                            "Close WebJam, open it again, and choose Host a Jam or paste "
                            "a fresh invitation from your host."
                        ),
                        retry_callback=None,
                    )
                ),
            )
            if reconnect and published:
                LOGGER.warning("Jamulus reconnect skipped: no server configured.")
            return False

        lease_acquired, lease_detail = self._acquire_runtime_component_lease(
            "client"
        )
        if not lease_acquired:
            publish_preflight_failure(
                state=JamulusState.NOT_RUNNING,
                metric=(
                    "metric_jamulus_reconnect_failed"
                    if reconnect
                    else "metric_jamulus_launch_failed"
                ),
                ui_callback=(
                    None
                    if reconnect
                    else lambda: self.show_actionable_error(
                        "Band audio is busy",
                        what_failed=lease_detail,
                        likely_cause=(
                            "Another WebJam window may be playing, or a verified "
                            "Jamulus update may still be installing."
                        ),
                        next_action=(
                            "Finish or close the other operation, then press Start "
                            "Audio again."
                        ),
                        retry_callback=None,
                    )
                ),
            )
            return False

        jamulus_path = self.find_jamulus()
        if not jamulus_path:
            published = publish_preflight_failure(
                state=(
                    JamulusState.NOT_RUNNING
                    if reconnect
                    else JamulusState.NOT_FOUND
                ),
                metric=(
                    "metric_jamulus_reconnect_failed"
                    if reconnect
                    else "metric_jamulus_launch_failed"
                ),
                ui_callback=(
                    None
                    if reconnect
                    else lambda: self.show_actionable_error(
                        "A music component is missing",
                        what_failed="WebJam couldn’t start the band audio on this Mac.",
                        likely_cause="The WebJam installation is incomplete.",
                        next_action="Reinstall the latest WebJam build, then try again.",
                        retry_callback=None,
                    )
                ),
                release_client_lease=True,
            )
            if reconnect and published:
                LOGGER.warning("Jamulus reconnect skipped: executable not found.")
            return False
        resolved_client_component = self._last_resolved_client_component
        if (
            resolved_client_component is not None
            and str(resolved_client_component.executable_path) != jamulus_path
        ):
            # A custom/test resolver may replace find_jamulus(). Never attach
            # stale registry identity to a different executable path.
            resolved_client_component = None

        with self._reconnect_lock:
            observed_process = self.jamulus_process
            observed_process_generation = self._jamulus_process_generation
        owned_live_process = self._jamulus_process_alive(observed_process)
        force_restart_process = (
            observed_process if force_restart and owned_live_process else None
        )
        force_restart_generation = (
            observed_process_generation if force_restart_process is not None else 0
        )
        force_restart_process_id = self._jamulus_process_id(
            force_restart_process
        )
        if owned_live_process and not force_restart:
            already_published = False
            with self._jamulus_launch_control_lock:
                if (
                    self._pending_jamulus_launch_cancel is not launch_cancel
                    or self._jamulus_launch_request_generation
                    != launch_request_generation
                    or launch_cancel.is_set()
                    or self.shutdown_requested()
                ):
                    return False
                with self._reconnect_lock:
                    current_process = self.jamulus_process
                    if (
                        current_process is observed_process
                        and self._jamulus_process_generation
                        == observed_process_generation
                        and self._jamulus_process_alive(current_process)
                    ):
                        self.jamulus_state = JamulusState.ALREADY.value
                        self.jamulus_reconnect_inflight = False
                        self._jamulus_native_setup_deadline = (
                            prior_native_setup_deadline
                        )
                        self._jamulus_native_setup_process_generation = (
                            prior_native_setup_generation
                        )
                        self._pending_jamulus_launch_cancel = None
                        self._pending_jamulus_launch_identity = None
                        already_published = True
            if already_published:
                self._schedule_jamulus_launch_ui_if_current(
                    launch_request_generation,
                    self.refresh_readiness,
                )
                if manual:
                    self._schedule_jamulus_launch_ui_if_current(
                        launch_request_generation,
                        lambda: self.set_status_banner("Jamulus is already running."),
                    )
                return True

        # Detect port conflict before launching Jamulus.  If the JSON-RPC port
        # is already in use (typically: another WebJam instance, or a previous
        # Jamulus process that didn't shut down cleanly), Popen would succeed
        # but Jamulus would silently fail to bind — leaving a running
        # subprocess we can't control via RPC.
        #
        # A force restart already owns the live process responsible for this
        # port. The worker below is the sole lifecycle-lock owner that may
        # retire it, so probing here would either reject our own port or force
        # a synchronous UI-thread termination.
        if not (force_restart and owned_live_process) and self._is_rpc_port_in_use():
            port = self.settings.jamulus_rpc_port
            published = publish_preflight_failure(
                state=JamulusState.PORT_IN_USE,
                metric=(
                    "metric_jamulus_port_conflict"
                    if manual
                    else "metric_jamulus_reconnect_failed"
                ),
                ui_callback=(
                    None
                    if reconnect
                    else lambda: self.show_actionable_error(
                        "Another audio session is open",
                        what_failed="WebJam can’t start a second music connection on this Mac.",
                        likely_cause=(
                            "Another WebJam window is open, or the last session is "
                            "still finishing."
                        ),
                        next_action="Close the other WebJam window, wait a moment, then try again.",
                        retry_callback=(
                            None if practice_request else self.retry_audio_launch
                        ),
                    )
                ),
                release_client_lease=True,
                terminal_reconnect=False,
            )
            if reconnect and published:
                LOGGER.warning(
                    "Jamulus reconnect skipped: JSON-RPC port %s already in use.", port
                )
            return False

        # A previous clean End/Leave intentionally publishes ``Stopped``.
        # Replace that terminal value synchronously once this exact launch
        # request has passed every preflight.  The startup journey begins
        # polling before the asynchronous worker necessarily reaches Popen;
        # leaving the stale value visible during that window would falsely
        # classify a healthy immediate restart as failed even though the new
        # client subsequently connects.
        #
        # Keep the generation/cancellation check under the control lock so a
        # concurrent Stop or superseding launch wins without this older
        # request overwriting its state.
        with self._jamulus_launch_control_lock:
            if (
                self._pending_jamulus_launch_cancel is not launch_cancel
                or self._jamulus_launch_request_generation
                != launch_request_generation
            ):
                return False
            if launch_cancel.is_set() or self.shutdown_requested():
                self._pending_jamulus_launch_cancel = None
                self._pending_jamulus_launch_identity = None
                self._release_unestablished_client_lease()
                return False
            self._set_jamulus_state(JamulusState.STARTING)

        banner_text = "Starting your band audio…" if not reconnect else "Reconnecting band audio…"
        if practice_request:
            banner_text = "Starting practice session..."
        self._schedule_jamulus_launch_ui_if_current(
            launch_request_generation,
            lambda: self.set_status_banner(banner_text, color="#BF5700"),
        )

        server = requested_server

        def _do_launch() -> None:
            with self._jamulus_lifecycle_lock:
                proc: subprocess.Popen | None = None
                runtime_paths_prepared = False
                try:
                    def cancelled(proc: subprocess.Popen | None = None) -> bool:
                        """Discard a stale queued launch without publishing it."""

                        if not (launch_cancel.is_set() or self.shutdown_requested()):
                            return False
                        process_stopped = self._terminate_jamulus_child(proc)
                        with self._reconnect_lock:
                            self.jamulus_reconnect_inflight = False
                            if proc is not None and not process_stopped:
                                # The child exists and cleanup is unproved.
                                # Publish it before returning so Stop/Shutdown
                                # retains one retryable owner and the runtime
                                # component lease cannot be released beneath
                                # an untracked executable.
                                self._jamulus_process_generation_counter += 1
                                self.jamulus_process = proc
                                self._jamulus_process_started_at = time.monotonic()
                                self._jamulus_process_generation = (
                                    self._jamulus_process_generation_counter
                                )
                                self._jamulus_process_recovery_generation = (
                                    launch_recovery_generation if reconnect else 0
                                )
                                self.jamulus_state = "Stop failed"
                        if process_stopped:
                            self._close_jamulus_log_file()
                            self._active_native_profile = None
                            self._set_live_audio_route_owned(False)
                            if runtime_paths_prepared:
                                self._release_client_runtime_paths(
                                    confirmed_stopped=True
                                )
                        if self.shutdown_requested() and process_stopped:
                            self._release_unestablished_client_lease()
                        return True

                    # A second click/deep-link can queue another launch while
                    # the first worker is still starting. Re-check only after
                    # acquiring the lifecycle lock so two clients can never be
                    # spawned and one silently lose process ownership.
                    if cancelled():
                        return
                    if force_restart:
                        with self._reconnect_lock:
                            current_process = self.jamulus_process
                            current_generation = self._jamulus_process_generation
                            current_started_at = self._jamulus_process_started_at
                            current_process_recovery_generation = (
                                self._jamulus_process_recovery_generation
                            )
                            current_recovery_generation = (
                                self._jamulus_recovery_generation
                            )
                            current_recovery_active = (
                                self._jamulus_recovery_active
                            )
                            current_native_setup_generation = (
                                self._jamulus_native_setup_process_generation
                            )
                            current_native_setup_deadline = (
                                self._jamulus_native_setup_deadline
                            )
                        current_process_id = self._jamulus_process_id(
                            current_process
                        )
                        if (
                            force_restart_process is None
                            or current_process is not force_restart_process
                            or current_generation != force_restart_generation
                            or (
                                force_restart_process_id > 0
                                and current_process_id
                                != force_restart_process_id
                            )
                        ):
                            # This worker belongs to an older process.
                            # Cancellation normally catches the race, but
                            # identity is the final guard against killing or
                            # replacing a newer client.
                            with self._reconnect_lock:
                                self.jamulus_reconnect_inflight = False
                            return
                        current_process_alive = self._jamulus_process_alive(
                            current_process
                        )
                        current_freshness, _current_rpc_age = (
                            self._jamulus_rpc_observation(
                                process_alive=current_process_alive,
                                process_started_at=current_started_at,
                                process_generation=current_generation,
                                process_id=current_process_id,
                                now=time.monotonic(),
                            )
                        )
                        current_observed_at = time.monotonic()
                        current_native_setup_active = bool(
                            current_process_alive
                            and current_generation > 0
                            and current_generation
                            == current_native_setup_generation
                            and current_observed_at
                            < current_native_setup_deadline
                        )
                        current_auth_timed_out = bool(
                            current_process_alive
                            and not current_native_setup_active
                            and current_recovery_active
                            and current_process_recovery_generation > 0
                            and current_process_recovery_generation
                            == current_recovery_generation
                            and current_started_at > 0.0
                            and (
                                current_observed_at - current_started_at
                                >= RECONNECT_LOCAL_ROSTER_GRACE_SECONDS
                            )
                        )
                        if (
                            current_process_alive
                            and current_freshness
                            in {
                                JamulusRpcFreshness.STARTING,
                                JamulusRpcFreshness.FRESH,
                            }
                            and not current_auth_timed_out
                        ):
                            # The exact queued process recovered before this
                            # worker acquired lifecycle ownership. Never kill
                            # it based on an earlier stale sample.
                            with self._reconnect_lock:
                                self.jamulus_reconnect_inflight = False
                            return
                    dead_process_replacement = False
                    with self._reconnect_lock:
                        current_process = self.jamulus_process
                        dead_process_replacement = bool(
                            observed_process is not None
                            and current_process is observed_process
                            and self._jamulus_process_generation
                            == observed_process_generation
                            and not self._jamulus_process_alive(current_process)
                        )
                    if dead_process_replacement:
                        # A crashed child cannot be terminated below, but its
                        # RPC reader may still be running and bound to that
                        # dead generation/PID. Retire the monitor before a
                        # replacement is published; otherwise
                        # JamulusController.start() correctly refuses to
                        # relabel the stale reader and the new client can
                        # never provide process-authenticating roster proof.
                        try:
                            self.jamulus_controller.stop()
                        except Exception as exc:
                            raise RuntimeError(
                                "Could not stop the old Jamulus monitor."
                            ) from exc
                        with self._reconnect_lock:
                            if (
                                self.jamulus_process is not observed_process
                                or self._jamulus_process_generation
                                != observed_process_generation
                                or self._jamulus_process_alive(
                                    self.jamulus_process
                                )
                            ):
                                raise RuntimeError(
                                    "Jamulus changed during dead-process recovery."
                                )
                            self.jamulus_process = None
                            self._jamulus_process_started_at = 0.0
                            self._jamulus_process_generation = 0
                            self._jamulus_process_recovery_generation = 0
                    if (
                        self.jamulus_process is not None
                        and self.jamulus_process.poll() is None
                    ):
                        if force_restart:
                            try:
                                self.jamulus_controller.stop()
                            except Exception as exc:
                                raise RuntimeError(
                                    "Could not stop the old Jamulus monitor."
                                ) from exc
                            try:
                                self.jamulus_process.terminate()
                                try:
                                    self.jamulus_process.wait(timeout=2.0)
                                except subprocess.TimeoutExpired:
                                    self.jamulus_process.kill()
                                    self.jamulus_process.wait(timeout=2.0)
                            except Exception as exc:
                                raise RuntimeError(
                                    "Could not replace a hung Jamulus process."
                                ) from exc
                            with self._reconnect_lock:
                                self.jamulus_process = None
                                self._jamulus_process_started_at = 0.0
                                self._jamulus_process_generation = 0
                                self._jamulus_process_recovery_generation = 0
                        else:
                            with self._jamulus_launch_control_lock:
                                if (
                                    self._pending_jamulus_launch_cancel
                                    is not launch_cancel
                                    or self._jamulus_launch_request_generation
                                    != launch_request_generation
                                    or launch_cancel.is_set()
                                    or self.shutdown_requested()
                                ):
                                    launch_cancel.set()
                                    cancelled()
                                    return
                                self._set_jamulus_state(JamulusState.ALREADY)
                                with self._reconnect_lock:
                                    self.jamulus_reconnect_inflight = False
                            self._schedule_jamulus_launch_ui_if_current(
                                launch_request_generation,
                                self.refresh_readiness,
                            )
                            return
                    if cancelled():
                        return

                    verified_client_component = resolved_client_component
                    if verified_client_component is not None:
                        current_component = self._revalidate_runtime_component(
                            verified_client_component
                        )
                        if current_component is None:
                            raise JamulusNativeProfileError(
                                "WebJam couldn't reverify the approved Jamulus "
                                "music component. Stop audio, finish any pending "
                                "update, then try again."
                            )
                        verified_client_component = current_component

                    native_profile = None
                    native_setup_required = False
                    if self._native_profile_manager is not None:
                        if reconnect:
                            native_profile = self._active_native_profile
                            if native_profile is None:
                                raise JamulusNativeProfileError(
                                    "WebJam couldn't restore its Jamulus profile. "
                                    "Start the jam again."
                                )
                            if (
                                verified_client_component is not None
                                and native_profile.jamulus_version
                                != verified_client_component.version
                            ):
                                raise JamulusNativeProfileError(
                                    "A Jamulus update became active during the "
                                    "session. Stop audio, then start the jam "
                                    "again to use it safely."
                                )
                            native_profile = (
                                self._native_profile_manager.validate_active(
                                    native_profile
                                )
                            )
                            self._active_native_profile = native_profile
                            native_setup_required = bool(
                                launch_native_setup_deadline > time.monotonic()
                            )
                        else:
                            if verified_client_component is None:
                                native_profile = self._native_profile_manager.prepare(
                                    self.settings,
                                    jamulus_path,
                                )
                            elif (
                                verified_client_component.catalog_entry
                                is not None
                            ):
                                # The platform provider just revalidated the
                                # exact signed-catalog runtime tree. Do not
                                # execute it a second time with ``--version``;
                                # on unsigned platforms that would load DLLs or
                                # plugins before the hardened launch boundary.
                                native_profile = self._native_profile_manager.plan(
                                    jamulus_version=(
                                        verified_client_component.version
                                    )
                                )
                            else:
                                native_profile = self._native_profile_manager.prepare(
                                    self.settings,
                                    jamulus_path,
                                    approved_versions=(
                                        self._approved_versions_for_resolved_component(
                                            verified_client_component
                                        )
                                    ),
                                    expected_version=(
                                        verified_client_component.version
                                    ),
                                )
                            # Capture the pre-launch fact. Jamulus may create
                            # the INI during Popen before the final path
                            # revalidation, but that does not mean a human has
                            # selected an interface or completed first-run
                            # sound setup.
                            native_setup_required = not bool(
                                getattr(
                                    native_profile,
                                    "profile_exists",
                                    True,
                                )
                            )
                            self._active_native_profile = native_profile
                            if (
                                native_setup_required
                                and launch_native_setup_deadline
                                > time.monotonic()
                            ):
                                with self._jamulus_launch_control_lock:
                                    if (
                                        self._pending_jamulus_launch_cancel
                                        is launch_cancel
                                        and self._jamulus_launch_request_generation
                                        == launch_request_generation
                                        and not launch_cancel.is_set()
                                    ):
                                        with self._reconnect_lock:
                                            self._jamulus_native_setup_deadline = (
                                                launch_native_setup_deadline
                                            )
                                            self._jamulus_native_setup_process_generation = 0
                    # A live Jamulus client owns the hardware route on every
                    # supported platform. WebJam's optional PortAudio meter
                    # must not contend with the musician's native setup.
                    self._set_live_audio_route_owned(True)

                    if (
                        self._hosting_enabled()
                        and not practice_request
                    ):
                        hosted_ok, hosted_detail = self.ensure_hosted_server()
                        if not hosted_ok:
                            LOGGER.error("Hosted server could not start: %s", hosted_detail)
                            host_failure_callbacks: list[Callable[[], None]] = []
                            with self._jamulus_launch_control_lock:
                                failure_is_current = bool(
                                    self._pending_jamulus_launch_cancel
                                    is launch_cancel
                                    and self._jamulus_launch_request_generation
                                    == launch_request_generation
                                    and not launch_cancel.is_set()
                                    and not self.shutdown_requested()
                                )
                                self._active_native_profile = None
                                self._set_live_audio_route_owned(False)
                                if not failure_is_current:
                                    if runtime_paths_prepared:
                                        self._release_client_runtime_paths(
                                            confirmed_stopped=True
                                        )
                                    return
                                launch_cancel.set()
                                self.jamulus_launch_intended = False
                                with self._reconnect_lock:
                                    self._jamulus_native_setup_deadline = 0.0
                                    self._jamulus_native_setup_process_generation = 0
                                    self.jamulus_reconnect_inflight = False
                                self._set_jamulus_state(JamulusState.STOPPED)
                                host_failure_callbacks.extend(
                                    (
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
                                        ),
                                        self.refresh_readiness,
                                    )
                                )
                                self._release_unestablished_client_lease()
                            for callback in host_failure_callbacks:
                                self._schedule_jamulus_launch_ui_if_current(
                                    launch_request_generation,
                                    callback,
                                )
                            return

                    # Cancellation may have arrived while profile/server
                    # preparation was running.  It must win before a process
                    # is created, even if this worker already owns the
                    # lifecycle lock.
                    if cancelled():
                        return

                    import secrets as _secrets
                    jsonrpc_secret_args: list[str] = []
                    # This exact constant is shared with JamulusRpcClient; a
                    # runtime home lookup must never make launcher and monitor
                    # authenticate against different files.
                    rpc_secret_path = Path(DEFAULT_SECRET_PATH)
                    if self._secure_macos_runtime_enabled:
                        # Any predecessor was proved absent (or synchronously
                        # reaped above) before this worker reached preparation.
                        if not self._release_client_runtime_paths(
                            confirmed_stopped=True
                        ):
                            raise SecureRuntimeError(
                                "WebJam could not retire its previous private "
                                "Jamulus credential."
                            )
                        try:
                            prepared_runtime_paths = (
                                self._prepare_owned_runtime_paths(
                                    secret_path=rpc_secret_path,
                                    secret_payload=(
                                        _secrets.token_urlsafe(24) + "\n"
                                    ).encode("ascii"),
                                )
                            )
                        except _JamulusRuntimePreparationError as exc:
                            if exc.retained_state is not None:
                                self._client_runtime_paths = exc.retained_state
                            raise
                        self._client_runtime_paths = prepared_runtime_paths
                        runtime_paths_prepared = True
                    else:
                        from core.file_io import atomic_write_text

                        try:
                            atomic_write_text(
                                rpc_secret_path,
                                _secrets.token_urlsafe(24) + "\n",
                                mode=0o600,
                            )
                        except OSError:
                            raise RuntimeError(
                                "Could not create Jamulus JSON-RPC secret file; "
                                "refusing to launch without RPC authentication."
                            ) from None
                    jsonrpc_secret_args = [
                        "--jsonrpcsecretfile", str(rpc_secret_path)
                    ]

                    remote_identity = bool(
                        self._remote_host_mode or self._remote_guest_mode
                    )
                    identity_args = []
                    if not remote_identity:
                        identity_args = [
                            "--clientname",
                            validate_jamulus_name(
                                getattr(
                                    self.settings,
                                    "musician_name",
                                    "WebJam Musician",
                                )
                            ).value,
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
                        self._close_jamulus_log_file()
                        log_file = open_private_text_log(log_path)
                        self._jamulus_log_file = log_file
                        stdout_dest = log_file
                    except OSError as exc:
                        LOGGER.debug(
                            "Could not open the Jamulus log file (%s).",
                            type(exc).__name__,
                        )

                    popen_kwargs: dict = {
                        "stdout": stdout_dest,
                        "stderr": subprocess.STDOUT if log_file else subprocess.DEVNULL,
                    }
                    child_environment = _jamulus_child_environment(
                        catalog_verified=(
                            verified_client_component is not None
                            and verified_client_component.catalog_entry
                            is not None
                        ),
                        executable=jamulus_path,
                    )
                    popen_kwargs["env"] = child_environment
                    if (
                        verified_client_component is not None
                        and verified_client_component.catalog_entry is not None
                        and native_profile is None
                    ):
                        popen_kwargs["cwd"] = str(
                            verified_client_component.executable_path.parent
                        )
                    if native_profile is not None:
                        popen_kwargs["cwd"] = str(native_profile.working_directory)
                    if sys.platform == "win32":
                        popen_kwargs["creationflags"] = (
                            getattr(subprocess, "CREATE_NO_WINDOW", 0)
                        )

                    for i in range(3):
                        if cancelled():
                            return
                        try:
                            # Jamulus still consumes pathname arguments/cwd.
                            # Recheck every retained directory, secret, and
                            # native profile at the last possible boundary.
                            native_profile = (
                                self._validate_primary_launch_paths(
                                    native_profile
                                )
                            )
                            proc = subprocess.Popen(cmd, **popen_kwargs)
                            try:
                                native_profile = (
                                    self._validate_primary_launch_paths(
                                        native_profile
                                    )
                                )
                            except (
                                JamulusNativeProfileError,
                                SecureRuntimeError,
                            ):
                                process_stopped = self._terminate_jamulus_child(
                                    proc
                                )
                                if process_stopped:
                                    self._release_client_runtime_paths(
                                        confirmed_stopped=True
                                    )
                                    proc = None
                                else:
                                    raise SecureRuntimeError(
                                        "WebJam could not confirm cleanup after "
                                        "private Jamulus runtime data changed."
                                    ) from None
                                raise
                            break
                        except (
                            JamulusNativeProfileError,
                            SecureRuntimeError,
                        ):
                            # Retrying cannot make a changed path safe.
                            raise
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

                    with self._jamulus_launch_control_lock:
                        if (
                            self._pending_jamulus_launch_cancel is not launch_cancel
                            or self._jamulus_launch_request_generation
                            != launch_request_generation
                            or launch_cancel.is_set()
                            or self.shutdown_requested()
                        ):
                            launch_cancel.set()
                            cancelled(proc)
                            return
                        with self._reconnect_lock:
                            if manual:
                                # A reconnect tick may have sampled the empty
                                # pending state just before this manual request
                                # installed its token. Manual publication is
                                # the final owner and retires any late recovery
                                # mutation before binding the new process.
                                self._reset_jamulus_recovery_locked()
                            self._jamulus_process_generation_counter += 1
                            self.jamulus_process = proc
                            self.jamulus_state = JamulusState.RUNNING.value
                            self.jamulus_reconnect_inflight = False
                            self._jamulus_process_started_at = time.monotonic()
                            self._jamulus_process_generation = (
                                self._jamulus_process_generation_counter
                            )
                            self._jamulus_process_recovery_generation = (
                                launch_recovery_generation if reconnect else 0
                            )
                            published_generation = self._jamulus_process_generation
                            if (
                                native_setup_required
                                and launch_native_setup_deadline > time.monotonic()
                            ):
                                self._jamulus_native_setup_deadline = (
                                    launch_native_setup_deadline
                                )
                                self._jamulus_native_setup_process_generation = (
                                    published_generation
                                )
                            else:
                                self._jamulus_native_setup_deadline = 0.0
                                self._jamulus_native_setup_process_generation = 0
                            published_process_id = self._jamulus_process_id(proc)
                    if verified_client_component is not None:
                        self._active_client_component = verified_client_component

                    if not reconnect:
                        self.metrics_service.increment("metric_jamulus_launch_success")

                    def _start_monitoring():
                        time.sleep(2.0)
                        # Stop/replace owns this same lifecycle lock. Recheck
                        # the exact published child only after acquiring it so
                        # a delayed thread can never restart monitoring after
                        # cleanup, or attach the old reader to a replacement.
                        with self._jamulus_lifecycle_lock:
                            with self._jamulus_launch_control_lock:
                                launch_still_intended = bool(
                                    self.jamulus_launch_intended
                                    and not launch_cancel.is_set()
                                    and not self.shutdown_requested()
                                )
                            with self._reconnect_lock:
                                current_process = self.jamulus_process
                                monitor_is_current = bool(
                                    launch_still_intended
                                    and current_process is proc
                                    and self._jamulus_process_generation
                                    == published_generation
                                    and self._jamulus_process_id(current_process)
                                    == published_process_id
                                    and self._jamulus_process_alive(current_process)
                                )
                            if not monitor_is_current:
                                return
                            try:
                                self.jamulus_controller.start(
                                    process_generation=published_generation,
                                    process_id=published_process_id,
                                )
                            except Exception as exc:
                                LOGGER.warning(
                                    "JamulusController.start() failed (%s).",
                                    type(exc).__name__,
                                )

                    threading.Thread(target=_start_monitoring, daemon=True).start()

                    self._schedule_jamulus_launch_ui_if_current(
                        launch_request_generation,
                        self.refresh_readiness,
                    )
                    if manual:
                        msg = "Band audio started — connecting everyone now."
                        self._schedule_jamulus_launch_ui_if_current(
                            launch_request_generation,
                            lambda m=msg: self.set_status_banner(m),
                        )

                except JamulusNativeProfileError as exc:
                    was_practice = practice_request
                    native_failure_callbacks: list[Callable[[], None]] = []
                    with self._jamulus_launch_control_lock:
                        failure_is_current = bool(
                            self._pending_jamulus_launch_cancel is launch_cancel
                            and self._jamulus_launch_request_generation
                            == launch_request_generation
                            and not launch_cancel.is_set()
                            and not self.shutdown_requested()
                        )
                        if not failure_is_current:
                            launch_cancel.set()
                            cancelled(proc)
                            return
                        if not reconnect:
                            self._clear_native_setup_grace_for_request_locked(
                                launch_cancel,
                                launch_native_setup_deadline,
                            )
                        LOGGER.info(
                            "Jamulus native-profile preflight failed (%s).",
                            type(exc).__name__,
                        )
                        if proc is None and runtime_paths_prepared:
                            self._release_client_runtime_paths(
                                confirmed_stopped=True
                            )
                        self._close_jamulus_log_file()
                        self._set_live_audio_route_owned(False)
                        self._active_native_profile = None
                        self._set_jamulus_state(
                            JamulusState.LAUNCH_FAILED
                            if not reconnect
                            else JamulusState.NOT_RUNNING
                        )
                        if reconnect:
                            # A reconnect must never rewrite a musician's
                            # native setup behind their back.
                            self._terminalize_jamulus_recovery_locked()
                            self.metrics_service.increment(
                                "metric_jamulus_reconnect_failed"
                            )
                        else:
                            self.jamulus_launch_intended = False
                            with self._reconnect_lock:
                                self._finish_jamulus_reconnect_attempt_locked(
                                    failed=False
                                )
                            self.metrics_service.increment(
                                "metric_jamulus_launch_failed"
                            )
                        if was_practice:
                            self._terminate_practice_server()
                            self.practice_mode = False
                        native_failure_callbacks.append(self.refresh_readiness)

                        def show_native_profile_error(
                            message: str = str(exc),
                            practice: bool = was_practice,
                        ) -> None:
                            self.show_actionable_error(
                                "Band audio needs attention",
                                what_failed=message,
                                likely_cause=(
                                    "Jamulus could not open its native sound profile."
                                ),
                                next_action=(
                                    "Open Jamulus Audio Settings, check your "
                                    "interface, then try again."
                                ),
                                retry_callback=(
                                    None if practice else self.retry_audio_launch
                                ),
                            )

                        native_failure_callbacks.append(show_native_profile_error)
                        self._release_unestablished_client_lease()
                    for callback in native_failure_callbacks:
                        self._schedule_jamulus_launch_ui_if_current(
                            launch_request_generation,
                            callback,
                        )
                except Exception as exc:
                    was_practice = practice_request
                    generic_failure_callbacks: list[Callable[[], None]] = []
                    with self._jamulus_launch_control_lock:
                        stale_worker = bool(
                            self._pending_jamulus_launch_cancel is not launch_cancel
                            or self._jamulus_launch_request_generation
                            != launch_request_generation
                            or launch_cancel.is_set()
                            or self.shutdown_requested()
                        )
                        if stale_worker:
                            launch_cancel.set()
                            cancelled(proc)
                            return
                        LOGGER.error(
                            "Failed to launch Jamulus (%s).",
                            type(exc).__name__,
                        )
                        child_stopped = self._terminate_jamulus_child(proc)
                        if not reconnect and child_stopped:
                            self._clear_native_setup_grace_for_request_locked(
                                launch_cancel,
                                launch_native_setup_deadline,
                            )
                        if proc is not None and not child_stopped:
                            with self._reconnect_lock:
                                if self.jamulus_process is not proc:
                                    self._jamulus_process_generation_counter += 1
                                    self.jamulus_process = proc
                                    self._jamulus_process_started_at = time.monotonic()
                                    self._jamulus_process_generation = (
                                        self._jamulus_process_generation_counter
                                    )
                                    self._jamulus_process_recovery_generation = (
                                        launch_recovery_generation if reconnect else 0
                                    )
                                self.jamulus_state = "Stop failed"
                                self.jamulus_reconnect_inflight = False
                            if reconnect:
                                self._terminalize_jamulus_recovery_locked()
                            else:
                                self.jamulus_launch_intended = False
                            self.metrics_service.increment(
                                "metric_jamulus_reconnect_failed"
                                if reconnect
                                else "metric_jamulus_launch_failed"
                            )
                            generic_failure_callbacks.append(
                                self.refresh_readiness
                            )
                            generic_failure_callbacks.append(
                                lambda: self.show_actionable_error(
                                    "Band audio cleanup needs attention",
                                    what_failed=(
                                        "WebJam could not confirm that the interrupted "
                                        "music engine stopped."
                                    ),
                                    likely_cause=(
                                        "The native audio process did not answer the "
                                        "bounded stop request."
                                    ),
                                    next_action=(
                                        "Choose End or Leave again to finish cleanup "
                                        "before starting another session."
                                    ),
                                    retry_callback=None,
                                )
                            )
                        else:
                            if proc is not None:
                                with self._reconnect_lock:
                                    if self.jamulus_process is proc:
                                        self.jamulus_process = None
                                        self._jamulus_process_started_at = 0.0
                                        self._jamulus_process_generation = 0
                                        self._jamulus_process_recovery_generation = 0
                            if child_stopped and runtime_paths_prepared:
                                self._release_client_runtime_paths(
                                    confirmed_stopped=True
                                )
                            self._close_jamulus_log_file()
                            if not reconnect:
                                self._active_native_profile = None
                                self._set_live_audio_route_owned(False)
                                self.jamulus_launch_intended = False
                            self._set_jamulus_state(
                                JamulusState.LAUNCH_FAILED
                                if not reconnect
                                else JamulusState.NOT_RUNNING
                            )
                            with self._reconnect_lock:
                                self._finish_jamulus_reconnect_attempt_locked(
                                    failed=reconnect
                                )

                            if reconnect:
                                self.metrics_service.increment(
                                    "metric_jamulus_reconnect_failed"
                                )
                                self._release_unestablished_client_lease()
                                generic_failure_callbacks.append(
                                    self.refresh_readiness
                                )
                            else:
                                self.metrics_service.increment(
                                    "metric_jamulus_launch_failed"
                                )
                                if practice_request:
                                    self._terminate_practice_server()
                                    self.practice_mode = False
                                self._release_unestablished_client_lease()
                                generic_failure_callbacks.append(
                                    self.refresh_readiness
                                )
                                LOGGER.debug(
                                    "Music connection launch failed (%s).",
                                    type(exc).__name__,
                                )
                                generic_failure_callbacks.append(
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
                                ),
                    for callback in generic_failure_callbacks:
                        self._schedule_jamulus_launch_ui_if_current(
                            launch_request_generation,
                            callback,
                        )
                finally:
                    self._retire_jamulus_launch_request(launch_cancel)

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
        return self._effective_server_for_mode(self.practice_mode)

    def _effective_server_for_mode(self, practice: bool) -> str:
        """Resolve one immutable launch target for the requested audio role."""

        if practice:
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
        with self._jamulus_launch_control_lock:
            launch_pending = self._pending_jamulus_launch_cancel is not None
        if launch_pending:
            self.metrics_service.increment("metric_practice_launch_failed")
            self.schedule_ui_callback(
                lambda: self.set_status_banner(
                    "Band audio is already starting or closing. Wait for it "
                    "to finish, then start Practice Solo."
                )
            )
            return False
        if self.jamulus_process is not None and self.jamulus_process.poll() is None:
            self.schedule_ui_callback(
                lambda: self.set_status_banner(
                    "Stop Audio first, then start a practice session."
                )
            )
            return False

        lease_acquired, lease_detail = self._acquire_runtime_component_lease(
            "practice"
        )
        if not lease_acquired:
            self.metrics_service.increment("metric_practice_launch_failed")
            self.show_actionable_error(
                "Practice audio is busy",
                what_failed=lease_detail,
                likely_cause=(
                    "Another WebJam window may be playing, or a verified "
                    "Jamulus update may still be installing."
                ),
                next_action=(
                    "Finish or close the other operation, then start Practice "
                    "Solo again."
                ),
                retry_callback=None,
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
            self._release_runtime_component_lease("practice")
            return False

        # Spawn the private local server (headless).  Its output goes to a
        # dedicated log for troubleshooting.
        cmd = [jamulus_path, "--server", "--nogui", "--port", str(PRACTICE_PORT)]
        stdout_dest = subprocess.DEVNULL
        practice_log = None
        try:
            log_path = Path.home() / ".webjam_practice_server.log"
            self._close_practice_log_file()
            practice_log = open_private_text_log(log_path)
            stdout_dest = practice_log
            self._practice_log_file = practice_log
        except OSError:
            pass
        practice_component = self._last_resolved_client_component
        practice_catalog_verified = (
            practice_component is not None
            and practice_component.catalog_entry is not None
            and str(practice_component.executable_path) == jamulus_path
        )
        popen_kwargs: dict = {
            "stdout": stdout_dest,
            "stderr": subprocess.STDOUT if stdout_dest is not subprocess.DEVNULL
                      else subprocess.DEVNULL,
            "env": _jamulus_child_environment(
                catalog_verified=practice_catalog_verified,
                executable=jamulus_path,
            ),
        }
        if practice_catalog_verified and practice_component is not None:
            popen_kwargs["cwd"] = str(practice_component.executable_path.parent)
        import sys
        if sys.platform == "win32":
            popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            self.practice_server_process = subprocess.Popen(cmd, **popen_kwargs)
        except Exception as exc:  # noqa: BLE001
            LOGGER.error(
                "Practice server failed to start (%s).",
                type(exc).__name__,
            )
            self.metrics_service.increment("metric_practice_launch_failed")
            self.show_actionable_error(
                "Practice Server Failed",
                what_failed="The local practice server could not start.",
                likely_cause="Jamulus path invalid, or the practice port is blocked.",
                next_action="Check the Jamulus path in Settings, then retry.",
                retry_callback=None,
            )
            self._release_runtime_component_lease("practice")
            return False

        self.practice_mode = True
        # Connect the regular client to the local server.
        accepted = self.launch_jamulus(
            manual=True,
            reconnect=False,
            practice_request=True,
        )
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
                LOGGER.warning(
                    "Failed to terminate practice server (%s).",
                    type(exc).__name__,
                )
                stopped = False
        if stopped:
            self.practice_server_process = None
            self._close_practice_log_file()
            self._release_runtime_component_lease("practice")
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
        with self._jamulus_launch_control_lock:
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

    def find_jamulus_server_with_source(self) -> tuple[str | None, str]:
        """Resolve server as managed, embedded, then installed system app."""

        self._last_resolved_server_component = None
        active = self._active_server_component
        if active is not None and (
            self.hosted_server_owned() or self.jamulus_launch_intended
        ):
            current = self._revalidate_runtime_component(active)
            if current is None:
                return None, "pinned-invalid"
            self._last_resolved_server_component = current
            return str(current.executable_path), current.source

        managed = self._managed_runtime_component(role=JamulusRole.SERVER)
        if managed is not None:
            self._last_resolved_server_component = managed
            return str(managed.executable_path), "managed"

        bundled = _bundled_jamulus_server_candidate()
        if bundled:
            component = self._embedded_runtime_component(
                bundled,
                role=JamulusRole.SERVER,
            )
            if component is not None:
                self._last_resolved_server_component = component
                return str(component.executable_path), "bundled"
        candidate = Path(self.JAMULUS_SERVER_BINARY)
        component = self._runtime_component(
            candidate,
            role=JamulusRole.SERVER,
            source="installed",
        )
        if component is not None:
            self._last_resolved_server_component = component
            return str(component.executable_path), "installed"
        return None, "missing"

    def find_jamulus_server(self) -> str | None:
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
        if (
            self._hosted_runtime_paths is not None
            and not self._runtime_paths_match(self._hosted_runtime_paths)
        ):
            return False, "the recorder credential could not be reverified"
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
            LOGGER.debug(
                "Hosted server RPC probe failed (%s).",
                type(exc).__name__,
            )
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

        if self._hosted_runtime_paths is not None:
            if self._runtime_paths_match(self._hosted_runtime_paths):
                return True, "recorder secret mode 0600"
            return False, "the recorder credential could not be reverified"
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
        except OSError:
            return False, "the recorder secret could not be secured"
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
            verified_server_version = ""
            approved_versions = sorted(
                self._approved_runtime_versions(JamulusRole.SERVER)
            )
            technical: list[str] = [
                f"approved_versions={','.join(approved_versions) or 'none'}",
                f"udp_port={int(self.settings.jamulus_port)}",
                f"rpc_port={int(self.settings.server_rpc_port)}",
            ]

            try:
                ok, detail = self.ensure_hosted_server()
                started_by_check = not was_owned and self.hosted_server_owned()
                adopted_by_check = (
                    not was_adopted and self.hosted_server_adopted()
                )
                component = (
                    self._active_server_component
                    or self._last_resolved_server_component
                )
                if component is not None:
                    verified_server_version = component.version
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
                LOGGER.error(
                    "Hosted server certification failed (%s).",
                    type(exc).__name__,
                )
                technical.append(
                    f"certification_error_type={type(exc).__name__}"
                )
                lifecycle_detail = (
                    "The band server check failed before it could complete. "
                    "Review the support bundle, then retry Band Check."
                )
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
                        ("server_version="
                        f"{verified_server_version or 'unverified'}"),
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
                    version_label = (
                        verified_server_version or "an approved version"
                    )
                    lifecycle_detail = (
                        f"WebJam started JamulusServer {version_label} on the "
                        "intended audio and control ports, authenticated its "
                        "recorder, then stopped it cleanly and confirmed both "
                        "ports were released."
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

        Mirrors server/start_macos_pilot.sh: central-registry version gate,
        port preflight, 0600 secret, recordings in the server app's sandbox
        container, and a caffeinate power assertion for the server's lifetime
        so the host Mac cannot sleep mid-session.
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

            lease_acquired, lease_detail = (
                self._acquire_runtime_component_lease("server")
            )
            if not lease_acquired:
                return False, lease_detail

            binary, server_source = self.find_jamulus_server_with_source()
            if not binary:
                approved = sorted(
                    self._approved_runtime_versions(JamulusRole.SERVER)
                )
                version_label = " or ".join(approved) or "an approved version"
                self._release_unestablished_server_lease()
                return False, (
                    f"JamulusServer.app {version_label} is not available. "
                    "Downloadable macOS builds include a known-good fallback; "
                    "source builds can use the official app in /Applications. "
                    "Reinstall WebJam or install the server, then press Start "
                    "Audio again."
                )
            server_component = self._last_resolved_server_component
            if (
                server_component is not None
                and str(server_component.executable_path) != binary
            ):
                server_component = None
            if server_component is not None:
                server_component = self._revalidate_runtime_component(
                    server_component
                )
                version = (
                    "unverified"
                    if server_component is None
                    else server_component.version
                )
            else:
                version = default_jamulus_version_probe(binary)
            approved = (
                self._approved_versions_for_resolved_component(server_component)
                if server_component is not None
                else self._approved_runtime_versions(JamulusRole.SERVER)
            )
            if version not in approved:
                version_label = " or ".join(sorted(approved)) or "an approved version"
                self._release_unestablished_server_lease()
                return False, (
                    "WebJam requires an approved JamulusServer.app "
                    f"({version_label}); this copy could not be verified."
                )
            if server_component is None:
                server_component = ResolvedJamulusRuntime(
                    executable_path=Path(binary),
                    role=JamulusRole.SERVER,
                    version=version,
                    source=server_source,
                )
            self._last_resolved_server_component = server_component
            if not self._port_free(udp_port, udp=True):
                self._release_unestablished_server_lease()
                return False, (
                    f"UDP port {udp_port} is already in use by another "
                    "application. Quit it, then press Start Audio again."
                )

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
            if self._secure_macos_runtime_enabled:
                import secrets as _secrets

                try:
                    if not self._release_hosted_runtime_paths(
                        confirmed_stopped=True
                    ):
                        raise SecureRuntimeError(
                            "WebJam could not retire its previous private "
                            "Jamulus credential."
                        )
                    try:
                        prepared_runtime_paths = (
                            self._prepare_owned_runtime_paths(
                                secret_path=secret_path,
                                secret_payload=(
                                    _secrets.token_hex(32) + "\n"
                                ).encode("ascii"),
                                recordings_path=recordings,
                            )
                        )
                    except _JamulusRuntimePreparationError as exc:
                        if exc.retained_state is not None:
                            self._hosted_runtime_paths = exc.retained_state
                        raise
                    self._hosted_runtime_paths = prepared_runtime_paths
                except SecureRuntimeError:
                    self._release_unestablished_server_lease()
                    return False, (
                        "WebJam could not prepare private band-server data. "
                        "Check this Mac's WebJam data permissions, then try "
                        "again."
                    )
            else:
                from core.file_io import atomic_write_text

                try:
                    recordings.mkdir(parents=True, exist_ok=True)
                    secret_path.parent.mkdir(parents=True, exist_ok=True)
                    if not secret_path.is_file() or not secret_path.stat().st_size:
                        import secrets as _secrets

                        atomic_write_text(
                            secret_path,
                            _secrets.token_hex(32) + "\n",
                            mode=0o600,
                        )
                    # Correct an older/manual file with permissive mode before
                    # the server reads it. The recorder credential stays local
                    # to this account even when it already existed.
                    secret_path.chmod(0o600)
                except OSError:
                    self._release_unestablished_server_lease()
                    return False, (
                        "WebJam could not prepare private band-server data. "
                        "Check its data folder, then try again."
                    )

            cmd = [
                binary,
                "--nogui",
                "--port",
                str(udp_port),
            ]
            if remote_host_mode:
                cmd.extend(("--serverbindip", "127.0.0.1"))
            # No --welcomemessage on purpose. Jamulus delivers a server
            # welcome as a chat message, and an arriving chat message makes
            # the musician's Jamulus client pop its Chat window open on top
            # of WebJam every time anyone joins. The banner said nothing the
            # session surface does not already show, so the cost was a
            # stray window and no benefit.
            cmd.extend([
                "--recording", str(recordings), "--norecord",
                "--jsonrpcbindip", "127.0.0.1",
                "--jsonrpcport", str(rpc_port),
                "--jsonrpcsecretfile", str(secret_path),
            ])
            stdout_dest = subprocess.DEVNULL
            hosted_log = None
            try:
                log_dir = Path.home() / "Library" / "Logs" / "WebJam"
                self._close_hosted_log_file()
                if os.name == "posix":
                    with SecureRuntimeDirectory.open(
                        home=Path.home(),
                        directory=log_dir,
                    ) as private_log_directory:
                        hosted_log = open_private_append_text_log(
                            private_log_directory,
                            "jamulus-server.log",
                        )
                else:
                    log_dir.mkdir(parents=True, exist_ok=True)
                    hosted_log = open_private_text_log(
                        log_dir / "jamulus-server.log",
                        append=True,
                    )
                self._hosted_log_file = hosted_log
                stdout_dest = hosted_log
            except (OSError, SecureRuntimeError):
                if hosted_log is not None:
                    try:
                        hosted_log.close()
                    except OSError:
                        pass
            if cancelled():
                self._release_hosted_runtime_paths(confirmed_stopped=True)
                self._release_unestablished_server_lease()
                return False, "Startup was cancelled."
            try:
                child_environment = _jamulus_child_environment(
                    catalog_verified=(
                        server_component.catalog_entry is not None
                    ),
                    executable=binary,
                )
                server_popen_kwargs: dict[str, object] = {
                    "stdout": stdout_dest,
                    "stderr": (
                        subprocess.STDOUT
                        if stdout_dest is not subprocess.DEVNULL
                        else subprocess.DEVNULL
                    ),
                    "env": child_environment,
                }
                if server_component.catalog_entry is not None:
                    server_popen_kwargs["cwd"] = str(
                        server_component.executable_path.parent
                    )
                self._validate_hosted_launch_paths()
                self.hosted_server_process = subprocess.Popen(
                    cmd,
                    **server_popen_kwargs,
                )
                self._validate_hosted_launch_paths()
            except Exception as exc:  # noqa: BLE001
                LOGGER.error(
                    "Hosted band server failed to start (%s).",
                    type(exc).__name__,
                )
                process_stopped = self._terminate_jamulus_child(
                    self.hosted_server_process
                )
                if process_stopped:
                    self.hosted_server_process = None
                    self._release_hosted_runtime_paths(
                        confirmed_stopped=True
                    )
                    self._close_hosted_log_file()
                    self._release_unestablished_server_lease()
                    return False, (
                        "The band server could not start safely. Check the "
                        "WebJam installation and try again."
                    )
                return False, (
                    "The band server changed during startup and WebJam could "
                    "not confirm cleanup. Choose End or Leave again before "
                    "retrying."
                )

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
                    if self._last_resolved_server_component is not None:
                        self._active_server_component = (
                            self._last_resolved_server_component
                        )
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
            LOGGER.warning(
                "caffeinate unavailable (%s).",
                type(exc).__name__,
            )

    def _close_hosted_log_file(self) -> None:
        if self._hosted_log_file is not None:
            try:
                self._hosted_log_file.close()
            except Exception:  # noqa: BLE001
                pass
            self._hosted_log_file = None

    def stop_hosted_server(self) -> bool:
        """Terminate an owned server and report whether it is confirmed stopped."""

        # Signal before waiting on the hosted lifecycle lock. A recovery worker
        # may already be inside ``ensure_hosted_server`` while holding that lock;
        # its cancellation callback must be able to observe Stop and unwind.
        self._cancel_pending_hosted_restart()
        with self._hosted_lifecycle_lock:
            # Detach from an externally managed server without sending it a
            # signal. Only the subprocess in hosted_server_process is owned.
            self._hosted_server_adopted = False
            proc = self.hosted_server_process
            stopped = True
            process_alive = False
            if proc is not None:
                try:
                    process_alive = proc.poll() is None
                except Exception as exc:  # noqa: BLE001
                    LOGGER.warning(
                        "Could not inspect the hosted server (%s); retaining "
                        "its runtime ownership.",
                        type(exc).__name__,
                    )
                    stopped = False
            if stopped and process_alive:
                try:
                    proc.terminate()
                    try:
                        proc.wait(timeout=3.0)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=2.0)
                except Exception as exc:  # noqa: BLE001
                    LOGGER.warning(
                        "Failed to terminate hosted server (%s).",
                        type(exc).__name__,
                    )
                    stopped = False
            if stopped:
                self.hosted_server_process = None
                self._release_hosted_runtime_paths(
                    confirmed_stopped=True
                )
                if not self._hosted_restart_inflight:
                    self._active_server_component = None
                    self._last_resolved_server_component = None
                    self._release_runtime_component_lease("server")
            caff = self._hosted_caffeinate_process
            if stopped:
                self._hosted_caffeinate_process = None
                if caff is not None:
                    try:
                        if caff.poll() is None:
                            caff.terminate()
                    except Exception:  # noqa: BLE001
                        pass
                self._close_hosted_log_file()
            return stopped

    def _cancel_pending_hosted_restart(self) -> None:
        """Retire the currently queued hosted-server recovery, if any."""

        with self._hosted_restart_control_lock:
            pending = self._pending_hosted_restart_cancel
            if pending is not None:
                pending.set()

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
        with self._hosted_restart_control_lock:
            if self._hosted_restart_inflight:
                return
            restart_cancel = threading.Event()
            self._pending_hosted_restart_cancel = restart_cancel
            self._hosted_restart_inflight = True
        LOGGER.warning("Hosted band server died — restarting it")
        self.schedule_ui_callback(
            lambda: self.set_status_banner(
                "Band server stopped unexpectedly — restarting it…",
                color="#BF5700",
            )
        )

        def _restart() -> None:
            def cancelled() -> bool:
                return bool(
                    restart_cancel.is_set()
                    or self.shutdown_requested()
                    or not self.jamulus_launch_intended
                )

            try:
                if cancelled():
                    return
                with self._hosted_lifecycle_lock:
                    if cancelled():
                        return
                    self.hosted_server_process = None
                ok, detail = self.ensure_hosted_server(
                    cancel_requested=cancelled,
                )
                if cancelled():
                    return
                if not ok:
                    LOGGER.error("Hosted server restart failed: %s", detail)
                    self.schedule_ui_callback(
                        lambda: self.set_status_banner(
                            "The band session couldn’t restart. Close WebJam and open it again.",
                            color="#BF5700",
                        )
                    )
            finally:
                with self._hosted_restart_control_lock:
                    if self._pending_hosted_restart_cancel is restart_cancel:
                        self._pending_hosted_restart_cancel = None
                    self._hosted_restart_inflight = False
                with self._hosted_lifecycle_lock:
                    if not self.hosted_server_alive():
                        self._active_server_component = None
                        self._last_resolved_server_component = None
                        self._release_runtime_component_lease("server")

        threading.Thread(
            target=_restart, daemon=True, name="hosted-server-restart",
        ).start()

    def stop_jamulus(
        self,
        *,
        expected_generation: int | None = None,
        expected_process_id: int | None = None,
        expected_launch_request_generation: int | None = None,
    ) -> bool:
        """Terminate the Jamulus process, stop monitoring, and clear reconnect state.

        Returns True only when monitoring and the subprocess are confirmed
        stopped (including an already-stopped subprocess). A failed process
        remains owned so the UI cannot claim cleanup succeeded.

        Supplying an expected generation and PID makes cleanup conditional on
        that exact owned process. A stale timeout then fails closed before it
        can cancel or stop a newer retry.
        """
        expected_identity: tuple[int, int] | None = None
        request_identity = 0
        if expected_launch_request_generation is not None:
            if expected_generation is not None or expected_process_id is not None:
                return False
            try:
                request_identity = int(expected_launch_request_generation)
            except (TypeError, ValueError):
                return False
            if request_identity <= 0:
                return False
        if expected_generation is not None or expected_process_id is not None:
            try:
                generation_value = int(expected_generation)
                process_id_value = int(expected_process_id)
            except (TypeError, ValueError):
                return False
            if generation_value <= 0 or process_id_value <= 0:
                return False
            expected_identity = (generation_value, process_id_value)
        if expected_identity is not None or request_identity > 0:
            # A timeout is allowed to stop only one exact published child.
            # Own the lifecycle before touching intent or a pending token so a
            # replacement cannot publish between validation and cancellation.
            with self._jamulus_lifecycle_lock:
                with self._jamulus_launch_control_lock:
                    pending_launch = self._pending_jamulus_launch_cancel
                    if request_identity > 0:
                        if (
                            self._jamulus_launch_request_generation
                            != request_identity
                        ):
                            return False
                    else:
                        with self._reconnect_lock:
                            current_process = self.jamulus_process
                            if expected_identity != (
                                self._jamulus_process_generation,
                                self._jamulus_process_id(current_process),
                            ):
                                return False
                    self._invalidate_jamulus_launch_callbacks_locked()
                    self.jamulus_launch_intended = False
                    if pending_launch is not None:
                        pending_launch.set()
                    # Keep request ownership locked through every shared-state
                    # mutation. A new manual/Practice request cannot install
                    # its lineage or server while this exact old request is
                    # still being retired.
                    self._cancel_pending_hosted_restart()
                    return self._stop_jamulus_under_lifecycle()

        # An unconditional user Stop remains signal-first so a queued worker
        # exits before Popen, then waits for any in-flight lifecycle owner.
        with self._jamulus_launch_control_lock:
            self._invalidate_jamulus_launch_callbacks_locked()
            self.jamulus_launch_intended = False
            pending_launch = self._pending_jamulus_launch_cancel
            if pending_launch is not None:
                pending_launch.set()
        self._cancel_pending_hosted_restart()
        with self._jamulus_lifecycle_lock:
            return self._stop_jamulus_under_lifecycle()

    def _stop_jamulus_under_lifecycle(self) -> bool:
        """Stop the currently owned client while lifecycle ownership is held."""

        with self._reconnect_lock:
            self._reset_jamulus_recovery_locked()

        monitoring_stopped = True
        try:
            self.jamulus_controller.stop()
        except Exception as exc:
            LOGGER.warning(
                "JamulusController.stop() failed (%s).",
                type(exc).__name__,
            )
            monitoring_stopped = False

        proc = self.jamulus_process
        process_stopped = self._terminate_jamulus_child(proc)

        with self._reconnect_lock:
            if process_stopped:
                self.jamulus_process = None
                self._jamulus_process_started_at = 0.0
                self._jamulus_process_generation = 0
                self._jamulus_process_recovery_generation = 0
                self._jamulus_native_setup_deadline = 0.0
                self._jamulus_native_setup_process_generation = 0

        if self.jamulus_process is None:
            self._close_jamulus_log_file()
        practice_stopped = self._terminate_practice_server()
        self.practice_mode = False
        stopped = monitoring_stopped and process_stopped and practice_stopped
        if stopped:
            self._release_client_runtime_paths(confirmed_stopped=True)
            self._active_native_profile = None
            self._active_client_component = None
            self._last_resolved_client_component = None
            self._set_live_audio_route_owned(False)
            self._release_runtime_component_lease("client")
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

    def _begin_webex_launch(self) -> int | None:
        with self._webex_launch_lock:
            if self._webex_launch_inflight:
                return None
            self._webex_launch_generation += 1
            self._webex_launch_inflight = True
            self.webex_state = WebexLaunchState.OPENING.value
            return self._webex_launch_generation

    def _finish_webex_launch(self) -> None:
        with self._webex_launch_lock:
            self._webex_launch_inflight = False

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
        """Open the configured meeting link and report only the handoff result.

        ``reconnect`` remains in the signature for one compatibility cycle but
        is intentionally ignored: WebJam cannot observe an external meeting
        disconnect and therefore must not invent reconnection behavior.
        """
        if self.shutdown_requested():
            return

        launch_url = str(getattr(self.settings, "webex_url", "") or "").strip()
        service_name = _meeting_service_name(launch_url)
        launch_generation = self._begin_webex_launch()
        if launch_generation is None:
            if manual:
                self.set_status_banner(
                    f"{service_name} is already opening externally."
                    if service_name
                    else "The meeting link is already opening externally."
                )
            return False
            
        if manual:
            self.metrics_service.increment("metric_webex_open_attempt")
            
        self.set_status_banner(
            f"Opening {service_name} externally…"
            if service_name
            else "Opening the meeting link externally…",
            color="#BF5700",
        )
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

                if not self.webex_controller.join_meeting_url(launch_url):
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
                    lambda: self.webex_event(
                        "meeting-handoff",
                        "opened-externally",
                    ),
                )
                    
                self._schedule_webex_ui_if_current(
                    launch_generation,
                    self.refresh_readiness,
                )
                if manual:
                    self._schedule_webex_ui_if_current(
                        launch_generation,
                        lambda: self.set_status_banner(
                            (
                                "Opened externally—finish joining in "
                                f"{service_name}."
                            )
                            if service_name
                            else (
                                "Opened externally—finish joining in your "
                                "meeting service."
                            )
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
                LOGGER.warning(
                    "External meeting launch failed: %s",
                    type(exc).__name__,
                )
                self.metrics_service.increment("metric_webex_open_failed")
                self._schedule_webex_ui_if_current(
                    launch_generation,
                    lambda: self.webex_event(
                        "meeting-handoff",
                        "open-failed",
                    ),
                )
                self._schedule_webex_ui_if_current(
                    launch_generation,
                    self.refresh_readiness,
                )
                self._schedule_webex_ui_if_current(
                    launch_generation,
                    lambda: self.show_actionable_error(
                        f"{service_name} Open Failed"
                        if service_name
                        else "Meeting Link Open Failed",
                        what_failed=(
                            f"The configured {service_name} meeting could not "
                            "be opened."
                            if service_name
                            else (
                                "The configured meeting link could not be "
                                "opened."
                            )
                        ),
                        likely_cause=(
                            "Default browser issue, network filtering, invalid "
                            "meeting URL, or transient launch issue."
                        ),
                        next_action=(
                            "Open Settings, verify the meeting link, then try "
                            "again."
                        ),
                        retry_callback=lambda: self.launch_webex(manual=True),
                        copy_text=launch_url,
                    )
                )
            finally:
                self._finish_webex_launch()

        try:
            threading.Thread(target=_do_open, daemon=True).start()
        except Exception:
            self._finish_webex_launch()
            self._publish_webex_state_if_current(
                launch_generation,
                WebexLaunchState.OPEN_FAILED,
            )
            raise
        return True

    def _reconnect_delay_seconds(self, attempts: int) -> float:
        """Calculate exponential backoff delay."""
        # Constants from main app for consistency
        RECONNECT_BASE_DELAY_SECONDS = 1.5
        RECONNECT_MAX_DELAY_SECONDS = 45.0
        
        delay = RECONNECT_BASE_DELAY_SECONDS * (2 ** (attempts - 1))
        return min(delay, RECONNECT_MAX_DELAY_SECONDS)

    @staticmethod
    def _jamulus_process_alive(process: object | None) -> bool:
        if process is None:
            return False
        try:
            return process.poll() is None
        except AttributeError:
            return False
        except Exception as exc:  # noqa: BLE001 - unknown ownership fails closed
            # A failed poll is not evidence that an owned child exited. Treat
            # it as live/unknown so reconnect cannot launch a second client or
            # release the component lease beside an untracked process.
            LOGGER.warning(
                "Could not determine whether the owned Jamulus process exited; "
                "retaining ownership (%s).",
                type(exc).__name__,
            )
            return True

    @staticmethod
    def _jamulus_process_poll_evidence(
        process: object | None,
    ) -> bool | None:
        """Return positive process evidence for user-visible activation.

        Lifecycle ownership deliberately treats a failed ``poll()`` as
        live/unknown so WebJam cannot launch a second client beside an
        untracked child. Foregrounding has a stricter trust boundary: an
        exception is not proof that the stored PID still belongs to this
        process, so callers must refuse AppKit activation.
        """

        if process is None:
            return False
        try:
            return process.poll() is None
        except AttributeError:
            return False
        except Exception:  # noqa: BLE001 - missing proof fails closed
            return None

    @staticmethod
    def _jamulus_process_id(process: object | None) -> int:
        if process is None:
            return 0
        try:
            process_id = int(process.pid)
        except (AttributeError, TypeError, ValueError, OSError):
            return 0
        return max(0, process_id)

    @classmethod
    def _terminate_jamulus_child(
        cls,
        process: object | None,
        *,
        timeout: float = 2.0,
    ) -> bool:
        """Best-effort terminate/kill with fail-closed ownership truth."""

        if process is None:
            return True
        try:
            if process.poll() is not None:
                return True
        except Exception as exc:  # noqa: BLE001 - unknown is not stopped
            LOGGER.warning(
                "Could not inspect the owned Jamulus process before cleanup (%s).",
                type(exc).__name__,
            )
            return False

        try:
            process.terminate()
        except Exception as exc:  # noqa: BLE001 - attempt hard fallback
            LOGGER.warning(
                "Jamulus terminate failed; attempting the bounded kill fallback "
                "(%s).",
                type(exc).__name__,
            )
        else:
            try:
                process.wait(timeout=timeout)
                # Popen.wait() returning normally is the authoritative reap
                # boundary; a subsequent poll is unnecessary and makes test
                # doubles (or unusual platform wrappers) look live forever.
                return True
            except subprocess.TimeoutExpired:
                pass
            except Exception as exc:  # noqa: BLE001 - verify/kill below
                LOGGER.warning(
                    "Jamulus did not confirm termination; attempting kill (%s).",
                    type(exc).__name__,
                )

        try:
            if process.poll() is not None:
                return True
        except Exception:  # noqa: BLE001 - unknown is not stopped
            return False

        try:
            process.kill()
            process.wait(timeout=timeout)
            return True
        except Exception as exc:  # noqa: BLE001 - retain owned process
            LOGGER.error(
                "Jamulus could not be stopped; retaining the owned process for "
                "an explicit cleanup retry (%s).",
                type(exc).__name__,
            )
            return False

    def _reset_jamulus_recovery_locked(self) -> None:
        """Retire recovery state; caller must hold ``_reconnect_lock``."""

        self.jamulus_reconnect_attempts = 0
        self.jamulus_next_reconnect_at = 0.0
        self.jamulus_reconnect_inflight = False
        self._jamulus_recovery_active = False
        self._jamulus_recovery_exhausted = False
        self._jamulus_process_recovery_generation = 0

    def _terminalize_jamulus_recovery(self) -> None:
        """Publish a finite failed recovery that requires ordered cleanup.

        Some retry failures cannot become healthy on another timer tick (for
        example, a missing saved session, an unavailable runtime lease, or an
        invalid native profile).  Clearing launch intent alone used to strand
        the application in ``active=True, exhausted=False`` forever because
        Bridge would refuse another attempt and the UI had no terminal fact to
        consume.  Keep the recovery generation active, mark it exhausted, and
        clear intent atomically in the documented control→reconnect lock order.
        """

        with self._jamulus_launch_control_lock:
            self._terminalize_jamulus_recovery_locked()

    def _terminalize_jamulus_recovery_locked(self) -> None:
        """Publish terminal recovery while the caller holds launch control."""

        self.jamulus_launch_intended = False
        with self._reconnect_lock:
            self._begin_jamulus_recovery_locked()
            self.jamulus_reconnect_inflight = False
            self._jamulus_recovery_exhausted = True

    def _begin_jamulus_recovery_locked(self) -> int:
        """Open or return one bounded recovery generation under the lock."""

        if not self._jamulus_recovery_active:
            self._jamulus_recovery_generation += 1
            self._jamulus_recovery_active = True
            self._jamulus_recovery_exhausted = False
        return self._jamulus_recovery_generation

    def _finish_jamulus_reconnect_attempt_locked(
        self,
        *,
        failed: bool,
    ) -> None:
        """Close one attempt and publish terminal exhaustion when proven."""

        self.jamulus_reconnect_inflight = False
        if (
            failed
            and self._jamulus_recovery_active
            and self.jamulus_reconnect_attempts >= RECONNECT_MAX_ATTEMPTS
        ):
            self._jamulus_recovery_exhausted = True

    def _jamulus_rpc_monitor_snapshot(
        self,
        *,
        process_generation: int,
        process_id: int,
    ) -> JamulusRpcMonitorSnapshot | None:
        """Return evidence only from the exact process-bound monitor epoch."""

        if process_generation <= 0 or process_id <= 0:
            return None
        provider = getattr(
            self.jamulus_controller,
            "rpc_monitor_snapshot_for",
            None,
        )
        if not callable(provider):
            return None
        try:
            snapshot = provider(
                process_generation=process_generation,
                process_id=process_id,
            )
        except Exception:  # noqa: BLE001 - supervision evidence fails closed
            return None
        if not isinstance(snapshot, JamulusRpcMonitorSnapshot):
            return None
        identity = snapshot.identity
        if (
            not identity.is_process_bound
            or identity.process_generation != process_generation
            or identity.process_id != process_id
        ):
            return None
        return snapshot

    def _jamulus_rpc_observation(
        self,
        *,
        process_alive: bool,
        process_started_at: float,
        process_generation: int = 0,
        process_id: int = 0,
        now: float,
    ) -> tuple[JamulusRpcFreshness, float | None]:
        """Classify RPC without treating unknown startup state as success."""

        monitor = self._jamulus_rpc_monitor_snapshot(
            process_generation=process_generation,
            process_id=process_id,
        )
        freshness, age = self._classify_jamulus_rpc_observation(
            process_alive=process_alive,
            process_started_at=process_started_at,
            monitor=monitor,
            now=now,
        )
        if (
            freshness is JamulusRpcFreshness.STALE
            and process_alive
            and process_generation > 0
            and process_generation == self._jamulus_native_setup_process_generation
            and now < self._jamulus_native_setup_deadline
        ):
            # Native device setup can legitimately precede the first
            # authenticated RPC interaction.  This remains STARTING—not
            # healthy—and is bound to one exact process generation plus an
            # absolute deadline.
            freshness = JamulusRpcFreshness.STARTING
        return freshness, age

    @staticmethod
    def _classify_jamulus_rpc_observation(
        *,
        process_alive: bool,
        process_started_at: float,
        monitor: JamulusRpcMonitorSnapshot | None,
        now: float,
    ) -> tuple[JamulusRpcFreshness, float | None]:
        """Classify one already-captured monitor snapshot consistently."""

        if not process_alive:
            return JamulusRpcFreshness.NO_PROCESS, None

        age = (
            monitor.last_activity_age_seconds
            if monitor is not None
            else None
        )
        activity_at = (
            monitor.last_activity_at
            if monitor is not None
            else None
        )
        usable_age = bool(
            not isinstance(age, bool)
            and isinstance(age, (int, float))
            and math.isfinite(float(age))
            and float(age) >= 0.0
        )
        activity_belongs_after_process_start = bool(
            not isinstance(activity_at, bool)
            and isinstance(activity_at, (int, float))
            and math.isfinite(float(activity_at))
            and float(activity_at) > 0.0
            and (
                process_started_at <= 0.0
                or float(activity_at) >= process_started_at
            )
        )
        rpc_available = bool(
            monitor is not None
            and monitor.running
            and monitor.available
            and monitor.authenticated
            and usable_age
            and activity_belongs_after_process_start
        )
        safe_age = float(age) if usable_age else None
        if rpc_available and safe_age is not None:
            freshness = (
                JamulusRpcFreshness.FRESH
                if safe_age <= RECONNECT_HANG_THRESHOLD_SECONDS
                else JamulusRpcFreshness.STALE
            )
            return freshness, safe_age

        # ``last_activity_age`` is intentionally infinite until the first
        # authenticated interaction. Give a newly published process one
        # bounded chance to start its monitor before unknown becomes stale.
        if process_started_at > 0.0:
            # A launch worker can publish a new process after the caller has
            # sampled ``now`` but before this exact process snapshot is read.
            # Clamp that legitimate publication race to age zero; the grace
            # remains bounded by the next observation from the shared clock.
            startup_age = max(0.0, now - process_started_at)
            if startup_age < RECONNECT_RPC_STARTUP_GRACE_SECONDS:
                return JamulusRpcFreshness.STARTING, safe_age
        return JamulusRpcFreshness.STALE, safe_age

    def _jamulus_process_is_stalled(self) -> bool:
        """Return True only after live-process RPC is provably stale."""

        proc = self.jamulus_process
        process_alive = self._jamulus_process_alive(proc)
        freshness, _age = self._jamulus_rpc_observation(
            process_alive=process_alive,
            process_started_at=self._jamulus_process_started_at,
            process_generation=self._jamulus_process_generation,
            process_id=self._jamulus_process_id(proc),
            now=time.monotonic(),
        )
        return freshness is JamulusRpcFreshness.STALE

    def jamulus_recovery_snapshot(
        self,
        *,
        now: float | None = None,
    ) -> JamulusRecoverySnapshot:
        """Return one immutable snapshot for UI/lifecycle integration."""

        observed_at = time.monotonic() if now is None else float(now)
        with self._jamulus_launch_control_lock:
            pending_request = self._pending_jamulus_launch_cancel
            pending = pending_request is not None
            launch_request_generation = (
                self._jamulus_launch_request_generation
            )
            launch_intended = self.jamulus_launch_intended
            with self._reconnect_lock:
                process = self.jamulus_process
                process_started_at = self._jamulus_process_started_at
                generation = self._jamulus_process_generation
                recovery_generation = self._jamulus_recovery_generation
                active = self._jamulus_recovery_active
                attempts = self.jamulus_reconnect_attempts
                inflight = self.jamulus_reconnect_inflight
                exhausted = self._jamulus_recovery_exhausted
                next_attempt_at = self.jamulus_next_reconnect_at
                native_setup_generation = (
                    self._jamulus_native_setup_process_generation
                )
                native_setup_deadline = self._jamulus_native_setup_deadline
        process_alive = self._jamulus_process_alive(process)
        process_id = self._jamulus_process_id(process)
        monitor = self._jamulus_rpc_monitor_snapshot(
            process_generation=generation,
            process_id=process_id,
        )
        freshness, age = self._classify_jamulus_rpc_observation(
            process_alive=process_alive,
            process_started_at=process_started_at,
            monitor=monitor,
            now=observed_at,
        )
        if (
            freshness is JamulusRpcFreshness.STALE
            and process_alive
            and generation > 0
            and generation == native_setup_generation
            and observed_at < native_setup_deadline
        ):
            freshness = JamulusRpcFreshness.STARTING
        monitor_epoch = (
            int(monitor.identity.monitor_epoch)
            if monitor is not None
            else 0
        )
        native_setup_grace_configured = bool(
            native_setup_deadline > 0.0
            and observed_at < native_setup_deadline
        )
        native_setup_grace_active = bool(
            process_alive
            and generation > 0
            and generation == native_setup_generation
            and observed_at < native_setup_deadline
        )
        return JamulusRecoverySnapshot(
            generation=generation,
            recovery_generation=recovery_generation,
            launch_intended=launch_intended,
            pending=pending,
            active=active,
            attempts_started=attempts,
            max_attempts=RECONNECT_MAX_ATTEMPTS,
            inflight=inflight,
            exhausted=exhausted,
            next_attempt_at=next_attempt_at,
            process_id=process_id,
            process_alive=process_alive,
            rpc_freshness=freshness,
            rpc_age_seconds=age,
            launch_request_generation=launch_request_generation,
            rpc_monitor_epoch=monitor_epoch,
            native_setup_grace_configured=native_setup_grace_configured,
            native_setup_grace_active=native_setup_grace_active,
        )

    def mark_jamulus_reconnect_authenticated(
        self,
        *,
        generation: int,
        process_id: int,
    ) -> bool:
        """Retire recovery only for fresh RPC on its current generation/PID."""

        try:
            expected_generation = int(generation)
            expected_process_id = int(process_id)
        except (TypeError, ValueError):
            return False
        if expected_generation <= 0 or expected_process_id <= 0:
            return False

        with self._jamulus_launch_control_lock:
            if self._pending_jamulus_launch_cancel is not None:
                return False
            with self._reconnect_lock:
                process = self.jamulus_process
                if (
                    not self._jamulus_recovery_active
                    or self.jamulus_reconnect_inflight
                    or expected_generation != self._jamulus_process_generation
                    or self._jamulus_recovery_generation
                    != self._jamulus_process_recovery_generation
                    or expected_process_id != self._jamulus_process_id(process)
                ):
                    return False
                process_alive = self._jamulus_process_alive(process)
                freshness, _age = self._jamulus_rpc_observation(
                    process_alive=process_alive,
                    process_started_at=self._jamulus_process_started_at,
                    process_generation=self._jamulus_process_generation,
                    process_id=self._jamulus_process_id(process),
                    now=time.monotonic(),
                )
                if freshness is not JamulusRpcFreshness.FRESH:
                    return False
                self._reset_jamulus_recovery_locked()
        self.metrics_service.increment("metric_jamulus_reconnect_success")
        return True

    def finish_native_sound_setup(
        self,
        *,
        generation: int,
        process_id: int,
    ) -> bool:
        """Retire first-run grace only for the exact current process identity.

        The controller calls this after authenticated client RPC and the local
        roster row are both proven.  It does not itself establish connection
        truth; it only restores ordinary hung-process supervision.
        """

        try:
            expected_generation = int(generation)
            expected_process_id = int(process_id)
        except (TypeError, ValueError):
            return False
        if expected_generation <= 0 or expected_process_id <= 0:
            return False
        observed_at = time.monotonic()
        with self._reconnect_lock:
            process = self.jamulus_process
            if (
                expected_generation != self._jamulus_process_generation
                or expected_generation != self._jamulus_native_setup_process_generation
                or expected_process_id != self._jamulus_process_id(process)
                or not self._jamulus_process_alive(process)
                or self._jamulus_native_setup_deadline <= observed_at
            ):
                return False
            self._jamulus_native_setup_deadline = 0.0
            self._jamulus_native_setup_process_generation = 0
        return True

    def attempt_auto_reconnects(self):
        """Auto-reconnect tick — retries dropped or stalled Jamulus processes.

        Called every ~3 seconds from `ApplicationController._on_reconnect_tick`.
        Per service:

        - **Jamulus**: if `jamulus_launch_intended=True` (user clicked Launch
          Audio at some point and didn't click Stop), and the subprocess has
          died (`poll() is not None`) or is unresponsive for too long, it
          schedules a relaunch with exponential
          backoff (cap 5 attempts, 45s max delay).
        The retired Tk application exposed an ``auto_reconnect_enabled``
        preference, but the current guided session has no matching control and
        relies on bounded recovery for truthful lifecycle state. Ignore that
        legacy persisted bit rather than leaving modern sessions permanently
        in "Reconnecting". Both retries set ``*_inflight=True`` to prevent
        double-fire while a relaunch worker thread is in flight.
        """
        if self.shutdown_requested():
            return

        self.attempt_hosted_server_recovery()
            
        if self._end_practice_if_server_died():
            return

        now = time.monotonic()
        self._attempt_auto_reconnect_jamulus(now)

    def attempt_hosted_server_recovery(self) -> None:
        """Supervise only the hosted server, independent of client policy."""

        if self.shutdown_requested():
            return
        self._restart_hosted_server_if_died()

    def _attempt_auto_reconnect_jamulus(self, now: float):
        # A launch request exists before its worker publishes a process. A
        # hosted server can make that window longer than the three-second
        # reconnect interval, so treat the request token as authoritative
        # in-flight work. ``launch_jamulus`` repeats this check atomically to
        # close the race between this snapshot and the eventual call below.
        with self._jamulus_launch_control_lock:
            if self._pending_jamulus_launch_cancel is not None:
                return

        with self._reconnect_lock:
            if not self.jamulus_launch_intended:
                return
            process = self.jamulus_process
            process_started_at = self._jamulus_process_started_at
            process_generation = self._jamulus_process_generation
            process_recovery_generation = (
                self._jamulus_process_recovery_generation
            )
            recovery_generation = self._jamulus_recovery_generation
            recovery_active = self._jamulus_recovery_active
            native_setup_generation = (
                self._jamulus_native_setup_process_generation
            )
            native_setup_deadline = self._jamulus_native_setup_deadline

        process_alive = self._jamulus_process_alive(process)
        process_id = self._jamulus_process_id(process)
        rpc_freshness, _rpc_age = self._jamulus_rpc_observation(
            process_alive=process_alive,
            process_started_at=process_started_at,
            process_generation=process_generation,
            process_id=process_id,
            now=now,
        )
        native_setup_active = bool(
            process_alive
            and process_generation > 0
            and process_generation == native_setup_generation
            and now < native_setup_deadline
        )
        local_roster_auth_timed_out = bool(
            process_alive
            and not native_setup_active
            and recovery_active
            and process_recovery_generation > 0
            and process_recovery_generation == recovery_generation
            and process_started_at > 0.0
            and (
                now - process_started_at
                >= RECONNECT_LOCAL_ROSTER_GRACE_SECONDS
            )
        )

        with self._reconnect_lock:
            # Stop, a completed worker, or a newer request may have replaced
            # the process while RPC was observed. The next tick will classify
            # that newer truth; this one must not mutate it.
            if (
                not self.jamulus_launch_intended
                or self.jamulus_process is not process
                or self._jamulus_process_generation != process_generation
            ):
                return

            if process_alive and (
                rpc_freshness is JamulusRpcFreshness.STARTING
                or (
                    rpc_freshness is JamulusRpcFreshness.FRESH
                    and not local_roster_auth_timed_out
                )
            ):
                # A fresh Popen remains part of the active recovery until the
                # application acknowledges authenticated RPC and the local
                # participant for this exact generation/PID.
                return

            if process is None and not recovery_active:
                # Initial launch has never published an owned process. Its own
                # worker/actionable error remains the sole launch owner.
                return

            generation = self._begin_jamulus_recovery_locked()

            if self.jamulus_reconnect_inflight:
                return

            if self.jamulus_reconnect_attempts >= RECONNECT_MAX_ATTEMPTS:
                self._jamulus_recovery_exhausted = True
                return

            if now < self.jamulus_next_reconnect_at:
                return

            self.jamulus_reconnect_attempts += 1
            self.jamulus_next_reconnect_at = (
                now
                + self._reconnect_delay_seconds(
                    self.jamulus_reconnect_attempts
                )
            )
            self.jamulus_reconnect_inflight = True
            self._jamulus_recovery_exhausted = False
            force_restart = process_alive and (
                rpc_freshness is JamulusRpcFreshness.STALE
                or local_roster_auth_timed_out
            )
        self.metrics_service.increment("metric_jamulus_reconnect_attempt")
        try:
            reconnect_kwargs: dict[str, bool] = {
                "manual": False,
                "reconnect": True,
                "force_restart": force_restart,
            }
            if self.practice_mode:
                reconnect_kwargs["practice_request"] = True
            self.launch_jamulus(**reconnect_kwargs)
        except Exception as exc:  # noqa: BLE001 - keep bounded retry owner live
            LOGGER.error(
                "Jamulus reconnect generation %s could not schedule attempt "
                "%s (%s).",
                generation,
                self.jamulus_reconnect_attempts,
                type(exc).__name__,
            )
            with self._reconnect_lock:
                if generation == self._jamulus_recovery_generation:
                    self._finish_jamulus_reconnect_attempt_locked(failed=True)
