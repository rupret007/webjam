from __future__ import annotations

import hashlib
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from services.pocket_stage_tls import (
    PocketStageTlsError,
    PocketStageTlsIdentity,
    discover_private_lan_ipv4,
    validate_gateway_host,
)


@pytest.mark.parametrize(
    "host",
    [
        "0.0.0.0",
        "8.8.8.8",
        "169.254.12.3",
        "192.0.2.10",
        "198.51.100.20",
        "203.0.113.30",
        "::1",
        "example.test",
    ],
)
def test_gateway_rejects_wildcard_public_link_local_and_non_ipv4_hosts(host: str) -> None:
    with pytest.raises(PocketStageTlsError):
        validate_gateway_host(host)


def test_gateway_accepts_private_ipv4_and_test_only_loopback() -> None:
    assert validate_gateway_host("192.168.50.12") == "192.168.50.12"
    assert validate_gateway_host("10.20.30.40") == "10.20.30.40"
    assert validate_gateway_host("127.0.0.1", allow_loopback=True) == "127.0.0.1"
    with pytest.raises(PocketStageTlsError):
        validate_gateway_host("127.0.0.1")


def test_discovery_uses_route_selection_without_sending_payload() -> None:
    class Probe:
        sent = False
        closed = False

        def connect(self, target) -> None:
            assert target == ("192.0.2.1", 9)

        def getsockname(self):
            return ("192.168.4.8", 49999)

        def send(self, _payload) -> None:
            self.sent = True

        def close(self) -> None:
            self.closed = True

    probe = Probe()
    with patch("services.pocket_stage_tls.socket.socket", return_value=probe):
        assert discover_private_lan_ipv4() == "192.168.4.8"
    assert probe.closed is True
    assert probe.sent is False


def test_ephemeral_identity_has_exact_ip_san_pin_and_private_permissions() -> None:
    pytest.importorskip("cryptography")
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID

    identity = PocketStageTlsIdentity.create("192.168.4.8")
    directory = identity.directory
    try:
        assert identity.certificate_path.parent == directory
        assert identity.private_key_path.parent == directory
        assert identity.certificate_path.is_file()
        assert identity.private_key_path.is_file()
        if os.name == "posix":
            assert directory.stat().st_mode & 0o777 == 0o700
            assert identity.certificate_path.stat().st_mode & 0o777 == 0o600
            assert identity.private_key_path.stat().st_mode & 0o777 == 0o600

        certificate = x509.load_pem_x509_certificate(
            identity.certificate_path.read_bytes()
        )
        san = certificate.extensions.get_extension_for_class(
            x509.SubjectAlternativeName
        ).value
        assert [str(value) for value in san.get_values_for_type(x509.IPAddress)] == [
            "192.168.4.8"
        ]
        key_usage = certificate.extensions.get_extension_for_class(
            x509.KeyUsage
        )
        assert key_usage.critical is True
        assert key_usage.value.digital_signature is True
        assert key_usage.value.key_cert_sign is False
        eku = certificate.extensions.get_extension_for_class(
            x509.ExtendedKeyUsage
        )
        assert eku.critical is False
        assert list(eku.value) == [ExtendedKeyUsageOID.SERVER_AUTH]
        der = certificate.public_bytes(serialization.Encoding.DER)
        assert hashlib.sha256(der).hexdigest() == identity.fingerprint_sha256
        assert len(identity.fingerprint_sha256) == 64
        assert identity.not_after_unix == certificate.not_valid_after_utc.timestamp()
        certificate.public_key().verify(
            certificate.signature,
            certificate.tbs_certificate_bytes,
            ec.ECDSA(certificate.signature_hash_algorithm),
        )
        assert isinstance(certificate.signature_hash_algorithm, hashes.SHA256)
    finally:
        identity.cleanup()
    assert not Path(directory).exists()
