from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import ssl
import subprocess
import sys
import urllib.request

import certifi
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest

from core.component_catalog import (
    CatalogKeyring,
    CatalogPublicKey,
    CatalogSequenceStore,
    ComponentCatalogEquivocation,
    ComponentCatalogError,
    ComponentCatalogExpired,
    ComponentCatalogRollback,
    ComponentCatalogSignatureError,
    ComponentCatalogVerifier,
)
from core.component_catalog_signing import sign_component_catalog
from core.component_download import (
    ComponentDownloadCancelled,
    ComponentDownloadError,
    ComponentDownloadIntegrityError,
    ComponentTlsTrustError,
    DownloadCancellation,
    OpenedDownload,
    SecureComponentDownloader,
    UrllibHttpsTransport,
    verify_downloaded_file,
)
from core.component_hosts import (
    ComponentUrlError,
    JAMULUS_RELEASE_HOST_POLICY,
)
from core.component_lock import (
    ComponentLockTimeout,
    InterProcessComponentLock,
)
from core.component_store import (
    ComponentBusyReason,
    ComponentBusyStatus,
    ComponentStoreError,
    ComponentTreeIntegrityError,
    ManagedComponentStore,
    default_component_store_root,
)
from core.jamulus_compatibility import (
    ActivationMode,
    ArtifactIdentity,
    ArtifactKind,
    ComponentTarget,
    JamulusCapabilities,
    JamulusCompatibility,
    JamulusCompatibilityError,
    JamulusCompatibilityRegistry,
    JamulusRole,
    JamulusSourceIdentity,
    LegalInventory,
    RuntimeFileIdentity,
    SourceProvenance,
    WebJamVersionRange,
    official_jamulus_compatibility_registry,
)
from core.jamulus_component_resolver import (
    ComponentOrigin,
    ComponentResolutionError,
    JamulusComponentResolver,
    ValidatedExternalComponent,
)
from core.jamulus_update_state import JamulusUpdateSnapshot, JamulusUpdateState


NOW = datetime(2026, 7, 28, 18, 0, tzinfo=timezone.utc)


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _entry(
    *,
    version: str = "3.12.3",
    target: ComponentTarget = ComponentTarget.MACOS_ARM64,
    role: JamulusRole = JamulusRole.CLIENT,
    variant: str = "official",
    activation: ActivationMode = ActivationMode.MANAGED,
    executable_data: bytes | None = None,
    artifact_data: bytes | None = None,
    capabilities: frozenset[str] | None = None,
) -> tuple[JamulusCompatibility, dict[str, bytes], bytes]:
    executable_data = executable_data or f"jamulus-{version}".encode()
    artifact_data = artifact_data or f"artifact-{version}-{target.value}".encode()
    runtime_data = {
        "bin/Jamulus": executable_data,
        "share/release.txt": version.encode(),
    }
    runtime_files = (
        tuple(
            RuntimeFileIdentity(
                relative_path=path,
                size=len(data),
                sha256=_digest(data),
                executable=path == "bin/Jamulus",
            )
            for path, data in runtime_data.items()
        )
        if activation is ActivationMode.MANAGED
        else ()
    )
    executable_path = "bin/Jamulus" if runtime_files else ""
    entry = JamulusCompatibility(
        component_id="jamulus",
        role=role,
        target=target,
        version=version,
        variant=variant,
        source=JamulusSourceIdentity(
            repository="jamulussoftware/jamulus",
            tag=f"r{version.replace('.', '_')}",
            commit="a" * 40,
            provenance=SourceProvenance.OFFICIAL_RELEASE,
        ),
        artifact=ArtifactIdentity(
            url=(
                "https://github.com/jamulussoftware/jamulus/releases/"
                f"download/r{version.replace('.', '_')}/jamulus-{version}.zip"
            ),
            filename=f"jamulus-{version}.zip",
            size=len(artifact_data),
            sha256=_digest(artifact_data),
            kind=ArtifactKind.ARCHIVE,
        ),
        runtime_files=runtime_files,
        executable_relative_path=executable_path,
        capabilities=JamulusCapabilities(
            capabilities
            or frozenset({"audio-client", "json-rpc-client", "native-gui"})
        ),
        webjam_range=WebJamVersionRange("0.22.0", "0.22.999"),
        legal=LegalInventory(
            license_files=("licenses/JAMULUS_COPYING.txt",),
            notice_files=("THIRD_PARTY_NOTICES.md",),
            source_offer="THIRD_PARTY_NOTICES.md",
        ),
        activation_mode=activation,
        publisher="Jamulus upstream release",
    )
    return entry, runtime_data, artifact_data


def _populate_staging(store: ManagedComponentStore, entry, data):
    staging = store.create_staging(entry)
    for relative, contents in data.items():
        path = staging.payload_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents)
        path.chmod(0o755 if relative == entry.executable_relative_path else 0o644)
    return staging


