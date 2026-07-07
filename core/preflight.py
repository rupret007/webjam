"""Pre-jam Ready Check.

A pure, GUI-free readiness probe so a musician learns what's missing *before*
they try to play — Jamulus installed, server configured, a virtual audio cable
present, and a Webex link set.  The UI can render ``run_ready_check(settings)``
however it likes; this module never touches Qt.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List


@dataclass
class CheckItem:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class ReadyCheckReport:
    items: List[CheckItem] = field(default_factory=list)

    @property
    def all_ok(self) -> bool:
        return bool(self.items) and all(i.ok for i in self.items)

    def to_text(self) -> str:
        lines = ["Ready Check"]
        for i in self.items:
            mark = "✓" if i.ok else "✗"
            line = f"  {mark} {i.name}"
            if i.detail:
                line += f" — {i.detail}"
            lines.append(line)
        lines.append("")
        lines.append(
            "All set — you're ready to jam."
            if self.all_ok
            else "Some items need attention before you jam."
        )
        return "\n".join(lines)


def _check_jamulus_executable(settings) -> CheckItem:
    candidates = list(getattr(settings, "jamulus_candidates", []) or [])
    found = next(
        (c for c in candidates if c and Path(str(c)).expanduser().exists()), None
    )
    if found:
        return CheckItem("Jamulus installed", True, found)
    return CheckItem(
        "Jamulus installed", False, "not found — install it free from jamulus.io"
    )


def _check_server(settings) -> CheckItem:
    host = str(getattr(settings, "jamulus_server", "") or "").strip()
    if not host or " " in host:
        return CheckItem(
            "Jamulus server set", False, "enter your band's server host in Settings"
        )
    try:
        port = int(getattr(settings, "jamulus_port", 0))
    except (TypeError, ValueError):
        port = 0
    if not (1 <= port <= 65535):
        return CheckItem("Jamulus server set", False, f"port out of range: {port}")
    return CheckItem("Jamulus server set", True, f"{host}:{port}")


def _check_audio_routing(settings) -> CheckItem:
    # Imported lazily so this stays import-safe even if sounddevice is missing.
    from core.audio_routing import scan_loopback_devices
    status = scan_loopback_devices()  # contracted never to raise
    if getattr(status, "ok", False):
        return CheckItem("Audio routing device", True, status.device_name)
    return CheckItem("Audio routing device", False, status.install_hint)


def _check_webex(settings) -> CheckItem:
    from core.webex_url import normalize_webex_url, webex_url_error
    url = str(getattr(settings, "webex_url", "") or "").strip()
    error = webex_url_error(url)
    if error is None:
        return CheckItem("Webex meeting set", True, normalize_webex_url(url))
    return CheckItem("Webex meeting set", False, error)


def run_ready_check(settings) -> ReadyCheckReport:
    """Run all readiness checks against ``settings`` and return a report."""
    return ReadyCheckReport(items=[
        _check_jamulus_executable(settings),
        _check_server(settings),
        _check_audio_routing(settings),
        _check_webex(settings),
    ])
