from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, asdict, field
from pathlib import Path

_logger = logging.getLogger(__name__)


@dataclass
class AppSettings:
    jamulus_server: str = "172.24.194.9"
    jamulus_port: int = 22124
    webex_url: str = "https://webjam-sbx.webex.com/meet/webjam01"
    jamulus_candidates: list[str] = field(default_factory=lambda: [
        r"C:\Program Files\Jamulus\Jamulus.exe",
        r"C:\Program Files (x86)\Jamulus\Jamulus.exe",
    ])
    config_file: str = str(Path.home() / ".webjam_config.json")
    webex_config_file: str = str(Path.home() / ".webjam_webex_config.json")
    audio_blocksize: int = 0
    audio_samplerate: int = 48000
    audio_latency: str = "low"
    enable_sentry: bool = False
    sentry_dsn: str = ""
    log_level: str = "INFO"
    log_file: str = str(Path.home() / ".webjam.log")


def load_settings(settings_path: str | None = None) -> AppSettings:
    base = AppSettings()
    file_path = Path(settings_path or base.config_file)
    data = asdict(base)

    if file_path.exists():
        try:
            loaded = json.loads(file_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data.update(loaded)
        except Exception as exc:
            _logger.warning("Failed to parse settings file %s: %s – using defaults", file_path, exc)

    env_map = {
        "WEBJAM_JAMULUS_SERVER": "jamulus_server",
        "WEBJAM_JAMULUS_PORT": "jamulus_port",
        "WEBJAM_WEBEX_URL": "webex_url",
        "WEBJAM_JAMULUS_CANDIDATES": "jamulus_candidates",
        "WEBJAM_AUDIO_BLOCKSIZE": "audio_blocksize",
        "WEBJAM_AUDIO_SAMPLERATE": "audio_samplerate",
        "WEBJAM_AUDIO_LATENCY": "audio_latency",
        "WEBJAM_ENABLE_SENTRY": "enable_sentry",
        "WEBJAM_SENTRY_DSN": "sentry_dsn",
        "WEBJAM_LOG_LEVEL": "log_level",
        "WEBJAM_LOG_FILE": "log_file",
    }
    for env_name, key in env_map.items():
        raw = os.getenv(env_name)
        if raw is None:
            continue
        if key == "jamulus_port":
            try:
                parsed = int(raw)
            except ValueError:
                continue
            if 1 <= parsed <= 65535:
                data[key] = parsed
            continue
        if key in {"audio_blocksize", "audio_samplerate"}:
            try:
                parsed = int(raw)
            except ValueError:
                continue
            if key == "audio_blocksize" and parsed < 0:
                continue
            if key == "audio_samplerate" and parsed <= 0:
                continue
            data[key] = parsed
        elif key == "enable_sentry":
            data[key] = raw.strip().lower() in {"1", "true", "yes", "on"}
        elif key == "jamulus_candidates":
            data[key] = [item.strip() for item in raw.split(";") if item.strip()]
        else:
            data[key] = raw

    settings = AppSettings(**data)

    if not (1 <= settings.jamulus_port <= 65535):
        _logger.warning("jamulus_port %d out of range; resetting to 22124", settings.jamulus_port)
        settings = AppSettings(**{**asdict(settings), "jamulus_port": 22124})
    if settings.audio_blocksize < 0:
        _logger.warning("audio_blocksize %d negative; resetting to 0", settings.audio_blocksize)
        settings = AppSettings(**{**asdict(settings), "audio_blocksize": 0})
    if settings.audio_samplerate <= 0:
        _logger.warning("audio_samplerate %d invalid; resetting to 48000", settings.audio_samplerate)
        settings = AppSettings(**{**asdict(settings), "audio_samplerate": 48000})

    return settings