def _test_signer(tmp_path: Path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    private = Ed25519PrivateKey.generate()
    private_path = tmp_path / "catalog-private.pem"
    private_path.write_bytes(
        private.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    private_path.chmod(0o600)
    raw_public = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    key = CatalogPublicKey(key_id="test-key", raw_key=raw_public)
    return private_path, CatalogKeyring((key,))


def _payload(entry: JamulusCompatibility, *, sequence: int = 1) -> dict:
    return {
        "schema": 1,
        "sequence": sequence,
        "issued_at": "2026-07-28T17:55:00Z",
        "expires_at": "2026-08-20T17:55:00Z",
        "webjam_version": "0.22.0",
        "components": [entry.to_dict()],
    }


def _signed(tmp_path, payload):
    private_path, keyring = _test_signer(tmp_path)
    envelope = sign_component_catalog(
        payload,
        private_key_path=private_path,
        key_id="test-key",
        keyring=keyring,
    )
    return envelope, keyring


class _Body(io.BytesIO):
    def __init__(self, data: bytes, *, headers=None, status=200):
        super().__init__(data)
        self.status = status
        self.headers = headers or {}


class _Transport:
    def __init__(self, data: bytes, *, headers=None, redirect_count=0):
        self.data = data
        self.headers = headers or {}
        self.redirect_count = redirect_count
        self.calls = 0

    def open(self, url, *, policy, cancellation):
        self.calls += 1
        policy.validate_source(url)
        cancellation.raise_if_cancelled()
        return OpenedDownload(
            _Body(self.data, headers=self.headers),
            redirect_count=self.redirect_count,
        )


def test_official_registry_centralizes_exact_3122_and_3123_artifacts():
    registry = official_jamulus_compatibility_registry()
    assert len(registry.entries) == 18
    windows = registry.exact(
        component_id="jamulus",
        role=JamulusRole.CLIENT,
        target=ComponentTarget.WINDOWS_X64,
        version="3.12.3",
        variant="official",
    )
    assert windows.artifact.size == 84_406_464
    assert (
        windows.artifact.sha256
        == "008918b1564b2a46f1a371d7e3df661a0d710689383dab5c61b80be3c4aaf5a1"
    )
    assert windows.legal.license_files == (
        "licenses/JAMULUS_COPYING-r3_12_3.txt",
    )
    assert windows.publisher == (
        "Unsigned upstream installer; exact WebJam-approved SHA-256"
    )
    fallback = registry.exact(
        component_id="jamulus",
        role=JamulusRole.CLIENT,
        target=ComponentTarget.MACOS_ARM64,
        version="3.12.2",
        variant="official",
    )
    assert fallback.source.commit == "ffca974ed4e47b8f4621f3b583c00db2f87974fa"
    assert fallback.legal.license_files == ("licenses/JAMULUS_COPYING.txt",)
    mac = registry.exact(
        component_id="jamulus",
        role=JamulusRole.CLIENT,
        target=ComponentTarget.MACOS_ARM64,
        version="3.12.3",
        variant="official",
    )
    assert mac.publisher == (
        "Developer ID Application: Jonathan Chung (V9ZZ6B9WH8)"
    )
    assert mac.capabilities.includes(
        {"audio-client", "json-rpc-client", "native-gui"}
    )
    assert not mac.capabilities.includes({"webjam-route-profile"})
    mac_server = registry.exact(
        component_id="jamulus",
        role=JamulusRole.SERVER,
        target=ComponentTarget.MACOS_ARM64,
        version="3.12.3",
        variant="official",
    )
    assert mac_server.capabilities.includes(
        {"audio-server", "json-rpc-server"}
    )
    assert not mac_server.capabilities.includes({"recording"})
    assert windows.capabilities.includes({"webjam-route-profile"})
    headless = registry.exact(
        component_id="jamulus",
        role=JamulusRole.HEADLESS,
        target=ComponentTarget.LINUX_X64,
        version="3.12.3",
        variant="official-headless",
    )
    assert headless.capabilities.includes(
        {"headless", "audio-server", "json-rpc-server"}
    )
    assert not headless.capabilities.includes({"audio-client"})
    candidates = registry.compatible(
        role=JamulusRole.CLIENT,
        target=ComponentTarget.MACOS_ARM64,
        webjam_version="0.22.0",
    )
    assert [item.version for item in candidates] == ["3.12.3", "3.12.2"]


def test_official_registry_authorizes_exact_v0270_but_no_future_patch():
    registry = official_jamulus_compatibility_registry()

    assert all(entry.supports_webjam("0.27.0") for entry in registry.entries)
    assert not any(entry.supports_webjam("0.27.1") for entry in registry.entries)

    for target in ComponentTarget:
        for role in (JamulusRole.CLIENT, JamulusRole.SERVER):
            candidates = registry.compatible(
                role=role,
                target=target,
                webjam_version="0.27.0",
            )
            assert [item.version for item in candidates] == ["3.12.3", "3.12.2"]
            assert registry.compatible(
                role=role,
                target=target,
                webjam_version="0.27.1",
            ) == ()


def test_compatibility_round_trip_is_exact():
    entry, _, _ = _entry()
    assert JamulusCompatibility.from_dict(entry.to_dict()) == entry
    assert entry.runtime_digest == JamulusCompatibility.from_dict(
        entry.to_dict()
    ).runtime_digest


def test_registry_combines_identical_entries_but_rejects_conflicts():
    entry, _, _ = _entry()
    one = JamulusCompatibilityRegistry((entry,))
    assert JamulusCompatibilityRegistry.combine(one, one).entries == (entry,)
    changed = entry.to_dict()
    changed["publisher"] = "Different publisher"
    conflict = JamulusCompatibilityRegistry(
        (JamulusCompatibility.from_dict(changed),)
    )
    with pytest.raises(JamulusCompatibilityError, match="conflicting"):
        JamulusCompatibilityRegistry.combine(one, conflict)


@pytest.mark.parametrize(
    "mutation,match",
    [
        (
            lambda value: value["runtime_files"].append(
                {
                    **value["runtime_files"][0],
                    "relative_path": "BIN/jamulus",
                }
            ),
            "case-insensitive",
        ),
        (
            lambda value: value["artifact"].update(url="http://github.com/a/b"),
            "HTTPS",
        ),
        (
            lambda value: value.update(executable_relative_path="../Jamulus"),
            "safe relative",
        ),
    ],
)
def test_compatibility_rejects_unsafe_identity(mutation, match):
    entry, _, _ = _entry()
    value = entry.to_dict()
    mutation(value)
    with pytest.raises(JamulusCompatibilityError, match=match):
        JamulusCompatibility.from_dict(value)


@pytest.mark.parametrize(
    "filename",
    [
        "C:Jamulus.exe",
        "Jamulus.exe:payload",
        "CON",
        "con.txt",
        "AUX.log",
        "COM1.exe",
        "lpt9.zip",
        "Jamulus.",
        "Jamulus ",
    ],
)
def test_artifact_filename_rejects_windows_ambiguous_names(filename):
    entry, _, _ = _entry()
    value = entry.to_dict()
    value["artifact"]["filename"] = filename
    with pytest.raises(JamulusCompatibilityError, match="portable|Windows"):
        JamulusCompatibility.from_dict(value)


@pytest.mark.parametrize(
    "relative_path",
    [
        "C:/Jamulus.exe",
        "bin/Jamulus.exe:payload",
        "bin/NUL",
        "bin/nul.exe",
        "share/COM9.txt",
        "share/LPT1.log",
        "bin/Jamulus.",
        "bin/Jamulus ",
    ],
)
def test_runtime_inventory_rejects_windows_ambiguous_paths(relative_path):
    entry, _, _ = _entry()
    value = entry.to_dict()
    value["runtime_files"][0]["relative_path"] = relative_path
    with pytest.raises(JamulusCompatibilityError, match="portable|Windows"):
        JamulusCompatibility.from_dict(value)


def test_managed_entry_requires_exact_runtime_inventory():
    entry, _, _ = _entry(activation=ActivationMode.PLATFORM_APPROVAL)
    value = entry.to_dict()
    value["activation_mode"] = "managed"
    with pytest.raises(JamulusCompatibilityError, match="runtime file inventory"):
        JamulusCompatibility.from_dict(value)


def test_patched_source_is_limited_to_headless_and_exact_patch():
    entry, _, _ = _entry()
    value = entry.to_dict()
    value["source"]["provenance"] = "webjam-patched-build"
    value["source"]["patch_sha256"] = "b" * 64
    with pytest.raises(JamulusCompatibilityError, match="only.*headless"):
        JamulusCompatibility.from_dict(value)
    value["role"] = "headless"
    value["capabilities"].append("headless")
    with pytest.raises(
        JamulusCompatibilityError, match="exact corresponding source"
    ):
        JamulusCompatibility.from_dict(value)


def test_signed_catalog_verifies_and_records_monotonic_sequence(tmp_path):
    entry, _, _ = _entry()
    envelope, keyring = _signed(tmp_path, _payload(entry, sequence=7))
    sequence = CatalogSequenceStore(tmp_path / "sequence.json")
    verified = ComponentCatalogVerifier(
        keyring=keyring, sequence_store=sequence, now=lambda: NOW
    ).verify(envelope, webjam_version="0.22.0")
    assert verified.sequence == 7
    assert verified.registry.require_exact(entry) == entry
    assert sequence.snapshot() == (7, verified.payload_sha256)
    assert verified.to_snapshot_dict()["component_count"] == 1


def test_catalog_tampering_fails_signature(tmp_path):
    entry, _, _ = _entry()
    envelope, keyring = _signed(tmp_path, _payload(entry))
    value = json.loads(envelope)
    value["payload"]["sequence"] = 2
    tampered = json.dumps(value).encode()
    with pytest.raises(ComponentCatalogSignatureError, match="verification"):
        ComponentCatalogVerifier(keyring=keyring, now=lambda: NOW).verify(
            tampered, webjam_version="0.22.0"
        )


def test_catalog_rejects_expired_future_and_long_lived_payloads(tmp_path):
    entry, _, _ = _entry()
    expired = _payload(entry)
    expired["expires_at"] = "2026-07-28T17:59:59Z"
    envelope, keys = _signed(tmp_path / "expired", expired)
    with pytest.raises(ComponentCatalogExpired):
        ComponentCatalogVerifier(keyring=keys, now=lambda: NOW).verify(
            envelope, webjam_version="0.22.0"
        )

    future = _payload(entry)
    future["issued_at"] = "2026-07-28T18:06:00Z"
    envelope, keys = _signed(tmp_path / "future", future)
    with pytest.raises(ComponentCatalogError, match="future"):
        ComponentCatalogVerifier(keyring=keys, now=lambda: NOW).verify(
            envelope, webjam_version="0.22.0"
        )

    long_lived = _payload(entry)
    long_lived["expires_at"] = "2026-09-30T17:55:00Z"
    envelope, keys = _signed(tmp_path / "long", long_lived)
    with pytest.raises(ComponentCatalogError, match="too long"):
        ComponentCatalogVerifier(keyring=keys, now=lambda: NOW).verify(
            envelope, webjam_version="0.22.0"
        )


def test_catalog_sequence_rejects_rollback_and_equivocation(tmp_path):
    entry, _, _ = _entry()
    private_path, keys = _test_signer(tmp_path)
    sequence = CatalogSequenceStore(tmp_path / "state" / "sequence.json")
    verifier = ComponentCatalogVerifier(
        keyring=keys, sequence_store=sequence, now=lambda: NOW
    )

    def sign(payload):
        return sign_component_catalog(
            payload,
            private_key_path=private_path,
            key_id="test-key",
            keyring=keys,
        )

    verifier.verify(sign(_payload(entry, sequence=9)), webjam_version="0.22.0")
    verifier.verify(sign(_payload(entry, sequence=9)), webjam_version="0.22.0")
    with pytest.raises(ComponentCatalogRollback):
        verifier.verify(sign(_payload(entry, sequence=8)), webjam_version="0.22.0")
    changed = _payload(entry, sequence=9)
    changed["expires_at"] = "2026-08-19T17:55:00Z"
    with pytest.raises(ComponentCatalogEquivocation):
        verifier.verify(sign(changed), webjam_version="0.22.0")


def test_catalog_sequence_state_rejects_symlink_and_corruption(tmp_path):
    target = tmp_path / "target.json"
    target.write_text(
        '{"schema":1,"highest_sequence":1,"payload_sha256":"' + "a" * 64 + '"}'
    )
    linked = tmp_path / "linked.json"
    linked.symlink_to(target)
    with pytest.raises(ComponentCatalogError, match="symlink"):
        CatalogSequenceStore(linked).compare_and_record(2, "b" * 64)
    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text('{"schema":1}')
    with pytest.raises(ComponentCatalogError, match="schema"):
        CatalogSequenceStore(corrupt).compare_and_record(2, "b" * 64)


def test_catalog_rejects_duplicate_json_keys_and_unknown_signer(tmp_path):
    duplicate = b'{"payload":{},"payload":{},"signature":{}}'
    with pytest.raises(ComponentCatalogError, match="duplicate"):
        ComponentCatalogVerifier(now=lambda: NOW).verify(
            duplicate, webjam_version="0.22.0"
        )
    entry, _, _ = _entry()
    envelope, _ = _signed(tmp_path, _payload(entry))
    with pytest.raises(ComponentCatalogSignatureError, match="unknown"):
        ComponentCatalogVerifier(now=lambda: NOW).verify(
            envelope, webjam_version="0.22.0"
        )


def test_catalog_rejects_wrong_webjam_and_unapproved_origin(tmp_path):
    entry, _, _ = _entry()
    payload = _payload(entry)
    payload["components"][0]["artifact"]["url"] = (
        "https://example.com/releases/jamulus.zip"
    )
    envelope, keys = _signed(tmp_path, payload)
    with pytest.raises(ComponentCatalogError, match="origin"):
        ComponentCatalogVerifier(keyring=keys, now=lambda: NOW).verify(
            envelope, webjam_version="0.22.0"
        )
    envelope, keys = _signed(tmp_path / "version", _payload(entry))
    with pytest.raises(ComponentCatalogError, match="exact WebJam"):
        ComponentCatalogVerifier(keyring=keys, now=lambda: NOW).verify(
            envelope, webjam_version="0.22.1"
        )


def test_signer_requires_private_key_permissions_and_matching_key(tmp_path):
    entry, _, _ = _entry()
    path, keys = _test_signer(tmp_path)
    path.chmod(0o644)
    with pytest.raises(ComponentCatalogError, match="group or others"):
        sign_component_catalog(
            _payload(entry),
            private_key_path=path,
            key_id="test-key",
            keyring=keys,
        )
    path.chmod(0o600)
    other = Ed25519PrivateKey.generate().public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    with pytest.raises(ComponentCatalogError, match="does not match"):
        sign_component_catalog(
            _payload(entry),
            private_key_path=path,
            key_id="test-key",
            keyring=CatalogKeyring((CatalogPublicKey("test-key", other),)),
        )
    linked = tmp_path / "linked-key.pem"
    linked.symlink_to(path)
    with pytest.raises(ComponentCatalogError, match="non-symlink"):
        sign_component_catalog(
            _payload(entry),
            private_key_path=linked,
            key_id="test-key",
            keyring=keys,
        )


def test_embedded_production_catalog_key_has_expected_fingerprint():
    key = CatalogKeyring.embedded().require("webjam-component-2026-07")
    assert (
        key.fingerprint_sha256
        == "ea6ba7a52aa37c0d289f5258d34134d11063e5697ce26fd039c2431d3546a687"
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/jamulussoftware/jamulus/x",
        "https://127.0.0.1/x",
        "https://user:pass@github.com/x",
        "https://github.com:444/x",
        "https://github.com/a/../secret",
        "https://github.com/a/%252e%252e/secret",
        "https://github.com/a/file?token=secret",
    ],
)
def test_host_policy_rejects_unsafe_source_urls(url):
    with pytest.raises(ComponentUrlError):
        JAMULUS_RELEASE_HOST_POLICY.validate_source(url)


def test_host_policy_allows_only_approved_https_redirects():
    source = "https://github.com/jamulussoftware/jamulus/releases/file"
    allowed = JAMULUS_RELEASE_HOST_POLICY.validate_redirect(
        source,
        "https://release-assets.githubusercontent.com/a/file?token=opaque",
    )
    assert allowed.startswith("https://release-assets.githubusercontent.com/")
    with pytest.raises(ComponentUrlError):
        JAMULUS_RELEASE_HOST_POLICY.validate_redirect(
            source, "https://evil.example/file"
        )


def test_https_transport_uses_packaged_ca_bytes_and_ignores_environment(
    monkeypatch,
):
    calls: list[dict[str, object]] = []
    real_create_default_context = ssl.create_default_context

    def create_default_context(*args, **kwargs):
        calls.append(dict(kwargs))
        return real_create_default_context(*args, **kwargs)

    monkeypatch.setenv("SSL_CERT_FILE", "/untrusted/private-ca.pem")
    monkeypatch.setenv("SSL_CERT_DIR", "/untrusted/private-ca-directory")
    monkeypatch.setattr(ssl, "create_default_context", create_default_context)

    transport = UrllibHttpsTransport()
    opener = transport._secure_opener()

    assert len(calls) == 1
    assert calls[0] == {"cadata": certifi.contents()}
    handler = next(
        item
        for item in opener.handlers
        if isinstance(item, urllib.request.HTTPSHandler)
    )
    context = handler._context
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname
    assert context.minimum_version >= ssl.TLSVersion.TLSv1_2
    assert transport.security_diagnostics() == {
        "trust_source": "packaged-certifi",
        "trust_status": "ready",
        "environment_ca_overrides": "ignored",
        "redirect_policy": "explicit-allowlist",
    }


def test_offline_component_helpers_import_without_site_packages():
    completed = subprocess.run(
        [
            sys.executable,
            "-S",
            "-c",
            "from core.component_download import verify_downloaded_file",
        ],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "PYTHONNOUSERSITE": "1"},
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize(
    "failure",
    [
        "",
        "not a public CA bundle",
        OSError("/Users/private/missing-ca.pem"),
    ],
)
def test_https_transport_rejects_missing_or_invalid_ca_without_path_text(
    monkeypatch,
    failure,
):
    def contents():
        if isinstance(failure, Exception):
            raise failure
        return failure

    monkeypatch.setattr(certifi, "contents", contents)
    transport = UrllibHttpsTransport()

    with pytest.raises(ComponentTlsTrustError) as raised:
        transport._secure_opener()

    assert "/Users/private" not in str(raised.value)
    assert transport.security_diagnostics()["trust_status"] == "unavailable"


def test_downloader_streams_exact_bytes_atomically(tmp_path):
    entry, _, artifact_data = _entry()
    progress = []
    transport = _Transport(
        artifact_data,
        headers={
            "Content-Length": str(len(artifact_data)),
            "Content-Encoding": "identity",
        },
        redirect_count=2,
    )
    result = SecureComponentDownloader(transport=transport).download(
        entry.artifact,
        destination_directory=tmp_path / "downloads with spaces",
        progress=progress.append,
    )
    assert result.path.read_bytes() == artifact_data
    assert result.sha256 == entry.artifact.sha256
    assert result.redirect_count == 2
    assert progress[-1].fraction == 1.0
    assert not list(result.path.parent.glob("*.part"))


@pytest.mark.parametrize(
    "data,headers",
    [
        (b"short", {}),
        (b"x" * 50, {}),
        (b"artifact-3.12.3-macos-arm64", {"Content-Length": "1"}),
        (
            b"artifact-3.12.3-macos-arm64",
            {"Content-Encoding": "gzip"},
        ),
    ],
)
def test_downloader_rejects_size_hash_or_encoding_mismatch(tmp_path, data, headers):
    entry, _, _ = _entry()
    with pytest.raises(ComponentDownloadIntegrityError):
        SecureComponentDownloader(
            transport=_Transport(data, headers=headers)
        ).download(entry.artifact, destination_directory=tmp_path)
    assert not list(tmp_path.glob("*.part"))
    assert not (tmp_path / entry.artifact.filename).exists()


def test_downloader_cancellation_cleans_partial_file(tmp_path):
    data = b"x" * 10_000
    entry, _, _ = _entry(artifact_data=data)
    cancellation = DownloadCancellation()

    def progress(value):
        if value.received:
            cancellation.cancel()

    with pytest.raises(ComponentDownloadCancelled):
        SecureComponentDownloader(
            transport=_Transport(data), chunk_size=4096
        ).download(
            entry.artifact,
            destination_directory=tmp_path,
            cancellation=cancellation,
            progress=progress,
        )
    assert not list(tmp_path.iterdir())


def test_downloader_rejects_symlinked_destination_directory(tmp_path):
    entry, _, artifact_data = _entry()
    actual = tmp_path / "actual"
    actual.mkdir()
    linked = tmp_path / "downloads"
    linked.symlink_to(actual)
    with pytest.raises(ComponentDownloadError, match="symlink"):
        SecureComponentDownloader(
            transport=_Transport(artifact_data)
        ).download(entry.artifact, destination_directory=linked)
    assert not list(actual.iterdir())


def test_download_verification_rejects_symlink_and_tampering(tmp_path):
    entry, _, artifact_data = _entry()
    path = tmp_path / "artifact"
    path.write_bytes(artifact_data)
    assert verify_downloaded_file(path, entry.artifact).sha256 == _digest(
        artifact_data
    )
    path.write_bytes(b"x" * len(artifact_data))
    with pytest.raises(ComponentDownloadIntegrityError, match="hash"):
        verify_downloaded_file(path, entry.artifact)
    path.unlink()
    target = tmp_path / "target"
    target.write_bytes(artifact_data)
    path.symlink_to(target)
    with pytest.raises(ComponentDownloadIntegrityError, match="regular"):
        verify_downloaded_file(path, entry.artifact)


def test_component_lock_has_bounded_contention(tmp_path):
    lock_path = tmp_path / "component.lock"
    with InterProcessComponentLock(lock_path):
        with pytest.raises(ComponentLockTimeout):
            with InterProcessComponentLock(lock_path, timeout=0.01):
                pass


def test_default_component_store_roots_are_platform_specific(tmp_path):
    assert default_component_store_root(
        platform_name="darwin", home=tmp_path
    ) == tmp_path / "Library/Application Support/WebJam/components"
    assert default_component_store_root(
        platform_name="win32",
        home=tmp_path,
        environ={"LOCALAPPDATA": "relative"},
    ) == tmp_path / "AppData/Local/WebJam/components"
    assert default_component_store_root(
        platform_name="linux",
        home=tmp_path,
        environ={"XDG_DATA_HOME": str(tmp_path / "xdg")},
    ) == tmp_path / "xdg/webjam/components"


def test_store_install_ready_activate_busy_upgrade_and_rollback(tmp_path):
    old, old_data, _ = _entry(version="3.12.2")
    new, new_data, _ = _entry(version="3.12.3")
    store = ManagedComponentStore(
        JamulusCompatibilityRegistry((old, new)), root=tmp_path / "components"
    )
    old_snapshot = store.commit_staging(_populate_staging(store, old, old_data))
    assert old_snapshot.is_ready
    activated = store.activate(old)
    assert activated.activated
    assert activated.current.entry.version == "3.12.2"

    ready = store.commit_staging(_populate_staging(store, new, new_data))
    assert ready.is_ready and store.current(
        component_id="jamulus",
        role=JamulusRole.CLIENT,
        target=ComponentTarget.MACOS_ARM64,
    ).entry.version == "3.12.2"
    deferred = store.activate(
        new,
        busy_check=lambda: ComponentBusyStatus(
            ComponentBusyReason.REFERENCE_TRACK_ACTIVE,
            "Reference Track is active.",
        ),
    )
    assert not deferred.activated
    assert deferred.deferred.reason is ComponentBusyReason.REFERENCE_TRACK_ACTIVE
    assert store.ready(
        component_id="jamulus",
        role=JamulusRole.CLIENT,
        target=ComponentTarget.MACOS_ARM64,
    ).entry.version == "3.12.3"

    upgraded = store.activate_ready(
        component_id="jamulus",
        role=JamulusRole.CLIENT,
        target=ComponentTarget.MACOS_ARM64,
    )
    assert upgraded.current.entry.version == "3.12.3"
    assert upgraded.previous.entry.version == "3.12.2"
    rolled_back = store.rollback(
        component_id="jamulus",
        role=JamulusRole.CLIENT,
        target=ComponentTarget.MACOS_ARM64,
    )
    assert rolled_back.current.entry.version == "3.12.2"
    assert rolled_back.previous.entry.version == "3.12.3"


def test_store_rejects_unexpected_symlink_tamper_and_bad_busy_callback(tmp_path):
    entry, data, _ = _entry()
    store = ManagedComponentStore(
        JamulusCompatibilityRegistry((entry,)), root=tmp_path / "components"
    )
    staging = _populate_staging(store, entry, data)
    (staging.payload_root / "unexpected").write_text("no")
    with pytest.raises(ComponentTreeIntegrityError, match="inventory"):
        store.commit_staging(staging)
    store.discard_staging(staging)

    staging = _populate_staging(store, entry, data)
    (staging.payload_root / "share/release.txt").unlink()
    (staging.payload_root / "share/release.txt").symlink_to(
        staging.payload_root / "bin/Jamulus"
    )
    with pytest.raises(ComponentTreeIntegrityError, match="symlink"):
        store.commit_staging(staging)
    store.discard_staging(staging)

    store.commit_staging(_populate_staging(store, entry, data))
    with pytest.raises(ComponentStoreError, match="prove.*idle"):
        store.activate(entry, busy_check=lambda: 1 / 0)
    store.activate(entry)
    current = store.current(
        component_id="jamulus",
        role=JamulusRole.CLIENT,
        target=ComponentTarget.MACOS_ARM64,
    )
    current.executable_path.write_bytes(b"tampered")
    with pytest.raises(ComponentTreeIntegrityError, match="size|hash"):
        store.current(
            component_id="jamulus",
            role=JamulusRole.CLIENT,
            target=ComponentTarget.MACOS_ARM64,
        )


def test_store_rejects_hardlinks_and_corrupt_state(tmp_path):
    entry, data, _ = _entry()
    store = ManagedComponentStore(
        JamulusCompatibilityRegistry((entry,)),
        root=tmp_path / "Component Store With Spaces",
    )
    staging = _populate_staging(store, entry, data)
    release = staging.payload_root / "share/release.txt"
    outside = tmp_path / "outside"
    outside.write_bytes(release.read_bytes())
    release.unlink()
    os.link(outside, release)
    with pytest.raises(ComponentTreeIntegrityError, match="hard-linked"):
        store.commit_staging(staging)
    store.discard_staging(staging)

    store.commit_staging(_populate_staging(store, entry, data))
    store.activate(entry)
    store.state_path.write_text('{"schema":1}')
    with pytest.raises(ComponentStoreError, match="schema"):
        store.current(
            component_id="jamulus",
            role=JamulusRole.CLIENT,
            target=ComponentTarget.MACOS_ARM64,
        )


def test_store_failed_atomic_commit_leaves_no_component_or_pointer(
    tmp_path, monkeypatch
):
    entry, data, _ = _entry()
    store = ManagedComponentStore(
        JamulusCompatibilityRegistry((entry,)), root=tmp_path / "components"
    )
    staging = _populate_staging(store, entry, data)
    from core import component_store as store_module

    real_atomic = store_module.atomic_write_text

    def fail_descriptor(path, *args, **kwargs):
        if Path(path).name == "descriptor.json":
            raise OSError("simulated disk failure")
        return real_atomic(path, *args, **kwargs)

    monkeypatch.setattr(store_module, "atomic_write_text", fail_descriptor)
    with pytest.raises(OSError, match="simulated"):
        store.commit_staging(staging)
    assert not store.state_path.exists()
    assert not list(store.components_root.rglob("descriptor.json"))
    assert not list(store.components_root.rglob(".pending-*"))


def test_store_rejects_root_symlink_and_packaged_root(tmp_path):
    entry, _, _ = _entry()
    registry = JamulusCompatibilityRegistry((entry,))
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    with pytest.raises(ComponentStoreError, match="symlink"):
        ManagedComponentStore(registry, root=link)
    blocked = tmp_path / "WebJam.app"
    with pytest.raises(ComponentStoreError, match="outside"):
        ManagedComponentStore(
            registry,
            root=blocked / "Contents/components",
            forbidden_roots=(blocked,),
        )


def test_store_caches_opaque_platform_artifact_without_activating(tmp_path):
    entry, _, artifact_data = _entry(
        activation=ActivationMode.PLATFORM_APPROVAL
    )
    store = ManagedComponentStore(
        JamulusCompatibilityRegistry((entry,)), root=tmp_path / "components"
    )
    cache = store.artifact_cache_directory(entry)
    (cache / entry.artifact.filename).write_bytes(artifact_data)
    snapshot = store.cached_artifact(entry)
    assert snapshot.to_dict()["artifact_sha256"] == entry.artifact.sha256
    with pytest.raises(ComponentStoreError, match="platform-approved"):
        store.create_staging(entry)
    assert (
        store.current(
            component_id="jamulus",
            role=JamulusRole.CLIENT,
            target=ComponentTarget.MACOS_ARM64,
        )
        is None
    )
    snapshot.path.write_bytes(b"x" * entry.artifact.size)
    with pytest.raises(ComponentTreeIntegrityError, match="verification"):
        store.cached_artifact(entry)


def test_store_prunes_only_unreferenced_verified_versions(tmp_path):
    first, first_data, _ = _entry(version="3.12.1")
    second, second_data, _ = _entry(version="3.12.2")
    third, third_data, _ = _entry(version="3.12.3")
    store = ManagedComponentStore(
        JamulusCompatibilityRegistry((first, second, third)),
        root=tmp_path / "components",
    )
    store.commit_staging(_populate_staging(store, first, first_data))
    store.activate(first)
    store.commit_staging(_populate_staging(store, second, second_data))
    store.activate(second)
    store.commit_staging(_populate_staging(store, third, third_data))
    store.activate(third)
    removed = store.prune()
    assert removed == (first.runtime_digest,)
    assert store.current(
        component_id="jamulus",
        role=JamulusRole.CLIENT,
        target=ComponentTarget.MACOS_ARM64,
    ).entry.version == "3.12.3"


def test_resolver_prefers_managed_then_embedded_explicit_and_system(tmp_path):
    entry, data, _ = _entry()
    registry = JamulusCompatibilityRegistry((entry,))
    store = ManagedComponentStore(registry, root=tmp_path / "components")
    store.commit_staging(_populate_staging(store, entry, data))
    store.activate(entry)
    external = tmp_path / "external"
    external.write_bytes(b"external")
    external.chmod(0o755)
    calls = []

    def validator(candidate, candidate_registry, role, target):
        calls.append(candidate.origin)
        return ValidatedExternalComponent(
            entry=candidate_registry.require_exact(entry),
            executable_path=candidate.path,
            content_verified=True,
            version_verified=True,
            architecture_verified=True,
            publisher_verified=True,
        )

    resolver = JamulusComponentResolver(
        registry, managed_store=store, external_validator=validator
    )
    managed = resolver.resolve(
        component_id="jamulus",
        role=JamulusRole.CLIENT,
        target=ComponentTarget.MACOS_ARM64,
        webjam_version="0.22.0",
        embedded_paths=(external,),
    )
    assert managed.origin is ComponentOrigin.MANAGED
    assert calls == []

    fallback_resolver = JamulusComponentResolver(
        registry, external_validator=validator
    )
    embedded = fallback_resolver.resolve(
        component_id="jamulus",
        role=JamulusRole.CLIENT,
        target=ComponentTarget.MACOS_ARM64,
        webjam_version="0.22.0",
        embedded_paths=(external,),
        explicit_paths=(external,),
        system_paths=(external,),
    )
    assert embedded.origin is ComponentOrigin.EMBEDDED
    assert embedded.used_fallback


def test_resolver_treats_platform_verified_update_as_managed_before_fallback(
    tmp_path,
):
    entry, _, _ = _entry(activation=ActivationMode.PLATFORM_APPROVAL)
    registry = JamulusCompatibilityRegistry((entry,))
    managed = tmp_path / "managed" / "Jamulus.app"
    executable = managed / "Contents/MacOS/Jamulus"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"platform verified")
    executable.chmod(0o755)
    fallback = tmp_path / "embedded"
    fallback.write_bytes(b"fallback")
    fallback.chmod(0o755)

    def validator(candidate, *_):
        selected = (
            executable if candidate.origin is ComponentOrigin.MANAGED else fallback
        )
        return ValidatedExternalComponent(
            entry=entry,
            executable_path=selected,
            content_verified=True,
            version_verified=True,
            architecture_verified=True,
            publisher_verified=True,
        )

    resolution = JamulusComponentResolver(
        registry, external_validator=validator
    ).resolve(
        component_id="jamulus",
        role=JamulusRole.CLIENT,
        target=ComponentTarget.MACOS_ARM64,
        webjam_version="0.22.0",
        managed_paths=(managed,),
        embedded_paths=(fallback,),
    )
    assert resolution.origin is ComponentOrigin.MANAGED
    assert not resolution.used_fallback
    assert resolution.executable_path == executable.resolve()


