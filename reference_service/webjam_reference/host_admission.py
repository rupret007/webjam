"""Bounded, immutable allocation policy for already verified TLS host peers.

This module does not issue credentials or validate certificate chains itself.
The listener's dedicated client CA and normal TLS verification own that check.
Policy replacement and revocation take effect on deliberate service restart.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import ssl
import stat
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

from .protocol import ProtocolError

MAX_POLICY_BYTES = 65_536
MAX_HOST_ENTRIES = 128
_MAX_CERTIFICATE_BYTES = 65_536
_PRINCIPAL = re.compile(r"[0-9a-f]{32}\Z")
_FINGERPRINT = re.compile(r"[0-9a-f]{64}\Z")
_POLICY_ERROR = "invalid host admission policy"


@dataclass(frozen=True, slots=True, repr=False)
class HostPrincipal:
    principal: str
    certificate_sha256: str
    not_before: float
    not_after: float

    def __repr__(self) -> str:
        return "HostPrincipal(<redacted>)"


@dataclass(frozen=True, slots=True)
class HostAdmissionPolicy:
    principals: tuple[str, ...] = field(repr=False)
    _by_fingerprint: Mapping[str, str] = field(repr=False)

    @classmethod
    def load(cls, path: Path | str) -> HostAdmissionPolicy:
        try:
            encoded = _read_policy(Path(path))
            value = json.loads(
                encoded,
                object_pairs_hook=_unique_fields,
                parse_constant=_reject_constant,
            )
            if not isinstance(value, dict) or set(value) != {"v", "hosts"}:
                raise ValueError(_POLICY_ERROR)
            if type(value["v"]) is not int or value["v"] != 1:
                raise ValueError(_POLICY_ERROR)
            hosts = value["hosts"]
            if not isinstance(hosts, list) or not 1 <= len(hosts) <= MAX_HOST_ENTRIES:
                raise ValueError(_POLICY_ERROR)
            principals: list[str] = []
            fingerprints: dict[str, str] = {}
            for host in hosts:
                if not isinstance(host, dict) or set(host) != {"principal", "certificate_sha256"}:
                    raise ValueError(_POLICY_ERROR)
                principal, fingerprint = host["principal"], host["certificate_sha256"]
                if (
                    not isinstance(principal, str)
                    or _PRINCIPAL.fullmatch(principal) is None
                    or not isinstance(fingerprint, str)
                    or _FINGERPRINT.fullmatch(fingerprint) is None
                    or principal in principals
                    or fingerprint in fingerprints
                ):
                    raise ValueError(_POLICY_ERROR)
                principals.append(principal)
                fingerprints[fingerprint] = principal
            return cls(tuple(principals), MappingProxyType(fingerprints))
        except (OSError, ValueError, TypeError, RecursionError, OverflowError):
            # Never retain or echo filenames, JSON, certificate identities, or
            # parser/system exception details in startup diagnostics.
            raise ValueError(_POLICY_ERROR) from None

    def identify(self, ssl_object: object) -> HostPrincipal | None:
        """Read one verified leaf receipt; allocation still requires authorize.

        Only the server's completed TLS object belongs here. Wire fields, CN,
        email, display names and client-supplied principal IDs are not identity.
        """
        try:
            context = ssl_object.context
            if (
                not isinstance(context, ssl.SSLContext)
                or context.protocol != ssl.PROTOCOL_TLS_SERVER
                or context.verify_mode not in (ssl.CERT_OPTIONAL, ssl.CERT_REQUIRED)
                or ssl_object.version() != "TLSv1.3"
            ):
                return None
            encoded = ssl_object.getpeercert(binary_form=True)
            if not isinstance(encoded, bytes) or not 1 <= len(encoded) <= _MAX_CERTIFICATE_BYTES:
                return None
            fingerprint = hashlib.sha256(encoded).hexdigest()
            principal = self._by_fingerprint.get(fingerprint)
            if principal is None:
                return None
            metadata = ssl_object.getpeercert()
            if not isinstance(metadata, dict):
                return None
            before, after = metadata.get("notBefore"), metadata.get("notAfter")
            if not isinstance(before, str) or not isinstance(after, str) or len(before) > 64 or len(after) > 64:
                return None
            not_before, not_after = ssl.cert_time_to_seconds(before), ssl.cert_time_to_seconds(after)
            if not _finite(not_before) or not _finite(not_after) or not_before >= not_after:
                return None
            return HostPrincipal(principal, fingerprint, not_before, not_after)
        except Exception:
            # Extraction failures cannot downgrade an unrecognized peer into
            # an anonymous host. The server maps a missing receipt to denial.
            return None

    def authorize(self, receipt: HostPrincipal | None, now: float) -> str:
        if (
            type(receipt) is not HostPrincipal
            or not isinstance(receipt.principal, str)
            or _PRINCIPAL.fullmatch(receipt.principal) is None
            or not isinstance(receipt.certificate_sha256, str)
            or _FINGERPRINT.fullmatch(receipt.certificate_sha256) is None
            or not _finite(now)
            or not _finite(receipt.not_before)
            or not _finite(receipt.not_after)
            or self._by_fingerprint.get(receipt.certificate_sha256) != receipt.principal
            or receipt.principal not in self.principals
            or not receipt.not_before <= now < receipt.not_after
        ):
            raise ProtocolError("unauthorized")
        return receipt.principal


def _finite(value: object) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _identity(value: os.stat_result) -> tuple[int, ...]:
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns


def _cross_api_identity(value: os.stat_result) -> tuple[int, ...]:
    # CPython 3.12 Windows lstat uses creation time for ctime, while fstat
    # reports metadata change time. Keep ctime for the same-API checks below.
    identity = _identity(value)
    return identity[:-1] if os.name == "nt" else identity


def _read_policy(path: Path) -> bytes:
    original = path.lstat()
    if not stat.S_ISREG(original.st_mode) or not 0 < original.st_size <= MAX_POLICY_BYTES:
        raise ValueError(_POLICY_ERROR)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or _cross_api_identity(opened) != _cross_api_identity(original)
        ):
            raise ValueError(_POLICY_ERROR)
        data = _read_bounded_policy(descriptor)
        if (
            len(data) != original.st_size
            or _identity(os.fstat(descriptor)) != _identity(opened)
            or _identity(path.lstat()) != _identity(original)
        ):
            raise ValueError(_POLICY_ERROR)
        # Same-size rewrites can share filesystem timestamps. Verify the bytes
        # again through the owned descriptor, then repeat the metadata checks.
        # This is a finite stable-read check, not an atomic snapshot against a
        # writer deliberately changing the file between both observations.
        os.lseek(descriptor, 0, os.SEEK_SET)
        verified = _read_bounded_policy(descriptor)
        if (
            verified != data
            or _identity(os.fstat(descriptor)) != _identity(opened)
            or _identity(path.lstat()) != _identity(original)
        ):
            raise ValueError(_POLICY_ERROR)
        return data
    finally:
        os.close(descriptor)


def _read_bounded_policy(descriptor: int) -> bytes:
    data = bytearray()
    while len(data) <= MAX_POLICY_BYTES:
        chunk = os.read(descriptor, min(8192, MAX_POLICY_BYTES + 1 - len(data)))
        if not chunk:
            break
        data.extend(chunk)
    return bytes(data)


def _unique_fields(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(_POLICY_ERROR)
        result[key] = value
    return result


def _reject_constant(_: str) -> None:
    raise ValueError(_POLICY_ERROR)
