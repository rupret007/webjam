"""Host-local Web Join RTP/Opus to Jamulus adapter (one instance per guest).

The trusted desktop supplies a loopback Jamulus port. After Pion enrollment,
Pion authenticates its ephemeral loopback UDP socket using a per-instance token:
WJWB1 + 32 token bytes -> WJWA1 + token; thereafter packets are RTP v2/Opus.
WJWC1 + token closes the bridge. No browser-provided address is accepted.

Jamulus uses Opus *custom* 128-sample frames, not WebRTC's standard Opus.
Wire layout follows jamulussoftware/jamulus r3_12_2 src/protocol.cpp. Native
libopus must export custom-mode APIs; absence is a hard startup failure.
"""
from __future__ import annotations

import ctypes as C
import ctypes.util
import hmac
import ipaddress
import math
from pathlib import Path
import secrets
import select
import socket
import struct
import sys
import threading
import time
from typing import Callable

from core.jamulus_protocol import _crc16_ccitt

SAMPLE_RATE = 48000
CHANNELS = 2
JAMULUS_FRAMES = 128
JAMULUS_BYTES = 106
WEB_FRAMES = 960
MAX_OPUS_BYTES = 1275
MAX_QUOTA_BYTES = 256 * 1024 * 1024
MAX_PCM_BYTES = SAMPLE_RATE * CHANNELS * 2 * 60 // 1000
HELLO = b"WJWB1"
READY = b"WJWA1"
CLOSE = b"WJWC1"


class BridgeError(RuntimeError):
    pass


def _library_path(explicit: str | None) -> str:
    if explicit is not None:
        path = Path(explicit)
        if not path.is_absolute() or not path.is_file():
            raise BridgeError("Web Join Opus library must be a trusted absolute file path")
        return str(path)
    # Frozen app dependency is shipped beside the executable, outside client input.
    roots = [Path(sys.executable).resolve().parent.parent / "Frameworks",
             Path(__file__).resolve().parents[1] / "native" / "web-join"]
    for root in roots:
        for name in ("libwebjoin_opus.dylib", "libwebjoin_opus.so", "webjoin_opus.dll"):
            path = root / name
            if path.is_file():
                return str(path)
    system = ctypes.util.find_library("opus")
    if system:
        return system
    raise BridgeError("Web Join requires libopus built with --enable-custom-modes")


