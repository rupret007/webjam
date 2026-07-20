from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import dataclass, asdict, field
from pathlib import Path

_logger = logging.getLogger(__name__)

WEBEX_AUDIO_MODES = ("talkback", "video_only", "audience_bridge")


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _coerce_settings_data(data: dict) -> None:
    """Coerce config values to expected types; fall back to defaults on invalid data."""
    defaults = asdict(AppSettings())
    # Integer fields
    for key in ("jamulus_port", "jamulus_rpc_port", "audio_blocksize", "audio_samplerate",
                "audio_input_device_index", "companion_api_port", "server_rpc_port"):
        if key in data:
            try:
                data[key] = int(data[key]) if data[key] is not None else defaults[key]
            except (TypeError, ValueError):
                data[key] = defaults[key]
                _logger.debug("Invalid %s in config; using default", key)
    # Boolean fields
    for key in (
        "enable_sentry",
        "companion_api_enabled",
        "local_capture_enabled",
        "local_capture_choice_made",
        "host_server_enabled",
    ):
        if key in data:
            data[key] = _as_bool(data[key])
    if "webex_audio_mode" in data:
        mode = str(data["webex_audio_mode"] or "").strip().lower()
        if mode not in WEBEX_AUDIO_MODES:
            _logger.debug("Invalid webex_audio_mode in config; using talkback")
            mode = defaults["webex_audio_mode"]
        data["webex_audio_mode"] = mode
    # List of strings
    if "jamulus_candidates" in data:
        v = data["jamulus_candidates"]
        if isinstance(v, list) and all(isinstance(x, str) for x in v):
            candidates = [s.strip() for s in v if s and str(s).strip()]
        else:
            candidates = []
        data["jamulus_candidates"] = candidates if candidates else defaults["jamulus_candidates"]
    # String fields: ensure str
    for key in ("jamulus_server", "webex_url", "config_file", "mix_file", "webex_config_file",
                "server_rpc_secret_file", "takes_directory",
                "take_playback_output_device", "jamulus_audio_input_uid",
                "jamulus_audio_output_uid",
                "audio_latency", "sentry_dsn", "log_level", "log_file",
                "musician_name"):
        if key in data and data[key] is not None and not isinstance(data[key], str):
            data[key] = str(data[key])


@dataclass
class AppSettings:
    # jamulus_server / webex_url intentionally default to EMPTY: the old
    # defaults (a private LAN IP and a sandbox meeting link) were dead for
    # anyone but the original dev box, and silently "working" against them
    # was worse than being asked.  The first-run wizard requires both.
    jamulus_server: str = ""
    jamulus_port: int = 22124
    jamulus_rpc_port: int = 22222   # JSON-RPC server port (--jsonrpcport flag)
    webex_url: str = ""
    jamulus_candidates: list[str] = field(default_factory=lambda: [
        # macOS (bundle binary)
        "/Applications/Jamulus.app/Contents/MacOS/Jamulus",
        # Windows
        r"C:\Program Files\Jamulus\Jamulus.exe",
        r"C:\Program Files (x86)\Jamulus\Jamulus.exe",
        # Linux / Homebrew / manual install
        "/usr/bin/jamulus",
        "/usr/local/bin/jamulus",
        "/usr/bin/Jamulus",
        "/usr/local/bin/Jamulus",
        "/opt/homebrew/bin/Jamulus",
    ])
    config_file: str = str(Path.home() / ".webjam_config.json")
    mix_file: str = str(Path.home() / ".webjam_mix.json")
    webex_config_file: str = str(Path.home() / ".webjam_webex_config.json")
    audio_blocksize: int = 0
    audio_samplerate: int = 48000
    audio_latency: str = "low"
    audio_input_device_index: int = -1  # -1 means "system default / auto-detect"
    # macOS live-music route.  These are CoreAudio UIDs, never PortAudio
    # indexes or display names; blank means resolve the Mac defaults when the
    # next session starts.  They deliberately stay separate from the meter's
    # best-effort PortAudio index above.
    jamulus_audio_input_uid: str = ""
    jamulus_audio_output_uid: str = ""
    # Webex carries speech only by default; Jamulus remains the music path.
    webex_audio_mode: str = "talkback"
    # Supplemental isolated input capture is independent of the Webex role.
    local_capture_enabled: bool = False
    # The first host Record click asks whether this Mac should retain isolated
    # interface inputs.  This records only that the musician made a choice;
    # it never selects or identifies a live-audio device.
    local_capture_choice_made: bool = False
    enable_sentry: bool = False
    sentry_dsn: str = ""
    log_level: str = "INFO"
    log_file: str = str(Path.home() / ".webjam.log")
    musician_name: str = "WebJam Musician"
    # Companion API — optional localhost HTTP bridge for DAWs/editors/scripts.
    # Opt-in: starts on launch only when enabled and fastapi/uvicorn exist.
    companion_api_enabled: bool = False
    companion_api_port: int = 8765
    # Band-server RPC (the Record button). The server's JSON-RPC stays on
    # loopback. A remote host uses an SSH tunnel terminating at
    # 127.0.0.1:server_rpc_port; in hosted mode the server is on this Mac.
    # server_rpc_secret_file points at the matching local secret.
    server_rpc_port: int = 22240
    server_rpc_secret_file: str = ""
    # This Mac hosts the band's Jamulus server: WebJam supervises the
    # official JamulusServer.app (macOS) with recording + loopback RPC,
    # replacing the manual server/start_macos_pilot.sh Terminal step.
    host_server_enabled: bool = False
    # Take Deck: where recorded takes live locally (fetched from the band
    # server, e.g. scp'd into this folder). Empty = the Deck shows a hint.
    takes_directory: str = ""
    take_playback_output_device: str = ""

    @property
    def webex_audio_bridge_enabled(self) -> bool:
        """Read-only compatibility view for one release cycle."""
        return self.webex_audio_mode == "audience_bridge"


