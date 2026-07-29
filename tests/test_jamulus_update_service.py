from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

import services.jamulus_component_update as update_module
from core.component_catalog import VerifiedComponentCatalog
from core.component_download import (
    ComponentDownloadCancelled,
    DownloadProgress,
    OpenedDownload,
    VerifiedDownload,
)
from core.component_store import ComponentBusyReason, ComponentBusyStatus
from core.component_lock import InterProcessComponentLock
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
    SourceProvenance,
    WebJamVersionRange,
)
from core.jamulus_update_state import JamulusUpdateState
from services.jamulus_component_platform import (
    JamulusLicenseApprovalRequired,
    JamulusPlatformError,
    JamulusPlatformInstallationNotFound,
    MacOSJamulusComponentStore,
    open_platform_jamulus_installer,
    platform_component_target,
)
from services.jamulus_component_update import (
    CatalogFetchError,
    JamulusComponentUpdateService,
    SignedCatalogFetcher,
)


NOW = datetime(2026, 7, 28, 19, 0, tzinfo=timezone.utc)


def _artifact(
    data: bytes,
    target: ComponentTarget,
    *,
    version: str = "3.12.4",
) -> ArtifactIdentity:
    suffix = {
        ComponentTarget.WINDOWS_X64: "win.exe",
        ComponentTarget.LINUX_X64: "ubuntu_amd64.deb",
        ComponentTarget.MACOS_ARM64: "mac.dmg",
        ComponentTarget.MACOS_X64: "mac.dmg",
    }[target]
    kind = {
        ComponentTarget.WINDOWS_X64: ArtifactKind.INSTALLER,
        ComponentTarget.LINUX_X64: ArtifactKind.PACKAGE,
        ComponentTarget.MACOS_ARM64: ArtifactKind.DISK_IMAGE,
        ComponentTarget.MACOS_X64: ArtifactKind.DISK_IMAGE,
    }[target]
    return ArtifactIdentity(
        url=(
            "https://github.com/jamulussoftware/jamulus/releases/"
            f"download/r{version.replace('.', '_')}/"
            f"jamulus_{version}_{suffix}"
        ),
        filename=f"jamulus_{version}_{suffix}",
        size=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        kind=kind,
    )


def _pair(
    data: bytes = b"approved Jamulus installer",
    *,
    target: ComponentTarget = ComponentTarget.WINDOWS_X64,
    version: str = "3.12.4",
) -> tuple[JamulusCompatibility, JamulusCompatibility]:
    artifact = _artifact(data, target, version=version)
    source = JamulusSourceIdentity(
        repository="jamulussoftware/jamulus",
        tag=f"r{version.replace('.', '_')}",
        commit="c" * 40,
        provenance=SourceProvenance.OFFICIAL_RELEASE,
    )
    legal = LegalInventory(
        license_files=("licenses/JAMULUS_COPYING-r3_12_3.txt",),
        notice_files=("THIRD_PARTY_NOTICES.md",),
        source_offer="THIRD_PARTY_NOTICES.md",
    )
    common = {
        "component_id": "jamulus",
        "target": target,
        "version": version,
        "variant": "official",
        "source": source,
        "artifact": artifact,
        "runtime_files": (),
        "executable_relative_path": "",
        "webjam_range": WebJamVersionRange("0.22.0", "0.22.999"),
        "legal": legal,
        "activation_mode": ActivationMode.PLATFORM_APPROVAL,
        "publisher": "Catalog-approved official Jamulus release",
    }
    client = JamulusCompatibility(
        role=JamulusRole.CLIENT,
        capabilities=JamulusCapabilities(
            frozenset(
                {
                    "audio-client",
                    "json-rpc-client",
                    "native-gui",
                    "webjam-route-profile",
                }
            )
        ),
        **common,
    )
    server = JamulusCompatibility(
        role=JamulusRole.SERVER,
        capabilities=JamulusCapabilities(
            frozenset({"audio-server", "json-rpc-server", "recording"})
        ),
        **common,
    )
    return client, server


def _catalog(
    client: JamulusCompatibility,
    server: JamulusCompatibility,
) -> VerifiedComponentCatalog:
    return VerifiedComponentCatalog(
        schema=1,
        sequence=14,
        issued_at=NOW,
        expires_at=NOW + timedelta(days=20),
        webjam_version="0.22.0",
        components=(client, server),
        payload_sha256="d" * 64,
        signer_key_id="test-key",
        signer_fingerprint_sha256="e" * 64,
    )