def test_resolver_skips_partial_external_proof_and_requires_capabilities(tmp_path):
    entry, _, _ = _entry()
    registry = JamulusCompatibilityRegistry((entry,))
    first = tmp_path / "first"
    second = tmp_path / "second"
    for path in (first, second):
        path.write_bytes(b"x")
        path.chmod(0o755)

    def validator(candidate, *_):
        return ValidatedExternalComponent(
            entry=entry,
            executable_path=candidate.path,
            content_verified=True,
            version_verified=True,
            architecture_verified=True,
            publisher_verified=candidate.path == second,
        )

    resolver = JamulusComponentResolver(registry, external_validator=validator)
    resolution = resolver.resolve(
        component_id="jamulus",
        role=JamulusRole.CLIENT,
        target=ComponentTarget.MACOS_ARM64,
        webjam_version="0.22.0",
        embedded_paths=(first,),
        explicit_paths=(second,),
        required_capabilities=("json-rpc-client",),
    )
    assert resolution.origin is ComponentOrigin.EXPLICIT
    with pytest.raises(ComponentResolutionError):
        resolver.resolve(
            component_id="jamulus",
            role=JamulusRole.CLIENT,
            target=ComponentTarget.MACOS_ARM64,
            webjam_version="0.22.0",
            system_paths=(second,),
            required_capabilities=("reference-track-zero-return",),
        )


