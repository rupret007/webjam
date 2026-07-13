from __future__ import annotations

import json

import pytest

from webjam_reference.config import ServiceConfig
from webjam_reference.protocol import (
    DatagramKind,
    ProtocolError,
    RelayFrame,
    ReplayWindow,
    Role,
    decode_fixed,
    encode_fixed,
    encode_relay,
    parse_control_line,
    parse_relay,
    verify_relay,
)


def test_config_is_loopback_only_by_default() -> None:
    config = ServiceConfig()
    assert config.control_bind == config.relay_bind == config.http_bind == "127.0.0.1"


def test_public_control_requires_explicit_transport_security() -> None:
    with pytest.raises(ValueError, match="public control bind requires TLS"):
        ServiceConfig(control_bind="0.0.0.0")
    assert ServiceConfig(
        control_bind="0.0.0.0", allow_insecure_public_control=True
    ).control_bind == "0.0.0.0"


def test_canonical_fixed_values_round_trip() -> None:
    raw = bytes(range(32))
    encoded = encode_fixed(raw)
    assert "=" not in encoded
    assert decode_fixed(encoded, 32) == raw
    for invalid in (encoded + "=", encoded[:-1], encoded[:-1] + "+", 4):
        with pytest.raises(ProtocolError, match="malformed"):
            decode_fixed(invalid, 32)


def test_control_parser_rejects_versions_unknown_shapes_and_oversize() -> None:
    assert parse_control_line(b'{"v":3,"op":"poll"}\n', 100)["op"] == "poll"
    with pytest.raises(ProtocolError, match="unsupported_version"):
        parse_control_line(b'{"v":2,"op":"poll"}\n', 100)
    with pytest.raises(ProtocolError, match="malformed"):
        parse_control_line(json.dumps(["not", "an", "object"]).encode() + b"\n", 100)
    with pytest.raises(ProtocolError, match="frame_too_large"):
        parse_control_line(b"x" * 101 + b"\n", 100)
    with pytest.raises(ProtocolError, match="malformed"):
        parse_control_line(b'{"v":3,"op":"poll","op":"close"}\n', 100)
    with pytest.raises(ProtocolError, match="malformed"):
        parse_control_line(b'{"v":3,"op":"poll","sequence":NaN}\n', 100)


def test_replay_window_allows_reordering_once_and_rejects_old_or_duplicate() -> None:
    window = ReplayWindow(8)
    assert window.accept(10)
    assert window.accept(12)
    assert window.accept(11)
    assert not window.accept(11)
    assert window.accept(20)
    assert not window.accept(10)
    assert not window.accept(-1)


def test_relay_codec_authenticates_header_and_opaque_payload() -> None:
    key = b"k" * 32
    frame = RelayFrame(Role.HOST, DatagramKind.DATA, b"s" * 32, 7, 99, b"opaque")
    encoded = encode_relay(frame, key)
    decoded, body, tag = parse_relay(encoded, 1_420)
    assert decoded == frame
    assert verify_relay(body, tag, key)
    assert not verify_relay(body, tag, b"x" * 32)
    damaged = encoded[:-17] + bytes([encoded[-17] ^ 1]) + encoded[-16:]
    _, damaged_body, damaged_tag = parse_relay(damaged, 1_420)
    assert not verify_relay(damaged_body, damaged_tag, key)


def test_client_delivery_frames_are_never_accepted_as_relay_input() -> None:
    delivery = encode_relay(
        RelayFrame(Role.GUEST, DatagramKind.DELIVERY, b"s" * 32, 1, 1, b"cipher"),
        b"k" * 32,
    )
    with pytest.raises(ProtocolError, match="malformed_datagram"):
        parse_relay(delivery, 1_420)
    frame, _, _ = parse_relay(delivery, 1_420, allow_delivery=True)
    assert frame.kind is DatagramKind.DELIVERY


def test_relay_datagram_size_is_hard_bounded() -> None:
    encoded = encode_relay(
        RelayFrame(Role.HOST, DatagramKind.DATA, b"s" * 32, 1, 1, b"x" * 1_350),
        b"k" * 32,
    )
    assert len(encoded) <= 1_420
    parse_relay(encoded, 1_420)
    with pytest.raises(ProtocolError, match="malformed_datagram"):
        parse_relay(encoded + b"x", len(encoded))


@pytest.mark.parametrize(
    "mutator",
    [
        lambda value: value[:4] + b"\x02" + value[5:],
        lambda value: b"BAD!" + value[4:],
        lambda value: value[:-1],
        lambda value: value + b"extra",
    ],
)
def test_relay_codec_rejects_malformed_and_versioned_frames(mutator) -> None:
    encoded = encode_relay(
        RelayFrame(Role.HOST, DatagramKind.BIND, b"s" * 32, 1, 0), b"k" * 32
    )
    with pytest.raises(ProtocolError):
        parse_relay(mutator(encoded), 1_420)
