"""Ephemeral TLS identity and LAN binding helpers for Pocket Stage.

Pocket Stage deliberately does not reuse the loopback companion API.  Each
explicit mobile-sharing activation gets a fresh self-signed certificate and
the iPhone pins its SHA-256 fingerprint from the pairing QR code.  The private
key exists only in a mode-0700 temporary directory for the lifetime of the
gateway.
"""

from __future__ import annotations

import hashlib
import ipaddress
import os
import shutil
import socket
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


class PocketStageTlsError(RuntimeError):
    """Raised when a secure Pocket Stage endpoint cannot be created."""


_RFC1918_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
)


def is_rfc1918_ipv4(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> bool:
    """Return true only for the three private IPv4 ranges in RFC 1918."""

    return bool(
        address.version == 4
        and any(address in network for network in _RFC1918_NETWORKS)
    )


def discover_private_lan_ipv4() -> str:
    """Return the preferred private IPv4 address without sending a packet.

    Connecting a UDP socket only asks the OS which interface it would use; no
    traffic is emitted.  Refusing loopback, link-local, and public addresses
    keeps an accidental WAN-facing listener out of the ordinary product path.
    """

    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("192.0.2.1", 9))
        candidate = str(probe.getsockname()[0])
    except OSError as exc:
        raise PocketStageTlsError(
            "No private Wi-Fi address is available. Connect this computer and "
            "the iPhone to the same private network, then try again."
        ) from exc
    finally:
        probe.close()

    try:
        address = ipaddress.ip_address(candidate)
    except ValueError as exc:
        raise PocketStageTlsError("The selected network address is invalid.") from exc
    if (
        address.version != 4
        or not is_rfc1918_ipv4(address)
        or address.is_loopback
        or address.is_link_local
        or address.is_unspecified
        or address.is_multicast
    ):
        raise PocketStageTlsError(
            "Pocket Stage requires a private Wi-Fi address on this computer."
        )
    return candidate


def validate_gateway_host(host: str, *, allow_loopback: bool = False) -> str:
    """Validate an explicit bind address used by the runtime or tests."""

    try:
        address = ipaddress.ip_address(str(host).strip())
    except ValueError as exc:
        raise PocketStageTlsError("Pocket Stage requires a literal IP address.") from exc
    if address.version != 4 or address.is_unspecified or address.is_multicast:
        raise PocketStageTlsError("Pocket Stage requires a private IPv4 address.")
    if address.is_loopback:
        if allow_loopback:
            return str(address)
        raise PocketStageTlsError("Pocket Stage cannot use a loopback address.")
    if not is_rfc1918_ipv4(address):
        raise PocketStageTlsError("Pocket Stage requires a private Wi-Fi address.")
    return str(address)


@dataclass(frozen=True)
class PocketStageTlsIdentity:
    """Paths and public pin for one temporary gateway certificate."""

    directory: Path
    certificate_path: Path
    private_key_path: Path
    fingerprint_sha256: str
    not_after_unix: float

    @classmethod
    def create(cls, host: str) -> PocketStageTlsIdentity:
        """Create a short-lived ECDSA identity whose SAN is the LAN address."""

        try:
            from cryptography import x509
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import ec
            from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
        except ImportError as exc:  # pragma: no cover - packaging contract covers it
            raise PocketStageTlsError(
                "This WebJam build is missing its secure mobile-pairing component."
            ) from exc

        try:
            address = ipaddress.ip_address(host)
        except ValueError as exc:
            raise PocketStageTlsError("The Pocket Stage address is invalid.") from exc
        if address.version != 4:
            raise PocketStageTlsError("Pocket Stage currently requires IPv4.")

        directory = Path(tempfile.mkdtemp(prefix="webjam-pocket-stage-"))
        try:
            os.chmod(directory, 0o700)
            key = ec.generate_private_key(ec.SECP256R1())
            subject = issuer = x509.Name(
                [x509.NameAttribute(NameOID.COMMON_NAME, "WebJam Pocket Stage")]
            )
            now = datetime.now(timezone.utc)
            certificate = (
                x509.CertificateBuilder()
                .subject_name(subject)
                .issuer_name(issuer)
                .public_key(key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(now - timedelta(minutes=5))
                .not_valid_after(now + timedelta(hours=12))
                .add_extension(
                    x509.SubjectAlternativeName([x509.IPAddress(address)]),
                    critical=False,
                )
                .add_extension(
                    x509.BasicConstraints(ca=False, path_length=None),
                    critical=True,
                )
                .add_extension(
                    x509.KeyUsage(
                        digital_signature=True,
                        content_commitment=False,
                        key_encipherment=False,
                        data_encipherment=False,
                        key_agreement=False,
                        key_cert_sign=False,
                        crl_sign=False,
                        encipher_only=False,
                        decipher_only=False,
                    ),
                    critical=True,
                )
                .add_extension(
                    x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),
                    critical=False,
                )
                .sign(key, hashes.SHA256())
            )
            certificate_der = certificate.public_bytes(serialization.Encoding.DER)
            certificate_path = directory / "pocket-stage-cert.pem"
            private_key_path = directory / "pocket-stage-key.pem"
            certificate_path.write_bytes(
                certificate.public_bytes(serialization.Encoding.PEM)
            )
            private_key_path.write_bytes(
                key.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                )
            )
            os.chmod(certificate_path, 0o600)
            os.chmod(private_key_path, 0o600)
            return cls(
                directory=directory,
                certificate_path=certificate_path,
                private_key_path=private_key_path,
                fingerprint_sha256=hashlib.sha256(certificate_der).hexdigest(),
                not_after_unix=certificate.not_valid_after_utc.timestamp(),
            )
        except Exception:
            shutil.rmtree(directory, ignore_errors=True)
            raise

    def cleanup(self) -> None:
        """Remove both the private key and its containing temporary directory."""

        shutil.rmtree(self.directory, ignore_errors=True)
