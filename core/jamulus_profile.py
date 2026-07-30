"""Jamulus-native profile and restart-readiness primitives for WebJam.

WebJam v0.16 deliberately does **not** select live audio hardware, channels,
or buffers.  Those choices belong to Jamulus's own sound setup.  This module
only gives WebJam a dedicated Jamulus profile name to launch with and a tiny
piece of restart-safe readiness evidence.

WebJam only supplies a dedicated filename and safe WebJam-owned working
directory.  The verified macOS component used by the integrated session is
non-sandboxed and resolves that filename relative to the supplied working
directory.  WebJam validates the resulting private file but never writes an
audio-device, channel, or buffer setting (or any Jamulus profile content at
all).  Jamulus creates and owns every setting in it.  The musician's normal
``Jamulus.ini`` remains untouched.

The readiness file is intentionally small and private.  It records only the
role, a one-way profile fingerprint, the Jamulus version, and whether a human
has confirmed sound.  It never stores an invite, Webex details, device data,
or filesystem paths.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass, field, replace
from enum import Enum
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys
from typing import Callable, Iterable, Mapping, NoReturn
from xml.etree import ElementTree

from core.file_io import atomic_write_text
from core.jamulus_child_environment import (
    JamulusChildEnvironmentError,
    sanitized_jamulus_child_environment,
)
from core.secure_runtime import SecureRuntimeDirectory, SecureRuntimeError


WEBJAM_NATIVE_PROFILE_FILENAME = "WebJam-native-v0.16.ini"
"""Dedicated Jamulus profile; never the musician's normal ``Jamulus.ini``."""

JAMULUS_CONTAINER_ID = "app.jamulussoftware.Jamulus"
PINNED_JAMULUS_VERSION = "3.12.2"
_PROFILE_FINGERPRINT_SCHEMA = 1
_MAX_PROFILE_BYTES = 4 * 1024 * 1024
_MAX_READINESS_BYTES = 16 * 1024
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")
_FINGERPRINT_RE = re.compile(r"^[0-9a-f]{64}$")


class JamulusNativeProfileError(RuntimeError):
    """A musician-safe failure preparing the native Jamulus profile."""


class NativeProfileAccess(str, Enum):
    """Whether WebJam may inspect the file represented by ``profile_path``."""

    WEBJAM_READABLE = "webjam_readable"


class StartupRole(str, Enum):
    """The only two roles that can reuse startup readiness."""

    HOST = "host"
    GUEST = "guest"


class StartupServerPhase(str, Enum):
    """Server lifecycle truth that is safe to retain across a restart."""

    NOT_REQUIRED = "not_required"
    STARTING = "starting"
    READY = "ready"
    RECOVERING = "recovering"
    FAILED = "failed"


class StartupClientPhase(str, Enum):
    """The musician-facing stage of the native Jamulus startup journey."""

    NOT_STARTED = "not_started"
    LAUNCHING = "launching"
    NATIVE_SOUND_SETUP = "native_sound_setup"
    VERIFYING = "verifying"
    READY = "ready"
    RECOVERING = "recovering"
    FAILED = "failed"


class StartupConnectionState(str, Enum):
    """Connection truth, distinct from setup and human audibility."""

    NOT_STARTED = "not_started"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    FAILED = "failed"


class StartupWebexDecision(str, Enum):
    """Optional conversation decision; never a Webex URL or join claim."""

    NOT_DECIDED = "not_decided"
    SKIPPED = "skipped"
    OPEN_REQUESTED = "open_requested"


class StartupNextAction(str, Enum):
    """Safe, declarative restart action with no free-form external details."""

    WAIT_FOR_SERVER = "wait_for_server"
    OPEN_JAMULUS = "open_jamulus"
    FINISH_SOUND_SETUP = "finish_sound_setup"
    CONFIRM_AUDIBLE = "confirm_audible"
    COPY_INVITE = "copy_invite"
    ENTER_JAM = "enter_jam"
    OPTIONAL_WEBEX = "optional_webex"
    RETRY = "retry"
    NONE = "none"