class OpusCodec:
    """Own all native states; fixed 48 kHz stereo, no sound-device access."""

    def __init__(self, library: str | None = None):
        self._states: list[tuple[str, int]] = []
        self._mode = None
        try:
            self.lib = C.CDLL(_library_path(library))
            ptr, integer = C.c_void_p, C.c_int
            signatures = {
                "opus_decoder_create": ([integer, integer, C.POINTER(integer)], ptr),
                "opus_encoder_create": ([integer, integer, integer, C.POINTER(integer)], ptr),
                "opus_custom_mode_create": ([integer, integer, C.POINTER(integer)], ptr),
                "opus_custom_encoder_create": ([ptr, integer, C.POINTER(integer)], ptr),
                "opus_custom_decoder_create": ([ptr, integer, C.POINTER(integer)], ptr),
                "opus_decode": ([ptr, ptr, integer, ptr, integer, integer], integer),
                "opus_encode": ([ptr, ptr, integer, ptr, integer], integer),
                "opus_custom_decode": ([ptr, ptr, integer, ptr, integer], integer),
                "opus_custom_encode": ([ptr, ptr, integer, ptr, integer], integer),
                "opus_packet_get_nb_samples": ([ptr, integer, integer], integer),
            }
            for name, (args, result) in signatures.items():
                fn = getattr(self.lib, name)
                fn.argtypes, fn.restype = args, result
            for name in ("opus_encoder", "opus_decoder", "opus_custom_encoder", "opus_custom_decoder", "opus_custom_mode"):
                fn = getattr(self.lib, name + "_destroy")
                fn.argtypes, fn.restype = [ptr], None
            for name in ("opus_encoder_ctl", "opus_custom_encoder_ctl"):
                # These are varargs C functions. Specify fixed arguments and explicitly
                # wrap the variadic integer (required by Apple arm64 ABI).
                fn = getattr(self.lib, name)
                fn.argtypes, fn.restype = [ptr, integer], integer
            error = integer()
            self._mode = self.lib.opus_custom_mode_create(SAMPLE_RATE, JAMULUS_FRAMES, C.byref(error))
            self._check(error.value)
            if not self._mode:
                raise BridgeError("Opus custom mode allocation failed")
            self.web_decoder = self._create("opus_decoder", SAMPLE_RATE, CHANNELS)
            self.web_encoder = self._create("opus_encoder", SAMPLE_RATE, CHANNELS, 2049)
            self.jam_decoder = self._create("opus_custom_decoder", self._mode, CHANNELS)
            self.jam_encoder = self._create("opus_custom_encoder", self._mode, CHANNELS)
            for request, value in ((4002, 128000), (4010, 5), (4012, 1), (4014, 10), (4016, 0)):
                self._check(self.lib.opus_encoder_ctl(self.web_encoder, request, integer(value)))
            # Custom CBR is fixed by max_data_bytes. Keep OPUS_BITRATE_MAX:
            # Opus 1.5.2 custom encoding with an explicit bitrate dereferences
            # the absent external entropy coder before initializing its own.
            for request, value in ((4006, 0), (4010, 5)):
                self._check(self.lib.opus_custom_encoder_ctl(self.jam_encoder, request, integer(value)))
        except (AttributeError, OSError) as exc:
            self.close()
            raise BridgeError("Web Join requires libopus built with --enable-custom-modes") from exc
        except Exception:
            self.close()
            raise

    @staticmethod
    def _check(result: int) -> int:
        if result < 0:
            raise BridgeError("Opus codec rejected audio")
        return result

    def _create(self, name: str, *args) -> int:
        error = C.c_int()
        state = getattr(self.lib, name + "_create")(*args, C.byref(error))
        self._check(error.value)
        if not state:
            raise BridgeError("Opus state allocation failed")
        self._states.append((name, state))
        return state

    def decode_web(self, payload: bytes) -> bytes:
        if not 1 <= len(payload) <= MAX_OPUS_BYTES:
            raise BridgeError("Invalid browser Opus packet size")
        frames = self.lib.opus_packet_get_nb_samples(payload, len(payload), SAMPLE_RATE)
        if frames not in (480, 960):
            raise BridgeError("Web Join accepts only 10 or 20 ms Opus packets")
        pcm = C.create_string_buffer(frames * CHANNELS * 2)
        actual = self._check(self.lib.opus_decode(self.web_decoder, payload, len(payload), pcm, frames, 0))
        return pcm.raw[:actual * CHANNELS * 2]

    def encode_web(self, pcm: bytes) -> bytes:
        if len(pcm) != WEB_FRAMES * CHANNELS * 2:
            raise BridgeError("Invalid browser PCM block")
        output = C.create_string_buffer(MAX_OPUS_BYTES)
        size = self._check(self.lib.opus_encode(self.web_encoder, pcm, WEB_FRAMES, output, MAX_OPUS_BYTES))
        return output.raw[:size]

    def encode_jamulus(self, pcm: bytes) -> bytes:
        if len(pcm) != JAMULUS_FRAMES * CHANNELS * 2:
            raise BridgeError("Invalid Jamulus PCM block")
        output = C.create_string_buffer(JAMULUS_BYTES)
        size = self._check(self.lib.opus_custom_encode(self.jam_encoder, pcm, JAMULUS_FRAMES, output, JAMULUS_BYTES))
        if size != JAMULUS_BYTES:
            raise BridgeError("Jamulus custom Opus must use fixed-size frames")
        return output.raw

    def decode_jamulus(self, payload: bytes) -> bytes:
        if len(payload) != JAMULUS_BYTES:
            raise BridgeError("Invalid Jamulus Opus packet size")
        pcm = C.create_string_buffer(JAMULUS_FRAMES * CHANNELS * 2)
        frames = self._check(self.lib.opus_custom_decode(self.jam_decoder, payload, len(payload), pcm, JAMULUS_FRAMES))
        if frames != JAMULUS_FRAMES:
            raise BridgeError("Invalid Jamulus Opus duration")
        return pcm.raw

    def close(self):
        for name, state in reversed(self._states):
            getattr(self.lib, name + "_destroy")(state)
        self._states.clear()
        if self._mode:
            self.lib.opus_custom_mode_destroy(self._mode)
            self._mode = None


