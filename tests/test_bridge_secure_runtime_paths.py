"""Adversarial native-child path ownership at the Bridge launch boundary."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from core.jamulus_profile import JamulusNativeProfileError
from tests.support.component_store import isolated_component_store_root


class _ImmediateThread:
    def __init__(self, *args, target=None, **kwargs):
        del args, kwargs
        self._target = target

    def start(self):
        if self._target is not None:
            self._target()


def _private_directory(path: Path) -> Path:
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    path.chmod(0o700)
    return path


def _bridge(*, home: Path, secret: Path | None = None, recordings: Path | None = None):
    from services.bridge_service import BridgeService

    settings = mock.MagicMock()
    settings.jamulus_server = "secure-runtime.example.com"
    settings.jamulus_port = 22124
    settings.jamulus_rpc_port = 22222
    settings.server_rpc_port = 22240
    settings.server_rpc_secret_file = str(secret or "")
    settings.takes_directory = str(recordings or "")
    settings.host_server_enabled = False
    settings.jamulus_candidates = []
    settings.musician_name = "Runtime Test"
    repository = mock.MagicMock()
    repository.get_setting.return_value = "1"
    bridge = BridgeService(
        jamulus_controller=mock.MagicMock(),
        webex_controller=mock.MagicMock(),
        metrics_service=mock.MagicMock(),
        repository=repository,
        settings=settings,
        ui_callbacks={
            "set_status_banner": mock.MagicMock(),
            "refresh_readiness": mock.MagicMock(),
            "show_actionable_error": mock.MagicMock(),
            "show_message": mock.MagicMock(),
            "shutdown_requested": lambda: False,
            "schedule_ui_callback": lambda callback: callback(),
        },
        component_store_root=isolated_component_store_root(),
    )
    # Production enables this only for macOS + AppSettings. These tests run
    # the exact POSIX path contract independently of their CI host platform.
    bridge._secure_macos_runtime_enabled = True
    bridge._macos_runtime_home = mock.MagicMock(return_value=home)
    bridge.find_jamulus = mock.MagicMock(return_value="/usr/bin/jamulus")
    bridge._is_rpc_port_in_use = mock.MagicMock(return_value=False)
    return bridge


def _primary_launch(
    bridge,
    *,
    secret_path: Path,
    popen,
) -> None:
    with mock.patch(
        "services.bridge_service.threading.Thread",
        _ImmediateThread,
    ), mock.patch(
        "services.bridge_service.DEFAULT_SECRET_PATH",
        secret_path,
    ), mock.patch(
        "services.bridge_service.subprocess.Popen",
        side_effect=popen,
    ), mock.patch(
        "services.bridge_service.time.sleep",
    ):
        bridge.launch_jamulus(manual=True, reconnect=False)


def _live_process() -> mock.MagicMock:
    process = mock.MagicMock()
    process.poll.return_value = None
    process.pid = 4312
    return process


def test_primary_secret_path_with_spaces_is_retained_and_removed_on_stop(
    tmp_path: Path,
) -> None:
    home = _private_directory(tmp_path / "Musician Home")
    secret = (
        home
        / "Library"
        / "Application Support"
        / "WebJam Test"
        / "Jamulus Client"
        / "rpc secret.txt"
    )
    bridge = _bridge(home=home)
    process = _live_process()
    captured: list[list[str]] = []

    def spawn(command, **_kwargs):
        captured.append(command)
        return process

    _primary_launch(bridge, secret_path=secret, popen=spawn)

    assert bridge.jamulus_state == "Running"
    assert captured[0][captured[0].index("--jsonrpcsecretfile") + 1] == str(secret)
    assert secret.is_file()
    assert secret.stat().st_mode & 0o777 == 0o600
    assert bridge._client_runtime_paths.matches()

    assert bridge.stop_jamulus() is True
    assert not secret.exists()


@pytest.mark.parametrize("leaf_symlink", [False, True])
def test_primary_refuses_intermediate_or_leaf_symlink_without_touching_target(
    tmp_path: Path,
    leaf_symlink: bool,
) -> None:
    home = _private_directory(tmp_path / "home")
    outside = _private_directory(tmp_path / "outside")
    target = outside / "do-not-touch"
    target.write_text("outside-data", encoding="utf-8")
    target.chmod(0o600)
    secret = home / "Library" / "Application Support" / "WebJam" / "rpc.secret"
    if leaf_symlink:
        _private_directory(secret.parent)
        secret.symlink_to(target)
    else:
        (home / "Library").symlink_to(outside, target_is_directory=True)
    bridge = _bridge(home=home)
    popen = mock.MagicMock()

    _primary_launch(bridge, secret_path=secret, popen=popen)

    popen.assert_not_called()
    assert bridge.jamulus_state == "Launch failed"
    assert target.read_text(encoding="utf-8") == "outside-data"


def test_primary_refuses_path_chain_replacement_before_popen(
    tmp_path: Path,
) -> None:
    home = _private_directory(tmp_path / "home")
    secret = home / "Library" / "Application Support" / "WebJam" / "rpc.secret"
    bridge = _bridge(home=home)
    original_validate = bridge._validate_primary_launch_paths
    changed = False

    def replace_then_validate(profile):
        nonlocal changed
        if not changed:
            changed = True
            application_support = home / "Library" / "Application Support"
            moved = home / "Library" / "Application Support retained"
            application_support.rename(moved)
            application_support.symlink_to(moved, target_is_directory=True)
        return original_validate(profile)

    bridge._validate_primary_launch_paths = replace_then_validate
    popen = mock.MagicMock()

    _primary_launch(bridge, secret_path=secret, popen=popen)

    popen.assert_not_called()
    assert bridge.jamulus_state == "Launch failed"


@pytest.mark.parametrize("mutation", ["mode", "hardlink", "leaf", "directory"])
def test_primary_refuses_runtime_mutation_during_popen_and_stops_child(
    tmp_path: Path,
    mutation: str,
) -> None:
    home = _private_directory(tmp_path / "home")
    outside = _private_directory(tmp_path / "outside")
    outside_target = outside / "target"
    outside_target.write_text("outside-data", encoding="utf-8")
    outside_target.chmod(0o600)
    secret = home / "Library" / "Application Support" / "WebJam" / "rpc.secret"
    process = _live_process()
    bridge = _bridge(home=home)

    def spawn(_command, **_kwargs):
        if mutation == "mode":
            secret.chmod(0o644)
        elif mutation == "hardlink":
            os.link(secret, secret.with_name("second-link"))
        elif mutation == "leaf":
            secret.rename(secret.with_name("owned-secret"))
            secret.symlink_to(outside_target)
        else:
            secret.parent.chmod(0o777)
        return process

    _primary_launch(bridge, secret_path=secret, popen=spawn)

    process.terminate.assert_called_once()
    assert bridge.jamulus_state == "Launch failed"
    assert outside_target.read_text(encoding="utf-8") == "outside-data"


def test_primary_revalidates_profile_before_each_attempt_and_after_popen(
    tmp_path: Path,
) -> None:
    home = _private_directory(tmp_path / "home")
    secret = home / "Library" / "Application Support" / "WebJam" / "rpc.secret"
    bridge = _bridge(home=home)
    plan = SimpleNamespace(
        arguments=(),
        working_directory=home,
        jamulus_version="3.12.2",
    )
    manager = mock.MagicMock()
    manager.prepare.return_value = plan
    bridge._native_profile_manager = manager
    process = _live_process()
    calls = 0

    def spawn(_command, **_kwargs):
        nonlocal calls
        calls += 1
        if calls < 3:
            raise OSError("temporary spawn failure")
        return process

    _primary_launch(bridge, secret_path=secret, popen=spawn)

    assert bridge.jamulus_state == "Running"
    assert manager.validate_active.call_count == 4
    assert calls == 3
    assert bridge.stop_jamulus() is True


def test_primary_profile_replacement_after_popen_stops_child(
    tmp_path: Path,
) -> None:
    home = _private_directory(tmp_path / "home")
    secret = home / "Library" / "Application Support" / "WebJam" / "rpc.secret"
    bridge = _bridge(home=home)
    plan = SimpleNamespace(
        arguments=(),
        working_directory=home,
        jamulus_version="3.12.2",
    )
    manager = mock.MagicMock()
    manager.prepare.return_value = plan
    manager.validate_active.side_effect = [
        None,
        JamulusNativeProfileError("The Jamulus profile changed."),
    ]
    bridge._native_profile_manager = manager
    process = _live_process()

    _primary_launch(bridge, secret_path=secret, popen=lambda *_a, **_k: process)

    process.terminate.assert_called_once()
    assert bridge.jamulus_state == "Launch failed"
    assert not secret.exists()


def test_primary_publishes_refreshed_profile_from_both_launch_checkpoints(
    tmp_path: Path,
) -> None:
    home = _private_directory(tmp_path / "home")
    secret = home / "Library" / "Application Support" / "WebJam" / "rpc.secret"
    bridge = _bridge(home=home)

    def plan(fingerprint: str):
        return SimpleNamespace(
            arguments=("--inifile", "WebJam.ini"),
            working_directory=home,
            jamulus_version="3.12.2",
            profile_fingerprint=fingerprint,
        )

    initial = plan("a" * 64)
    before = plan("b" * 64)
    after = plan("c" * 64)
    manager = mock.MagicMock()
    manager.prepare.return_value = initial
    manager.validate_active.side_effect = [before, after]
    bridge._native_profile_manager = manager
    process = _live_process()

    _primary_launch(bridge, secret_path=secret, popen=lambda *_a, **_k: process)

    assert manager.validate_active.call_args_list == [
        mock.call(initial),
        mock.call(before),
    ]
    assert bridge._active_native_profile is after
    assert bridge.stop_jamulus() is True


def _prepare_hosted_bridge(
    *,
    home: Path,
    secret: Path,
    recordings: Path,
):
    bridge = _bridge(home=home, secret=secret, recordings=recordings)
    bridge.settings.host_server_enabled = True
    bridge.find_jamulus_server_with_source = mock.MagicMock(
        return_value=("/Applications/JamulusServer", "bundled")
    )
    bridge._approved_runtime_versions = mock.MagicMock(
        return_value=frozenset({"3.12.2"})
    )
    bridge._port_free = mock.MagicMock(return_value=True)
    bridge._probe_hosted_server_rpc = mock.MagicMock(
        return_value=(True, "ready")
    )
    bridge._start_hosted_caffeinate = mock.MagicMock()
    return bridge


def test_hosted_refuses_recordings_symlink_and_cleans_only_owned_secret(
    tmp_path: Path,
) -> None:
    home = _private_directory(tmp_path / "home")
    outside = _private_directory(tmp_path / "outside")
    outside_marker = outside / "marker"
    outside_marker.write_text("outside-data", encoding="utf-8")
    secret = home / "Library" / "Application Support" / "WebJam" / "rpc.secret"
    recordings = home / "Library" / "Application Support" / "WebJam" / "Takes"
    _private_directory(recordings.parent)
    recordings.symlink_to(outside, target_is_directory=True)
    bridge = _prepare_hosted_bridge(
        home=home,
        secret=secret,
        recordings=recordings,
    )

    with mock.patch(
        "services.bridge_service.default_jamulus_version_probe",
        return_value="3.12.2",
    ), mock.patch("services.bridge_service.subprocess.Popen") as popen:
        ok, detail = bridge.ensure_hosted_server()

    assert not ok
    assert "private band-server data" in detail
    popen.assert_not_called()
    assert outside_marker.read_text(encoding="utf-8") == "outside-data"
    assert not secret.exists()


def test_hosted_retains_proofs_until_confirmed_stop_then_removes_secret(
    tmp_path: Path,
) -> None:
    home = _private_directory(tmp_path / "home with spaces")
    base = home / "Library" / "Application Support" / "WebJam Server"
    secret = base / "Secret Data" / "rpc secret"
    recordings = base / "Session Recordings"
    bridge = _prepare_hosted_bridge(
        home=home,
        secret=secret,
        recordings=recordings,
    )
    process = _live_process()

    with mock.patch(
        "services.bridge_service.default_jamulus_version_probe",
        return_value="3.12.2",
    ), mock.patch(
        "services.bridge_service.subprocess.Popen",
        return_value=process,
    ), mock.patch(
        "services.bridge_service.Path.home",
        return_value=home,
    ):
        ok, detail = bridge.ensure_hosted_server()

    assert ok, detail
    assert bridge._hosted_runtime_paths.matches()
    assert secret.is_file()
    assert recordings.is_dir()
    assert bridge.stop_hosted_server() is True
    assert not secret.exists()
    assert recordings.is_dir()


def test_hosted_recordings_replacement_after_popen_stops_child(
    tmp_path: Path,
) -> None:
    home = _private_directory(tmp_path / "home")
    outside = _private_directory(tmp_path / "outside")
    outside_marker = outside / "marker"
    outside_marker.write_text("outside-data", encoding="utf-8")
    base = home / "Library" / "Application Support" / "WebJam Server"
    secret = base / "Secret" / "rpc.secret"
    recordings = base / "Recordings"
    bridge = _prepare_hosted_bridge(
        home=home,
        secret=secret,
        recordings=recordings,
    )
    process = _live_process()

    def spawn(_command, **_kwargs):
        retained = recordings.with_name("Recordings retained")
        recordings.rename(retained)
        recordings.symlink_to(outside, target_is_directory=True)
        return process

    with mock.patch(
        "services.bridge_service.default_jamulus_version_probe",
        return_value="3.12.2",
    ), mock.patch(
        "services.bridge_service.subprocess.Popen",
        side_effect=spawn,
    ), mock.patch(
        "services.bridge_service.Path.home",
        return_value=home,
    ):
        ok, detail = bridge.ensure_hosted_server()

    assert not ok
    assert "could not start safely" in detail
    process.terminate.assert_called_once()
    assert bridge.hosted_server_process is None
    assert outside_marker.read_text(encoding="utf-8") == "outside-data"


def test_hosted_unknown_process_state_retains_secret_and_descriptors(
    tmp_path: Path,
) -> None:
    home = _private_directory(tmp_path / "home")
    base = home / "Library" / "Application Support" / "WebJam Server"
    secret = base / "Secret" / "rpc.secret"
    recordings = base / "Recordings"
    bridge = _prepare_hosted_bridge(
        home=home,
        secret=secret,
        recordings=recordings,
    )
    bridge._hosted_runtime_paths = bridge._prepare_owned_runtime_paths(
        secret_path=secret,
        secret_payload=b"private-secret\n",
        recordings_path=recordings,
    )
    process = mock.MagicMock()
    process.poll.side_effect = OSError("unknown process state")
    bridge.hosted_server_process = process

    assert bridge.stop_hosted_server() is False
    assert bridge.hosted_server_process is process
    assert bridge._hosted_runtime_paths is not None
    assert secret.is_file()


def test_runtime_oserror_paths_do_not_escape_bridge_logs_or_user_text(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    home = _private_directory(tmp_path / "home")
    secret = home / "Private Music" / "rpc.secret"
    private_text = str(home / "Private Music")
    bridge = _bridge(home=home)

    caplog.set_level(logging.DEBUG, logger="webjam.services.bridge")
    with mock.patch(
        "services.bridge_service.SecureRuntimeDirectory.open",
        side_effect=OSError(private_text),
    ):
        _primary_launch(bridge, secret_path=secret, popen=mock.MagicMock())

    rendered_calls = " ".join(
        str(call)
        for call in bridge.show_actionable_error.call_args_list
    )
    rendered_logs = " ".join(record.getMessage() for record in caplog.records)
    assert private_text not in rendered_calls
    assert private_text not in rendered_logs
    assert bridge.jamulus_state == "Launch failed"


def test_hosted_runtime_oserror_path_does_not_escape_result_or_logs(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    home = _private_directory(tmp_path / "home")
    private_text = str(home / "Private Band Material")
    secret = home / "Private Band Material" / "rpc.secret"
    recordings = home / "Private Band Material" / "Recordings"
    bridge = _prepare_hosted_bridge(
        home=home,
        secret=secret,
        recordings=recordings,
    )
    caplog.set_level(logging.DEBUG, logger="webjam.services.bridge")

    with mock.patch(
        "services.bridge_service.default_jamulus_version_probe",
        return_value="3.12.2",
    ), mock.patch(
        "services.bridge_service.SecureRuntimeDirectory.open",
        side_effect=OSError(private_text),
    ):
        ok, detail = bridge.ensure_hosted_server()

    rendered_logs = " ".join(record.getMessage() for record in caplog.records)
    assert not ok
    assert private_text not in detail
    assert private_text not in rendered_logs


def test_stop_never_unlinks_replaced_primary_secret_target(
    tmp_path: Path,
) -> None:
    home = _private_directory(tmp_path / "home")
    outside = _private_directory(tmp_path / "outside")
    target = outside / "do-not-touch"
    target.write_text("outside-data", encoding="utf-8")
    target.chmod(0o600)
    secret = home / "Library" / "Application Support" / "WebJam" / "rpc.secret"
    bridge = _bridge(home=home)
    process = _live_process()

    _primary_launch(bridge, secret_path=secret, popen=lambda *_a, **_k: process)
    secret.rename(secret.with_name("owned-secret"))
    secret.symlink_to(target)

    assert bridge.stop_jamulus() is True
    assert secret.is_symlink()
    assert target.read_text(encoding="utf-8") == "outside-data"

    replacement = mock.MagicMock()
    _primary_launch(bridge, secret_path=secret, popen=replacement)
    replacement.assert_not_called()
    assert bridge.jamulus_state == "Launch failed"


def test_primary_ambiguous_preparation_cleanup_latches_and_preserves_retry_target(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    home = _private_directory(tmp_path / "private musician home")
    secret = home / "Library" / "Application Support" / "WebJam" / "rpc.secret"
    retained_owned = secret.with_name("retained-owned-secret")
    replacement_payload = b"unrelated-private-replacement\n"
    bridge = _bridge(home=home)
    first_popen = mock.MagicMock()

    def replace_before_ambiguous_cleanup(_runtime, proof) -> bool:
        proof.path.rename(retained_owned)
        proof.path.write_bytes(replacement_payload)
        proof.path.chmod(0o600)
        return False

    caplog.set_level(logging.DEBUG, logger="webjam.services.bridge")
    with mock.patch(
        "services.bridge_service._OwnedJamulusRuntimePaths.matches",
        return_value=False,
    ), mock.patch(
        "services.bridge_service.SecureRuntimeDirectory.remove_owned_file",
        autospec=True,
        side_effect=replace_before_ambiguous_cleanup,
    ):
        _primary_launch(bridge, secret_path=secret, popen=first_popen)

    first_popen.assert_not_called()
    assert bridge.jamulus_state == "Launch failed"
    assert bridge._client_runtime_paths is not None
    assert retained_owned.is_file()
    assert secret.read_bytes() == replacement_payload
    # The retained dirfd remains valid; retry must not reconstruct ownership
    # from the now attacker-replaceable display path.
    assert bridge._client_runtime_paths.secret_directory.path_matches()

    retry_popen = mock.MagicMock()
    _primary_launch(bridge, secret_path=secret, popen=retry_popen)

    retry_popen.assert_not_called()
    assert bridge.jamulus_state == "Launch failed"
    assert secret.read_bytes() == replacement_payload
    rendered_calls = " ".join(
        str(call) for call in bridge.show_actionable_error.call_args_list
    )
    rendered_logs = " ".join(record.getMessage() for record in caplog.records)
    assert str(secret.parent) not in rendered_calls
    assert str(secret.parent) not in rendered_logs
    secret.unlink()
    retained_owned.rename(secret)
    assert bridge._release_client_runtime_paths(confirmed_stopped=True)


def test_hosted_ambiguous_preparation_cleanup_latches_and_preserves_retry_target(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    home = _private_directory(tmp_path / "private band home")
    base = home / "Library" / "Application Support" / "WebJam Server"
    secret = base / "Secret" / "rpc.secret"
    recordings = base / "Recordings"
    retained_owned = secret.with_name("retained-owned-secret")
    replacement_payload = b"unrelated-private-replacement\n"
    bridge = _prepare_hosted_bridge(
        home=home,
        secret=secret,
        recordings=recordings,
    )
    first_popen = mock.MagicMock()

    def replace_before_ambiguous_cleanup(_runtime, proof) -> bool:
        proof.path.rename(retained_owned)
        proof.path.write_bytes(replacement_payload)
        proof.path.chmod(0o600)
        return False

    caplog.set_level(logging.DEBUG, logger="webjam.services.bridge")
    with mock.patch(
        "services.bridge_service.default_jamulus_version_probe",
        return_value="3.12.2",
    ), mock.patch(
        "services.bridge_service._OwnedJamulusRuntimePaths.matches",
        return_value=False,
    ), mock.patch(
        "services.bridge_service.SecureRuntimeDirectory.remove_owned_file",
        autospec=True,
        side_effect=replace_before_ambiguous_cleanup,
    ), mock.patch(
        "services.bridge_service.subprocess.Popen",
        first_popen,
    ):
        ok, detail = bridge.ensure_hosted_server()

    assert not ok
    assert "private band-server data" in detail
    first_popen.assert_not_called()
    assert bridge._hosted_runtime_paths is not None
    assert retained_owned.is_file()
    assert secret.read_bytes() == replacement_payload
    assert bridge._hosted_runtime_paths.secret_directory.path_matches()

    retry_popen = mock.MagicMock()
    bridge._last_resolved_server_component = None
    with mock.patch(
        "services.bridge_service.default_jamulus_version_probe",
        return_value="3.12.2",
    ), mock.patch(
        "services.bridge_service.subprocess.Popen",
        retry_popen,
    ):
        retry_ok, retry_detail = bridge.ensure_hosted_server()

    assert not retry_ok
    assert "private band-server data" in retry_detail
    retry_popen.assert_not_called()
    assert secret.read_bytes() == replacement_payload
    rendered_logs = " ".join(record.getMessage() for record in caplog.records)
    assert str(secret.parent) not in detail
    assert str(secret.parent) not in retry_detail
    assert str(secret.parent) not in rendered_logs
    secret.unlink()
    retained_owned.rename(secret)
    assert bridge._release_hosted_runtime_paths(confirmed_stopped=True)


def test_owned_process_failure_logs_never_include_exception_paths(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from services.bridge_service import BridgeService

    private_path = str(tmp_path / "Private Runtime" / "rpc.secret")
    caplog.set_level(logging.DEBUG, logger="webjam.services.bridge")

    unknown = mock.MagicMock()
    unknown.poll.side_effect = OSError(private_path)
    assert BridgeService._jamulus_process_alive(unknown) is True

    failed_stop = mock.MagicMock()
    failed_stop.poll.side_effect = [None, None]
    failed_stop.terminate.side_effect = OSError(private_path)
    failed_stop.kill.side_effect = OSError(private_path)
    assert BridgeService._terminate_jamulus_child(failed_stop) is False

    rendered_logs = caplog.text
    assert private_path not in rendered_logs
    assert "OSError" in rendered_logs
    assert all(record.exc_info is None for record in caplog.records)
