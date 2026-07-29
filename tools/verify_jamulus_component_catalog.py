#!/usr/bin/env python3
"""Verify one public Jamulus catalog with the packaged WebJam trust policy."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import stat

from core.component_catalog import (
    MAX_CATALOG_BYTES,
    ComponentCatalogError,
    ComponentCatalogVerifier,
    VerifiedComponentCatalog,
)
from core.jamulus_compatibility import (
    JamulusRole,
    official_jamulus_compatibility_registry,
)
from webjam_qt import __version__


APPROVED_COMPONENT_VERSION = "3.12.3"


def verify_catalog_file(
    path: Path,
    *,
    webjam_version: str = __version__,
    minimum_sequence: int = 1,
) -> VerifiedComponentCatalog:
    if not path.is_absolute():
        raise ComponentCatalogError("catalog path must be absolute")
    try:
        details = path.lstat()
    except OSError as exc:
        raise ComponentCatalogError("catalog file is unavailable") from exc
    if (
        stat.S_ISLNK(details.st_mode)
        or not stat.S_ISREG(details.st_mode)
        or not 0 < details.st_size <= MAX_CATALOG_BYTES
    ):
        raise ComponentCatalogError("catalog file identity is invalid")
    verified = ComponentCatalogVerifier().verify(
        path.read_bytes(),
        webjam_version=webjam_version,
    )
    if verified.sequence < minimum_sequence:
        raise ComponentCatalogError(
            "catalog sequence is below the release requirement"
        )
    expected = tuple(
        entry
        for entry in official_jamulus_compatibility_registry().entries
        if entry.version == APPROVED_COMPONENT_VERSION
        and entry.role in {JamulusRole.CLIENT, JamulusRole.SERVER}
    )
    if verified.components != expected:
        raise ComponentCatalogError(
            "catalog component inventory differs from the release policy"
        )
    return verified


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify a signed WebJam Jamulus component catalog."
    )
    parser.add_argument("catalog", type=Path)
    parser.add_argument(
        "--webjam-version",
        default=__version__,
        help="exact WebJam version required by the catalog",
    )
    parser.add_argument("--minimum-sequence", type=int, default=1)
    arguments = parser.parse_args()
    verified = verify_catalog_file(
        arguments.catalog,
        webjam_version=arguments.webjam_version,
        minimum_sequence=arguments.minimum_sequence,
    )
    print(
        json.dumps(
            verified.to_snapshot_dict(),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
