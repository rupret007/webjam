#!/usr/bin/env python3
"""Create and self-verify WebJam's signed Jamulus component catalog.

Run this only from a trusted release workstation. The Ed25519 private key is
read from an owner-private absolute file, never from a command argument value,
environment variable, log, repository file, or desktop package.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
import stat

from core.component_catalog import ComponentCatalogVerifier
from core.component_catalog_signing import sign_component_catalog
from core.file_io import atomic_write_bytes
from core.jamulus_compatibility import (
    ComponentTarget,
    JamulusRole,
    official_jamulus_compatibility_registry,
)
from webjam_qt import __version__


CATALOG_FILENAME = "WebJam-Jamulus-components-v1.json"
CATALOG_KEY_ID = "webjam-component-2026-07"
APPROVED_COMPONENT_VERSION = "3.12.3"
EXPECTED_COMPONENT_COUNT = 8


def build_payload(
    *,
    sequence: int,
    issued_at: datetime,
    validity_days: int,
) -> dict[str, object]:
    if isinstance(sequence, bool) or not 1 <= sequence <= 2**63 - 1:
        raise ValueError("sequence must be a positive 63-bit integer")
    if isinstance(validity_days, bool) or not 1 <= validity_days <= 30:
        raise ValueError("validity-days must be between 1 and 30")
    if issued_at.tzinfo is None:
        raise ValueError("issued-at must be timezone-aware")
    issued = issued_at.astimezone(timezone.utc).replace(microsecond=0)
    expires = issued + timedelta(days=validity_days)
    registry = official_jamulus_compatibility_registry()
    components = tuple(
        entry
        for entry in registry.entries
        if entry.version == APPROVED_COMPONENT_VERSION
        and entry.role in {JamulusRole.CLIENT, JamulusRole.SERVER}
    )
    if len(components) != EXPECTED_COMPONENT_COUNT:
        raise ValueError(
            "the approved client/server component inventory is incomplete"
        )
    for component in components:
        if component.target in {
            ComponentTarget.MACOS_ARM64,
            ComponentTarget.MACOS_X64,
        } and component.capabilities.includes(
            {"webjam-route-profile"}
            if component.role is JamulusRole.CLIENT
            else {"recording"}
        ):
            raise ValueError(
                "an upstream macOS source artifact claims WebJam runtime-file "
                "capabilities"
            )
    return {
        "schema": 1,
        "sequence": sequence,
        "issued_at": _format_time(issued),
        "expires_at": _format_time(expires),
        "webjam_version": __version__,
        "components": [entry.to_dict() for entry in components],
    }


def create_catalog(
    *,
    sequence: int,
    issued_at: datetime,
    validity_days: int,
    private_key_path: Path,
    output_path: Path,
) -> bytes:
    if not output_path.is_absolute():
        raise ValueError("output path must be absolute")
    if output_path.name != CATALOG_FILENAME:
        raise ValueError(f"output filename must be {CATALOG_FILENAME}")
    if output_path.exists() or output_path.is_symlink():
        details = output_path.lstat()
        if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
            raise ValueError("output must be a regular non-symlink file")
    payload = build_payload(
        sequence=sequence,
        issued_at=issued_at,
        validity_days=validity_days,
    )
    envelope = sign_component_catalog(
        payload,
        private_key_path=private_key_path,
        key_id=CATALOG_KEY_ID,
    )
    verified = ComponentCatalogVerifier(
        now=lambda: issued_at.astimezone(timezone.utc)
    ).verify(envelope, webjam_version=__version__)
    if (
        verified.sequence != sequence
        or len(verified.components) != EXPECTED_COMPONENT_COUNT
        or verified.signer_key_id != CATALOG_KEY_ID
    ):
        raise ValueError("signed catalog self-verification was incomplete")
    atomic_write_bytes(output_path, envelope, mode=0o644)
    return envelope


def _parse_issued_at(value: str) -> datetime:
    candidate = str(value).strip()
    if not candidate:
        return datetime.now(timezone.utc).replace(microsecond=0)
    if not candidate.endswith("Z"):
        raise argparse.ArgumentTypeError("issued-at must end in Z")
    try:
        parsed = datetime.fromisoformat(candidate[:-1] + "+00:00")
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "issued-at must use YYYY-MM-DDTHH:MM:SSZ"
        ) from exc
    if parsed.microsecond:
        raise argparse.ArgumentTypeError("issued-at cannot contain fractions")
    return parsed


def _format_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create one signed WebJam Jamulus component catalog."
    )
    parser.add_argument("--sequence", required=True, type=int)
    parser.add_argument(
        "--issued-at",
        default="",
        type=_parse_issued_at,
        help="UTC YYYY-MM-DDTHH:MM:SSZ; defaults to the current time",
    )
    parser.add_argument("--validity-days", type=int, default=30)
    parser.add_argument("--private-key", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    issued_at = (
        arguments.issued_at
        if isinstance(arguments.issued_at, datetime)
        else _parse_issued_at(arguments.issued_at)
    )
    create_catalog(
        sequence=arguments.sequence,
        issued_at=issued_at,
        validity_days=arguments.validity_days,
        private_key_path=arguments.private_key,
        output_path=arguments.output,
    )
    print(
        f"Created and self-verified {CATALOG_FILENAME}: "
        f"sequence {arguments.sequence}, expires "
        f"{_format_time(issued_at + timedelta(days=arguments.validity_days))}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