@pytest.mark.parametrize(
    "proof",
    [
        {
            "content_verified": False,
            "version_verified": True,
            "architecture_verified": True,
            "publisher_verified": True,
        },
        {
            "content_verified": True,
            "version_verified": False,
            "architecture_verified": True,
            "publisher_verified": True,
        },
        {
            "content_verified": True,
            "version_verified": True,
            "architecture_verified": False,
            "publisher_verified": True,
        },
        {
            "content_verified": True,
            "version_verified": True,
            "architecture_verified": True,
            "publisher_verified": False,
        },
    ],
)
def test_resolver_rejects_each_incomplete_external_proof(tmp_path, proof):
    entry, _, _ = _entry()
    path = tmp_path / "Jamulus"
    path.write_bytes(b"x")
    path.chmod(0o755)

    def validator(candidate, *_):
        return ValidatedExternalComponent(
            entry=entry, executable_path=candidate.path, **proof
        )

    resolver = JamulusComponentResolver(
        JamulusCompatibilityRegistry((entry,)), external_validator=validator
    )
    with pytest.raises(ComponentResolutionError):
        resolver.resolve(
            component_id="jamulus",
            role=JamulusRole.CLIENT,
            target=ComponentTarget.MACOS_ARM64,
            webjam_version="0.22.0",
            managed_paths=(path,),
        )


