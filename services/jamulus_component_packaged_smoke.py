"""Exact frozen-runtime proof for the public Jamulus component catalog.

This is reachable only through WebJam's explicit frozen smoke hook. It accepts
no URL, key, or trust-store input: the probe exercises the same fixed catalog,
embedded Ed25519 key, Certifi trust data, redirect policy, and exact WebJam
version used by the musician-facing updater.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile

from core.component_catalog import CatalogSequenceStore, ComponentCatalogVerifier
from core.component_download import DownloadCancellation
from core.jamulus_compatibility import ComponentTarget, JamulusRole
from services.jamulus_component_platform import platform_component_target
from services.jamulus_component_update import (
    DEFAULT_COMPONENT_CATALOG_URL,
    SignedCatalogFetcher,
)
from webjam_qt import __version__


SUCCESS_MARKER = "WebJam Jamulus catalog frozen-runtime smoke passed"
EXPECTED_COMPONENT_COUNT = 8
EXPECTED_JAMULUS_VERSION = "3.12.3"


def _validated_result_path(result_path: Path) -> Path:
    supplied_path = result_path.absolute()
    temporary_root = Path(tempfile.gettempdir()).resolve()
    supplied_parent = supplied_path.parent
    try:
        parent_details = supplied_parent.lstat()
    except OSError as exc:
        raise RuntimeError("Jamulus catalog smoke result path is invalid.") from exc
    if supplied_path.is_symlink() or stat.S_ISLNK(parent_details.st_mode):
        raise RuntimeError("Jamulus catalog smoke result path is invalid.")
    try:
        parent = supplied_parent.resolve(strict=True)
    except OSError as exc:
        raise RuntimeError("Jamulus catalog smoke result path is invalid.") from exc
    path = parent / supplied_path.name
    if (
        parent.parent != temporary_root
        or not parent.name.startswith("webjam-component-catalog-smoke-")
        or not stat.S_ISDIR(parent_details.st_mode)
        or path.name != "result.json"
        or path.exists()
    ):
        raise RuntimeError("Jamulus catalog smoke result path is invalid.")
    return path


def _write_success_result(result_path: Path, payload: dict[str, object]) -> None:
    path = _validated_result_path(result_path)
    encoded = (
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(encoded)
            output.flush()
            os.fsync(output.fileno())
    except Exception:
        try:
            path.unlink()
        except OSError:
            pass
        raise


def run_frozen_component_catalog_smoke(*, result_path: Path) -> int:
    """Fetch and fully authorize the live catalog from the frozen executable."""

    result_path = _validated_result_path(result_path)
    fetcher = SignedCatalogFetcher()
    envelope = fetcher.fetch(
        DEFAULT_COMPONENT_CATALOG_URL,
        cancellation=DownloadCancellation(),
    )
    sequence_store = CatalogSequenceStore(result_path.parent / "catalog-sequence.json")
    catalog = ComponentCatalogVerifier(sequence_store=sequence_store).verify(
        envelope,
        webjam_version=__version__,
    )
    if len(catalog.components) != EXPECTED_COMPONENT_COUNT:
        raise RuntimeError("Catalog smoke found an unexpected component inventory.")
    if {component.version for component in catalog.components} != {
        EXPECTED_JAMULUS_VERSION
    }:
        raise RuntimeError("Catalog smoke found an unexpected Jamulus version.")
    if {component.role for component in catalog.components} != {
        JamulusRole.CLIENT,
        JamulusRole.SERVER,
    }:
        raise RuntimeError("Catalog smoke found an unexpected Jamulus role.")
    target = platform_component_target()
    clients = catalog.registry.compatible(
        role=JamulusRole.CLIENT,
        target=target,
        webjam_version=__version__,
        required_capabilities={"audio-client", "json-rpc-client"},
    )
    if not clients:
        raise RuntimeError("Catalog smoke found no compatible Jamulus client.")
    client = clients[0]
    server = catalog.registry.exact(
        component_id=client.component_id,
        role=JamulusRole.SERVER,
        target=target,
        version=client.version,
        variant=client.variant,
    )
    if client.artifact != server.artifact:
        raise RuntimeError("Catalog smoke client/server package identity differs.")
    if target in {
        ComponentTarget.MACOS_ARM64,
        ComponentTarget.MACOS_X64,
    } and (
        client.capabilities.includes({"webjam-route-profile"})
        or server.capabilities.includes({"recording"})
    ):
        raise RuntimeError(
            "Catalog smoke found unsafe macOS runtime-file capability claims."
        )
    transport = fetcher.security_diagnostics()
    if transport.get("trust_status") != "ready":
        raise RuntimeError("Catalog smoke did not establish packaged TLS trust.")
    _write_success_result(
        result_path,
        {
            "marker": SUCCESS_MARKER,
            "status": "passed",
            "webjam_version": __version__,
            "target": target.value,
            "catalog_sequence": catalog.sequence,
            "component_count": len(catalog.components),
            "available_version": client.version,
            "catalog_envelope_sha256": hashlib.sha256(envelope).hexdigest(),
            "catalog_payload_sha256": catalog.payload_sha256,
            "signer_fingerprint_sha256": (catalog.signer_fingerprint_sha256),
            "trust_source": transport.get("trust_source", ""),
            "trust_status": transport.get("trust_status", ""),
            "environment_ca_overrides": transport.get(
                "environment_ca_overrides",
                "",
            ),
            "redirect_policy": transport.get("redirect_policy", ""),
        },
    )
    return 0


__all__ = [
    "EXPECTED_COMPONENT_COUNT",
    "EXPECTED_JAMULUS_VERSION",
    "SUCCESS_MARKER",
    "_validated_result_path",
    "run_frozen_component_catalog_smoke",
]
