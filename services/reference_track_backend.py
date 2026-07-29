"""Platform audio bridge for a Jamulus-routed Reference Track.

The shipping implementation is deliberately macOS-only and fail-closed.  It
requires a 48-kHz BlackHole device with at least four input/output channels:

* BlackHole channels 0/1 carry WebJam's decoded song into a second Jamulus
  client's inputs.
* BlackHole channels 2/3 receive that client's return mix, keeping it
  physically separate from the song input.

The client has its own profile, UDP port, authenticated loopback RPC port, and
secret.  Before the output stream opens, every current return fader must accept
the pinned client's exact zero-level result.  A monitor repeats that proof for
late joiners; loss of RPC, the route, or the owned process immediately makes
the callback emit silence.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import stat
import subprocess
import sys
import threading
import time
from typing import Callable, Mapping

import numpy as np

from core.audio_route_profile import (
    AudioRoutePlatform,
    AudioRouteProfile,
    Jamulus3122AudioRouteAdapter,
    JamulusChannelMode,
    ProvisionedJamulusConfig,
    RouteConfirmationLevel,
)
from core.coreaudio_devices import CoreAudioScan
from core.coreaudio_process_route import (
    CoreAudioProcessRouteError,
    CoreAudioProcessRouteProbe,
    CoreAudioProcessRouteSnapshot,
)
from core.file_io import atomic_write_text
from core.jamulus_endpoint import parse_jamulus_endpoint
from core.macos_audio_route import jamulus_macos_config_directory
from core.reference_track import (
    REFERENCE_MAX_DECODE_FRAMES,
    REFERENCE_SAMPLE_RATE,
    ReferenceAudioBridgeSession,
    ReferenceTrackCapability,
    ReferenceTrackError,
    ReferenceTrackLaunchContext,
)


REFERENCE_PROFILE_FILENAME = "WebJam-reference-track-v1.ini"
REFERENCE_SECRET_FILENAME = ".WebJam-reference-track-v1.rpc-secret"
REFERENCE_PARTICIPANT_NAME = "WebJam Track"
_PINNED_JAMULUS_VERSION = "3.12.2"
_RPC_MAX_LINE_BYTES = 1024 * 1024
_RPC_READY_TIMEOUT_S = 12.0
_RPC_CALL_TIMEOUT_S = 1.5
_FADER_RECHECK_SECONDS = 0.4
_ROUTE_RECHECK_SECONDS = 0.4
_ROUTE_PROOF_MAX_AGE_SECONDS = 1.2
_MAX_CLIENT_ROWS = 64
_UNCERTIFIED_ROUTE_DETAIL = (
    "The Reference Track engine is included, but playback is locked in this "
    "private test candidate until the BlackHole route, direct-monitor "
    "isolation, and CoreAudio device-switch behavior pass the physical macOS "
    "pilot."
)


class _UnavailableReferenceBackend:
    def __init__(self, platform: str) -> None:
        self._platform = platform

    def capability(
        self, audience_bridge_active: bool = False
    ) -> ReferenceTrackCapability:
        del audience_bridge_active
        if self._platform.startswith("win"):
            detail = (
                "Reference Track is not available on Windows yet. Its "
                "VB-CABLE/JACK isolation backend still needs physical proof."
            )
            platform = "windows"
            backend = "vb-cable-jack"
            reason_code = "windows_backend_unavailable"
        elif self._platform.startswith("linux"):
            detail = (
                "Reference Track is not available on Linux yet. Its JACK "
                "isolation backend still needs physical proof."
            )
            platform = "linux"
            backend = "jack"
            reason_code = "linux_backend_unavailable"
        else:
            detail = "Reference Track routing is not available on this platform."
            platform = self._platform or "unknown"
            backend = "unavailable"
            reason_code = "unsupported_platform"
        return ReferenceTrackCapability(
            False,
            platform,
            detail,
            backend=backend,
            reason_code=reason_code,
        )

    def prepare(
        self, context: ReferenceTrackLaunchContext
    ) -> ReferenceAudioBridgeSession:
        del context
        raise ReferenceTrackError(self.capability().detail)


@dataclass(frozen=True, slots=True)
class _BlackHoleRoute:
    uid: str
    name: str
    object_id: int
    sounddevice_index: int
    channels: int
    generation: str


def _default_version_probe(binary: str) -> str:
    try:
        result = subprocess.run(
            [binary, "--version"],
            capture_output=True,
            text=True,
            timeout=8.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    import re

    match = re.search(
        r"(?:version\s+)?(\d+\.\d+\.\d+)",
        f"{result.stdout}\n{result.stderr}",
        flags=re.IGNORECASE,
    )
    return match.group(1) if match else ""


def _default_headless_client_probe(binary: str) -> bool:
    """Require a true headless client, not a GUI build run with ``--nogui``.

    In pinned Jamulus 3.12.2, the JSON-RPC fader handler applies gains directly
    only in a compile-time HEADLESS client.  A GUI build launched with the
    runtime ``--nogui`` flag returns ``"ok"`` but has no mixer dialog connected
    to apply the command.  On macOS the GUI build is unambiguously linked
    against QtWidgets; a separately packaged headless client must not be.
    """

    if not sys.platform.startswith("darwin"):
        return False
    try:
        result = subprocess.run(
            ["/usr/bin/otool", "-L", binary],
            capture_output=True,
            text=True,
            timeout=8.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and "QtWidgets.framework" not in result.stdout


def _default_port_allocator(kind: str, excluded: set[int]) -> int:
    socket_type = socket.SOCK_DGRAM if kind == "udp" else socket.SOCK_STREAM
    for _attempt in range(32):
        probe = socket.socket(socket.AF_INET, socket_type)
        try:
            probe.bind(("127.0.0.1", 0))
            port = int(probe.getsockname()[1])
        finally:
            probe.close()
        if port not in excluded:
            return port
    raise ReferenceTrackError(
        "WebJam couldn't reserve a separate local port for Reference Track."
    )


class _ReferenceRpcControl:
    """Small synchronous owner for the dedicated Jamulus client's RPC socket."""

    _ALLOWED = frozenset(
        {
            "jamulus/apiAuth",
            "jamulus/getMode",
            "jamulusclient/getClientList",
            "jamulusclient/setFaderLevel",
        }
    )

    def __init__(
        self,
        port: int,
        secret: str,
        *,
        socket_factory: Callable[..., socket.socket] = socket.create_connection,
    ) -> None:
        self._port = int(port)
        self._secret = str(secret)
        self._socket_factory = socket_factory
        self._socket: socket.socket | None = None
        self._buffer = bytearray()
        self._request_id = 0
        self._closed = False

    def connect(self) -> None:
        if self._closed:
            raise ReferenceTrackError("Reference Track control was already closed.")
        self._disconnect_socket()
        try:
            sock = self._socket_factory(
                ("127.0.0.1", self._port), timeout=_RPC_CALL_TIMEOUT_S
            )
            sock.settimeout(_RPC_CALL_TIMEOUT_S)
            self._socket = sock
            result = self.call("jamulus/apiAuth", {"secret": self._secret})
            if result != "ok":
                raise ReferenceTrackError(
                    "Reference Track couldn't authenticate its private "
                    "Jamulus control."
                )
            mode = self.call("jamulus/getMode", {})
            if not isinstance(mode, Mapping) or mode.get("mode") != "client":
                raise ReferenceTrackError(
                    "Reference Track control did not reach its owned "
                    "Jamulus client."
                )
        except ReferenceTrackError:
            self._disconnect_socket()
            raise
        except Exception:  # noqa: BLE001 - socket factory boundary
            self._disconnect_socket()
            raise ReferenceTrackError(
                "Reference Track's private Jamulus control is not ready."
            ) from None

    def call(self, method: str, params: Mapping[str, object]) -> object:
        if method not in self._ALLOWED:
            raise ReferenceTrackError(
                "Reference Track refused an unsupported Jamulus command."
            )
        sock = self._socket
        if sock is None or self._closed:
            raise ReferenceTrackError("Reference Track control is unavailable.")
        self._request_id += 1
        request_id = self._request_id
        payload = (
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": method,
                    "params": dict(params),
                },
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        try:
            sock.sendall(payload)
            while True:
                response = self._read_object()
                if response.get("id") != request_id:
                    continue
                if "error" in response:
                    raise ReferenceTrackError(
                        "Reference Track's Jamulus client refused a safety command."
                    )
                if "result" not in response:
                    raise ReferenceTrackError(
                        "Reference Track received an invalid Jamulus response."
                    )
                return response["result"]
        except ReferenceTrackError:
            raise
        except (OSError, ValueError, TypeError) as exc:
            raise ReferenceTrackError(
                "Reference Track lost its private Jamulus control connection."
            ) from exc

    def client_rows(self) -> tuple[tuple[int, int], ...]:
        """Return validated ``(array_index, server_id)`` rows.

        Jamulus calls the command parameter ``channelIndex`` literally: it is
        the position in the current client array.  The row's ``id`` is a
        separate server-assigned identity and can be sparse after reconnects.
        """

        result = self.call("jamulusclient/getClientList", {})
        if not isinstance(result, Mapping) or not isinstance(
            result.get("clients"), list
        ):
            raise ReferenceTrackError(
                "Reference Track couldn't verify the Jamulus return mix."
            )
        raw_clients = result["clients"]
        if not 1 <= len(raw_clients) <= _MAX_CLIENT_ROWS:
            raise ReferenceTrackError(
                "Reference Track is waiting for its Jamulus participant to connect."
            )
        rows: list[tuple[int, int]] = []
        seen_ids: set[int] = set()
        for index, raw in enumerate(raw_clients):
            if not isinstance(raw, Mapping):
                raise ReferenceTrackError(
                    "Reference Track couldn't verify the Jamulus return mix."
                )
            value = raw.get("id")
            if not isinstance(value, int) or isinstance(value, bool):
                raise ReferenceTrackError(
                    "Reference Track couldn't verify the Jamulus return mix."
                )
            channel_id = value
            if channel_id < 0 or channel_id in seen_ids:
                raise ReferenceTrackError(
                    "Reference Track couldn't verify the Jamulus return mix."
                )
            seen_ids.add(channel_id)
            rows.append((index, channel_id))
        return tuple(rows)

    def prove_all_faders_zero(self) -> int:
        rows = self.client_rows()
        for channel_index, _server_id in rows:
            result = self.call(
                "jamulusclient/setFaderLevel",
                {"channelIndex": channel_index, "level": 0},
            )
            # The pinned Jamulus 3.12.2 JSON-RPC contract returns exact "ok".
            # Null, boolean, and the server-recorder token "acknowledged" are
            # not evidence that the client accepted the command.
            if result != "ok":
                raise ReferenceTrackError(
                    "Reference Track couldn't prove that every return fader is zero."
                )
        # Refuse a successful-looking proof if the roster changed while its
        # position-based commands were being applied.
        if self.client_rows() != rows:
            raise ReferenceTrackError(
                "Reference Track's Jamulus roster changed during route proof."
            )
        return len(rows)

    def _read_object(self) -> dict:
        sock = self._socket
        if sock is None:
            raise ReferenceTrackError("Reference Track control is unavailable.")
        while True:
            newline = self._buffer.find(b"\n")
            if newline >= 0:
                raw = bytes(self._buffer[:newline])
                del self._buffer[: newline + 1]
                try:
                    value = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise ReferenceTrackError(
                        "Reference Track received an invalid Jamulus response."
                    ) from exc
                if not isinstance(value, dict):
                    raise ReferenceTrackError(
                        "Reference Track received an invalid Jamulus response."
                    )
                return value
            chunk = sock.recv(16_384)
            if not chunk:
                raise ReferenceTrackError(
                    "Reference Track's Jamulus control connection closed."
                )
            self._buffer.extend(chunk)
            if len(self._buffer) > _RPC_MAX_LINE_BYTES:
                raise ReferenceTrackError(
                    "Reference Track refused an oversized Jamulus response."
                )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._secret = ""
        self._disconnect_socket()

    def _disconnect_socket(self) -> None:
        sock = self._socket
        self._socket = None
        self._buffer.clear()
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass


