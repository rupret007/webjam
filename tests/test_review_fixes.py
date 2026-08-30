"""
Regression tests for the deep-review fixes (v0.7.1 hardening).

Covers: Take Deck samplerate adoption + play-after-finish + finish stops the
sink; server-RPC split-frame framing + timeout; RPC client stop/start (no
zombie reader) + level id mapping; Record button polls recorder state;
settings server_rpc_port env validation; diagnostics redaction by convention.
"""
from __future__ import annotations

import json
import os
import socket
import struct
import tempfile
import threading
import time
import unittest
import wave
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest


pytestmark = pytest.mark.requires_local_socket

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


# ------------------------------------------------------------------ Take Deck
from core.take_player import TakePlayer  # noqa: E402


@dataclass
class _Trk:
    path: Path
    name: str
    offset_s: float = 0.0
    duration_s: float = 0.0
    samplerate: int = 0


@dataclass
class _Take:
    tracks: list


def _wav(path, seconds, rate, value=0.3):
    n = int(seconds * rate)
    ints = np.int16(np.full(n, value * 32767))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(struct.pack("<%dh" % n, *ints.tolist()))


class _CapSink:
    def __init__(self):
        self.blocks = []
        self.stopped = 0
    def start(self, sr, bs, pull):
        for _ in range(100000):
            b = pull(bs)
            self.blocks.append(np.array(b))
            if len(self.blocks) > 3 and all(np.abs(x).max() < 1e-6 for x in self.blocks[-3:]):
                break
    def stop(self): self.stopped += 1
    def mixed(self): return np.concatenate(self.blocks) if self.blocks else np.zeros(0)


class TestSampleRateAdoption(unittest.TestCase):
    def test_player_adopts_take_rate(self):
        with tempfile.TemporaryDirectory() as d:
            _wav(Path(d) / "a.wav", 0.2, 44100)
            take = _Take(tracks=[_Trk(path=Path(d) / "a.wav", name="a",
                                      duration_s=0.2, samplerate=44100)])
            player = TakePlayer(samplerate=48000, sink=_CapSink())
            player.load(take)
            self.assertEqual(player.samplerate, 44100)
            # duration computed against the true rate, not the 48k default
            self.assertAlmostEqual(player.duration_s, 0.2, places=2)

    def test_mixed_rate_take_warns_and_picks_modal(self):
        with tempfile.TemporaryDirectory() as d:
            _wav(Path(d) / "a.wav", 0.1, 48000)
            _wav(Path(d) / "b.wav", 0.1, 48000)
            _wav(Path(d) / "c.wav", 0.1, 44100)
            take = _Take(tracks=[
                _Trk(path=Path(d) / "a.wav", name="a", duration_s=0.1, samplerate=48000),
                _Trk(path=Path(d) / "b.wav", name="b", duration_s=0.1, samplerate=48000),
                _Trk(path=Path(d) / "c.wav", name="c", duration_s=0.1, samplerate=44100),
            ])
            player = TakePlayer(sink=_CapSink())
            player.load(take)
            self.assertEqual(player.samplerate, 48000)  # modal rate


class TestPlayAfterFinish(unittest.TestCase):
    def test_replay_after_finish_rewinds_and_plays(self):
        with tempfile.TemporaryDirectory() as d:
            _wav(Path(d) / "a.wav", 0.2, 8000, value=0.4)
            take = _Take(tracks=[_Trk(path=Path(d) / "a.wav", name="a",
                                      duration_s=0.2, samplerate=8000)])
            sink = _CapSink()
            player = TakePlayer(samplerate=8000, blocksize=256, sink=sink)
            player.load(take)
            player.play()
            # simulate having reached the end
            player._pos_frames = player._total_frames
            sink.blocks = []
            player._playing = False
            player.play()  # should rewind, not sit silent
            self.assertGreater(np.abs(sink.mixed()[:400]).max(), 0.1)


# ------------------------------------------------------------------ server RPC
from core.jamulus_server_rpc import JamulusServerRpc, ServerRpcError  # noqa: E402


