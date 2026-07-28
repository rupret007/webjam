"""Jamulus-native profile and restart-readiness primitives for WebJam.

WebJam v0.16 deliberately does **not** select live audio hardware, channels,
or buffers.  Those choices belong to Jamulus's own sound setup.  This module
only gives WebJam a dedicated Jamulus profile name to launch with and a tiny
piece of restart-safe readiness evidence.

WebJam only supplies a dedicated filename and safe working directory.
Jamulus creates that file and owns every setting in it.  In particular, this
module never writes an audio-device, channel, or buffer setting (or any
Jamulus profile content at all).  It also leaves the musician's normal
``Jamulus.ini`` untouched.

The readiness file is intentionally small and private.  It records only the
role, a one-way profile fingerprint, the Jamulus version, and whether a human
has confirmed sound.  It never stores an invite, Webex details, device data,
or filesystem paths.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass, field
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
from typing import Callable, Mapping
from xml.etree import ElementTree

from core.file_io import atomic_write_text


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
    must never be written to the readiness record.
    """

    profile_filename: str
    arguments: tuple[str, ...]
    working_directory: Path
    profile_path: Path
    profile_fingerprint: str
    jamulus_version: str
    profile_exists: bool
    environment: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if Path(self.profile_filename).name != self.profile_filename:
            raise ValueError("profile_filename must be a filename")
        if self.arguments != ("--inifile", self.profile_filename):
            raise ValueError("Jamulus profile arguments must use filename-only --inifile")
        if not _FINGERPRINT_RE.fullmatch(self.profile_fingerprint):
            raise ValueError("profile_fingerprint must be a SHA-256 hex digest")
        _normalize_version(self.jamulus_version)

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

    On macOS Jamulus accepts only a filename for ``--inifile`` in the bundled
    sandbox's configuration area.  We consequently use that established
    directory as the process working directory and pass the fixed filename
    only.  The directory is checked before use so a symlink cannot redirect
    the child process to an unexpected location.
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

    @property
    def profile_filename(self) -> str:
        return self._profile_filename

    def profile_directory(self) -> Path:
        """Return the conventional directory required by this Jamulus build."""

        if self._platform.startswith("darwin"):
            return (
                self._home
                / "Library"
                / "Containers"
                / JAMULUS_CONTAINER_ID
                / "Data"
                / ".config"
                / "Jamulus"
            )
        return self._home / ".config" / "Jamulus"

    def plan(self, *, jamulus_version: str) -> JamulusNativeProfilePlan:
        """Return filename-only launch facts without writing a profile.

        Existing content is *never* written or normalized.  When the profile
        is absent, Jamulus—not WebJam—creates it after the musician opens its
        native sound setup.  That is what lets the musician finish native
        setup without WebJam changing a device, channel map, buffer, or normal
        profile.
        """

        version = _normalize_version(jamulus_version)
        directory = self._safe_profile_directory()
        profile_path = directory / self._profile_filename
        profile_exists = self._profile_exists(profile_path)
        fingerprint = native_profile_fingerprint(
            profile_filename=self._profile_filename,
            jamulus_version=version,
            profile_bytes=(self._read_profile(profile_path) if profile_exists else b""),
            profile_exists=profile_exists,
        )
        return JamulusNativeProfilePlan(
            profile_filename=self._profile_filename,
            arguments=("--inifile", self._profile_filename),
            working_directory=directory,
            profile_path=profile_path,
            profile_fingerprint=fingerprint,
            jamulus_version=version,
            profile_exists=profile_exists,
        )

    def prepare(
        self,
        _settings: object | None,
        jamulus_binary: str | Path,
    ) -> JamulusNativeProfilePlan:
        """Compatibility-shaped helper for the existing process supervisor.

        ``_settings`` is intentionally ignored.  Retaining it makes the
        transition from the old route manager mechanical while proving this
        implementation no longer reads WebJam's device/channel/buffer fields.
        """

        version = self._version_probe(str(jamulus_binary or ""))
        if version != PINNED_JAMULUS_VERSION:
            raise JamulusNativeProfileError(
                "WebJam needs its included Jamulus 3.12.2 music component. "
                "Reinstall WebJam, then try again."
            )
        return self.plan(jamulus_version=version)

    def validate_active(self, plan: JamulusNativeProfilePlan) -> None:
        """Recheck only profile-file safety before an automatic reconnect.

        No device snapshot is taken and no native Jamulus setting is changed.
        A musician can intentionally adjust sound in Jamulus; the next client
        launch then uses that native profile without WebJam second-guessing it.
        """

        if not isinstance(plan, JamulusNativeProfilePlan):
            raise JamulusNativeProfileError(
                "WebJam couldn't restore its Jamulus profile. Start the jam again."
            )
        directory = self._safe_profile_directory()
        expected_path = directory / self._profile_filename
        if (
            plan.profile_filename != self._profile_filename
            or plan.working_directory != directory
            or plan.profile_path != expected_path
        ):
            raise JamulusNativeProfileError(
                "WebJam couldn't restore its Jamulus profile. Start the jam again."
            )
        _require_regular_file(expected_path, allow_missing=True)

    def _safe_profile_directory(self) -> Path:
        candidate = self.profile_directory()
        return _ensure_managed_directory(
            home=self._home,
            directory=candidate,
            private=False,
            error_type=JamulusNativeProfileError,
            error_message="WebJam couldn't prepare its Jamulus profile. Reopen WebJam and try again.",
        )

    @staticmethod
    def _profile_exists(profile_path: Path) -> bool:
        _require_regular_file(profile_path, allow_missing=True)
        return profile_path.exists()

    @staticmethod
    def _read_profile(profile_path: Path) -> bytes:
        _require_regular_file(profile_path, allow_missing=False)
        try:
            size = profile_path.stat().st_size
            if size < 0 or size > _MAX_PROFILE_BYTES:
                raise ValueError("profile is too large")
            return profile_path.read_bytes()
        except (OSError, ValueError) as exc:
            raise JamulusNativeProfileError(
                "WebJam couldn't read its Jamulus profile. Reopen WebJam and try again."
            ) from exc


