"""Run the exact frozen WebJam Jamulus-catalog probe with a hard deadline."""

from __future__ import annotations

import argparse
from contextlib import ExitStack
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tempfile
import zipfile


SUCCESS_MARKER = "WebJam Jamulus catalog frozen-runtime smoke passed"
EXPECTED_COMPONENT_COUNT = 8
MAX_ARCHIVE_ENTRIES = 50_000
MAX_ARCHIVE_UNCOMPRESSED_BYTES = 4 * 1024 * 1024 * 1024
MAX_RESULT_BYTES = 16 * 1024
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _extract_webjam_archive(archive: Path, destination: Path) -> Path:
    """Safely extract one verified Linux package and return its app binary."""

    supplied_archive = archive.absolute()
    if supplied_archive.is_symlink():
        raise ValueError("Frozen WebJam archive identity is invalid.")
    archive = supplied_archive.resolve()
    if not archive.is_file():
        raise ValueError("Frozen WebJam archive is missing.")
    destination.mkdir(mode=0o700, parents=True, exist_ok=False)
    seen: set[str] = set()
    total_size = 0
    with zipfile.ZipFile(archive) as source:
        entries = source.infolist()
        if not entries or len(entries) > MAX_ARCHIVE_ENTRIES:
            raise ValueError("Frozen WebJam archive inventory is invalid.")
        for entry in entries:
            name = entry.filename
            if not name or "\x00" in name or "\\" in name or name.startswith("/"):
                raise ValueError("Frozen WebJam archive path is invalid.")
            is_directory = name.endswith("/")
            canonical_name = name[:-1] if is_directory else name
            parsed = PurePosixPath(canonical_name)
            if (
                not canonical_name
                or parsed.is_absolute()
                or parsed.as_posix() != canonical_name
                or any(part in {"", ".", ".."} for part in parsed.parts)
                or parsed.parts[0] != "WebJam"
                or canonical_name in seen
            ):
                raise ValueError("Frozen WebJam archive path is invalid.")
            seen.add(canonical_name)
            mode = entry.external_attr >> 16
            file_type = stat.S_IFMT(mode)
            if file_type not in {
                0,
                stat.S_IFDIR if is_directory else stat.S_IFREG,
            }:
                raise ValueError("Frozen WebJam archive entry type is invalid.")
            if is_directory and file_type not in {0, stat.S_IFDIR}:
                raise ValueError("Frozen WebJam archive entry type is invalid.")
            if not is_directory and file_type == stat.S_IFDIR:
                raise ValueError("Frozen WebJam archive entry type is invalid.")
            total_size += entry.file_size
            if (
                entry.file_size < 0
                or entry.compress_size < 0
                or total_size > MAX_ARCHIVE_UNCOMPRESSED_BYTES
            ):
                raise ValueError("Frozen WebJam archive size is invalid.")

        for entry in entries:
            name = entry.filename
            is_directory = name.endswith("/")
            canonical_name = name[:-1] if is_directory else name
            output = destination.joinpath(*PurePosixPath(canonical_name).parts)
            if is_directory:
                output.mkdir(mode=0o755, parents=True, exist_ok=True)
                continue
            output.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
            with source.open(entry, "r") as input_file, output.open("xb") as target:
                shutil.copyfileobj(input_file, target, length=1024 * 1024)
            mode = (entry.external_attr >> 16) & 0o777
            output.chmod(mode or 0o644)

    binary = destination / "WebJam" / "WebJam"
    if not binary.is_file() or binary.is_symlink() or not os.access(binary, os.X_OK):
        raise ValueError("Frozen WebJam archive has no executable app binary.")
    return binary


