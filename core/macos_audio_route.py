"""The macOS boundary between WebJam's device choice and Jamulus.

Jamulus 3.12.2 selects a CoreAudio pair by display name, while CoreAudio gives
WebJam persistent device UIDs.  This module resolves the latter immediately
before launch, rejects ambiguous or unsuitable devices, then writes one
WebJam-owned inifile inside Jamulus's allowed container.  It never modifies a
musician's ``Jamulus.ini`` and it deliberately does *not* claim that writing a
config proves audible sound.

The implementation is macOS-first.  Other platforms retain their existing
system-controlled route until they have an equivalently native, testable
resolver.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import re
import subprocess
from typing import Callable, Mapping, Protocol, Sequence

from core.audio_route_profile import (
    AudioRoutePlatform,
    AudioRouteProfile,
    Jamulus3122AudioRouteAdapter,
    JamulusChannelMode,
    ProvisionedJamulusConfig,
    RouteConfirmationLevel,
)


WEBJAM_ROUTE_INIFILE = "WebJam-route-v1.ini"
JAMULUS_CONTAINER_ID = "app.jamulussoftware.Jamulus"
_PINNED_JAMULUS_VERSION = "3.12.2"


class JamulusAudioRouteError(RuntimeError):
    """A musician-safe route preflight failure.

    ``str(error)`` is intentionally suitable for an action dialog.  Callers
    may log the original exception separately, but must not put filesystem
    paths or CoreAudio status codes into the public message.
    """


class _CoreAudioDevice(Protocol):
    uid: str
    name: str
    input_channels: int
    output_channels: int
    nominal_rate: float


class _CoreAudioSnapshot(Protocol):
    devices: Sequence[_CoreAudioDevice]
    default_input_uid: str
    default_output_uid: str


def jamulus_macos_config_directory(home: Path | None = None) -> Path:
    """Return the one directory used for the owned macOS route config.

    Jamulus accepts only a filename for ``--inifile`` on macOS.  We therefore
    keep the file below the app's standard Data container and launch Jamulus
    with this directory as its working directory.
    """

    root = Path.home() if home is None else Path(home)
    return (
        root
        / "Library"
        / "Containers"
        / JAMULUS_CONTAINER_ID
        / "Data"
        / ".config"
        / "Jamulus"
    )


@dataclass(frozen=True, slots=True)
class PreparedJamulusAudioRoute:
    """A frozen route plan used for one client lifecycle and reconnects."""

    profile: AudioRouteProfile
    provisioned: ProvisionedJamulusConfig
    arguments: tuple[str, ...]
    environment: Mapping[str, str]
    working_directory: Path


def _default_version_probe(binary: str) -> str:
    """Read the version without starting a music session or opening a UI."""

    try:
        completed = subprocess.run(
            [binary, "--version"],
            capture_output=True,
            text=True,
            timeout=8,
        )
    except (OSError, subprocess.SubprocessError):
        return "unverified"
    match = re.search(
        r"(?:version\\s+)?(\\d+\\.\\d+\\.\\d+)",
        f"{completed.stdout}\\n{completed.stderr}",
        flags=re.IGNORECASE,
    )
    return match.group(1) if match else "unverified"


class MacOSJamulusRouteManager:
    """Resolve and safely stage the CoreAudio route for pinned Jamulus."""

    def __init__(
        self,
        *,
        scanner: Callable[[], _CoreAudioSnapshot] | None = None,
        adapter: Jamulus3122AudioRouteAdapter | None = None,
        home: Path | None = None,
        version_probe: Callable[[str], str] = _default_version_probe,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if scanner is None:
            from core.coreaudio_devices import scan_coreaudio_devices

            scanner = scan_coreaudio_devices
        self._scanner = scanner
        self._adapter = adapter or Jamulus3122AudioRouteAdapter()
        self._home = Path.home() if home is None else Path(home)
        self._version_probe = version_probe
        self._now = now or (lambda: datetime.now(timezone.utc))

    def prepare(self, settings, jamulus_binary: str) -> PreparedJamulusAudioRoute:
        """Probe, validate, and atomically stage a new owned client config."""

        version = self._version_probe(str(jamulus_binary or ""))
        if version != _PINNED_JAMULUS_VERSION:
            raise JamulusAudioRouteError(
                "WebJam needs its included Jamulus 3.12.2 music component. "
                "Reinstall WebJam, then try again."
            )

        profile = self._resolve_profile(settings)
        target_dir = jamulus_macos_config_directory(self._home)
        target = target_dir / WEBJAM_ROUTE_INIFILE
        try:
            # Never place the real musician name in a persistent config or a
            # v3 client's process arguments.  Authenticated RPC applies it
            # after launch where supported.
            provisioned = self._adapter.provision(
                profile,
                target,
                musician_name="WebJam Musician",
            )
        except (OSError, ValueError, RuntimeError) as exc:
            raise JamulusAudioRouteError(
                "WebJam couldn't prepare the band audio route. Open Audio MIDI "
                "Setup, reconnect the interface, then try again."
            ) from exc

        arguments = self._adapter.launch_arguments(profile, target)
        if arguments != ("--inifile", WEBJAM_ROUTE_INIFILE):
            # This guards a future adapter regression from passing an absolute
            # path that sandboxed macOS Jamulus cannot read.
            raise JamulusAudioRouteError(
                "WebJam couldn't prepare the band audio route. Reopen WebJam "
                "and try again."
            )
        return PreparedJamulusAudioRoute(
            profile=profile,
            provisioned=provisioned,
            arguments=arguments,
            environment=dict(self._adapter.environment_overrides(profile)),
            working_directory=target_dir,
        )

    def validate_active(self, plan: PreparedJamulusAudioRoute) -> None:
        """Fail closed if an automatic reconnect would silently switch gear."""

        snapshot = self._scan()
        current = self._resolve_profile_from_snapshot(
            snapshot,
            input_uid=plan.profile.input_device_id,
            output_uid=plan.profile.output_device_id,
            requested_buffer_frames=plan.profile.requested_buffer_frames,
        )
        if current.invalidation_fingerprint() != plan.profile.invalidation_fingerprint():
            raise JamulusAudioRouteError(
                "Your band audio device changed. End this session, check the "
                "device in Settings, then start again."
            )
        try:
            # Recheck the fixed owned target on each reconnect.  This catches
            # deletion/symlink replacement and restores the same frozen route;
            # it does not read or alter Jamulus.ini.
            self._adapter.provision(
                plan.profile,
                plan.provisioned.path,
                musician_name="WebJam Musician",
            )
        except (OSError, ValueError, RuntimeError) as exc:
            raise JamulusAudioRouteError(
                "WebJam couldn't restore the band audio route. Reopen WebJam "
                "and try again."
            ) from exc

    def _scan(self) -> _CoreAudioSnapshot:
        try:
            snapshot = self._scanner()
        except Exception as exc:  # noqa: BLE001 - a native framework boundary
            raise JamulusAudioRouteError(
                "WebJam couldn't inspect this Mac's audio devices. Open Audio "
                "MIDI Setup, reconnect the interface, then try again."
            ) from exc
        if str(getattr(snapshot, "error", "") or "").strip():
            raise JamulusAudioRouteError(
                "WebJam couldn't inspect this Mac's audio devices. Open Audio "
                "MIDI Setup, reconnect the interface, then try again."
            )
        if not getattr(snapshot, "devices", ()):
            raise JamulusAudioRouteError(
                "WebJam couldn't find an input and output for band audio. "
                "Connect your interface, then try again."
            )
        return snapshot

    def _resolve_profile(self, settings) -> AudioRouteProfile:
        snapshot = self._scan()
        return self._resolve_profile_from_snapshot(
            snapshot,
            input_uid=str(getattr(settings, "jamulus_audio_input_uid", "") or ""),
            output_uid=str(getattr(settings, "jamulus_audio_output_uid", "") or ""),
            requested_buffer_frames=self._requested_buffer_frames(settings),
        )

    @staticmethod
    def _requested_buffer_frames(settings) -> int:
        raw = getattr(settings, "audio_blocksize", 0)
        try:
            value = int(raw or 0)
        except (TypeError, ValueError):
            value = 0
        return value if value in {64, 128, 256} else 128

    def _resolve_profile_from_snapshot(
        self,
        snapshot: _CoreAudioSnapshot,
        *,
        input_uid: str,
        output_uid: str,
        requested_buffer_frames: int | None,
    ) -> AudioRouteProfile:
        devices = tuple(getattr(snapshot, "devices", ()))
        chosen_input = self._choose_device(
            devices,
            requested_uid=input_uid,
            default_uid=str(getattr(snapshot, "default_input_uid", "") or ""),
            direction="input",
        )
        chosen_output = self._choose_device(
            devices,
            requested_uid=output_uid,
            default_uid=str(getattr(snapshot, "default_output_uid", "") or ""),
            direction="output",
        )
        self._validate_device(chosen_input, direction="input")
        self._validate_device(chosen_output, direction="output")
        self._reject_ambiguous_selector(devices, chosen_input, direction="input")
        self._reject_ambiguous_selector(devices, chosen_output, direction="output")

        input_count = int(chosen_input.input_channels)
        output_count = int(chosen_output.output_channels)
        input_channels = (0, 1) if input_count >= 2 else (0, 0)
        output_channels = (0, 1) if output_count >= 2 else (0, 0)
        channel_mode = (
            JamulusChannelMode.MONO
            if output_count == 1
            else JamulusChannelMode.MONO_IN_STEREO_OUT
        )
        generation = self._device_generation(devices)
        profile = AudioRouteProfile(
            platform=AudioRoutePlatform.MACOS_COREAUDIO,
            input_device_id=str(chosen_input.uid),
            output_device_id=str(chosen_output.uid),
            input_device_name=str(chosen_input.name),
            output_device_name=str(chosen_output.name),
            input_channels=input_channels,
            output_channels=output_channels,
            channel_mode=channel_mode,
            requested_buffer_frames=requested_buffer_frames,
            device_generation=generation,
            app_version=self._app_version(),
        )
        return profile.with_confirmation(
            RouteConfirmationLevel.PREFLIGHTED,
            method="coreaudio_uid_name_capability_rate_probe",
            verified_at=self._timestamp(),
        )

    @staticmethod
    def _choose_device(
        devices: Sequence[_CoreAudioDevice],
        *,
        requested_uid: str,
        default_uid: str,
        direction: str,
    ) -> _CoreAudioDevice:
        wanted = requested_uid.strip() or default_uid.strip()
        if not wanted:
            raise JamulusAudioRouteError(
                f"WebJam couldn't find your Mac's default band {direction}. "
                "Open Audio MIDI Setup, choose a device, then try again."
            )
        for device in devices:
            if str(device.uid) == wanted:
                return device
        if requested_uid.strip():
            raise JamulusAudioRouteError(
                f"The selected band {direction} is no longer connected. Reconnect "
                "it or choose another device in Settings."
            )
        raise JamulusAudioRouteError(
            f"WebJam couldn't find your Mac's default band {direction}. Open "
            "Audio MIDI Setup, choose a device, then try again."
        )

    @staticmethod
    def _validate_device(device: _CoreAudioDevice, *, direction: str) -> None:
        name = str(device.name or "")
        if not name or any(char in name for char in ("/", "\\x00", "\\r", "\\n")):
            raise JamulusAudioRouteError(
                f"WebJam can't use this band {direction}. Rename the device in "
                "Audio MIDI Setup, then try again."
            )
        channels = int(
            device.input_channels if direction == "input" else device.output_channels
        )
        if channels < 1:
            raise JamulusAudioRouteError(
                f"The selected band {direction} has no usable channels. Choose "
                "another device in Settings."
            )
        rate = float(device.nominal_rate or 0)
        if abs(rate - 48_000.0) > 0.5:
            raise JamulusAudioRouteError(
                f"Set the selected band {direction} to 48 kHz in Audio MIDI "
                "Setup, then try again."
            )

    @staticmethod
    def _reject_ambiguous_selector(
        devices: Sequence[_CoreAudioDevice],
        chosen: _CoreAudioDevice,
        *,
        direction: str,
    ) -> None:
        name = str(chosen.name)
        matching_uids = {
            str(device.uid)
            for device in devices
            if str(device.name) == name
            and int(
                device.input_channels
                if direction == "input"
                else device.output_channels
            )
            > 0
        }
        if len(matching_uids) > 1:
            raise JamulusAudioRouteError(
                f"Two band {direction} devices have the same name. Rename one "
                "in Audio MIDI Setup, then try again."
            )

    @staticmethod
    def _device_generation(devices: Sequence[_CoreAudioDevice]) -> str:
        rows = sorted(
            "\\x1f".join(
                (
                    str(device.uid),
                    str(device.name),
                    str(int(device.input_channels)),
                    str(int(device.output_channels)),
                    f"{float(device.nominal_rate or 0):.3f}",
                )
            )
            for device in devices
        )
        return "coreaudio:" + hashlib.sha256(
            "\\x1e".join(rows).encode("utf-8")
        ).hexdigest()[:24]

    @staticmethod
    def _app_version() -> str:
        try:
            from webjam_qt import __version__

            return str(__version__)
        except Exception:  # pragma: no cover - defensive against partial builds
            return "unknown"

    def _timestamp(self) -> str:
        value = self._now()
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
