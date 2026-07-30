from __future__ import annotations

import os
from pathlib import Path
import stat
import traceback

import pytest

from core.secure_runtime import SecureRuntimeDirectory, SecureRuntimeError


def test_private_runtime_with_spaces_writes_owned_secret_and_directory(
    tmp_path: Path,
) -> None:
    home = tmp_path / "Home With Spaces"
    home.mkdir()
    runtime_path = (
        home / "Library" / "Application Support" / "WebJam" / "JamulusClient"
    )

    with SecureRuntimeDirectory.open(
        home=home,
        directory=runtime_path,
    ) as runtime:
        recordings = runtime.ensure_child_directory("Recordings")
        secret = runtime.write_private_file(
            "webjam_client_rpc.secret",
            b"private-secret\n",
        )
        assert runtime.path_matches()
        assert recordings.matches()
        assert secret.matches()

    assert secret.path.read_bytes() == b"private-secret\n"
    assert stat.S_IMODE(runtime_path.stat().st_mode) == 0o700
    assert stat.S_IMODE(recordings.path.stat().st_mode) == 0o700
    assert stat.S_IMODE(secret.path.stat().st_mode) == 0o600


def test_intermediate_symlink_is_rejected_without_touching_outside(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    library = home / "Library"
    library.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o755)
    original_mode = stat.S_IMODE(outside.stat().st_mode)
    (library / "Application Support").symlink_to(
        outside,
        target_is_directory=True,
    )

    with pytest.raises(SecureRuntimeError):
        SecureRuntimeDirectory.open(
            home=home,
            directory=(
                home
                / "Library"
                / "Application Support"
                / "WebJam"
                / "runtime"
            ),
        )

    assert stat.S_IMODE(outside.stat().st_mode) == original_mode
    assert not (outside / "WebJam").exists()


