#!/usr/bin/env python3
"""Verify and extract the native extension from WebJam's Intel crypto wheel."""

from __future__ import annotations

import argparse
import base64
import csv
from email.parser import BytesParser
from email.policy import default
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import zipfile


_WHEEL_NAME = re.compile(
    r"^cryptography-50\.0\.0-(?P<python>cp[0-9]+)-"
    r"(?P<abi>abi3)-(?P<platform>macosx_[0-9_]+_x86_64)\.whl$"
)
_TAG = re.compile(r"^cp[0-9]+-abi3-macosx_[0-9_]+_x86_64$")
_MAX_FILE_SIZE = 128 * 1024 * 1024
_MAX_TOTAL_SIZE = 256 * 1024 * 1024


def _safe_member(info: zipfile.ZipInfo) -> None:
    name = info.filename
    path = PurePosixPath(name)
    if (
        not name
        or "\\" in name
        or "\x00" in name
        or path.is_absolute()
        or ".." in path.parts
    ):
        raise ValueError("wheel contains an unsafe member path")
    mode = (info.external_attr >> 16) & 0o170000
    if mode == stat.S_IFLNK:
        raise ValueError("wheel contains a symbolic link")
    if info.flag_bits & 0x1:
        raise ValueError("wheel contains an encrypted member")
    if info.file_size > _MAX_FILE_SIZE:
        raise ValueError("wheel member exceeds the size limit")


def _record_digest(data: bytes) -> str:
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest())
    return "sha256=" + digest.rstrip(b"=").decode("ascii")


def verify(wheel: Path, extracted_extension: Path) -> dict[str, object]:
    if not wheel.is_file() or wheel.is_symlink():
        raise ValueError("wheel is missing or unsafe")
    match = _WHEEL_NAME.fullmatch(wheel.name)
    if match is None or "arm64" in wheel.name or "universal2" in wheel.name:
        raise ValueError("wheel filename is not an Intel-only cryptography 50 wheel")
    if extracted_extension.exists() or extracted_extension.is_symlink():
        raise ValueError("refusing to replace an extracted extension")
    if not extracted_extension.parent.is_dir() or extracted_extension.parent.is_symlink():
        raise ValueError("extension output parent is missing or unsafe")

    try:
        archive = zipfile.ZipFile(wheel)
    except (OSError, zipfile.BadZipFile) as exc:
        raise ValueError("wheel is not a readable ZIP archive") from exc
    with archive:
        infos = archive.infolist()
        names = [info.filename for info in infos]
        if len(names) != len(set(names)):
            raise ValueError("wheel contains duplicate member names")
        for info in infos:
            _safe_member(info)
        if sum(info.file_size for info in infos) > _MAX_TOTAL_SIZE:
            raise ValueError("wheel exceeds the total size limit")
        if archive.testzip() is not None:
            raise ValueError("wheel member CRC validation failed")

        files = {info.filename for info in infos if not info.is_dir()}
        dist_info = "cryptography-50.0.0.dist-info"
        metadata_name = f"{dist_info}/METADATA"
        wheel_name = f"{dist_info}/WHEEL"
        record_name = f"{dist_info}/RECORD"
        licenses = {
            f"{dist_info}/licenses/LICENSE",
            f"{dist_info}/licenses/LICENSE.APACHE",
            f"{dist_info}/licenses/LICENSE.BSD",
        }
        required = {metadata_name, wheel_name, record_name, *licenses}
        if not required.issubset(files):
            raise ValueError("wheel metadata or license inventory is incomplete")

        metadata = BytesParser(policy=default).parsebytes(archive.read(metadata_name))
        if metadata.get("Name") != "cryptography":
            raise ValueError("wheel METADATA has the wrong project name")
        if metadata.get("Version") != "50.0.0":
            raise ValueError("wheel METADATA has the wrong version")

        wheel_metadata = BytesParser(policy=default).parsebytes(archive.read(wheel_name))
        if wheel_metadata.get("Root-Is-Purelib", "").lower() != "false":
            raise ValueError("cryptography wheel unexpectedly claims to be pure Python")
        tags = wheel_metadata.get_all("Tag", [])
        expected_tag = "-".join(
            (match.group("python"), match.group("abi"), match.group("platform"))
        )
        if tags != [expected_tag] or _TAG.fullmatch(expected_tag) is None:
            raise ValueError("wheel has an ambiguous or non-Intel compatibility tag")

        extensions = sorted(
            name
            for name in files
            if PurePosixPath(name).parent
            == PurePosixPath("cryptography/hazmat/bindings")
            and PurePosixPath(name).name.startswith("_rust")
            and name.endswith(".so")
        )
        if len(extensions) != 1:
            raise ValueError("wheel must contain exactly one Rust extension")
        if any(name.endswith(".dylib") for name in files):
            raise ValueError("wheel unexpectedly contains a dynamic library")

        record_rows = list(
            csv.reader(io.StringIO(archive.read(record_name).decode("utf-8")))
        )
        if any(len(row) != 3 for row in record_rows):
            raise ValueError("wheel RECORD contains a malformed row")
        records = {row[0]: (row[1], row[2]) for row in record_rows}
        if len(records) != len(record_rows) or set(records) != files:
            raise ValueError("wheel RECORD does not exactly cover its files")
        for name in sorted(files):
            recorded_hash, recorded_size = records[name]
            if name == record_name:
                if recorded_hash or recorded_size:
                    raise ValueError("wheel RECORD must leave its own digest empty")
                continue
            data = archive.read(name)
            if recorded_hash != _record_digest(data):
                raise ValueError("wheel RECORD digest validation failed")
            if recorded_size != str(len(data)):
                raise ValueError("wheel RECORD size validation failed")

        extension_name = extensions[0]
        extension_data = archive.read(extension_name)
        descriptor = os.open(
            extracted_extension,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(extension_data)
                handle.flush()
                os.fsync(handle.fileno())
        except BaseException:
            extracted_extension.unlink(missing_ok=True)
            raise

    return {
        "extension": extension_name,
        "extension_sha256": hashlib.sha256(extension_data).hexdigest(),
        "tag": expected_tag,
        "version": "50.0.0",
        "wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("wheel", type=Path)
    parser.add_argument("extracted_extension", type=Path)
    args = parser.parse_args(argv)
    try:
        result = verify(args.wheel, args.extracted_extension)
    except (OSError, UnicodeError, ValueError, zipfile.BadZipFile) as exc:
        parser.error(str(exc))
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
