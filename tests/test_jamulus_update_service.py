from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
import plistlib
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
    ComponentDownloadError,
    ComponentSecureConnectionError,
    ComponentTlsTrustError,
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
    RuntimeFileIdentity,
    SourceProvenance,
    WebJamVersionRange,
    official_jamulus_compatibility_registry,
)
from core.jamulus_update_state import JamulusUpdateState
from core.jamulus_component_resolver import (
    ComponentOrigin,
    ExternalComponentCandidate,
)
from services.jamulus_component_platform import (
    JamulusLicenseApprovalRequired,
    JamulusPlatformError,
    JamulusPlatformInstallationNotFound,
    MacOSBundleVerifier,
    MacOSExecutionContract,
    MacOSExecutionContractKind,
    MacOSJamulusComponentStore,
    VerifiedMacBundle,
    macos_integrated_runtime_contract_allows,
    macos_integrated_runtime_entry_is_eligible,
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


def _integrated_macos_pair(
    *,
    target: ComponentTarget = ComponentTarget.MACOS_ARM64,
) -> tuple[JamulusCompatibility, JamulusCompatibility]:
    client, server = _pair(target=target, version="3.12.4")
    artifact = replace(client.artifact, kind=ArtifactKind.ARCHIVE)

    def integrated(
        entry: JamulusCompatibility,
        executable: str,
        capabilities: frozenset[str],
    ) -> JamulusCompatibility:
        return replace(
            entry,
            variant="webjam-integrated",
            artifact=artifact,
            runtime_files=(
                RuntimeFileIdentity(
                    relative_path=executable,
                    size=20,
                    sha256="a" * 64,
                    executable=True,
                ),
            ),
            executable_relative_path=executable,
            capabilities=JamulusCapabilities(capabilities),
            activation_mode=ActivationMode.MANAGED,
        )

    return (
        integrated(
            client,
            "Jamulus.app/Contents/MacOS/Jamulus",
            frozenset(
                {
                    "audio-client",
                    "json-rpc-client",
                    "native-gui",
                    "webjam-route-profile",
                    "webjam-integrated-runtime",
                }
            ),
        ),
        integrated(
            server,
            "JamulusServer.app/Contents/MacOS/JamulusServer",
            frozenset(
                {
                    "audio-server",
                    "json-rpc-server",
                    "recording",
                    "webjam-integrated-runtime",
                }
            ),
        ),
    )


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
    platform_store=None,
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
        platform_store=platform_store,
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


@pytest.mark.parametrize(
    ("failure", "reason_code", "message_fragment"),
    [
        (
            ComponentTlsTrustError("/Users/private/cacert.pem"),
            "catalog-trust-unavailable",
            "secure Jamulus update checker",
        ),
        (
            ComponentSecureConnectionError("token=private"),
            "catalog-secure-connection-failed",
            "trusted connection",
        ),
        (
            CatalogFetchError("private response body"),
            "catalog-service-unavailable",
            "unusable response",
        ),
        (
            ComponentDownloadError("rejected redirect with private URL"),
            "catalog-service-unavailable",
            "unusable response",
        ),
    ],
)
def test_catalog_connection_failures_are_specific_bounded_and_diagnostic(
    tmp_path,
    failure,
    reason_code,
    message_fragment,
):
    service, _ = _service(
        tmp_path,
        fetcher=_Fetcher(failure),
    )

    assert service.check_now()
    _wait(service)

    assert service.snapshot.state is JamulusUpdateState.FALLBACK
    assert service.snapshot.reason_code == reason_code
    assert message_fragment in service.snapshot.message
    assert "private" not in service.snapshot.message.lower()
    diagnostics = service.diagnostics()
    assert diagnostics["catalog_transport"]["last_check"] == "failed"
    assert diagnostics["catalog_transport"]["reason_code"] == reason_code
    assert "private" not in json.dumps(diagnostics).lower()


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


def test_macos_upstream_catalog_and_existing_pointer_stay_source_only(
    tmp_path,
):
    class SourceOnlyContract:
        activation_allowed = False

    class SourceOnlyInstalled:
        version = "3.12.4"
        activation_allowed = False

        @staticmethod
        def execution_contract_for(_role):
            return SourceOnlyContract()

    class SourceOnlyStore:
        @staticmethod
        def current():
            return SourceOnlyInstalled()

        @staticmethod
        def previous():
            return None

    downloader = _Downloader(b"approved Jamulus installer")
    service, _data = _service(
        tmp_path,
        target=ComponentTarget.MACOS_ARM64,
        downloader=downloader,
        platform_store=SourceOnlyStore(),
    )

    assert service.check_now()
    _wait(service)

    assert service.snapshot.state is JamulusUpdateState.FALLBACK
    assert service.snapshot.active_version == "3.12.2"
    assert service.snapshot.available_version == "3.12.4"
    assert (
        service.snapshot.reason_code
        == "macos-integrated-runtime-required"
    )
    assert "does not have the WebJam-integrated execution contract" in (
        service.snapshot.message
    )
    assert not service.snapshot.can_download
    assert not service.snapshot.can_approve
    assert not service.snapshot.can_activate
    assert not service.snapshot.can_rollback
    assert downloader.calls == 0
    assert service.managed_client_component() is None
    assert service.managed_server_component() is None


def test_typed_macos_source_contract_cannot_claim_runtime_file_capability():
    with pytest.raises(JamulusPlatformError, match="runtime-file capabilities"):
        MacOSExecutionContract(
            kind=MacOSExecutionContractKind.OFFICIAL_SOURCE,
            role=JamulusRole.CLIENT,
            target=ComponentTarget.MACOS_ARM64,
            source_app_sandbox_enabled=True,
            source_entitlements_sha256="a" * 64,
            runtime_capabilities=frozenset(
                {
                    "audio-client",
                    "json-rpc-client",
                    "native-gui",
                    "webjam-route-profile",
                }
            ),
            activation_allowed=False,
            reason_code="official-source-app-sandboxed",
        )


def test_shared_macos_integrated_policy_requires_gate_shape_and_live_contract():
    client, _server = _integrated_macos_pair()
    contract = MacOSExecutionContract(
        kind=MacOSExecutionContractKind.WEBJAM_INTEGRATED,
        role=JamulusRole.CLIENT,
        target=ComponentTarget.MACOS_ARM64,
        source_app_sandbox_enabled=False,
        source_entitlements_sha256="b" * 64,
        runtime_capabilities=client.capabilities.values,
        activation_allowed=True,
        reason_code="verified-webjam-integrated-runtime",
    )

    assert not macos_integrated_runtime_entry_is_eligible(client)
    assert not macos_integrated_runtime_contract_allows(client, contract)
    assert macos_integrated_runtime_entry_is_eligible(
        client,
        verifier_enabled=True,
    )
    assert macos_integrated_runtime_contract_allows(
        client,
        contract,
        verifier_enabled=True,
    )

    official = official_jamulus_compatibility_registry().exact(
        component_id="jamulus",
        role=JamulusRole.CLIENT,
        target=ComponentTarget.MACOS_ARM64,
        version="3.12.3",
    )
    assert not macos_integrated_runtime_entry_is_eligible(
        official,
        verifier_enabled=True,
    )


def test_integrated_execution_contract_rejects_app_sandbox():
    client, _server = _integrated_macos_pair()

    with pytest.raises(JamulusPlatformError, match="sandboxed"):
        MacOSExecutionContract(
            kind=MacOSExecutionContractKind.WEBJAM_INTEGRATED,
            role=JamulusRole.CLIENT,
            target=ComponentTarget.MACOS_ARM64,
            source_app_sandbox_enabled=True,
            source_entitlements_sha256="b" * 64,
            runtime_capabilities=client.capabilities.values,
            activation_allowed=True,
            reason_code="verified-webjam-integrated-runtime",
        )


def test_updater_handoff_uses_shared_integrated_gate_and_contract(
    tmp_path,
    monkeypatch,
):
    client, server = _integrated_macos_pair()
    client_path = tmp_path / "Jamulus"
    server_path = tmp_path / "JamulusServer"
    client_path.write_bytes(b"client")
    server_path.write_bytes(b"server")
    client_path.chmod(0o700)
    server_path.chmod(0o700)
    contracts = {
        JamulusRole.CLIENT: MacOSExecutionContract(
            kind=MacOSExecutionContractKind.WEBJAM_INTEGRATED,
            role=JamulusRole.CLIENT,
            target=ComponentTarget.MACOS_ARM64,
            source_app_sandbox_enabled=False,
            source_entitlements_sha256="b" * 64,
            runtime_capabilities=client.capabilities.values,
            activation_allowed=True,
            reason_code="verified-webjam-integrated-runtime",
        ),
        JamulusRole.SERVER: MacOSExecutionContract(
            kind=MacOSExecutionContractKind.WEBJAM_INTEGRATED,
            role=JamulusRole.SERVER,
            target=ComponentTarget.MACOS_ARM64,
            source_app_sandbox_enabled=False,
            source_entitlements_sha256="c" * 64,
            runtime_capabilities=server.capabilities.values,
            activation_allowed=True,
            reason_code="verified-webjam-integrated-runtime",
        ),
    }
    current = SimpleNamespace(
        version=client.version,
        target=ComponentTarget.MACOS_ARM64,
        artifact_sha256=client.artifact.sha256,
        client_path=client_path,
        server_path=server_path,
        execution_contract_for=lambda role: contracts[JamulusRole(role)],
    )
    store = SimpleNamespace(
        current=lambda: current,
        previous=lambda: None,
    )
    service, _data = _service(
        tmp_path,
        target=ComponentTarget.MACOS_ARM64,
        platform_store=store,
    )
    service._registry = JamulusCompatibilityRegistry((client, server))

    assert service.managed_client_component() is None
    assert service.managed_server_component() is None

    monkeypatch.setattr(
        "services.jamulus_component_platform."
        "MACOS_INTEGRATED_RUNTIME_VERIFIER_ENABLED",
        True,
    )
    managed_client = service.managed_client_component()
    managed_server = service.managed_server_component()

    assert managed_client is not None
    assert managed_client.entry.variant == "webjam-integrated"
    assert managed_client.publisher_verified is False
    assert managed_client.trust_policy_verified is True
    assert managed_client.execution_contract_verified is True
    assert managed_client.executable_path == client_path
    assert managed_server is not None
    assert managed_server.entry.variant == "webjam-integrated"
    assert managed_server.publisher_verified is False
    assert managed_server.trust_policy_verified is True
    assert managed_server.execution_contract_verified is True
    assert managed_server.executable_path == server_path


@pytest.mark.parametrize(
    ("role", "overclaim"),
    (
        (JamulusRole.CLIENT, "webjam-route-profile"),
        (JamulusRole.SERVER, "recording"),
    ),
)
def test_old_signed_macos_capability_overclaim_is_narrowed_not_activated(
    role,
    overclaim,
):
    baseline = official_jamulus_compatibility_registry()
    baked = baseline.exact(
        component_id="jamulus",
        role=role,
        target=ComponentTarget.MACOS_ARM64,
        version="3.12.3",
    )
    overclaimed = baked.to_dict()
    overclaimed["capabilities"].append(overclaim)
    signed = JamulusCompatibilityRegistry(
        (JamulusCompatibility.from_dict(overclaimed),)
    )

    merged = update_module._merge_registries(baseline, signed)
    accepted = merged.require_exact(baked)

    assert accepted == baked
    assert not accepted.capabilities.includes({overclaim})


def test_signed_macos_identity_conflict_cannot_use_overclaim_downgrade():
    baseline = official_jamulus_compatibility_registry()
    baked = baseline.exact(
        component_id="jamulus",
        role=JamulusRole.CLIENT,
        target=ComponentTarget.MACOS_ARM64,
        version="3.12.3",
    )
    changed = baked.to_dict()
    changed["capabilities"].append("webjam-route-profile")
    changed["publisher"] = "Different publisher"
    signed = JamulusCompatibilityRegistry(
        (JamulusCompatibility.from_dict(changed),)
    )

    with pytest.raises(
        update_module.JamulusComponentUpdateError,
        match="conflicts",
    ):
        update_module._merge_registries(baseline, signed)


def _mac_bundle_for_verifier(
    tmp_path: Path,
    *,
    role: JamulusRole = JamulusRole.CLIENT,
) -> Path:
    executable_name = (
        "Jamulus" if role is JamulusRole.CLIENT else "JamulusServer"
    )
    bundle = tmp_path / f"{executable_name}.app"
    executable = bundle / "Contents" / "MacOS" / executable_name
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"mock Mach-O")
    executable.chmod(0o755)
    (bundle / "Contents" / "Info.plist").write_bytes(
        plistlib.dumps(
            {
                "CFBundleIdentifier": (
                    "app.jamulussoftware.Jamulus"
                    if role is JamulusRole.CLIENT
                    else "app.jamulussoftware.JamulusServer"
                ),
                "CFBundleVersion": "3.12.3",
            }
        )
    )
    return bundle