def test_secret_symlink_is_rejected_without_chmod_or_overwrite(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    runtime_path = home / "Library" / "Application Support" / "WebJam"
    outside = tmp_path / "outside.secret"
    outside.write_bytes(b"do-not-touch\n")
    outside.chmod(0o644)

    with SecureRuntimeDirectory.open(
        home=home,
        directory=runtime_path,
    ) as runtime:
        (runtime.path / "rpc.secret").symlink_to(outside)
        with pytest.raises(SecureRuntimeError):
            runtime.write_private_file("rpc.secret", b"replacement\n")

    assert outside.read_bytes() == b"do-not-touch\n"
    assert stat.S_IMODE(outside.stat().st_mode) == 0o644


def test_path_proofs_fail_after_directory_or_leaf_replacement(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    runtime_path = home / "Library" / "Application Support" / "WebJam"

    with SecureRuntimeDirectory.open(
        home=home,
        directory=runtime_path,
    ) as runtime:
        secret = runtime.write_private_file("rpc.secret", b"first\n")
        secret.path.rename(secret.path.with_suffix(".old"))
        secret.path.write_bytes(b"second\n")
        assert not secret.matches()

        replacement = runtime.path.with_name("WebJam.replacement")
        runtime.path.rename(replacement)
        runtime.path.mkdir(mode=0o700)
        assert not runtime.path_matches()


@pytest.mark.skipif(os.name != "posix", reason="POSIX ownership contract")
def test_existing_runtime_owned_by_another_uid_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    runtime_path = home / "runtime"
    runtime_path.mkdir()
    real_fstat = os.fstat

    def foreign_owner(descriptor: int):
        details = real_fstat(descriptor)
        if int(details.st_ino) == int(runtime_path.stat().st_ino):
            values = list(details)
            values[4] = int(details.st_uid) + 1
            return os.stat_result(values)
        return details

    monkeypatch.setattr(os, "fstat", foreign_owner)
    with pytest.raises(SecureRuntimeError):
        SecureRuntimeDirectory.open(home=home, directory=runtime_path)


@pytest.mark.skipif(os.name != "posix", reason="POSIX ownership contract")
def test_intermediate_owner_mismatch_is_rejected_before_chmod_or_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    support = home / "Library" / "Application Support"
    support.mkdir(parents=True, mode=0o755)
    support.chmod(0o755)
    identity = (int(support.stat().st_dev), int(support.stat().st_ino))
    real_fstat = os.fstat

    def foreign_support_owner(descriptor: int):
        details = real_fstat(descriptor)
        if (int(details.st_dev), int(details.st_ino)) == identity:
            values = list(details)
            values[4] = int(details.st_uid) + 1
            return os.stat_result(values)
        return details

    monkeypatch.setattr(os, "fstat", foreign_support_owner)

    with pytest.raises(SecureRuntimeError):
        SecureRuntimeDirectory.open(
            home=home,
            directory=support / "WebJam" / "JamulusClient",
        )

    assert stat.S_IMODE(support.stat().st_mode) == 0o755
    assert not (support / "WebJam").exists()


def test_proofs_reject_widened_modes_and_added_hard_link(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    runtime_path = home / "Library" / "Application Support" / "WebJam"

    with SecureRuntimeDirectory.open(
        home=home,
        directory=runtime_path,
    ) as runtime:
        secret = runtime.write_private_file("rpc.secret", b"secret\n")

        secret.path.chmod(0o644)
        assert not secret.matches()
        secret.path.chmod(0o600)

        os.link(secret.path, runtime.path / "rpc.secret.link")
        assert not secret.matches()

        runtime.path.chmod(0o777)
        assert not runtime.path_matches()
        assert not runtime.proof.matches()


def test_intermediate_symlink_back_to_retained_tree_invalidates_proofs(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    runtime_path = (
        home / "Library" / "Application Support" / "WebJam" / "JamulusClient"
    )

    with SecureRuntimeDirectory.open(
        home=home,
        directory=runtime_path,
    ) as runtime:
        secret = runtime.write_private_file("rpc.secret", b"secret\n")
        support = home / "Library" / "Application Support"
        retained = home / "Library" / "Application Support.retained"
        support.rename(retained)
        support.symlink_to(retained, target_is_directory=True)

        assert not runtime.path_matches()
        assert not runtime.proof.matches()
        assert not secret.matches()


def test_world_writable_owned_ancestor_is_rejected_without_mutation(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    library = home / "Library"
    library.mkdir(parents=True)
    library.chmod(0o777)

    with pytest.raises(SecureRuntimeError):
        SecureRuntimeDirectory.open(
            home=home,
            directory=library / "Application Support" / "WebJam",
        )

    assert stat.S_IMODE(library.stat().st_mode) == 0o777
    assert not (library / "Application Support").exists()


def test_umask_zero_still_creates_private_runtime_components(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    previous = os.umask(0)
    try:
        with SecureRuntimeDirectory.open(
            home=home,
            directory=home / "Library" / "Application Support" / "WebJam",
        ) as runtime:
            assert stat.S_IMODE(runtime.path.stat().st_mode) == 0o700
            assert stat.S_IMODE((home / "Library").stat().st_mode) == 0o700
            assert (
                stat.S_IMODE(
                    (home / "Library" / "Application Support").stat().st_mode
                )
                == 0o700
            )
    finally:
        os.umask(previous)


def test_exact_home_and_public_modes_are_rejected(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()

    with pytest.raises(SecureRuntimeError):
        SecureRuntimeDirectory.open(home=home, directory=home)
    with pytest.raises(SecureRuntimeError):
        SecureRuntimeDirectory.open(
            home=home,
            directory=home / "runtime",
            mode=0o777,
        )

    assert stat.S_IMODE(home.stat().st_mode) != 0o777
    assert not (home / "runtime").exists()


@pytest.mark.parametrize(
    "operation",
    ("open", "chmod", "truncate", "write", "fsync"),
)
def test_os_failures_are_typed_and_suppress_private_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    home = tmp_path / "Private Home With Spaces"
    home.mkdir()
    runtime_path = home / "Library" / "Application Support" / "WebJam"
    runtime = SecureRuntimeDirectory.open(home=home, directory=runtime_path)
    private_text = str(home)
    raw_message = f"raw failure at {private_text}"

    if operation == "open":
        original = os.open

        def fail_open(path, *args, **kwargs):
            if path == "rpc.secret":
                raise OSError(raw_message)
            return original(path, *args, **kwargs)

        monkeypatch.setattr(os, "open", fail_open)
    else:
        target = {
            "chmod": "fchmod",
            "truncate": "ftruncate",
            "write": "write",
            "fsync": "fsync",
        }[operation]

        def fail_operation(*_args, **_kwargs):
            raise OSError(raw_message)

        monkeypatch.setattr(os, target, fail_operation)

    try:
        with pytest.raises(SecureRuntimeError) as caught:
            runtime.write_private_file("rpc.secret", b"secret\n")
        rendered = "".join(
            traceback.format_exception(
                type(caught.value),
                caught.value,
                caught.value.__traceback__,
            )
        )
        assert private_text not in str(caught.value)
        assert private_text not in rendered
        assert raw_message not in rendered
    finally:
        runtime.close()


def test_remove_owned_file_refuses_replacement_and_removes_exact_secret(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    runtime_path = home / "Library" / "Application Support" / "WebJam"

    with SecureRuntimeDirectory.open(
        home=home,
        directory=runtime_path,
    ) as runtime:
        first = runtime.write_private_file("rpc.secret", b"first\n")
        first.path.rename(runtime.path / "rpc.secret.old")
        first.path.write_bytes(b"replacement\n")
        first.path.chmod(0o600)

        assert runtime.remove_owned_file(first) is False
        assert first.path.read_bytes() == b"replacement\n"

        second = runtime.write_private_file("owned.secret", b"second\n")
        assert runtime.remove_owned_file(second) is True
        assert not second.path.exists()


def test_remove_owned_file_quarantines_name_swap_and_restores_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    runtime_path = home / "Library" / "Application Support" / "WebJam"

    with SecureRuntimeDirectory.open(
        home=home,
        directory=runtime_path,
    ) as runtime:
        proof = runtime.write_private_file("rpc.secret", b"owned-secret\n")
        retained_owned = runtime.path / "retained-owned.secret"
        real_rename = os.rename
        swapped = False

        def swap_before_quarantine(
            source,
            destination,
            *,
            src_dir_fd=None,
            dst_dir_fd=None,
        ):
            nonlocal swapped
            if source == "rpc.secret" and destination == "owned" and not swapped:
                swapped = True
                real_rename(
                    source,
                    retained_owned.name,
                    src_dir_fd=src_dir_fd,
                    dst_dir_fd=src_dir_fd,
                )
                descriptor = os.open(
                    source,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                    dir_fd=src_dir_fd,
                )
                try:
                    os.write(descriptor, b"unrelated-replacement\n")
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            return real_rename(
                source,
                destination,
                src_dir_fd=src_dir_fd,
                dst_dir_fd=dst_dir_fd,
            )

        monkeypatch.setattr(os, "rename", swap_before_quarantine)

        assert runtime.remove_owned_file(proof) is False
        assert proof.path.read_bytes() == b"unrelated-replacement\n"
        assert retained_owned.read_bytes() == b"owned-secret\n"
        assert not list(runtime.path.glob(".webjam-quarantine-*"))


def test_remove_owned_file_never_unlinks_new_original_after_quarantine(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    runtime_path = home / "Library" / "Application Support" / "WebJam"

    with SecureRuntimeDirectory.open(
        home=home,
        directory=runtime_path,
    ) as runtime:
        proof = runtime.write_private_file("rpc.secret", b"owned-secret\n")
        real_unlink = os.unlink
        replaced = False

        def replace_original_then_unlink(path, *, dir_fd=None):
            nonlocal replaced
            if path == "owned" and not replaced:
                replaced = True
                descriptor = os.open(
                    "rpc.secret",
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                    dir_fd=runtime.descriptor,
                )
                try:
                    os.write(descriptor, b"new-original\n")
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            return real_unlink(path, dir_fd=dir_fd)

        monkeypatch.setattr(os, "unlink", replace_original_then_unlink)

        assert runtime.remove_owned_file(proof) is True
        assert proof.path.read_bytes() == b"new-original\n"
        assert not list(runtime.path.glob(".webjam-quarantine-*"))


def test_remove_owned_file_refuses_hardlink_and_symlink_without_data_loss(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    runtime_path = home / "Library" / "Application Support" / "WebJam"
    outside = tmp_path / "outside.secret"
    outside.write_bytes(b"outside-data\n")

    with SecureRuntimeDirectory.open(
        home=home,
        directory=runtime_path,
    ) as runtime:
        proof = runtime.write_private_file("rpc.secret", b"owned-secret\n")
        linked = runtime.path / "linked.secret"
        os.link(proof.path, linked)

        assert runtime.remove_owned_file(proof) is False
        assert proof.path.read_bytes() == b"owned-secret\n"
        assert linked.read_bytes() == b"owned-secret\n"

        linked.unlink()
        retained_owned = runtime.path / "retained-owned.secret"
        proof.path.rename(retained_owned)
        proof.path.symlink_to(outside)

        assert runtime.remove_owned_file(proof) is False
        assert proof.path.is_symlink()
        assert outside.read_bytes() == b"outside-data\n"
        assert retained_owned.read_bytes() == b"owned-secret\n"


def test_remove_owned_file_ambiguous_unlink_restores_owned_name_and_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    runtime_path = home / "Library" / "Application Support" / "WebJam"

    with SecureRuntimeDirectory.open(
        home=home,
        directory=runtime_path,
    ) as runtime:
        proof = runtime.write_private_file("rpc.secret", b"owned-secret\n")
        real_unlink = os.unlink
        failed_once = False

        def ambiguous_unlink(path, *, dir_fd=None):
            nonlocal failed_once
            if path == "owned" and not failed_once:
                failed_once = True
                raise OSError("ambiguous cleanup")
            return real_unlink(path, dir_fd=dir_fd)

        monkeypatch.setattr(os, "unlink", ambiguous_unlink)

        assert runtime.remove_owned_file(proof) is False
        assert proof.path.read_bytes() == b"owned-secret\n"
        assert stat.S_IMODE(proof.path.stat().st_mode) == 0o600
        assert proof.path.stat().st_nlink == 1
        assert not list(runtime.path.glob(".webjam-quarantine-*"))
