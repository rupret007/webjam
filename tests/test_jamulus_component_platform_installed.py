from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import stat
import struct
import subprocess

import pytest

import services.jamulus_component_platform as platform_module
from core.component_lock import (
    ComponentLockTimeout,
    InterProcessComponentLock,
)
from core.component_store import ComponentBusyReason, ComponentBusyStatus
from core.jamulus_compatibility import (
    ActivationMode,
    ArtifactIdentity,
    ArtifactKind,
    ComponentTarget,
    JamulusCapabilities,
    JamulusCompatibility,
    JamulusCompatibilityRegistry,
    JamulusRole,
    JamulusSourceIdentity,
    LegalInventory,
    RuntimeFileIdentity,
    SourceProvenance,
    WebJamVersionRange,
    official_jamulus_compatibility_registry,
)
from services.jamulus_component_platform import (
    JamulusPlatformError,
    JamulusPlatformInstallDeferred,
    JamulusPlatformInstallationNotFound,
    PlatformInstalledJamulusStore,
    _sanitized_loader_environment,
)


def _pe_image(machine: int = 0x8664) -> bytes:
    payload = bytearray(256)
    payload[:2] = b"MZ"
    struct.pack_into("<I", payload, 0x3C, 0x80)
    payload[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<H", payload, 0x84, machine)
    payload[0x90:] = bytes(range(112))
    return bytes(payload)


def _elf_image(machine: int = 62) -> bytes:
    payload = bytearray(128)
    payload[:4] = b"\x7fELF"
    payload[4] = 2  # ELFCLASS64
    payload[5] = 1  # ELFDATA2LSB
    payload[6] = 1  # EV_CURRENT
    struct.pack_into("<H", payload, 16, 3)
    struct.pack_into("<H", payload, 18, machine)
    payload[20:] = bytes(range(108))
    return bytes(payload)


def _pair(
    runtime: bytes,
    *,
    target: ComponentTarget,
    version: str = "3.12.3",
    activation: ActivationMode = ActivationMode.PLATFORM_APPROVAL,
    artifact_data: bytes = b"approved upstream package",
) -> tuple[JamulusCompatibility, JamulusCompatibility]:
    runtime_path = (
        "Jamulus.exe" if target is ComponentTarget.WINDOWS_X64 else "usr/bin/jamulus"
    )
    kind = (
        ArtifactKind.INSTALLER
        if target is ComponentTarget.WINDOWS_X64
        else ArtifactKind.PACKAGE
    )
    artifact = ArtifactIdentity(
        url=(
            "https://github.com/jamulussoftware/jamulus/releases/download/"
            f"r{version.replace('.', '_')}/jamulus_{version}.bin"
        ),
        filename=f"jamulus_{version}.bin",
        size=len(artifact_data),
        sha256=hashlib.sha256(artifact_data).hexdigest(),
        kind=kind,
    )
    runtime_identity = RuntimeFileIdentity(
        relative_path=runtime_path,
        size=len(runtime),
        sha256=hashlib.sha256(runtime).hexdigest(),
        executable=True,
    )
    common = {
        "component_id": "jamulus",
        "target": target,
        "version": version,
        "variant": "official",
        "source": JamulusSourceIdentity(
            repository="jamulussoftware/jamulus",
            tag=f"r{version.replace('.', '_')}",
            commit="a" * 40,
            provenance=SourceProvenance.OFFICIAL_RELEASE,
        ),
        "artifact": artifact,
        "runtime_files": (runtime_identity,),
        "executable_relative_path": runtime_path,
        "webjam_range": WebJamVersionRange("0.22.0", "0.22.999"),
        "legal": LegalInventory(
            license_files=("licenses/JAMULUS_COPYING-r3_12_3.txt",),
            notice_files=("THIRD_PARTY_NOTICES.md",),
            source_offer="THIRD_PARTY_NOTICES.md",
        ),
        "activation_mode": activation,
        "publisher": "Exact catalog bytes; platform approval required",
    }
    return (
        JamulusCompatibility(
            role=JamulusRole.CLIENT,
            capabilities=JamulusCapabilities(
                frozenset({"audio-client", "json-rpc-client"})
            ),
            **common,
        ),
        JamulusCompatibility(
            role=JamulusRole.SERVER,
            capabilities=JamulusCapabilities(
                frozenset({"audio-server", "json-rpc-server"})
            ),
            **common,
        ),
    )


def _write_runtime(path: Path, payload: bytes, *, executable: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    path.chmod(0o755 if executable else 0o644)


def _store(
    tmp_path: Path,
    pair: tuple[JamulusCompatibility, JamulusCompatibility],
    *,
    platform_name: str,
    path: Path,
    version: str = "3.12.3",
    lock_timeout: float = 5.0,
) -> PlatformInstalledJamulusStore:
    return PlatformInstalledJamulusStore(
        JamulusCompatibilityRegistry(pair),
        target=pair[0].target,
        root=tmp_path / "components",
        platform_name=platform_name,
        version_probe=lambda _path: version,
        canonical_path_provider=lambda: (path,),
        path_trust_verifier=lambda _path, _target: None,
        linux_package_verifier=lambda _path, _version: None,
        windows_inventory_provider=lambda entry: tuple(entry.runtime_files),
        lock_timeout=lock_timeout,
    )


def test_official_3123_runtime_identities_match_selected_x64_payloads() -> None:
    registry = official_jamulus_compatibility_registry()
    expected = {
        ComponentTarget.WINDOWS_X64: (
            "Jamulus.exe",
            3_111_424,
            "25c3dacaece705a233d9d2a1b7ddb00bb5dfcd10fb3af7ed98f024c56b473295",
        ),
        ComponentTarget.LINUX_X64: (
            "usr/bin/jamulus",
            3_430_688,
            "f576bb7139b4f48ae8331cff46641dc5a0350e6afbd11cd93411fbf36834c983",
        ),
    }
    for target, identity in expected.items():
        client = registry.exact(
            component_id="jamulus",
            role=JamulusRole.CLIENT,
            target=target,
            version="3.12.3",
        )
        server = registry.exact(
            component_id="jamulus",
            role=JamulusRole.SERVER,
            target=target,
            version="3.12.3",
        )
        observed = client.runtime_files[0]
        assert (
            observed.relative_path,
            observed.size,
            observed.sha256,
        ) == identity
        if target is ComponentTarget.WINDOWS_X64:
            assert len(client.runtime_files) == 27
            assert {
                item.relative_path.casefold() for item in client.runtime_files
            } == {
                "jamulus.exe",
                "avcodec-61.dll",
                "avformat-61.dll",
                "avutil-59.dll",
                "d3dcompiler_47.dll",
                "dxcompiler.dll",
                "dxil.dll",
                "icuuc.dll",
                "qt6core.dll",
                "qt6gui.dll",
                "qt6multimedia.dll",
                "qt6network.dll",
                "qt6widgets.dll",
                "qt6xml.dll",
                "swresample-5.dll",
                "swscale-8.dll",
                "generic/qtuiotouchplugin.dll",
                "imageformats/qgif.dll",
                "imageformats/qico.dll",
                "imageformats/qjpeg.dll",
                "multimedia/ffmpegmediaplugin.dll",
                "multimedia/windowsmediaplugin.dll",
                "networkinformation/qnetworklistmanager.dll",
                "platforms/qwindows.dll",
                "styles/qmodernwindowsstyle.dll",
                "tls/qcertonlybackend.dll",
                "tls/qschannelbackend.dll",
            }
        else:
            assert len(client.runtime_files) == 1
        assert server.runtime_files == client.runtime_files
        assert client.executable_relative_path == identity[0]
        assert server.executable_relative_path == identity[0]


def test_windows_receipt_uses_only_program_files_and_revalidates_each_role(
    tmp_path: Path,
) -> None:
    runtime = _pe_image()
    pair = _pair(runtime, target=ComponentTarget.WINDOWS_X64)
    program_files = tmp_path / "Program Files"
    executable = program_files / "Jamulus" / "Jamulus.exe"
    _write_runtime(executable, runtime, executable=False)
    probes: list[str] = []
    busy_calls = 0

    def idle():
        nonlocal busy_calls
        busy_calls += 1
        return None

    store = PlatformInstalledJamulusStore(
        JamulusCompatibilityRegistry(pair),
        target=ComponentTarget.WINDOWS_X64,
        root=tmp_path / "components",
        platform_name="win32",
        environ={
            "ProgramFiles": str(program_files),
            "PATH": str(tmp_path / "untrusted"),
            "WEBJAM_JAMULUS_PATH": str(tmp_path / "untrusted.exe"),
        },
        version_probe=lambda path: probes.append(path) or "3.12.3",
        path_trust_verifier=lambda _path, _target: None,
        windows_inventory_provider=lambda entry: tuple(entry.runtime_files),
    )
    assert store.current(JamulusRole.CLIENT) is None

    result = store.record_installed(pair[0], pair[1], idle)
    assert result.client.entry == pair[0]
    assert result.server.entry == pair[1]
    assert result.client.executable_path == executable
    assert result.client.publisher_verified is False
    assert result.client.content_verified is True
    assert result.client.version_verified is True
    assert result.client.architecture_verified is True
    assert result.client.trust_policy_verified is True
    assert result.server.trust_policy_verified is True
    assert busy_calls == 2

    client = store.current(JamulusRole.CLIENT)
    server = store.current(JamulusRole.SERVER)
    assert client is not None and client.entry == pair[0]
    assert server is not None and server.entry == pair[1]
    assert client.executable_path == server.executable_path == executable
    assert len(probes) == 3

    receipt_text = store.state_path.read_text(encoding="utf-8")
    receipt = json.loads(receipt_text)
    assert receipt["target"] == "windows-x64"
    assert frozenset(receipt) == frozenset({"schema", "target", "client", "server"})
    assert str(executable) not in receipt_text
    assert "Program Files" not in receipt_text
    assert stat.S_IMODE(store.state_path.stat().st_mode) == 0o600
    assert store.state_path == (
        tmp_path / "components" / "platform" / "windows-x64" / "state.json"
    )
    assert not hasattr(store, "rollback")


def test_windows_program_files_x86_is_not_a_canonical_x64_fallback(
    tmp_path: Path,
) -> None:
    runtime = _pe_image()
    pair = _pair(runtime, target=ComponentTarget.WINDOWS_X64)
    program_files = tmp_path / "Program Files"
    program_files_x86 = tmp_path / "Program Files (x86)"
    executable = program_files_x86 / "Jamulus" / "Jamulus.exe"
    _write_runtime(executable, runtime, executable=False)
    untrusted = tmp_path / "elsewhere" / "Jamulus.exe"
    _write_runtime(untrusted, runtime, executable=False)
    store = PlatformInstalledJamulusStore(
        JamulusCompatibilityRegistry(pair),
        target=ComponentTarget.WINDOWS_X64,
        root=tmp_path / "components",
        platform_name="win32",
        environ={
            "PROGRAMFILES": str(program_files),
            "PROGRAMFILES(X86)": str(program_files_x86),
            "PATH": str(untrusted.parent),
        },
        version_probe=lambda _path: "3.12.3",
    )

    with pytest.raises(JamulusPlatformInstallationNotFound):
        store.record_installed(pair[0], pair[1], lambda: None)
    assert not store.state_path.exists()


def test_linux_exact_elf_receipt_requires_executable_mode(tmp_path: Path) -> None:
    runtime = _elf_image()
    pair = _pair(runtime, target=ComponentTarget.LINUX_X64)
    executable = tmp_path / "simulated-root" / "usr" / "bin" / "jamulus"
    _write_runtime(executable, runtime, executable=True)
    store = _store(
        tmp_path,
        pair,
        platform_name="linux",
        path=executable,
    )

    result = store.record_installed(pair[0], pair[1], lambda: None)

    assert result.client.executable_path == executable
    assert store.current(JamulusRole.SERVER).entry == pair[1]
    executable.chmod(0o644)
    with pytest.raises(JamulusPlatformError, match="executable"):
        store.current(JamulusRole.CLIENT)


@pytest.mark.parametrize(
    ("target", "runtime", "platform_name"),
    [
        (ComponentTarget.WINDOWS_X64, _pe_image(0x014C), "win32"),
        (ComponentTarget.LINUX_X64, _elf_image(3), "linux"),
    ],
)
def test_wrong_binary_architecture_is_rejected_even_when_catalog_hash_matches(
    tmp_path: Path,
    target: ComponentTarget,
    runtime: bytes,
    platform_name: str,
) -> None:
    pair = _pair(runtime, target=target)
    executable = tmp_path / (
        "Jamulus.exe" if target is ComponentTarget.WINDOWS_X64 else "jamulus"
    )
    _write_runtime(
        executable,
        runtime,
        executable=target is ComponentTarget.LINUX_X64,
    )
    store = _store(
        tmp_path,
        pair,
        platform_name=platform_name,
        path=executable,
    )

    with pytest.raises(JamulusPlatformError, match="architecture"):
        store.record_installed(pair[0], pair[1], lambda: None)
    assert not store.state_path.exists()


def test_symlink_nonregular_and_wrong_version_fail_closed(tmp_path: Path) -> None:
    runtime = _elf_image()
    pair = _pair(runtime, target=ComponentTarget.LINUX_X64)
    actual = tmp_path / "actual-jamulus"
    _write_runtime(actual, runtime, executable=True)
    symlink = tmp_path / "jamulus"
    symlink.symlink_to(actual)
    symlink_store = _store(
        tmp_path / "symlink-store",
        pair,
        platform_name="linux",
        path=symlink,
    )
    with pytest.raises(JamulusPlatformError, match="regular file"):
        symlink_store.record_installed(pair[0], pair[1], lambda: None)

    directory = tmp_path / "directory" / "jamulus"
    directory.mkdir(parents=True)
    directory_store = _store(
        tmp_path / "directory-store",
        pair,
        platform_name="linux",
        path=directory,
    )
    with pytest.raises(JamulusPlatformError, match="regular file"):
        directory_store.record_installed(pair[0], pair[1], lambda: None)

    wrong_version_runtime = tmp_path / "wrong-version" / "jamulus"
    _write_runtime(wrong_version_runtime, runtime, executable=True)
    wrong_version_store = _store(
        tmp_path / "version-store",
        pair,
        platform_name="linux",
        path=wrong_version_runtime,
        version="3.12.2",
    )
    with pytest.raises(JamulusPlatformError, match="version"):
        wrong_version_store.record_installed(pair[0], pair[1], lambda: None)


def test_hash_and_registry_are_revalidated_on_every_current(
    tmp_path: Path,
) -> None:
    runtime = _pe_image()
    pair = _pair(runtime, target=ComponentTarget.WINDOWS_X64)
    executable = tmp_path / "Jamulus.exe"
    _write_runtime(executable, runtime, executable=False)
    store = _store(
        tmp_path,
        pair,
        platform_name="win32",
        path=executable,
    )
    store.record_installed(pair[0], pair[1], lambda: None)

    changed = bytearray(runtime)
    changed[-1] ^= 0xFF
    executable.write_bytes(changed)
    with pytest.raises(JamulusPlatformError, match="does not match the catalog"):
        store.current(JamulusRole.CLIENT)

    executable.write_bytes(runtime)
    different = _pair(
        runtime,
        target=ComponentTarget.WINDOWS_X64,
        version="3.12.4",
    )
    store.registry = JamulusCompatibilityRegistry(different)
    with pytest.raises(JamulusPlatformError, match="current registry"):
        store.current(JamulusRole.CLIENT)


def test_receipt_schema_permissions_and_role_are_strict(tmp_path: Path) -> None:
    runtime = _elf_image()
    pair = _pair(runtime, target=ComponentTarget.LINUX_X64)
    executable = tmp_path / "jamulus"
    _write_runtime(executable, runtime, executable=True)
    store = _store(
        tmp_path,
        pair,
        platform_name="linux",
        path=executable,
    )
    store.record_installed(pair[0], pair[1], lambda: None)

    store.state_path.chmod(0o644)
    with pytest.raises(JamulusPlatformError, match="not private"):
        store.current(JamulusRole.CLIENT)
    store.state_path.chmod(0o600)

    value = json.loads(store.state_path.read_text(encoding="utf-8"))
    value["executable_path"] = str(executable)
    store.state_path.write_text(json.dumps(value), encoding="utf-8")
    store.state_path.chmod(0o600)
    with pytest.raises(JamulusPlatformError, match="receipt is invalid"):
        store.current(JamulusRole.CLIENT)
    with pytest.raises(JamulusPlatformError, match="unsupported"):
        store.current(JamulusRole.HEADLESS)


def test_pair_platform_approval_busy_state_and_lock_are_enforced(
    tmp_path: Path,
) -> None:
    runtime = _pe_image()
    pair = _pair(runtime, target=ComponentTarget.WINDOWS_X64)
    executable = tmp_path / "Jamulus.exe"
    _write_runtime(executable, runtime, executable=False)
    store = _store(
        tmp_path,
        pair,
        platform_name="win32",
        path=executable,
        lock_timeout=0.01,
    )
    busy = ComponentBusyStatus(ComponentBusyReason.CLIENT_ACTIVE)
    with pytest.raises(JamulusPlatformInstallDeferred) as deferred:
        store.record_installed(pair[0], pair[1], lambda: busy)
    assert deferred.value.status == busy
    assert not store.state_path.exists()

    embedded_server = replace(pair[1], activation_mode=ActivationMode.EMBEDDED_ONLY)
    unapproved = PlatformInstalledJamulusStore(
        JamulusCompatibilityRegistry((pair[0], embedded_server)),
        target=ComponentTarget.WINDOWS_X64,
        root=tmp_path / "unapproved-components",
        platform_name="win32",
        version_probe=lambda _path: "3.12.3",
        canonical_path_provider=lambda: (executable,),
    )
    with pytest.raises(JamulusPlatformError, match="not approved"):
        unapproved.record_installed(pair[0], embedded_server, lambda: None)

    store.record_installed(pair[0], pair[1], lambda: None)
    with InterProcessComponentLock(store.lock_path):
        with pytest.raises(ComponentLockTimeout):
            store.current(JamulusRole.CLIENT)


def test_mismatched_artifact_pair_is_rejected(tmp_path: Path) -> None:
    runtime = _pe_image()
    client, server = _pair(runtime, target=ComponentTarget.WINDOWS_X64)
    different_artifact = replace(
        server.artifact,
        sha256="f" * 64,
    )
    server = replace(server, artifact=different_artifact)
    executable = tmp_path / "Jamulus.exe"
    _write_runtime(executable, runtime, executable=False)
    store = PlatformInstalledJamulusStore(
        JamulusCompatibilityRegistry((client, server)),
        target=ComponentTarget.WINDOWS_X64,
        root=tmp_path / "components",
        platform_name="win32",
        version_probe=lambda _path: "3.12.3",
        canonical_path_provider=lambda: (executable,),
    )

    with pytest.raises(JamulusPlatformError, match="not approved"):
        store.record_installed(client, server, lambda: None)


def test_windows_full_loadable_inventory_is_proved_before_version_probe(
    tmp_path: Path,
) -> None:
    executable_bytes = _pe_image()
    plugin_bytes = bytearray(_pe_image())
    plugin_bytes[-1] ^= 0x55
    plugin_bytes = bytes(plugin_bytes)
    client, server = _pair(
        executable_bytes,
        target=ComponentTarget.WINDOWS_X64,
    )
    plugin = RuntimeFileIdentity(
        relative_path="platforms/qwindows.dll",
        size=len(plugin_bytes),
        sha256=hashlib.sha256(plugin_bytes).hexdigest(),
        executable=False,
    )
    client = replace(client, runtime_files=(*client.runtime_files, plugin))
    server = replace(server, runtime_files=(*server.runtime_files, plugin))
    pair = (client, server)
    install_root = tmp_path / "Program Files" / "Jamulus"
    executable = install_root / "Jamulus.exe"
    plugin_path = install_root / "platforms" / "qwindows.dll"
    _write_runtime(executable, executable_bytes, executable=False)
    _write_runtime(plugin_path, plugin_bytes, executable=False)
    probes: list[Path] = []
    store = PlatformInstalledJamulusStore(
        JamulusCompatibilityRegistry(pair),
        target=ComponentTarget.WINDOWS_X64,
        root=tmp_path / "components",
        platform_name="win32",
        version_probe=lambda path: probes.append(Path(path)) or "3.12.3",
        canonical_path_provider=lambda: (executable,),
        path_trust_verifier=lambda _path, _target: None,
        windows_inventory_provider=lambda entry: tuple(entry.runtime_files),
    )

    result = store.record_installed(client, server, lambda: None)

    assert result.client.trust_policy_verified is True
    assert probes == [executable]

    changed = bytearray(plugin_bytes)
    changed[-1] ^= 0xFF
    plugin_path.write_bytes(changed)
    with pytest.raises(JamulusPlatformError) as tampered:
        store.current(JamulusRole.CLIENT)
    assert not isinstance(tampered.value, JamulusPlatformInstallationNotFound)
    assert probes == [executable]

    plugin_path.write_bytes(plugin_bytes)
    unexpected = install_root / "untrusted.dll"
    _write_runtime(unexpected, _pe_image(), executable=False)
    with pytest.raises(JamulusPlatformError, match="inventory is unexpected"):
        store.current(JamulusRole.CLIENT)
    assert probes == [executable]


def test_linux_package_trust_is_proved_before_and_after_version_probe(
    tmp_path: Path,
) -> None:
    runtime = _elf_image()
    pair = _pair(runtime, target=ComponentTarget.LINUX_X64)
    executable = tmp_path / "simulated-root" / "usr" / "bin" / "jamulus"
    _write_runtime(executable, runtime, executable=True)
    package_calls: list[tuple[Path, str]] = []
    probes: list[Path] = []
    reject_package = True

    def verify_package(path: Path, version: str) -> None:
        package_calls.append((path, version))
        if reject_package:
            raise JamulusPlatformError("package ownership rejected")

    store = PlatformInstalledJamulusStore(
        JamulusCompatibilityRegistry(pair),
        target=ComponentTarget.LINUX_X64,
        root=tmp_path / "components",
        platform_name="linux",
        version_probe=lambda path: probes.append(Path(path)) or "3.12.3",
        canonical_path_provider=lambda: (executable,),
        path_trust_verifier=lambda _path, _target: None,
        linux_package_verifier=verify_package,
    )

    with pytest.raises(JamulusPlatformError, match="package ownership rejected"):
        store.record_installed(pair[0], pair[1], lambda: None)
    assert probes == []
    assert not store.state_path.exists()

    reject_package = False
    result = store.record_installed(pair[0], pair[1], lambda: None)

    assert probes == [executable]
    assert package_calls == [
        (executable, "3.12.3"),
        (executable, "3.12.3"),
        (executable, "3.12.3"),
    ]
    assert result.client.publisher_verified is False
    assert result.client.trust_policy_verified is True


def test_platform_receipt_authorization_runs_after_slow_proof(
    tmp_path: Path,
) -> None:
    runtime = _elf_image()
    pair = _pair(runtime, target=ComponentTarget.LINUX_X64)
    executable = tmp_path / "simulated-root" / "usr" / "bin" / "jamulus"
    _write_runtime(executable, runtime, executable=True)
    events: list[str] = []
    store = PlatformInstalledJamulusStore(
        JamulusCompatibilityRegistry(pair),
        target=ComponentTarget.LINUX_X64,
        root=tmp_path / "components",
        platform_name="linux",
        version_probe=lambda _path: events.append("probe") or "3.12.3",
        canonical_path_provider=lambda: (executable,),
        path_trust_verifier=lambda _path, _target: events.append("path"),
        linux_package_verifier=lambda _path, _version: events.append("package"),
    )

    def reject(
        _client: JamulusCompatibility,
        _server: JamulusCompatibility,
    ) -> None:
        events.append("authorize")
        raise JamulusPlatformError("authorization expired")

    with pytest.raises(JamulusPlatformError, match="authorization expired"):
        store.record_installed(
            pair[0],
            pair[1],
            lambda: None,
            authorization_check=reject,
        )

    assert events == [
        "path",
        "package",
        "probe",
        "path",
        "package",
        "authorize",
    ]
    assert not store.state_path.exists()

    events.clear()
    result = store.record_installed(
        pair[0],
        pair[1],
        lambda: None,
        authorization_check=lambda _client, _server: events.append("authorize"),
    )
    assert events[-1] == "authorize"
    assert result.client.trust_policy_verified is True
    assert store.state_path.exists()


def test_installed_version_probe_environment_removes_loader_injection() -> None:
    environment = _sanitized_loader_environment(
        {
            "HOME": "/home/musician",
            "LD_PRELOAD": "/tmp/injected.so",
            "LD_LIBRARY_PATH": "/tmp/libraries",
            "QT_PLUGIN_PATH": "/tmp/plugins",
            "QT_QPA_PLATFORM_PLUGIN_PATH": "/tmp/platforms",
            "WEBJAM_DIAGNOSTIC": "safe",
            "PATH": "/tmp/untrusted",
        },
        platform_name="linux",
        executable=Path("/usr/bin/jamulus"),
    )

    assert environment == {
        "HOME": "/home/musician",
        "WEBJAM_DIAGNOSTIC": "safe",
        "PATH": "/usr/bin:/bin",
    }


def test_linux_dpkg_verifier_accepts_real_tab_delimited_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], dict[str, str]]] = []
    responses = iter(
        (
            b"jamulus: /usr/bin/jamulus\n",
            b"ii \t3.12.3\tamd64\n",
        )
    )

    monkeypatch.setattr(
        platform_module,
        "_verify_linux_system_tool",
        lambda _path: None,
    )

    def run(arguments: list[str], **kwargs) -> subprocess.CompletedProcess[bytes]:
        calls.append((list(arguments), dict(kwargs["env"])))
        return subprocess.CompletedProcess(arguments, 0, next(responses), b"")

    monkeypatch.setattr(platform_module.subprocess, "run", run)

    platform_module._verify_linux_dpkg_install(
        Path("/usr/bin/jamulus"),
        "3.12.3",
    )

    assert calls[0][0] == [
        "/usr/bin/dpkg-query",
        "-S",
        "/usr/bin/jamulus",
    ]
    assert calls[1][0][-1] == "jamulus"
    assert calls[0][1] == calls[1][1] == {
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": "/usr/bin:/bin",
    }


def test_default_installed_version_probe_parses_real_jamulus_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}
    monkeypatch.setenv("LD_PRELOAD", "/tmp/injected.so")
    monkeypatch.setenv("QT_PLUGIN_PATH", "/tmp/plugins")

    def run(arguments: list[str], **kwargs) -> subprocess.CompletedProcess[bytes]:
        observed["arguments"] = list(arguments)
        observed["environment"] = dict(kwargs["env"])
        observed["cwd"] = kwargs["cwd"]
        return subprocess.CompletedProcess(
            arguments,
            0,
            b"Jamulus version 3.12.3\n",
            b"",
        )

    monkeypatch.setattr(platform_module.subprocess, "run", run)

    version = platform_module._sanitized_installed_version_probe(
        Path("/usr/bin/jamulus"),
        platform_name="linux",
    )

    assert version == "3.12.3"
    assert observed["arguments"] == ["/usr/bin/jamulus", "--version"]
    assert observed["cwd"] == "/"
    environment = observed["environment"]
    assert isinstance(environment, dict)
    assert "LD_PRELOAD" not in environment
    assert "QT_PLUGIN_PATH" not in environment
    assert environment["PATH"] == "/usr/bin:/bin"
