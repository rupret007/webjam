"""Tests for the v0.16 Jamulus-native profile ownership boundary."""

from __future__ import annotations

import base64
import json
from pathlib import Path
import stat

import pytest

from core.jamulus_profile import (
    JAMULUS_CONTAINER_ID,
    PINNED_JAMULUS_VERSION,
    WEBJAM_NATIVE_PROFILE_FILENAME,
    JamulusAppDataPermissionError,
    JamulusNativeProfileError,
    JamulusNativeProfileManager,
    StartupAttemptRecord,
    StartupAttemptStore,
    StartupClientPhase,
    StartupConnectionState,
    StartupNextAction,
    StartupReadinessRecord,
    StartupReadinessStore,
    StartupRole,
    StartupServerPhase,
    native_profile_fingerprint,
    read_native_audio_device_names,
)


def _manager(tmp_path: Path, **changes: object) -> JamulusNativeProfileManager:
    return JamulusNativeProfileManager(
        home=tmp_path,
        platform="darwin",
        **changes,
    )


def test_plan_uses_dedicated_filename_only_inifile_and_safe_working_directory(
    tmp_path: Path,
) -> None:
    plan = _manager(tmp_path).plan(jamulus_version=PINNED_JAMULUS_VERSION)

    assert plan.profile_filename == WEBJAM_NATIVE_PROFILE_FILENAME
    assert plan.arguments == ("--inifile", WEBJAM_NATIVE_PROFILE_FILENAME)
    assert "/" not in plan.arguments[1]
    assert plan.environment == {}
    assert plan.working_directory == (
        tmp_path
        / "Library"
        / "Containers"
        / JAMULUS_CONTAINER_ID
        / "Data"
        / ".config"
        / "Jamulus"
    )
    assert plan.profile_path.parent == plan.working_directory
    assert plan.profile_exists is False
    assert plan.profile_path.exists() is False


def test_first_plan_creates_no_profile_or_webjam_audio_settings(
    tmp_path: Path,
) -> None:
    plan = _manager(tmp_path).plan(jamulus_version=PINNED_JAMULUS_VERSION)

    assert plan.profile_exists is False
    assert plan.profile_path.exists() is False


def test_macos_app_data_denial_has_typed_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def deny_container_access(
        _path: Path,
        *args: object,
        **kwargs: object,
    ) -> None:
        del args, kwargs
        raise PermissionError("denied by TCC")

    monkeypatch.setattr(Path, "mkdir", deny_container_access)

    with pytest.raises(
        JamulusAppDataPermissionError,
        match=(
            "macOS didn't allow WebJam to use the Jamulus-owned profile "
            "dedicated to WebJam"
        ),
    ):
        _manager(tmp_path).plan(jamulus_version=PINNED_JAMULUS_VERSION)


@pytest.mark.parametrize("operation", ("plan", "validate"))
def test_macos_existing_profile_lstat_denial_has_typed_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    manager = _manager(tmp_path)
    baseline = manager.plan(jamulus_version=PINNED_JAMULUS_VERSION)
    baseline.profile_path.write_text("<client/>", encoding="utf-8")
    original_lstat = Path.lstat

    def deny_dedicated_profile(path: Path, *args: object, **kwargs: object):
        if path == baseline.profile_path:
            raise PermissionError("denied by TCC")
        return original_lstat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", deny_dedicated_profile)

    with pytest.raises(JamulusAppDataPermissionError):
        if operation == "plan":
            manager.plan(jamulus_version=PINNED_JAMULUS_VERSION)
        else:
            manager.validate_active(baseline)


def test_macos_existing_profile_read_denial_has_typed_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(tmp_path)
    baseline = manager.plan(jamulus_version=PINNED_JAMULUS_VERSION)
    baseline.profile_path.write_text("<client/>", encoding="utf-8")
    original_read_bytes = Path.read_bytes

    def deny_dedicated_profile(path: Path) -> bytes:
        if path == baseline.profile_path:
            raise PermissionError("denied by TCC")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", deny_dedicated_profile)

    with pytest.raises(JamulusAppDataPermissionError):
        manager.plan(jamulus_version=PINNED_JAMULUS_VERSION)


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

    monkeypatch.setattr(Path, "mkdir", deny_profile_access)
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
    assert native_profile.read_text(encoding="utf-8") == existing
    assert normal_profile.read_text(encoding="utf-8") == "normal-musician-profile"


def test_read_native_audio_device_names_uses_current_owned_profile(
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
            + base64.b64encode(
                b"in: device/with/slash/out: Built-in Output"
            ).decode("ascii")
            + "</auddev_base64></client>"
        ),
    ),
)
def test_read_native_audio_device_names_fails_closed_and_path_free(
    tmp_path: Path,
    profile: str,
) -> None:
    plan = _manager(tmp_path).plan(jamulus_version=PINNED_JAMULUS_VERSION)
    plan.profile_path.write_text(profile, encoding="utf-8")

    with pytest.raises(JamulusNativeProfileError) as caught:
        read_native_audio_device_names(plan)
    assert "couldn't verify the primary Jamulus audio route" in str(caught.value)
    assert str(tmp_path) not in str(caught.value)


def test_profile_fingerprint_is_path_free_and_changes_with_native_profile_content(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    first = manager.plan(jamulus_version=PINNED_JAMULUS_VERSION)
    first_fingerprint = first.profile_fingerprint
    first.profile_path.write_text("<client><native>changed</native></client>", encoding="utf-8")

    second = manager.plan(jamulus_version=PINNED_JAMULUS_VERSION)

    assert second.profile_fingerprint != first_fingerprint
    assert str(tmp_path) not in second.profile_fingerprint
    assert len(second.profile_fingerprint) == 64
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


def test_symlinked_profile_directory_is_rejected(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    unsafe = manager.profile_directory()
    unsafe.parent.mkdir(parents=True)
    destination = tmp_path / "outside"
    destination.mkdir()
    unsafe.symlink_to(destination, target_is_directory=True)

    with pytest.raises(JamulusNativeProfileError):
        manager.plan(jamulus_version=PINNED_JAMULUS_VERSION)


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

    plan.profile_path.write_text("<client><changed/></client>", encoding="utf-8")
    changed = manager.plan(jamulus_version=PINNED_JAMULUS_VERSION)
    assert store.is_current(changed, StartupRole.HOST) is False


def test_load_fails_closed_for_public_or_unapproved_readiness_json(tmp_path: Path) -> None:
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
