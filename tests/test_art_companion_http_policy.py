"""Opt-in URLSession policy checks against an actual loopback HTTP listener.

One initial byte lets CFNetwork expose headers; the rest of each stalled body
stays blocked until Swift finishes. The chunked case sends 64 KiB + 1 bytes and
withholds its terminator. Separate zero-byte responses check bounded unavailability
when CFNetwork exposes no status, even with the host's no-store/nosniff headers.
No request is logged.
"""

from __future__ import annotations

import os
import secrets
import signal
import subprocess
import sys
import tempfile
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = [
    pytest.mark.requires_local_socket,
    pytest.mark.skipif(
        sys.platform != "darwin"
        or os.environ.get("WEBJAM_RUN_SWIFT_ART_COMPANION_INTEGRATION") != "1",
        reason="requires explicit macOS Swift HTTP policy integration gate",
    ),
]


def _require(condition: bool, message: str) -> None:
    if not condition:
        pytest.fail(message, pytrace=False)


def _failure_diagnostic(checkpoint: Path) -> str:
    for category in (
        "authentication", "unavailable", "invalid-response", "oversized-response",
        "accepted-response", "other-fixed-error",
    ):
        if checkpoint.with_name(checkpoint.name + "." + category).is_file():
            return f"Swift HTTP policy probe observed {category} instead of the expected rejection."
    return "Swift HTTP policy probe reported a failed assertion; captured output is withheld."


class _PolicyServer(ThreadingHTTPServer):
    def __init__(self, scenario: str, token: str, participant: str, checkpoint: Path) -> None:
        self.scenario = scenario
        self.token = token
        self.participant = participant
        self.checkpoint = checkpoint
        self.requests = 0
        self.targets = 0
        self.count_lock = threading.Lock()
        self.invalid_request = threading.Event()
        self.headers_sent = threading.Event()
        self.stream_sent = threading.Event()
        self.release_response = threading.Event()
        super().__init__(("127.0.0.1", 0), _PolicyHandler)

    def handle_error(self, request, client_address) -> None:
        # BaseHTTPServer's traceback could retain request details.
        self.invalid_request.set()


class _PolicyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args) -> None:
        pass

    def do_GET(self) -> None:
        self._respond()

    def do_POST(self) -> None:
        self._respond()

    def _headers(self, status: int, *, length: int | None = None, chunked: bool = False) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Connection", "close")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if length is not None:
            self.send_header("Content-Length", str(length))
        if chunked:
            self.send_header("Transfer-Encoding", "chunked")
        if status == 307:
            self.send_header("Location", "/redirect-target")
        self.end_headers()
        self.wfile.flush()

    def _respond(self) -> None:
        server = self.server
        self.connection.settimeout(5)
        with server.count_lock:
            server.requests += 1
            if self.path == "/redirect-target":
                server.targets += 1
        try:
            self._response_body(server)
        except (BrokenPipeError, ConnectionResetError):
            # An early policy rejection deliberately cancels the response.
            pass

    def _response_body(self, server: _PolicyServer) -> None:
        if self.path == "/redirect-target":
            self._headers(200, length=2)
            self.wfile.write(b"{}")
            return
        is_redirect = server.scenario == "redirect"
        valid = (
            self.path == ("/v1/enroll" if is_redirect else "/v1/state")
            and self.command == ("POST" if is_redirect else "GET")
            and self.headers.get("Authorization") == f"Bearer {server.token}"
            and (is_redirect or self.headers.get("X-WebJam-Participant") == server.participant)
        )
        if not valid:
            server.invalid_request.set()
            self._headers(400, length=0)
            return
        if is_redirect:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 4096 or len(self.rfile.read(length)) != length:
                server.invalid_request.set()
                self._headers(400, length=0)
                return
            self._headers(307, length=0)
        elif server.scenario == "streamed-oversize":
            self._headers(200, chunked=True)
            for _ in range(8):
                self.wfile.write(b"2000\r\n" + b"x" * 8192 + b"\r\n")
                self.wfile.flush()
            self.wfile.write(b"1\r\nx\r\n")
            self.wfile.flush()
            server.stream_sent.set()
        else:
            status = 401 if server.scenario.startswith("unauthorized-") else (
                403 if server.scenario.startswith("forbidden-") else 200
            )
            declared_oversize = server.scenario.startswith("declared-") or server.scenario.endswith("-oversize")
            self._headers(status, length=65537 if declared_oversize else 2)
            if "-opaque-" not in server.scenario:
                # CFNetwork requires an initial byte to expose response headers
                # on this runtime. The body remains incomplete and never ends
                # until the policy assertion finishes; no full JSON can decode.
                self.wfile.write(b"x")
                self.wfile.flush()
        server.headers_sent.set()
        server.checkpoint.with_name(server.checkpoint.name + ".headers").touch()
        if not is_redirect:
            # No remaining body or final chunk can arrive before the assertion.
            server.release_response.wait(timeout=15)


def _stop_probe(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=5)


@pytest.mark.parametrize("scenario", [
    "redirect", "declared-oversize", "streamed-oversize",
    "unauthorized-oversize", "forbidden-oversize", "unauthorized-stall", "forbidden-stall",
    "cancel-stall", "close-stall",
    "declared-opaque-stall", "unauthorized-opaque-stall",
])
def test_swift_urlsession_enforces_live_http_response_policy(tmp_path: Path, scenario: str) -> None:
    session_id = str(uuid.uuid4())
    token = secrets.token_urlsafe(32)
    checkpoint = tmp_path / "policy-confirmed"
    server = _PolicyServer(scenario, token, session_id, checkpoint)
    listener = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    process = None
    listener.start()
    try:
        environment = os.environ.copy()
        environment.update({
            "WEBJAM_ART_HTTP_TEST_ENDPOINT": f"http://127.0.0.1:{server.server_port}/",
            "WEBJAM_ART_HTTP_TEST_SESSION": session_id,
            "WEBJAM_ART_HTTP_TEST_TOKEN": token,
            "WEBJAM_ART_HTTP_TEST_CASE": scenario,
            "WEBJAM_ART_HTTP_TEST_CHECKPOINT": str(checkpoint),
        })
        # Framework diagnostics may contain private values. Keep captured output
        # ephemeral and report only fixed policy failures, never child output.
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            process = subprocess.Popen(
                ["swift", "test", "--package-path", str(ROOT / "ios"),
                 "--filter", "liveArtCompanionHTTPPolicy"],
                env=environment, cwd=ROOT, stdout=stdout, stderr=stderr,
                start_new_session=True,
            )
            try:
                result = process.wait(timeout=120)
            except subprocess.TimeoutExpired:
                pytest.fail("Swift HTTP policy probe timed out.", pytrace=False)
            _require(result == 0, _failure_diagnostic(checkpoint))
            _require(checkpoint.is_file(), "Swift HTTP policy probe did not confirm the expected error.")
    finally:
        _stop_probe(process)
        server.release_response.set()
        server.shutdown()
        server.server_close()
        listener.join(timeout=5)
    _require(not listener.is_alive(), "The HTTP policy listener did not stop.")
    _require(not server.invalid_request.is_set(), "The HTTP policy listener received an unexpected request.")
    _require(server.headers_sent.is_set(), "The HTTP policy response was not delivered.")
    _require(server.requests == 1, "The HTTP policy client sent an unexpected additional request.")
    _require(server.targets == 0, "The HTTP policy client followed a forbidden redirect.")
    if scenario == "streamed-oversize":
        _require(server.stream_sent.is_set(), "The HTTP policy listener did not send the oversized stream.")
