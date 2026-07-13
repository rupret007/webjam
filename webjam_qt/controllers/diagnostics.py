"""
DiagnosticsExporter — build a Markdown summary of the running app for bug reports.

The summary is intended to be copied to the clipboard via Ctrl+Shift+D and
pasted directly into a GitHub issue.  It collects version info, service
state, log paths + a tail of the WebJam log, and a sanitised view of the
user's settings (the Webex guest-issuer secret is redacted).
"""

from __future__ import annotations

import logging
import platform
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from core.redaction import (
    REDACTED_FIELDS as _REDACTED_FIELDS,  # noqa: F401 - compatibility re-export
    REDACTED_NAME_HINTS as _REDACTED_NAME_HINTS,  # noqa: F401 - compatibility re-export
    redact_mapping,
    redact_text,
)

LOGGER = logging.getLogger("webjam.qt.diagnostics")

_LOG_TAIL_LINES = 30


class DiagnosticsExporter:
    """Builds a Markdown bug-report summary from live app state."""

    def __init__(
        self,
        settings: Any,
        bridge: Any,
        jamulus_controller: Any,
        window_version: str,
    ) -> None:
        self.settings = settings
        self.bridge = bridge
        self.jamulus = jamulus_controller
        self.window_version = window_version

    def build_summary(self) -> str:
        lines: list[str] = []
        lines.append("# WebJam Diagnostics")
        lines.append("")
        lines.append("## Versions")
        lines.append(f"- WebJam: `{self.window_version}`")
        lines.append(f"- Python: `{sys.version.split()[0]}` ({sys.executable})")
        lines.append(f"- Platform: `{platform.platform()}`")
        lines.append("")

        lines.append("## Service state")
        lines.append(f"- Jamulus state: `{getattr(self.bridge, 'jamulus_state', 'unknown')}`")
        lines.append(f"- Webex state: `{getattr(self.bridge, 'webex_state', 'unknown')}`")
        server = f"{getattr(self.settings, 'jamulus_server', '?')}:{getattr(self.settings, 'jamulus_port', '?')}"
        lines.append(f"- Jamulus server: `{server}`")
        try:
            jamulus_path = self.bridge.find_jamulus()
        except Exception:  # noqa: BLE001
            jamulus_path = None
        lines.append(f"- Jamulus binary: `{jamulus_path or 'not found'}`")
        lines.append(f"- Audio routing: `{self._routing_status()}`")
        try:
            n_participants = len(self.jamulus.get_participants())
        except Exception:  # noqa: BLE001
            n_participants = "unknown"
        lines.append(f"- Participants: `{n_participants}`")
        rpc = getattr(self.jamulus, "rpc_client", None)
        rpc_avail = getattr(rpc, "available", False) if rpc is not None else False
        lines.append(f"- RPC available: `{rpc_avail}`")
        try:
            age = rpc.last_activity_age() if rpc is not None else None
        except Exception:  # noqa: BLE001
            age = None
        lines.append(f"- RPC last activity age (s): `{age}`")
        lines.append("")

        log_path = Path.home() / ".webjam.log"
        jamulus_log = Path.home() / ".webjam_jamulus.log"
        server_log = Path.home() / "Library" / "Logs" / "WebJam" / "jamulus-server.log"
        lines.append("## Log files")
        lines.append(f"- `{log_path}` (WebJam)")
        lines.append(f"- `{jamulus_log}` (Jamulus client stdout/stderr)")
        lines.append(f"- `{server_log}` (Jamulus server stdout/stderr)")
        lines.append("")

        for label, path in (
            ("~/.webjam.log", log_path),
            ("~/.webjam_jamulus.log", jamulus_log),
            ("~/Library/Logs/WebJam/jamulus-server.log", server_log),
        ):
            lines.append(f"## Last {_LOG_TAIL_LINES} lines of `{label}`")
            lines.append("```")
            lines.extend(self._tail_log(path))
            lines.append("```")
            lines.append("")

        lines.append("## Settings (sanitised)")
        lines.append("```json")
        lines.append(self._sanitised_settings_json())
        lines.append("```")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _routing_status(self) -> str:
        try:
            diag = self.jamulus.audio_engine.diagnostics()
            return f"{diag.backend} / {diag.latency_mode} (active={diag.active})"
        except Exception:  # noqa: BLE001
            return "unknown"

    def _tail_log(self, path: Path) -> list[str]:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ["(log file unavailable)"]
        except Exception:  # noqa: BLE001
            LOGGER.debug("Unexpected log read failure", exc_info=True)
            return ["(log file unavailable)"]
        all_lines = text.splitlines()
        tail = all_lines[-_LOG_TAIL_LINES:] if all_lines else ["(empty log)"]
        return [redact_text(line) for line in tail]

    def _sanitised_settings_json(self) -> str:
        import json
        try:
            data = asdict(self.settings)
        except TypeError:
            data = {k: getattr(self.settings, k) for k in dir(self.settings)
                    if not k.startswith("_") and not callable(getattr(self.settings, k))}
        data = redact_mapping(data)
        try:
            return json.dumps(data, indent=2, default=str, sort_keys=True)
        except Exception:  # noqa: BLE001
            return "(settings unavailable)"
