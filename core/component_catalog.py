"""Signed, expiring Jamulus component catalog verification.

Catalog signatures cover canonical UTF-8 JSON for the payload only.  Parsing
rejects duplicate keys, floats, non-finite values, unknown fields, oversized
input, expired catalogs, future-issued catalogs, replay, rollback, and
same-sequence equivocation.  Exact component records are then validated by
``core.jamulus_compatibility``.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import stat
from types import MappingProxyType
from typing import Callable, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from core.component_hosts import HttpsHostPolicy, JAMULUS_RELEASE_HOST_POLICY
from core.component_lock import InterProcessComponentLock
from core.file_io import atomic_write_text
from core.jamulus_compatibility import (
    JamulusCompatibility,
    JamulusCompatibilityError,
    JamulusCompatibilityRegistry,
)


CATALOG_SCHEMA = 1
MAX_CATALOG_BYTES = 1_048_576
MAX_CATALOG_COMPONENTS = 256
MAX_CATALOG_LIFETIME = timedelta(days=31)
MAX_CLOCK_SKEW = timedelta(minutes=5)
_KEY_ID_RE = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9])?$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

# Generated once for WebJam's component catalog on 2026-07-28.  The matching
# private key is release-operator material and is never stored in this
# repository or packaged application.
EMBEDDED_CATALOG_PUBLIC_KEYS_BASE64 = MappingProxyType(
    {
        "webjam-component-2026-07": (
            "ztKv91ay3M3kr7f19CMKGBUKINostuHJTeVxm8LjD4c="
        )
    }
)


class ComponentCatalogError(ValueError):
    pass


class ComponentCatalogSignatureError(ComponentCatalogError):
    pass


class ComponentCatalogExpired(ComponentCatalogError):
    pass


class ComponentCatalogRollback(ComponentCatalogError):
    pass


class ComponentCatalogEquivocation(ComponentCatalogError):
    pass


def canonical_payload_bytes(payload: Mapping[str, object]) -> bytes:
    """Return the sole byte representation covered by an Ed25519 signature."""

    try:
        return json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ComponentCatalogError("catalog payload is not canonical JSON") from exc


@dataclass(frozen=True, slots=True)
class CatalogPublicKey:
    key_id: str
    raw_key: bytes

    def __post_init__(self) -> None:
        if not isinstance(self.key_id, str) or not _KEY_ID_RE.fullmatch(self.key_id):
            raise ComponentCatalogError("catalog key id is invalid")
        if not isinstance(self.raw_key, bytes) or len(self.raw_key) != 32:
            raise ComponentCatalogError(
                "catalog Ed25519 public key must be exactly 32 bytes"
            )
        try:
            Ed25519PublicKey.from_public_bytes(self.raw_key)
        except ValueError as exc:
            raise ComponentCatalogError("catalog public key is invalid") from exc

    @property
    def fingerprint_sha256(self) -> str:
        return hashlib.sha256(self.raw_key).hexdigest()

    @classmethod
    def from_base64(cls, key_id: str, value: str) -> "CatalogPublicKey":
        if not isinstance(value, str):
            raise ComponentCatalogError("catalog public key must be base64")
        try:
            raw = base64.b64decode(value, validate=True)
        except (ValueError, base64.binascii.Error) as exc:
            raise ComponentCatalogError("catalog public key is not valid base64") from exc
        return cls(key_id=key_id, raw_key=raw)


class CatalogKeyring:
    def __init__(self, keys: tuple[CatalogPublicKey, ...]) -> None:
        mapping: dict[str, CatalogPublicKey] = {}
        for key in keys:
            if key.key_id in mapping:
                raise ComponentCatalogError("duplicate catalog key id")
            mapping[key.key_id] = key
        if not mapping:
            raise ComponentCatalogError("catalog keyring cannot be empty")
        self._keys = MappingProxyType(mapping)

    @classmethod
    def embedded(cls) -> "CatalogKeyring":
        return cls(
            tuple(
                CatalogPublicKey.from_base64(key_id, encoded)
                for key_id, encoded in EMBEDDED_CATALOG_PUBLIC_KEYS_BASE64.items()
            )
        )

    def require(self, key_id: str) -> CatalogPublicKey:
        try:
            return self._keys[key_id]
        except KeyError as exc:
            raise ComponentCatalogSignatureError(
                "catalog was signed by an unknown key"
            ) from exc


@dataclass(frozen=True, slots=True)
class VerifiedComponentCatalog:
    schema: int
    sequence: int
    issued_at: datetime
    expires_at: datetime
    webjam_version: str
    components: tuple[JamulusCompatibility, ...]
    payload_sha256: str
    signer_key_id: str
    signer_fingerprint_sha256: str

    @property
    def registry(self) -> JamulusCompatibilityRegistry:
        return JamulusCompatibilityRegistry(self.components)

    def to_snapshot_dict(self) -> dict[str, object]:
        return {
            "schema": self.schema,
            "sequence": self.sequence,
            "issued_at": _format_time(self.issued_at),
            "expires_at": _format_time(self.expires_at),
            "webjam_version": self.webjam_version,
            "component_count": len(self.components),
            "payload_sha256": self.payload_sha256,
            "signer_key_id": self.signer_key_id,
            "signer_fingerprint_sha256": self.signer_fingerprint_sha256,
        }


class CatalogSequenceStore:
    """Crash-safe monotonic catalog sequence record.

    A repeated sequence is accepted only when the signed payload hash is
    identical.  The update occurs under an inter-process lock so two WebJam
    instances cannot race a newer catalog back to an older one.
    """

    def __init__(self, path: str | Path, *, lock_timeout: float = 5.0) -> None:
        self.path = Path(path)
        self.lock_path = self.path.with_name(f".{self.path.name}.lock")
        self.lock_timeout = float(lock_timeout)

    def compare_and_record(self, sequence: int, payload_sha256: str) -> None:
        sequence = _strict_int(
            sequence, label="catalog sequence", minimum=1, maximum=2**63 - 1
        )
        if not isinstance(payload_sha256, str) or not _SHA256_RE.fullmatch(
            payload_sha256
        ):
            raise ComponentCatalogError("catalog payload digest is invalid")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.is_symlink():
            raise ComponentCatalogError("catalog sequence state cannot be a symlink")
        with InterProcessComponentLock(
            self.lock_path, timeout=self.lock_timeout
        ):
            previous_sequence, previous_hash = self._read()
            if sequence < previous_sequence:
                raise ComponentCatalogRollback(
                    "catalog sequence is older than the accepted sequence"
                )
            if sequence == previous_sequence:
                if payload_sha256 != previous_hash:
                    raise ComponentCatalogEquivocation(
                        "catalog sequence was reused for different content"
                    )
                return
            payload = {
                "schema": 1,
                "highest_sequence": sequence,
                "payload_sha256": payload_sha256,
            }
            atomic_write_text(
                self.path,
                json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
                mode=0o600,
            )

    def snapshot(self) -> tuple[int, str]:
        if self.path.is_symlink():
            raise ComponentCatalogError("catalog sequence state cannot be a symlink")
        with InterProcessComponentLock(
            self.lock_path, timeout=self.lock_timeout
        ):
            return self._read()

    def _read(self) -> tuple[int, str]:
        if not self.path.exists():
            return (0, "")
        details = self.path.lstat()
        if (
            stat.S_ISLNK(details.st_mode)
            or not stat.S_ISREG(details.st_mode)
            or details.st_size > 4096
        ):
            raise ComponentCatalogError("catalog sequence state is invalid")
        try:
            value = _strict_json_loads(self.path.read_bytes(), maximum=4096)
        except OSError as exc:
            raise ComponentCatalogError("catalog sequence state is unreadable") from exc
        data = _strict_dict(
            value,
            keys=frozenset({"schema", "highest_sequence", "payload_sha256"}),
            label="catalog sequence state",
        )
        if data["schema"] != 1:
            raise ComponentCatalogError("catalog sequence state schema is unsupported")
        sequence = _strict_int(
            data["highest_sequence"],
            label="highest catalog sequence",
            minimum=1,
            maximum=2**63 - 1,
        )
        digest = data["payload_sha256"]
        if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
            raise ComponentCatalogError("catalog sequence state digest is invalid")
        return (sequence, digest)


class ComponentCatalogVerifier:
    def __init__(
        self,
        *,
        keyring: CatalogKeyring | None = None,
        host_policy: HttpsHostPolicy = JAMULUS_RELEASE_HOST_POLICY,
        sequence_store: CatalogSequenceStore | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.keyring = keyring or CatalogKeyring.embedded()
        self.host_policy = host_policy
        self.sequence_store = sequence_store
        self._now = now or (lambda: datetime.now(timezone.utc))

    def verify(
        self,
        envelope_bytes: bytes,
        *,
        webjam_version: str,
    ) -> VerifiedComponentCatalog:
        envelope = _strict_json_loads(
            envelope_bytes, maximum=MAX_CATALOG_BYTES
        )
        data = _strict_dict(
            envelope,
            keys=frozenset({"payload", "signature"}),
            label="catalog envelope",
        )
        signature = _strict_dict(
            data["signature"],
            keys=frozenset({"algorithm", "key_id", "value"}),
            label="catalog signature",
        )
        if signature["algorithm"] != "Ed25519":
            raise ComponentCatalogSignatureError(
                "catalog signature algorithm is unsupported"
            )
        key_id = signature["key_id"]
        if not isinstance(key_id, str):
            raise ComponentCatalogSignatureError("catalog signature key id is invalid")
        key = self.keyring.require(key_id)
        signature_bytes = _decode_signature(signature["value"])
        payload = data["payload"]
        if not isinstance(payload, dict):
            raise ComponentCatalogError("catalog payload must be an object")
        canonical = canonical_payload_bytes(payload)
        try:
            Ed25519PublicKey.from_public_bytes(key.raw_key).verify(
                signature_bytes, canonical
            )
        except InvalidSignature as exc:
            raise ComponentCatalogSignatureError(
                "catalog signature verification failed"
            ) from exc

        verified = self._validate_payload(
            payload,
            webjam_version=webjam_version,
            key=key,
            payload_sha256=hashlib.sha256(canonical).hexdigest(),
        )
        if self.sequence_store is not None:
            self.sequence_store.compare_and_record(
                verified.sequence, verified.payload_sha256
            )
        return verified

    def _validate_payload(
        self,
        payload: dict,
        *,
        webjam_version: str,
        key: CatalogPublicKey,
        payload_sha256: str,
    ) -> VerifiedComponentCatalog:
        data = _strict_dict(
            payload,
            keys=frozenset(
                {
                    "schema",
                    "sequence",
                    "issued_at",
                    "expires_at",
                    "webjam_version",
                    "components",
                }
            ),
            label="catalog payload",
        )
        if data["schema"] != CATALOG_SCHEMA:
            raise ComponentCatalogError("catalog schema is unsupported")
        sequence = _strict_int(
            data["sequence"],
            label="catalog sequence",
            minimum=1,
            maximum=2**63 - 1,
        )
        issued_at = _parse_time(data["issued_at"], label="catalog issued_at")
        expires_at = _parse_time(data["expires_at"], label="catalog expires_at")
        if expires_at <= issued_at:
            raise ComponentCatalogError("catalog expiry must follow its issue time")
        if expires_at - issued_at > MAX_CATALOG_LIFETIME:
            raise ComponentCatalogError("catalog validity window is too long")
        now = self._now()
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise ComponentCatalogError("catalog clock must be timezone-aware")
        now = now.astimezone(timezone.utc)
        if issued_at > now + MAX_CLOCK_SKEW:
            raise ComponentCatalogError("catalog issue time is in the future")
        if expires_at <= now:
            raise ComponentCatalogExpired("component catalog has expired")
        catalog_webjam = data["webjam_version"]
        if not isinstance(catalog_webjam, str) or catalog_webjam != webjam_version:
            raise ComponentCatalogError(
                "catalog does not target this exact WebJam version"
            )
        values = data["components"]
        if (
            not isinstance(values, list)
            or not values
            or len(values) > MAX_CATALOG_COMPONENTS
        ):
            raise ComponentCatalogError(
                "catalog components must be a bounded non-empty list"
            )
        try:
            components = tuple(
                JamulusCompatibility.from_dict(value) for value in values
            )
            registry = JamulusCompatibilityRegistry(components)
        except JamulusCompatibilityError as exc:
            raise ComponentCatalogError("catalog component policy is invalid") from exc
        for component in registry.entries:
            if not component.supports_webjam(webjam_version):
                raise ComponentCatalogError(
                    "catalog component is outside its WebJam compatibility range"
                )
            try:
                self.host_policy.validate_source(component.artifact.url)
            except ValueError as exc:
                raise ComponentCatalogError(
                    "catalog component URL is not an approved release origin"
                ) from exc
        return VerifiedComponentCatalog(
            schema=CATALOG_SCHEMA,
            sequence=sequence,
            issued_at=issued_at,
            expires_at=expires_at,
            webjam_version=webjam_version,
            components=registry.entries,
            payload_sha256=payload_sha256,
            signer_key_id=key.key_id,
            signer_fingerprint_sha256=key.fingerprint_sha256,
        )


def _decode_signature(value: object) -> bytes:
    if not isinstance(value, str) or len(value) > 128:
        raise ComponentCatalogSignatureError("catalog signature encoding is invalid")
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise ComponentCatalogSignatureError(
            "catalog signature is not valid base64"
        ) from exc
    if len(decoded) != 64:
        raise ComponentCatalogSignatureError(
            "catalog Ed25519 signature must be 64 bytes"
        )
    return decoded


def _parse_time(value: object, *, label: str) -> datetime:
    if not isinstance(value, str):
        raise ComponentCatalogError(f"{label} must be a UTC timestamp")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise ComponentCatalogError(
            f"{label} must use canonical UTC second precision"
        ) from exc
    return parsed.replace(tzinfo=timezone.utc)


def _format_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _strict_int(
    value: object,
    *,
    label: str,
    minimum: int,
    maximum: int,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ComponentCatalogError(f"{label} must be an integer")
    if not minimum <= value <= maximum:
        raise ComponentCatalogError(f"{label} is outside the allowed range")
    return value


def _strict_dict(value: object, *, keys: frozenset[str], label: str) -> dict:
    if not isinstance(value, dict):
        raise ComponentCatalogError(f"{label} must be an object")
    actual = frozenset(value)
    if actual != keys:
        missing = sorted(keys - actual)
        extra = sorted(actual - keys)
        raise ComponentCatalogError(
            f"{label} has an invalid schema (missing={missing}, extra={extra})"
        )
    return value


def _strict_json_loads(raw: bytes, *, maximum: int) -> object:
    if not isinstance(raw, bytes) or not raw or len(raw) > maximum:
        raise ComponentCatalogError("catalog JSON size is invalid")

    def object_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ComponentCatalogError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    def reject_float(value: str) -> object:
        raise ComponentCatalogError("catalog JSON cannot contain floating-point values")

    def reject_constant(value: str) -> object:
        raise ComponentCatalogError("catalog JSON cannot contain non-finite values")

    try:
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=object_pairs,
            parse_float=reject_float,
            parse_constant=reject_constant,
        )
    except UnicodeDecodeError as exc:
        raise ComponentCatalogError("catalog JSON is not valid UTF-8") from exc
    except json.JSONDecodeError as exc:
        raise ComponentCatalogError("catalog JSON is malformed") from exc


__all__ = [
    "CATALOG_SCHEMA",
    "CatalogKeyring",
    "CatalogPublicKey",
    "CatalogSequenceStore",
    "ComponentCatalogEquivocation",
    "ComponentCatalogError",
    "ComponentCatalogExpired",
    "ComponentCatalogRollback",
    "ComponentCatalogSignatureError",
    "ComponentCatalogVerifier",
    "EMBEDDED_CATALOG_PUBLIC_KEYS_BASE64",
    "VerifiedComponentCatalog",
    "canonical_payload_bytes",
]
