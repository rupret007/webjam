"""
Jamulus UDP protocol adapter.

Implements the Jamulus binary protocol packet format for sending mixer
control commands and receiving participant/level updates.

Protocol format (all values little-endian):
    [2B: msg_id] [2B: count] [2B: data_len] [N bytes: data] [2B: CRC]

CRC: CRC-16-CCITT (poly=0x1021, init=0xFFFF, final XOR=0xFFFF)
     Applied over: id + count + data_len + data

This is the secondary integration path (fallback when JSON-RPC is
unavailable).  It implements only what WebJam needs:
  - REQ_CONN_CLIENTS_LIST / CONN_CLIENTS_LIST    (participant list)
  - CHANNEL_GAIN                                  (fader control)
  - CLT_CHANNEL_LEVEL_LIST                        (audio levels, server push)
  - ACKNOWLEDGE                                   (ACK responses)

Connection model: WebJam opens a secondary UDP socket to the Jamulus
server.  This appears as an additional client, but with no audio data
the server eventually disconnects it.  We reconnect automatically.

Note: per-channel level data requires the server to send
CLT_CHANNEL_LEVEL_LIST — this happens automatically for connected clients.
For the secondary (monitoring) client, the server may or may not send
levels; behaviour depends on the Jamulus version and server config.
"""

from __future__ import annotations

import logging
import socket
import struct
import threading
import time
from typing import Dict, List, Optional, Tuple

_logger = logging.getLogger("webjam.jamulus_protocol")


# ---------------------------------------------------------------------------
# Message IDs (from Jamulus src/protocol.h)
# ---------------------------------------------------------------------------
class _MsgId:
    ACKNOWLEDGE              = 1
    CHANNEL_GAIN             = 3
    CHANNEL_PAN              = 4
    DISCONNECT               = 13
    VERSION_AND_OS           = 17
    REQ_VERSION_AND_OS       = 18
    REQ_CONN_CLIENTS_LIST    = 6
    CONN_CLIENTS_LIST        = 7
    CLT_CHANNEL_LEVEL_LIST   = 27
    REQ_CHANNEL_LEVEL_LIST   = 28
    CLT_REQ_CHANNEL_INFOS    = 24
    CHANNEL_INFOS            = 25
    MUTE_STATE_CHANGED       = 30
    SPLIT_MESS_SUPPORTED     = 31
    JITT_BUF_SIZE            = 10
    REQ_JITT_BUF_SIZE        = 11
    NETW_TRANSPORT_PROPS     = 15
    REQ_NETW_TRANSPORT_PROPS = 16


