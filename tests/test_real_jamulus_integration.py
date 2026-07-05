"""
Integration tests against a REAL Jamulus binary.

These retire the biggest standing risk in WebJam: until this suite, the
JSON-RPC client and the practice/band-server process orchestration had only
ever been exercised against a faithful fake. Here they meet the shipping
binary (jamulus-headless) — the apiAuth handshake, the server-mode method
surface the upcoming Record button will use, and the exact command lines
BridgeService spawns.

Skipped unless WEBJAM_JAMULUS_BINARY points at a Jamulus binary. In CI the
"Integration (real Jamulus)" job installs the pinned official .deb and sets
the variable; locally you can run:

    WEBJAM_JAMULUS_BINARY=$(command -v jamulus-headless) \
        pytest tests/test_real_jamulus_integration.py -v
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

JAMULUS = os.environ.get("WEBJAM_JAMULUS_BINARY", "")

RPC_HOST = "127.0.0.1"
SERVER_UDP_PORT = 22160
SERVER_RPC_PORT = 22161
SECRET = "webjam-integration-secret-0123456789"


def _wait_for_tcp(port: int, timeout_s: float = 20.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((RPC_HOST, port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.2)
    return False


def _tcp_listening(port: int) -> bool:
    try:
        with socket.create_connection((RPC_HOST, port), timeout=0.5):
            return True
    except OSError:
        return False


class RawRpc:
    """Minimal NDJSON JSON-RPC helper — deliberately independent of
    WebJam's own client so it can double-check the wire contract."""

    def __init__(self, port: int):
        self.sock = socket.create_connection((RPC_HOST, port), timeout=5.0)
        self.reader = self.sock.makefile("r", encoding="utf-8", newline="\n")
        self._id = 0

    def close(self):
        try:
            self.reader.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.sock.close()
        except Exception:  # noqa: BLE001
            pass

    def call(self, method: str, params: dict, timeout_s: float = 5.0):
        """Send a request; return the matching response object (or None on
        timeout/close). Notifications received meanwhile are ignored."""
        self._id += 1
        req_id = self._id
        line = json.dumps({
            "id": req_id, "jsonrpc": "2.0", "method": method, "params": params,
        }) + "\n"
        self.sock.sendall(line.encode("utf-8"))
        self.sock.settimeout(1.0)
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            try:
                raw = self.reader.readline()
            except socket.timeout:
                continue
            except OSError:
                return None
            if raw == "":
                return None  # server closed the connection
            try:
                obj = json.loads(raw)
            except ValueError:
                continue
            if obj.get("id") == req_id:
                return obj
        return None

    def auth(self, secret: str):
        return self.call("jamulus/apiAuth", {"secret": secret})