@pytest.mark.parametrize(
    "target",
    (ComponentTarget.MACOS_ARM64, ComponentTarget.MACOS_X64),
)
@pytest.mark.parametrize(
    "role",
    (JamulusRole.CLIENT, JamulusRole.SERVER),
)
def test_macos_bundle_verifier_records_live_sandbox_entitlement(
    tmp_path,
    monkeypatch,
    target,
    role,
):
    monkeypatch.setattr(sys, "platform", "darwin")
    bundle = _mac_bundle_for_verifier(tmp_path, role=role)
    entitlement_bytes = plistlib.dumps(
        {
            "com.apple.security.app-sandbox": True,
            "com.apple.security.network.client": True,
        }
    )

    def runner(arguments, **_kwargs):
        args = list(arguments)
        if args[:3] == ["/usr/bin/codesign", "--verify", "--deep"]:
            return subprocess.CompletedProcess(args, 0, b"", b"")
        if args[:3] == ["/usr/bin/codesign", "-d", "--verbose=4"]:
            identifier = (
                b"app.jamulussoftware.Jamulus"
                if role is JamulusRole.CLIENT
                else b"app.jamulussoftware.JamulusServer"
            )
            return subprocess.CompletedProcess(
                args,
                0,
                b"",
                (
                    b"Identifier=" + identifier + b"\n"
                    b"TeamIdentifier=V9ZZ6B9WH8\n"
                    b"Authority=Developer ID Application: Jonathan Chung "
                    b"(V9ZZ6B9WH8)\n"
                ),
            )
        if args[:3] == ["/usr/bin/codesign", "-d", "--xml"]:
            return subprocess.CompletedProcess(args, 0, entitlement_bytes, b"")
        if args[0] == "/usr/sbin/spctl":
            return subprocess.CompletedProcess(
                args, 0, b"", b"source=Notarized Developer ID"
            )
        if args[:2] == ["/usr/bin/lipo", "-archs"]:
            return subprocess.CompletedProcess(args, 0, b"arm64 x86_64", b"")
        raise AssertionError(args)

    verified = MacOSBundleVerifier(command_runner=runner).verify(
        bundle,
        role=role,
        version="3.12.3",
        target=target,
    )

    assert verified.app_sandbox_enabled is True
    assert len(verified.entitlements_sha256) == 64