# ---------------------------------------------------------------------------
# CRC
# ---------------------------------------------------------------------------
def _crc16_ccitt(data: bytes) -> int:
    """CRC-16-CCITT as used by Jamulus (poly=0x1021, init=0xFFFF, xorout=0xFFFF)."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc ^ 0xFFFF


# ---------------------------------------------------------------------------
# Packet builder / parser
# ---------------------------------------------------------------------------
def _build_packet(msg_id: int, count: int, data: bytes) -> bytes:
    header = struct.pack("<HHH", msg_id, count, len(data))
    payload = header + data
    crc = _crc16_ccitt(payload)
    return payload + struct.pack("<H", crc)


def _parse_packet(raw: bytes) -> Optional[Tuple[int, int, bytes]]:
    """
    Parse a raw UDP datagram into ``(msg_id, count, data)``.

    Returns None on CRC mismatch or truncated packet.
    """
    if len(raw) < 8:  # 2+2+2 header + 0 data + 2 crc
        return None
    msg_id, count, data_len = struct.unpack_from("<HHH", raw, 0)
    expected_size = 6 + data_len + 2
    if len(raw) < expected_size:
        return None
    data = raw[6: 6 + data_len]
    crc_received = struct.unpack_from("<H", raw, 6 + data_len)[0]
    crc_computed = _crc16_ccitt(raw[:6 + data_len])
    if crc_received != crc_computed:
        _logger.debug(
            "CRC mismatch msg_id=%d: got %04x expected %04x",
            msg_id, crc_received, crc_computed,
        )
        return None
    return msg_id, count, data


# ---------------------------------------------------------------------------
# Payload parsers
# ---------------------------------------------------------------------------
def _parse_le_string(data: bytes, offset: int) -> Tuple[str, int]:
    """Parse a Jamulus LE-prefixed UTF-8 string.  Returns (string, new_offset)."""
    if offset + 2 > len(data):
        return "", offset
    length = struct.unpack_from("<H", data, offset)[0]
    offset += 2
    end = offset + length
    if end > len(data):
        end = len(data)
    text = data[offset:end].decode("utf-8", errors="replace")
    return text, end


def _parse_conn_clients_list(data: bytes) -> Dict[int, str]:
    """
    Parse CONN_CLIENTS_LIST payload.

    Per-entry format:
        uint16_t channel_id
        uint16_t country
        uint32_t instrument
        uint8_t  skill_level
        string   name    (LE uint16 length + UTF-8 bytes)
        string   city    (LE uint16 length + UTF-8 bytes)
    """
    clients: Dict[int, str] = {}
    offset = 0
    while offset + 9 <= len(data):
        try:
            channel_id = struct.unpack_from("<H", data, offset)[0]
            offset += 2
            offset += 2    # country
            offset += 4    # instrument
            offset += 1    # skill_level
            name, offset = _parse_le_string(data, offset)
            _city, offset = _parse_le_string(data, offset)
            clients[channel_id] = name.strip() or f"Participant {channel_id}"
        except struct.error:
            break
    return clients


def _parse_level_list(data: bytes) -> Dict[int, float]:
    """
    Parse CLT_CHANNEL_LEVEL_LIST payload.

    Each entry is a uint16_t; the number of entries = total connected channels.
    Values are 0..32767 mapped to 0..1.
    """
    levels: Dict[int, float] = {}
    count = len(data) // 2
    for i in range(count):
        raw = struct.unpack_from("<H", data, i * 2)[0]
        levels[i] = min(1.0, raw / 32767.0)
    return levels


# ---------------------------------------------------------------------------
# Main adapter
# ---------------------------------------------------------------------------
class JamulusProtocolAdapter:
    """
    UDP adapter to a Jamulus server.

    Can be used as a secondary monitoring client to:
    - Receive the connected-client list (CONN_CLIENTS_LIST)
    - Receive per-channel audio levels (CLT_CHANNEL_LEVEL_LIST)
    - Send CHANNEL_GAIN commands

    ``enabled=True`` activates actual UDP communication.
    ``enabled=False`` keeps the adapter as an in-memory stub (prior behaviour).
    """

    RECONNECT_WAIT_S = 5.0
    POLL_INTERVAL_S  = 3.0

    def __init__(
        self,
        host: str,
        port: int,
        timeout: float = 1.0,
        enabled: bool = False,
        *,
        on_participants_changed: Optional[callable] = None,
        on_levels: Optional[callable] = None,
    ) -> None:
        if not host or not isinstance(host, str):
            raise ValueError(f"host must be non-empty string, got {host!r}")
        if not isinstance(port, int) or not (1 <= port <= 65535):
            raise ValueError(f"port must be 1-65535, got {port!r}")

        self.host = host
        self.port = port
        self.timeout = timeout
        self.enabled = enabled
        self._on_participants_changed = on_participants_changed
        self._on_levels = on_levels

        self._sock: Optional[socket.socket] = None
        self._participants: Dict[int, str] = {}
        self._participants_lock = threading.Lock()

        self._running = False
        self._rx_thread: Optional[threading.Thread] = None
        self._tx_count = 1

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def connect(self) -> None:
        if not self.enabled:
            return
        if self._sock:
            return
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(self.timeout)
            self._sock = sock
            _logger.info("Jamulus UDP adapter bound, targeting %s:%d", self.host, self.port)
        except Exception as exc:
            _logger.warning("UDP socket creation failed: %s", exc)

    def close(self) -> None:
        self._running = False
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None

    def start_receiving(self) -> None:
        """Start background thread that receives Jamulus protocol messages."""
        if not self.enabled or self._running:
            return
        self.connect()
        self._running = True
        self._rx_thread = threading.Thread(
            target=self._receive_loop, daemon=True, name="jamulus-udp-rx"
        )
        self._rx_thread.start()
        # Send initial handshake
        self._send_req_conn_clients_list()
        self._send_req_channel_level_list()

    def stop_receiving(self) -> None:
        self._running = False
        self.close()

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------
    def apply_mixer(
        self,
        channel_id: int,
        fader_level: int,
        pan: int,
        muted: bool,
    ) -> None:
        """Send CHANNEL_GAIN + CHANNEL_PAN to the server."""
        if not self.enabled or not self._sock:
            return
        effective_level = 0 if muted else max(0, min(127, fader_level))
        # Convert 0..127 → 0..32767
        gain = int(effective_level / 127.0 * 32767)
        pan_val = max(0, min(100, pan))
        try:
            self._send(_MsgId.CHANNEL_GAIN, struct.pack("<HH", channel_id, gain))
            self._send(_MsgId.CHANNEL_PAN, struct.pack("<HH", channel_id, pan_val))
        except Exception as exc:
            _logger.debug("apply_mixer send failed: %s", exc)

    def request_clients(self) -> Dict[int, str]:
        """Return cached participant list (populated from background thread)."""
        if not self.enabled:
            with self._participants_lock:
                return dict(self._participants)
        self._send_req_conn_clients_list()
        with self._participants_lock:
            return dict(self._participants)

    def set_cached_participants(self, participants: Dict[int, str]) -> None:
        with self._participants_lock:
            self._participants = dict(participants)

    def get_cached_participants(self) -> List[str]:
        with self._participants_lock:
            return [self._participants[k] for k in sorted(self._participants.keys())]

    # ------------------------------------------------------------------
    # Internal — send helpers
    # ------------------------------------------------------------------
    def _send(self, msg_id: int, data: bytes = b"") -> None:
        if not self._sock:
            return
        count = self._tx_count
        self._tx_count = (self._tx_count + 1) & 0xFFFF
        packet = _build_packet(msg_id, count, data)
        try:
            self._sock.sendto(packet, (self.host, self.port))
        except Exception as exc:
            _logger.debug("UDP send failed: %s", exc)

    def _send_req_conn_clients_list(self) -> None:
        if not self.enabled or not self._sock:
            return
        self._send(_MsgId.REQ_CONN_CLIENTS_LIST)

    def _send_req_channel_level_list(self) -> None:
        if not self.enabled or not self._sock:
            return
        self._send(_MsgId.REQ_CHANNEL_LEVEL_LIST)

    def _send_ack(self, ack_id: int, ack_count: int) -> None:
        # ACK payload: [2B: msg_id being acked] [2B: count being acked]
        data = struct.pack("<HH", ack_id, ack_count)
        self._send(_MsgId.ACKNOWLEDGE, data)

    # ------------------------------------------------------------------
    # Receive loop
    # ------------------------------------------------------------------
    def _receive_loop(self) -> None:
        last_poll = 0.0
        while self._running:
            now = time.monotonic()
            if now - last_poll >= self.POLL_INTERVAL_S:
                last_poll = now
                self._send_req_conn_clients_list()
                self._send_req_channel_level_list()

            if not self._sock:
                time.sleep(self.RECONNECT_WAIT_S)
                self.connect()
                continue

            try:
                raw, _ = self._sock.recvfrom(8192)
                self._handle_packet(raw)
            except socket.timeout:
                pass  # Expected — we use short timeout and poll in the loop
            except OSError as exc:
                if self._running:
                    _logger.debug("UDP receive error: %s", exc)
                    self._sock = None
                    time.sleep(self.RECONNECT_WAIT_S)
                    self.connect()

    def _handle_packet(self, raw: bytes) -> None:
        parsed = _parse_packet(raw)
        if parsed is None:
            return
        msg_id, count, data = parsed

        if msg_id == _MsgId.ACKNOWLEDGE:
            return  # Nothing to do; we don't have reliability-required messages pending

        # ACK all non-ACK messages so server doesn't resend endlessly
        if msg_id != _MsgId.ACKNOWLEDGE:
            self._send_ack(msg_id, count)

        if msg_id == _MsgId.CONN_CLIENTS_LIST:
            clients = _parse_conn_clients_list(data)
            if clients:
                with self._participants_lock:
                    self._participants = clients
                if self._on_participants_changed:
                    try:
                        self._on_participants_changed(dict(clients))
                    except Exception as exc:  # noqa: BLE001
                        _logger.debug("on_participants_changed callback error: %s", exc)

        elif msg_id == _MsgId.CLT_CHANNEL_LEVEL_LIST:
            levels = _parse_level_list(data)
            if levels and self._on_levels:
                try:
                    self._on_levels(levels)
                except Exception as exc:  # noqa: BLE001
                    _logger.debug("on_levels callback error: %s", exc)

        elif msg_id == _MsgId.REQ_VERSION_AND_OS:
            # Server probing our version — respond with minimal VERSION_AND_OS
            # OS type: 0=Windows, 1=macOS, 2=Linux (send 1 for macOS)
            import sys
            os_type = 2 if sys.platform.startswith("linux") else (1 if sys.platform == "darwin" else 0)
            version_bytes = "WebJam/0.1".encode("utf-8")
            # string format: [2B LE: length][bytes]
            version_str = struct.pack("<H", len(version_bytes)) + version_bytes
            self._send(_MsgId.VERSION_AND_OS, struct.pack("<H", os_type) + version_str)

        elif msg_id == _MsgId.REQ_NETW_TRANSPORT_PROPS:
            # Respond with minimal network transport properties to keep connection alive
            # Format: [4B: block_size_samples][2B: codec][2B: flags][4B: version]
            props = struct.pack("<IHHH", 128, 2, 0, 5)  # 128 samples, OPUS codec, no flags, v5
            self._send(_MsgId.NETW_TRANSPORT_PROPS, props)

        else:
            _logger.debug("Unhandled Jamulus msg_id=%d len=%d", msg_id, len(data))
