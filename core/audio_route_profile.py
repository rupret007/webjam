"""Canonical, versioned audio-route truth for the Jamulus 3.12.2 boundary.

The profile records stable operating-system identity.  The adapter translates
that identity into the narrower selectors Jamulus 3.12.2 accepts.  In
particular, a CoreAudio display-name pair or ASIO driver name is a launch
selector, not a stable identifier and not proof of the device Jamulus opened.

Jamulus 3.12.2 exposes no JSON-RPC method for reading its active device,
channel map, sample rate, or hardware buffer.  Consequently macOS and Windows
may be called configured or OS-preflighted, but never graph-confirmed.  Human
hearing remains the final acoustic confirmation on every platform.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, replace
from enum import Enum, IntEnum
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
from typing import Mapping
from xml.etree import ElementTree


class AudioRoutePlatform(str, Enum):
    """The audio boundary controlled by the pinned Jamulus build."""

    MACOS_COREAUDIO = "macos_coreaudio"
    WINDOWS_ASIO = "windows_asio"
    LINUX_JACK = "linux_jack"


class RouteConfirmationLevel(str, Enum):
    """Honest, monotonically stronger route evidence."""

    CONFIGURED = "configured"
    PREFLIGHTED = "preflighted"
    GRAPH_CONFIRMED = "graph_confirmed"
    MUSICIAN_CONFIRMED = "musician_confirmed"


class JamulusChannelMode(IntEnum):
    """Values stored by Jamulus in the ``audiochannels`` setting."""

    MONO = 0
    MONO_IN_STEREO_OUT = 1
    STEREO = 2


@dataclass(frozen=True, slots=True)
class AudioRouteProfile:
    """Immutable route shared by Band Check, Jamulus, capture, and Studio.

    ``input_device_id`` and ``output_device_id`` are stable OS identifiers:
    CoreAudio UIDs, an ASIO registration identity, or stable JACK/PipeWire
    descriptors.  Display names are retained only for Jamulus 3.12.2 adapter
    translation and musician-facing copy.
    """

    platform: AudioRoutePlatform
    input_device_id: str
    output_device_id: str
    input_device_name: str
    output_device_name: str
    input_channels: tuple[int, int] = (0, 1)
    output_channels: tuple[int, int] = (0, 1)
    channel_mode: JamulusChannelMode = JamulusChannelMode.MONO_IN_STEREO_OUT
    sample_rate: int = 48_000
    requested_buffer_frames: int | None = 128
    observed_buffer_frames: int | None = None
    device_generation: str = "unknown"
    app_version: str = ""
    jamulus_version: str = "3.12.2"
    jamulus_binary_sha256: str = ""
    adapter_version: str = "jamulus-3.12.2-route-v1"
    config_generation: str = "1"
    confirmation_level: RouteConfirmationLevel = RouteConfirmationLevel.CONFIGURED
    last_verified_at: str = ""
    verification_method: str = "versioned_inifile"
    invalidation_reason: str = ""
    jack_server: str = ""
    jack_input_ports: tuple[str, str] = ("", "")
    jack_output_ports: tuple[str, str] = ("", "")

    def __post_init__(self) -> None:
        object.__setattr__(self, "platform", AudioRoutePlatform(self.platform))
        object.__setattr__(
            self, "channel_mode", JamulusChannelMode(self.channel_mode)
        )
        object.__setattr__(
            self,
            "confirmation_level",
            RouteConfirmationLevel(self.confirmation_level),
        )
        object.__setattr__(self, "input_channels", tuple(self.input_channels))
        object.__setattr__(self, "output_channels", tuple(self.output_channels))
        object.__setattr__(
            self,
            "jack_input_ports",
            tuple(str(port or "").strip() for port in self.jack_input_ports),
        )
        object.__setattr__(
            self,
            "jack_output_ports",
            tuple(str(port or "").strip() for port in self.jack_output_ports),
        )

        for field_name in (
            "input_device_id",
            "output_device_id",
            "input_device_name",
            "output_device_name",
            "device_generation",
            "adapter_version",
            "config_generation",
            "verification_method",
        ):
            value = str(getattr(self, field_name) or "").strip()
            if not value:
                raise ValueError(f"{field_name} must not be empty")
            if len(value) > 512:
                raise ValueError(f"{field_name} is too long")
            object.__setattr__(self, field_name, value)

        for field_name, maximum in (
            ("app_version", 128),
            ("jamulus_version", 128),
            ("last_verified_at", 128),
            ("invalidation_reason", 512),
            ("jack_server", 512),
        ):
            value = str(getattr(self, field_name) or "").strip()
            if len(value) > maximum:
                raise ValueError(f"{field_name} is too long")
            object.__setattr__(self, field_name, value)

        if self.jamulus_version != "3.12.2":
            raise ValueError("this adapter profile requires Jamulus 3.12.2 exactly")
        if self.sample_rate != 48_000:
            raise ValueError("Jamulus 3.12.2 requires a 48000 Hz audio route")
        self._validate_channels("input_channels", self.input_channels)
        self._validate_channels("output_channels", self.output_channels)

        if self.requested_buffer_frames is not None:
            if isinstance(self.requested_buffer_frames, bool):
                raise ValueError("requested_buffer_frames must be an integer")
            requested = int(self.requested_buffer_frames)
            if self.platform is not AudioRoutePlatform.LINUX_JACK and requested not in {
                64,
                128,
                256,
            }:
                raise ValueError(
                    "CoreAudio/ASIO buffer preference must be 64, 128, or 256 frames"
                )
            if requested <= 0:
                raise ValueError("requested_buffer_frames must be positive")
            object.__setattr__(self, "requested_buffer_frames", requested)
        if self.observed_buffer_frames is not None:
            if isinstance(self.observed_buffer_frames, bool):
                raise ValueError("observed_buffer_frames must be an integer")
            observed = int(self.observed_buffer_frames)
            if observed <= 0:
                raise ValueError("observed_buffer_frames must be positive")
            object.__setattr__(self, "observed_buffer_frames", observed)

        binary_hash = str(self.jamulus_binary_sha256 or "").strip().lower()
        if binary_hash and (
            len(binary_hash) != 64
            or any(character not in "0123456789abcdef" for character in binary_hash)
        ):
            raise ValueError("jamulus_binary_sha256 must be a 64-character SHA-256")
        object.__setattr__(self, "jamulus_binary_sha256", binary_hash)

        if self.platform is AudioRoutePlatform.WINDOWS_ASIO:
            if self.input_device_id != self.output_device_id:
                raise ValueError("Jamulus ASIO uses one driver for both input and output")
            if self.input_device_name != self.output_device_name:
                raise ValueError("Jamulus ASIO input/output driver names must match")

        for display_name in (self.input_device_name, self.output_device_name):
            if any(character in display_name for character in ("\x00", "\r", "\n")):
                raise ValueError("audio device display names contain control characters")
        if self.platform is AudioRoutePlatform.MACOS_COREAUDIO and any(
            "/" in display_name
            for display_name in (self.input_device_name, self.output_device_name)
        ):
            raise ValueError(
                "CoreAudio display names containing '/' cannot form a Jamulus selector"
            )

        if self.platform is AudioRoutePlatform.LINUX_JACK:
            if not self.jack_server.strip():
                raise ValueError("jack_server is required for a Linux JACK route")
            self._validate_ports("jack_input_ports", self.jack_input_ports)
            self._validate_ports("jack_output_ports", self.jack_output_ports)
        elif any(
            value
            for value in (
                self.jack_server,
                *self.jack_input_ports,
                *self.jack_output_ports,
            )
        ):
            raise ValueError("JACK ownership fields are Linux-only")

        if (
            self.confirmation_level is RouteConfirmationLevel.GRAPH_CONFIRMED
            and self.platform is not AudioRoutePlatform.LINUX_JACK
        ):
            raise ValueError(
                "Jamulus 3.12.2 cannot graph-confirm CoreAudio or ASIO routes"
            )
        if (
            self.confirmation_level is not RouteConfirmationLevel.CONFIGURED
            and not self.last_verified_at.strip()
        ):
            raise ValueError("confirmed routes require last_verified_at")
    @staticmethod
    def _validate_channels(name: str, channels: tuple[int, ...]) -> None:
        if len(channels) != 2:
            raise ValueError(f"{name} must contain left and right channel indices")
        if any(
            isinstance(channel, bool)
            or not isinstance(channel, int)
            or not 0 <= channel <= 63
            for channel in channels
        ):
            raise ValueError(f"{name} indices must be integers in the range 0..63")

    @staticmethod
    def _validate_ports(name: str, ports: tuple[str, ...]) -> None:
        if len(ports) != 2 or any(not str(port).strip() for port in ports):
            raise ValueError(f"{name} must contain two non-empty JACK ports")
        if any(len(str(port)) > 512 for port in ports):
            raise ValueError(f"{name} contains an overlong JACK port")

    @property
    def send_channel_count(self) -> int:
        return 2 if self.channel_mode is JamulusChannelMode.STEREO else 1

    @property
    def return_channel_count(self) -> int:
        return 1 if self.channel_mode is JamulusChannelMode.MONO else 2

    @property
    def is_valid(self) -> bool:
        return not bool(self.invalidation_reason.strip())

    def route_affecting_values(self) -> Mapping[str, object]:
        """Return only values whose change invalidates prior route evidence."""

        return {
            "schema": 1,
            "platform": self.platform.value,
            "input_device_id": self.input_device_id,
            "output_device_id": self.output_device_id,
            "input_device_name": self.input_device_name,
            "output_device_name": self.output_device_name,
            "input_channels": list(self.input_channels),
            "output_channels": list(self.output_channels),
            "channel_mode": int(self.channel_mode),
            "sample_rate": self.sample_rate,
            "requested_buffer_frames": self.requested_buffer_frames,
            "observed_buffer_frames": self.observed_buffer_frames,
            "device_generation": self.device_generation,
            "app_version": self.app_version,
            "jamulus_version": self.jamulus_version,
            "jamulus_binary_sha256": self.jamulus_binary_sha256,
            "adapter_version": self.adapter_version,
            "config_generation": self.config_generation,
            "jack_server": self.jack_server,
            "jack_input_ports": list(self.jack_input_ports),
            "jack_output_ports": list(self.jack_output_ports),
        }

    def invalidation_fingerprint(self) -> str:
        canonical = json.dumps(
            self.route_affecting_values(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    def with_confirmation(
        self,
        level: RouteConfirmationLevel,
        *,
        method: str,
        verified_at: str,
    ) -> AudioRouteProfile:
        requested = RouteConfirmationLevel(level)
        rank = {
            RouteConfirmationLevel.CONFIGURED: 0,
            RouteConfirmationLevel.PREFLIGHTED: 1,
            RouteConfirmationLevel.GRAPH_CONFIRMED: 2,
            RouteConfirmationLevel.MUSICIAN_CONFIRMED: 3,
        }
        if rank[requested] < rank[self.confirmation_level]:
            raise ValueError("route confirmation cannot be downgraded")
        return replace(
            self,
            confirmation_level=requested,
            verification_method=method,
            last_verified_at=verified_at,
            invalidation_reason="",
        )

    def invalidate(self, reason: str) -> AudioRouteProfile:
        normalized = str(reason or "").strip()
        if not normalized:
            raise ValueError("an invalidated route requires a reason")
        return replace(
            self,
            confirmation_level=RouteConfirmationLevel.CONFIGURED,
            last_verified_at="",
            verification_method="invalidated",
            invalidation_reason=normalized,
        )


@dataclass(frozen=True, slots=True)
class ProvisionedJamulusConfig:
    path: Path
    backup_path: Path | None
    sha256: str
    profile_fingerprint: str
    previous_existed: bool


class Jamulus3122AudioRouteAdapter:
    """Strict adapter for the documented/pinned Jamulus 3.12.2 settings."""

    JAMULUS_VERSION = "3.12.2"
    ADAPTER_VERSION = "jamulus-3.12.2-route-v1"
    live_send_mute = False
    _BUFFER_FACTORS = {64: 1, 128: 2, 256: 4}

    def validate(self, profile: AudioRouteProfile) -> None:
        if profile.jamulus_version != self.JAMULUS_VERSION:
            raise ValueError("unsupported Jamulus version")
        if profile.adapter_version != self.ADAPTER_VERSION:
            raise ValueError("unsupported audio-route adapter version")

    def jamulus_device_selector(self, profile: AudioRouteProfile) -> str | None:
        self.validate(profile)
        if profile.platform is AudioRoutePlatform.MACOS_COREAUDIO:
            return (
                f"in: {profile.input_device_name}/"
                f"out: {profile.output_device_name}"
            )
        if profile.platform is AudioRoutePlatform.WINDOWS_ASIO:
            return profile.input_device_name
        return None

    def render_inifile(
        self,
        profile: AudioRouteProfile,
        *,
        musician_name: str = "WebJam Musician",
    ) -> bytes:
        """Render only settings proven against pinned 3.12.2 source."""

        self.validate(profile)
        name = str(musician_name or "WebJam Musician").strip()
        if not name or len(name) > 256:
            raise ValueError("musician_name must contain 1..256 characters")

        root = ElementTree.Element("client")

        def setting(key: str, value: object) -> None:
            ElementTree.SubElement(root, key).text = str(value)

        setting("name_base64", self._base64(name))
        setting("audiochannels", int(profile.channel_mode))
        setting("enableaudioalerts", 0)

        selector = self.jamulus_device_selector(profile)
        if selector is not None:
            setting("auddev_base64", self._base64(selector))
            setting("sndcrdinlch", profile.input_channels[0])
            setting("sndcrdinrch", profile.input_channels[1])
            setting("sndcrdoutlch", profile.output_channels[0])
            setting("sndcrdoutrch", profile.output_channels[1])
            if profile.requested_buffer_frames is not None:
                setting(
                    "prefsndcrdbufidx",
                    self._BUFFER_FACTORS[profile.requested_buffer_frames],
                )

        ElementTree.indent(root, space="  ")
        return ElementTree.tostring(
            root,
            encoding="utf-8",
            xml_declaration=True,
            short_empty_elements=False,
        ) + b"\n"

    def launch_arguments(
        self, profile: AudioRouteProfile, config_path: Path
    ) -> tuple[str, ...]:
        self.validate(profile)
        path = Path(config_path).expanduser()
        arguments = ["--inifile", str(path)]
        if profile.platform is AudioRoutePlatform.LINUX_JACK:
            arguments.append("--nojackconnect")
        return tuple(arguments)

    def environment_overrides(self, profile: AudioRouteProfile) -> Mapping[str, str]:
        self.validate(profile)
        if profile.platform is AudioRoutePlatform.LINUX_JACK:
            return {"JACK_DEFAULT_SERVER": profile.jack_server}
        return {}

    def jack_connections(
        self, profile: AudioRouteProfile, *, client_name: str
    ) -> tuple[tuple[str, str], ...]:
        self.validate(profile)
        if profile.platform is not AudioRoutePlatform.LINUX_JACK:
            raise ValueError("JACK graph ownership is Linux-only")
        normalized_name = str(client_name or "").strip()
        if not normalized_name:
            raise ValueError("client_name must not be empty")
        jamulus = f"Jamulus {normalized_name}"
        return (
            (profile.jack_input_ports[0], f"{jamulus}:input left"),
            (profile.jack_input_ports[1], f"{jamulus}:input right"),
            (f"{jamulus}:output left", profile.jack_output_ports[0]),
            (f"{jamulus}:output right", profile.jack_output_ports[1]),
        )

    def provision(
        self,
        profile: AudioRouteProfile,
        target: Path,
        *,
        musician_name: str = "WebJam Musician",
    ) -> ProvisionedJamulusConfig:
        """Atomically install a protected WebJam-owned Jamulus inifile.

        Existing contents are copied atomically to ``<target>.bak`` first.
        Any failure restores the original target before the exception escapes.
        Symlink targets are rejected to keep backup/rollback inside the owned
        directory.
        """

        payload = self.render_inifile(profile, musician_name=musician_name)
        destination = Path(target).expanduser()
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._require_safe_file(destination, allow_missing=True)
        previous_existed = destination.exists()
        previous = destination.read_bytes() if previous_existed else None
        backup = destination.with_name(destination.name + ".bak")
        self._require_safe_file(backup, allow_missing=True)

        if previous is not None:
            self._atomic_write(backup, previous)
        try:
            self._atomic_write(destination, payload)
        except Exception:
            try:
                if previous is None:
                    if destination.exists() and not destination.is_symlink():
                        destination.unlink()
                else:
                    self._atomic_write(destination, previous)
            except Exception as rollback_error:
                raise RuntimeError(
                    "Jamulus config update failed and rollback could not restore it"
                ) from rollback_error
            raise

        return ProvisionedJamulusConfig(
            path=destination,
            backup_path=backup if previous is not None else None,
            sha256=hashlib.sha256(payload).hexdigest(),
            profile_fingerprint=profile.invalidation_fingerprint(),
            previous_existed=previous_existed,
        )

    def restore_backup(self, provisioned: ProvisionedJamulusConfig) -> None:
        backup = provisioned.backup_path
        if backup is None:
            raise ValueError("this provision did not replace an existing config")
        self._require_safe_file(backup, allow_missing=False)
        self._require_safe_file(provisioned.path, allow_missing=True)
        self._atomic_write(provisioned.path, backup.read_bytes())

    @staticmethod
    def _base64(value: str) -> str:
        return base64.b64encode(value.encode("utf-8")).decode("ascii")

    @staticmethod
    def _require_safe_file(path: Path, *, allow_missing: bool) -> None:
        if path.is_symlink():
            raise ValueError(f"refusing Jamulus config symlink: {path.name}")
        if not path.exists():
            if allow_missing:
                return
            raise FileNotFoundError(path)
        mode = path.stat().st_mode
        if not stat.S_ISREG(mode):
            raise ValueError(f"Jamulus config path is not a regular file: {path.name}")

    @staticmethod
    def _atomic_write(path: Path, payload: bytes) -> None:
        descriptor, raw_temp = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        temporary = Path(raw_temp)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = -1
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            path.chmod(0o600)
            try:
                directory = os.open(path.parent, os.O_RDONLY)
            except OSError:
                directory = -1
            if directory >= 0:
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
