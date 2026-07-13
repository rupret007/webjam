from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
import socket


def test_reference_service_runs_as_an_independent_local_process() -> None:
    tcp_control = socket.socket()
    tcp_http = socket.socket()
    udp_relay = socket.socket(type=socket.SOCK_DGRAM)
    for listener in (tcp_control, tcp_http, udp_relay):
        listener.bind(("127.0.0.1", 0))
    control_port = tcp_control.getsockname()[1]
    http_port = tcp_http.getsockname()[1]
    relay_port = udp_relay.getsockname()[1]
    for listener in (tcp_control, tcp_http, udp_relay):
        listener.close()

    root = Path(__file__).resolve().parents[1]
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "webjam_reference",
            "--control-port",
            str(control_port),
            "--relay-port",
            str(relay_port),
            "--http-port",
            str(http_port),
        ],
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        deadline = time.monotonic() + 5
        health: dict[str, object] | None = None
        while time.monotonic() < deadline:
            if process.poll() is not None:
                break
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{http_port}/healthz", timeout=0.2
                ) as response:
                    health = json.load(response)
                break
            except (OSError, urllib.error.URLError):
                time.sleep(0.02)
        assert health == {"status": "ok", "v": 3}
    finally:
        process.terminate()
        output, _ = process.communicate(timeout=5)
    assert process.returncode == 0
    assert '"event":"started"' in output
    assert '"event":"stopped"' in output
    assert "127.0.0.1" not in output
