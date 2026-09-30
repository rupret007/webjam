"""Verify a completed export and describe its exact packaged evidence."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


class ExportReceiptError(ValueError):
    pass


@dataclass(frozen=True)
class ExportReceipt:
    folder: Path
    file_count: int
    details: str


def verify_export_receipt(folder: Path) -> ExportReceipt:
    """A receipt is ready only after all packaged checksums match actual bytes."""
    root = Path(folder)
    try:
        if root.is_symlink() or not root.is_dir():
            raise ExportReceiptError("The export folder is unavailable.")
        checksum = root / "SHA256SUMS.txt"
        if not checksum.exists():
            checksum = root / "CHECKSUMS.sha256"
        if not stat.S_ISREG(checksum.lstat().st_mode) or checksum.stat().st_size > 1024 * 1024:
            raise ExportReceiptError("The export checksum list is unavailable.")
        entries = checksum.read_text(encoding="utf-8").splitlines()
        verified = set()
        metadata = {}
        for line in entries:
            match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
            if match is None:
                raise ExportReceiptError("The export checksum list is invalid.")
            digest, name = match.groups()
            relative = PurePosixPath(name)
            if (relative.is_absolute() or ".." in relative.parts or "\\" in name
                    or not relative.parts or name in verified):
                raise ExportReceiptError("The export file inventory is invalid.")
            path = root
            for part in relative.parts:
                path = path / part
                if path.is_symlink():
                    raise ExportReceiptError("The export contains a linked file.")
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode):
                raise ExportReceiptError("An exported file is unavailable.")
            keep_bytes = name in {"provenance.json", "studio-document.json", "webjam-track-export.json"}
            if keep_bytes and info.st_size > 16 * 1024 * 1024:
                raise ExportReceiptError("Export metadata is too large to inspect.")
            hasher = hashlib.sha256()
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                                 | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0))
            collected = bytearray()
            with os.fdopen(descriptor, "rb") as handle:
                opened = os.fstat(handle.fileno())
                if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                    raise ExportReceiptError("An exported file changed while verifying.")
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    hasher.update(chunk)
                    if keep_bytes:
                        collected.extend(chunk)
                        if len(collected) > 16 * 1024 * 1024:
                            raise ExportReceiptError("Export metadata is too large to inspect.")
            if hasher.hexdigest() != digest:
                raise ExportReceiptError("An exported file changed after publication.")
            verified.add(name)
            if keep_bytes:
                metadata[name] = bytes(collected)
        if not verified:
            raise ExportReceiptError("The export has no verified files.")
        edited = "provenance.json" in verified
        evidence = "provenance.json" if edited else "webjam-track-export.json"
        if evidence not in verified or (edited and "studio-document.json" not in verified):
            raise ExportReceiptError("The export source/settings evidence is missing.")
        # Limit UI parsing; audio verification remains streaming.
        def document(name):
            # Render the exact bytes hashed above, never a later path reread.
            value = json.loads(metadata[name])
            if not isinstance(value, dict):
                raise ExportReceiptError("Export metadata is invalid.")
            return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)

        detail = (
            "Export verified at completion\n"
            f"Destination: {root}\n"
            f"Files checked: {len(verified)}\n"
            "This verifies package bytes, not an external-editor import or listening test.\n\n"
            "Exact exported sources and render settings\n" + document(evidence)
        )
        if edited:
            detail += "\n\nExact Studio arrangement and mix\n" + document("studio-document.json")
        detail += "\n\nVerified file checksums\n" + "\n".join(entries)
        return ExportReceipt(root, len(verified), detail)
    except (OSError, UnicodeError, ValueError) as exc:
        raise ExportReceiptError("Export verification failed. Keep the recording and retry export.") from exc
