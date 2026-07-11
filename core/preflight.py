"""Pre-jam Ready Check.

A pure, GUI-free readiness probe so a musician learns what's missing *before*
they try to play — Jamulus installed, server configured, a virtual audio cable
present, and a Webex link set.  The UI can render ``run_ready_check(settings)``
however it likes; this module never touches Qt.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
from typing import List


@dataclass
class CheckItem:
    name: str
    ok: bool
    detail: str = ""
    required: bool = True


@dataclass
class ReadyCheckReport:
    items: List[CheckItem] = field(default_factory=list)

    @property
    def all_ok(self) -> bool:
        required = [item for item in self.items if item.required]
        return bool(required) and all(item.ok for item in required)

    def to_text(self) -> str:
        lines = ["Ready Check"]
        for i in self.items:
            mark = "✓" if i.ok else ("✗" if i.required else "!")
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
    bridge_required = bool(
        getattr(settings, "webex_audio_bridge_enabled", False)
    )
    if getattr(status, "ok", False):
        return CheckItem("Webex audio bridge", True, status.device_name)
    if bridge_required:
        return CheckItem("Webex audio bridge", False, status.install_hint)
    return CheckItem(
        "Webex audio bridge",
        False,
        "not enabled on this Mac — Jamulus audio is unaffected",
        required=False,
    )


def _check_selected_input(settings) -> CheckItem:
    from core.audio_routing import list_input_devices

    selected = int(getattr(settings, "audio_input_device_index", -1) or -1)
    if selected < 0:
        return CheckItem("Meter input", True, "system default")
    device = next(
        (item for item in list_input_devices() if item["index"] == selected), None
    )
    if device is None:
        return CheckItem(
            "Meter input", False, "saved input device is no longer connected"
        )
    requested_rate = int(getattr(settings, "audio_samplerate", 48000) or 48000)
    try:
        import sounddevice as sd  # type: ignore

        sd.check_input_settings(
            device=selected,
            channels=1,
            samplerate=requested_rate,
        )
    except Exception as exc:  # noqa: BLE001
        return CheckItem(
            "Meter input",
            False,
            f"{device['name']} can't open at {requested_rate} Hz — pick a "
            f"different input or sample rate in Settings. (Details: {exc})",
        )
    rate = int(device.get("default_samplerate", 0) or 0)
    detail = (
        f"{device['name']} · {requested_rate} Hz supported"
        + (f" · default {rate} Hz" if rate else "")
    )
    return CheckItem("Meter input", True, detail)


def _check_recorder(settings) -> CheckItem:
    secret_path = str(getattr(settings, "server_rpc_secret_file", "") or "").strip()
    if not secret_path:
        return CheckItem(
            "Host recorder",
            True,
            "not configured on this musician's Mac",
            required=False,
        )
    try:
        from core.jamulus_server_rpc import JamulusServerRpc, read_secret_file

        secret = read_secret_file(secret_path)
        rpc = JamulusServerRpc(
            port=int(getattr(settings, "server_rpc_port", 22240)), secret=secret
        )
        rpc.CONNECT_TIMEOUT_S = 0.75
        rpc.CALL_TIMEOUT_S = 1.25
        with rpc:
            status = rpc.get_recorder_status()
        if not status.get("initialised", False):
            return CheckItem("Host recorder", False, "server recorder is not initialised")
    except Exception as exc:  # noqa: BLE001
        return CheckItem(
            "Host recorder",
            False,
            "couldn't reach the band server's recorder — check the SSH "
            "tunnel, RPC port, and secret file, then run Ready Check again. "
            f"(Details: {exc})",
        )

    takes = Path(str(getattr(settings, "takes_directory", "") or "")).expanduser()
    if not takes.is_dir() or not os.access(takes, os.W_OK):
        return CheckItem("Host recorder", False, "local Takes folder is not writable")
    return CheckItem(
        "Host recorder", True, f"ready · {status.get('recordingDirectory', takes)}"
    )


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
        _check_selected_input(settings),
        _check_webex(settings),
        _check_recorder(settings),
    ])