def test_resolver_fails_closed_without_external_validator(tmp_path):
    entry, _, _ = _entry()
    path = tmp_path / "Jamulus"
    path.write_bytes(b"x")
    path.chmod(0o755)
    resolver = JamulusComponentResolver(
        JamulusCompatibilityRegistry((entry,))
    )
    assert (
        resolver.resolve_optional(
            component_id="jamulus",
            role=JamulusRole.CLIENT,
            target=ComponentTarget.MACOS_ARM64,
            webjam_version="0.22.0",
            embedded_paths=(path,),
        )
        is None
    )


def test_update_snapshot_is_immutable_serializable_and_transition_checked():
    snapshot = JamulusUpdateSnapshot(
        state=JamulusUpdateState.IDLE,
        target="macos-arm64",
    )
    checking = snapshot.transition(
        JamulusUpdateState.CHECKING,
        message="Checking for an approved Jamulus component.",
    )
    available = checking.transition(
        JamulusUpdateState.AVAILABLE,
        available_version="3.12.3",
    )
    downloading = available.transition(
        JamulusUpdateState.DOWNLOADING, progress_percent=20
    )
    assert (
        JamulusUpdateSnapshot.from_dict(downloading.to_dict()) == downloading
    )
    with pytest.raises(ValueError, match="invalid updater transition"):
        snapshot.transition(JamulusUpdateState.READY)
    with pytest.raises(ValueError, match="between 0 and 100"):
        JamulusUpdateSnapshot(progress_percent=101)
