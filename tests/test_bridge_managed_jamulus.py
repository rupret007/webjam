"""Managed Jamulus runtime resolution and session-pinning contracts."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from core.jamulus_compatibility import (
    ActivationMode,
    ComponentTarget,
    JamulusCapabilities,
    JamulusRole,
    WebJamVersionRange,
    official_jamulus_compatibility_registry,
)
from core.jamulus_component_resolver import ValidatedExternalComponent
from services.bridge_service import BridgeService


class _ImmediateThread:
    def __init__(self, *args, target=None, **kwargs) -> None:
        self._target = target

    def start(self) -> None:
        if self._target is not None:
            self._target()


def _executable(path: Path) -> Path:
    path.write_bytes(b"test executable")
    path.chmod(0o700)
    return path


def _bridge(component_store_root: Path) -> BridgeService:
    settings = MagicMock()
    settings.jamulus_server = "band.example.test"
    settings.jamulus_port = 22124
    settings.jamulus_rpc_port = 22222
    settings.jamulus_candidates = []
    settings.host_server_enabled = False
    settings.musician_name = "Private Musician"
    bridge = BridgeService(
        jamulus_controller=MagicMock(),
        webex_controller=MagicMock(),
        metrics_service=MagicMock(),
        repository=MagicMock(),
        settings=settings,
        ui_callbacks={
            "set_status_banner": MagicMock(),
            "refresh_readiness": MagicMock(),
            "show_actionable_error": MagicMock(),
            "show_message": MagicMock(),
            "shutdown_requested": lambda: False,
            "schedule_ui_callback": lambda callback: callback(),
        },
        component_store_root=component_store_root,
    )
    bridge._runtime_webjam_version = lambda: "0.22.0"
    bridge._is_rpc_port_in_use = MagicMock(return_value=False)
    return bridge


def test_managed_client_precedes_embedded_and_is_reverified_each_use(
    tmp_path: Path,
) -> None:
    bridge = _bridge(tmp_path / "components")
    managed = _executable(tmp_path / "managed-client")
    calls = 0

    def provider() -> Path:
        nonlocal calls
        calls += 1
        return managed

    bridge.set_managed_jamulus_paths(provider, None)
    with patch(
        "services.bridge_service.default_jamulus_version_probe",
        return_value="3.12.3",
    ), patch(
        "services.bridge_service._bundled_jamulus_candidate",
        return_value="/embedded/Jamulus",
    ):
        assert bridge.find_jamulus() == str(managed)
        assert bridge.find_jamulus() == str(managed)

    assert calls == 2
    assert bridge._last_resolved_client_component is not None
    assert bridge._last_resolved_client_component.version == "3.12.3"
    assert bridge._last_resolved_client_component.source == "managed"


def test_invalid_managed_client_falls_back_to_embedded_without_using_explicit(
    tmp_path: Path,
) -> None:
    bridge = _bridge(tmp_path / "components")
    managed = _executable(tmp_path / "unapproved-managed-client")
    explicit = _executable(tmp_path / "explicit-client")
    bridge.settings.jamulus_candidates = [str(explicit)]
    bridge.set_managed_jamulus_paths(lambda: managed, None)

    with patch(
        "services.bridge_service.default_jamulus_version_probe",
        return_value="9.9.9",
    ), patch(
        "services.bridge_service._bundled_jamulus_candidate",
        return_value="/embedded/Jamulus",
    ):
        assert bridge.find_jamulus() == "/embedded/Jamulus"

    component = bridge._last_resolved_client_component
    assert component is not None
    assert component.version == "3.12.2"
    assert component.source == "bundled"


def test_explicit_client_requires_an_approved_version(tmp_path: Path) -> None:
    bridge = _bridge(tmp_path / "components")
    explicit = _executable(tmp_path / "explicit-client")
    bridge.settings.jamulus_candidates = [str(explicit)]

    with patch(
        "services.bridge_service._bundled_jamulus_candidate",
        return_value=None,
    ), patch(
        "services.bridge_service.default_jamulus_version_probe",
        side_effect=("4.0.0", "3.12.2"),
    ):
        assert bridge.find_jamulus() is None
        assert bridge.find_jamulus() == str(explicit)

    component = bridge._last_resolved_client_component
    assert component is not None
    assert component.source == "explicit"
    assert component.version == "3.12.2"


def test_client_and_server_providers_are_role_separated(tmp_path: Path) -> None:
    bridge = _bridge(tmp_path / "components")
    client = _executable(tmp_path / "Jamulus")
    server = _executable(tmp_path / "JamulusServer")
    client_provider = MagicMock(return_value=client)
    server_provider = MagicMock(return_value=server)
    bridge.set_managed_jamulus_paths(client_provider, server_provider)

    with patch(
        "services.bridge_service.default_jamulus_version_probe",
        return_value="3.12.3",
    ):
        assert bridge.find_jamulus() == str(client)
        client_provider.assert_called_once_with()
        server_provider.assert_not_called()

        assert bridge.find_jamulus_server_with_source() == (
            str(server),
            "managed",
        )
        server_provider.assert_called_once_with()

    assert bridge._last_resolved_client_component.role is JamulusRole.CLIENT
    assert bridge._last_resolved_server_component.role is JamulusRole.SERVER


@patch(
    "services.bridge_service.threading.Thread",
    side_effect=lambda *args, **kwargs: _ImmediateThread(*args, **kwargs),
)
@patch("services.bridge_service.time.sleep")
def test_managed_version_drives_native_profile_selection(
    _sleep: MagicMock,
    _thread: MagicMock,
    tmp_path: Path,
) -> None:
    bridge = _bridge(tmp_path / "components")
    managed = _executable(tmp_path / "Jamulus")
    bridge.set_managed_jamulus_paths(lambda: managed, None)
    plan = SimpleNamespace(
        arguments=("--inifile", "WebJam-native-v0.16.ini"),
        working_directory=tmp_path,
        jamulus_version="3.12.3",
    )
    manager = MagicMock()
    manager.prepare.return_value = plan
    bridge._native_profile_manager = manager
    process = MagicMock()
    process.poll.return_value = None

    with patch(
        "services.bridge_service.default_jamulus_version_probe",
        return_value="3.12.3",
    ), patch(
        "services.bridge_service.subprocess.Popen",
        return_value=process,
    ), patch("core.file_io.atomic_write_text"):
        assert bridge.launch_jamulus(manual=True)

    manager.prepare.assert_called_once()
    assert manager.prepare.call_args.args == (bridge.settings, str(managed))
    assert manager.prepare.call_args.kwargs["expected_version"] == "3.12.3"
    assert manager.prepare.call_args.kwargs["approved_versions"] == {
        "3.12.2",
        "3.12.3",
    }
    assert bridge.active_jamulus_component == {
        "role": "client",
        "version": "3.12.3",
        "source": "managed",
    }


def test_live_session_pin_refuses_component_swap_or_provider_replacement(
    tmp_path: Path,
) -> None:
    bridge = _bridge(tmp_path / "components")
    first = _executable(tmp_path / "managed-first")
    replacement = _executable(tmp_path / "managed-replacement")
    selected = [first]

    def provider() -> Path:
        return selected[0]

    bridge.set_managed_jamulus_paths(provider, None)
    with patch(
        "services.bridge_service.default_jamulus_version_probe",
        return_value="3.12.3",
    ):
        assert bridge.find_jamulus() == str(first)
        bridge._active_client_component = bridge._last_resolved_client_component
        bridge.jamulus_launch_intended = True
        selected[0] = replacement

        assert bridge.find_jamulus() is None
        with pytest.raises(RuntimeError, match="only while audio is stopped"):
            bridge.set_managed_jamulus_paths(lambda: replacement, None)


def test_live_host_pin_refuses_server_component_swap(tmp_path: Path) -> None:
    bridge = _bridge(tmp_path / "components")
    first = _executable(tmp_path / "server-first")
    replacement = _executable(tmp_path / "server-replacement")
    selected = [first]
    bridge.set_managed_jamulus_paths(None, lambda: selected[0])

    with patch(
        "services.bridge_service.default_jamulus_version_probe",
        return_value="3.12.3",
    ):
        assert bridge.find_jamulus_server_with_source() == (
            str(first),
            "managed",
        )
        bridge._active_server_component = bridge._last_resolved_server_component
        bridge.jamulus_launch_intended = True
        selected[0] = replacement

        assert bridge.find_jamulus_server_with_source() == (
            None,
            "pinned-invalid",
        )


def test_reference_track_never_uses_managed_interactive_client(
    tmp_path: Path,
) -> None:
    bridge = _bridge(tmp_path / "components")
    managed = _executable(tmp_path / "managed-client")
    provider = MagicMock(return_value=managed)
    bridge.set_managed_jamulus_paths(provider, None)

    with patch(
        "services.bridge_service._bundled_reference_track_jamulus_candidate",
        return_value="/embedded/JamulusHeadlessClient",
    ):
        assert (
            bridge.find_reference_track_jamulus()
            == "/embedded/JamulusHeadlessClient"
        )
    provider.assert_not_called()


def test_catalog_component_accepts_future_compatible_version_not_baked_in(
    tmp_path: Path,
) -> None:
    bridge = _bridge(tmp_path / "components")
    binary = _executable(tmp_path / "future-Jamulus")
    baseline = official_jamulus_compatibility_registry().exact(
        component_id="jamulus",
        role=JamulusRole.CLIENT,
        target=bridge._jamulus_component_target,
        version="3.12.3",
    )
    future = replace(baseline, version="3.12.4")
    validated = ValidatedExternalComponent(
        entry=future,
        executable_path=binary,
        content_verified=True,
        version_verified=True,
        architecture_verified=True,
        publisher_verified=True,
    )
    legacy = MagicMock(return_value=tmp_path / "legacy")
    bridge.set_managed_jamulus_paths(legacy, None)
    provider = MagicMock(return_value=validated)
    bridge.set_managed_jamulus_components(provider, None)

    with patch(
        "services.bridge_service.default_jamulus_version_probe",
        side_effect=AssertionError("validated runtime must not be executed"),
    ) as probe:
        assert bridge.find_jamulus() == str(binary)

    probe.assert_not_called()
    provider.assert_called_once_with()
    legacy.assert_not_called()
    component = bridge._last_resolved_client_component
    assert component is not None
    assert component.catalog_entry == future
    assert component.version == "3.12.4"


@patch(
    "services.bridge_service.threading.Thread",
    side_effect=lambda *args, **kwargs: _ImmediateThread(*args, **kwargs),
)
@patch("services.bridge_service.time.sleep")
def test_future_catalog_client_is_launchable_with_native_profile(
    _sleep: MagicMock,
    _thread: MagicMock,
    tmp_path: Path,
) -> None:
    bridge = _bridge(tmp_path / "components")
    binary = _executable(tmp_path / "future-Jamulus")
    baseline = official_jamulus_compatibility_registry().exact(
        component_id="jamulus",
        role=JamulusRole.CLIENT,
        target=bridge._jamulus_component_target,
        version="3.12.3",
    )
    future = replace(baseline, version="3.12.4")
    bridge.set_managed_jamulus_components(
        lambda: ValidatedExternalComponent(
            entry=future,
            executable_path=binary,
            content_verified=True,
            version_verified=True,
            architecture_verified=True,
            publisher_verified=True,
        ),
        None,
    )
    plan = SimpleNamespace(
        arguments=("--inifile", "WebJam-native-v0.22.ini"),
        working_directory=tmp_path,
        jamulus_version="3.12.4",
    )
    manager = MagicMock()
    manager.plan.return_value = plan
    bridge._native_profile_manager = manager
    process = MagicMock()
    process.poll.return_value = None

    with patch.dict(
        "services.bridge_service.os.environ",
        {
            "LD_PRELOAD": "/tmp/untrusted.so",
            "QT_PLUGIN_PATH": "/tmp/untrusted-qt",
            "WEBJAM_TEST_KEEP": "yes",
        },
        clear=False,
    ), patch(
        "services.bridge_service.default_jamulus_version_probe",
        side_effect=AssertionError("validated runtime must not be executed"),
    ) as probe, patch(
        "services.bridge_service.subprocess.Popen",
        return_value=process,
    ) as popen, patch("core.file_io.atomic_write_text"):
        assert bridge.launch_jamulus(manual=True)

    manager.prepare.assert_not_called()
    manager.plan.assert_called_once_with(jamulus_version="3.12.4")
    probe.assert_not_called()
    child_environment = popen.call_args.kwargs["env"]
    assert "LD_PRELOAD" not in child_environment
    assert "QT_PLUGIN_PATH" not in child_environment
    assert child_environment["WEBJAM_TEST_KEEP"] == "yes"
    assert bridge.active_jamulus_component["version"] == "3.12.4"
    assert bridge.stop_jamulus()


def test_future_catalog_server_is_launchable(
    tmp_path: Path,
) -> None:
    bridge = _bridge(tmp_path / "components")
    binary = _executable(tmp_path / "future-JamulusServer")
    baseline = official_jamulus_compatibility_registry().exact(
        component_id="jamulus",
        role=JamulusRole.SERVER,
        target=bridge._jamulus_component_target,
        version="3.12.3",
    )
    future = replace(baseline, version="3.12.4")
    bridge.set_managed_jamulus_components(
        None,
        lambda: ValidatedExternalComponent(
            entry=future,
            executable_path=binary,
            content_verified=True,
            version_verified=True,
            architecture_verified=True,
            publisher_verified=True,
        ),
    )
    bridge.settings.host_server_enabled = True
    bridge.settings.server_rpc_port = 22240
    bridge.settings.server_rpc_secret_file = str(tmp_path / "server.secret")
    bridge.settings.takes_directory = str(tmp_path / "Recordings")
    bridge._port_free = MagicMock(return_value=True)
    bridge._probe_hosted_server_rpc = MagicMock(return_value=(True, "ready"))
    bridge._start_hosted_caffeinate = MagicMock()
    process = MagicMock()
    process.poll.return_value = None
    process.pid = 4242

    with patch.dict(
        "services.bridge_service.os.environ",
        {
            "DYLD_INSERT_LIBRARIES": "/tmp/untrusted.dylib",
            "QML_IMPORT_PATH": "/tmp/untrusted-qml",
            "WEBJAM_TEST_KEEP": "yes",
        },
        clear=False,
    ), patch(
        "services.bridge_service.default_jamulus_version_probe",
        side_effect=AssertionError("validated runtime must not be executed"),
    ) as probe, patch(
        "services.bridge_service.subprocess.Popen",
        return_value=process,
    ) as popen, patch(
        "services.bridge_service.Path.home",
        return_value=tmp_path,
    ):
        ok, detail = bridge.ensure_hosted_server()

    assert ok, detail
    probe.assert_not_called()
    child_environment = popen.call_args.kwargs["env"]
    assert "DYLD_INSERT_LIBRARIES" not in child_environment
    assert "QML_IMPORT_PATH" not in child_environment
    assert child_environment["WEBJAM_TEST_KEEP"] == "yes"
    assert popen.call_args.kwargs["cwd"] == str(binary.parent)
    assert bridge._active_server_component is not None
    assert bridge._active_server_component.public_details() == {
        "role": "server",
        "version": "3.12.4",
        "source": "managed",
    }
    assert bridge.stop_hosted_server()


@pytest.mark.parametrize(
    "change",
    (
        {"content_verified": False},
        {"version_verified": False},
        {"architecture_verified": False},
    ),
)
def test_catalog_component_rejects_incomplete_verification(
    tmp_path: Path,
    change: dict[str, bool],
) -> None:
    bridge = _bridge(tmp_path / "components")
    binary = _executable(tmp_path / "Jamulus")
    entry = official_jamulus_compatibility_registry().exact(
        component_id="jamulus",
        role=JamulusRole.CLIENT,
        target=bridge._jamulus_component_target,
        version="3.12.3",
    )
    facts = {
        "content_verified": True,
        "version_verified": True,
        "architecture_verified": True,
        "publisher_verified": True,
        **change,
    }
    bridge.set_managed_jamulus_components(
        lambda: ValidatedExternalComponent(
            entry=entry,
            executable_path=binary,
            **facts,
        ),
        None,
    )

    with patch(
        "services.bridge_service.default_jamulus_version_probe",
        return_value="3.12.3",
    ), patch(
        "services.bridge_service._bundled_jamulus_candidate",
        return_value="/embedded/Jamulus",
    ):
        assert bridge.find_jamulus() == "/embedded/Jamulus"


def test_catalog_component_rejects_wrong_role(
    tmp_path: Path,
) -> None:
    bridge = _bridge(tmp_path / "components")
    binary = _executable(tmp_path / "JamulusServer")
    server_entry = official_jamulus_compatibility_registry().exact(
        component_id="jamulus",
        role=JamulusRole.SERVER,
        target=bridge._jamulus_component_target,
        version="3.12.3",
    )
    validated = ValidatedExternalComponent(
        entry=server_entry,
        executable_path=binary,
        content_verified=True,
        version_verified=True,
        architecture_verified=True,
        publisher_verified=True,
    )
    bridge.set_managed_jamulus_components(lambda: validated, None)

    with patch(
        "services.bridge_service.default_jamulus_version_probe",
        return_value="3.12.2",
    ), patch(
        "services.bridge_service._bundled_jamulus_candidate",
        return_value="/embedded/Jamulus",
    ):
        assert bridge.find_jamulus() == "/embedded/Jamulus"


@pytest.mark.parametrize(
    "entry_change",
    (
        {"component_id": "not-jamulus"},
        {"variant": "unapproved"},
        {"activation_mode": ActivationMode.EMBEDDED_ONLY},
        {
            "capabilities": JamulusCapabilities(
                frozenset({"audio-client"})
            )
        },
        {
            "webjam_range": WebJamVersionRange(
                minimum="0.23.0",
                maximum="0.23.999",
            )
        },
    ),
)
def test_catalog_component_rejects_incompatible_identity_policy(
    tmp_path: Path,
    entry_change: dict[str, object],
) -> None:
    bridge = _bridge(tmp_path / "components")
    binary = _executable(tmp_path / "Jamulus")
    baseline = official_jamulus_compatibility_registry().exact(
        component_id="jamulus",
        role=JamulusRole.CLIENT,
        target=bridge._jamulus_component_target,
        version="3.12.3",
    )
    validated = ValidatedExternalComponent(
        entry=replace(baseline, **entry_change),
        executable_path=binary,
        content_verified=True,
        version_verified=True,
        architecture_verified=True,
        publisher_verified=True,
    )
    bridge.set_managed_jamulus_components(lambda: validated, None)

    with patch(
        "services.bridge_service.default_jamulus_version_probe",
        return_value="3.12.3",
    ), patch(
        "services.bridge_service._bundled_jamulus_candidate",
        return_value="/embedded/Jamulus",
    ):
        assert bridge.find_jamulus() == "/embedded/Jamulus"


def test_catalog_component_rejects_wrong_target(
    tmp_path: Path,
) -> None:
    bridge = _bridge(tmp_path / "components")
    binary = _executable(tmp_path / "Jamulus")
    baseline = official_jamulus_compatibility_registry().exact(
        component_id="jamulus",
        role=JamulusRole.CLIENT,
        target=bridge._jamulus_component_target,
        version="3.12.3",
    )
    other_target = (
        ComponentTarget.MACOS_X64
        if bridge._jamulus_component_target is ComponentTarget.MACOS_ARM64
        else ComponentTarget.MACOS_ARM64
    )
    validated = ValidatedExternalComponent(
        entry=replace(baseline, target=other_target),
        executable_path=binary,
        content_verified=True,
        version_verified=True,
        architecture_verified=True,
        publisher_verified=True,
    )
    bridge.set_managed_jamulus_components(lambda: validated, None)

    with patch(
        "services.bridge_service.default_jamulus_version_probe",
        return_value="3.12.3",
    ), patch(
        "services.bridge_service._bundled_jamulus_candidate",
        return_value="/embedded/Jamulus",
    ):
        assert bridge.find_jamulus() == "/embedded/Jamulus"


@pytest.mark.parametrize("path_kind", ("symlink", "not-executable"))
def test_catalog_component_rejects_unsafe_executable_path(
    tmp_path: Path,
    path_kind: str,
) -> None:
    bridge = _bridge(tmp_path / "components")
    executable = _executable(tmp_path / "real-Jamulus")
    if path_kind == "symlink":
        candidate = tmp_path / "Jamulus-link"
        candidate.symlink_to(executable)
    else:
        candidate = executable
        candidate.chmod(0o600)
    entry = official_jamulus_compatibility_registry().exact(
        component_id="jamulus",
        role=JamulusRole.CLIENT,
        target=bridge._jamulus_component_target,
        version="3.12.3",
    )
    validated = ValidatedExternalComponent(
        entry=entry,
        executable_path=candidate,
        content_verified=True,
        version_verified=True,
        architecture_verified=True,
        publisher_verified=True,
    )
    bridge.set_managed_jamulus_components(lambda: validated, None)

    with patch(
        "services.bridge_service.default_jamulus_version_probe",
        return_value="3.12.3",
    ), patch(
        "services.bridge_service._bundled_jamulus_candidate",
        return_value="/embedded/Jamulus",
    ):
        assert bridge.find_jamulus() == "/embedded/Jamulus"


def test_catalog_component_resolution_does_not_execute_prevalidated_binary(
    tmp_path: Path,
) -> None:
    bridge = _bridge(tmp_path / "components")
    binary = _executable(tmp_path / "Jamulus")
    entry = official_jamulus_compatibility_registry().exact(
        component_id="jamulus",
        role=JamulusRole.CLIENT,
        target=bridge._jamulus_component_target,
        version="3.12.3",
    )
    validated = ValidatedExternalComponent(
        entry=entry,
        executable_path=binary,
        content_verified=True,
        version_verified=True,
        architecture_verified=True,
        publisher_verified=True,
    )
    bridge.set_managed_jamulus_components(lambda: validated, None)

    with patch(
        "services.bridge_service.default_jamulus_version_probe",
        side_effect=AssertionError("validated runtime must not be executed"),
    ) as probe:
        assert bridge.find_jamulus() == str(binary)

    probe.assert_not_called()


def test_catalog_session_pin_revalidates_exact_identity(
    tmp_path: Path,
) -> None:
    bridge = _bridge(tmp_path / "components")
    binary = _executable(tmp_path / "Jamulus")
    entry = official_jamulus_compatibility_registry().exact(
        component_id="jamulus",
        role=JamulusRole.CLIENT,
        target=bridge._jamulus_component_target,
        version="3.12.3",
    )
    selected = [
        ValidatedExternalComponent(
            entry=entry,
            executable_path=binary,
            content_verified=True,
            version_verified=True,
            architecture_verified=True,
            publisher_verified=True,
        )
    ]
    bridge.set_managed_jamulus_components(lambda: selected[0], None)

    with patch(
        "services.bridge_service.default_jamulus_version_probe",
        return_value="3.12.3",
    ):
        assert bridge.find_jamulus() == str(binary)
        bridge._active_client_component = bridge._last_resolved_client_component
        bridge.jamulus_launch_intended = True
        selected[0] = replace(
            selected[0],
            entry=replace(entry, version="3.12.4"),
        )

        assert bridge.find_jamulus() is None


def test_managed_provider_change_is_blocked_by_any_runtime_lease(
    tmp_path: Path,
) -> None:
    bridge = _bridge(tmp_path / "components")
    assert bridge._acquire_runtime_component_lease("practice")[0]

    with pytest.raises(RuntimeError, match="only while audio is stopped"):
        bridge.set_managed_jamulus_components(None, None)

    bridge._release_runtime_component_lease("practice")


def test_audited_unsigned_windows_policy_is_narrow_and_truthful(
    tmp_path: Path,
) -> None:
    entry = official_jamulus_compatibility_registry().exact(
        component_id="jamulus",
        role=JamulusRole.CLIENT,
        target=ComponentTarget.WINDOWS_X64,
        version="3.12.3",
    )
    exact_unsigned = ValidatedExternalComponent(
        entry=entry,
        executable_path=tmp_path / "Jamulus.exe",
        content_verified=True,
        version_verified=True,
        architecture_verified=True,
        publisher_verified=False,
    )
    assert not BridgeService._validated_component_trust_is_approved(
        exact_unsigned
    )
    exact_unsigned = replace(exact_unsigned, trust_policy_verified=True)
    assert BridgeService._validated_component_trust_is_approved(exact_unsigned)
    assert not exact_unsigned.publisher_verified

    arbitrary_publisher = replace(entry, publisher="Unsigned third-party binary")
    assert not BridgeService._validated_component_trust_is_approved(
        replace(exact_unsigned, entry=arbitrary_publisher)
    )
    managed_activation = replace(entry, activation_mode=ActivationMode.MANAGED)
    assert not BridgeService._validated_component_trust_is_approved(
        replace(exact_unsigned, entry=managed_activation)
    )
    assert not BridgeService._validated_component_trust_is_approved(
        replace(exact_unsigned, content_verified=False)
    )


def test_linux_requires_explicit_package_ownership_policy_proof(
    tmp_path: Path,
) -> None:
    entry = official_jamulus_compatibility_registry().exact(
        component_id="jamulus",
        role=JamulusRole.CLIENT,
        target=ComponentTarget.LINUX_X64,
        version="3.12.3",
    )
    package = ValidatedExternalComponent(
        entry=entry,
        executable_path=tmp_path / "jamulus",
        content_verified=True,
        version_verified=True,
        architecture_verified=True,
        publisher_verified=False,
    )
    assert not BridgeService._validated_component_trust_is_approved(package)
    assert BridgeService._validated_component_trust_is_approved(
        replace(package, trust_policy_verified=True)
    )
