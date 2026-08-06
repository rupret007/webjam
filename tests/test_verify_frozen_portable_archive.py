"""Safety contracts for frozen portable ZIP preflight and extraction checks."""

from __future__ import annotations

from pathlib import Path
import stat
import warnings
import zipfile

import pytest

from tests.support import verify_frozen_portable_archive as portable


def _regular_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name)
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | 0o644) << 16
    return info


def _symlink_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name)
    info.create_system = 3
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    return info


def _write_windows_archive(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(_regular_info("WebJam/WebJam.exe"), b"exe")
        archive.writestr(_regular_info("WebJam/_internal/data.txt"), b"data")


def _write_macos_archive(path: Path, *, target: str = "MacOS/WebJam") -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            _regular_info("WebJam.app/Contents/MacOS/WebJam"),
            b"binary",
        )
        archive.writestr(
            _symlink_info("WebJam.app/Contents/Current"),
            target.encode("utf-8"),
        )
        archive.writestr(_regular_info("Install WebJam.command"), b"installer")
        archive.writestr(
            _regular_info("Install WebJam - Remove Quarantine.command"),
            b"advanced",
        )
        archive.writestr(_regular_info("READ ME FIRST.txt"), b"read me")
        archive.writestr(
            _regular_info("WebJam Candidate Info.txt"),
            b"version=0.22.5",
        )
        archive.writestr(
            _regular_info(
                "Pocket Stage iPhone Setup/Open Pocket Stage in Xcode.command"
            ),
            b"open",
        )
        archive.writestr(
            _regular_info("__MACOSX/._READ ME FIRST.txt"),
            b"apple-double",
        )


def _write_macos_extraction(extracted: Path, *, link_target: str | Path) -> None:
    binary = extracted / "WebJam.app" / "Contents" / "MacOS" / "WebJam"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"binary")
    (extracted / "WebJam.app" / "Contents" / "Current").symlink_to(link_target)
    (extracted / "Install WebJam.command").write_bytes(b"installer")
    (extracted / "Install WebJam - Remove Quarantine.command").write_bytes(
        b"advanced"
    )
    (extracted / "READ ME FIRST.txt").write_bytes(b"read me")
    (extracted / "WebJam Candidate Info.txt").write_bytes(b"version=0.22.5")
    pocket_opener = (
        extracted
        / "Pocket Stage iPhone Setup"
        / "Open Pocket Stage in Xcode.command"
    )
    pocket_opener.parent.mkdir()
    pocket_opener.write_bytes(b"open")


def test_windows_archive_and_exact_extraction_pass(tmp_path: Path) -> None:
    archive_path = tmp_path / "WebJam-windows-x64.zip"
    _write_windows_archive(archive_path)
    inventory = portable.scan_portable_archive(
        archive_path,
        platform_name="windows-x64",
    )
    assert inventory.root_name == "WebJam"
    assert inventory.symlink_targets == {}

    extracted = tmp_path / "extracted"
    extracted.mkdir()
    with zipfile.ZipFile(archive_path) as archive:
        archive.extractall(extracted)
    portable.verify_extracted_archive(
        archive_path,
        extracted,
        platform_name="windows-x64",
    )


def test_macos_in_bundle_relative_symlink_and_extraction_pass(tmp_path: Path) -> None:
    archive_path = tmp_path / "WebJam-macos-arm64.zip"
    _write_macos_archive(archive_path)
    inventory = portable.scan_portable_archive(
        archive_path,
        platform_name="macos-arm64",
    )
    assert inventory.symlink_targets == {
        "WebJam.app/Contents/Current": "MacOS/WebJam"
    }
    assert inventory.top_level_names == {
        "WebJam.app",
        "Install WebJam.command",
        "Install WebJam - Remove Quarantine.command",
        "READ ME FIRST.txt",
        "WebJam Candidate Info.txt",
        "Pocket Stage iPhone Setup",
    }

    extracted = tmp_path / "extracted"
    extracted.mkdir()
    _write_macos_extraction(extracted, link_target="MacOS/WebJam")
    portable.verify_extracted_archive(
        archive_path,
        extracted,
        platform_name="macos-arm64",
    )