def read_native_audio_device_names(
    plan: JamulusNativeProfilePlan,
) -> tuple[str, str]:
    """Read the active Jamulus-owned CoreAudio selector without persisting it.

    This is a narrow runtime safety boundary for Reference Track.  The values
    are read only while the primary client is alive and are never copied into
    WebJam settings, logs, readiness records, or support artifacts.
    """

    if not isinstance(plan, JamulusNativeProfilePlan):
        raise JamulusNativeProfileError(
            "WebJam couldn't verify the primary Jamulus audio route."
        )
    try:
        raw = JamulusNativeProfileManager._read_profile(plan.profile_path)
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

    try:
        completed = subprocess.run(
            [str(binary), "--version"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
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
) -> Path:
    """Create and validate an app-owned descendant without accepting links."""

    try:
        safe_home = Path(home).expanduser().resolve()
        relative = Path(directory).expanduser().relative_to(Path(home).expanduser())
        candidate = safe_home / relative
        candidate.mkdir(parents=True, exist_ok=True, mode=0o700)
        resolved = candidate.resolve(strict=True)
        try:
            resolved.relative_to(safe_home)
        except ValueError as exc:
            raise error_type(error_message) from exc
        if candidate.is_symlink() or not stat.S_ISDIR(candidate.stat().st_mode):
            raise error_type(error_message)
        if private:
            os.chmod(candidate, 0o700)
        return resolved
    except error_type:
        raise
    except (OSError, ValueError) as exc:
        raise error_type(error_message) from exc


def _require_regular_file(path: Path, *, allow_missing: bool) -> None:
    try:
        status = path.lstat()
    except FileNotFoundError:
        if allow_missing:
            return
        raise
    if stat.S_ISLNK(status.st_mode) or not stat.S_ISREG(status.st_mode):
        raise JamulusNativeProfileError("WebJam couldn't safely access its startup files.")


def _fsync_directory(directory: Path) -> None:
    if os.name != "posix":
        return
    descriptor = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
