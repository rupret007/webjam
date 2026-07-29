#!/usr/bin/env python3
"""Create fail-closed evidence for an unapproved managed HEADLESS build.

This helper records the exact CI-produced container and app-tree bytes.  Its
output is deliberately *not* an activating component-catalog entry: a separate
reviewed signing boundary must consume the evidence after the legal gate is
resolved.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import tempfile


VERSION = "3.12.3"
PROFILE = "r3_12_3"
SOURCE_COMMIT = "74dc422116983a2173eb917cb4d6a403886b31e5"
APP_NAME = "JamulusHeadlessClient.app"
EXECUTABLE = f"{APP_NAME}/Contents/MacOS/JamulusHeadlessClient"
TARGET_ARCHITECTURES = {
    "macos-arm64": "arm64",
    "macos-x64": "x86_64",
}
PROVENANCE_KEYS = frozenset(
    {
        "format",
        "component",
        "version",
        "profile",
        "source_repository",
        "source_commit",
        "source_tag",
        "source_tree",
        "source_archive_commit",
        "corresponding_source_sha256",
        "patch_sha256",
        "license_sha256",
        "qt_version",
        "qt_source_archive_sha256",
        "aqtinstall_version",
        "architecture",
        "deployment_target",
        "apple_clang_version",
        "macos_sdk_version",
        "build_mode",
        "server_only",
    }
)


class EvidenceError(ValueError):
    """The candidate cannot be represented as safe evidence."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative(value: str, *, label: str) -> str:
    if not value or "\\" in value or "\x00" in value:
        raise EvidenceError(f"{label} is not a canonical relative path")
    parsed = PurePosixPath(value)
    if parsed.is_absolute() or any(part in {"", ".", ".."} for part in parsed.parts):
        raise EvidenceError(f"{label} is not a safe relative path")
    if parsed.as_posix() != value:
        raise EvidenceError(f"{label} is not canonical")
    return value


def _provenance(path: Path) -> dict[str, str]:
    if not path.is_file() or path.is_symlink():
        raise EvidenceError("provenance is missing or unsafe")
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if not separator or not key or key in result or "\x00" in value:
            raise EvidenceError("provenance is malformed or duplicated")
        result[key] = value
    if frozenset(result) != PROVENANCE_KEYS:
        raise EvidenceError("provenance schema does not exactly match the reviewed build")
    required = {
        "format": "1",
        "component": "JamulusHeadlessClient",
        "version": VERSION,
        "profile": PROFILE,
        "source_repository": "https://github.com/jamulussoftware/jamulus.git",
        "source_commit": SOURCE_COMMIT,
        "source_tag": PROFILE,
        "qt_version": "6.10.2",
        "aqtinstall_version": "3.3.0",
        "deployment_target": "13.0",
        "build_mode": "headless-client",
        "server_only": "false",
    }
    if any(result[key] != value for key, value in required.items()):
        raise EvidenceError("provenance identity does not match r3_12_3")
    for key in (
        "corresponding_source_sha256",
        "patch_sha256",
        "license_sha256",
        "qt_source_archive_sha256",
    ):
        if len(result[key]) != 64 or any(c not in "0123456789abcdef" for c in result[key]):
            raise EvidenceError(f"provenance {key} is not a SHA-256")
    return result


def _tree_inventory(app: Path) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    root_parent = app.parent
    for current, directory_names, file_names in os.walk(
        app, topdown=True, followlinks=False
    ):
        current_path = Path(current)
        retained_directories: list[str] = []
        for name in sorted(directory_names):
            path = current_path / name
            relative = _safe_relative(
                path.relative_to(root_parent).as_posix(), label="tree path"
            )
            if path.is_symlink():
                target = os.readlink(path)
                _safe_relative(target, label=f"symlink target for {relative}")
                entries.append(
                    {"kind": "symlink", "path": relative, "target": target}
                )
            else:
                retained_directories.append(name)
        directory_names[:] = retained_directories
        for name in sorted(file_names):
            path = current_path / name
            relative = _safe_relative(
                path.relative_to(root_parent).as_posix(), label="tree path"
            )
            metadata = path.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                target = os.readlink(path)
                _safe_relative(target, label=f"symlink target for {relative}")
                entries.append(
                    {"kind": "symlink", "path": relative, "target": target}
                )
            elif stat.S_ISREG(metadata.st_mode):
                entries.append(
                    {
                        "kind": "file",
                        "path": relative,
                        "size": metadata.st_size,
                        "sha256": _sha256(path),
                        "mode": stat.S_IMODE(metadata.st_mode),
                    }
                )
            else:
                raise EvidenceError(f"unsupported filesystem entry: {relative}")
    return sorted(entries, key=lambda item: (str(item["path"]), str(item["kind"])))


