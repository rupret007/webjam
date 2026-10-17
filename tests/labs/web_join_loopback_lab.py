"""Explicit native lab (no mocks, skips, sound devices or external server).

WEBJAM_WEB_JOIN_OPUS=/absolute/libwebjoin_opus.dylib \
WEBJAM_WEB_JOIN_JAMULUS=/absolute/JamulusServer \
python -m pytest tests/labs/web_join_loopback_lab.py -s

This intentionally requires real native dependencies when explicitly selected.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import socket
import subprocess
import time

import numpy as np
import pytest

from services.web_join_bridge import (
    CLOSE, HELLO, READY, JAMULUS_FRAMES, OpusCodec, WebJoinBridge,
    make_rtp, parse_rtp, _control, _parse_control,
)

pytestmark = pytest.mark.requires_local_socket


def native_library():
    path = Path(os.environ["WEBJAM_WEB_JOIN_OPUS"])
    assert path.is_absolute() and path.is_file(), "Provide the trusted custom-mode libopus artifact"
    return str(path)


def test_native_standard_and_custom_opus_transcode():
    codec = OpusCodec(native_library())
    try:
        samples = (np.sin(np.arange(960) * (2 * math.pi * 440 / 48000)) * 9000).astype(np.int16)
        pcm = np.column_stack((samples, samples)).tobytes()
        decoded = codec.decode_web(codec.encode_web(pcm))
        assert len(decoded) == len(pcm)
        custom = codec.encode_jamulus(decoded[:JAMULUS_FRAMES * 4])
        assert len(codec.decode_jamulus(custom)) == JAMULUS_FRAMES * 4
    finally:
        codec.close()


def test_browser_sine_through_real_jamulus_mix_and_latency(tmp_path):
    binary = Path(os.environ["WEBJAM_WEB_JOIN_JAMULUS"])
    assert binary.is_absolute() and binary.is_file(), "Provide the trusted Jamulus server executable"
    library = native_library()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    log_path = tmp_path / "jamulus.log"
    with log_path.open("wb") as log:
        server = subprocess.Popen([str(binary), "--server", "--nogui", "--serverbindip", "127.0.0.1",
            "--port", str(port), "--numchannels", "4", "--directoryaddress", "", "--notranslation"],
            stdout=log, stderr=subprocess.STDOUT)
        bridge = WebJoinBridge(port, opus_library=library, startup_timeout_s=3)
        browser = OpusCodec(library)
        peer = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            # Prove UDP readiness rather than assuming process launch time.
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
                probe.settimeout(.1)
                deadline = time.monotonic() + 5
                ready = False
                while time.monotonic() < deadline:
                    assert server.poll() is None, log_path.read_text()
                    probe.sendto(_control(1001, 0, bytes(4)), ("127.0.0.1", port))
                    try:
                        response, _address = probe.recvfrom(1500)
                    except socket.timeout:
                        continue
                    parsed = _parse_control(response)
                    if parsed and parsed[0] == 1001:
                        ready = True
                        break
                assert ready, "Jamulus UDP readiness probe failed\n" + log_path.read_text()
            bridge.start()
            peer.bind(("127.0.0.1", 0))
            peer.connect(("127.0.0.1", bridge.port))
            peer.settimeout(1)
            peer.send(HELLO + bridge.token)
            try:
                hello_response = peer.recv(100)
            except socket.timeout:
                pytest.fail("Bridge handshake failed: " + bridge.close_reason + "\n" + log_path.read_text())
            assert hello_response == READY + bridge.token
            peer.setblocking(False)
            received = []
            started = time.monotonic()
            first_signal_sent = None
            first_signal_returned = None
            # Warm up the real server fade-in, then modulate a sine. The initial
            # silence provides a detectable onset to measure added loopback delay.
            for index in range(125):
                target = started + index * .02
                time.sleep(max(0, target - time.monotonic()))
                sample_indexes = np.arange(960) + index * 960
                amplitude = 0 if index < 35 else 10000
                samples = (np.sin(sample_indexes * 2 * math.pi * 440 / 48000) * amplitude).astype(np.int16)
                pcm = np.column_stack((samples, samples)).tobytes()
                if index == 35:
                    first_signal_sent = time.monotonic()
                peer.send(make_rtp(browser.encode_web(pcm), index, index * 960))
                while True:
                    try:
                        raw = peer.recv(1500)
                    except BlockingIOError:
                        break
                    except ConnectionRefusedError:
                        pytest.fail("Bridge closed at block %d: %s\n%s" % (index, bridge.close_reason, log_path.read_text()))
                    _sequence, _timestamp, opus = parse_rtp(raw)
                    decoded = browser.decode_web(opus)
                    received.append(decoded)
                    rms = np.sqrt(np.mean(np.frombuffer(decoded, dtype=np.int16).astype(float) ** 2))
                    if first_signal_sent and first_signal_returned is None and rms > 700:
                        first_signal_returned = time.monotonic()
                assert not bridge.closed.is_set(), bridge.close_reason + "\n" + log_path.read_text()
            assert received, "Real Jamulus emitted no return mix"
            assert first_signal_returned is not None, "Browser sine did not pass through the real Jamulus mix"
            added_latency_ms = (first_signal_returned - first_signal_sent) * 1000
            assert 0 < added_latency_ms < 250, f"Unexpected added loopback latency {added_latency_ms:.1f} ms"
            stereo = np.frombuffer(b"".join(received), dtype=np.int16).reshape(-1, 2)
            tail = stereo[-24000:, 0].astype(float)
            spectrum = np.abs(np.fft.rfft(tail))
            peak_hz = float(np.fft.rfftfreq(len(tail), 1 / 48000)[np.argmax(spectrum)])
            assert abs(peak_hz - 440) <= 4
            assert float(np.sqrt(np.mean(tail ** 2))) > 1000
            evidence = {"sample_rate": 48000, "channels": 2, "peak_hz": peak_hz,
                "added_loopback_latency_ms": round(added_latency_ms, 2), "received_packets": len(received),
                "bridge_bytes": bridge.bytes_used, "path": "standard Opus -> custom Opus -> real Jamulus mix -> standard Opus"}
            (tmp_path / "web-join-latency.json").write_text(json.dumps(evidence, indent=2) + "\n")
            print("WEB_JOIN_LAB " + json.dumps(evidence, sort_keys=True))
            peer.send(CLOSE + bridge.token)
            assert bridge.closed.wait(2)
        finally:
            peer.close()
            bridge.close()
            browser.close()
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)