@unittest.skipUnless(JAMULUS, "WEBJAM_JAMULUS_BINARY not set — integration skipped")
class TestRealJamulusServerRpc(unittest.TestCase):
    """One real jamulus-headless server, shared across the RPC tests."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="webjam-int-")
        cls.secret_path = Path(cls.tmp.name) / "secret.txt"
        cls.secret_path.write_text(SECRET + "\n", encoding="utf-8")
        cls.rec_dir = Path(cls.tmp.name) / "recordings"
        cls.rec_dir.mkdir()
        cls.proc = subprocess.Popen(
            [
                JAMULUS, "--server", "--nogui",
                "--port", str(SERVER_UDP_PORT),
                "--jsonrpcport", str(SERVER_RPC_PORT),
                "--jsonrpcsecretfile", str(cls.secret_path),
                "--recording", str(cls.rec_dir),
                "--norecord",
            ],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        if not _wait_for_tcp(SERVER_RPC_PORT):
            out = b""
            if cls.proc.poll() is not None and cls.proc.stdout:
                out = cls.proc.stdout.read() or b""
            cls.proc.terminate()
            raise AssertionError(
                f"real Jamulus server RPC port never opened; exit="
                f"{cls.proc.poll()} output={out[-2000:]!r}"
            )

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        try:
            cls.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            cls.proc.kill()
        cls.tmp.cleanup()

    # -- wire-contract checks (independent RawRpc client) ------------------
    def test_api_auth_handshake_succeeds(self):
        rpc = RawRpc(SERVER_RPC_PORT)
        try:
            resp = rpc.auth(SECRET)
            self.assertIsNotNone(resp, "no auth response from real Jamulus")
            self.assertEqual(resp.get("result"), "ok", resp)
        finally:
            rpc.close()

    def test_wrong_secret_is_rejected(self):
        rpc = RawRpc(SERVER_RPC_PORT)
        try:
            resp = rpc.auth("definitely-not-the-secret")
            # Jamulus either answers with an error object or closes the
            # connection — both count as rejection; "ok" would be a failure.
            if resp is not None:
                self.assertNotEqual(resp.get("result"), "ok", resp)
        finally:
            rpc.close()

    def test_get_mode_reports_server(self):
        rpc = RawRpc(SERVER_RPC_PORT)
        try:
            self.assertEqual(rpc.auth(SECRET).get("result"), "ok")
            resp = rpc.call("jamulus/getMode", {})
            self.assertEqual(resp["result"]["mode"], "server", resp)
        finally:
            rpc.close()

    def test_recorder_status_and_toggle_contract(self):
        """The exact API surface the upcoming Record button will use."""
        rpc = RawRpc(SERVER_RPC_PORT)
        try:
            self.assertEqual(rpc.auth(SECRET).get("result"), "ok")

            status = rpc.call("jamulusserver/getRecorderStatus", {})["result"]
            self.assertTrue(status["initialised"], status)
            self.assertFalse(status["enabled"], status)  # --norecord
            self.assertEqual(status["recordingDirectory"], str(self.rec_dir))

            resp = rpc.call("jamulusserver/startRecording", {})
            self.assertEqual(resp.get("result"), "acknowledged", resp)
            deadline = time.monotonic() + 5.0
            enabled = False
            while time.monotonic() < deadline and not enabled:
                enabled = rpc.call(
                    "jamulusserver/getRecorderStatus", {}
                )["result"]["enabled"]
                if not enabled:
                    time.sleep(0.2)
            self.assertTrue(enabled, "recorder never armed after startRecording")

            resp = rpc.call("jamulusserver/stopRecording", {})
            self.assertEqual(resp.get("result"), "acknowledged", resp)
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline and enabled:
                enabled = rpc.call(
                    "jamulusserver/getRecorderStatus", {}
                )["result"]["enabled"]
                if enabled:
                    time.sleep(0.2)
            self.assertFalse(enabled, "recorder never disarmed after stopRecording")
        finally:
            rpc.close()

    def test_get_clients_shape(self):
        rpc = RawRpc(SERVER_RPC_PORT)
        try:
            self.assertEqual(rpc.auth(SECRET).get("result"), "ok")
            result = rpc.call("jamulusserver/getClients", {})["result"]
            self.assertIn("connections", result)
            self.assertIn("clients", result)
            self.assertEqual(result["connections"], 0)  # nobody connected
        finally:
            rpc.close()

    # -- WebJam's own RPC client against the real binary --------------------
    def test_webjam_rpc_client_authenticates_for_real(self):
        from core.jamulus_rpc_client import JamulusRpcClient

        client = JamulusRpcClient(
            port=SERVER_RPC_PORT, secret_path=self.secret_path,
        )
        try:
            client.start()
            deadline = time.monotonic() + 15.0
            while time.monotonic() < deadline and not client.available:
                time.sleep(0.2)
            self.assertTrue(
                client.available,
                "WebJam's JamulusRpcClient failed the real apiAuth handshake",
            )
            self.assertLess(client.last_activity_age(), 20.0)
        finally:
            client.stop()


@unittest.skipUnless(JAMULUS, "WEBJAM_JAMULUS_BINARY not set — integration skipped")
class TestWebJamServerRpcAgainstRealBinary(unittest.TestCase):
    """WebJam's Record-button transport (JamulusServerRpc) vs the real
    binary — the wrappers the Conductor's ● Record button calls."""

    UDP_PORT = 22164
    RPC_PORT = 22165

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="webjam-srpc-")
        cls.secret_path = Path(cls.tmp.name) / "secret.txt"
        cls.secret_path.write_text(SECRET + "\n", encoding="utf-8")
        rec = Path(cls.tmp.name) / "recordings"
        rec.mkdir()
        cls.proc = subprocess.Popen(
            [
                JAMULUS, "--server", "--nogui",
                "--port", str(cls.UDP_PORT),
                "--jsonrpcport", str(cls.RPC_PORT),
                "--jsonrpcsecretfile", str(cls.secret_path),
                "--recording", str(rec),
                "--norecord",
            ],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        if not _wait_for_tcp(cls.RPC_PORT):
            cls.proc.terminate()
            raise AssertionError("server RPC port never opened")

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        try:
            cls.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            cls.proc.kill()
        cls.tmp.cleanup()

    def test_record_button_cycle_for_real(self):
        from core.jamulus_server_rpc import JamulusServerRpc

        with JamulusServerRpc(port=self.RPC_PORT, secret=SECRET) as rpc:
            status = rpc.get_recorder_status()
            self.assertTrue(status["initialised"])
            self.assertFalse(status["enabled"])

            self.assertTrue(rpc.start_recording())
            deadline = time.monotonic() + 5.0
            enabled = False
            while time.monotonic() < deadline and not enabled:
                enabled = rpc.get_recorder_status()["enabled"]
                if not enabled:
                    time.sleep(0.2)
            self.assertTrue(enabled, "start_recording never armed the recorder")

            self.assertTrue(rpc.restart_recording())  # new take
            self.assertTrue(rpc.stop_recording())
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline and enabled:
                enabled = rpc.get_recorder_status()["enabled"]
                if enabled:
                    time.sleep(0.2)
            self.assertFalse(enabled, "stop_recording never disarmed")

    def test_get_clients_for_real(self):
        from core.jamulus_server_rpc import JamulusServerRpc

        with JamulusServerRpc(port=self.RPC_PORT, secret=SECRET) as rpc:
            result = rpc.get_clients()
        self.assertEqual(result["connections"], 0)
        self.assertIn("clients", result)

    def test_wrong_secret_raises_for_real(self):
        from core.jamulus_server_rpc import JamulusServerRpc, ServerRpcError

        with self.assertRaises(ServerRpcError):
            JamulusServerRpc(port=self.RPC_PORT, secret="wrong").connect()


@unittest.skipUnless(JAMULUS, "WEBJAM_JAMULUS_BINARY not set — integration skipped")
class TestPracticeModeCommandLine(unittest.TestCase):
    def test_practice_server_command_runs_on_real_binary(self):
        """Spawn EXACTLY the command BridgeService.launch_practice_session
        uses and prove the real binary accepts it and stays up."""
        from services.bridge_service import PRACTICE_PORT

        proc = subprocess.Popen(
            [JAMULUS, "--server", "--nogui", "--port", str(PRACTICE_PORT)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        try:
            time.sleep(2.5)
            if proc.poll() is not None:
                out = (proc.stdout.read() or b"")[-2000:]
                self.fail(
                    f"practice-mode server exited (code {proc.poll()}): {out!r}"
                )
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()

    def test_jsonrpcport_without_secretfile_is_refused(self):
        """BridgeService relies on this contract: --jsonrpcport without
        --jsonrpcsecretfile must not silently expose an unauthenticated RPC."""
        proc = subprocess.Popen(
            [JAMULUS, "--server", "--nogui",
             "--port", "22162", "--jsonrpcport", "22163"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline and proc.poll() is None:
                time.sleep(0.2)
            if proc.poll() is None:
                # Still running: acceptable only if the RPC port never opened.
                self.assertFalse(
                    _tcp_listening(22163),
                    "Jamulus exposed JSON-RPC without a secret file!",
                )
            else:
                self.assertNotEqual(proc.returncode, 0)
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()


if __name__ == "__main__":
    unittest.main()