def _control(message: int, count: int = 0, payload: bytes = b"") -> bytes:
    body = struct.pack("<HHBH", 0, message, count, len(payload)) + payload
    return body + struct.pack("<H", _crc16_ccitt(body))


def _parse_control(raw: bytes) -> tuple[int, int, bytes] | None:
    if len(raw) < 9 or raw[:2] != b"\0\0":
        return None
    _, message, count, size = struct.unpack_from("<HHBH", raw)
    if len(raw) != size + 9 or _crc16_ccitt(raw[:-2]) != struct.unpack_from("<H", raw, len(raw) - 2)[0]:
        return None
    return message, count, raw[7:-2]


def parse_rtp(raw: bytes) -> tuple[int, int, bytes]:
    if not 13 <= len(raw) <= 1500 or raw[0] >> 6 != 2:
        raise BridgeError("Invalid RTP packet")
    offset = 12 + 4 * (raw[0] & 15)
    if len(raw) < offset:
        raise BridgeError("Truncated RTP header")
    if raw[0] & 16:
        if len(raw) < offset + 4:
            raise BridgeError("Truncated RTP extension")
        offset += 4 + 4 * struct.unpack_from("!H", raw, offset + 2)[0]
    end = len(raw)
    if raw[0] & 32:
        padding = raw[-1]
        if padding == 0 or padding > end - offset:
            raise BridgeError("Invalid RTP padding")
        end -= padding
    if not 1 <= end - offset <= MAX_OPUS_BYTES:
        raise BridgeError("Invalid RTP payload size")
    sequence, timestamp = struct.unpack_from("!HI", raw, 2)
    return sequence, timestamp, raw[offset:end]


def make_rtp(payload: bytes, sequence: int, timestamp: int, ssrc: int = 1) -> bytes:
    return struct.pack("!BBHII", 0x80, 111, sequence & 65535, timestamp & 0xFFFFFFFF, ssrc) + payload