@dataclass(frozen=True, slots=True)
class JamulusNativeProfilePlan:
    """Frozen launch facts for one Jamulus-native client lifecycle.

    ``working_directory`` and ``profile_path`` are process-local launch
    details.  They are deliberately not part of ``profile_fingerprint`` and
    must never be written to the readiness record.  On macOS,
    ``profile_path`` is the real dedicated profile inside WebJam's private
    Application Support launch directory.  It is validated as a bounded
    regular file while Jamulus alone owns its settings.
    """

    profile_filename: str
    arguments: tuple[str, ...]
    working_directory: Path
    profile_path: Path
    profile_fingerprint: str
    jamulus_version: str
    profile_exists: bool
    working_directory_device: int
    working_directory_inode: int
    environment: Mapping[str, str] = field(default_factory=dict)
    profile_access: NativeProfileAccess | str = NativeProfileAccess.WEBJAM_READABLE
    _directory_runtime: SecureRuntimeDirectory | None = field(
        default=None,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        if Path(self.profile_filename).name != self.profile_filename:
            raise ValueError("profile_filename must be a filename")
        if self.arguments != ("--inifile", self.profile_filename):
            raise ValueError("Jamulus profile arguments must use filename-only --inifile")
        if not _FINGERPRINT_RE.fullmatch(self.profile_fingerprint):
            raise ValueError("profile_fingerprint must be a SHA-256 hex digest")
        _normalize_version(self.jamulus_version)
        if (
            isinstance(self.working_directory_device, bool)
            or isinstance(self.working_directory_inode, bool)
            or int(self.working_directory_device) <= 0
            or int(self.working_directory_inode) <= 0
        ):
            raise ValueError("working directory identity must be positive")
        object.__setattr__(
            self,
            "profile_access",
            NativeProfileAccess(self.profile_access),
        )
        if self._directory_runtime is not None and (
            self._directory_runtime.path != self.working_directory
            or not self._directory_runtime.path_matches()
            or self._directory_runtime.proof.device
            != int(self.working_directory_device)
            or self._directory_runtime.proof.inode
            != int(self.working_directory_inode)
        ):
            raise ValueError("profile runtime does not match working directory")

    def readiness_record(
        self,
        role: StartupRole | str,
        *,
        human_confirmed: bool,
    ) -> StartupReadinessRecord:
        """Build the deliberately path-free record used for restart recovery."""

        return StartupReadinessRecord(
            role=role,
            profile_fingerprint=self.profile_fingerprint,
            jamulus_version=self.jamulus_version,
            human_confirmed=human_confirmed,
        )


@dataclass(frozen=True, slots=True)
class StartupReadinessRecord:
    """Durable human sound confirmation without sensitive startup state."""

    role: StartupRole | str
    profile_fingerprint: str
    jamulus_version: str
    human_confirmed: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "role", StartupRole(self.role))
        fingerprint = str(self.profile_fingerprint or "").strip().lower()
        if not _FINGERPRINT_RE.fullmatch(fingerprint):
            raise ValueError("profile_fingerprint must be a SHA-256 hex digest")
        object.__setattr__(self, "profile_fingerprint", fingerprint)
        object.__setattr__(self, "jamulus_version", _normalize_version(self.jamulus_version))
        if not isinstance(self.human_confirmed, bool):
            raise ValueError("human_confirmed must be a boolean")

    def to_mapping(self) -> dict[str, object]:
        """Return exactly the fields approved for durable startup recovery."""

        return {
            "role": self.role.value,
            "profile_fingerprint": self.profile_fingerprint,
            "jamulus_version": self.jamulus_version,
            "human_confirmed": self.human_confirmed,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> StartupReadinessRecord:
        expected = {
            "role",
            "profile_fingerprint",
            "jamulus_version",
            "human_confirmed",
        }
        if set(value) != expected:
            raise ValueError("readiness record has unsupported fields")
        return cls(
            role=str(value["role"]),
            profile_fingerprint=str(value["profile_fingerprint"]),
            jamulus_version=str(value["jamulus_version"]),
            human_confirmed=value["human_confirmed"],  # type: ignore[arg-type]
        )

    def matches(self, plan: JamulusNativeProfilePlan, role: StartupRole | str) -> bool:
        """Whether this is reusable evidence for this exact native profile."""

        return bool(
            self.human_confirmed
            and self.role is StartupRole(role)
            and self.profile_fingerprint == plan.profile_fingerprint
            and self.jamulus_version == plan.jamulus_version
        )


@dataclass(frozen=True, slots=True)
class StartupAttemptRecord:
    """Private, allowlisted recovery state for one host or guest attempt.

    This is deliberately an operational sketch, not a session record.  It
    keeps no invite or server address, no device data, no filesystem path, and
    no claim that Webex was joined or configured.
    """

    attempt_id: str
    generation: int
    role: StartupRole | str
    server_phase: StartupServerPhase | str
    client_phase: StartupClientPhase | str
    profile_fingerprint: str
    connection_state: StartupConnectionState | str
    human_confirmed: bool
    webex_decision: StartupWebexDecision | str | None
    next_action: StartupNextAction | str

    def __post_init__(self) -> None:
        attempt_id = str(self.attempt_id or "").strip().lower()
        if not _FINGERPRINT_RE.fullmatch(attempt_id):
            raise ValueError("attempt_id must be a SHA-256 hex digest")
        object.__setattr__(self, "attempt_id", attempt_id)
        if isinstance(self.generation, bool) or not isinstance(self.generation, int):
            raise ValueError("generation must be an integer")
        if not 0 <= self.generation <= 2**31 - 1:
            raise ValueError("generation is out of range")
        object.__setattr__(self, "role", StartupRole(self.role))
        object.__setattr__(self, "server_phase", StartupServerPhase(self.server_phase))
        object.__setattr__(self, "client_phase", StartupClientPhase(self.client_phase))
        fingerprint = str(self.profile_fingerprint or "").strip().lower()
        if not _FINGERPRINT_RE.fullmatch(fingerprint):
            raise ValueError("profile_fingerprint must be a SHA-256 hex digest")
        object.__setattr__(self, "profile_fingerprint", fingerprint)
        object.__setattr__(
            self,
            "connection_state",
            StartupConnectionState(self.connection_state),
        )
        if not isinstance(self.human_confirmed, bool):
            raise ValueError("human_confirmed must be a boolean")
        if self.webex_decision is not None:
            object.__setattr__(
                self,
                "webex_decision",
                StartupWebexDecision(self.webex_decision),
            )
        object.__setattr__(self, "next_action", StartupNextAction(self.next_action))

    @classmethod
    def new(
        cls,
        *,
        generation: int,
        role: StartupRole | str,
        server_phase: StartupServerPhase | str,
        client_phase: StartupClientPhase | str,
        profile_fingerprint: str,
        connection_state: StartupConnectionState | str,
        human_confirmed: bool,
        webex_decision: StartupWebexDecision | str | None,
        next_action: StartupNextAction | str,
        entropy: bytes | None = None,
    ) -> StartupAttemptRecord:
        """Create a digest-only attempt identifier without retaining entropy."""

        normalized_role = StartupRole(role)
        fingerprint = str(profile_fingerprint or "").strip().lower()
        if not _FINGERPRINT_RE.fullmatch(fingerprint):
            raise ValueError("profile_fingerprint must be a SHA-256 hex digest")
        if isinstance(generation, bool) or not isinstance(generation, int):
            raise ValueError("generation must be an integer")
        nonce = secrets.token_bytes(32) if entropy is None else bytes(entropy)
        attempt_id = _attempt_digest(
            generation=generation,
            role=normalized_role,
            profile_fingerprint=fingerprint,
            entropy=nonce,
        )
        return cls(
            attempt_id=attempt_id,
            generation=generation,
            role=normalized_role,
            server_phase=server_phase,
            client_phase=client_phase,
            profile_fingerprint=fingerprint,
            connection_state=connection_state,
            human_confirmed=human_confirmed,
            webex_decision=webex_decision,
            next_action=next_action,
        )

    def to_mapping(self) -> dict[str, object]:
        """Return exactly the durable recovery fields approved for v0.16."""

        return {
            "attempt_id": self.attempt_id,
            "generation": self.generation,
            "role": self.role.value,
            "server_phase": self.server_phase.value,
            "client_phase": self.client_phase.value,
            "profile_fingerprint": self.profile_fingerprint,
            "connection_state": self.connection_state.value,
            "human_confirmed": self.human_confirmed,
            "webex_decision": (
                None if self.webex_decision is None else self.webex_decision.value
            ),
            "next_action": self.next_action.value,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> StartupAttemptRecord:
        expected = {
            "attempt_id",
            "generation",
            "role",
            "server_phase",
            "client_phase",
            "profile_fingerprint",
            "connection_state",
            "human_confirmed",
            "webex_decision",
            "next_action",
        }
        if set(value) != expected:
            raise ValueError("startup attempt has unsupported fields")
        return cls(
            attempt_id=str(value["attempt_id"]),
            generation=value["generation"],  # type: ignore[arg-type]
            role=str(value["role"]),
            server_phase=str(value["server_phase"]),
            client_phase=str(value["client_phase"]),
            profile_fingerprint=str(value["profile_fingerprint"]),
            connection_state=str(value["connection_state"]),
            human_confirmed=value["human_confirmed"],  # type: ignore[arg-type]
            webex_decision=(
                None
                if value["webex_decision"] is None
                else str(value["webex_decision"])
            ),
            next_action=str(value["next_action"]),
        )


class JamulusNativeProfileManager:
    """Provision a dedicated, Jamulus-owned profile without audio routing.

    On macOS the verified, non-sandboxed integrated component accepts a
    filename for ``--inifile`` and resolves it relative to the supplied
    working directory.  WebJam passes that fixed filename while starting the
    process from a private WebJam-owned directory.  It never creates, stats,
    reads, resolves, or changes Jamulus's container.  The launch directory and
    profile are checked before use so a symlink cannot redirect the child.
    """

    def __init__(
        self,
        *,
        home: Path | None = None,
        platform: str | None = None,
        profile_filename: str = WEBJAM_NATIVE_PROFILE_FILENAME,
        version_probe: Callable[[str], str] | None = None,
    ) -> None:
        filename = str(profile_filename or "").strip()
        if (
            not filename
            or Path(filename).name != filename
            or filename in {".", ".."}
            or not filename.endswith(".ini")
        ):
            raise ValueError("profile_filename must be a safe .ini filename")
        self._home = Path.home() if home is None else Path(home)
        self._platform = str(platform or sys.platform).lower()
        self._profile_filename = filename
        self._version_probe = version_probe or default_jamulus_version_probe
        self._runtime_directory: SecureRuntimeDirectory | None = None

    def close(self) -> None:
        """Release the retained macOS profile-directory proof."""

        runtime = self._runtime_directory
        self._runtime_directory = None
        if runtime is None:
            return
        try:
            runtime.close()
        except SecureRuntimeError:
            pass

    @property
    def profile_filename(self) -> str:
        return self._profile_filename

    def profile_directory(self) -> Path:
        """Return the WebJam-managed directory for the integrated profile."""

        if self._platform.startswith("darwin"):
            return (
                self._home
                / "Library"
                / "Application Support"
                / "WebJam"
                / "Jamulus Launch"
            )
        return self._home / ".config" / "Jamulus"

    def launch_working_directory(self) -> Path:
        """Return the process cwd that WebJam is allowed to own and validate."""

        return self.profile_directory()

    def plan(self, *, jamulus_version: str) -> JamulusNativeProfilePlan:
        """Return filename-only launch facts without writing a profile.

        Existing content is *never* written or normalized.  When the profile
        is absent, Jamulus—not WebJam—creates it after the musician opens its
        native sound setup.  That is what lets the musician finish native
        setup without WebJam changing a device, channel map, buffer, or normal
        profile.
        """

        version = _normalize_version(jamulus_version)
        runtime: SecureRuntimeDirectory | None = None
        try:
            if self._platform.startswith("darwin") and os.name == "posix":
                runtime = SecureRuntimeDirectory.open(
                    home=self._home,
                    directory=self.launch_working_directory(),
                )
                directory = runtime.path
                directory_device = runtime.proof.device
                directory_inode = runtime.proof.inode
            else:
                directory = self._safe_launch_directory()
                directory_device, directory_inode = _directory_identity(
                    directory,
                    error_type=JamulusNativeProfileError,
                    error_message=(
                        "WebJam couldn't prepare its Jamulus profile. Reopen "
                        "WebJam and try again."
                    ),
                )
            profile_path = directory / self._profile_filename
            profile_exists, profile_bytes = _read_profile_snapshot(
                directory=directory,
                filename=self._profile_filename,
                expected_device=directory_device,
                expected_inode=directory_inode,
                allow_missing=True,
            )
            profile_access = NativeProfileAccess.WEBJAM_READABLE
            fingerprint = native_profile_fingerprint(
                profile_filename=self._profile_filename,
                jamulus_version=version,
                profile_bytes=profile_bytes,
                profile_exists=profile_exists,
            )
            planned = JamulusNativeProfilePlan(
                profile_filename=self._profile_filename,
                arguments=("--inifile", self._profile_filename),
                working_directory=directory,
                profile_path=profile_path,
                profile_fingerprint=fingerprint,
                jamulus_version=version,
                profile_exists=profile_exists,
                working_directory_device=directory_device,
                working_directory_inode=directory_inode,
                profile_access=profile_access,
                _directory_runtime=runtime,
            )
        except PermissionError as exc:
            if runtime is not None:
                try:
                    runtime.close()
                except SecureRuntimeError:
                    pass
            self._raise_profile_permission_error(exc)
        except SecureRuntimeError:
            if runtime is not None:
                try:
                    runtime.close()
                except SecureRuntimeError:
                    pass
            raise JamulusNativeProfileError(
                "WebJam couldn't prepare its Jamulus profile. Reopen WebJam "
                "and try again."
            ) from None
        except Exception:
            if runtime is not None:
                try:
                    runtime.close()
                except SecureRuntimeError:
                    pass
            raise
        previous = self._runtime_directory
        self._runtime_directory = runtime
        if previous is not None and previous is not runtime:
            try:
                previous.close()
            except SecureRuntimeError:
                pass
        return planned

    def prepare(
        self,
        _settings: object | None,
        jamulus_binary: str | Path,
        *,
        approved_versions: Iterable[str] | None = None,
        expected_version: str | None = None,
    ) -> JamulusNativeProfilePlan:
        """Compatibility-shaped helper for the existing process supervisor.

        ``_settings`` is intentionally ignored.  Retaining it makes the
        transition from the old route manager mechanical while proving this
        implementation no longer reads WebJam's device/channel/buffer fields.

        The default remains the immutable 3.12.2 embedded boundary. Runtime
        component selection may supply the exact versions approved by
        WebJam's compatibility registry plus the version selected for this
        launch. The executable is probed again here, immediately before its
        native profile is used, so a changed component fails closed.
        """

        if approved_versions is None:
            allowed = frozenset({PINNED_JAMULUS_VERSION})
        else:
            try:
                allowed = frozenset(
                    _normalize_version(item) for item in approved_versions
                )
            except (TypeError, ValueError) as exc:
                raise ValueError("approved_versions contains an invalid version") from exc
            if not allowed:
                raise ValueError("approved_versions cannot be empty")
        expected = (
            None
            if expected_version is None
            else _normalize_version(expected_version)
        )
        if expected is not None and expected not in allowed:
            raise ValueError("expected_version must be approved")
        version = self._version_probe(str(jamulus_binary or ""))
        if version not in allowed or (
            expected is not None and version != expected
        ):
            if (
                allowed == {PINNED_JAMULUS_VERSION}
                and expected is None
            ):
                raise JamulusNativeProfileError(
                    "WebJam needs its included Jamulus 3.12.2 music component. "
                    "Reinstall WebJam, then try again."
                )
            raise JamulusNativeProfileError(
                "WebJam couldn't verify the approved Jamulus music component. "
                "Finish any pending Jamulus update or reinstall WebJam, then "
                "try again."
            )
        return self.plan(jamulus_version=version)

    def validate_active(
        self,
        plan: JamulusNativeProfilePlan,
    ) -> JamulusNativeProfilePlan:
        """Recheck profile safety and return current content identity.

        No device snapshot is taken and no native Jamulus setting is changed.
        A musician can intentionally adjust sound in Jamulus.  The returned
        plan refreshes the one-way fingerprint so stale human-confirmation
        evidence cannot silently carry across that change.
        """

        if not isinstance(plan, JamulusNativeProfilePlan):
            raise JamulusNativeProfileError(
                "WebJam couldn't restore its Jamulus profile. Start the jam again."
            )
        runtime = plan._directory_runtime
        if runtime is not None:
            if (
                runtime is not self._runtime_directory
                or not runtime.path_matches()
            ):
                raise JamulusNativeProfileError(
                    "WebJam couldn't restore its Jamulus profile. Start the "
                    "jam again."
                )
            directory = runtime.path
            directory_device = runtime.proof.device
            directory_inode = runtime.proof.inode
        else:
            if self._platform.startswith("darwin") and os.name == "posix":
                raise JamulusNativeProfileError(
                    "WebJam couldn't restore its Jamulus profile. Start the "
                    "jam again."
                )
            directory = self._safe_launch_directory()
            directory_device, directory_inode = _directory_identity(
                directory,
                error_type=JamulusNativeProfileError,
                error_message=(
                    "WebJam couldn't restore its Jamulus profile. Start the "
                    "jam again."
                ),
            )
        expected_path = directory / self._profile_filename
        expected_access = NativeProfileAccess.WEBJAM_READABLE
        if (
            plan.profile_filename != self._profile_filename
            or plan.working_directory != directory
            or plan.profile_path != expected_path
            or plan.profile_access is not expected_access
        ):
            raise JamulusNativeProfileError(
                "WebJam couldn't restore its Jamulus profile. Start the jam again."
            )
        if (
            directory_device != plan.working_directory_device
            or directory_inode != plan.working_directory_inode
        ):
            raise JamulusNativeProfileError(
                "WebJam couldn't restore its Jamulus profile. Start the jam again."
            )
        try:
            profile_exists, profile_bytes = _read_profile_snapshot(
                directory=directory,
                filename=self._profile_filename,
                expected_device=directory_device,
                expected_inode=directory_inode,
                allow_missing=not plan.profile_exists,
            )
        except PermissionError as exc:
            self._raise_profile_permission_error(exc)
        fingerprint = native_profile_fingerprint(
            profile_filename=self._profile_filename,
            jamulus_version=plan.jamulus_version,
            profile_bytes=profile_bytes,
            profile_exists=profile_exists,
        )
        if (
            profile_exists == plan.profile_exists
            and fingerprint == plan.profile_fingerprint
        ):
            return plan
        return replace(
            plan,
            profile_exists=profile_exists,
            profile_fingerprint=fingerprint,
        )

    def _safe_launch_directory(self) -> Path:
        candidate = self.launch_working_directory()
        return _ensure_managed_directory(
            home=self._home,
            directory=candidate,
            private=True,
            error_type=JamulusNativeProfileError,
            error_message="WebJam couldn't prepare its Jamulus profile. Reopen WebJam and try again.",
        )

    def _raise_profile_permission_error(
        self,
        _exc: PermissionError,
    ) -> NoReturn:
        raise JamulusNativeProfileError(
            "WebJam couldn't access its Jamulus profile. Reopen WebJam and try "
            "again."
        ) from None

def read_native_audio_device_names(
    plan: JamulusNativeProfilePlan,
) -> tuple[str, str]:
    """Read the active Jamulus-owned CoreAudio selector without persisting it.

    This is a narrow optional consistency check for the WebJam-owned profile
    file. CoreAudio process-route proof remains authoritative; profile names
    only catch an obvious mismatch. Returned values are never copied into
    settings, logs, readiness records, or support artifacts.
    """

    if not isinstance(plan, JamulusNativeProfilePlan):
        raise JamulusNativeProfileError(
            "WebJam couldn't verify the primary Jamulus audio route."
        )
    if _path_mentions_jamulus_container(plan.profile_path):
        raise JamulusNativeProfileError(
            "WebJam couldn't verify the primary Jamulus audio route."
        )
    if plan.profile_path != (
        plan.working_directory / plan.profile_filename
    ):
        raise JamulusNativeProfileError(
            "WebJam couldn't verify the primary Jamulus audio route."
        )
    if (
        plan._directory_runtime is not None
        and not plan._directory_runtime.path_matches()
    ):
        raise JamulusNativeProfileError(
            "WebJam couldn't verify the primary Jamulus audio route."
        )
    try:
        profile_exists, raw = _read_profile_snapshot(
            directory=plan.working_directory,
            filename=plan.profile_filename,
            expected_device=plan.working_directory_device,
            expected_inode=plan.working_directory_inode,
            allow_missing=False,
        )
        if not profile_exists:
            raise ValueError("missing profile")
        root = ElementTree.fromstring(raw)
        if root.tag != "client":
            raise ValueError("unexpected profile root")
        encoded = str(root.findtext("auddev_base64") or "").strip()
        if not encoded or len(encoded) > 4_096:
            raise ValueError("missing audio selector")
        selector = base64.b64decode(
            encoded.encode("ascii"),
            validate=True,
        ).decode("utf-8")
        prefix = "in: "
        separator = "/out: "
        if not selector.startswith(prefix) or selector.count(separator) != 1:
            raise ValueError("invalid audio selector")
        input_name, output_name = selector[len(prefix) :].split(separator, 1)
        input_name = input_name.strip()
        output_name = output_name.strip()
        if any(
            not value
            or len(value) > 512
            or any(character in value for character in ("/", "\0", "\r", "\n"))
            for value in (input_name, output_name)
        ):
            raise ValueError("invalid audio device name")
    except (
        JamulusNativeProfileError,
        OSError,
        UnicodeError,
        ValueError,
        binascii.Error,
        ElementTree.ParseError,
    ):
        raise JamulusNativeProfileError(
            "WebJam couldn't verify the primary Jamulus audio route."
        ) from None
    return input_name, output_name


class StartupReadinessStore:
    """Atomic, private storage for minimal restart-safe readiness evidence."""

    def __init__(
        self,
        *,
        home: Path | None = None,
        platform: str | None = None,
        path: Path | None = None,
    ) -> None:
        self._home = Path.home() if home is None else Path(home)
        self._platform = str(platform or sys.platform).lower()
        if path is None:
            if self._platform.startswith("darwin"):
                path = self._home / "Library" / "Application Support" / "WebJam" / "startup-readiness-v1.json"
            else:
                path = self._home / ".local" / "state" / "webjam" / "startup-readiness-v1.json"
        self._path = Path(path)
        if self._path.name != "startup-readiness-v1.json":
            raise ValueError("startup readiness path must use the managed filename")

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> StartupReadinessRecord | None:
        """Return a valid private record, otherwise fail closed with ``None``."""

        try:
            self._safe_state_directory(create=False)
            _require_regular_file(self._path, allow_missing=True)
            if not self._path.exists():
                return None
            mode = stat.S_IMODE(self._path.stat().st_mode)
            if mode & 0o077:
                return None
            if self._path.stat().st_size > _MAX_READINESS_BYTES:
                return None
            decoded = json.loads(self._path.read_text(encoding="utf-8"))
            if not isinstance(decoded, dict):
                return None
            return StartupReadinessRecord.from_mapping(decoded)
        except (
            JamulusNativeProfileError,
            OSError,
            ValueError,
            TypeError,
            json.JSONDecodeError,
        ):
            return None

    def save(self, record: StartupReadinessRecord) -> None:
        """Atomically save the exact approved readiness fields as mode 0600."""

        if not isinstance(record, StartupReadinessRecord):
            raise TypeError("record must be a StartupReadinessRecord")
        self._safe_state_directory(create=True)
        _require_regular_file(self._path, allow_missing=True)
        payload = json.dumps(
            record.to_mapping(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ) + "\n"
        atomic_write_text(self._path, payload, mode=0o600)
        os.chmod(self._path, 0o600)

    def save_for_plan(
        self,
        plan: JamulusNativeProfilePlan,
        role: StartupRole | str,
        *,
        human_confirmed: bool,
    ) -> StartupReadinessRecord:
        """Persist exactly one plan/role confirmation and return it."""

        record = plan.readiness_record(role, human_confirmed=human_confirmed)
        self.save(record)
        return record

    def is_current(
        self,
        plan: JamulusNativeProfilePlan,
        role: StartupRole | str,
    ) -> bool:
        record = self.load()
        return bool(record is not None and record.matches(plan, role))

    def clear(self) -> None:
        """Remove only the managed record; never touch a normal Jamulus profile."""

        try:
            self._safe_state_directory(create=False)
        except JamulusNativeProfileError:
            return
        _require_regular_file(self._path, allow_missing=True)
        try:
            self._path.unlink()
            _fsync_directory(self._path.parent)
        except FileNotFoundError:
            return

    def _safe_state_directory(self, *, create: bool) -> Path:
        if not create and not self._path.parent.exists():
            return self._path.parent
        return _ensure_managed_directory(
            home=self._home,
            directory=self._path.parent,
            private=True,
            error_type=JamulusNativeProfileError,
            error_message="WebJam couldn't access its private startup state.",
        )


class StartupAttemptStore:
    """Atomic, private persistence for the allowlisted startup recovery plan."""

    def __init__(
        self,
        *,
        home: Path | None = None,
        platform: str | None = None,
        path: Path | None = None,
    ) -> None:
        self._home = Path.home() if home is None else Path(home)
        self._platform = str(platform or sys.platform).lower()
        if path is None:
            if self._platform.startswith("darwin"):
                path = self._home / "Library" / "Application Support" / "WebJam" / "startup-attempt-v1.json"
            else:
                path = self._home / ".local" / "state" / "webjam" / "startup-attempt-v1.json"
        self._path = Path(path)
        if self._path.name != "startup-attempt-v1.json":
            raise ValueError("startup attempt path must use the managed filename")

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> StartupAttemptRecord | None:
        """Return a valid private attempt record, otherwise fail closed."""

        try:
            self._safe_state_directory(create=False)
            _require_regular_file(self._path, allow_missing=True)
            if not self._path.exists():
                return None
            mode = stat.S_IMODE(self._path.stat().st_mode)
            if mode & 0o077 or self._path.stat().st_size > _MAX_READINESS_BYTES:
                return None
            decoded = json.loads(self._path.read_text(encoding="utf-8"))
            if not isinstance(decoded, dict):
                return None
            return StartupAttemptRecord.from_mapping(decoded)
        except (
            JamulusNativeProfileError,
            OSError,
            ValueError,
            TypeError,
            json.JSONDecodeError,
        ):
            return None

    def save(self, record: StartupAttemptRecord) -> None:
        """Atomically save only the fixed recovery contract at mode 0600."""

        if not isinstance(record, StartupAttemptRecord):
            raise TypeError("record must be a StartupAttemptRecord")
        self._safe_state_directory(create=True)
        _require_regular_file(self._path, allow_missing=True)
        payload = json.dumps(
            record.to_mapping(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ) + "\n"
        atomic_write_text(self._path, payload, mode=0o600)
        os.chmod(self._path, 0o600)

    def next_generation(self) -> int:
        """Return the next monotonic generation without retaining stale state."""

        current = self.load()
        return 1 if current is None else current.generation + 1

    def clear(self) -> None:
        """Remove only this managed attempt file."""

        try:
            self._safe_state_directory(create=False)
        except JamulusNativeProfileError:
            return
        _require_regular_file(self._path, allow_missing=True)
        try:
            self._path.unlink()
            _fsync_directory(self._path.parent)
        except FileNotFoundError:
            return

    def _safe_state_directory(self, *, create: bool) -> Path:
        if not create and not self._path.parent.exists():
            return self._path.parent
        return _ensure_managed_directory(
            home=self._home,
            directory=self._path.parent,
            private=True,
            error_type=JamulusNativeProfileError,
            error_message="WebJam couldn't access its private startup state.",
        )


def native_profile_fingerprint(
    *,
    profile_filename: str,
    jamulus_version: str,
    profile_bytes: bytes,
    profile_exists: bool = True,
) -> str:
    """Hash native profile content without persisting or exposing it.

    Jamulus may add its own device names and settings after native setup.  We
    hash those bytes only in memory, so a later native change safely invalidates
    a stale human confirmation without writing device or path details anywhere.
    """

    filename = str(profile_filename or "").strip()
    if Path(filename).name != filename or not filename.endswith(".ini"):
        raise ValueError("profile_filename must be a safe .ini filename")
    version = _normalize_version(jamulus_version)
    raw = bytes(profile_bytes)
    if len(raw) > _MAX_PROFILE_BYTES:
        raise ValueError("profile is too large")
    payload = {
        "schema": _PROFILE_FINGERPRINT_SCHEMA,
        "profile_filename": filename,
        "jamulus_version": version,
        "profile_exists": bool(profile_exists),
        "profile_content_sha256": hashlib.sha256(raw).hexdigest(),
    }
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")
    return hashlib.sha256(canonical).hexdigest()


def _attempt_digest(
    *,
    generation: int,
    role: StartupRole,
    profile_fingerprint: str,
    entropy: bytes,
) -> str:
    """Create a one-way id without retaining nonce, paths, or session data."""

    if not 0 <= generation <= 2**31 - 1:
        raise ValueError("generation is out of range")
    payload = {
        "schema": 1,
        "generation": generation,
        "role": role.value,
        "profile_fingerprint": profile_fingerprint,
        "entropy_sha256": hashlib.sha256(entropy).hexdigest(),
    }
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")
    return hashlib.sha256(canonical).hexdigest()


def default_jamulus_version_probe(binary: str) -> str:
    """Read the Jamulus version without opening a UI or joining a session."""

    path = Path(binary)
    platform_name = (
        "darwin"
        if sys.platform.startswith("darwin")
        else "win32"
        if sys.platform.startswith("win")
        else "linux"
        if sys.platform.startswith("linux")
        else ""
    )
    try:
        environment = sanitized_jamulus_child_environment(
            os.environ,
            platform_name=platform_name,
            executable=path,
        )
        completed = subprocess.run(
            [str(path), "--version"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
            shell=False,
            env=environment,
            cwd=str(path.parent if platform_name == "win32" else Path("/")),
        )
    except (
        JamulusChildEnvironmentError,
        OSError,
        subprocess.SubprocessError,
    ):
        return "unverified"
    match = re.search(
        r"(?:version\s+)?(\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?)",
        f"{completed.stdout}\n{completed.stderr}",
        flags=re.IGNORECASE,
    )
    return match.group(1) if match else "unverified"


def _normalize_version(value: object) -> str:
    version = str(value or "").strip()
    if not _VERSION_RE.fullmatch(version) or len(version) > 128:
        raise ValueError("jamulus_version must be a semantic version")
    return version


def _ensure_managed_directory(
    *,
    home: Path,
    directory: Path,
    private: bool,
    error_type: type[Exception],
    error_message: str,
    permission_error_type: type[Exception] | None = None,
    permission_error_message: str | None = None,
) -> Path:
    """Create and validate an app-owned descendant without accepting links."""

    try:
        requested_home = Path(home).expanduser()
        safe_home = requested_home.resolve(strict=True)
        relative = Path(directory).expanduser().relative_to(requested_home)
        if any(component in {"", ".", ".."} for component in relative.parts):
            raise error_type(error_message)
        if os.name == "posix":
            return _ensure_managed_directory_posix(
                safe_home=safe_home,
                relative=relative,
                private=private,
                error_type=error_type,
                error_message=error_message,
            )
        candidate = safe_home
        for component in relative.parts:
            child = candidate / component
            try:
                status = child.lstat()
            except FileNotFoundError:
                child.mkdir(mode=0o700 if private else 0o777)
                status = child.lstat()
            if (
                stat.S_ISLNK(status.st_mode)
                or not stat.S_ISDIR(status.st_mode)
                or not _owned_by_current_user(status)
                or stat.S_IMODE(status.st_mode) & 0o022
            ):
                raise error_type(error_message)
            candidate = child
        if private:
            os.chmod(candidate, 0o700)
        return candidate
    except error_type:
        raise
    except PermissionError as exc:
        denied_type = permission_error_type or error_type
        denied_message = permission_error_message or error_message
        raise denied_type(denied_message) from exc
    except (OSError, ValueError) as exc:
        raise error_type(error_message) from exc


def _ensure_managed_directory_posix(
    *,
    safe_home: Path,
    relative: Path,
    private: bool,
    error_type: type[Exception],
    error_message: str,
) -> Path:
    """Walk from a retained home dirfd so intermediate links cannot redirect."""

    flags = os.O_RDONLY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    current_fd = os.open(safe_home, flags)
    candidate = safe_home
    try:
        home_details = os.fstat(current_fd)
        if (
            not stat.S_ISDIR(home_details.st_mode)
            or not _owned_by_current_user(home_details)
            or stat.S_IMODE(home_details.st_mode) & 0o022
        ):
            raise error_type(error_message)
        for component in relative.parts:
            try:
                next_fd = os.open(component, flags, dir_fd=current_fd)
            except FileNotFoundError:
                os.mkdir(
                    component,
                    mode=0o700 if private else 0o777,
                    dir_fd=current_fd,
                )
                next_fd = os.open(component, flags, dir_fd=current_fd)
            try:
                next_details = os.fstat(next_fd)
                if (
                    not stat.S_ISDIR(next_details.st_mode)
                    or not _owned_by_current_user(next_details)
                    or stat.S_IMODE(next_details.st_mode) & 0o022
                ):
                    raise error_type(error_message)
            except Exception:
                os.close(next_fd)
                raise
            os.close(current_fd)
            current_fd = next_fd
            candidate = candidate / component

        opened = os.fstat(current_fd)
        if (
            not stat.S_ISDIR(opened.st_mode)
            or not _owned_by_current_user(opened)
            or stat.S_IMODE(opened.st_mode) & 0o022
        ):
            raise error_type(error_message)
        if private:
            os.fchmod(current_fd, 0o700)
            opened = os.fstat(current_fd)
            if stat.S_IMODE(opened.st_mode) != 0o700:
                raise error_type(error_message)
        visible = candidate.lstat()
        if (
            stat.S_ISLNK(visible.st_mode)
            or not stat.S_ISDIR(visible.st_mode)
            or int(visible.st_dev) != int(opened.st_dev)
            or int(visible.st_ino) != int(opened.st_ino)
        ):
            raise error_type(error_message)
        return candidate
    finally:
        os.close(current_fd)


def _directory_identity(
    directory: Path,
    *,
    error_type: type[Exception],
    error_message: str,
) -> tuple[int, int]:
    """Return a positive, owner-bound directory identity without following links."""

    try:
        details = Path(directory).lstat()
    except OSError as exc:
        raise error_type(error_message) from exc
    if (
        stat.S_ISLNK(details.st_mode)
        or not stat.S_ISDIR(details.st_mode)
        or int(details.st_dev) <= 0
        or int(details.st_ino) <= 0
        or not _owned_by_current_user(details)
    ):
        raise error_type(error_message)
    return int(details.st_dev), int(details.st_ino)


def _owned_by_current_user(details: os.stat_result) -> bool:
    return not hasattr(os, "geteuid") or int(details.st_uid) == int(os.geteuid())


def _read_profile_snapshot(
    *,
    directory: Path,
    filename: str,
    expected_device: int,
    expected_inode: int,
    allow_missing: bool,
) -> tuple[bool, bytes]:
    """Read one bounded profile through its verified directory descriptor."""

    if Path(filename).name != filename or filename in {"", ".", ".."}:
        raise JamulusNativeProfileError(
            "WebJam couldn't read its Jamulus profile. Reopen WebJam and try again."
        )
    if os.name != "posix":
        return _read_profile_snapshot_portable(
            directory=directory,
            filename=filename,
            expected_device=expected_device,
            expected_inode=expected_inode,
            allow_missing=allow_missing,
        )

    nofollow = getattr(os, "O_NOFOLLOW", 0)
    directory_flag = getattr(os, "O_DIRECTORY", 0)
    if not nofollow or not directory_flag:
        raise JamulusNativeProfileError(
            "WebJam couldn't read its Jamulus profile. Reopen WebJam and try again."
        )
    directory_descriptor = -1
    profile_descriptor = -1
    try:
        directory_descriptor = os.open(
            directory,
            os.O_RDONLY
            | nofollow
            | directory_flag
            | getattr(os, "O_CLOEXEC", 0),
        )
        directory_details = os.fstat(directory_descriptor)
        if (
            not stat.S_ISDIR(directory_details.st_mode)
            or not _owned_by_current_user(directory_details)
            or stat.S_IMODE(directory_details.st_mode) & 0o022
            or int(directory_details.st_dev) != int(expected_device)
            or int(directory_details.st_ino) != int(expected_inode)
        ):
            raise ValueError("profile directory identity changed")
        try:
            profile_descriptor = os.open(
                filename,
                os.O_RDONLY | nofollow | getattr(os, "O_CLOEXEC", 0),
                dir_fd=directory_descriptor,
            )
        except FileNotFoundError:
            if allow_missing:
                return False, b""
            raise ValueError("profile disappeared") from None
        details = os.fstat(profile_descriptor)
        size = int(details.st_size)
        if (
            not stat.S_ISREG(details.st_mode)
            or not _owned_by_current_user(details)
            or int(details.st_nlink) != 1
            or stat.S_IMODE(details.st_mode) & 0o022
            or size < 0
            or size > _MAX_PROFILE_BYTES
        ):
            raise ValueError("profile is unsafe")
        payload = bytearray()
        while len(payload) <= _MAX_PROFILE_BYTES:
            chunk = os.read(
                profile_descriptor,
                min(64 * 1024, _MAX_PROFILE_BYTES + 1 - len(payload)),
            )
            if not chunk:
                break
            payload.extend(chunk)
        if len(payload) > _MAX_PROFILE_BYTES:
            raise ValueError("profile is too large")
        final = os.fstat(profile_descriptor)
        entry = os.stat(
            filename,
            dir_fd=directory_descriptor,
            follow_symlinks=False,
        )
        if (
            len(payload) != size
            or not stat.S_ISREG(final.st_mode)
            or int(final.st_dev) != int(details.st_dev)
            or int(final.st_ino) != int(details.st_ino)
            or int(final.st_size) != size
            or int(final.st_nlink) != 1
            or not _owned_by_current_user(final)
            or stat.S_IMODE(final.st_mode) & 0o022
            or not stat.S_ISREG(entry.st_mode)
            or int(entry.st_dev) != int(details.st_dev)
            or int(entry.st_ino) != int(details.st_ino)
            or int(entry.st_size) != size
            or int(entry.st_nlink) != 1
            or not _owned_by_current_user(entry)
            or stat.S_IMODE(entry.st_mode) & 0o022
        ):
            raise ValueError("profile changed during validation")
        return True, bytes(payload)
    except PermissionError:
        raise
    except (NotImplementedError, OSError, ValueError):
        raise JamulusNativeProfileError(
            "WebJam couldn't read its Jamulus profile. Reopen WebJam and try again."
        ) from None
    finally:
        if profile_descriptor >= 0:
            try:
                os.close(profile_descriptor)
            except OSError:
                pass
        if directory_descriptor >= 0:
            try:
                os.close(directory_descriptor)
            except OSError:
                pass


def _read_profile_snapshot_portable(
    *,
    directory: Path,
    filename: str,
    expected_device: int,
    expected_inode: int,
    allow_missing: bool,
) -> tuple[bool, bytes]:
    """Portable fallback; macOS/Linux use the descriptor-anchored boundary."""

    profile_path = Path(directory) / filename
    try:
        directory_details = Path(directory).lstat()
        if (
            stat.S_ISLNK(directory_details.st_mode)
            or not stat.S_ISDIR(directory_details.st_mode)
            or int(directory_details.st_dev) != int(expected_device)
            or int(directory_details.st_ino) != int(expected_inode)
        ):
            raise ValueError("profile directory identity changed")
        try:
            details = profile_path.lstat()
        except FileNotFoundError:
            if allow_missing:
                return False, b""
            raise ValueError("profile disappeared") from None
        if (
            stat.S_ISLNK(details.st_mode)
            or not stat.S_ISREG(details.st_mode)
            or int(details.st_nlink) != 1
            or int(details.st_size) < 0
            or int(details.st_size) > _MAX_PROFILE_BYTES
        ):
            raise ValueError("profile is unsafe")
        payload = profile_path.read_bytes()
        if len(payload) > _MAX_PROFILE_BYTES:
            raise ValueError("profile is too large")
        after = profile_path.lstat()
        if (
            int(after.st_dev) != int(details.st_dev)
            or int(after.st_ino) != int(details.st_ino)
            or int(after.st_size) != int(details.st_size)
        ):
            raise ValueError("profile changed during validation")
        return True, payload
    except PermissionError:
        raise
    except (OSError, ValueError):
        raise JamulusNativeProfileError(
            "WebJam couldn't read its Jamulus profile. Reopen WebJam and try again."
        ) from None


def _require_regular_file(path: Path, *, allow_missing: bool) -> bool:
    try:
        status = path.lstat()
    except FileNotFoundError:
        if allow_missing:
            return False
        raise
    if stat.S_ISLNK(status.st_mode) or not stat.S_ISREG(status.st_mode):
        raise JamulusNativeProfileError("WebJam couldn't safely access its startup files.")
    return True


def _path_mentions_jamulus_container(path: Path) -> bool:
    """Reject another-app container paths lexically without resolving them."""

    try:
        parts = tuple(Path(path).parts)
    except (TypeError, ValueError):
        return True
    return JAMULUS_CONTAINER_ID in parts


def _fsync_directory(directory: Path) -> None:
    if os.name != "posix":
        return
    descriptor = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