def load_settings(settings_path: str | None = None) -> AppSettings:
    base = AppSettings()
    file_path = Path(settings_path or base.config_file)
    data = asdict(base)

    loaded_data: dict = {}
    if file_path.exists():
        try:
            loaded = json.loads(file_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                loaded_data = loaded
                data.update(loaded)
        except Exception as exc:
            _logger.warning("Failed to parse settings file %s: %s - using defaults", file_path, exc)

    # Migrate the one-release legacy bridge flag before coercion.  Explicit
    # new fields always win over their legacy-derived values.
    if "webex_audio_mode" not in loaded_data and "webex_audio_bridge_enabled" in loaded_data:
        legacy_bridge = _as_bool(loaded_data["webex_audio_bridge_enabled"])
        data["webex_audio_mode"] = "audience_bridge" if legacy_bridge else "video_only"
        if "local_capture_enabled" not in loaded_data:
            data["local_capture_enabled"] = legacy_bridge
    if "musician_name" not in loaded_data and "webex_display_name" in loaded_data:
        legacy_name = str(loaded_data["webex_display_name"] or "").strip()
        if legacy_name:
            data["musician_name"] = legacy_name

    # Coerce types to prevent TypeError when constructing AppSettings
    _coerce_settings_data(data)

    env_map = {
        "WEBJAM_JAMULUS_SERVER": "jamulus_server",
        "WEBJAM_JAMULUS_PORT": "jamulus_port",
        "WEBJAM_JAMULUS_RPC_PORT": "jamulus_rpc_port",
        "WEBJAM_SERVER_RPC_PORT": "server_rpc_port",
        "WEBJAM_SERVER_RPC_SECRET_FILE": "server_rpc_secret_file",
        "WEBJAM_TAKES_DIRECTORY": "takes_directory",
        "WEBJAM_TAKE_PLAYBACK_OUTPUT_DEVICE": "take_playback_output_device",
        "WEBJAM_LOCAL_CAPTURE_ENABLED": "local_capture_enabled",
        "WEBJAM_HOST_SERVER_ENABLED": "host_server_enabled",
        "WEBJAM_WEBEX_URL": "webex_url",
        "WEBJAM_MUSICIAN_NAME": "musician_name",
        "WEBJAM_JAMULUS_CANDIDATES": "jamulus_candidates",
        "WEBJAM_AUDIO_BLOCKSIZE": "audio_blocksize",
        "WEBJAM_AUDIO_SAMPLERATE": "audio_samplerate",
        "WEBJAM_AUDIO_LATENCY": "audio_latency",
        "WEBJAM_AUDIO_INPUT_DEVICE_INDEX": "audio_input_device_index",
        "WEBJAM_ENABLE_SENTRY": "enable_sentry",
        "WEBJAM_SENTRY_DSN": "sentry_dsn",
        "WEBJAM_LOG_LEVEL": "log_level",
        "WEBJAM_LOG_FILE": "log_file",
        "WEBJAM_COMPANION_API": "companion_api_enabled",
        "WEBJAM_COMPANION_API_PORT": "companion_api_port",
    }
    for env_name, key in env_map.items():
        raw = os.getenv(env_name)
        if raw is None:
            continue
        if key in {"jamulus_port", "jamulus_rpc_port"}:
            try:
                parsed = int(raw)
            except ValueError:
                continue
            if 1 <= parsed <= 65535:
                data[key] = parsed
            continue
        if key in {"audio_blocksize", "audio_samplerate", "audio_input_device_index",
                   "companion_api_port", "server_rpc_port"}:
            try:
                parsed = int(raw)
            except ValueError:
                continue
            if key == "audio_blocksize" and parsed < 0:
                continue
            if key == "audio_samplerate" and parsed <= 0:
                continue
            if key in {"companion_api_port", "server_rpc_port"} and not (1 <= parsed <= 65535):
                continue
            data[key] = parsed
        elif key in {
            "enable_sentry",
            "companion_api_enabled",
            "local_capture_enabled",
            "local_capture_choice_made",
            "host_server_enabled",
        }:
            data[key] = _as_bool(raw)
        elif key == "jamulus_candidates":
            data[key] = [item.strip() for item in raw.split(";") if item.strip()]
        else:
            data[key] = raw

    # The new mode environment variable has precedence over the legacy bridge
    # variable. An invalid new value is coerced to the safe talkback default;
    # it must never fall through to a stale legacy bridge setting.
    new_mode_env = os.getenv("WEBJAM_WEBEX_AUDIO_MODE")
    legacy_bridge_env = os.getenv("WEBJAM_WEBEX_AUDIO_BRIDGE_ENABLED")
    local_capture_env = os.getenv("WEBJAM_LOCAL_CAPTURE_ENABLED")
    normalized_env_mode = str(new_mode_env or "").strip().lower()
    if new_mode_env is not None:
        # Presence of the new variable always wins.  Coercion below turns an
        # invalid value into the safe talkback default instead of allowing a
        # stale legacy bridge flag to select an unintended audio topology.
        data["webex_audio_mode"] = normalized_env_mode
    elif legacy_bridge_env is not None:
        legacy_bridge = _as_bool(legacy_bridge_env)
        data["webex_audio_mode"] = "audience_bridge" if legacy_bridge else "video_only"
        if local_capture_env is None:
            data["local_capture_enabled"] = legacy_bridge

    _coerce_settings_data(data)
    valid_keys = {f.name for f in AppSettings.__dataclass_fields__.values()}
    data = {k: v for k, v in data.items() if k in valid_keys}
    settings = AppSettings(**data)

    if not (1 <= settings.jamulus_port <= 65535):
        _logger.warning("jamulus_port %d out of range; resetting to 22124", settings.jamulus_port)
        settings = AppSettings(**{**asdict(settings), "jamulus_port": 22124})
    if not (1 <= settings.jamulus_rpc_port <= 65535):
        _logger.warning("jamulus_rpc_port %d out of range; resetting to 22222",
                        settings.jamulus_rpc_port)
        settings = AppSettings(**{**asdict(settings), "jamulus_rpc_port": 22222})
    if settings.audio_blocksize < 0:
        _logger.warning("audio_blocksize %d negative; resetting to 0", settings.audio_blocksize)
        settings = AppSettings(**{**asdict(settings), "audio_blocksize": 0})
    if settings.audio_samplerate <= 0:
        _logger.warning("audio_samplerate %d invalid; resetting to 48000", settings.audio_samplerate)
        settings = AppSettings(**{**asdict(settings), "audio_samplerate": 48000})
    if not (1 <= settings.server_rpc_port <= 65535):
        _logger.warning("server_rpc_port %d out of range; resetting to 22240",
                        settings.server_rpc_port)
        settings = AppSettings(**{**asdict(settings), "server_rpc_port": 22240})
    if not (1 <= settings.companion_api_port <= 65535):
        _logger.warning("companion_api_port %d out of range; resetting to 8765",
                        settings.companion_api_port)
        settings = AppSettings(**{**asdict(settings), "companion_api_port": 8765})

    # The musician-facing product has one default conversation topology:
    # Jamulus carries the band and native Webex is optional talkback.  Preserve
    # the independent local-capture preference—Studio owns that recording
    # choice and silently disabling it would lose the host's isolated stems.
    simple_defaults = {}
    if "WEBJAM_WEBEX_AUDIO_MODE" not in os.environ:
        simple_defaults["webex_audio_mode"] = "talkback"
    if (
        "local_capture_enabled" not in loaded_data
        and "WEBJAM_LOCAL_CAPTURE_ENABLED" not in os.environ
    ):
        # Do not let the retired bridge flag silently arm a new local audio
        # recording. An explicit modern preference or environment override is
        # preserved; absent that, capture stays safely off.
        simple_defaults["local_capture_enabled"] = False
    if simple_defaults:
        settings = AppSettings(**{**asdict(settings), **simple_defaults})

    if settings.host_server_enabled:
        # Hosting is an all-in-one local topology. Never let a stale public or
        # LAN address make the host start a server here but connect its client
        # somewhere else.
        derived = {
            "jamulus_server": "127.0.0.1",
            "server_rpc_secret_file": str(hosted_server_secret_path()),
            "takes_directory": str(hosted_server_recordings_dir()),
        }
        settings = AppSettings(**{**asdict(settings), **derived})

    return settings


def jamulus_server_container_documents() -> Path:
    """The sandboxed JamulusServer.app writable Documents directory.

    The official server bundle is sandboxed: recordings and its RPC secret
    must live inside its container (Data/Music is only a symlink and is not
    writable). Mirrors server/start_macos_pilot.sh.
    """
    return (
        Path.home() / "Library" / "Containers"
        / "app.jamulussoftware.JamulusServer" / "Data" / "Documents"
    )


def jamulus_client_container_documents() -> Path:
    """Writable Documents directory for the sandboxed Jamulus client.

    The official macOS Jamulus.app has App Sandbox enabled.  A secret in the
    user's bare home directory is visible to WebJam but not to Jamulus, which
    makes Jamulus exit immediately when launched with --jsonrpcsecretfile.
    """
    return (
        Path.home() / "Library" / "Containers"
        / "app.jamulussoftware.Jamulus" / "Data" / "Documents"
    )


def webjam_application_support_dir() -> Path:
    """Private runtime data owned by the packaged WebJam application."""
    return Path.home() / "Library" / "Application Support" / "WebJam"


def jamulus_client_rpc_secret_path() -> Path:
    """Cross-platform location shared by Jamulus and JamulusRpcClient."""
    if sys.platform == "darwin":
        if getattr(sys, "frozen", False):
            return (
                webjam_application_support_dir()
                / "JamulusClient" / "webjam_client_rpc.secret"
            )
        return jamulus_client_container_documents() / "webjam_client_rpc.secret"
    return Path.home() / ".webjam_jsonrpc_secret"


def hosted_server_secret_path() -> Path:
    if sys.platform == "darwin" and getattr(sys, "frozen", False):
        return (
            webjam_application_support_dir()
            / "JamulusServer" / "webjam_server_rpc.secret"
        )
    return jamulus_server_container_documents() / "webjam_server_rpc.secret"


def hosted_server_recordings_dir() -> Path:
    if sys.platform == "darwin" and getattr(sys, "frozen", False):
        return webjam_application_support_dir() / "JamulusServer" / "Recordings"
    return jamulus_server_container_documents() / "WebJam Recordings"


def save_settings(settings: AppSettings) -> None:
    """Atomically persist all current settings with private-file permissions."""
    from core.file_io import atomic_write_text

    path = Path(settings.config_file).expanduser()
    atomic_write_text(path, json.dumps(asdict(settings), indent=2), mode=0o600)