class MacOSBlackHoleReferenceBackend:
    """Capability-gated owner of the macOS second-client topology."""

    def __init__(
        self,
        *,
        platform: str | None = None,
        scanner: Callable[[], CoreAudioScan] | None = None,
        sounddevice_module: object | None = None,
        version_probe: Callable[[str], str] = _default_version_probe,
        headless_client_probe: Callable[
            [str], bool
        ] = _default_headless_client_probe,
        popen_factory: Callable[..., subprocess.Popen] = subprocess.Popen,
        port_allocator: Callable[[str, set[int]], int] = _default_port_allocator,
        rpc_factory: Callable[[int, str], _ReferenceRpcControl] | None = None,
        process_route_probe: CoreAudioProcessRouteProbe | None = None,
        home: Path | None = None,
        physical_route_certified: bool = False,
    ) -> None:
        self._platform = str(platform or sys.platform).lower()
        # This is deliberately constructor-only.  Production wiring never
        # enables it, and there is no environment variable, setting, CLI flag,
        # or UI action that can turn incomplete physical evidence into route
        # authority.  Focused tests and a controlled source pilot may exercise
        # the implementation by constructing the backend explicitly.
        if not isinstance(physical_route_certified, bool):
            raise TypeError("physical_route_certified must be a boolean")
        self._physical_route_certified = physical_route_certified
        if scanner is None:
            from core.coreaudio_devices import scan_coreaudio_devices

            scanner = scan_coreaudio_devices
        self._scanner = scanner
        self._sounddevice = sounddevice_module
        self._version_probe = version_probe
        self._headless_client_probe = headless_client_probe
        self._popen_factory = popen_factory
        self._port_allocator = port_allocator
        self._rpc_factory = rpc_factory or (
            lambda port, secret: _ReferenceRpcControl(port, secret)
        )
        self._process_route_probe = (
            process_route_probe
            if process_route_probe is not None
            else CoreAudioProcessRouteProbe(platform_name=self._platform)
        )
        self._home = Path.home() if home is None else Path(home)
        self._lock = threading.RLock()
        self._active: _MacReferenceSession | None = None

    def capability(
        self, audience_bridge_active: bool = False
    ) -> ReferenceTrackCapability:
        if not self._platform.startswith("darwin"):
            return _UnavailableReferenceBackend(self._platform).capability()
        if not self._physical_route_certified:
            return ReferenceTrackCapability(
                False,
                "macos",
                _UNCERTIFIED_ROUTE_DETAIL,
                backend="blackhole",
                reason_code="physical_certification_required",
            )
        if audience_bridge_active:
            return ReferenceTrackCapability(
                False,
                "macos",
                "Reference Track can't share BlackHole with the Webex audience "
                "bridge. Switch Webex to talkback or video-only first.",
                backend="blackhole",
                reason_code="audience_bridge_conflict",
            )
        live_route_error = self._process_route_probe.capability_error()
        if live_route_error:
            return ReferenceTrackCapability(
                False,
                "macos",
                live_route_error,
                backend="blackhole",
                reason_code="live_route_unavailable",
            )
        try:
            route = self._resolve_route()
        except ReferenceTrackError as exc:
            return ReferenceTrackCapability(
                False,
                "macos",
                str(exc),
                backend="blackhole",
                reason_code="blackhole_unavailable",
            )
        return ReferenceTrackCapability(
            True,
            "macos",
            "BlackHole is ready at 48 kHz. WebJam will verify the primary "
            "Jamulus process's live route before playback.",
            route.name,
            backend="blackhole",
            reason_code="ready",
        )

    def prepare(
        self, context: ReferenceTrackLaunchContext
    ) -> ReferenceAudioBridgeSession:
        # Do not rely on a prior capability check.  The mutation boundary has
        # its own release lock so alternate callers cannot launch the backing
        # client or open BlackHole from an uncertified production backend.
        if not self._physical_route_certified:
            raise ReferenceTrackError(_UNCERTIFIED_ROUTE_DETAIL)
        if context.audience_bridge_active:
            raise ReferenceTrackError(self.capability(True).detail)
        if not self._platform.startswith("darwin"):
            raise ReferenceTrackError(self.capability().detail)
        with self._lock:
            if self._active is not None:
                if not self._active.health_error():
                    raise ReferenceTrackError(
                        "A Reference Track route is already active."
                    )
                self._active.stop()
                self._active = None

            route = self._resolve_route()
            primary_route = self._prove_primary_route(context, route)
            binary = Path(context.jamulus_binary).expanduser()
            if (
                not binary.is_file()
                or not os.access(binary, os.X_OK)
                or self._version_probe(str(binary)) != _PINNED_JAMULUS_VERSION
            ):
                raise ReferenceTrackError(
                    "Reference Track needs the included Jamulus 3.12.2 component."
                )
            if not self._headless_client_probe(str(binary)):
                raise ReferenceTrackError(
                    "Reference Track needs a packaged headless Jamulus client. "
                    "This build's interactive Jamulus client cannot prove zero "
                    "return faders while hidden."
                )
            server = self._normalized_endpoint(context.server_address)
            excluded = {
                int(context.primary_udp_port),
                int(context.primary_rpc_port),
            }
            udp_port = self._port_allocator("udp", excluded)
            excluded.add(udp_port)
            rpc_port = self._port_allocator("tcp", excluded)
            excluded.add(rpc_port)
            if len({udp_port, rpc_port, *excluded}) < 4:
                raise ReferenceTrackError(
                    "Reference Track couldn't reserve separate local ports."
                )

            session = self._prepare_session(
                route=route,
                binary=str(binary),
                server=server,
                udp_port=udp_port,
                rpc_port=rpc_port,
                context=context,
                primary_route=primary_route,
            )
            self._active = session
            return session

    def _prove_primary_route(
        self,
        context: ReferenceTrackLaunchContext,
        route: _BlackHoleRoute,
    ) -> CoreAudioProcessRouteSnapshot:
        try:
            scan = self._scanner()
        except Exception as exc:  # noqa: BLE001 - native hot-plug boundary
            raise ReferenceTrackError(
                "Reference Track couldn't read a fresh CoreAudio device snapshot."
            ) from exc
        try:
            current_route = self._resolve_route_from_scan(scan)
        except ReferenceTrackError:
            raise ReferenceTrackError(
                "Reference Track stopped because its BlackHole route changed."
            ) from None
        try:
            proof = self._process_route_probe.snapshot(
                context.primary_process_id, scan
            )
        except CoreAudioProcessRouteError as exc:
            raise ReferenceTrackError(str(exc)) from None
        if current_route != route:
            raise ReferenceTrackError(
                "Reference Track stopped because its BlackHole route changed."
            )

        live_devices = (proof.input_device, proof.output_device)
        if any(
            device.object_id == route.object_id
            or device.uid == route.uid
            or "blackhole" in device.name.casefold()
            for device in live_devices
        ):
            raise ReferenceTrackError(
                "Reference Track can't start while the primary Jamulus client "
                "is using BlackHole."
            )
        expected_names = (
            str(context.primary_input_device_name or "").strip(),
            str(context.primary_output_device_name or "").strip(),
        )
        live_names = (proof.input_device.name, proof.output_device.name)
        if any(
            expected and expected.casefold() != live.casefold()
            for expected, live in zip(expected_names, live_names, strict=True)
        ):
            raise ReferenceTrackError(
                "The primary Jamulus live route does not match its current "
                "launch profile. Reconnect band audio, then try again."
            )
        return proof

    def _prepare_session(
        self,
        *,
        route: _BlackHoleRoute,
        binary: str,
        server: str,
        udp_port: int,
        rpc_port: int,
        context: ReferenceTrackLaunchContext,
        primary_route: CoreAudioProcessRouteSnapshot,
    ) -> "_MacReferenceSession":
        config_dir = jamulus_macos_config_directory(self._home)
        profile = AudioRouteProfile(
            platform=AudioRoutePlatform.MACOS_COREAUDIO,
            input_device_id=route.uid,
            output_device_id=route.uid,
            input_device_name=route.name,
            output_device_name=route.name,
            input_channels=(0, 1),
            output_channels=(2, 3),
            channel_mode=JamulusChannelMode.STEREO,
            sample_rate=REFERENCE_SAMPLE_RATE,
            requested_buffer_frames=128,
            device_generation=route.generation,
            app_version=self._app_version(),
            confirmation_level=RouteConfirmationLevel.PREFLIGHTED,
            last_verified_at="runtime",
            verification_method="coreaudio_blackhole_split_channels",
        )
        adapter = Jamulus3122AudioRouteAdapter()
        provisioned: ProvisionedJamulusConfig | None = None
        secret_path = config_dir / REFERENCE_SECRET_FILENAME
        secret_value = ""
        process: subprocess.Popen | None = None
        rpc: _ReferenceRpcControl | None = None
        try:
            provisioned = adapter.provision(
                profile,
                config_dir / REFERENCE_PROFILE_FILENAME,
                musician_name=REFERENCE_PARTICIPANT_NAME,
            )
            secret_value = secrets.token_urlsafe(32)
            atomic_write_text(secret_path, secret_value + "\n", mode=0o600)
            sounddevice_module = self._load_sounddevice()
            rpc = self._rpc_factory(rpc_port, secret_value)
            command = [
                binary,
                "--nogui",
                "--mutemyown",
                "--inifile",
                REFERENCE_PROFILE_FILENAME,
                "--clientname",
                REFERENCE_PARTICIPANT_NAME,
                "--connect",
                server,
                "--port",
                str(udp_port),
                "--jsonrpcbindip",
                "127.0.0.1",
                "--jsonrpcport",
                str(rpc_port),
                "--jsonrpcsecretfile",
                str(secret_path),
            ]
            process = self._popen_factory(
                command,
                cwd=str(config_dir),
                env=self._child_environment(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as exc:
            if rpc is not None:
                rpc.close()
            terminated = self._terminate_process(process)
            cleaned = (
                self._cleanup_owned_files(adapter, provisioned, secret_path)
                if terminated
                else False
            )
            secret_value = ""
            if not terminated:
                raise ReferenceTrackError(
                    "Reference Track couldn't confirm that its owned Jamulus "
                    "client stopped after startup failed."
                ) from exc
            if not cleaned:
                raise ReferenceTrackError(
                    "Reference Track stopped after startup failed, but its "
                    "private cleanup could not be confirmed."
                ) from exc
            if isinstance(exc, ReferenceTrackError):
                raise
            raise ReferenceTrackError(
                "WebJam couldn't prepare a safe Reference Track route."
            ) from exc
        secret_value = ""

        def prove_routes() -> CoreAudioProcessRouteSnapshot:
            return self._prove_primary_route(context, route)

        return _MacReferenceSession(
            route=route,
            process=process,
            rpc=rpc,
            sounddevice_module=sounddevice_module,
            route_proof=primary_route,
            prove_routes=prove_routes,
            adapter=adapter,
            provisioned=provisioned,
            secret_path=secret_path,
            on_stopped=self._session_stopped,
        )

    def _resolve_route(self) -> _BlackHoleRoute:
        try:
            scan = self._scanner()
        except Exception as exc:  # noqa: BLE001
            raise ReferenceTrackError(
                "WebJam couldn't inspect BlackHole safely."
            ) from exc
        if not isinstance(scan, CoreAudioScan) or scan.error:
            raise ReferenceTrackError("WebJam couldn't inspect BlackHole safely.")
        return self._resolve_route_from_scan(scan)

    def _resolve_route_from_scan(self, scan: CoreAudioScan) -> _BlackHoleRoute:
        if not isinstance(scan, CoreAudioScan) or scan.error:
            raise ReferenceTrackError("WebJam couldn't inspect BlackHole safely.")
        candidates = [
            device
            for device in scan.devices
            if "blackhole" in device.name.casefold()
            and device.input_channels >= 4
            and device.output_channels >= 4
            and device.nominal_rate is not None
            and abs(float(device.nominal_rate) - REFERENCE_SAMPLE_RATE) < 0.5
        ]
        if not candidates:
            raise ReferenceTrackError(
                "Reference Track needs BlackHole 16ch or 64ch set to 48 kHz. "
                "BlackHole 2ch cannot isolate the Jamulus return mix safely."
            )
        candidates.sort(key=lambda value: (value.input_channels, value.name, value.uid))
        chosen = candidates[0]
        duplicate_names = [
            device for device in scan.devices if device.name == chosen.name
        ]
        if len(duplicate_names) != 1:
            raise ReferenceTrackError(
                "More than one CoreAudio device has the selected BlackHole name. "
                "Rename or remove the duplicate before using Reference Track."
            )
        sd = self._load_sounddevice()
        try:
            devices = tuple(sd.query_devices())
        except Exception as exc:
            raise ReferenceTrackError(
                "WebJam couldn't open BlackHole for Reference Track."
            ) from exc
        matches = [
            (index, raw)
            for index, raw in enumerate(devices)
            if str(raw.get("name", "")) == chosen.name
            and int(raw.get("max_output_channels", 0)) >= 2
            and abs(
                float(raw.get("default_samplerate", 0.0))
                - REFERENCE_SAMPLE_RATE
            )
            < 0.5
        ]
        if len(matches) != 1:
            raise ReferenceTrackError(
                "WebJam couldn't match one unambiguous 48-kHz BlackHole output."
            )
        generation_payload = "|".join(
            f"{device.uid}:{device.name}:{device.input_channels}:"
            f"{device.output_channels}:{device.nominal_rate}"
            for device in sorted(scan.devices, key=lambda item: item.uid)
        ).encode("utf-8")
        return _BlackHoleRoute(
            uid=chosen.uid,
            name=chosen.name,
            object_id=chosen.object_id,
            sounddevice_index=matches[0][0],
            channels=min(chosen.input_channels, chosen.output_channels),
            generation=hashlib.sha256(generation_payload).hexdigest(),
        )

    def _load_sounddevice(self):
        if self._sounddevice is not None:
            return self._sounddevice
        try:
            import sounddevice as sd  # type: ignore
        except Exception as exc:  # noqa: BLE001
            raise ReferenceTrackError(
                "Reference Track audio support is unavailable in this build."
            ) from exc
        self._sounddevice = sd
        return sd

    @staticmethod
    def _normalized_endpoint(value: str) -> str:
        try:
            endpoint = parse_jamulus_endpoint(value)
        except (TypeError, ValueError) as exc:
            raise ReferenceTrackError(
                "Reference Track needs the current Jamulus server address."
            ) from exc
        host = f"[{endpoint.host}]" if ":" in endpoint.host else endpoint.host
        return f"{host}:{endpoint.port}"

    @staticmethod
    def _wait_for_rpc(
        process: subprocess.Popen, rpc: _ReferenceRpcControl
    ) -> None:
        deadline = time.monotonic() + _RPC_READY_TIMEOUT_S
        last_error: ReferenceTrackError | None = None
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise ReferenceTrackError(
                    "The Reference Track Jamulus client exited during startup."
                )
            try:
                rpc.connect()
                return
            except ReferenceTrackError as exc:
                last_error = exc
                time.sleep(0.15)
        raise ReferenceTrackError(
            "Reference Track's private Jamulus control did not become ready."
        ) from last_error

    @staticmethod
    def _child_environment() -> dict[str, str]:
        environment = os.environ.copy()
        if environment.get("QT_QPA_PLATFORM", "").strip().lower() == "offscreen":
            environment.pop("QT_QPA_PLATFORM", None)
        rules = environment.get("QT_LOGGING_RULES", "").strip().rstrip(";")
        environment["QT_LOGGING_RULES"] = (
            f"{rules};default.warning=false" if rules else "default.warning=false"
        )
        return environment

    @staticmethod
    def _app_version() -> str:
        try:
            from webjam_qt import __version__

            return str(__version__)
        except Exception:  # noqa: BLE001
            return ""

    @staticmethod
    def _terminate_process(process: subprocess.Popen | None) -> bool:
        if process is None or process.poll() is not None:
            return True
        try:
            process.terminate()
            try:
                process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2.0)
        except Exception:  # noqa: BLE001
            return process.poll() is not None
        return process.poll() is not None

    @staticmethod
    def _cleanup_owned_files(
        adapter: Jamulus3122AudioRouteAdapter,
        provisioned: ProvisionedJamulusConfig | None,
        secret_path: Path,
    ) -> bool:
        clean = True
        try:
            secret_mode = secret_path.lstat().st_mode
        except FileNotFoundError:
            pass
        except OSError:
            clean = False
        else:
            if stat.S_ISREG(secret_mode) and not secret_path.is_symlink():
                try:
                    secret_path.unlink()
                except OSError:
                    clean = False
            else:
                clean = False
        if provisioned is None:
            return clean and not (
                secret_path.exists() or secret_path.is_symlink()
            )

        target = provisioned.path
        backup = provisioned.backup_path
        try:
            if provisioned.previous_existed:
                if backup is None:
                    return False
                previous = backup.read_bytes()
                adapter.restore_backup(provisioned)
                if target.read_bytes() != previous:
                    clean = False
            elif target.exists() or target.is_symlink():
                if (
                    not target.is_file()
                    or target.is_symlink()
                    or hashlib.sha256(target.read_bytes()).hexdigest()
                    != provisioned.sha256
                ):
                    clean = False
                else:
                    target.unlink()
            if backup is not None and backup.is_file() and not backup.is_symlink():
                backup.unlink()
        except (OSError, ValueError, RuntimeError):
            clean = False
        if secret_path.exists() or secret_path.is_symlink():
            clean = False
        if provisioned.previous_existed:
            if not target.is_file() or target.is_symlink():
                clean = False
        elif target.exists() or target.is_symlink():
            clean = False
        if backup is not None and (backup.exists() or backup.is_symlink()):
            clean = False
        return clean

    def _session_stopped(self, session: "_MacReferenceSession") -> None:
        with self._lock:
            if self._active is session:
                self._active = None


class _MacReferenceSession:
    def __init__(
        self,
        *,
        route: _BlackHoleRoute,
        process: subprocess.Popen,
        rpc: _ReferenceRpcControl,
        sounddevice_module: object,
        route_proof: CoreAudioProcessRouteSnapshot,
        prove_routes: Callable[[], CoreAudioProcessRouteSnapshot],
        adapter: Jamulus3122AudioRouteAdapter,
        provisioned: ProvisionedJamulusConfig,
        secret_path: Path,
        on_stopped: Callable[["_MacReferenceSession"], None],
    ) -> None:
        self._route = route
        self._process = process
        self._rpc = rpc
        self._sounddevice = sounddevice_module
        self._route_proof = route_proof
        self._prove_routes = prove_routes
        self._adapter = adapter
        self._provisioned = provisioned
        self._secret_path = secret_path
        self._on_stopped = on_stopped
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._monitor: threading.Thread | None = None
        self._stream = None
        self._pull: Callable[[int], np.ndarray] | None = None
        self._health_error = ""
        self._teardown_started = False
        self._cleanup_complete = False
        self._control_ready = False
        self._route_proof_monotonic = 0.0
        self._route_proof_wall = 0.0

    @property
    def route_name(self) -> str:
        return self._route.name

    def start(self, pull: Callable[[int], np.ndarray]) -> None:
        if not callable(pull):
            raise TypeError("pull must be callable")
        with self._lock:
            if self._teardown_started:
                raise ReferenceTrackError("Reference Track route was already stopped.")
            if self._stream is not None:
                return
            self._pull = pull
        stream = None
        try:
            self._recheck_routes()
            self._prepare_control()
            # RPC startup can take several seconds. A proof gathered before
            # that wait is stale and cannot authorize opening the song stream.
            self._recheck_routes()
            stream = self._sounddevice.OutputStream(
                device=self._route.sounddevice_index,
                samplerate=REFERENCE_SAMPLE_RATE,
                channels=2,
                dtype="float32",
                blocksize=1_024,
                latency="low",
                callback=self._audio_callback,
            )
            stream.start()
            # Catch a device switch racing the stream open before any decoded
            # song frames are permitted through the callback.
            self._recheck_routes()
        except Exception as exc:  # noqa: BLE001
            if stream is not None:
                try:
                    stream.close()
                except Exception:  # noqa: BLE001
                    pass
            message = (
                str(exc)
                if isinstance(exc, ReferenceTrackError)
                else "WebJam couldn't open the isolated BlackHole song channels."
            )
            self._set_health_error(message)
            raise ReferenceTrackError(message) from None
        with self._lock:
            self._stream = stream
        self._monitor = threading.Thread(
            target=self._monitor_safety,
            name="WebJam reference-track safety monitor",
            daemon=True,
        )
        self._monitor.start()

    def _prepare_control(self) -> None:
        if self._control_ready:
            return
        MacOSBlackHoleReferenceBackend._wait_for_rpc(self._process, self._rpc)
        deadline = time.monotonic() + _RPC_READY_TIMEOUT_S
        while True:
            if self._process.poll() is not None:
                raise ReferenceTrackError(
                    "The Reference Track Jamulus client exited during startup."
                )
            try:
                self._rpc.prove_all_faders_zero()
                self._control_ready = True
                return
            except ReferenceTrackError:
                if time.monotonic() >= deadline:
                    raise ReferenceTrackError(
                        "Reference Track couldn't prove a connected, zero-return "
                        "Jamulus mix."
                    )
                time.sleep(0.15)

    def health_error(self) -> str:
        with self._lock:
            return self._health_error

    def stop(self) -> None:
        with self._lock:
            if self._cleanup_complete:
                return
            self._teardown_started = True
            self._stop_event.set()
            stream = self._stream
            self._stream = None
            self._pull = None
        if stream is not None:
            try:
                stream.stop()
            except Exception:  # noqa: BLE001
                pass
            try:
                stream.close()
            except Exception:  # noqa: BLE001
                pass
        monitor = self._monitor
        if monitor is not None and monitor is not threading.current_thread():
            monitor.join(timeout=2.0)
        self._rpc.close()
        if not MacOSBlackHoleReferenceBackend._terminate_process(self._process):
            message = (
                "Reference Track couldn't confirm that its owned Jamulus "
                "client stopped."
            )
            self._set_health_error(message)
            raise ReferenceTrackError(message)
        if not MacOSBlackHoleReferenceBackend._cleanup_owned_files(
            self._adapter, self._provisioned, self._secret_path
        ):
            message = (
                "Reference Track stopped, but its private profile and control "
                "cleanup could not be confirmed."
            )
            self._set_health_error(message)
            raise ReferenceTrackError(message)
        with self._lock:
            self._cleanup_complete = True
        self._on_stopped(self)

    def _audio_callback(self, outdata, frames, _time_info, status) -> None:
        outdata.fill(0)
        if status:
            self._set_health_error(
                "Reference Track's BlackHole stream reported an audio fault."
            )
            return
        with self._lock:
            pull = self._pull
            unhealthy = bool(self._health_error) or self._teardown_started
            proof_monotonic = self._route_proof_monotonic
            proof_wall = self._route_proof_wall
        now_monotonic = time.monotonic()
        now_wall = time.time()
        proof_stale = (
            proof_monotonic <= 0.0
            or proof_wall <= 0.0
            or max(
                max(0.0, now_monotonic - proof_monotonic),
                max(0.0, now_wall - proof_wall),
            )
            > _ROUTE_PROOF_MAX_AGE_SECONDS
        )
        if proof_stale:
            self._set_health_error(
                "Reference Track stopped because its live primary Jamulus "
                "route proof became stale."
            )
            return
        if unhealthy or pull is None:
            return
        try:
            amount = int(frames)
            if not 1 <= amount <= REFERENCE_MAX_DECODE_FRAMES:
                raise ValueError("unexpected callback size")
            audio = np.asarray(pull(amount), dtype=np.float32)
            if audio.shape != (amount, 2) or not np.isfinite(audio).all():
                raise ValueError("invalid callback audio")
            outdata[:] = np.clip(audio, -1.0, 1.0)
        except Exception:  # noqa: BLE001 - real-time boundary
            outdata.fill(0)
            self._set_health_error(
                "Reference Track stopped because its bounded audio stream failed."
            )

    def _monitor_safety(self) -> None:
        next_route_probe = 0.0
        while not self._stop_event.wait(_FADER_RECHECK_SECONDS):
            if self._process.poll() is not None:
                self._set_health_error(
                    "Reference Track's owned Jamulus client stopped."
                )
                return
            try:
                self._rpc.prove_all_faders_zero()
            except ReferenceTrackError:
                self._set_health_error(
                    "Reference Track stopped because zero return faders could "
                    "no longer be proved."
                )
                return
            now = time.monotonic()
            if now >= next_route_probe:
                next_route_probe = now + _ROUTE_RECHECK_SECONDS
                try:
                    self._recheck_routes()
                except ReferenceTrackError as exc:
                    self._set_health_error(
                        str(exc)
                        or (
                            "Reference Track stopped because its live audio "
                            "route could no longer be proved."
                        )
                    )
                    return

    def _recheck_routes(self) -> None:
        try:
            current = self._prove_routes()
        except ReferenceTrackError:
            raise
        except Exception as exc:  # noqa: BLE001 - native proof boundary
            raise ReferenceTrackError(
                "Reference Track stopped because its live audio route could "
                "no longer be proved."
            ) from exc
        if current != self._route_proof:
            raise ReferenceTrackError(
                "Reference Track stopped because the primary Jamulus live "
                "audio route changed."
            )
        now_monotonic = time.monotonic()
        now_wall = time.time()
        with self._lock:
            if self._teardown_started:
                raise ReferenceTrackError(
                    "Reference Track route was already stopped."
                )
            self._route_proof_monotonic = now_monotonic
            self._route_proof_wall = now_wall

    def _set_health_error(self, message: str) -> None:
        with self._lock:
            if not self._health_error:
                self._health_error = str(message)


def create_reference_audio_backend():
    """Return the honest production backend for the current platform."""

    if sys.platform == "darwin":
        return MacOSBlackHoleReferenceBackend()
    return _UnavailableReferenceBackend(sys.platform)


__all__ = [
    "MacOSBlackHoleReferenceBackend",
    "REFERENCE_PARTICIPANT_NAME",
    "REFERENCE_PROFILE_FILENAME",
    "REFERENCE_SECRET_FILENAME",
    "create_reference_audio_backend",
]