@pytest.mark.parametrize(
    "name",
    (
        "/WebJam/WebJam.exe",
        "WebJam/../escape",
        "WebJam//WebJam.exe",
        "WebJam\\WebJam.exe",
        "WebJam/./WebJam.exe",
        "WebJam/WebJam.exe\x00hidden",
    ),
)
def test_invalid_archive_paths_are_rejected(name: str) -> None:
    with pytest.raises(portable.PortableArchiveError):
        portable._canonical_archive_name(name, is_directory=False)


def test_wrong_root_and_root_file_are_rejected(tmp_path: Path) -> None:
    for name in ("Other/WebJam.exe", "WebJam"):
        archive_path = tmp_path / f"{len(name)}.zip"
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr(_regular_info(name), b"bad")
        with pytest.raises(portable.PortableArchiveError):
            portable.scan_portable_archive(
                archive_path,
                platform_name="windows-x64",
            )


def test_duplicate_member_is_rejected(tmp_path: Path) -> None:
    archive_path = tmp_path / "duplicate.zip"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr(_regular_info("WebJam/WebJam.exe"), b"one")
            archive.writestr(_regular_info("WebJam/WebJam.exe"), b"two")
    with pytest.raises(portable.PortableArchiveError):
        portable.scan_portable_archive(
            archive_path,
            platform_name="windows-x64",
        )


def test_windows_symlink_and_special_entry_are_rejected(tmp_path: Path) -> None:
    symlink_archive = tmp_path / "symlink.zip"
    with zipfile.ZipFile(symlink_archive, "w") as archive:
        archive.writestr(_symlink_info("WebJam/link"), b"WebJam.exe")
    with pytest.raises(portable.PortableArchiveError):
        portable.scan_portable_archive(
            symlink_archive,
            platform_name="windows-x64",
        )

    special_archive = tmp_path / "special.zip"
    special = zipfile.ZipInfo("WebJam/fifo")
    special.create_system = 3
    special.external_attr = (stat.S_IFIFO | 0o600) << 16
    with zipfile.ZipFile(special_archive, "w") as archive:
        archive.writestr(special, b"x")
    with pytest.raises(portable.PortableArchiveError):
        portable.scan_portable_archive(
            special_archive,
            platform_name="windows-x64",
        )


@pytest.mark.parametrize("target", ("/tmp/outside", "../../../outside", "..\\bad"))
def test_macos_escaping_symlink_is_rejected(tmp_path: Path, target: str) -> None:
    archive_path = tmp_path / "bad-link.zip"
    _write_macos_archive(archive_path, target=target)
    with pytest.raises(portable.PortableArchiveError):
        portable.scan_portable_archive(
            archive_path,
            platform_name="macos-x64",
        )


def test_entry_count_and_expanded_size_are_bounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive_path = tmp_path / "bounded.zip"
    _write_windows_archive(archive_path)
    monkeypatch.setattr(portable, "MAX_ARCHIVE_ENTRIES", 1)
    with pytest.raises(portable.PortableArchiveError):
        portable.scan_portable_archive(
            archive_path,
            platform_name="windows-x64",
        )
    monkeypatch.setattr(portable, "MAX_ARCHIVE_ENTRIES", 50_000)
    monkeypatch.setattr(portable, "MAX_TOTAL_UNCOMPRESSED_BYTES", 1)
    with pytest.raises(portable.PortableArchiveError):
        portable.scan_portable_archive(
            archive_path,
            platform_name="windows-x64",
        )


def test_post_extraction_rejects_extra_file_and_outside_symlink(
    tmp_path: Path,
) -> None:
    windows_archive = tmp_path / "windows.zip"
    _write_windows_archive(windows_archive)
    windows_extract = tmp_path / "windows-extract"
    windows_extract.mkdir()
    with zipfile.ZipFile(windows_archive) as archive:
        archive.extractall(windows_extract)
    (windows_extract / "WebJam" / "extra.txt").write_text("extra", encoding="utf-8")
    with pytest.raises(portable.PortableArchiveError):
        portable.verify_extracted_archive(
            windows_archive,
            windows_extract,
            platform_name="windows-x64",
        )

    mac_archive = tmp_path / "mac.zip"
    _write_macos_archive(mac_archive)
    mac_extract = tmp_path / "mac-extract"
    mac_extract.mkdir()
    outside = tmp_path / "outside"
    outside.write_text("outside", encoding="utf-8")
    _write_macos_extraction(mac_extract, link_target=outside)
    with pytest.raises(portable.PortableArchiveError):
        portable.verify_extracted_archive(
            mac_archive,
            mac_extract,
            platform_name="macos-arm64",
        )
