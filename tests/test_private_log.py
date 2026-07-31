from __future__ import annotations

import os
from pathlib import Path
import stat

import pytest

from core.private_log import (
    PrivateLogError,
    _open_private_portable_append_text_log,
    _open_private_portable_text_log,
    open_private_append_text_log,
    open_private_text_log,
)
from core.secure_runtime import SecureRuntimeDirectory, SecureRuntimeError


def _private_directory(path: Path) -> Path:
    path.mkdir(mode=0o700)
    path.chmod(0o700)
    return path


def test_private_log_handles_spaces_and_is_mode_0600(tmp_path: Path) -> None:
    parent = _private_directory(tmp_path / "home with spaces")
    path = parent / ".webjam_jamulus.log"

    with open_private_text_log(path) as stream:
        stream.write("device diagnostic\n")

    assert path.read_text(encoding="utf-8") == "device diagnostic\n"
    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert path.stat().st_nlink == 1


@pytest.mark.skipif(os.name != "posix", reason="POSIX file mode contract")
def test_existing_regular_log_is_replaced_privately_and_truncated(
    tmp_path: Path,
) -> None:
    parent = _private_directory(tmp_path / "home")
    path = parent / ".webjam_jamulus.log"
    path.write_text("stale private diagnostic", encoding="utf-8")
    path.chmod(0o644)
    old_inode = path.stat().st_ino

    with open_private_text_log(path) as stream:
        stream.write("fresh\n")

    assert path.read_text(encoding="utf-8") == "fresh\n"
    assert path.stat().st_ino != old_inode
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.mark.skipif(os.name != "posix", reason="POSIX symlink contract")
def test_symlink_log_is_refused_without_touching_target(tmp_path: Path) -> None:
    parent = _private_directory(tmp_path / "home")
    outside = tmp_path / "outside.log"
    outside.write_text("do not truncate", encoding="utf-8")
    path = parent / ".webjam_jamulus.log"
    path.symlink_to(outside)

    with pytest.raises(PrivateLogError):
        open_private_text_log(path)

    assert path.is_symlink()
    assert outside.read_text(encoding="utf-8") == "do not truncate"


@pytest.mark.skipif(os.name != "posix", reason="POSIX hard-link contract")
def test_hard_link_log_is_refused_without_touching_target(tmp_path: Path) -> None:
    parent = _private_directory(tmp_path / "home")
    outside = tmp_path / "outside.log"
    outside.write_text("do not truncate", encoding="utf-8")
    path = parent / ".webjam_jamulus.log"
    os.link(outside, path)

    with pytest.raises(PrivateLogError):
        open_private_text_log(path)

    assert path.stat().st_ino == outside.stat().st_ino
    assert outside.read_text(encoding="utf-8") == "do not truncate"


@pytest.mark.skipif(os.name != "posix", reason="POSIX directory mode contract")
def test_group_writable_parent_is_refused(tmp_path: Path) -> None:
    parent = _private_directory(tmp_path / "unsafe-home")
    parent.chmod(0o770)
    path = parent / ".webjam_practice_server.log"

    with pytest.raises(PrivateLogError):
        open_private_text_log(path)

    assert not path.exists()


def test_portable_overwrite_reopens_published_inode_without_truncation_risk(
    tmp_path: Path,
) -> None:
    parent = _private_directory(tmp_path / "portable home")
    path = parent / ".webjam_jamulus.log"
    path.write_text("old", encoding="utf-8")

    with _open_private_portable_text_log(
        path,
        path.name,
    ) as stream:
        stream.write("new\n")

    assert path.read_text(encoding="utf-8") == "new\n"
    assert path.stat().st_nlink == 1


def test_portable_append_preserves_existing_diagnostics(tmp_path: Path) -> None:
    parent = _private_directory(tmp_path / "portable home")
    path = parent / "jamulus-server.log"
    path.write_text("old\n", encoding="utf-8")

    with _open_private_portable_append_text_log(path) as stream:
        stream.write("new\n")

    assert path.read_text(encoding="utf-8") == "old\nnew\n"


@pytest.mark.skipif(os.name != "posix", reason="POSIX dirfd contract")
def test_private_append_preserves_content_and_hardens_mode(
    tmp_path: Path,
) -> None:
    home = _private_directory(tmp_path / "home")
    log_dir = home / "Library" / "Logs" / "WebJam"
    with SecureRuntimeDirectory.open(
        home=home,
        directory=log_dir,
    ) as directory:
        path = log_dir / "jamulus-server.log"
        path.write_text("old\n", encoding="utf-8")
        path.chmod(0o644)
        with open_private_append_text_log(
            directory,
            path.name,
        ) as stream:
            stream.write("new\n")

    assert path.read_text(encoding="utf-8") == "old\nnew\n"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.mark.skipif(os.name != "posix", reason="POSIX no-follow contract")
@pytest.mark.parametrize("link_kind", ("symlink", "hardlink"))
def test_private_append_refuses_links_without_touching_target(
    tmp_path: Path,
    link_kind: str,
) -> None:
    home = _private_directory(tmp_path / "home")
    outside = tmp_path / "outside.log"
    outside.write_text("do not append", encoding="utf-8")
    log_dir = home / "Library" / "Logs" / "WebJam"
    with SecureRuntimeDirectory.open(
        home=home,
        directory=log_dir,
    ) as directory:
        path = log_dir / "jamulus-server.log"
        if link_kind == "symlink":
            path.symlink_to(outside)
        else:
            os.link(outside, path)
        with pytest.raises(PrivateLogError):
            open_private_append_text_log(directory, path.name)

    assert outside.read_text(encoding="utf-8") == "do not append"


@pytest.mark.skipif(os.name != "posix", reason="POSIX full-chain contract")
def test_hosted_log_intermediate_symlink_escape_is_refused(
    tmp_path: Path,
) -> None:
    home = _private_directory(tmp_path / "home")
    outside = _private_directory(tmp_path / "outside")
    marker = outside / "marker"
    marker.write_text("outside-data", encoding="utf-8")
    (home / "Library").symlink_to(outside, target_is_directory=True)

    with pytest.raises(SecureRuntimeError):
        SecureRuntimeDirectory.open(
            home=home,
            directory=home / "Library" / "Logs" / "WebJam",
        )

    assert marker.read_text(encoding="utf-8") == "outside-data"
    assert not (outside / "Logs").exists()