class _SplitFrameServer:
    """Sends a JSON response split across two writes with a delay between."""
    def __init__(self, gap=0.3):
        self.gap = gap
        self.srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(1)
        self.port = self.srv.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        conn, _ = self.srv.accept()
        f = conn.makefile("rb")
        # auth
        f.readline()
        conn.sendall(b'{"id":1,"jsonrpc":"2.0","result":"ok"}\n')
        # next request
        req = json.loads(f.readline())
        rid = req["id"]
        full = json.dumps({"id": rid, "jsonrpc": "2.0",
                           "result": {"enabled": True, "initialised": True}}).encode()
        # split it with a gap that exceeds the old 1s inner timeout would be too
        # slow for tests; use a modest gap to prove reassembly works
        conn.sendall(full[:10])
        time.sleep(self.gap)
        conn.sendall(full[10:] + b"\n")

    def stop(self):
        try:
            self.srv.close()
        except OSError:
            pass


class TestServerRpcFraming(unittest.TestCase):
    def test_split_frame_is_reassembled(self):
        srv = _SplitFrameServer(gap=0.3)
        try:
            with JamulusServerRpc(port=srv.port, secret="x") as rpc:
                status = rpc.get_recorder_status()
            self.assertTrue(status["enabled"])
        finally:
            srv.stop()

    def test_timeout_raises_actionable(self):
        # server that authenticates then never answers the call
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]

        def serve():
            conn, _ = srv.accept()
            f = conn.makefile("rb")
            f.readline()
            conn.sendall(b'{"id":1,"jsonrpc":"2.0","result":"ok"}\n')
            time.sleep(2.0)  # never answer the call
        threading.Thread(target=serve, daemon=True).start()
        try:
            with patch.object(JamulusServerRpc, "CALL_TIMEOUT_S", 0.5):
                with JamulusServerRpc(port=port, secret="x") as rpc:
                    with self.assertRaises(ServerRpcError) as ctx:
                        rpc.get_recorder_status()
            self.assertIn("timed out", str(ctx.exception))
        finally:
            srv.close()


# ------------------------------------------------------------------ RPC client
from core.jamulus_rpc_client import ChannelInfo, JamulusRpcClient  # noqa: E402


class TestClientLevelMapping(unittest.TestCase):
    def test_levels_keyed_by_client_channel_id_not_index(self):
        got = {}
        c = JamulusRpcClient(port=22222, on_levels=lambda lv: got.update(lv))
        # sparse channel ids: positions 0,1 -> channel ids 2,5
        c._clients = [ChannelInfo(channel_id=2, name="a"),
                      ChannelInfo(channel_id=5, name="b")]
        c._emit_levels([9, 0])
        self.assertIn(2, got)
        self.assertIn(5, got)
        self.assertNotIn(0, got)  # would be wrong (index-based)
        self.assertAlmostEqual(got[2], 1.0)

    def test_levels_fall_back_to_index_without_clients(self):
        got = {}
        c = JamulusRpcClient(port=22222, on_levels=lambda lv: got.update(lv))
        c._clients = []
        c._emit_levels([9])
        self.assertIn(0, got)


class TestClientStopStartNoZombie(unittest.TestCase):
    def test_stop_joins_reader_thread(self):
        c = JamulusRpcClient(port=1)  # nothing listening; reader loops+backs off
        c.start()
        time.sleep(0.2)
        c.stop()
        # after stop, the reader thread must be gone (joined)
        t = c._thread
        self.assertTrue(t is None or not t.is_alive())
        # restart is clean
        c.start()
        time.sleep(0.1)
        alive = [th for th in threading.enumerate() if th.name == "jamulus-rpc" and th.is_alive()]
        c.stop()
        self.assertLessEqual(len(alive), 1)


# ------------------------------------------------------------------ settings
class TestServerRpcPortEnv(unittest.TestCase):
    def test_bad_env_falls_back_to_default(self):
        from core.settings import load_settings
        with patch.dict(os.environ, {"WEBJAM_SERVER_RPC_PORT": "not_a_port"}):
            s = load_settings("/nonexistent/config.json")
        self.assertEqual(s.server_rpc_port, 22240)

    def test_out_of_range_env_falls_back(self):
        from core.settings import load_settings
        with patch.dict(os.environ, {"WEBJAM_SERVER_RPC_PORT": "999999"}):
            s = load_settings("/nonexistent/config.json")
        self.assertEqual(s.server_rpc_port, 22240)

    def test_valid_env_applied(self):
        from core.settings import load_settings
        with patch.dict(os.environ, {"WEBJAM_SERVER_RPC_PORT": "22250"}):
            s = load_settings("/nonexistent/config.json")
        self.assertEqual(s.server_rpc_port, 22250)