class WebJoinBridge:
    """Bounded worker and native codec owner; start is an explicit host action."""

    def __init__(self, jamulus_port: int, *, opus_library: str | None = None,
                 quota_bytes: int = MAX_QUOTA_BYTES, idle_timeout_s: float = 15,
                 startup_timeout_s: float = 5, max_duration_s: float = 3600,
                 authentication_timeout_s: float = 180,
                 codec_factory: Callable = OpusCodec):
        if type(jamulus_port) is not int or not 1 <= jamulus_port <= 65535:
            raise ValueError("Jamulus port must be a trusted local port")
        if type(quota_bytes) is not int or not 0 < quota_bytes <= MAX_QUOTA_BYTES:
            raise ValueError("Web Join quota may not exceed 256 MiB")
        for value in (idle_timeout_s, startup_timeout_s, max_duration_s, authentication_timeout_s):
            if not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError("Web Join requires finite positive timeouts")
        if idle_timeout_s > 60 or startup_timeout_s > 30 or max_duration_s > 3600 or authentication_timeout_s > 600:
            raise ValueError("Web Join timeout exceeds host safety bounds")
        self.jamulus_port = jamulus_port
        self.quota_bytes = quota_bytes
        self.idle_timeout_s, self.startup_timeout_s = idle_timeout_s, startup_timeout_s
        self.max_duration_s = max_duration_s
        self.authentication_timeout_s = authentication_timeout_s
        self._library, self._codec_factory = opus_library, codec_factory
        self.token = secrets.token_bytes(32)
        self.closed, self._stop = threading.Event(), threading.Event()
        self.close_reason = ""
        self.bytes_used = 0
        self._socket = self._jam = self._codec = self._thread = None
        self._peer = None
        self.port = 0
        self._input, self._output = bytearray(), bytearray()
        self._last_sequence = None
        self._control_count = 0
        self._pending: dict[tuple[int, int], tuple[bytes, float]] = {}
        self._last_control = None

    def start(self) -> None:
        if self._socket or self.closed.is_set():
            raise BridgeError("Web Join bridge cannot be restarted")
        try:
            self._codec = self._codec_factory(self._library)
            self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._socket.bind(("127.0.0.1", 0))
            self._socket.setblocking(False)
            self.port = self._socket.getsockname()[1]
            self._thread = threading.Thread(target=self._run, name="web-join-bridge", daemon=True)
            self._thread.start()
        except Exception:
            self._cleanup()
            raise

    def close(self) -> None:
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=2)
            if self._thread.is_alive():
                raise BridgeError("Web Join bridge failed to stop")
        elif not self._thread:
            self._cleanup()

    def _charge(self, size: int):
        if self.bytes_used + size > self.quota_bytes:
            raise BridgeError("quota exhausted")
        self.bytes_used += size

    def _send_control(self, message: int, payload: bytes = b""):
        if len(self._pending) >= 16:
            raise BridgeError("Jamulus control backlog exceeded")
        count = self._control_count
        self._control_count = (count + 1) & 255
        raw = _control(message, count, payload)
        self._jam.send(raw)
        self._pending[(message, count)] = (raw, time.monotonic())

    def _properties(self):
        # 106 bytes, one 128-sample frame, stereo, 48kHz, CT_OPUS, no counter.
        self._send_control(20, struct.pack("<IHB IHHI", JAMULUS_BYTES, 1, CHANNELS, SAMPLE_RATE, 2, 0, 0))

    def _open_jamulus(self):
        self._jam = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._jam.connect(("127.0.0.1", self.jamulus_port))
        self._jam.setblocking(False)
        # Audio creates the server channel; reliable properties follow it.
        self._jam.send(self._codec.encode_jamulus(bytes(JAMULUS_FRAMES * 4)))
        self._properties()
        name = b"Web Join (delay)"
        self._send_control(25, struct.pack("<HIBH", 0, 0, 0, len(name)) + name + b"\0\0")
        self._send_control(10, struct.pack("<H", 4))

    def _handle_control(self, message: int, count: int, payload: bytes):
        if message == 1:
            if len(payload) == 2:
                self._pending.pop((struct.unpack("<H", payload)[0], count), None)
            return
        if message >= 1000:
            if message in (1003, 1010):
                raise BridgeError("Jamulus server refused or disconnected guest")
            return
        self._jam.send(_control(1, count, struct.pack("<H", message)))
        key = (message, count)
        if key == self._last_control:
            return
        self._last_control = key
        if message == 21:
            self._properties()
        elif message == 11:
            self._send_control(10, struct.pack("<H", 4))
        elif message == 23:
            name = b"Web Join (delay)"
            self._send_control(25, struct.pack("<HIBH", 0, 0, 0, len(name)) + name + b"\0\0")
        elif message == 27:
            raise BridgeError("Jamulus server requires unsupported license consent")
        # Do not advertise split-message support: no unbounded roster allocation.

    def _recover_after_stall(self):
        """Discard stale live audio after an OS pause without expanding queues."""
        self._input.clear()
        self._output.clear()
        # Drain only a bounded number of already-queued local datagrams. Control
        # closes still take effect, and discarded admitted traffic still counts.
        for sock in (self._socket, self._jam):
            if sock is None:
                continue
            for _ in range(64):
                try:
                    raw, address = sock.recvfrom(1501)
                except BlockingIOError:
                    break
                if sock is self._socket:
                    if address != self._peer:
                        continue
                    if hmac.compare_digest(raw, CLOSE + self.token):
                        raise BridgeError("endpoint closed")
                    if hmac.compare_digest(raw, HELLO + self.token):
                        self._socket.sendto(READY + self.token, address)
                        continue
                    seq, _timestamp, _payload = parse_rtp(raw)
                    self._charge(len(raw))
                    if self._last_sequence is None or 0 < ((seq - self._last_sequence) & 65535) < 32768:
                        self._last_sequence = seq
                else:
                    control = _parse_control(raw)
                    if control:
                        self._handle_control(*control)

    def _pace_audio(self, now: float, next_tick: float) -> float:
        if now < next_tick:
            return next_tick
        if now - next_tick > .06:
            self._recover_after_stall()
            next_tick = now
        # Preserve the sample clock across ordinary jitter. Recover a long OS
        # pause with a dropout and one fresh frame, never a burst of old audio.
        due = min(8, 1 + int((now - next_tick) * SAMPLE_RATE / JAMULUS_FRAMES))
        size = JAMULUS_FRAMES * 4
        for _ in range(due):
            pcm = bytes(self._input[:size])
            del self._input[:size]
            self._jam.send(self._codec.encode_jamulus(pcm.ljust(size, b"\0")))
            next_tick += JAMULUS_FRAMES / SAMPLE_RATE
        return next_tick

    def _run(self):
        started = last_web = last_jam = time.monotonic()
        next_tick = started
        authenticated_at = None
        sequence, timestamp, ssrc = 0, 0, secrets.randbits(32)
        try:
            while not self._stop.is_set():
                now = time.monotonic()
                if now - started >= self.max_duration_s:
                    raise BridgeError("session expired")
                if self._peer is None and now - started >= self.authentication_timeout_s:
                    raise BridgeError("bridge authentication timeout")
                if self._peer and now - last_web >= self.idle_timeout_s:
                    raise BridgeError("browser media timeout")
                if authenticated_at and now - last_jam >= self.startup_timeout_s:
                    raise BridgeError("Jamulus media timeout")
                if self._jam:
                    next_tick = self._pace_audio(now, next_tick)
                sockets = [self._socket] + ([self._jam] if self._jam else [])
                readable, _, _ = select.select(sockets, [], [], min(0.02, max(0, next_tick - now)) if self._jam else 0.02)
                for sock in readable:
                    raw, address = sock.recvfrom(1501)
                    if sock is self._socket:
                        if self._peer is None:
                            if ipaddress.ip_address(address[0]).is_loopback and hmac.compare_digest(raw, HELLO + self.token):
                                self._peer = address
                                authenticated_at = last_web = last_jam = time.monotonic()
                                self._open_jamulus()
                                self._socket.sendto(READY + self.token, address)
                                next_tick = time.monotonic()
                            continue
                        if address != self._peer:
                            continue
                        if hmac.compare_digest(raw, HELLO + self.token):
                            self._socket.sendto(READY + self.token, address)
                            continue
                        if hmac.compare_digest(raw, CLOSE + self.token):
                            self.close_reason = "endpoint closed"
                            return
                        seq, _ts, payload = parse_rtp(raw)
                        if self._last_sequence is not None and not 0 < ((seq - self._last_sequence) & 65535) < 32768:
                            continue
                        self._charge(len(raw))
                        self._last_sequence = seq
                        pcm = self._codec.decode_web(payload)
                        if len(self._input) + len(pcm) > MAX_PCM_BYTES:
                            raise BridgeError("browser audio queue exceeded 60 ms")
                        self._input.extend(pcm)
                        last_web = time.monotonic()
                    else:
                        control = _parse_control(raw)
                        if control:
                            self._handle_control(*control)
                        elif len(raw) == JAMULUS_BYTES:
                            pcm = self._codec.decode_jamulus(raw)
                            last_jam = time.monotonic()
                            # Do not queue pre-input mix on the local UDP socket.
                            if self._last_sequence is None:
                                continue
                            self._output.extend(pcm)
                            if len(self._output) >= WEB_FRAMES * 4:
                                pcm = bytes(self._output[:WEB_FRAMES * 4])
                                del self._output[:WEB_FRAMES * 4]
                                packet = make_rtp(self._codec.encode_web(pcm), sequence, timestamp, ssrc)
                                self._charge(len(packet))
                                self._socket.sendto(packet, self._peer)
                                sequence, timestamp = (sequence + 1) & 65535, (timestamp + WEB_FRAMES) & 0xFFFFFFFF
                now = time.monotonic()
                for key, (raw, sent) in list(self._pending.items()):
                    if now - sent >= 0.25:
                        self._jam.send(raw)
                        self._pending[key] = (raw, now)
            self.close_reason = self.close_reason or "host closed"
        except (BridgeError, OSError, ValueError) as exc:
            self.close_reason = str(exc)
        except Exception:
            self.close_reason = "bridge worker failed"
        finally:
            self._cleanup()

    def _cleanup(self):
        if self._jam:
            try:
                self._jam.send(_control(1010))
            except OSError:
                pass
            self._jam.close()
            self._jam = None
        if self._socket:
            self._socket.close()
            self._socket = None
        if self._codec:
            self._codec.close()
            self._codec = None
        self._input.clear()
        self._output.clear()
        self._pending.clear()
        self.closed.set()
