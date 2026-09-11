"""Disposable TLS identities for real service tests; never production credentials."""

from __future__ import annotations

import hashlib
import json
import ssl
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from webjam_reference.config import ServiceConfig


@dataclass(frozen=True)
class TestCertificate:
    __test__ = False
    certificate: Path
    key: Path
    fingerprint: str
    expires: float


@dataclass(frozen=True)
class HostTLSMaterial:
    directory: Path
    server_ca: Path
    host_ca: Path
    server: TestCertificate
    clients: dict[str, TestCertificate]

    def manifest(self, names: tuple[str, ...] = ("a", "b")) -> Path:
        path = self.directory / ("admission-" + "-".join(names) + ".json")
        path.write_text(json.dumps({"v": 1, "hosts": [
            {"principal": name * 32,
             "certificate_sha256": self.clients[name].fingerprint}
            for name in names
        ]}))
        path.chmod(0o600)
        return path

    def config(self, **changes: object) -> ServiceConfig:
        values = dict(
            control_port=0, relay_port=0, http_port=0,
            tls_cert_path=self.server.certificate, tls_key_path=self.server.key,
            host_client_ca_path=self.host_ca, host_admission_path=self.manifest(),
            min_session_ttl_seconds=1, max_session_ttl_seconds=20,
            idle_timeout_seconds=10, cleanup_interval_seconds=0.02,
            tls_handshake_timeout_seconds=0.3,
            connection_write_timeout_seconds=0.3,
            connection_shutdown_timeout_seconds=0.3,
        )
        values.update(changes)
        return ServiceConfig(**values)

    def client_context(self, name: str | None = None) -> ssl.SSLContext:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.minimum_version = ssl.TLSVersion.TLSv1_3
        context.load_verify_locations(self.server_ca)
        if name is not None:
            client = self.clients[name]
            context.load_cert_chain(client.certificate, client.key)
        return context


@pytest.fixture
def host_tls_material(tmp_path: Path) -> HostTLSMaterial:
    """Generate real, ephemeral certificates without an external issuer or CLI."""
    tmp_path.chmod(0o700)
    now = datetime.now(UTC)

    def issue(name, *, issuer=None, ca=False, purpose=None, start=None, end=None):
        key = ec.generate_private_key(ec.SECP256R1())
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
        issuer_cert, issuer_key = issuer if issuer else (None, key)
        builder = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer_cert.subject if issuer_cert else subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(start or now - timedelta(days=1))
            .not_valid_after(end or now + timedelta(days=1))
            .add_extension(x509.BasicConstraints(ca=ca, path_length=0 if ca else None), critical=True)
            .add_extension(x509.KeyUsage(
                digital_signature=True, content_commitment=False,
                key_encipherment=False, data_encipherment=False,
                key_agreement=False, key_cert_sign=ca, crl_sign=ca,
                encipher_only=False, decipher_only=False,
            ), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        )
        if issuer_cert:
            builder = builder.add_extension(
                x509.AuthorityKeyIdentifier.from_issuer_public_key(issuer_key.public_key()), critical=False)
        if purpose:
            builder = builder.add_extension(x509.ExtendedKeyUsage([purpose]), critical=False)
        if purpose == ExtendedKeyUsageOID.SERVER_AUTH:
            builder = builder.add_extension(x509.SubjectAlternativeName([x509.DNSName("service.test")]), critical=False)
        cert = builder.sign(issuer_key, hashes.SHA256())
        cert_path, key_path = tmp_path / (name + ".pem"), tmp_path / (name + ".key")
        for path, data in (
            (cert_path, cert.public_bytes(serialization.Encoding.PEM)),
            (key_path, key.private_bytes(serialization.Encoding.PEM,
                                       serialization.PrivateFormat.PKCS8,
                                       serialization.NoEncryption())),
        ):
            with path.open("xb") as stream:
                path.chmod(0o600)
                stream.write(data)
        item = TestCertificate(cert_path, key_path,
                               hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest(),
                               cert.not_valid_after_utc.timestamp())
        return item, (cert, key)

    server_ca, server_signer = issue("server-ca", ca=True)
    host_ca, host_signer = issue("host-ca", ca=True)
    _, other_signer = issue("untrusted-ca", ca=True)
    server, _ = issue("server", issuer=server_signer, purpose=ExtendedKeyUsageOID.SERVER_AUTH)
    clients = {}
    for name in ("a", "b", "unapproved"):
        clients[name], _ = issue(name, issuer=host_signer, purpose=ExtendedKeyUsageOID.CLIENT_AUTH)
    clients["untrusted"], _ = issue("untrusted", issuer=other_signer, purpose=ExtendedKeyUsageOID.CLIENT_AUTH)
    clients["expired"], _ = issue("expired", issuer=host_signer, purpose=ExtendedKeyUsageOID.CLIENT_AUTH,
                                  start=now - timedelta(days=2), end=now - timedelta(days=1))
    clients["future"], _ = issue("future", issuer=host_signer, purpose=ExtendedKeyUsageOID.CLIENT_AUTH,
                                 start=now + timedelta(days=1), end=now + timedelta(days=2))
    clients["wrong-purpose"], _ = issue("wrong-purpose", issuer=host_signer, purpose=ExtendedKeyUsageOID.SERVER_AUTH)
    return HostTLSMaterial(tmp_path, server_ca.certificate, host_ca.certificate, server, clients)
