from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


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
    command = [sys.executable, "-m", "webjam_reference"]
    process_options = {}
    if os.name == "nt":
        # TerminateProcess bypasses Python cleanup. A new process group lets
        # this test send CTRL_BREAK only to its own child; the test bootstrap
        # forwards that event to the unchanged module's real SIGINT/Runner path.
        # This is not a claim that the native CLI handles CTRL_BREAK itself.
        command = [sys.executable, "-c", (
            "import runpy, signal; "
            "signal.signal(signal.SIGBREAK, lambda *_: signal.raise_signal(signal.SIGINT)); "
            "runpy.run_module('webjam_reference', run_name='__main__')"
        )]
        process_options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    command.extend([
            "--control-port",
            str(control_port),
            "--relay-port",
            str(relay_port),
            "--http-port",
            str(http_port),
    ])
    process = subprocess.Popen(
        command,
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        **process_options,
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
        try:
            if process.poll() is None:
                if os.name == "nt":
                    process.send_signal(signal.CTRL_BREAK_EVENT)
                else:
                    process.terminate()
            output, _ = process.communicate(timeout=5)
        except BaseException:
            # Failure cleanup must reap the child, but cannot count as a
            # successful graceful shutdown or satisfy the stopped assertion.
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)
            raise
    assert process.returncode == (130 if os.name == "nt" else 0), output
    assert '"event":"started"' in output
    assert '"event":"stopped"' in output
    assert "127.0.0.1" not in output
