"""Pre-jam Ready Check.

A pure, GUI-free readiness probe so a musician learns what's missing *before*
they try to play. Virtual-loopback audio is checked only for the advanced
audience-bridge role; native Webex choices are explicit manual confirmations.
The UI can render ``run_ready_check(settings)`` however it likes; this module
never touches Qt.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import sys
from typing import List


@dataclass
class CheckItem:
    name: str
    ok: bool
    detail: str = ""
    required: bool = True
    manual_verification: bool = False


@dataclass
class ReadyCheckReport:
    items: List[CheckItem] = field(default_factory=list)

    @property
    def all_ok(self) -> bool:
        required = [item for item in self.items if item.required]
        return bool(required) and all(item.ok for item in required)

    @property
    def automated_ok(self) -> bool:
        required = [
            item for item in self.items
            if item.required and not item.manual_verification
        ]
        return bool(required) and all(item.ok for item in required)

    @property
    def pending_manual_count(self) -> int:
        return sum(
            1 for item in self.items
            if item.manual_verification and item.required and not item.ok
        )

    def to_text(self) -> str:
        lines = ["Ready Check"]
        for i in self.items:
            mark = (
                "✓"
                if i.ok
                else "□"
                if i.manual_verification
                else "✗"
                if i.required
                else "!"
            )
            line = f"  {mark} {i.name}"
            if i.detail:
                line += f" — {i.detail}"
            lines.append(line)
        lines.append("")
        if self.automated_ok and self.pending_manual_count:
            lines.append(
                "Automated checks passed; confirm "
                f"{self.pending_manual_count} Webex settings."
            )
        else:
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
        return CheckItem("Webex audio bridge", True, status.device_name)
    return CheckItem("Webex audio bridge", False, status.install_hint)


def _check_selected_input(settings) -> CheckItem:
    from core.audio_routing import list_input_devices

    selected = int(getattr(settings, "audio_input_device_index", -1) or -1)
    local_capture = bool(getattr(settings, "local_capture_enabled", False))
    item_name = "Meter and local recording input"
    if local_capture and int(getattr(settings, "audio_samplerate", 48000) or 48000) != 48000:
        return CheckItem(
            item_name, False, "isolated local recording requires 48,000 Hz"
        )
    if selected < 0 and not local_capture:
        return CheckItem(item_name, True, "system default")
    device = None
    if selected >= 0:
        device = next(
            (item for item in list_input_devices() if item["index"] == selected), None
        )
        if device is None:
            return CheckItem(
                item_name, False, "saved input device is no longer connected"
            )
    requested_rate = int(getattr(settings, "audio_samplerate", 48000) or 48000)
    try:
        import sounddevice as sd  # type: ignore

        sd.check_input_settings(
            device=None if selected < 0 else selected,
            channels=2 if local_capture else 1,
            samplerate=requested_rate,
        )
    except Exception as exc:  # noqa: BLE001
        device_name = "system default" if device is None else device["name"]
        return CheckItem(
            item_name,
            False,
            f"{device_name} can't open at {requested_rate} Hz — pick a "
            f"different input or sample rate in Settings. (Details: {exc})",
        )
    device_name = "system default" if device is None else device["name"]
    rate = int((device or {}).get("default_samplerate", 0) or 0)
    detail = (
        f"{device_name} · {requested_rate} Hz supported"
        + (f" · default {rate} Hz" if rate else "")
    )
    return CheckItem(item_name, True, detail)


def _check_local_capture(settings) -> CheckItem | None:
    if not bool(getattr(settings, "local_capture_enabled", False)):
        return None
    takes = Path(str(getattr(settings, "takes_directory", "") or "")).expanduser()
    if not str(getattr(settings, "takes_directory", "") or "").strip():
        return CheckItem(
            "Local stem recording", False, "choose a Takes folder in Settings"
        )
    if not takes.is_dir() or not os.access(takes, os.W_OK):
        return CheckItem(
            "Local stem recording", False, "configured Takes folder is not writable"
        )
    try:
        import soundfile  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        return CheckItem(
            "Local stem recording", False, f"audio file support is unavailable: {exc}"
        )
    return CheckItem("Local stem recording", True, f"48 kHz stems · {takes}")


def _manual_item(name: str, detail: str) -> CheckItem:
    return CheckItem(
        name, False, detail, required=True, manual_verification=True
    )


def _webex_manual_checks(settings) -> list[CheckItem]:
    mode = str(getattr(settings, "webex_audio_mode", "talkback") or "talkback")
    if mode == "video_only":
        return [
            _manual_item(
                "Webex joined without computer audio",
                "Confirm Webex shows video only and cannot send or play meeting audio.",
            )
        ]
    if mode == "audience_bridge":
        return [
            _manual_item(
                "Webex virtual microphone",
                "Select BlackHole or VB-CABLE as the microphone.",
            ),
            _manual_item(
                "Webex physical speaker",
                "Select the wired audio interface, never the virtual bridge.",
            ),
            _manual_item(
                "Real microphone excluded",
                "Confirm the Mac or interface microphone is not sent to Webex.",
            ),
            _manual_item(
                "Webex Music Mode", "Enable Music Mode for the program feed."
            ),
            _manual_item(
                "Audience echo test",
                "Verify from a second device with headphones that the mix is "
                "clear and echo-free.",
            ),
        ]
    checks = [
        _manual_item(
            "Webex talkback microphone",
            "Select the intended dedicated speech microphone.",
        ),
        _manual_item(
            "Webex wired speaker",
            "Select the wired audio interface used by your headphones.",
        ),
        _manual_item(
            "Webex muted for Play",
            "Confirm the Webex microphone is muted before music starts.",
        ),
        _manual_item(
            "Webex push-to-talk",
            "Hold Spacebar and confirm temporary unmute works, then release it.",
        ),
        _manual_item(
            "Optimize for My Voice",
            "Select Optimize for My Voice in Webex Smart Audio.",
        ),
    ]
    if sys.platform == "darwin":
        checks.insert(
            -1,
            _manual_item(
                "Standard microphone mode",
                "In macOS Control Center, select Standard Mic Mode for Webex.",
            ),
        )
    return checks


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
        if bool(getattr(settings, "host_server_enabled", False)):
            detail = (
                "couldn't reach the band server's recorder — this Mac hosts "
                "the server, and WebJam starts it with Start Audio. Press "
                "Start Audio, then run Ready Check again. "
                f"(Details: {exc})"
            )
        else:
            detail = (
                "couldn't reach the band server's recorder — is the server "
                "running? Same Mac: start it with server/start_macos_pilot.sh. "
                "Remote Linux: check the SSH tunnel. Then verify the RPC port "
                "and secret file and run Ready Check again. "
                f"(Details: {exc})"
            )
        return CheckItem("Host recorder", False, detail)

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


def _check_hosted_server(settings) -> "CheckItem | None":
    """When this Mac hosts, verify the supported dedicated server binary."""
    if getattr(settings, "host_server_enabled", False) is not True:
        return None
    if sys.platform != "darwin":
        return CheckItem(
            "Band server (hosted)",
            False,
            "in-app band-server hosting is currently supported only on macOS",
        )
    from services.bridge_service import _bundled_jamulus_server_candidate

    installed = Path(
        "/Applications/JamulusServer.app/Contents/MacOS/JamulusServer"
    )
    bundled = _bundled_jamulus_server_candidate()
    installed_available = installed.is_file()
    binary = str(installed) if installed_available else bundled
    source = "installed" if installed_available else "bundled"
    if not binary:
        return CheckItem(
            "Band server (hosted)",
            False,
            "JamulusServer.app is unavailable — reinstall the downloadable "
            "WebJam build, install the official 3.12.2 server for a source "
            "build, or turn off hosting in Settings.",
        )
    try:
        import subprocess

        probe = subprocess.run(
            [binary, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        version_text = (probe.stdout or "") + (probe.stderr or "")
    except Exception as exc:  # noqa: BLE001
        return CheckItem(
            "Band server (hosted)",
            False,
            f"couldn't verify JamulusServer.app version ({exc})",
        )
    if getattr(probe, "returncode", 0) != 0 or "Version 3.12.2" not in version_text:
        return CheckItem(
            "Band server (hosted)",
            False,
            "the closed pilot requires JamulusServer.app 3.12.2 exactly",
        )
    return CheckItem(
        "Band server (hosted)",
        True,
        f"JamulusServer.app 3.12.2 verified ({source}) — WebJam starts it "
        "with Start Audio",
    )


def run_ready_check(settings) -> ReadyCheckReport:
    """Run all readiness checks against ``settings`` and return a report."""
    items = [
        _check_jamulus_executable(settings),
        _check_server(settings),
        _check_selected_input(settings),
        _check_webex(settings),
        _check_recorder(settings),
    ]
    hosted = _check_hosted_server(settings)
    if hosted is not None:
        items.insert(1, hosted)
    if str(getattr(settings, "webex_audio_mode", "talkback")) == "audience_bridge":
        items.insert(2, _check_audio_routing(settings))
    local_capture = _check_local_capture(settings)
    if local_capture is not None:
        items.insert(-2, local_capture)
    items.extend(_webex_manual_checks(settings))
    return ReadyCheckReport(items=items)
