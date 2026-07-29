#!/usr/bin/env python3
"""Export and verify the approved Jamulus 3.12.3 Windows x64 runtime.

The authoritative identities live in ``core.jamulus_compatibility`` so the
signed component catalog, runtime activation, and release CI cannot drift.
This command only serializes that contract or applies the production verifier
to an already isolated x64 loadable-code tree. It never launches Jamulus.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from core.jamulus_compatibility import (
    ComponentTarget,
    JamulusCompatibility,
    JamulusRole,
    RuntimeFileIdentity,
    official_jamulus_compatibility_registry,
)
from services.jamulus_component_platform import (
    _approved_windows_runtime_inventory,
    _verify_windows_runtime_inventory,
)


APPROVED_VERSION = "3.12.3"
APPROVED_INSTALLER_SHA256 = (
    "008918b1564b2a46f1a371d7e3df661a0d710689383dab5c61b80be3c4aaf5a1"
)
EXPECTED_LOADABLE_COUNT = 27


def approved_entry(role: JamulusRole = JamulusRole.CLIENT) -> JamulusCompatibility:
    return official_jamulus_compatibility_registry().exact(
        component_id="jamulus",
        role=role,
        target=ComponentTarget.WINDOWS_X64,
        version=APPROVED_VERSION,
        variant="official",
    )


def approved_inventory() -> tuple[RuntimeFileIdentity, ...]:
    client = approved_entry(JamulusRole.CLIENT)
    server = approved_entry(JamulusRole.SERVER)
    if client.artifact.sha256 != APPROVED_INSTALLER_SHA256:
        raise RuntimeError("approved Jamulus installer identity changed")
    if client.runtime_files != server.runtime_files:
        raise RuntimeError("Jamulus client/server runtime contracts differ")
    inventory = _approved_windows_runtime_inventory(client)
    if len(inventory) != EXPECTED_LOADABLE_COUNT:
        raise RuntimeError("approved Jamulus x64 loadable inventory is incomplete")
    return inventory


def manifest_payload() -> dict[str, object]:
    return {
        "schema": 1,
        "component_id": "jamulus",
        "version": APPROVED_VERSION,
        "target": ComponentTarget.WINDOWS_X64.value,
        "installer_sha256": APPROVED_INSTALLER_SHA256,
        "loadable_count": EXPECTED_LOADABLE_COUNT,
        "files": [item.to_dict() for item in approved_inventory()],
    }


def export_manifest(output: Path) -> None:
    if not output.is_absolute():
        raise ValueError("manifest output path must be absolute")
    if output.exists() or output.is_symlink():
        raise ValueError("manifest output path must not already exist")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            manifest_payload(),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )


def verify_isolated_tree(root: Path) -> None:
    if not root.is_absolute():
        raise ValueError("runtime tree path must be absolute")
    inventory = approved_inventory()
    expected = frozenset(item.relative_path.casefold() for item in inventory)
    observed = frozenset(
        path.relative_to(root).as_posix().casefold()
        for path in root.rglob("*")
        if path.is_file()
    )
    if observed != expected:
        raise RuntimeError("isolated Jamulus x64 runtime inventory is not exact")
    executable = root.joinpath(*approved_entry().executable_relative_path.split("/"))
    _verify_windows_runtime_inventory(
        root,
        executable=executable,
        inventory=inventory,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export or verify the approved Jamulus Windows x64 runtime."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    export_parser = subparsers.add_parser("export")
    export_parser.add_argument("--output", type=Path, required=True)
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("root", type=Path)
    arguments = parser.parse_args()
    if arguments.command == "export":
        export_manifest(arguments.output)
    else:
        verify_isolated_tree(arguments.root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
