"""Tests for the v0.16 Jamulus-native profile ownership boundary."""

from __future__ import annotations

import base64
from dataclasses import replace
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

import pytest

import core.jamulus_profile as jamulus_profile
from core.jamulus_profile import (
    JAMULUS_CONTAINER_ID,
    PINNED_JAMULUS_VERSION,
    WEBJAM_NATIVE_PROFILE_FILENAME,
    JamulusNativeProfileError,
    JamulusNativeProfileManager,
    NativeProfileAccess,
    StartupAttemptRecord,
    StartupAttemptStore,
    StartupClientPhase,
    StartupConnectionState,
    StartupNextAction,
    StartupReadinessRecord,
    StartupReadinessStore,
    StartupRole,
    StartupServerPhase,
    default_jamulus_version_probe,
    native_profile_fingerprint,
    read_native_audio_device_selector,
    read_native_audio_device_names,
)


def _manager(tmp_path: Path, **changes: object) -> JamulusNativeProfileManager:
    return JamulusNativeProfileManager(
        home=tmp_path,
        platform="darwin",
        **changes,
    )


def test_default_version_probe_uses_bounded_environment_and_neutral_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binary = tmp_path / "Jamulus"
    binary.write_bytes(b"synthetic")
    binary.chmod(0o700)
    observed: dict[str, object] = {}
    monkeypatch.setenv("DYLD_INSERT_LIBRARIES", "/tmp/injected.dylib")
    monkeypatch.setenv("LD_PRELOAD", "/tmp/injected.so")
    monkeypatch.setenv("QML2_IMPORT_PATH", "/tmp/qml")
    monkeypatch.setenv("QTWEBENGINEPROCESS_PATH", "/tmp/qt-helper")
    monkeypatch.setenv("WEBJAM_DIAGNOSTIC", "safe")
    monkeypatch.setenv("PATH", "/tmp/untrusted")

    def run(arguments, **kwargs):
        observed["arguments"] = list(arguments)
        observed["kwargs"] = dict(kwargs)
        return subprocess.CompletedProcess(
            arguments,
            0,
            "Jamulus version 3.12.2\n",
            "",
        )

    monkeypatch.setattr(jamulus_profile.subprocess, "run", run)

    assert default_jamulus_version_probe(str(binary)) == "3.12.2"
    assert observed["arguments"] == [str(binary), "--version"]
    kwargs = observed["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["shell"] is False
    platform_name = (
        "win32"
        if sys.platform.startswith("win")
        else "darwin"
        if sys.platform.startswith("darwin")
        else "linux"
    )
    assert kwargs["cwd"] == (
        str(binary.parent) if platform_name == "win32" else "/"
    )
    environment = kwargs["env"]
    assert isinstance(environment, dict)
    assert environment["WEBJAM_DIAGNOSTIC"] == "safe"
    assert not any(
        key.upper().startswith(("DYLD_", "LD_", "QML", "QT"))
        for key in environment
    )
    assert environment["PATH"] != "/tmp/untrusted"


def test_plan_uses_dedicated_filename_only_inifile_and_safe_working_directory(
    tmp_path: Path,
) -> None:
    home = tmp_path / "Musician Home With Spaces"
    home.mkdir()
    plan = _manager(home).plan(jamulus_version=PINNED_JAMULUS_VERSION)

    assert plan.profile_filename == WEBJAM_NATIVE_PROFILE_FILENAME
    assert plan.arguments == ("--inifile", WEBJAM_NATIVE_PROFILE_FILENAME)
    assert "/" not in plan.arguments[1]
    assert plan.environment == {}
    assert plan.working_directory == (
        home / "Library" / "Application Support" / "WebJam" / "Jamulus Launch"
    )
    assert plan.profile_path.parent == plan.working_directory
    assert JAMULUS_CONTAINER_ID not in plan.profile_path.parts
    assert plan.profile_access is NativeProfileAccess.WEBJAM_READABLE
    assert plan.profile_exists is False
    assert plan.profile_path.exists() is False
    assert plan.working_directory_device > 0
    assert plan.working_directory_inode > 0
    assert stat.S_IMODE(plan.working_directory.stat().st_mode) == 0o700


def test_first_plan_creates_no_profile_or_webjam_audio_settings_or_container(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    old_container = (
        tmp_path
        / "Library"
        / "Containers"
        / JAMULUS_CONTAINER_ID
    )
    plan = manager.plan(jamulus_version=PINNED_JAMULUS_VERSION)

    assert plan.profile_exists is False
    assert plan.profile_path.exists() is False
    assert plan.working_directory.is_dir()
    assert old_container.exists() is False


def test_macos_plan_prepare_validate_and_device_names_never_access_jamulus_container(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "Home With Spaces"
    home.mkdir()
    manager = _manager(
        home,
        version_probe=lambda _binary: PINNED_JAMULUS_VERSION,
    )
    observed: list[tuple[str, Path]] = []

    def guard_path_method(name: str) -> None:
        original = getattr(Path, name)

        def guarded(path: Path, *args: object, **kwargs: object):
            candidate = Path(path)
            observed.append((name, candidate))
            if JAMULUS_CONTAINER_ID in candidate.parts:
                raise AssertionError(f"{name} touched Jamulus's protected container")
            return original(path, *args, **kwargs)

        monkeypatch.setattr(Path, name, guarded)

    for method_name in (
        "mkdir",
        "resolve",
        "lstat",
        "stat",
        "read_bytes",
        "exists",
        "is_symlink",
    ):
        guard_path_method(method_name)

    plan = manager.plan(jamulus_version=PINNED_JAMULUS_VERSION)
    manager.validate_active(plan)
    prepared = manager.prepare(
        object(),
        "/Applications/WebJam.app/Contents/Resources/Jamulus.app/Contents/MacOS/Jamulus",
    )
    manager.validate_active(prepared)
    selector = base64.b64encode(
        b"in: Built-in Microphone/out: Built-in Output"
    ).decode("ascii")
    prepared.profile_path.write_text(
        f"<client><auddev_base64>{selector}</auddev_base64></client>",
        encoding="utf-8",
    )
    assert read_native_audio_device_names(prepared) == (
        "Built-in Microphone",
        "Built-in Output",
    )

    assert observed
    assert all(JAMULUS_CONTAINER_ID not in path.parts for _name, path in observed)


def test_non_macos_profile_permission_error_keeps_portable_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def deny_profile_access(
        _path: Path,
        *args: object,
        **kwargs: object,
    ) -> None:
        del args, kwargs
        raise PermissionError("denied by filesystem")

    monkeypatch.setattr(os, "mkdir", deny_profile_access)
    manager = JamulusNativeProfileManager(home=tmp_path, platform="linux")

    with pytest.raises(JamulusNativeProfileError) as raised:
        manager.plan(jamulus_version=PINNED_JAMULUS_VERSION)

    assert type(raised.value) is JamulusNativeProfileError
    assert str(raised.value) == (
        "WebJam couldn't prepare its Jamulus profile. Reopen WebJam and try again."
    )


def test_existing_native_profile_is_never_rewritten_and_normal_profile_is_untouched(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    directory = manager.profile_directory()
    directory.mkdir(parents=True)
    normal_profile = directory / "Jamulus.ini"
    normal_profile.write_text("normal-musician-profile", encoding="utf-8")
    native_profile = directory / WEBJAM_NATIVE_PROFILE_FILENAME
    existing = "<client><auddev_base64>native-device-choice</auddev_base64></client>"
    native_profile.write_text(existing, encoding="utf-8")

    plan = manager.plan(jamulus_version=PINNED_JAMULUS_VERSION)

    assert plan.profile_exists is True
    assert plan.profile_access is NativeProfileAccess.WEBJAM_READABLE
    assert native_profile.read_text(encoding="utf-8") == existing
    assert normal_profile.read_text(encoding="utf-8") == "normal-musician-profile"


def test_macos_read_native_audio_device_names_uses_webjam_owned_profile(
    tmp_path: Path,
) -> None:
    plan = _manager(tmp_path).plan(jamulus_version=PINNED_JAMULUS_VERSION)
    selector = base64.b64encode(
        b"in: Built-in Microphone/out: Built-in Output"
    ).decode("ascii")
    plan.profile_path.write_text(
        f"<client><auddev_base64>{selector}</auddev_base64></client>",
        encoding="utf-8",
    )

    assert read_native_audio_device_names(plan) == (
        "Built-in Microphone",
        "Built-in Output",
    )


def test_system_default_selector_is_valid_but_not_explicit_route_proof(
    tmp_path: Path,
) -> None:
    plan = _manager(tmp_path).plan(jamulus_version=PINNED_JAMULUS_VERSION)
    selector = base64.b64encode(b"System Default In/Out Devices").decode("ascii")
    plan.profile_path.write_text(
        f"<client><auddev_base64>{selector}</auddev_base64></client>",
        encoding="utf-8",
    )

    parsed = read_native_audio_device_selector(plan)
    assert parsed.uses_system_defaults is True
    assert parsed.input_name == ""
    assert parsed.output_name == ""
    with pytest.raises(JamulusNativeProfileError):
        read_native_audio_device_names(plan)


def test_device_name_reader_rejects_even_forged_jamulus_container_plan(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    plan = manager.plan(jamulus_version=PINNED_JAMULUS_VERSION)
    container_profile = (
        tmp_path
        / "Library"
        / "Containers"
        / JAMULUS_CONTAINER_ID
        / "Data"
        / ".config"
        / "Jamulus"
        / plan.profile_filename
    )
    forged = replace(
        plan,
        profile_path=container_profile,
        profile_access=NativeProfileAccess.WEBJAM_READABLE,
    )

    with pytest.raises(
        JamulusNativeProfileError,
        match="couldn't verify the primary Jamulus audio route",
    ):
        read_native_audio_device_names(forged)


def test_read_native_audio_device_names_parses_a_webjam_readable_profile(
    tmp_path: Path,
) -> None:
    manager = JamulusNativeProfileManager(home=tmp_path, platform="linux")
    plan = manager.plan(jamulus_version=PINNED_JAMULUS_VERSION)
    selector = base64.b64encode(b"in: Built-in Microphone/out: Built-in Output").decode(
        "ascii"
    )
    plan.profile_path.write_text(
        f"<client><auddev_base64>{selector}</auddev_base64></client>",
        encoding="utf-8",
    )

    assert plan.profile_access is NativeProfileAccess.WEBJAM_READABLE
    assert read_native_audio_device_names(plan) == (
        "Built-in Microphone",
        "Built-in Output",
    )


@pytest.mark.parametrize(
    "profile",
    (
        "<client/>",
        "<client><auddev_base64>not-base64</auddev_base64></client>",
        (
            "<client><auddev_base64>"
            + base64.b64encode(b"in: BlackHole 16ch/out: ").decode("ascii")
            + "</auddev_base64></client>"
        ),
        (
            "<client><auddev_base64>"
            + base64.b64encode(b"in: device/with/slash/out: Built-in Output").decode(
                "ascii"
            )
            + "</auddev_base64></client>"
        ),
    ),
)
def test_read_native_audio_device_names_fails_closed_and_path_free(
    tmp_path: Path,
    profile: str,
) -> None:
    plan = JamulusNativeProfileManager(
        home=tmp_path,
        platform="linux",
    ).plan(jamulus_version=PINNED_JAMULUS_VERSION)
    plan.profile_path.write_text(profile, encoding="utf-8")

    with pytest.raises(JamulusNativeProfileError) as caught:
        read_native_audio_device_names(plan)
    assert "couldn't verify the primary Jamulus audio route" in str(caught.value)
    assert str(tmp_path) not in str(caught.value)


def test_macos_profile_fingerprint_is_path_free_and_tracks_real_content(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    container = manager.profile_directory()
    container.mkdir(parents=True)
    native_profile = container / WEBJAM_NATIVE_PROFILE_FILENAME
    native_profile.write_text(
        "<client><native>first</native></client>", encoding="utf-8"
    )
    first = manager.plan(jamulus_version=PINNED_JAMULUS_VERSION)
    first_fingerprint = first.profile_fingerprint
    native_profile.write_text(
        "<client><native>changed</native></client>", encoding="utf-8"
    )

    second = manager.plan(jamulus_version=PINNED_JAMULUS_VERSION)

    assert second.profile_fingerprint != first_fingerprint
    assert str(tmp_path) not in second.profile_fingerprint
    assert len(second.profile_fingerprint) == 64
    assert second.profile_fingerprint == native_profile_fingerprint(
        profile_filename=WEBJAM_NATIVE_PROFILE_FILENAME,
        jamulus_version=PINNED_JAMULUS_VERSION,
        profile_bytes=native_profile.read_bytes(),
        profile_exists=True,
    )


def test_content_fingerprint_remains_deterministic_for_readable_platforms() -> None:
    assert native_profile_fingerprint(
        profile_filename=WEBJAM_NATIVE_PROFILE_FILENAME,
        jamulus_version=PINNED_JAMULUS_VERSION,
        profile_bytes=b"profile",
        profile_exists=True,
    ) == native_profile_fingerprint(
        profile_filename=WEBJAM_NATIVE_PROFILE_FILENAME,
        jamulus_version=PINNED_JAMULUS_VERSION,
        profile_bytes=b"profile",
        profile_exists=True,
    )


def test_prepare_accepts_only_the_bundled_jamulus_version(tmp_path: Path) -> None:
    successful = _manager(
        tmp_path,
        version_probe=lambda _binary: PINNED_JAMULUS_VERSION,
    ).prepare(object(), "/Applications/Jamulus.app/Contents/MacOS/Jamulus")
    assert successful.jamulus_version == PINNED_JAMULUS_VERSION

    manager = _manager(tmp_path / "bad", version_probe=lambda _binary: "3.12.3")
    with pytest.raises(JamulusNativeProfileError, match="included Jamulus 3.12.2"):
        manager.prepare(None, "/Applications/Jamulus.app/Contents/MacOS/Jamulus")


def test_prepare_accepts_exact_registry_selected_update_version(
    tmp_path: Path,
) -> None:
    manager = _manager(
        tmp_path,
        version_probe=lambda _binary: "3.12.3",
    )

    plan = manager.prepare(
        None,
        "/managed/Jamulus.app/Contents/MacOS/Jamulus",
        approved_versions={"3.12.2", "3.12.3"},
        expected_version="3.12.3",
    )

    assert plan.jamulus_version == "3.12.3"


def test_prepare_rejects_approved_but_different_selected_version(
    tmp_path: Path,
) -> None:
    manager = _manager(
        tmp_path,
        version_probe=lambda _binary: "3.12.2",
    )

    with pytest.raises(
        JamulusNativeProfileError,
        match="couldn't verify the approved Jamulus",
    ):
        manager.prepare(
            None,
            "/managed/Jamulus.app/Contents/MacOS/Jamulus",
            approved_versions={"3.12.2", "3.12.3"},
            expected_version="3.12.3",
        )


def test_symlinked_macos_launch_directory_is_rejected(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    unsafe = manager.launch_working_directory()
    unsafe.parent.mkdir(parents=True)
    destination = tmp_path / "outside"
    destination.mkdir()
    unsafe.symlink_to(destination, target_is_directory=True)

    with pytest.raises(JamulusNativeProfileError):
        manager.plan(jamulus_version=PINNED_JAMULUS_VERSION)


def test_intermediate_symlink_cannot_redirect_macos_launch_creation(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    library = home / "Library"
    library.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (library / "Application Support").symlink_to(
        outside,
        target_is_directory=True,
    )

    with pytest.raises(JamulusNativeProfileError):
        _manager(home).plan(jamulus_version=PINNED_JAMULUS_VERSION)

    assert not (outside / "WebJam").exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX ownership contract")
def test_foreign_owned_intermediate_directory_is_rejected_before_changes(
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

    with pytest.raises(JamulusNativeProfileError):
        _manager(home).plan(jamulus_version=PINNED_JAMULUS_VERSION)

    assert stat.S_IMODE(support.stat().st_mode) == 0o755
    assert not (support / "WebJam").exists()


def test_active_plan_rejects_replaced_launch_directory(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    plan = manager.plan(jamulus_version=PINNED_JAMULUS_VERSION)
    original = plan.working_directory.with_name("Jamulus Launch.original")
    plan.working_directory.rename(original)
    plan.working_directory.mkdir(mode=0o700)

    with pytest.raises(
        JamulusNativeProfileError,
        match="couldn't restore its Jamulus profile",
    ):
        manager.validate_active(plan)


def test_active_plan_rejects_intermediate_symlink_back_to_retained_tree(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    plan = manager.plan(jamulus_version=PINNED_JAMULUS_VERSION)
    support = tmp_path / "Library" / "Application Support"
    retained = tmp_path / "Library" / "Application Support.retained"
    support.rename(retained)
    support.symlink_to(retained, target_is_directory=True)

    with pytest.raises(
        JamulusNativeProfileError,
        match="couldn't restore its Jamulus profile",
    ):
        manager.validate_active(plan)


def test_new_plan_retires_old_directory_lease(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    old = manager.plan(jamulus_version=PINNED_JAMULUS_VERSION)
    current = manager.plan(jamulus_version=PINNED_JAMULUS_VERSION)

    with pytest.raises(
        JamulusNativeProfileError,
        match="couldn't restore its Jamulus profile",
    ):
        manager.validate_active(old)
    assert manager.validate_active(current) is current


def test_profile_symlink_is_rejected_without_reading_target(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    plan = manager.plan(jamulus_version=PINNED_JAMULUS_VERSION)
    outside = tmp_path / "outside.ini"
    outside.write_text("<client/>", encoding="utf-8")
    plan.profile_path.symlink_to(outside)

    with pytest.raises(JamulusNativeProfileError):
        manager.plan(jamulus_version=PINNED_JAMULUS_VERSION)
    assert outside.read_text(encoding="utf-8") == "<client/>"


def test_hardlinked_profile_is_rejected_without_touching_other_link(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    plan = manager.plan(jamulus_version=PINNED_JAMULUS_VERSION)
    outside = tmp_path / "unrelated.ini"
    outside.write_text("<client><keep/></client>", encoding="utf-8")
    os.link(outside, plan.profile_path)

    with pytest.raises(
        JamulusNativeProfileError,
        match="couldn't read its Jamulus profile",
    ):
        manager.plan(jamulus_version=PINNED_JAMULUS_VERSION)

    assert outside.read_text(encoding="utf-8") == "<client><keep/></client>"


def test_profile_mode_widened_during_read_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(tmp_path)
    initial = manager.plan(jamulus_version=PINNED_JAMULUS_VERSION)
    initial.profile_path.write_text("<client/>", encoding="utf-8")
    plan = manager.plan(jamulus_version=PINNED_JAMULUS_VERSION)
    identity = (
        int(plan.profile_path.stat().st_dev),
        int(plan.profile_path.stat().st_ino),
    )
    real_read = jamulus_profile.os.read
    widened = False

    def read_then_widen(descriptor: int, amount: int) -> bytes:
        nonlocal widened
        payload = real_read(descriptor, amount)
        details = os.fstat(descriptor)
        if (
            not widened
            and (int(details.st_dev), int(details.st_ino)) == identity
        ):
            widened = True
            plan.profile_path.chmod(0o666)
        return payload

    monkeypatch.setattr(jamulus_profile.os, "read", read_then_widen)

    with pytest.raises(
        JamulusNativeProfileError,
        match="couldn't read its Jamulus profile",
    ):
        manager.validate_active(plan)
    assert widened


def test_existing_profile_disappearance_fails_active_validation(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    first = manager.plan(jamulus_version=PINNED_JAMULUS_VERSION)
    first.profile_path.write_text("<client/>", encoding="utf-8")
    plan = manager.plan(jamulus_version=PINNED_JAMULUS_VERSION)
    assert plan.profile_exists
    plan.profile_path.unlink()

    with pytest.raises(
        JamulusNativeProfileError,
        match="couldn't read its Jamulus profile",
    ):
        manager.validate_active(plan)


def test_oversized_profile_replacement_fails_active_validation(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    first = manager.plan(jamulus_version=PINNED_JAMULUS_VERSION)
    first.profile_path.write_text("<client/>", encoding="utf-8")
    plan = manager.plan(jamulus_version=PINNED_JAMULUS_VERSION)
    with plan.profile_path.open("wb") as profile:
        profile.truncate(4 * 1024 * 1024 + 1)

    with pytest.raises(
        JamulusNativeProfileError,
        match="couldn't read its Jamulus profile",
    ):
        manager.validate_active(plan)


def test_active_validation_refreshes_changed_content_and_new_profile(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    missing = manager.plan(jamulus_version=PINNED_JAMULUS_VERSION)
    missing.profile_path.write_text(
        "<client><sound>first</sound></client>",
        encoding="utf-8",
    )

    created = manager.validate_active(missing)
    assert created.profile_exists
    assert created.profile_fingerprint != missing.profile_fingerprint

    readiness = created.readiness_record(
        StartupRole.HOST,
        human_confirmed=True,
    )
    created.profile_path.write_text(
        "<client><sound>changed</sound></client>",
        encoding="utf-8",
    )
    changed = manager.validate_active(created)

    assert changed.profile_exists
    assert changed.profile_fingerprint != created.profile_fingerprint
    assert readiness.matches(changed, StartupRole.HOST) is False


def test_forged_outside_profile_path_is_rejected_even_with_valid_xml(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    plan = manager.plan(jamulus_version=PINNED_JAMULUS_VERSION)
    outside = tmp_path / "outside.ini"
    selector = base64.b64encode(
        b"in: Built-in Microphone/out: Built-in Output"
    ).decode("ascii")
    outside.write_text(
        f"<client><auddev_base64>{selector}</auddev_base64></client>",
        encoding="utf-8",
    )
    forged = replace(plan, profile_path=outside)

    with pytest.raises(
        JamulusNativeProfileError,
        match="couldn't verify the primary Jamulus audio route",
    ):
        read_native_audio_device_names(forged)


def test_group_writable_ancestor_is_rejected_without_mutation(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    library = home / "Library"
    library.mkdir(parents=True)
    library.chmod(0o777)

    with pytest.raises(JamulusNativeProfileError):
        _manager(home).plan(jamulus_version=PINNED_JAMULUS_VERSION)

    assert stat.S_IMODE(library.stat().st_mode) == 0o777
    assert not (library / "Application Support").exists()


def test_linux_profile_directory_stays_private_under_umask_zero(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    previous = os.umask(0)
    try:
        plan = JamulusNativeProfileManager(
            home=home,
            platform="linux",
        ).plan(jamulus_version=PINNED_JAMULUS_VERSION)
    finally:
        os.umask(previous)

    assert stat.S_IMODE(plan.working_directory.stat().st_mode) == 0o700
    assert stat.S_IMODE((home / ".config").stat().st_mode) == 0o700


def test_readiness_record_contains_only_approved_restart_fields(tmp_path: Path) -> None:
    plan = _manager(tmp_path).plan(jamulus_version=PINNED_JAMULUS_VERSION)
    store = StartupReadinessStore(home=tmp_path, platform="darwin")
    record = store.save_for_plan(plan, StartupRole.HOST, human_confirmed=True)

    raw = json.loads(store.path.read_text(encoding="utf-8"))
    assert record.role is StartupRole.HOST
    assert raw == {
        "human_confirmed": True,
        "jamulus_version": PINNED_JAMULUS_VERSION,
        "profile_fingerprint": plan.profile_fingerprint,
        "role": "host",
    }
    serialized = store.path.read_text(encoding="utf-8")
    assert str(tmp_path) not in serialized
    assert "invite" not in serialized
    assert "webex" not in serialized
    assert "device" not in serialized
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    assert stat.S_IMODE(store.path.parent.stat().st_mode) == 0o700


def test_readiness_reuse_requires_role_current_profile_version_and_human_confirmation(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    plan = manager.plan(jamulus_version=PINNED_JAMULUS_VERSION)
    store = StartupReadinessStore(home=tmp_path, platform="darwin")

    store.save_for_plan(plan, StartupRole.HOST, human_confirmed=False)
    assert store.is_current(plan, StartupRole.HOST) is False

    store.save_for_plan(plan, StartupRole.HOST, human_confirmed=True)
    assert store.is_current(plan, StartupRole.HOST) is True
    assert store.is_current(plan, StartupRole.GUEST) is False

    # The real WebJam-owned profile changed, so an old human confirmation
    # cannot silently carry forward.
    plan.profile_path.write_text("<client><changed/></client>", encoding="utf-8")
    changed_profile = manager.plan(jamulus_version=PINNED_JAMULUS_VERSION)
    assert store.is_current(changed_profile, StartupRole.HOST) is False

    changed_component = manager.plan(jamulus_version="3.12.3")
    assert store.is_current(changed_component, StartupRole.HOST) is False


def test_load_fails_closed_for_public_or_unapproved_readiness_json(
    tmp_path: Path,
) -> None:
    store = StartupReadinessStore(home=tmp_path, platform="darwin")
    valid = StartupReadinessRecord(
        role=StartupRole.GUEST,
        profile_fingerprint="a" * 64,
        jamulus_version=PINNED_JAMULUS_VERSION,
        human_confirmed=True,
    )
    store.save(valid)
    assert store.load() == valid

    store.path.chmod(0o644)
    assert store.load() is None

    store.path.chmod(0o600)
    store.path.write_text(
        json.dumps({**valid.to_mapping(), "invite": "must-not-persist"}),
        encoding="utf-8",
    )
    assert store.load() is None


def test_readiness_rejects_symlink_target(tmp_path: Path) -> None:
    store = StartupReadinessStore(home=tmp_path, platform="darwin")
    store.path.parent.mkdir(parents=True)
    target = tmp_path / "outside.json"
    target.write_text("{}", encoding="utf-8")
    store.path.symlink_to(target)

    assert store.load() is None
    with pytest.raises(JamulusNativeProfileError):
        store.save(
            StartupReadinessRecord(
                role=StartupRole.HOST,
                profile_fingerprint="b" * 64,
                jamulus_version=PINNED_JAMULUS_VERSION,
                human_confirmed=True,
            )
        )


def test_startup_attempt_is_private_allowlisted_and_has_only_safe_recovery_state(
    tmp_path: Path,
) -> None:
    plan = _manager(tmp_path).plan(jamulus_version=PINNED_JAMULUS_VERSION)
    store = StartupAttemptStore(home=tmp_path, platform="darwin")
    record = StartupAttemptRecord.new(
        generation=7,
        role=StartupRole.HOST,
        server_phase=StartupServerPhase.READY,
        client_phase=StartupClientPhase.NATIVE_SOUND_SETUP,
        profile_fingerprint=plan.profile_fingerprint,
        connection_state=StartupConnectionState.CONNECTING,
        human_confirmed=False,
        webex_decision=None,
        next_action=StartupNextAction.FINISH_SOUND_SETUP,
        entropy=b"test-only-entropy",
    )

    store.save(record)

    assert store.load() == record
    assert store.next_generation() == 8
    raw = json.loads(store.path.read_text(encoding="utf-8"))
    assert set(raw) == {
        "attempt_id",
        "generation",
        "role",
        "server_phase",
        "client_phase",
        "profile_fingerprint",
        "connection_state",
        "human_confirmed",
        "webex_decision",
        "next_action",
    }
    assert raw["webex_decision"] is None
    serialized = store.path.read_text(encoding="utf-8")
    assert str(tmp_path) not in serialized
    assert "invite" not in serialized
    assert "url" not in serialized
    assert "device" not in serialized
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    assert stat.S_IMODE(store.path.parent.stat().st_mode) == 0o700

    store.path.write_text(
        json.dumps({**raw, "server_url": "https://must-not-persist.example"}),
        encoding="utf-8",
    )
    store.path.chmod(0o600)
    assert store.load() is None

    requested_webex = StartupAttemptRecord.new(
        generation=8,
        role=StartupRole.GUEST,
        server_phase=StartupServerPhase.NOT_REQUIRED,
        client_phase=StartupClientPhase.READY,
        profile_fingerprint=plan.profile_fingerprint,
        connection_state=StartupConnectionState.CONNECTED,
        human_confirmed=True,
        webex_decision="open_requested",
        next_action=StartupNextAction.OPTIONAL_WEBEX,
        entropy=b"webex-decision-is-not-a-url",
    )
    assert requested_webex.webex_decision is not None
    assert requested_webex.webex_decision.value == "open_requested"
