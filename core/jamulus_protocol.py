from __future__ import annotations

import socket
import struct
from typing import Dict, List, Optional


class JamulusProtocolAdapter:
    """
    Lightweight Jamulus UDP adapter.

    This adapter provides a stable API for controller integration.
    It sends best-effort command packets and tracks last-known participant cache.
    """

    def __init__(self, host: str, port: int, timeout: float = 1.0, enabled: bool = False):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.enabled = enabled
        self._sock: Optional[socket.socket] = None
        self._participants: Dict[int, str] = {}

    def connect(self) -> None:
        if not self.enabled:
            return
        if self._sock:
            return
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(self.timeout)
        self._sock = sock

    def close(self) -> None:
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None

    def request_clients(self) -> Dict[int, str]:
        """
        Request clients list from Jamulus endpoint.
        Falls back to cached participant list on failure.
        """
        self.connect()
        if not self.enabled:
            return dict(self._participants)
        if not self._sock:
            return dict(self._participants)

        try:
            # Simplified message body: [msg_type=REQ_CONN_CLIENTS_LIST]
            payload = struct.pack("!B", 5)
            self._sock.sendto(payload, (self.host, self.port))
            data, _ = self._sock.recvfrom(4096)
            parsed = self._parse_clients_payload(data)
            if parsed:
                self._participants = parsed
        except Exception:
            # Keep cached participant map.
            pass

        return dict(self._participants)

    def apply_mixer(
        self,
        channel_id: int,
        fader_level: int,
        pan: int,
        muted: bool,
    ) -> None:
        self.connect()
        if not self.enabled:
            return
        if not self._sock:
            return
        try:
            # Minimal custom packet for extension, not strict Jamulus binary schema.
            payload = struct.pack("!BHHHB", 2, channel_id, fader_level, pan, int(muted))
            self._sock.sendto(payload, (self.host, self.port))
        except Exception:
            pass

    def _parse_clients_payload(self, data: bytes) -> Dict[int, str]:
        """
        Parse a simple newline list payload:
        '0:UserA\\n1:UserB'

        If binary protocol payload arrives, parser returns empty map and controller
        will keep prior cache.
        """
        try:
            text = data.decode("utf-8", errors="strict")
        except Exception:
            return {}

        parsed: Dict[int, str] = {}
        for line in text.splitlines():
            if ":" not in line:
                continue
            idx, name = line.split(":", 1)
            try:
                parsed[int(idx.strip())] = name.strip() or f"Participant {idx.strip()}"
            except ValueError:
                continue
        return parsed

    def set_cached_participants(self, participants: Dict[int, str]) -> None:
        self._participants = dict(participants)

    def get_cached_participants(self) -> List[str]:
        return [self._participants[k] for k in sorted(self._participants.keys())]