class _Fetcher:
    def __init__(self, value: bytes | Exception = b'{"signed":true}') -> None:
        self.value = value
        self.calls = 0

    def fetch(self, _url, *, cancellation):
        self.calls += 1
        cancellation.raise_if_cancelled()
        if isinstance(self.value, Exception):
            raise self.value
        return self.value


class _Verifier:
    def __init__(self, catalog: VerifiedComponentCatalog) -> None:
        self.catalog = catalog
        self.calls: list[bytes] = []

    def verify(self, envelope, *, webjam_version):
        assert webjam_version == "0.22.0"
        self.calls.append(bytes(envelope))
        return self.catalog


class _Downloader:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.calls = 0

    def download(
        self,
        artifact,
        *,
        destination_directory,
        cancellation,
        progress,
    ):
        self.calls += 1
        cancellation.raise_if_cancelled()
        destination = Path(destination_directory)
        destination.mkdir(parents=True, exist_ok=True)
        path = destination / artifact.filename
        path.write_bytes(self.data)
        progress(DownloadProgress(len(self.data), len(self.data)))
        return VerifiedDownload(
            path=path,
            size=len(self.data),
            sha256=hashlib.sha256(self.data).hexdigest(),
            redirect_count=0,
        )


class _BlockingDownloader(_Downloader):
    def __init__(self, data: bytes) -> None:
        super().__init__(data)
        self.started = threading.Event()

    def download(self, artifact, **kwargs):
        self.calls += 1
        cancellation = kwargs["cancellation"]
        self.started.set()
        while not cancellation.cancelled:
            time.sleep(0.005)
        raise ComponentDownloadCancelled("cancelled")