def test_macos_bundle_verifier_rejects_non_boolean_sandbox_entitlement(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(sys, "platform", "darwin")
    bundle = _mac_bundle_for_verifier(tmp_path)

    def runner(arguments, **_kwargs):
        args = list(arguments)
        if args[:3] == ["/usr/bin/codesign", "--verify", "--deep"]:
            return subprocess.CompletedProcess(args, 0, b"", b"")
        if args[:3] == ["/usr/bin/codesign", "-d", "--verbose=4"]:
            return subprocess.CompletedProcess(
                args,
                0,
                b"",
                (
                    b"Identifier=app.jamulussoftware.Jamulus\n"
                    b"TeamIdentifier=V9ZZ6B9WH8\n"
                    b"Authority=Developer ID Application: Jonathan Chung "
                    b"(V9ZZ6B9WH8)\n"
                ),
            )
        if args[:3] == ["/usr/bin/codesign", "-d", "--xml"]:
            return subprocess.CompletedProcess(
                args,
                0,
                plistlib.dumps(
                    {"com.apple.security.app-sandbox": "true"}
                ),
                b"",
            )
        raise AssertionError(args)

    with pytest.raises(JamulusPlatformError, match="Sandbox entitlement"):
        MacOSBundleVerifier(command_runner=runner).verify(
            bundle,
            role=JamulusRole.CLIENT,
            version="3.12.3",
            target=ComponentTarget.MACOS_ARM64,
        )


@pytest.mark.parametrize(
    ("failure", "match"),
    (
        ("tampered-or-partially-signed", "signature is invalid"),
        ("wrong-publisher", "publisher identity"),
        ("wrong-architecture", "does not support"),
    ),
)
def test_macos_bundle_verifier_rejects_incomplete_execution_evidence(
    tmp_path,
    monkeypatch,
    failure,
    match,
):
    monkeypatch.setattr(sys, "platform", "darwin")
    bundle = _mac_bundle_for_verifier(tmp_path)

    def runner(arguments, **_kwargs):
        args = list(arguments)
        if args[:3] == ["/usr/bin/codesign", "--verify", "--deep"]:
            return subprocess.CompletedProcess(
                args,
                1 if failure == "tampered-or-partially-signed" else 0,
                b"",
                b"",
            )
        if args[:3] == ["/usr/bin/codesign", "-d", "--verbose=4"]:
            authority = (
                b"Authority=Developer ID Application: Someone Else "
                b"(BADTEAM123)\n"
                if failure == "wrong-publisher"
                else (
                    b"Authority=Developer ID Application: Jonathan Chung "
                    b"(V9ZZ6B9WH8)\n"
                )
            )
            return subprocess.CompletedProcess(
                args,
                0,
                b"",
                (
                    b"Identifier=app.jamulussoftware.Jamulus\n"
                    b"TeamIdentifier=V9ZZ6B9WH8\n"
                    + authority
                ),
            )
        if args[:3] == ["/usr/bin/codesign", "-d", "--xml"]:
            return subprocess.CompletedProcess(
                args,
                0,
                plistlib.dumps(
                    {"com.apple.security.app-sandbox": True}
                ),
                b"",
            )
        if args[0] == "/usr/sbin/spctl":
            return subprocess.CompletedProcess(
                args, 0, b"", b"source=Notarized Developer ID"
            )
        if args[:2] == ["/usr/bin/lipo", "-archs"]:
            architectures = (
                b"x86_64"
                if failure == "wrong-architecture"
                else b"arm64 x86_64"
            )
            return subprocess.CompletedProcess(
                args, 0, architectures, b""
            )
        raise AssertionError(args)

    with pytest.raises(JamulusPlatformError, match=match):
        MacOSBundleVerifier(command_runner=runner).verify(
            bundle,
            role=JamulusRole.CLIENT,
            version="3.12.3",
            target=ComponentTarget.MACOS_ARM64,
        )


def test_macos_bundle_verifier_rejects_escaping_bundle_symlink(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(sys, "platform", "darwin")
    bundle = _mac_bundle_for_verifier(tmp_path)
    outside = tmp_path / "outside"
    outside.write_bytes(b"not part of bundle")
    (bundle / "Contents" / "escape").symlink_to(outside)

    with pytest.raises(JamulusPlatformError, match="escaping symlink"):
        MacOSBundleVerifier(
            command_runner=lambda *_args, **_kwargs: pytest.fail(
                "no platform command may run after a symlink violation"
            )
        ).verify(
            bundle,
            role=JamulusRole.CLIENT,
            version="3.12.3",
            target=ComponentTarget.MACOS_ARM64,
        )


class _NoopMacVerifier:
    def verify(self, *args, **kwargs):
        raise AssertionError("bundle verification must not run before acceptance")


class _AcceptingMacVerifier:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def verify(self, bundle, *, role, version, target):
        self.calls.append((Path(bundle).name, role.value, version))
        assert target is ComponentTarget.MACOS_ARM64
        return VerifiedMacBundle(
            path=Path(bundle),
            role=role,
            version=version,
            architectures=("arm64", "x86_64"),
            team_identifier="V9ZZ6B9WH8",
            bundle_identifier=(
                "app.jamulussoftware.Jamulus"
                if role is JamulusRole.CLIENT
                else "app.jamulussoftware.JamulusServer"
            ),
            app_sandbox_enabled=True,
            entitlements_sha256=hashlib.sha256(
                b"test-sandbox-entitlements"
            ).hexdigest(),
        )


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


def test_macos_external_validation_never_activates_upstream_source(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(sys, "platform", "darwin")
    client, server = _pair(
        target=ComponentTarget.MACOS_ARM64,
        version="3.12.3",
    )
    registry = JamulusCompatibilityRegistry((client, server))
    calls: list[str] = []

    class RecordingRegistry:
        def compatible(self, **kwargs):
            calls.append(kwargs["webjam_version"])
            return registry.compatible(**kwargs)

    executable = (
        tmp_path / "Jamulus.app" / "Contents" / "MacOS" / "Jamulus"
    )
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"official binary")
    store = MacOSJamulusComponentStore(
        registry,
        webjam_version="0.22.1",
        root=tmp_path / "store",
        verifier=_AcceptingMacVerifier(),
    )

    result = store.external_validator(
        ExternalComponentCandidate(
            path=executable,
            origin=ComponentOrigin.MANAGED,
        ),
        RecordingRegistry(),
        JamulusRole.CLIENT,
        ComponentTarget.MACOS_ARM64,
    )

    assert result is None
    assert calls == []


def test_macos_license_refusal_mounts_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    commands = []

    def runner(arguments, **kwargs):
        commands.append(arguments)
        return subprocess.CompletedProcess(arguments, 0, b"", b"")

    store = MacOSJamulusComponentStore(
        webjam_version="0.22.0",
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
        webjam_version="0.22.0",
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
    assert first.current.activation_allowed is False
    assert (
        first.current.client_execution_contract.reason_code
        == "official-source-app-sandboxed"
    )
    assert (
        first.current.server_execution_contract.reason_code
        == "official-source-app-sandboxed"
    )
    assert "webjam-route-profile" not in (
        first.current.client_execution_contract.runtime_capabilities
    )
    assert "recording" not in (
        first.current.server_execution_contract.runtime_capabilities
    )

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
        webjam_version="0.22.0",
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
        webjam_version="0.22.0",
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
        webjam_version="0.22.0",
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
        webjam_version="0.22.0",
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