def _read_result(path: Path) -> dict[str, object]:
    try:
        details = path.lstat()
        if (
            stat.S_ISLNK(details.st_mode)
            or not stat.S_ISREG(details.st_mode)
            or not 0 < details.st_size <= MAX_RESULT_BYTES
        ):
            raise ValueError
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        raise SystemExit(
            "Frozen Jamulus catalog smoke produced no valid result."
        ) from None
    if not isinstance(value, dict):
        raise SystemExit("Frozen Jamulus catalog smoke produced no valid result.")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument("--binary", type=Path)
    input_group.add_argument("--archive", type=Path)
    parser.add_argument("--cwd", type=Path)
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--expected-sequence", type=int, required=True)
    parser.add_argument("--expected-target", required=True)
    parser.add_argument("--expected-jamulus-version", required=True)
    parser.add_argument("--expected-catalog-envelope-sha256", required=True)
    parser.add_argument("--expected-catalog-payload-sha256", required=True)
    parser.add_argument("--expected-signer-fingerprint-sha256", required=True)
    arguments = parser.parse_args()
    for value in (
        arguments.expected_catalog_envelope_sha256,
        arguments.expected_catalog_payload_sha256,
        arguments.expected_signer_fingerprint_sha256,
    ):
        if not _SHA256_RE.fullmatch(value):
            raise SystemExit("Frozen catalog smoke expected digest is invalid.")
    with ExitStack() as stack:
        if arguments.archive is not None:
            extraction_parent = Path(
                stack.enter_context(
                    tempfile.TemporaryDirectory(prefix="webjam-frozen-package-")
                )
            )
            try:
                binary = _extract_webjam_archive(
                    arguments.archive,
                    extraction_parent / "extracted",
                )
            except (OSError, ValueError, zipfile.BadZipFile):
                raise SystemExit(
                    "Frozen WebJam archive could not be safely extracted."
                ) from None
            working_directory = binary.parent
        else:
            assert arguments.binary is not None
            supplied_binary = arguments.binary.absolute()
            if (
                supplied_binary.is_symlink()
                or not supplied_binary.is_file()
                or not os.access(supplied_binary, os.X_OK)
            ):
                raise SystemExit("Frozen WebJam binary is missing or not executable.")
            binary = supplied_binary.resolve()
            working_directory = (arguments.cwd or binary.parent).resolve()

        directory = stack.enter_context(
            tempfile.TemporaryDirectory(prefix="webjam-component-catalog-smoke-")
        )
        result_path = Path(directory) / "result.json"
        runtime_home = Path(directory) / "home"
        runtime_home.mkdir(mode=0o700)
        temporary_root = str(Path(directory).parent)
        environment = {
            "HOME": str(runtime_home),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PATH": os.defpath,
            "QT_QPA_PLATFORM": "offscreen",
            "TEMP": temporary_root,
            "TMP": temporary_root,
            "TMPDIR": temporary_root,
        }
        environment["WEBJAM_SMOKE_COMPONENT_CATALOG_RUNTIME"] = "1"
        environment["WEBJAM_SMOKE_COMPONENT_CATALOG_RESULT"] = str(result_path)
        # Prove that launch-environment CA overrides cannot replace WebJam's
        # release-locked Certifi trust data.
        environment["SSL_CERT_FILE"] = str(Path(directory) / "not-trusted.pem")
        environment["SSL_CERT_DIR"] = str(Path(directory) / "not-trusted-dir")
        for key in (
            "WEBJAM_SMOKE_REFERENCE_STUDIO_RUNTIME",
            "WEBJAM_SMOKE_POCKET_STAGE_RUNTIME",
            "WEBJAM_SMOKE_LAUNCH_ONLY",
            "WEBJAM_SMOKE_AUTOSTART_AUDIO",
        ):
            environment.pop(key, None)
        try:
            completed = subprocess.run(
                [str(binary)],
                cwd=working_directory,
                env=environment,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=60,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise SystemExit(
                "Frozen Jamulus catalog smoke exceeded 60 seconds."
            ) from None
        result = _read_result(result_path)
        expected = {
            "marker": SUCCESS_MARKER,
            "status": "passed",
            "webjam_version": arguments.expected_version,
            "catalog_sequence": arguments.expected_sequence,
            "component_count": EXPECTED_COMPONENT_COUNT,
            "available_version": arguments.expected_jamulus_version,
            "target": arguments.expected_target,
            "catalog_envelope_sha256": (arguments.expected_catalog_envelope_sha256),
            "catalog_payload_sha256": (arguments.expected_catalog_payload_sha256),
            "signer_fingerprint_sha256": (arguments.expected_signer_fingerprint_sha256),
            "trust_source": "packaged-certifi",
            "trust_status": "ready",
            "environment_ca_overrides": "ignored",
            "redirect_policy": "explicit-allowlist",
        }
        mismatches = tuple(
            key for key, value in expected.items() if result.get(key) != value
        )
        exact_fields = set(result) == set(expected)
        if completed.returncode != 0 or mismatches or not exact_fields:
            mismatch_text = ",".join(mismatches)
            if not exact_fields:
                mismatch_text = ",".join(
                    value for value in (mismatch_text, "result-fields") if value
                )
            mismatch_text = mismatch_text or "process-exit"
            raise SystemExit(
                "Frozen Jamulus catalog smoke failed "
                f"(exit {completed.returncode}; checks {mismatch_text})."
            )
    print(SUCCESS_MARKER)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
