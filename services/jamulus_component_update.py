"""Asynchronous, fail-closed Jamulus component update coordinator.

The coordinator checks a signed WebJam compatibility catalog, downloads only
exact approved bytes, and keeps installation separate from live-session
ownership.  It publishes immutable, path-free presentation snapshots suitable
for Qt and support diagnostics.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sys
import threading
from typing import Callable, NoReturn

from core.component_catalog import (
    MAX_CATALOG_BYTES,
    CatalogSequenceStore,
    ComponentCatalogVerifier,
    VerifiedComponentCatalog,
)
from core.component_download import (
    ComponentDownloadCancelled,
    ComponentDownloadError,
    ComponentSecureConnectionError,
    ComponentTlsTrustError,
    DownloadCancellation,
    DownloadProgress,
    SecureComponentDownloader,
    UrllibHttpsTransport,
    verify_downloaded_file,
)
from core.component_hosts import HttpsHostPolicy, JAMULUS_RELEASE_HOST_POLICY
from core.component_lock import (
    ComponentLockTimeout,
    InterProcessComponentLock,
    RUNTIME_ACTIVE_LOCK_NAME,
)
from core.component_store import (
    BusyCheck,
    ComponentBusyReason,
    ComponentBusyStatus,
    ManagedComponentStore,
    default_component_store_root,
)
from core.file_io import atomic_write_text
from core.jamulus_compatibility import (
    ActivationMode,
    ComponentTarget,
    JamulusCompatibility,
    JamulusCompatibilityRegistry,
    JamulusRole,
    official_jamulus_compatibility_registry,
)
from core.jamulus_update_state import JamulusUpdateSnapshot, JamulusUpdateState
from core.jamulus_component_resolver import ValidatedExternalComponent
from services.jamulus_component_platform import (
    JamulusLicenseApprovalRequired,
    JamulusPlatformError,
    JamulusPlatformInstallDeferred,
    JamulusPlatformInstallationNotFound,
    MACOS_INTEGRATED_RUNTIME_VARIANT,
    MacOSExecutionContract,
    MacOSExecutionContractKind,
    MacOSJamulusComponentStore,
    PlatformInstalledJamulusStore,
    macos_integrated_runtime_contract_allows,
    macos_integrated_runtime_entry_is_eligible,
    open_platform_jamulus_installer,
    platform_component_target,
)


DEFAULT_COMPONENT_CATALOG_URL = (
    "https://github.com/rupret007/webjam/releases/download/"
    "jamulus-components-v3/WebJam-Jamulus-components-v1.json"
)
EMBEDDED_FALLBACK_VERSION = "3.12.2"
_CLIENT_EXECUTION_CAPABILITIES = frozenset(
    {
        "audio-client",
        "json-rpc-client",
        "native-gui",
        "webjam-route-profile",
    }
)
_SERVER_EXECUTION_CAPABILITIES = frozenset(
    {"audio-server", "json-rpc-server", "recording"}
)


class JamulusComponentUpdateError(RuntimeError):
    pass


class CatalogFetchError(JamulusComponentUpdateError):
    pass


class CatalogAuthorizationStale(JamulusComponentUpdateError):
    """A previously verified catalog is no longer valid at point of use."""


@dataclass(frozen=True, slots=True)
class JamulusUpdatePresentation:
    """Qt-ready updater state with no local paths or raw exceptions."""

    snapshot: JamulusUpdateSnapshot
    previous_version: str = ""
    can_download: bool = False
    can_activate: bool = False
    can_approve: bool = False
    can_rollback: bool = False
    approve_label: str = "Open installer"
    activate_label: str = "Restart when idle"
    detail: str = ""

    @property
    def state(self) -> JamulusUpdateState:
        return self.snapshot.state

    @property
    def active_version(self) -> str:
        return self.snapshot.active_version

    @property
    def available_version(self) -> str:
        return self.snapshot.available_version

    @property
    def target(self) -> str:
        return self.snapshot.target

    @property
    def progress_percent(self) -> int:
        return self.snapshot.progress_percent

    @property
    def reason_code(self) -> str:
        return self.snapshot.reason_code

    @property
    def message(self) -> str:
        return self.snapshot.message

    @property
    def checked_at_utc(self) -> str:
        return self.snapshot.checked_at_utc

    @property
    def restart_when_idle(self) -> bool:
        return self.snapshot.restart_when_idle

    def to_public_dict(self) -> dict[str, object]:
        value = self.snapshot.to_dict()
        value.update(
            {
                "previous_version": self.previous_version,
                "can_download": self.can_download,
                "can_activate": self.can_activate,
                "can_approve": self.can_approve,
                "can_rollback": self.can_rollback,
                "activate_label": self.activate_label,
                "detail": self.detail,
            }
        )
        return value


class SignedCatalogFetcher:
    """Bounded HTTPS fetch whose bytes are trusted only after Ed25519 proof."""

    def __init__(
        self,
        *,
        transport: UrllibHttpsTransport | None = None,
        host_policy: HttpsHostPolicy = JAMULUS_RELEASE_HOST_POLICY,
    ) -> None:
        self.transport = transport or UrllibHttpsTransport(
            timeout=30.0, user_agent="WebJam Jamulus component catalog"
        )
        self.host_policy = host_policy

    def security_diagnostics(self) -> dict[str, str]:
        """Return finite, path-free transport trust facts."""

        diagnostics = getattr(self.transport, "security_diagnostics", None)
        if not callable(diagnostics):
            return {"trust_source": "injected", "trust_status": "unknown"}
        try:
            value = diagnostics()
        except Exception:  # noqa: BLE001 - diagnostics stays best-effort
            return {"trust_source": "unavailable", "trust_status": "unknown"}
        if not isinstance(value, dict):
            return {"trust_source": "unavailable", "trust_status": "unknown"}
        return {
            key: str(value[key])
            for key in (
                "trust_source",
                "trust_status",
                "environment_ca_overrides",
                "redirect_policy",
            )
            if isinstance(value.get(key), str)
        }

    def fetch(
        self,
        url: str,
        *,
        cancellation: DownloadCancellation,
    ) -> bytes:
        opened = self.transport.open(
            url,
            policy=self.host_policy,
            cancellation=cancellation,
        )
        body = opened.body
        try:
            if body.status != 200:
                raise CatalogFetchError(
                    "the component catalog server returned an error"
                )
            encoding = (
                str(body.headers.get("Content-Encoding", "") or "").strip().lower()
            )
            if encoding not in {"", "identity"}:
                raise CatalogFetchError(
                    "the component catalog used an unexpected encoding"
                )
            length_text = str(body.headers.get("Content-Length", "") or "").strip()
            expected_length: int | None = None
            if length_text:
                if (
                    not length_text.isascii()
                    or not length_text.isdigit()
                    or not 1 <= int(length_text) <= MAX_CATALOG_BYTES
                ):
                    raise CatalogFetchError("the component catalog size is invalid")
                expected_length = int(length_text)
            chunks: list[bytes] = []
            total = 0
            while True:
                cancellation.raise_if_cancelled()
                chunk = body.read(min(64 * 1024, MAX_CATALOG_BYTES + 1 - total))
                if not isinstance(chunk, bytes):
                    raise CatalogFetchError(
                        "the component catalog returned invalid bytes"
                    )
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > MAX_CATALOG_BYTES:
                    raise CatalogFetchError(
                        "the component catalog exceeded its size limit"
                    )
            if total <= 0:
                raise CatalogFetchError("the component catalog was empty")
            if expected_length is not None and total != expected_length:
                raise CatalogFetchError("the component catalog response was incomplete")
            return b"".join(chunks)
        finally:
            body.close()


SnapshotCallback = Callable[[JamulusUpdatePresentation], None]
ActiveVersionProvider = Callable[[], str]
PlatformApprovalLauncher = Callable[[Path, JamulusCompatibility], bool]
PlatformStoreFactory = Callable[
    [JamulusCompatibilityRegistry, Path],
    MacOSJamulusComponentStore,
]
InstalledStoreFactory = Callable[
    [JamulusCompatibilityRegistry, ComponentTarget, Path],
    PlatformInstalledJamulusStore,
]


class JamulusComponentUpdateService:
    """One single-flight updater for the local target and client/server pair."""

    def __init__(
        self,
        *,
        webjam_version: str,
        busy_check: BusyCheck,
        on_snapshot: SnapshotCallback | None = None,
        target: ComponentTarget | None = None,
        root: str | Path | None = None,
        catalog_url: str = DEFAULT_COMPONENT_CATALOG_URL,
        catalog_fetcher: SignedCatalogFetcher | None = None,
        catalog_verifier: ComponentCatalogVerifier | None = None,
        downloader: SecureComponentDownloader | None = None,
        active_version_provider: ActiveVersionProvider | None = None,
        platform_approval_launcher: PlatformApprovalLauncher | None = None,
        platform_store: MacOSJamulusComponentStore | None = None,
        platform_store_factory: PlatformStoreFactory | None = None,
        installed_store: PlatformInstalledJamulusStore | None = None,
        installed_store_factory: InstalledStoreFactory | None = None,
        automatic_download: bool = True,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(webjam_version, str) or not webjam_version.strip():
            raise ValueError("webjam_version is required")
        if not callable(busy_check):
            raise TypeError("busy_check must be callable")
        self.webjam_version = webjam_version.strip()
        self.target = target or platform_component_target()
        self.root = Path(root) if root is not None else default_component_store_root()
        self.catalog_url = str(catalog_url)
        self.busy_check = busy_check
        self.on_snapshot = on_snapshot or (lambda _snapshot: None)
        self.catalog_fetcher = catalog_fetcher or SignedCatalogFetcher()
        self.sequence_store = CatalogSequenceStore(self.root / "catalog-sequence.json")
        self.catalog_verifier = catalog_verifier or ComponentCatalogVerifier(
            sequence_store=self.sequence_store
        )
        self.downloader = downloader or SecureComponentDownloader()
        self.active_version_provider = (
            active_version_provider or self._default_active_version
        )
        self.platform_approval_launcher = (
            platform_approval_launcher or open_platform_jamulus_installer
        )
        self._platform_store_factory = platform_store_factory
        self._platform_store_injected = platform_store is not None
        self._installed_store_factory = installed_store_factory
        self._installed_store_injected = installed_store is not None
        self.automatic_download = bool(automatic_download)
        self._now = now or (lambda: datetime.now(timezone.utc))
        self.catalog_cache_path = self.root / "catalog-envelope.json"
        self.operation_lock_path = self.root / ".update-operation.lock"
        self.runtime_lock_path = self.root / RUNTIME_ACTIVE_LOCK_NAME
        self._baseline_registry = official_jamulus_compatibility_registry()
        self._registry = self._load_cached_registry()
        self._store = ManagedComponentStore(self._registry, root=self.root)
        self._platform_store = platform_store
        if (
            self.target
            in {
                ComponentTarget.MACOS_ARM64,
                ComponentTarget.MACOS_X64,
            }
            and self._platform_store is None
        ):
            self._platform_store = self._make_platform_store(self._registry)
        self._installed_store = installed_store
        if (
            self.target
            in {
                ComponentTarget.WINDOWS_X64,
                ComponentTarget.LINUX_X64,
            }
            and self._installed_store is None
        ):
            self._installed_store = self._make_installed_store(self._registry)
        self._state_lock = threading.RLock()
        self._operation_thread: threading.Thread | None = None
        self._cancellation: DownloadCancellation | None = None
        self._catalog: VerifiedComponentCatalog | None = None
        self._candidate: JamulusCompatibility | None = None
        self._server_candidate: JamulusCompatibility | None = None
        self._previous_version = ""
        self._rollback_available = False
        self._last_catalog_fetch_status = "not-checked"
        self._last_catalog_fetch_reason = ""
        initial_active_version = (
            self._safe_active_version()
            if active_version_provider is not None
            else EMBEDDED_FALLBACK_VERSION
        )
        initial = JamulusUpdateSnapshot(
            state=JamulusUpdateState.IDLE,
            active_version=initial_active_version,
            target=self.target.value,
            message="Jamulus update checking has not started.",
        )
        self._presentation = self._present(initial)

    @property
    def snapshot(self) -> JamulusUpdatePresentation:
        with self._state_lock:
            return self._presentation

    @property
    def operation_in_progress(self) -> bool:
        with self._state_lock:
            return bool(
                self._operation_thread is not None and self._operation_thread.is_alive()
            )

    def start_automatic_check(self) -> bool:
        return self.check_now(automatic=True)

    def check_now(self, *, automatic: bool = False) -> bool:
        return self._start_worker(
            "jamulus-update-check",
            lambda token: self._check_worker(
                token,
                automatic_download=(self.automatic_download if automatic else False),
            ),
            checking=True,
        )

    def download_available(self) -> bool:
        with self._state_lock:
            candidate = self._candidate
        if candidate is None:
            self._publish_failure(
                "no-approved-update",
                "No approved Jamulus update is currently available.",
            )
            return False
        return self._start_worker(
            "jamulus-update-download",
            lambda token: self._download_worker(candidate, token),
        )

    def approve_ready(self, *, license_accepted: bool = False) -> bool:
        """Apply one downloaded platform artifact after explicit approval."""

        with self._state_lock:
            candidate = self._candidate
            server = self._server_candidate
        if candidate is None:
            self._publish_failure(
                "no-ready-update",
                "No verified Jamulus update is ready to install.",
            )
            return False
        return self._start_worker(
            "jamulus-update-approve",
            lambda _token: self._approve_worker(
                candidate,
                server,
                license_accepted=license_accepted,
            ),
            refresh_previous_after=True,
        )

    def activate_when_idle(self) -> bool:
        """Retry approval or verify an OS-owned install at a clean stop."""

        presentation = self.snapshot
        # macOS still needs an earlier explicit SLA acceptance; the UI invokes
        # approve_ready again rather than allowing this method to infer consent.
        if self.target in {
            ComponentTarget.MACOS_ARM64,
            ComponentTarget.MACOS_X64,
        }:
            return False
        with self._state_lock:
            candidate = self._candidate
            server = self._server_candidate
        if candidate is None or server is None:
            return False
        if (
            presentation.state is JamulusUpdateState.READY
            or presentation.reason_code == "finish-platform-installer"
        ):
            return self._start_worker(
                "jamulus-update-verify-install",
                lambda _token: self._verify_platform_install_worker(
                    candidate,
                    server,
                ),
            )
        if presentation.state is not JamulusUpdateState.DEFERRED:
            return False
        return self.approve_ready()

    def rollback(self) -> bool:
        with self._state_lock:
            rollback_available = self._rollback_available
        if (
            self.target
            not in {
                ComponentTarget.MACOS_ARM64,
                ComponentTarget.MACOS_X64,
            }
            or self._platform_store is None
            or not rollback_available
        ):
            self._publish_failure(
                "rollback-unavailable",
                "No previous managed Jamulus version is available on this platform.",
            )
            return False
        return self._start_worker(
            "jamulus-update-rollback",
            lambda _token: self._rollback_worker(),
            refresh_previous_after=True,
        )

    def cancel(self) -> None:
        with self._state_lock:
            token = self._cancellation
        if token is not None:
            token.cancel()

    def close(self, *, timeout: float = 2.0) -> bool:
        self.cancel()
        with self._state_lock:
            worker = self._operation_thread
        if worker is None or not worker.is_alive():
            return True
        worker.join(max(0.0, min(float(timeout), 5.0)))
        return not worker.is_alive()

    def managed_client_component(self) -> ValidatedExternalComponent | None:
        """Return a freshly verified catalog identity and client executable."""

        return self._managed_component(JamulusRole.CLIENT)

    def managed_server_component(self) -> ValidatedExternalComponent | None:
        return self._managed_component(JamulusRole.SERVER)

    def managed_client_path(self) -> Path | None:
        """Compatibility wrapper for older Bridge implementations."""

        component = self.managed_client_component()
        return component.executable_path if component is not None else None

    def managed_server_path(self) -> Path | None:
        component = self.managed_server_component()
        return component.executable_path if component is not None else None

    def _managed_component(
        self,
        role: JamulusRole,
    ) -> ValidatedExternalComponent | None:
        try:
            if self.target in {
                ComponentTarget.MACOS_ARM64,
                ComponentTarget.MACOS_X64,
            }:
                if self._platform_store is None:
                    return None
                current = self._platform_store.current()
                if current is None:
                    return None
                contract = current.execution_contract_for(role)
                if (
                    not isinstance(contract, MacOSExecutionContract)
                    or contract.kind
                    is not MacOSExecutionContractKind.WEBJAM_INTEGRATED
                    or not contract.activation_allowed
                ):
                    return None
                entry = self._registry.exact(
                    component_id="jamulus",
                    role=role,
                    target=current.target,
                    version=current.version,
                    variant=MACOS_INTEGRATED_RUNTIME_VARIANT,
                )
                if (
                    entry.artifact.sha256 != current.artifact_sha256
                    or not macos_integrated_runtime_contract_allows(
                        entry,
                        contract,
                    )
                ):
                    return None
                executable = (
                    current.client_path
                    if role is JamulusRole.CLIENT
                    else current.server_path
                )
                return ValidatedExternalComponent(
                    entry=entry,
                    executable_path=executable,
                    content_verified=True,
                    version_verified=True,
                    architecture_verified=True,
                    # The integrated live contract proves WebJam's exact
                    # runtime policy. It does not by itself assert an Apple
                    # Developer ID identity (ad-hoc test candidates are an
                    # allowed future signing mode).
                    publisher_verified=False,
                    trust_policy_verified=True,
                    execution_contract_verified=True,
                )
            if self._installed_store is None:
                return None
            current = self._installed_store.current(role)
            if current is None:
                return None
            return ValidatedExternalComponent(
                entry=current.entry,
                executable_path=current.executable_path,
                content_verified=current.content_verified,
                version_verified=current.version_verified,
                architecture_verified=current.architecture_verified,
                publisher_verified=current.publisher_verified,
                trust_policy_verified=current.trust_policy_verified,
            )
        except (JamulusPlatformError, OSError, RuntimeError, ValueError):
            return None

    def license_text(self) -> str:
        """Return the packaged 3.12.3 license without accepting it."""

        candidates = [
            Path(__file__).resolve().parent.parent
            / "licenses"
            / "JAMULUS_COPYING-r3_12_3.txt"
        ]
        frozen_data_root = str(getattr(sys, "_MEIPASS", "") or "").strip()
        if frozen_data_root:
            candidates.append(
                Path(frozen_data_root)
                / "THIRD_PARTY_LICENSES"
                / "JAMULUS_COPYING-r3_12_3.txt"
            )
        for path in candidates:
            try:
                if (
                    path.is_file()
                    and not path.is_symlink()
                    and 0 < path.stat().st_size <= 1024 * 1024
                ):
                    return path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                continue
        raise JamulusComponentUpdateError("the Jamulus license text is unavailable")

    def diagnostics(self) -> dict[str, object]:
        """Return support facts without paths, URLs, credentials, or errors."""

        with self._state_lock:
            catalog = self._catalog
            presentation = self._presentation
            fetch_status = self._last_catalog_fetch_status
            fetch_reason = self._last_catalog_fetch_reason
            operation = bool(
                self._operation_thread is not None and self._operation_thread.is_alive()
            )
        transport = {
            "last_check": fetch_status,
            **_safe_transport_diagnostics(self.catalog_fetcher),
        }
        if fetch_reason:
            transport["reason_code"] = fetch_reason
        return {
            "update": presentation.to_public_dict(),
            "catalog": (
                {
                    "status": "verified",
                    **catalog.to_snapshot_dict(),
                }
                if catalog is not None
                else {"status": "not-verified"}
            ),
            "embedded_fallback_version": EMBEDDED_FALLBACK_VERSION,
            "automatic_download": self.automatic_download,
            "operation_in_progress": operation,
            "catalog_transport": transport,
        }

    def _check_worker(
        self,
        token: DownloadCancellation,
        *,
        automatic_download: bool,
    ) -> None:
        envelope: bytes | None = None
        network_failed = False
        incompatible_macos_version = ""
        try:
            envelope = self.catalog_fetcher.fetch(
                self.catalog_url,
                cancellation=token,
            )
        except ComponentDownloadCancelled:
            raise
        except (CatalogFetchError, ComponentDownloadError, OSError) as exc:
            network_failed = True
            failure_reason = _catalog_fetch_reason(exc)
            with self._state_lock:
                self._last_catalog_fetch_status = "failed"
                self._last_catalog_fetch_reason = failure_reason
            envelope = self._read_cached_catalog()
        else:
            with self._state_lock:
                self._last_catalog_fetch_status = "online"
                self._last_catalog_fetch_reason = ""
        if envelope is None:
            self._clear_actionable_catalog()
            reason_code = (
                self._last_catalog_fetch_reason
                if network_failed
                else "catalog-offline"
            )
            self._publish(
                JamulusUpdateSnapshot(
                    state=JamulusUpdateState.FALLBACK,
                    active_version=self._safe_active_version(),
                    target=self.target.value,
                    reason_code=reason_code,
                    message=_catalog_fetch_message(reason_code),
                    checked_at_utc=_utc_now(),
                )
            )
            return
        try:
            # Fetching may happen concurrently, but accepting a catalog and
            # publishing its cache must be serialized with every point-of-use
            # authorization. The lock order is always operation -> sequence.
            with InterProcessComponentLock(self.operation_lock_path, timeout=30.0):
                token.raise_if_cancelled()
                catalog = self.catalog_verifier.verify(
                    envelope,
                    webjam_version=self.webjam_version,
                )
                if not isinstance(catalog, VerifiedComponentCatalog):
                    raise JamulusComponentUpdateError(
                        "the component catalog verifier returned invalid state"
                    )
                # Custom/test verifiers may not own this service's sequence
                # store. Recording here also makes point-of-use checks
                # independent from verifier construction.
                self.sequence_store.compare_and_record(
                    catalog.sequence,
                    catalog.payload_sha256,
                )
                if not network_failed:
                    self._cache_catalog(envelope)
                registry = _merge_registries(self._baseline_registry, catalog.registry)
                store = ManagedComponentStore(registry, root=self.root)
                platform_store = self._platform_store
                if (
                    self.target
                    in {
                        ComponentTarget.MACOS_ARM64,
                        ComponentTarget.MACOS_X64,
                    }
                    and not self._platform_store_injected
                ):
                    platform_store = self._make_platform_store(registry)
                installed_store = self._installed_store
                if (
                    self.target
                    in {
                        ComponentTarget.WINDOWS_X64,
                        ComponentTarget.LINUX_X64,
                    }
                    and not self._installed_store_injected
                ):
                    installed_store = self._make_installed_store(registry)
                candidates = registry.compatible(
                    role=JamulusRole.CLIENT,
                    target=self.target,
                    webjam_version=self.webjam_version,
                    required_capabilities=_CLIENT_EXECUTION_CAPABILITIES,
                )
                if self.target in {
                    ComponentTarget.MACOS_ARM64,
                    ComponentTarget.MACOS_X64,
                }:
                    candidates = tuple(
                        entry
                        for entry in candidates
                        if macos_integrated_runtime_entry_is_eligible(entry)
                    )
                if not candidates:
                    source_candidates = registry.compatible(
                        role=JamulusRole.CLIENT,
                        target=self.target,
                        webjam_version=self.webjam_version,
                        required_capabilities={
                            "audio-client",
                            "json-rpc-client",
                        },
                    )
                    if (
                        self.target
                        in {
                            ComponentTarget.MACOS_ARM64,
                            ComponentTarget.MACOS_X64,
                        }
                        and source_candidates
                    ):
                        incompatible_macos_version = source_candidates[0].version
                        source_server = registry.exact(
                            component_id=source_candidates[0].component_id,
                            role=JamulusRole.SERVER,
                            target=self.target,
                            version=incompatible_macos_version,
                            variant=source_candidates[0].variant,
                        )
                        if (
                            source_server.artifact
                            != source_candidates[0].artifact
                            or not source_server.capabilities.includes(
                                {"audio-server", "json-rpc-server"}
                            )
                        ):
                            raise JamulusComponentUpdateError(
                                "the approved client/server package identities differ"
                            )
                        with self._state_lock:
                            self._catalog = catalog
                            self._registry = registry
                            self._store = store
                            self._platform_store = platform_store
                            self._installed_store = installed_store
                            self._candidate = None
                            self._server_candidate = None
                        active = self._safe_active_version()
                    else:
                        raise JamulusComponentUpdateError(
                            "the signed catalog has no compatible client"
                        )
                else:
                    candidate = candidates[0]
                    server = registry.exact(
                        component_id=candidate.component_id,
                        role=JamulusRole.SERVER,
                        target=self.target,
                        version=candidate.version,
                        variant=candidate.variant,
                    )
                    if (
                        server.artifact != candidate.artifact
                        or not server.capabilities.includes(
                            _SERVER_EXECUTION_CAPABILITIES
                        )
                        or (
                            self.target
                            in {
                                ComponentTarget.MACOS_ARM64,
                                ComponentTarget.MACOS_X64,
                            }
                            and not macos_integrated_runtime_entry_is_eligible(
                                server
                            )
                        )
                    ):
                        raise JamulusComponentUpdateError(
                            "the approved client/server execution contract differs"
                        )
                    with self._state_lock:
                        self._catalog = catalog
                        self._registry = registry
                        self._store = store
                        self._platform_store = platform_store
                        self._installed_store = installed_store
                        self._candidate = candidate
                        self._server_candidate = server
                    active = self._safe_active_version()
                    cached = store.cached_artifact(candidate)
        except Exception:
            self._clear_actionable_catalog()
            raise

        checked_at = _utc_now()
        if incompatible_macos_version:
            self._publish(
                JamulusUpdateSnapshot(
                    state=JamulusUpdateState.FALLBACK,
                    active_version=active,
                    available_version=incompatible_macos_version,
                    target=self.target.value,
                    reason_code="macos-integrated-runtime-required",
                    message=(
                        f"Jamulus {incompatible_macos_version} is verified as "
                        "an upstream Mac download, but it does not have the "
                        "WebJam-integrated execution contract needed for "
                        "private profiles, RPC secrets, and recordings. "
                        f"WebJam kept its integrated {active} component."
                    ),
                    checked_at_utc=checked_at,
                )
            )
            return
        if _version_tuple(active) >= _version_tuple(candidate.version):
            snapshot = JamulusUpdateSnapshot(
                state=JamulusUpdateState.UP_TO_DATE,
                active_version=active,
                available_version=candidate.version,
                target=self.target.value,
                message=f"Jamulus {active} is approved and up to date.",
                checked_at_utc=checked_at,
            )
        elif cached is not None:
            snapshot = JamulusUpdateSnapshot(
                state=JamulusUpdateState.READY,
                active_version=active,
                available_version=candidate.version,
                target=self.target.value,
                progress_percent=100,
                reason_code="platform-approval-required",
                message=_ready_message(self.target, candidate.version),
                checked_at_utc=checked_at,
            )
        else:
            snapshot = JamulusUpdateSnapshot(
                state=JamulusUpdateState.AVAILABLE,
                active_version=active,
                available_version=candidate.version,
                target=self.target.value,
                message=f"Approved Jamulus {candidate.version} is available.",
                checked_at_utc=checked_at,
            )
        self._publish(snapshot)
        if snapshot.state is not JamulusUpdateState.AVAILABLE:
            return
        if automatic_download:
            busy = _safe_busy_check(self.busy_check)
            if busy is None:
                self._download_worker(candidate, token)
            else:
                self._publish(
                    JamulusUpdateSnapshot(
                        state=JamulusUpdateState.AVAILABLE,
                        active_version=active,
                        available_version=candidate.version,
                        target=self.target.value,
                        reason_code="automatic-download-deferred",
                        message=(
                            f"Approved Jamulus {candidate.version} is "
                            "available. Automatic download will not use "
                            "bandwidth during an active session."
                        ),
                        checked_at_utc=checked_at,
                    )
                )

    def _download_worker(
        self,
        candidate: JamulusCompatibility,
        token: DownloadCancellation,
    ) -> None:
        self._publish(
            JamulusUpdateSnapshot(
                state=JamulusUpdateState.DOWNLOADING,
                active_version=self._safe_active_version(),
                available_version=candidate.version,
                target=self.target.value,
                message=f"Downloading approved Jamulus {candidate.version}…",
                checked_at_utc=self.snapshot.checked_at_utc,
            )
        )
        with InterProcessComponentLock(self.operation_lock_path, timeout=30.0):
            store, server = self._require_current_authorization(candidate)
            cached = store.cached_artifact(candidate)
            if cached is None:
                destination = store.artifact_cache_directory(candidate)

                def progress(value: DownloadProgress) -> None:
                    self._publish(
                        JamulusUpdateSnapshot(
                            state=JamulusUpdateState.DOWNLOADING,
                            active_version=self._safe_active_version(),
                            available_version=candidate.version,
                            target=self.target.value,
                            progress_percent=round(value.fraction * 100),
                            message=(
                                f"Downloading approved Jamulus {candidate.version}…"
                            ),
                            checked_at_utc=self.snapshot.checked_at_utc,
                        )
                    )

                self.downloader.download(
                    candidate.artifact,
                    destination_directory=destination,
                    cancellation=token,
                    progress=progress,
                )
                store.cached_artifact(candidate)
            # A long transfer may cross the catalog expiry. Refuse to expose
            # the artifact as ready unless authorization is still current.
            self._require_current_authorization(candidate, server)
        self._publish(
            JamulusUpdateSnapshot(
                state=JamulusUpdateState.READY,
                active_version=self._safe_active_version(),
                available_version=candidate.version,
                target=self.target.value,
                progress_percent=100,
                reason_code="platform-approval-required",
                message=_ready_message(self.target, candidate.version),
                checked_at_utc=self.snapshot.checked_at_utc or _utc_now(),
            )
        )

    def _approve_worker(
        self,
        candidate: JamulusCompatibility,
        server: JamulusCompatibility | None,
        *,
        license_accepted: bool,
    ) -> None:
        with InterProcessComponentLock(self.operation_lock_path, timeout=30.0):
            store, approved_server = self._require_current_authorization(
                candidate, server
            )
            cached = store.cached_artifact(candidate)
            if cached is None:
                raise JamulusComponentUpdateError(
                    "the verified Jamulus download is no longer available"
                )
            if self.target in {
                ComponentTarget.MACOS_ARM64,
                ComponentTarget.MACOS_X64,
            }:
                if self._platform_store is None:
                    raise JamulusComponentUpdateError(
                        "the macOS Jamulus installer is unavailable"
                    )
                with self._runtime_update_lock():
                    # Recheck after cache verification and immediately before
                    # the platform boundary. The platform store repeats its
                    # exact-byte and idle proofs under its own install lock.
                    self._require_current_authorization(candidate, approved_server)
                    result = self._platform_store.install_from_dmg(
                        client_entry=candidate,
                        server_entry=approved_server,
                        dmg_path=cached.path,
                        license_accepted=license_accepted,
                        busy_check=self.busy_check,
                        authorization_check=(
                            lambda observed_client, observed_server: (
                                self._reauthorize_platform_pair(
                                    candidate,
                                    approved_server,
                                    observed_client,
                                    observed_server,
                                )
                            )
                        ),
                    )
                snapshot = JamulusUpdateSnapshot(
                    state=JamulusUpdateState.UP_TO_DATE,
                    active_version=result.current.version,
                    available_version=result.current.version,
                    target=self.target.value,
                    progress_percent=100,
                    message=(
                        f"Jamulus {result.current.version} is installed and "
                        "will be used for the next session."
                    ),
                    checked_at_utc=_utc_now(),
                )
            else:
                with self._runtime_update_lock():
                    if self.platform_approval_launcher is None:
                        raise JamulusComponentUpdateError(
                            "the operating-system installer launcher is unavailable"
                        )
                    verify_downloaded_file(cached.path, candidate.artifact)
                    # Hashing can be slow on a large installer. Recheck signed
                    # authorization after hashing and directly before handoff.
                    self._require_current_authorization(candidate, approved_server)
                    if not self.platform_approval_launcher(cached.path, candidate):
                        raise JamulusComponentUpdateError(
                            "the operating-system installer did not open"
                        )
                snapshot = JamulusUpdateSnapshot(
                    state=JamulusUpdateState.DEFERRED,
                    active_version=self._safe_active_version(),
                    available_version=candidate.version,
                    target=self.target.value,
                    reason_code="finish-platform-installer",
                    message=(
                        "The verified Jamulus installer is open. Finish the "
                        "operating-system approval; WebJam will verify the "
                        "installed result before using it."
                    ),
                    checked_at_utc=_utc_now(),
                )
        self._publish(snapshot)

    def _verify_platform_install_worker(
        self,
        candidate: JamulusCompatibility,
        server: JamulusCompatibility,
    ) -> None:
        """Prove an OS-approved installation before exposing it to Bridge."""

        with InterProcessComponentLock(self.operation_lock_path, timeout=30.0):
            _store, approved_server = self._require_current_authorization(
                candidate, server
            )
            if self._installed_store is None:
                raise JamulusComponentUpdateError(
                    "installed Jamulus verification is unavailable"
                )
            with self._runtime_update_lock():
                self._require_current_authorization(candidate, approved_server)
                try:
                    result = self._installed_store.record_installed(
                        candidate,
                        approved_server,
                        self.busy_check,
                        authorization_check=(
                            lambda observed_client, observed_server: (
                                self._reauthorize_platform_pair(
                                    candidate,
                                    approved_server,
                                    observed_client,
                                    observed_server,
                                )
                            )
                        ),
                    )
                except JamulusPlatformInstallationNotFound:
                    self._publish(
                        JamulusUpdateSnapshot(
                            state=JamulusUpdateState.READY,
                            active_version=self._safe_active_version(),
                            available_version=candidate.version,
                            target=self.target.value,
                            progress_percent=100,
                            reason_code="platform-install-not-found",
                            message=(
                                "WebJam did not find the approved Jamulus "
                                "installation yet. Finish the operating-system "
                                "installer, then verify again."
                            ),
                            checked_at_utc=_utc_now(),
                        )
                    )
                    return
                # Do not publish or expose a receipt if signed authorization
                # expired or was superseded during platform verification.
                self._require_current_authorization(candidate, approved_server)
        self._publish(
            JamulusUpdateSnapshot(
                state=JamulusUpdateState.UP_TO_DATE,
                active_version=result.client.version,
                available_version=result.client.version,
                target=self.target.value,
                progress_percent=100,
                message=(
                    f"Jamulus {result.client.version} passed WebJam’s installed "
                    "runtime checks and will be used for the next session."
                ),
                checked_at_utc=_utc_now(),
            )
        )

    def _rollback_worker(self) -> None:
        assert self._platform_store is not None
        with InterProcessComponentLock(self.operation_lock_path, timeout=30.0):
            with self._runtime_update_lock():
                result = self._platform_store.rollback(busy_check=self.busy_check)
        self._publish(
            JamulusUpdateSnapshot(
                state=JamulusUpdateState.FALLBACK,
                active_version=result.current.version,
                target=self.target.value,
                reason_code="manual-rollback",
                message=(
                    f"WebJam restored Jamulus {result.current.version}. "
                    "The newer copy remains available for recovery."
                ),
                checked_at_utc=_utc_now(),
            )
        )

    def _clear_actionable_catalog(
        self,
        *,
        expected: VerifiedComponentCatalog | None = None,
    ) -> None:
        """Clear only catalog-derived actions, preserving the active install."""

        with self._state_lock:
            if expected is not None and self._catalog is not expected:
                return
            self._catalog = None
            self._candidate = None
            self._server_candidate = None

    def _require_current_authorization(
        self,
        candidate: JamulusCompatibility,
        server: JamulusCompatibility | None = None,
    ) -> tuple[ManagedComponentStore, JamulusCompatibility]:
        """Re-prove catalog authority while the operation lock is held.

        Every caller holds ``operation_lock_path`` before entering this method,
        keeping the global lock order operation -> catalog sequence.
        """

        with self._state_lock:
            catalog = self._catalog
            expected_candidate = self._candidate
            expected_server = self._server_candidate
            store = self._store

        def stale(message: str) -> NoReturn:
            self._clear_actionable_catalog(expected=catalog)
            raise CatalogAuthorizationStale(message)

        if catalog is None or expected_candidate is None or expected_server is None:
            stale("the approved component catalog is no longer actionable")
        if candidate != expected_candidate:
            stale("the approved client identity changed")
        if server is not None and server != expected_server:
            stale("the approved server identity changed")

        now = self._now()
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise JamulusComponentUpdateError(
                "the updater clock must be timezone-aware"
            )
        if catalog.expires_at <= now.astimezone(timezone.utc):
            stale("the approved component catalog expired")
        if catalog.webjam_version != self.webjam_version:
            stale("the approved component catalog targets another WebJam version")

        # This read takes the sequence lock while the operation lock is held.
        # A higher accepted sequence or different same-sequence hash revokes
        # the in-memory catalog before any network or platform boundary.
        if self.sequence_store.snapshot() != (
            catalog.sequence,
            catalog.payload_sha256,
        ):
            stale("a newer component catalog superseded this approval")

        try:
            approved_client = catalog.registry.require_exact(candidate)
            approved_server = catalog.registry.require_exact(expected_server)
            store.registry.require_exact(approved_client)
            store.registry.require_exact(approved_server)
        except Exception as exc:
            self._clear_actionable_catalog(expected=catalog)
            raise CatalogAuthorizationStale(
                "the approved client/server identity is no longer exact"
            ) from exc
        if (
            approved_client.role is not JamulusRole.CLIENT
            or approved_server.role is not JamulusRole.SERVER
            or approved_client.target is not self.target
            or approved_server.target is not self.target
            or not approved_client.supports_webjam(self.webjam_version)
            or not approved_server.supports_webjam(self.webjam_version)
            or approved_client.artifact != approved_server.artifact
        ):
            stale("the approved client/server pair is no longer compatible")
        return store, approved_server

    def _reauthorize_platform_pair(
        self,
        expected_client: JamulusCompatibility,
        expected_server: JamulusCompatibility,
        observed_client: JamulusCompatibility,
        observed_server: JamulusCompatibility,
    ) -> None:
        """Re-prove signed authority immediately before durable activation."""

        if observed_client != expected_client or observed_server != expected_server:
            self._clear_actionable_catalog()
            raise CatalogAuthorizationStale(
                "the platform installer identity changed during verification"
            )
        self._require_current_authorization(expected_client, expected_server)

    @contextmanager
    def _runtime_update_lock(self):
        """Exclude every live Bridge owner from an update transition."""

        try:
            with InterProcessComponentLock(self.runtime_lock_path, timeout=0.0):
                busy = _safe_busy_check(self.busy_check)
                if busy is not None:
                    raise JamulusPlatformInstallDeferred(busy)
                yield
        except ComponentLockTimeout as exc:
            raise JamulusPlatformInstallDeferred(
                ComponentBusyStatus(ComponentBusyReason.ANOTHER_INSTANCE_ACTIVE)
            ) from exc

    def _refresh_previous_snapshot(self) -> None:
        """Verify rollback state once at an operation boundary."""

        with self._state_lock:
            platform_store = self._platform_store
        previous_version = ""
        rollback_available = False
        if platform_store is not None:
            try:
                previous = platform_store.previous()
                previous_activatable = bool(
                    previous is not None
                    and (
                        self.target
                        not in {
                            ComponentTarget.MACOS_ARM64,
                            ComponentTarget.MACOS_X64,
                        }
                        or bool(getattr(previous, "activation_allowed", False))
                    )
                )
                if previous_activatable:
                    value = str(previous.version or "").strip()
                    _version_tuple(value)
                    previous_version = value
                    rollback_available = True
            except Exception:
                # Rollback stays disabled when exact previous-state
                # verification cannot be completed.
                pass
        with self._state_lock:
            self._previous_version = previous_version
            self._rollback_available = rollback_available

    def _start_worker(
        self,
        name: str,
        operation: Callable[[DownloadCancellation], None],
        *,
        checking: bool = False,
        refresh_previous_after: bool = False,
    ) -> bool:
        with self._state_lock:
            if self._operation_thread is not None and self._operation_thread.is_alive():
                return False
            token = DownloadCancellation()
            self._cancellation = token
            if checking:
                checking_snapshot = JamulusUpdateSnapshot(
                    state=JamulusUpdateState.CHECKING,
                    active_version=self._safe_active_version(),
                    target=self.target.value,
                    message="Checking WebJam’s signed Jamulus catalog…",
                )
                self._presentation = self._present(checking_snapshot)
                self._notify(self._presentation)

            def run() -> None:
                try:
                    self._refresh_previous_snapshot()
                    operation(token)
                except ComponentDownloadCancelled:
                    self._publish(
                        JamulusUpdateSnapshot(
                            state=JamulusUpdateState.CANCELLED,
                            active_version=self._safe_active_version(),
                            available_version=(
                                self._candidate.version
                                if self._candidate is not None
                                else ""
                            ),
                            target=self.target.value,
                            reason_code="cancelled",
                            message=(
                                "Jamulus update download was cancelled. "
                                "The current version was not changed."
                            ),
                            checked_at_utc=self.snapshot.checked_at_utc,
                        )
                    )
                except JamulusPlatformInstallDeferred as exc:
                    self._publish(
                        JamulusUpdateSnapshot(
                            state=JamulusUpdateState.DEFERRED,
                            active_version=self._safe_active_version(),
                            available_version=(
                                self._candidate.version
                                if self._candidate is not None
                                else ""
                            ),
                            target=self.target.value,
                            reason_code=exc.status.reason.value,
                            message=(
                                "Jamulus update is ready. WebJam will not "
                                "change it while the session is active."
                            ),
                            restart_when_idle=True,
                            checked_at_utc=self.snapshot.checked_at_utc,
                        )
                    )
                except JamulusLicenseApprovalRequired:
                    self._publish(
                        JamulusUpdateSnapshot(
                            state=JamulusUpdateState.READY,
                            active_version=self._safe_active_version(),
                            available_version=(
                                self._candidate.version
                                if self._candidate is not None
                                else ""
                            ),
                            target=self.target.value,
                            reason_code="license-approval-required",
                            message=_ready_message(
                                self.target,
                                (
                                    self._candidate.version
                                    if self._candidate is not None
                                    else ""
                                ),
                            ),
                            checked_at_utc=self.snapshot.checked_at_utc,
                        )
                    )
                except Exception as exc:  # noqa: BLE001 - publish bounded truth
                    self._publish_failure(
                        _safe_reason_code(exc),
                        (
                            "Jamulus update could not be completed. WebJam "
                            "kept the current known-good version."
                        ),
                    )
                finally:
                    if refresh_previous_after:
                        self._refresh_previous_snapshot()
                    with self._state_lock:
                        self._cancellation = None
                        self._operation_thread = None
                        # Terminal publications occur while this worker owns
                        # the operation. Republish the same underlying state
                        # after ownership clears so rollback does not remain
                        # falsely disabled.
                        final_presentation = self._present(
                            self._presentation.snapshot,
                            operation_active=False,
                        )
                        self._presentation = final_presentation
                        # Keep new worker admission serialized through this
                        # final notification so an older terminal callback
                        # cannot arrive after a newer CHECKING state.
                        self._notify(final_presentation)

            worker = threading.Thread(target=run, daemon=True, name=name)
            self._operation_thread = worker
            worker.start()
            return True

    def _publish_failure(self, reason_code: str, message: str) -> None:
        self._publish(
            JamulusUpdateSnapshot(
                state=JamulusUpdateState.FAILED,
                active_version=self._safe_active_version(),
                available_version=(
                    self._candidate.version if self._candidate is not None else ""
                ),
                target=self.target.value,
                reason_code=reason_code,
                message=message,
                checked_at_utc=self.snapshot.checked_at_utc or _utc_now(),
            )
        )

    def _publish(self, snapshot: JamulusUpdateSnapshot) -> None:
        presentation = self._present(snapshot)
        with self._state_lock:
            self._presentation = presentation
        self._notify(presentation)

    def _notify(self, presentation: JamulusUpdatePresentation) -> None:
        try:
            self.on_snapshot(presentation)
        except Exception:
            # A closing or failed UI consumer must not alter updater state or
            # make a verified component operation fail.
            pass

    def _present(
        self,
        snapshot: JamulusUpdateSnapshot,
        *,
        operation_active: bool | None = None,
    ) -> JamulusUpdatePresentation:
        with self._state_lock:
            previous_version = self._previous_version
            can_rollback = self._rollback_available
            if operation_active is None:
                operation_active = bool(
                    self._operation_thread is not None
                    and self._operation_thread.is_alive()
                )
        is_macos = self.target in {
            ComponentTarget.MACOS_ARM64,
            ComponentTarget.MACOS_X64,
        }
        busy_deferred = (
            snapshot.state is JamulusUpdateState.DEFERRED and snapshot.restart_when_idle
        )
        can_approve = snapshot.state is JamulusUpdateState.READY or (
            busy_deferred and is_macos
        )
        verify_platform_install = not is_macos and (
            snapshot.state is JamulusUpdateState.READY
            or snapshot.reason_code == "finish-platform-installer"
        )
        can_activate = (busy_deferred and not is_macos) or verify_platform_install
        return JamulusUpdatePresentation(
            snapshot=snapshot,
            previous_version=previous_version,
            can_download=snapshot.state is JamulusUpdateState.AVAILABLE,
            can_activate=can_activate,
            can_approve=can_approve,
            can_rollback=can_rollback and not operation_active,
            approve_label=(
                "Review license and install" if is_macos else "Open verified installer"
            ),
            activate_label=(
                "Verify installation"
                if verify_platform_install
                else "Open installer when idle"
            ),
            detail=_detail_for_reason(snapshot.reason_code),
        )

    def _make_platform_store(
        self,
        registry: JamulusCompatibilityRegistry,
    ) -> MacOSJamulusComponentStore:
        if self._platform_store_factory is not None:
            return self._platform_store_factory(registry, self.root)
        return MacOSJamulusComponentStore(
            registry,
            webjam_version=self.webjam_version,
            root=self.root,
        )

    def _make_installed_store(
        self,
        registry: JamulusCompatibilityRegistry,
    ) -> PlatformInstalledJamulusStore:
        if self._installed_store_factory is not None:
            return self._installed_store_factory(registry, self.target, self.root)
        platform_name = (
            "win32" if self.target is ComponentTarget.WINDOWS_X64 else "linux"
        )
        return PlatformInstalledJamulusStore(
            registry,
            target=self.target,
            root=self.root,
            platform_name=platform_name,
            webjam_version=self.webjam_version,
        )

    def _safe_active_version(self) -> str:
        try:
            value = str(self.active_version_provider() or "").strip()
            _version_tuple(value)
            return value
        except Exception:
            return EMBEDDED_FALLBACK_VERSION

    def _default_active_version(self) -> str:
        if self._platform_store is not None:
            try:
                current = self._platform_store.current()
            except JamulusPlatformError:
                current = None
            if current is not None and current.activation_allowed:
                return current.version
        if self._installed_store is not None:
            try:
                current = self._installed_store.current(JamulusRole.CLIENT)
            except JamulusPlatformError:
                current = None
            if current is not None:
                return current.version
        return EMBEDDED_FALLBACK_VERSION

    def _load_cached_registry(self) -> JamulusCompatibilityRegistry:
        """Recognize a previously approved install without making it actionable."""

        envelope = self._read_cached_catalog()
        if envelope is None:
            return self._baseline_registry
        try:
            with InterProcessComponentLock(self.operation_lock_path, timeout=0.0):
                catalog = self.catalog_verifier.verify(
                    envelope,
                    webjam_version=self.webjam_version,
                )
                if not isinstance(catalog, VerifiedComponentCatalog):
                    return self._baseline_registry
                self.sequence_store.compare_and_record(
                    catalog.sequence,
                    catalog.payload_sha256,
                )
                return _merge_registries(self._baseline_registry, catalog.registry)
        except Exception:
            return self._baseline_registry

    def _cache_catalog(self, envelope: bytes) -> None:
        try:
            text = envelope.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise JamulusComponentUpdateError(
                "the verified catalog is not UTF-8"
            ) from exc
        atomic_write_text(self.catalog_cache_path, text, mode=0o600)

    def _read_cached_catalog(self) -> bytes | None:
        try:
            if (
                self.catalog_cache_path.is_symlink()
                or not self.catalog_cache_path.is_file()
                or not 0 < self.catalog_cache_path.stat().st_size <= MAX_CATALOG_BYTES
            ):
                return None
            return self.catalog_cache_path.read_bytes()
        except OSError:
            return None


def _merge_registries(
    baseline: JamulusCompatibilityRegistry,
    catalog: JamulusCompatibilityRegistry,
) -> JamulusCompatibilityRegistry:
    values: dict[tuple[object, ...], JamulusCompatibility] = {
        entry.key: entry for entry in baseline.entries
    }
    for entry in catalog.entries:
        existing = values.get(entry.key)
        if existing is not None and existing != entry:
            if _macos_source_capability_overclaim_is_downgradable(
                existing,
                entry,
            ):
                # Older signed catalogs described the untouched upstream Mac
                # DMG as if it could use WebJam-owned files. Keep the signed
                # artifact/source/publisher evidence, but enforce the narrower
                # baked execution policy until a corrected higher-sequence
                # catalog is published.
                continue
            raise JamulusComponentUpdateError(
                "the signed catalog conflicts with the built-in compatibility record"
            )
        values[entry.key] = entry
    return JamulusCompatibilityRegistry(values.values())


def _macos_source_capability_overclaim_is_downgradable(
    baked: JamulusCompatibility,
    signed: JamulusCompatibility,
) -> bool:
    if (
        baked.key != signed.key
        or baked.target
        not in {
            ComponentTarget.MACOS_ARM64,
            ComponentTarget.MACOS_X64,
        }
        or baked.role not in {JamulusRole.CLIENT, JamulusRole.SERVER}
        or baked.variant != "official"
        or baked.activation_mode is not ActivationMode.PLATFORM_APPROVAL
        or signed.activation_mode is not ActivationMode.PLATFORM_APPROVAL
    ):
        return False
    baked_values = baked.capabilities.values
    signed_values = signed.capabilities.values
    permitted_extra = (
        {"webjam-route-profile"}
        if baked.role is JamulusRole.CLIENT
        else {"recording"}
    )
    if not (
        baked_values < signed_values
        and signed_values - baked_values <= permitted_extra
    ):
        return False
    baked_dict = baked.to_dict()
    signed_dict = signed.to_dict()
    signed_dict["capabilities"] = baked_dict["capabilities"]
    return signed_dict == baked_dict


def _version_tuple(value: str) -> tuple[int, int, int]:
    pieces = str(value).split(".")
    if len(pieces) != 3 or any(
        not piece.isascii() or not piece.isdigit() for piece in pieces
    ):
        raise ValueError("invalid semantic version")
    return tuple(int(piece) for piece in pieces)  # type: ignore[return-value]


def _ready_message(target: ComponentTarget, version: str) -> str:
    if target in {
        ComponentTarget.MACOS_ARM64,
        ComponentTarget.MACOS_X64,
    }:
        return (
            f"Jamulus {version} is verified and ready. Review its open-source "
            "license to install it after the session."
        )
    return (
        f"Jamulus {version} is verified and ready. The operating system will "
        "ask before installing it."
    )


def _detail_for_reason(reason_code: str) -> str:
    return {
        "catalog-offline": (
            "Nothing was changed. You can continue with the embedded fallback "
            "and check again when online."
        ),
        "catalog-secure-connection-failed": (
            "Check your internet connection, VPN or firewall, and system date "
            "and time. If this continues, save a Support Bundle for diagnosis."
        ),
        "catalog-trust-unavailable": (
            "Restart WebJam. If this continues, reinstall the current WebJam "
            "package and save a Support Bundle for diagnosis."
        ),
        "catalog-service-unavailable": (
            "The update service returned an unusable response. Nothing changed; "
            "try Check now later."
        ),
        "license-approval-required": (
            "WebJam never accepts the Jamulus disk-image license silently."
        ),
        "finish-platform-installer": (
            "WebJam will use the new copy only after its exact approved runtime "
            "files, version, architecture, and platform trust policy are proven."
        ),
        "platform-install-not-found": (
            "Finish the operating-system installer first, then choose Verify "
            "installation again."
        ),
        "platform-verification-failed": (
            "The installed copy did not satisfy WebJam’s complete runtime trust "
            "policy. The known-good fallback remains active."
        ),
        "macos-integrated-runtime-required": (
            "The upstream Mac download remains verified source evidence only. "
            "WebJam will not activate it until a separately inventoried, "
            "live-verified integrated runtime is available."
        ),
        "automatic-download-deferred": (
            "Download it when the rehearsal is idle, or choose Download when "
            "you are ready."
        ),
        "catalog-authorization-stale": (
            "The signed approval changed or expired. Choose Check now before "
            "downloading or installing Jamulus."
        ),
        ComponentBusyReason.CLIENT_ACTIVE.value: (
            "End the live music session, then return here to install."
        ),
        ComponentBusyReason.SERVER_ACTIVE.value: (
            "End the hosted session, then return here to install."
        ),
        ComponentBusyReason.REFERENCE_TRACK_ACTIVE.value: (
            "Stop Shared Track, then return here to install."
        ),
        ComponentBusyReason.RECORDING_ACTIVE.value: (
            "Finish the recording, then return here to install."
        ),
        ComponentBusyReason.PRACTICE_ACTIVE.value: (
            "End solo practice, then return here to install."
        ),
        ComponentBusyReason.RECONNECT_PENDING.value: (
            "Wait for reconnection to finish, then try again."
        ),
        ComponentBusyReason.LAUNCH_IN_PROGRESS.value: (
            "Wait for session startup to finish, then try again."
        ),
        ComponentBusyReason.ANOTHER_INSTANCE_ACTIVE.value: (
            "Another WebJam process owns a Jamulus runtime. Close or finish that "
            "session, then try again."
        ),
    }.get(str(reason_code), "")


def _catalog_fetch_reason(exc: Exception) -> str:
    if isinstance(exc, ComponentTlsTrustError):
        return "catalog-trust-unavailable"
    if isinstance(exc, ComponentSecureConnectionError):
        return "catalog-secure-connection-failed"
    if isinstance(exc, (CatalogFetchError, ComponentDownloadError)):
        return "catalog-service-unavailable"
    return "catalog-offline"


def _safe_transport_diagnostics(fetcher: object) -> dict[str, str]:
    diagnostics = getattr(fetcher, "security_diagnostics", None)
    if not callable(diagnostics):
        return {"trust_source": "injected", "trust_status": "unknown"}
    try:
        value = diagnostics()
    except Exception:  # noqa: BLE001 - support facts remain optional
        return {"trust_source": "unavailable", "trust_status": "unknown"}
    if not isinstance(value, dict):
        return {"trust_source": "unavailable", "trust_status": "unknown"}
    return {
        key: str(value[key])
        for key in (
            "trust_source",
            "trust_status",
            "environment_ca_overrides",
            "redirect_policy",
        )
        if isinstance(value.get(key), str)
    }


def _catalog_fetch_message(reason_code: str) -> str:
    return {
        "catalog-trust-unavailable": (
            "WebJam's secure Jamulus update checker is unavailable. WebJam "
            "kept the current known-good Jamulus copy."
        ),
        "catalog-secure-connection-failed": (
            "WebJam could not establish a trusted connection to check for "
            "Jamulus updates. The current known-good copy is unchanged."
        ),
        "catalog-service-unavailable": (
            "The Jamulus update service returned an unusable response. WebJam "
            "kept the current known-good Jamulus copy."
        ),
    }.get(
        str(reason_code),
        (
            "Jamulus update checking is offline. WebJam will keep using its "
            "known-good Jamulus copy."
        ),
    )


def _safe_reason_code(exc: Exception) -> str:
    if isinstance(exc, CatalogAuthorizationStale):
        return "catalog-authorization-stale"
    if isinstance(exc, ComponentDownloadError):
        return "download-failed"
    if isinstance(exc, JamulusPlatformError):
        return "platform-verification-failed"
    name = type(exc).__name__.lower()
    allowed = {
        "componentcatalogexpired": "catalog-expired",
        "componentcatalogrollback": "catalog-rollback-rejected",
        "componentcatalogequivocation": "catalog-equivocation-rejected",
        "componentcatalogsignatureerror": "catalog-signature-rejected",
        "componentlocktimeout": "another-instance-busy",
    }
    return allowed.get(name, "update-failed")


def _safe_busy_check(callback: BusyCheck) -> ComponentBusyStatus | None:
    try:
        result = callback()
    except Exception as exc:
        raise JamulusComponentUpdateError(
            "the updater could not prove the session is idle"
        ) from exc
    if result is not None and not isinstance(result, ComponentBusyStatus):
        raise JamulusComponentUpdateError("the updater received an invalid busy state")
    return result


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


__all__ = [
    "DEFAULT_COMPONENT_CATALOG_URL",
    "EMBEDDED_FALLBACK_VERSION",
    "CatalogAuthorizationStale",
    "CatalogFetchError",
    "JamulusComponentUpdateError",
    "JamulusComponentUpdateService",
    "JamulusUpdatePresentation",
    "SignedCatalogFetcher",
]