def _wait(service: JamulusComponentUpdateService, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while service.operation_in_progress and time.monotonic() < deadline:
        time.sleep(0.005)
    assert not service.operation_in_progress


def _service(
    tmp_path: Path,
    *,
    target: ComponentTarget = ComponentTarget.WINDOWS_X64,
    fetcher: _Fetcher | None = None,
    verifier: _Verifier | None = None,
    downloader: _Downloader | None = None,
    busy_check=lambda: None,
    launcher=None,
    automatic_download: bool = False,
    now=None,
    installed_store=None,
) -> tuple[JamulusComponentUpdateService, bytes]:
    data = b"approved Jamulus installer"
    client, server = _pair(data, target=target)
    service = JamulusComponentUpdateService(
        webjam_version="0.22.0",
        target=target,
        root=tmp_path / "Component Store With Spaces",
        catalog_fetcher=fetcher or _Fetcher(),
        catalog_verifier=verifier or _Verifier(_catalog(client, server)),
        downloader=downloader or _Downloader(data),
        busy_check=busy_check,
        active_version_provider=lambda: "3.12.2",
        platform_approval_launcher=launcher or (lambda _path, _entry: True),
        automatic_download=automatic_download,
        now=now,
        installed_store=installed_store,
    )
    return service, data


def test_license_text_uses_frozen_third_party_license_layout(
    tmp_path,
    monkeypatch,
):
    frozen_root = tmp_path / "WebJam.app" / "Contents" / "Frameworks" / "_internal"
    packaged_license = (
        frozen_root / "THIRD_PARTY_LICENSES" / "JAMULUS_COPYING-r3_12_3.txt"
    )
    packaged_license.parent.mkdir(parents=True)
    packaged_license.write_text("frozen Jamulus license\n", encoding="utf-8")
    legacy_path = frozen_root / "licenses" / "JAMULUS_COPYING-r3_12_3.txt"
    legacy_path.parent.mkdir()
    legacy_path.write_text("wrong legacy location\n", encoding="utf-8")

    monkeypatch.setattr(
        update_module,
        "__file__",
        str(tmp_path / "frozen" / "services" / "jamulus_component_update.py"),
    )
    monkeypatch.setattr(sys, "_MEIPASS", str(frozen_root), raising=False)
    service, _data = _service(tmp_path)

    assert service.license_text() == "frozen Jamulus license\n"


def test_license_text_retains_source_tree_layout(tmp_path, monkeypatch):
    source_root = tmp_path / "source checkout"
    module_path = source_root / "services" / "jamulus_component_update.py"
    source_license = source_root / "licenses" / "JAMULUS_COPYING-r3_12_3.txt"
    source_license.parent.mkdir(parents=True)
    source_license.write_text("source-tree Jamulus license\n", encoding="utf-8")

    monkeypatch.setattr(update_module, "__file__", str(module_path))
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    service, _data = _service(tmp_path)

    assert service.license_text() == "source-tree Jamulus license\n"


def test_check_download_and_platform_handoff_are_truthful_and_redacted(tmp_path):
    opened: list[tuple[Path, str]] = []
    service, _data = _service(
        tmp_path,
        launcher=lambda path, entry: opened.append((path, entry.version)) or True,
    )
    assert service.check_now()
    _wait(service)
    assert service.snapshot.state is JamulusUpdateState.AVAILABLE
    assert service.snapshot.can_download

    assert service.download_available()
    _wait(service)
    assert service.snapshot.state is JamulusUpdateState.READY
    assert service.snapshot.can_approve

    assert service.approve_ready()
    _wait(service)
    snapshot = service.snapshot
    assert snapshot.state is JamulusUpdateState.DEFERRED
    assert snapshot.reason_code == "finish-platform-installer"
    assert not snapshot.can_approve
    assert snapshot.can_activate
    assert snapshot.activate_label == "Verify installation"
    assert opened and opened[0][1] == "3.12.4"

    diagnostics = service.diagnostics()
    serialized = json.dumps(diagnostics)
    assert diagnostics["catalog"]["status"] == "verified"
    assert diagnostics["catalog"]["sequence"] == 14
    assert "Component Store With Spaces" not in serialized
    assert str(tmp_path) not in serialized


def test_expired_catalog_is_revoked_immediately_before_download(tmp_path):
    clock = [NOW + timedelta(hours=1)]
    downloader = _Downloader(b"approved Jamulus installer")
    service, _data = _service(
        tmp_path,
        downloader=downloader,
        now=lambda: clock[0],
    )
    assert service.check_now()
    _wait(service)
    assert service.snapshot.can_download

    clock[0] = NOW + timedelta(days=20)
    assert service.download_available()
    _wait(service)

    assert downloader.calls == 0
    assert service.snapshot.state is JamulusUpdateState.FAILED
    assert service.snapshot.reason_code == "catalog-authorization-stale"
    assert not service.snapshot.available_version
    assert not service.snapshot.can_download
    assert not service.snapshot.can_approve
    assert service.diagnostics()["catalog"]["status"] == "not-verified"


def test_higher_catalog_sequence_revokes_download_authorization(tmp_path):
    downloader = _Downloader(b"approved Jamulus installer")
    service, _data = _service(
        tmp_path,
        downloader=downloader,
        now=lambda: NOW + timedelta(hours=1),
    )
    assert service.check_now()
    _wait(service)
    service.sequence_store.compare_and_record(15, "f" * 64)

    assert service.download_available()
    _wait(service)

    assert downloader.calls == 0
    assert service.snapshot.state is JamulusUpdateState.FAILED
    assert service.snapshot.reason_code == "catalog-authorization-stale"
    assert not service.snapshot.can_download
    assert not service.snapshot.can_approve
    assert service.diagnostics()["catalog"]["status"] == "not-verified"


def test_expired_catalog_is_revoked_immediately_before_platform_handoff(
    tmp_path,
):
    clock = [NOW + timedelta(hours=1)]
    opened: list[Path] = []
    service, _data = _service(
        tmp_path,
        launcher=lambda path, _entry: opened.append(path) or True,
        now=lambda: clock[0],
    )
    assert service.check_now()
    _wait(service)
    assert service.download_available()
    _wait(service)
    assert service.snapshot.state is JamulusUpdateState.READY

    clock[0] = NOW + timedelta(days=20)
    assert service.approve_ready()
    _wait(service)

    assert opened == []
    assert service.snapshot.state is JamulusUpdateState.FAILED
    assert service.snapshot.reason_code == "catalog-authorization-stale"
    assert not service.snapshot.can_approve
    assert service.diagnostics()["catalog"]["status"] == "not-verified"


def test_progress_publication_uses_one_verified_previous_snapshot(tmp_path):
    class Previous:
        version = "3.12.3"

    class CountingPlatformStore:
        def __init__(self) -> None:
            self.previous_calls = 0

        def previous(self):
            self.previous_calls += 1
            return Previous()

    service, _data = _service(
        tmp_path,
        now=lambda: NOW + timedelta(hours=1),
    )
    assert service.check_now()
    _wait(service)
    platform_store = CountingPlatformStore()
    service._platform_store = platform_store

    assert service.download_available()
    _wait(service)

    assert platform_store.previous_calls == 1
    assert service.snapshot.state is JamulusUpdateState.READY
    assert service.snapshot.previous_version == "3.12.3"
    assert service.snapshot.can_rollback


def test_automatic_download_and_busy_install_defer_until_clean_stop(tmp_path):
    busy = []
    opened = []
    service, _data = _service(
        tmp_path,
        busy_check=lambda: busy[0] if busy else None,
        launcher=lambda path, entry: opened.append((path, entry)) or True,
        automatic_download=True,
    )
    assert service.start_automatic_check()
    _wait(service)
    assert service.snapshot.state is JamulusUpdateState.READY

    busy.append(
        ComponentBusyStatus(
            ComponentBusyReason.RECORDING_ACTIVE,
            "Recording is active.",
        )
    )
    assert service.approve_ready()
    _wait(service)
    assert service.snapshot.state is JamulusUpdateState.DEFERRED
    assert service.snapshot.reason_code == ComponentBusyReason.RECORDING_ACTIVE.value
    assert service.snapshot.restart_when_idle
    assert service.snapshot.can_activate
    assert not opened

    busy.clear()
    assert service.activate_when_idle()
    _wait(service)
    assert opened
    assert service.snapshot.reason_code == "finish-platform-installer"


class _AcceptingInstalledStore:
    def __init__(
        self,
        client: JamulusCompatibility,
        server: JamulusCompatibility,
    ) -> None:
        self.client = client
        self.server = server
        self.recorded = False
        self.record_calls = 0

    def record_installed(
        self,
        client,
        server,
        busy_check,
        *,
        authorization_check=None,
    ):
        assert client == self.client
        assert server == self.server
        assert busy_check() is None
        if authorization_check is not None:
            authorization_check(client, server)
        self.record_calls += 1
        self.recorded = True
        return SimpleNamespace(
            client=SimpleNamespace(version=client.version),
            server=SimpleNamespace(version=server.version),
        )

    def current(self, role):
        if not self.recorded:
            return None
        entry = self.client if JamulusRole(role) is JamulusRole.CLIENT else self.server
        return SimpleNamespace(
            entry=entry,
            executable_path=Path("/approved/Jamulus.exe"),
            content_verified=True,
            version_verified=True,
            architecture_verified=True,
            publisher_verified=False,
            trust_policy_verified=True,
        )


def test_platform_install_is_reverified_before_bridge_can_use_it(tmp_path):
    data = b"approved Jamulus installer"
    client, server = _pair(data)
    installed = _AcceptingInstalledStore(client, server)
    service, _data = _service(tmp_path, installed_store=installed)

    assert service.check_now()
    _wait(service)
    assert service.download_available()
    _wait(service)
    assert service.approve_ready()
    _wait(service)
    assert service.snapshot.reason_code == "finish-platform-installer"

    assert service.activate_when_idle()
    _wait(service)

    assert installed.record_calls == 1
    assert service.snapshot.state is JamulusUpdateState.UP_TO_DATE
    managed = service.managed_client_component()
    assert managed is not None
    assert managed.entry == client
    assert managed.publisher_verified is False
    assert managed.trust_policy_verified is True
    assert managed.fully_verified


def test_missing_platform_install_stays_ready_with_clear_recovery(tmp_path):
    data = b"approved Jamulus installer"
    client, server = _pair(data)

    class MissingInstalledStore(_AcceptingInstalledStore):
        def record_installed(self, *_args, **_kwargs):
            raise JamulusPlatformInstallationNotFound(
                "no approved installation was found"
            )

    service, _data = _service(
        tmp_path,
        installed_store=MissingInstalledStore(client, server),
    )
    assert service.check_now()
    _wait(service)
    assert service.download_available()
    _wait(service)

    assert service.activate_when_idle()
    _wait(service)

    assert service.snapshot.state is JamulusUpdateState.READY
    assert service.snapshot.reason_code == "platform-install-not-found"
    assert service.snapshot.can_approve
    assert service.snapshot.can_activate
    assert service.snapshot.activate_label == "Verify installation"
    assert "Finish" in service.snapshot.message


def test_catalog_supersession_during_platform_proof_writes_no_receipt(tmp_path):
    data = b"approved Jamulus installer"
    client, server = _pair(data)

    class SupersedingInstalledStore(_AcceptingInstalledStore):
        service: JamulusComponentUpdateService | None = None

        def record_installed(
            self,
            client,
            server,
            busy_check,
            *,
            authorization_check=None,
        ):
            assert self.service is not None
            self.service.sequence_store.compare_and_record(15, "f" * 64)
            return super().record_installed(
                client,
                server,
                busy_check,
                authorization_check=authorization_check,
            )

    installed = SupersedingInstalledStore(client, server)
    service, _data = _service(tmp_path, installed_store=installed)
    installed.service = service
    assert service.check_now()
    _wait(service)
    assert service.download_available()
    _wait(service)

    assert service.activate_when_idle()
    _wait(service)

    assert installed.recorded is False
    assert service.snapshot.state is JamulusUpdateState.FAILED
    assert service.snapshot.reason_code == "catalog-authorization-stale"
    assert service.diagnostics()["catalog"]["status"] == "not-verified"


def test_runtime_lease_in_another_process_defers_installer_handoff(tmp_path):
    opened: list[Path] = []
    service, _data = _service(
        tmp_path,
        launcher=lambda path, _entry: opened.append(path) or True,
    )
    assert service.check_now()
    _wait(service)
    assert service.download_available()
    _wait(service)

    with InterProcessComponentLock(service.runtime_lock_path, timeout=0.0):
        assert service.approve_ready()
        _wait(service)

    assert opened == []
    assert service.snapshot.state is JamulusUpdateState.DEFERRED
    assert (
        service.snapshot.reason_code
        == ComponentBusyReason.ANOTHER_INSTANCE_ACTIVE.value
    )
    assert service.snapshot.restart_when_idle


def test_automatic_download_never_consumes_live_session_bandwidth(tmp_path):
    downloader = _Downloader(b"approved Jamulus installer")
    service, _ = _service(
        tmp_path,
        busy_check=lambda: ComponentBusyStatus(
            ComponentBusyReason.CLIENT_ACTIVE,
            "Client is active.",
        ),
        downloader=downloader,
        automatic_download=True,
    )
    assert service.start_automatic_check()
    _wait(service)
    assert service.snapshot.state is JamulusUpdateState.AVAILABLE
    assert service.snapshot.reason_code == "automatic-download-deferred"
    assert service.snapshot.can_download
    assert downloader.calls == 0


def test_single_flight_cancel_preserves_current_version(tmp_path):
    data = b"approved Jamulus installer"
    downloader = _BlockingDownloader(data)
    service, _ = _service(tmp_path, downloader=downloader)
    assert service.check_now()
    _wait(service)
    assert service.download_available()
    assert downloader.started.wait(1)
    assert not service.check_now()
    service.cancel()
    _wait(service)
    assert service.snapshot.state is JamulusUpdateState.CANCELLED
    assert service.snapshot.active_version == "3.12.2"
    assert not list((tmp_path / "Component Store With Spaces").rglob("*.part"))


def test_network_failure_uses_verified_cache_but_programming_error_does_not(
    tmp_path,
):
    first_fetcher = _Fetcher(b"cached signed envelope")
    client, server = _pair()
    verifier = _Verifier(_catalog(client, server))
    first, _ = _service(
        tmp_path,
        fetcher=first_fetcher,
        verifier=verifier,
    )
    assert first.check_now()
    _wait(first)
    assert first.snapshot.state is JamulusUpdateState.AVAILABLE

    offline, _ = _service(
        tmp_path,
        fetcher=_Fetcher(CatalogFetchError("offline")),
        verifier=verifier,
    )
    assert offline.check_now()
    _wait(offline)
    assert offline.snapshot.state is JamulusUpdateState.AVAILABLE
    assert verifier.calls[-1] == b"cached signed envelope"

    broken, _ = _service(
        tmp_path / "broken",
        fetcher=_Fetcher(RuntimeError("secret local path /Users/name")),
        verifier=verifier,
    )
    assert broken.check_now()
    _wait(broken)
    assert broken.snapshot.state is JamulusUpdateState.FAILED
    assert broken.snapshot.reason_code == "update-failed"
    assert "secret" not in broken.snapshot.message


class _Body(io.BytesIO):
    def __init__(self, data: bytes, content_length: int) -> None:
        super().__init__(data)
        self.status = 200
        self.headers = {
            "Content-Length": str(content_length),
            "Content-Encoding": "identity",
        }


class _Transport:
    def __init__(self, data: bytes, content_length: int) -> None:
        self.data = data
        self.content_length = content_length

    def open(self, url, *, policy, cancellation):
        policy.validate_source(url)
        cancellation.raise_if_cancelled()
        return OpenedDownload(
            body=_Body(self.data, self.content_length),
            redirect_count=0,
        )


def test_catalog_fetcher_rejects_incomplete_content_length():
    from core.component_download import DownloadCancellation

    fetcher = SignedCatalogFetcher(transport=_Transport(b"{}", content_length=10))
    with pytest.raises(CatalogFetchError, match="incomplete"):
        fetcher.fetch(
            "https://github.com/rupret007/webjam/releases/download/x/y.json",
            cancellation=DownloadCancellation(),
        )


@pytest.mark.parametrize(
    ("platform_name", "target", "kind"),
    [
        ("win32", ComponentTarget.WINDOWS_X64, ArtifactKind.INSTALLER),
        ("linux", ComponentTarget.LINUX_X64, ArtifactKind.PACKAGE),
    ],
)
def test_platform_installer_handoff_is_explicit_and_never_uses_sudo(
    tmp_path,
    platform_name,
    target,
    kind,
):
    data = b"tiny approved package"
    client, _server = _pair(data, target=target)
    assert client.artifact.kind is kind
    path = tmp_path / client.artifact.filename
    path.write_bytes(data)
    started: list[str] = []
    commands: list[list[str]] = []

    def runner(arguments, *, timeout):
        commands.append(arguments)
        return subprocess.CompletedProcess(arguments, 0, b"", b"")

    assert open_platform_jamulus_installer(
        path,
        client,
        platform_name=platform_name,
        startfile=lambda value: started.append(value),
        command_runner=runner,
    )
    if platform_name == "win32":
        assert started == [str(path)]
        assert not commands
    else:
        assert commands == [["/usr/bin/xdg-open", str(path)]]
        assert "sudo" not in json.dumps(commands)


def test_platform_target_mapping_rejects_unknown_architectures():
    assert (
        platform_component_target(platform_name="darwin", machine="arm64")
        is ComponentTarget.MACOS_ARM64
    )
    assert (
        platform_component_target(platform_name="win32", machine="AMD64")
        is ComponentTarget.WINDOWS_X64
    )
    assert (
        platform_component_target(platform_name="linux", machine="x86_64")
        is ComponentTarget.LINUX_X64
    )
    with pytest.raises(JamulusPlatformError, match="unsupported"):
        platform_component_target(platform_name="darwin", machine="ppc")


class _NoopMacVerifier:
    def verify(self, *args, **kwargs):
        raise AssertionError("bundle verification must not run before acceptance")


class _AcceptingMacVerifier:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def verify(self, bundle, *, role, version, target):
        self.calls.append((Path(bundle).name, role.value, version))
        assert target is ComponentTarget.MACOS_ARM64
        return object()


class _FakeMacCommands:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], bytes | None]] = []
        self.fail_next_copy = False

    def __call__(self, arguments, *, input=None, timeout):
        args = list(arguments)
        self.calls.append((args, input))
        if args[:2] == ["/usr/bin/hdiutil", "attach"]:
            mount = Path(args[args.index("-mountpoint") + 1])
            background = mount / ".background"
            background.mkdir()
            (background / "installerbackground.png").write_bytes(b"png")
            (mount / ".DS_Store").write_bytes(b"finder")
            (mount / "Applications").symlink_to("/Applications")
            for app, executable in (
                ("Jamulus.app", "Jamulus"),
                ("JamulusServer.app", "JamulusServer"),
            ):
                binary = mount / app / "Contents" / "MacOS" / executable
                binary.parent.mkdir(parents=True)
                binary.write_bytes(b"official binary")
                binary.chmod(0o755)
            return subprocess.CompletedProcess(args, 0, b"", b"")
        if args[:2] == ["/usr/bin/ditto", "--rsrc"]:
            if self.fail_next_copy:
                self.fail_next_copy = False
                return subprocess.CompletedProcess(args, 1, b"", b"copy failed")
            shutil.copytree(args[-2], args[-1], symlinks=True)
            return subprocess.CompletedProcess(args, 0, b"", b"")
        if args[:2] == ["/usr/bin/xattr", "-w"]:
            return subprocess.CompletedProcess(args, 0, b"", b"")
        if args[:2] == ["/usr/bin/hdiutil", "detach"]:
            mount = Path(args[-1])
            if mount.exists():
                shutil.rmtree(mount)
            return subprocess.CompletedProcess(args, 0, b"", b"")
        raise AssertionError(f"unexpected command: {args}")


