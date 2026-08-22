"""Release-operator helper for signing a component catalog payload.

The desktop runtime never needs private-key material.  This helper is kept
separate from verification so release automation can consume a key path
without putting key bytes in command arguments, logs, or the repository.
"""

from __future__ import annotations

import base64
import json
import os
import stat
from collections.abc import Mapping
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.component_catalog import (
    CatalogKeyring,
    ComponentCatalogError,
    canonical_payload_bytes,
)


def sign_component_catalog(
    payload: Mapping[str, object],
    *,
    private_key_path: str | Path,
    key_id: str,
    keyring: CatalogKeyring | None = None,
) -> bytes:
    """Return a canonical signed catalog envelope.

    ``private_key_path`` must be an absolute, non-symlinked, owner-private PEM
    file.  Its public half must match ``key_id`` in the provided (or embedded)
    keyring.  The helper deliberately has no logging calls.
    """

    path = Path(private_key_path)
    if not path.is_absolute():
        raise ComponentCatalogError("catalog private-key path must be absolute")
    try:
        details = path.lstat()
    except OSError as exc:
        raise ComponentCatalogError("catalog private key is unavailable") from exc
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
        raise ComponentCatalogError(
            "catalog private key must be a regular non-symlink file"
        )
    if details.st_size <= 0 or details.st_size > 16_384:
        raise ComponentCatalogError("catalog private-key size is invalid")
    if os.name == "posix" and details.st_mode & 0o077:
        raise ComponentCatalogError(
            "catalog private key must not be accessible to group or others"
        )
    try:
        private_bytes = path.read_bytes()
        private_key = serialization.load_pem_private_key(
            private_bytes, password=None
        )
    except (OSError, TypeError, ValueError) as exc:
        raise ComponentCatalogError("catalog private key could not be loaded") from exc
    finally:
        # Python cannot guarantee memory erasure, but retaining a second local
        # reference serves no purpose after deserialization.
        if "private_bytes" in locals():
            del private_bytes
    if not isinstance(private_key, Ed25519PrivateKey):
        raise ComponentCatalogError("catalog private key is not Ed25519")
    selected = (keyring or CatalogKeyring.embedded()).require(key_id)
    raw_public = private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    if raw_public != selected.raw_key:
        raise ComponentCatalogError(
            "catalog private key does not match the embedded public key"
        )
    canonical = canonical_payload_bytes(payload)
    signature = private_key.sign(canonical)
    envelope = {
        "payload": dict(payload),
        "signature": {
            "algorithm": "Ed25519",
            "key_id": key_id,
            "value": base64.b64encode(signature).decode("ascii"),
        },
    }
    return (
        json.dumps(
            envelope,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


__all__ = ["sign_component_catalog"]
