from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, asdict, field
from pathlib import Path

LOGGER = logging.getLogger(__name__)


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
    file_path = Path(settings_path or (Path.home() / ".webjam_settings.json"))
    data = asdict(base)

    if file_path.exists():
        try:
            loaded = json.loads(file_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data.update(loaded)
        except Exception as exc:
            # Keep defaults on malformed settings.
            LOGGER.warning("Failed to parse settings file '%s'; using defaults. Error: %s", file_path, exc)

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
        if key in {"jamulus_port", "audio_blocksize", "audio_samplerate"}:
            try:
                data[key] = int(raw)
            except ValueError:
                continue
        elif key == "enable_sentry":
            data[key] = raw.strip().lower() in {"1", "true", "yes", "on"}
        elif key == "jamulus_candidates":
            data[key] = [item.strip() for item in raw.split(";") if item.strip()]
        else:
            data[key] = raw

    return AppSettings(**data)