def create_evidence(
    *,
    archive: Path,
    app: Path,
    manifest: Path,
    target: str,
) -> dict[str, object]:
    if target not in TARGET_ARCHITECTURES:
        raise EvidenceError("target must be macos-arm64 or macos-x64")
    architecture = TARGET_ARCHITECTURES[target]
    expected_archive = (
        f"JamulusHeadlessClient-{PROFILE}-{target}-UNAPPROVED-EVIDENCE.zip"
    )
    if archive.name != expected_archive or not archive.is_file() or archive.is_symlink():
        raise EvidenceError("archive identity is missing or unsafe")
    if app.name != APP_NAME or not app.is_dir() or app.is_symlink():
        raise EvidenceError("app bundle identity is missing or unsafe")
    if not manifest.is_file() or manifest.is_symlink():
        raise EvidenceError("binary checksum manifest is missing or unsafe")
    provenance_path = (
        app
        / "Contents"
        / "Resources"
        / "THIRD_PARTY_LICENSES"
        / "JamulusHeadlessClient-PROVENANCE.txt"
    )
    provenance = _provenance(provenance_path)
    if provenance["architecture"] != architecture:
        raise EvidenceError("provenance architecture does not match target")

    binary = app / "Contents" / "MacOS" / "JamulusHeadlessClient"
    if not binary.is_file() or binary.is_symlink():
        raise EvidenceError("HEADLESS executable is missing or unsafe")
    manifest_parts = manifest.read_text(encoding="utf-8").strip().split()
    if manifest_parts != [_sha256(binary), EXECUTABLE]:
        raise EvidenceError("binary checksum manifest does not match the app")

    license_root = app / "Contents" / "Resources" / "THIRD_PARTY_LICENSES"
    exact_materials = {
        "license_sha256": license_root / "JAMULUS_COPYING.txt",
        "patch_sha256": license_root / "jamulus-headless-r3_12_3.patch",
        "corresponding_source_sha256": (
            license_root / "JamulusHeadlessClient-CORRESPONDING-SOURCE.tar.gz"
        ),
        "qt_source_archive_sha256": (
            license_root / "qtbase-everywhere-src-6.10.2.tar.xz"
        ),
    }
    for key, path in exact_materials.items():
        if not path.is_file() or path.is_symlink() or _sha256(path) != provenance[key]:
            raise EvidenceError(f"{key} material does not match provenance")

    inventory = _tree_inventory(app)
    canonical_inventory = json.dumps(
        inventory,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "schema": 1,
        "component": "JamulusHeadlessClient",
        "version": VERSION,
        "profile": PROFILE,
        "target": target,
        "architecture": architecture,
        "archive": archive.name,
        "archive_size": archive.stat().st_size,
        "archive_sha256": _sha256(archive),
        "source_commit": SOURCE_COMMIT,
        "patch_sha256": provenance["patch_sha256"],
        "license_sha256": provenance["license_sha256"],
        "corresponding_source_sha256": provenance[
            "corresponding_source_sha256"
        ],
        "runtime_inventory_sha256": hashlib.sha256(canonical_inventory).hexdigest(),
        "runtime_inventory": inventory,
        "activation_approved": False,
        "catalog_injection_required": True,
        "catalog_signing_automatic": False,
        "desktop_release_inventory": False,
        "legal_gate": "pending-qualified-agpl-13-review-or-protocol-visible-source-offer",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--app", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--target", choices=tuple(TARGET_ARCHITECTURES), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or args.output.is_symlink():
        raise SystemExit("refusing to replace existing evidence")
    if not args.output.parent.is_dir() or args.output.parent.is_symlink():
        raise SystemExit("evidence output parent is missing or unsafe")
    evidence = create_evidence(
        archive=args.archive,
        app=args.app,
        manifest=args.manifest,
        target=args.target,
    )
    payload = (
        json.dumps(
            evidence,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    )
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{args.output.name}.", dir=args.output.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, args.output)
    finally:
        temporary.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