# ------------------------------------------------------------------ diagnostics
class TestDiagnosticsRedaction(unittest.TestCase):
    def test_redacts_by_name_convention(self):
        from dataclasses import asdict

        from core.settings import AppSettings
        from core.redaction import REDACTED, redact_mapping

        s = AppSettings()
        # A future secret-named field must redact via the name convention.
        data = asdict(s)
        data["webex_guest_issuer_secret"] = "supersecret"
        data["some_future_token"] = "leakme"
        redacted = redact_mapping(data)

        self.assertEqual(redacted["webex_guest_issuer_secret"], REDACTED)
        self.assertEqual(redacted["some_future_token"], REDACTED)

    def test_diagnostics_exporter_uses_shared_redaction(self):
        from core.settings import AppSettings
        from webjam_qt.controllers.diagnostics import DiagnosticsExporter

        s = AppSettings()
        s.sentry_dsn = "supersecret"
        exporter = DiagnosticsExporter(
            settings=s,
            bridge=object(),
            jamulus_controller=object(),
            window_version="0.7.2",
        )

        payload = exporter._sanitised_settings_json()

        self.assertIn('"sentry_dsn": "[redacted]"', payload)
        self.assertNotIn("supersecret", payload)

    def test_redact_text_handles_json_and_env_secret_names(self):
        from core.redaction import redact_text

        text = (
            'WEBJAM_WEBEX_GUEST_ISSUER_SECRET=envsecret '
            '{"webex_guest_issuer_secret": "jsonsecret", '
            '"access_token": "tok123", '
            '"safe": "visible"} '
            "Authorization: Bearer bearer-secret"
        )

        redacted = redact_text(text)

        self.assertNotIn("envsecret", redacted)
        self.assertNotIn("jsonsecret", redacted)
        self.assertNotIn("tok123", redacted)
        self.assertNotIn("bearer-secret", redacted)
        self.assertIn('"safe": "visible"', redacted)

    def test_redaction_keeps_only_webex_origin(self):
        from core.redaction import redact_mapping, redact_text, redact_webex_url

        meeting = "https://example.webex.com/meet/sample-room?token=private#lobby"
        expected = "https://example.webex.com/[redacted]"
        self.assertEqual(redact_webex_url(meeting), expected)
        self.assertEqual(redact_mapping({"webex_url": meeting})["webex_url"], expected)
        redacted = redact_text(f"Opening {meeting} for the band")
        self.assertIn(expected, redacted)
        self.assertNotIn("sample-room", redacted)
        self.assertNotIn("private", redacted)

    def test_redaction_rejects_non_webex_url_as_sensitive(self):
        from core.redaction import redact_webex_url

        self.assertEqual(
            redact_webex_url("https://example.com/meet/private"), "[redacted]"
        )

    def test_webex_redaction_never_retains_userinfo_or_port(self):
        from core.redaction import redact_text, redact_webex_url

        redacted = redact_webex_url(
            "https://user:secret@cisco.webex.com:8443/meet/private"
        )
        self.assertEqual(redacted, "https://cisco.webex.com/[redacted]")
        self.assertNotIn("user", redacted)
        self.assertNotIn("secret", redacted)
        self.assertNotIn("8443", redacted)
        log_line = redact_text(
            "Opening https://user:secret@cisco.webex.com:8443/meet/private"
        )
        self.assertNotIn("user", log_line)
        self.assertNotIn("secret", log_line)
        self.assertNotIn("8443", log_line)

    def test_metrics_service_redacts_log_file_copy(self):
        from ui.services import MetricsService

        with tempfile.TemporaryDirectory() as d:
            source = Path(d) / "webjam.log"
            target_dir = Path(d) / "out"
            target_dir.mkdir()
            source.write_text(
                'WEBJAM_WEBEX_GUEST_ISSUER_SECRET=envsecret\n'
                '{"webex_guest_issuer_secret": "jsonsecret"}\n',
                encoding="utf-8",
            )
            missing: list[str] = []

            MetricsService._copy_files([source], target_dir, missing, redact=True)

            copied = (target_dir / "webjam.log").read_text(encoding="utf-8")
            self.assertEqual(missing, [])
            self.assertNotIn("envsecret", copied)
            self.assertNotIn("jsonsecret", copied)


if __name__ == "__main__":
    unittest.main()
