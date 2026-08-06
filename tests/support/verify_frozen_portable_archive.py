"""Fail-closed preflight and extraction checks for frozen portable ZIPs."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
from pathlib import Path, PurePosixPath
import stat
import zipfile


MAX_ARCHIVE_BYTES = 2 * 1024 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 50_000
MAX_ENTRY_UNCOMPRESSED_BYTES = 2 * 1024 * 1024 * 1024
MAX_TOTAL_UNCOMPRESSED_BYTES = 8 * 1024 * 1024 * 1024
MAX_SYMLINK_TARGET_BYTES = 4 * 1024
_PLATFORM_ROOTS = {
    "windows-x64": "WebJam",
    "macos-arm64": "WebJam.app",
    "macos-x64": "WebJam.app",
}
_MACOS_TOP_LEVELS = frozenset(
    {
        "WebJam.app",
        "Install WebJam.command",
        "Install WebJam - Remove Quarantine.command",
        "READ ME FIRST.txt",
        "WebJam Candidate Info.txt",
        "Pocket Stage iPhone Setup",
    }
)
_MACOS_METADATA_ROOT = "__MACOSX"


class PortableArchiveError(ValueError):
    """The frozen portable archive or its extraction is unsafe."""


@dataclass(frozen=True)
class PortableArchiveInventory:
    """Bound paths and symlink targets from one portable archive."""

    root_name: str
    top_level_names: frozenset[str]
    non_directory_paths: frozenset[str]
    symlink_targets: dict[str, str]


def _platform_root(platform_name: str) -> str:
    try:
        return _PLATFORM_ROOTS[platform_name]
    except KeyError as exc:
        raise PortableArchiveError("Portable archive platform is invalid.") from exc


def _canonical_archive_name(name: str, *, is_directory: bool) -> str:
    if not name or "\x00" in name or "\\" in name or name.startswith("/"):
        raise PortableArchiveError("Portable archive path is invalid.")
    canonical = name[:-1] if is_directory and name.endswith("/") else name
    parsed = PurePosixPath(canonical)
    if (
        not canonical
        or parsed.is_absolute()
        or parsed.as_posix() != canonical
        or any(part in {"", ".", ".."} for part in parsed.parts)
    ):
        raise PortableArchiveError("Portable archive path is invalid.")
    return canonical


def _lexical_symlink_destination(link_name: str, target: str) -> str:
    if not target or "\x00" in target or "\\" in target or target.startswith("/"):
        raise PortableArchiveError("Portable archive symlink target is invalid.")
    parsed_target = PurePosixPath(target)
    if parsed_target.is_absolute():
        raise PortableArchiveError("Portable archive symlink target is invalid.")
    resolved_parts = list(PurePosixPath(link_name).parent.parts)
    for part in parsed_target.parts:
        if part in {"", "."}:
            continue
        if part == "..":
            if len(resolved_parts) <= 1:
                raise PortableArchiveError(
                    "Portable archive symlink escapes its bundle."
                )
            resolved_parts.pop()
            continue
        resolved_parts.append(part)
    if not resolved_parts:
        raise PortableArchiveError("Portable archive symlink target is invalid.")
    return PurePosixPath(*resolved_parts).as_posix()


def scan_portable_archive(
    archive: Path,
    *,
    platform_name: str,
) -> PortableArchiveInventory:
    """Validate one product ZIP without extracting it."""

    root_name = _platform_root(platform_name)
    expected_top_levels = (
        frozenset({root_name})
        if platform_name == "windows-x64"
        else _MACOS_TOP_LEVELS
    )
    supplied = archive.absolute()
    try:
        details = supplied.lstat()
    except OSError as exc:
        raise PortableArchiveError("Portable archive is unavailable.") from exc
    if (
        stat.S_ISLNK(details.st_mode)
        or not stat.S_ISREG(details.st_mode)
        or not 0 < details.st_size <= MAX_ARCHIVE_BYTES
    ):
        raise PortableArchiveError("Portable archive identity is invalid.")

    seen: set[str] = set()
    non_directories: set[str] = set()
    symlink_entries: list[tuple[zipfile.ZipInfo, str]] = []
    observed_top_levels: set[str] = set()
    total_size = 0
    try:
        with zipfile.ZipFile(supplied) as source:
            entries = source.infolist()
            if not entries or len(entries) > MAX_ARCHIVE_ENTRIES:
                raise PortableArchiveError("Portable archive inventory is invalid.")
            for entry in entries:
                is_directory = entry.is_dir()
                original_name = getattr(entry, "orig_filename", entry.filename)
                if original_name != entry.filename:
                    raise PortableArchiveError("Portable archive path is invalid.")
                canonical = _canonical_archive_name(
                    entry.filename,
                    is_directory=is_directory,
                )
                parsed = PurePosixPath(canonical)
                is_macos_metadata = (
                    platform_name != "windows-x64"
                    and parsed.parts[0] == _MACOS_METADATA_ROOT
                )
                if (
                    (
                        parsed.parts[0] not in expected_top_levels
                        and not is_macos_metadata
                    )
                    or (
                        platform_name == "windows-x64"
                        and len(parsed.parts) == 1
                        and not is_directory
                    )
                    or canonical in seen
                ):
                    raise PortableArchiveError("Portable archive path is invalid.")
                seen.add(canonical)
                if not is_macos_metadata:
                    observed_top_levels.add(parsed.parts[0])
                if entry.flag_bits & 0x1:
                    raise PortableArchiveError("Encrypted portable entries are invalid.")
                if entry.file_size < 0 or entry.compress_size < 0:
                    raise PortableArchiveError("Portable archive size is invalid.")
                if entry.file_size > MAX_ENTRY_UNCOMPRESSED_BYTES:
                    raise PortableArchiveError("Portable archive entry is too large.")
                total_size += entry.file_size
                if total_size > MAX_TOTAL_UNCOMPRESSED_BYTES:
                    raise PortableArchiveError("Portable archive expands too large.")

                mode = entry.external_attr >> 16
                file_type = stat.S_IFMT(mode)
                if is_macos_metadata:
                    if is_directory:
                        if file_type not in {0, stat.S_IFDIR}:
                            raise PortableArchiveError(
                                "Portable archive metadata type is invalid."
                            )
                        continue
                    if (
                        file_type not in {0, stat.S_IFREG}
                        or not parsed.name.startswith("._")
                    ):
                        raise PortableArchiveError(
                            "Portable archive metadata entry is invalid."
                        )
                    continue
                if is_directory:
                    if file_type not in {0, stat.S_IFDIR}:
                        raise PortableArchiveError(
                            "Portable archive entry type is invalid."
                        )
                    continue
                if file_type == stat.S_IFLNK:
                    if platform_name == "windows-x64":
                        raise PortableArchiveError(
                            "Windows portable archives cannot contain symlinks."
                        )
                    if not 0 < entry.file_size <= MAX_SYMLINK_TARGET_BYTES:
                        raise PortableArchiveError(
                            "Portable archive symlink target is invalid."
                        )
                    if parsed.parts[0] != root_name:
                        raise PortableArchiveError(
                            "Only the Mac app bundle may contain symlinks."
                        )
                    symlink_entries.append((entry, canonical))
                elif file_type not in {0, stat.S_IFREG}:
                    raise PortableArchiveError(
                        "Portable archive entry type is invalid."
                    )
                non_directories.add(canonical)

            if observed_top_levels != set(expected_top_levels):
                raise PortableArchiveError(
                    "Portable archive top-level inventory is not exact."
                )

            symlink_targets: dict[str, str] = {}
            for entry, canonical in symlink_entries:
                try:
                    target = source.read(entry).decode("utf-8", errors="strict")
                except (OSError, UnicodeError, RuntimeError) as exc:
                    raise PortableArchiveError(
                        "Portable archive symlink target is invalid."
                    ) from exc
                destination = _lexical_symlink_destination(canonical, target)
                if PurePosixPath(destination).parts[0] != root_name:
                    raise PortableArchiveError(
                        "Portable archive symlink escapes its bundle."
                    )
                symlink_targets[canonical] = target
    except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        raise PortableArchiveError("Portable archive is unreadable.") from exc

    return PortableArchiveInventory(
        root_name=root_name,
        top_level_names=expected_top_levels,
        non_directory_paths=frozenset(non_directories),
        symlink_targets=symlink_targets,
    )


def verify_extracted_archive(
    archive: Path,
    extracted_parent: Path,
    *,
    platform_name: str,
) -> None:
    """Require an extraction to match the scanned inventory and stay in-bundle."""

    inventory = scan_portable_archive(archive, platform_name=platform_name)
    parent = extracted_parent.absolute()
    try:
        parent_details = parent.lstat()
    except OSError as exc:
        raise PortableArchiveError("Portable extraction is unavailable.") from exc
    if stat.S_ISLNK(parent_details.st_mode) or not stat.S_ISDIR(parent_details.st_mode):
        raise PortableArchiveError("Portable extraction identity is invalid.")
    children = list(parent.iterdir())
    child_names = {child.name for child in children}
    if child_names - {_MACOS_METADATA_ROOT} != set(inventory.top_level_names):
        raise PortableArchiveError("Portable extraction root is not exact.")
    bundle_root = parent / inventory.root_name
    if bundle_root.is_symlink() or not bundle_root.is_dir():
        raise PortableArchiveError("Portable extraction bundle is invalid.")
    parent_resolved = parent.resolve(strict=True)
    bundle_resolved = bundle_root.resolve(strict=True)
    if not bundle_resolved.is_relative_to(parent_resolved):
        raise PortableArchiveError("Portable extraction escapes its destination.")

    actual_non_directories: set[str] = set()
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    for directory, directory_names, file_names in os.walk(
        parent,
        topdown=True,
        followlinks=False,
    ):
        directory_path = Path(directory)
        for name in tuple(directory_names):
            path = directory_path / name
            relative = path.relative_to(parent)
            details = path.lstat()
            is_metadata = relative.parts[0] == _MACOS_METADATA_ROOT
            if path.is_symlink():
                directory_names.remove(name)
                file_names.append(name)
                continue
            if reparse_flag and getattr(details, "st_file_attributes", 0) & reparse_flag:
                raise PortableArchiveError(
                    "Portable extraction contains a reparse point."
                )
            if not stat.S_ISDIR(details.st_mode):
                raise PortableArchiveError(
                    "Portable extraction entry type is invalid."
                )
            if not path.resolve(strict=True).is_relative_to(parent_resolved):
                raise PortableArchiveError(
                    "Portable extraction escapes its destination."
                )
            if is_metadata and platform_name == "windows-x64":
                raise PortableArchiveError("Portable extraction root is not exact.")

        for name in file_names:
            path = directory_path / name
            relative = path.relative_to(parent).as_posix()
            parsed_relative = PurePosixPath(relative)
            is_metadata = parsed_relative.parts[0] == _MACOS_METADATA_ROOT
            details = path.lstat()
            if is_metadata:
                if (
                    platform_name == "windows-x64"
                    or path.is_symlink()
                    or not stat.S_ISREG(details.st_mode)
                    or not parsed_relative.name.startswith("._")
                ):
                    raise PortableArchiveError(
                        "Portable extraction metadata entry is invalid."
                    )
                continue
            if reparse_flag and getattr(details, "st_file_attributes", 0) & reparse_flag:
                if not path.is_symlink() or platform_name == "windows-x64":
                    raise PortableArchiveError(
                        "Portable extraction contains a reparse point."
                    )
            if path.is_symlink():
                if platform_name == "windows-x64":
                    raise PortableArchiveError(
                        "Windows portable extraction contains a symlink."
                    )
                try:
                    target = os.readlink(path)
                except OSError as exc:
                    raise PortableArchiveError(
                        "Portable extraction symlink is invalid."
                    ) from exc
                if inventory.symlink_targets.get(relative) != target:
                    raise PortableArchiveError(
                        "Portable extraction symlink changed."
                    )
                destination = _lexical_symlink_destination(relative, target)
                if PurePosixPath(destination).parts[0] != inventory.root_name:
                    raise PortableArchiveError(
                        "Portable extraction symlink escapes its bundle."
                    )
                resolved = path.resolve(strict=False)
                if not resolved.is_relative_to(bundle_resolved):
                    raise PortableArchiveError(
                        "Portable extraction symlink escapes its bundle."
                    )
            elif not stat.S_ISREG(details.st_mode):
                raise PortableArchiveError(
                    "Portable extraction entry type is invalid."
                )
            elif not path.resolve(strict=True).is_relative_to(parent_resolved):
                raise PortableArchiveError(
                    "Portable extraction escapes its destination."
                )
            if (
                parsed_relative.parts[0] == inventory.root_name
                and not path.resolve(strict=False).is_relative_to(bundle_resolved)
            ):
                raise PortableArchiveError(
                    "Portable extraction app entry escapes its bundle."
                )
            actual_non_directories.add(relative)

    if actual_non_directories != set(inventory.non_directory_paths):
        raise PortableArchiveError("Portable extraction inventory changed.")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify one frozen WebJam portable ZIP before or after extraction."
    )
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument(
        "--platform",
        choices=tuple(_PLATFORM_ROOTS),
        required=True,
    )
    parser.add_argument("--extracted-parent", type=Path)
    arguments = parser.parse_args()
    try:
        if arguments.extracted_parent is None:
            scan_portable_archive(
                arguments.archive,
                platform_name=arguments.platform,
            )
        else:
            verify_extracted_archive(
                arguments.archive,
                arguments.extracted_parent,
                platform_name=arguments.platform,
            )
    except PortableArchiveError as exc:
        raise SystemExit(str(exc)) from None
    print("WebJam frozen portable archive verification passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