def test_macos_license_refusal_mounts_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    commands = []

    def runner(arguments, **kwargs):
        commands.append(arguments)
        return subprocess.CompletedProcess(arguments, 0, b"", b"")

    store = MacOSJamulusComponentStore(
        root=tmp_path,
        verifier=_NoopMacVerifier(),
        command_runner=runner,
    )
    registry_client, registry_server = _pair(target=ComponentTarget.MACOS_ARM64)
    store.registry = type(store.registry)((registry_client, registry_server))
    with pytest.raises(JamulusLicenseApprovalRequired):
        store.install_from_dmg(
            client_entry=registry_client,
            server_entry=registry_server,
            dmg_path=tmp_path / "missing.dmg",
            license_accepted=False,
            busy_check=lambda: None,
        )
    assert commands == []


def test_macos_store_installs_atomically_keeps_previous_and_rolls_back(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(sys, "platform", "darwin")
    old_data = b"approved Jamulus 3.12.3 disk image"
    new_data = b"approved Jamulus 3.12.4 disk image"
    old_client, old_server = _pair(
        old_data,
        target=ComponentTarget.MACOS_ARM64,
        version="3.12.3",
    )
    new_client, new_server = _pair(
        new_data,
        target=ComponentTarget.MACOS_ARM64,
        version="3.12.4",
    )
    commands = _FakeMacCommands()
    verifier = _AcceptingMacVerifier()
    store = MacOSJamulusComponentStore(
        JamulusCompatibilityRegistry((old_client, old_server, new_client, new_server)),
        root=tmp_path,
        verifier=verifier,
        command_runner=commands,
    )
    old_dmg = tmp_path / old_client.artifact.filename
    old_dmg.write_bytes(old_data)
    first = store.install_from_dmg(
        client_entry=old_client,
        server_entry=old_server,
        dmg_path=old_dmg,
        license_accepted=True,
        busy_check=lambda: None,
    )
    assert first.current.version == "3.12.3"
    assert first.previous is None

    new_dmg = tmp_path / new_client.artifact.filename
    new_dmg.write_bytes(new_data)
    second = store.install_from_dmg(
        client_entry=new_client,
        server_entry=new_server,
        dmg_path=new_dmg,
        license_accepted=True,
        busy_check=lambda: None,
    )
    assert second.current.version == "3.12.4"
    assert second.previous.version == "3.12.3"
    assert store.current().version == "3.12.4"
    assert store.previous().version == "3.12.3"

    restored = store.rollback(busy_check=lambda: None)
    assert restored.current.version == "3.12.3"
    assert restored.previous.version == "3.12.4"
    command_text = json.dumps([item[0] for item in commands.calls])
    assert "sudo" not in command_text
    assert "spctl --master-disable" not in command_text
    assert '"-d", "com.apple.quarantine"' not in command_text
    attach_inputs = [
        supplied
        for args, supplied in commands.calls
        if args[:2] == ["/usr/bin/hdiutil", "attach"]
    ]
    assert attach_inputs == [b"Y\n", b"Y\n"]


def test_macos_failed_copy_preserves_last_known_good(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    old_data = b"old dmg"
    new_data = b"new dmg"
    old_client, old_server = _pair(
        old_data,
        target=ComponentTarget.MACOS_ARM64,
        version="3.12.3",
    )
    new_client, new_server = _pair(
        new_data,
        target=ComponentTarget.MACOS_ARM64,
        version="3.12.4",
    )
    commands = _FakeMacCommands()
    store = MacOSJamulusComponentStore(
        JamulusCompatibilityRegistry((old_client, old_server, new_client, new_server)),
        root=tmp_path,
        verifier=_AcceptingMacVerifier(),
        command_runner=commands,
    )
    old_dmg = tmp_path / old_client.artifact.filename
    old_dmg.write_bytes(old_data)
    store.install_from_dmg(
        client_entry=old_client,
        server_entry=old_server,
        dmg_path=old_dmg,
        license_accepted=True,
        busy_check=lambda: None,
    )

    new_dmg = tmp_path / new_client.artifact.filename
    new_dmg.write_bytes(new_data)
    commands.fail_next_copy = True
    with pytest.raises(JamulusPlatformError, match="staged"):
        store.install_from_dmg(
            client_entry=new_client,
            server_entry=new_server,
            dmg_path=new_dmg,
            license_accepted=True,
            busy_check=lambda: None,
        )
    assert store.current().version == "3.12.3"
    assert not list(store.staging_root.glob("install-*"))


def test_macos_current_lookup_does_not_mutate_or_auto_rollback(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(sys, "platform", "darwin")
    old_data = b"approved Jamulus 3.12.3 disk image"
    new_data = b"approved Jamulus 3.12.4 disk image"
    old_client, old_server = _pair(
        old_data,
        target=ComponentTarget.MACOS_ARM64,
        version="3.12.3",
    )
    new_client, new_server = _pair(
        new_data,
        target=ComponentTarget.MACOS_ARM64,
        version="3.12.4",
    )
    store = MacOSJamulusComponentStore(
        JamulusCompatibilityRegistry(
            (old_client, old_server, new_client, new_server)
        ),
        root=tmp_path,
        verifier=_AcceptingMacVerifier(),
        command_runner=_FakeMacCommands(),
    )
    for client, server, data in (
        (old_client, old_server, old_data),
        (new_client, new_server, new_data),
    ):
        dmg = tmp_path / client.artifact.filename
        dmg.write_bytes(data)
        installed = store.install_from_dmg(
            client_entry=client,
            server_entry=server,
            dmg_path=dmg,
            license_accepted=True,
            busy_check=lambda: None,
        )

    shutil.rmtree(installed.current.client_path.parents[2])
    state_before = store.state_path.read_bytes()

    with pytest.raises(JamulusPlatformError, match="unexpected files"):
        store.current()

    assert store.state_path.read_bytes() == state_before
    assert store.previous().version == "3.12.3"

    restored = store.rollback(busy_check=lambda: None)
    assert restored.current.version == "3.12.3"
    assert restored.previous is None
    assert store.current().version == "3.12.3"
    assert store.previous() is None


def test_macos_point_of_use_authorization_precedes_pointer_write(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(sys, "platform", "darwin")
    data = b"approved Jamulus 3.12.3 disk image"
    client, server = _pair(
        data,
        target=ComponentTarget.MACOS_ARM64,
        version="3.12.3",
    )
    commands = _FakeMacCommands()
    store = MacOSJamulusComponentStore(
        JamulusCompatibilityRegistry((client, server)),
        root=tmp_path,
        verifier=_AcceptingMacVerifier(),
        command_runner=commands,
    )
    dmg = tmp_path / client.artifact.filename
    dmg.write_bytes(data)
    authorizations: list[str] = []

    def reject(_client, _server):
        authorizations.append("reject")
        raise JamulusPlatformError("authorization expired")

    with pytest.raises(JamulusPlatformError, match="authorization expired"):
        store.install_from_dmg(
            client_entry=client,
            server_entry=server,
            dmg_path=dmg,
            license_accepted=True,
            busy_check=lambda: None,
            authorization_check=reject,
        )

    assert authorizations == ["reject"]
    assert not store.state_path.exists()

    installed = store.install_from_dmg(
        client_entry=client,
        server_entry=server,
        dmg_path=dmg,
        license_accepted=True,
        busy_check=lambda: None,
        authorization_check=lambda _client, _server: authorizations.append(
            "accept"
        ),
    )
    assert installed.current.version == "3.12.3"
    assert authorizations == ["reject", "accept"]
    assert sum(
        args[:2] == ["/usr/bin/hdiutil", "attach"]
        for args, _input in commands.calls
    ) == 1


def test_corrupt_macos_pointer_is_bounded_platform_error(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    store = MacOSJamulusComponentStore(
        root=tmp_path,
        verifier=_NoopMacVerifier(),
    )
    store.state_path.write_text(
        json.dumps(
            {
                "schema": 1,
                "current": {
                    "version": "not-a-version",
                    "target": "bad-target",
                    "artifact_sha256": "a" * 64,
                },
                "previous": None,
            }
        )
    )
    with pytest.raises(JamulusPlatformError, match="target|version"):
        store.current()
