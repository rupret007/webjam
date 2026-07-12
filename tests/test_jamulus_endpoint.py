from __future__ import annotations

import pytest

from core.jamulus_endpoint import (
    JamulusEndpoint,
    JamulusEndpointError,
    parse_jamulus_endpoint,
)


@pytest.mark.parametrize(("raw", "expected"), [
    ("band.example.com", JamulusEndpoint("band.example.com", 22124)),
    ("BAND.EXAMPLE.COM.", JamulusEndpoint("band.example.com", 22124)),
    ("192.0.2.10", JamulusEndpoint("192.0.2.10", 22124)),
    ("198.51.100.23:23000", JamulusEndpoint("198.51.100.23", 23000)),
    ("localhost:22124", JamulusEndpoint("localhost", 22124)),
    ("[2001:db8::1]:22125", JamulusEndpoint("2001:db8::1", 22125)),
    ("2001:db8::1", JamulusEndpoint("2001:db8::1", 22124)),
])
def test_parse_jamulus_endpoint(raw, expected):
    assert parse_jamulus_endpoint(raw) == expected


@pytest.mark.parametrize("raw", [
    "", "   ", "https://band.example.com", "user@host", "host/path",
    "host?x=1", "host#fragment", "bad host", "host:", "host:0",
    "host:65536", "host:not-a-port", "-bad.example", "bad_.example",
    "[not-ipv6]:22124", "2001:db8::1:99999",
])
def test_invalid_jamulus_endpoint(raw):
    with pytest.raises(JamulusEndpointError):
        parse_jamulus_endpoint(raw)


def test_custom_default_port():
    assert parse_jamulus_endpoint("host", default_port=23000).port == 23000


def test_invalid_default_port_is_programmer_error():
    with pytest.raises(ValueError):
        parse_jamulus_endpoint("host", default_port=0)
