"""Fail-closed archive handling for the exact frozen updater release gate."""

from __future__ import annotations

import hashlib
import json
import stat
from pathlib import Path
import sys
from types import SimpleNamespace
import warnings
import zipfile

import pytest

import services.jamulus_component_packaged_smoke as packaged_smoke
import tests.support.run_frozen_component_catalog_smoke as smoke_runner
from core.jamulus_compatibility import ComponentTarget, JamulusRole
from tests.support.run_frozen_component_catalog_smoke import (
    _extract_webjam_archive,
    _read_result,
    _runtime_environment,
)


def _write_entry(
    archive: zipfile.ZipFile,
    name: str,
    payload: bytes,
    *,
    mode: int = stat.S_IFREG | 0o644,
) -> None:
    entry = zipfile.ZipInfo(name)
    entry.create_system = 3
    entry.external_attr = mode << 16
    archive.writestr(entry, payload)


def _valid_archive(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        _write_entry(
            archive,
            "WebJam/WebJam",
            b"frozen-binary",
            mode=stat.S_IFREG | 0o755,
        )
        _write_entry(
            archive,
            "WebJam/_internal/certifi/cacert.pem",
            b"certificate-data",
        )


def test_safe_archive_extraction_returns_executable_binary(tmp_path: Path) -> None:
    archive_path = tmp_path / "WebJam-linux-x64.zip"
    _valid_archive(archive_path)

    binary = _extract_webjam_archive(
        archive_path,
        tmp_path / "extracted",
    )

    assert binary.read_bytes() == b"frozen-binary"
    assert binary.stat().st_mode & stat.S_IXUSR


@pytest.mark.parametrize(
    "entry_name",
    (
        "../outside",
        "/absolute",
        r"WebJam\outside",
        "Other/WebJam",
    ),
)
def test_safe_archive_extraction_rejects_unsafe_paths(
    tmp_path: Path,
    entry_name: str,
) -> None:
    archive_path = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        _write_entry(archive, entry_name, b"unsafe")

    with pytest.raises(ValueError, match="path is invalid"):
        _extract_webjam_archive(archive_path, tmp_path / "extracted")


def test_safe_archive_extraction_rejects_symlinks(tmp_path: Path) -> None:
    archive_path = tmp_path / "symlink.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        _write_entry(
            archive,
            "WebJam/WebJam",
            b"outside",
            mode=stat.S_IFLNK | 0o777,
        )

    with pytest.raises(ValueError, match="entry type is invalid"):
        _extract_webjam_archive(archive_path, tmp_path / "extracted")


def test_safe_archive_extraction_rejects_archive_path_symlink(
    tmp_path: Path,
) -> None:
    archive_path = tmp_path / "WebJam-linux-x64.zip"
    archive_link = tmp_path / "download.zip"
    _valid_archive(archive_path)
    try:
        archive_link.symlink_to(archive_path)
    except OSError:
        pytest.skip("This platform cannot create a test symlink.")

    with pytest.raises(ValueError, match="archive identity is invalid"):
        _extract_webjam_archive(archive_link, tmp_path / "extracted")


def test_safe_archive_extraction_rejects_duplicate_paths(tmp_path: Path) -> None:
    archive_path = tmp_path / "duplicate.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        _write_entry(
            archive,
            "WebJam/WebJam",
            b"one",
            mode=stat.S_IFREG | 0o755,
        )
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            _write_entry(
                archive,
                "WebJam/WebJam",
                b"two",
                mode=stat.S_IFREG | 0o755,
            )

    with pytest.raises(ValueError, match="path is invalid"):
        _extract_webjam_archive(archive_path, tmp_path / "extracted")


def test_safe_archive_extraction_requires_exact_app_binary(tmp_path: Path) -> None:
    archive_path = tmp_path / "missing.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        _write_entry(archive, "WebJam/README-LINUX.txt", b"read me")

    with pytest.raises(ValueError, match="no executable app binary"):
        _extract_webjam_archive(archive_path, tmp_path / "extracted")


def test_packaged_smoke_rejects_result_symlink_before_catalog_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = tmp_path / "webjam-component-catalog-smoke-first"
    second = tmp_path / "webjam-component-catalog-smoke-second"
    first.mkdir()
    second.mkdir()
    result_path = first / "result.json"
    try:
        result_path.symlink_to(second / "result.json")
    except OSError:
        pytest.skip("This platform cannot create a test symlink.")
    monkeypatch.setattr(
        packaged_smoke.tempfile,
        "gettempdir",
        lambda: str(tmp_path),
    )

    with pytest.raises(RuntimeError, match="result path is invalid"):
        packaged_smoke.run_frozen_component_catalog_smoke(result_path=result_path)

    assert not (first / "catalog-sequence.json").exists()
    assert not (second / "catalog-sequence.json").exists()


def test_packaged_smoke_reports_exact_verified_catalog_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory = tmp_path / "webjam-component-catalog-smoke-identity"
    directory.mkdir()
    result_path = directory / "result.json"
    envelope = b"exact-public-catalog-envelope"
    payload_sha256 = "b" * 64
    signer_sha256 = "c" * 64
    client = SimpleNamespace(
        component_id="jamulus-official",
        version="3.12.3",
        variant="standard",
        artifact="same-package",
        role=JamulusRole.CLIENT,
        capabilities=SimpleNamespace(includes=lambda _required: False),
    )
    server = SimpleNamespace(
        component_id="jamulus-official",
        version="3.12.3",
        variant="standard",
        artifact="same-package",
        role=JamulusRole.SERVER,
        capabilities=SimpleNamespace(includes=lambda _required: False),
    )
    components = tuple(client if index % 2 == 0 else server for index in range(8))

    class FakeRegistry:
        def compatible(self, **_kwargs: object) -> tuple[object, ...]:
            return (client,)

        def exact(self, **_kwargs: object) -> object:
            return server

    catalog = SimpleNamespace(
        components=components,
        registry=FakeRegistry(),
        sequence=2,
        payload_sha256=payload_sha256,
        signer_fingerprint_sha256=signer_sha256,
    )

    class FakeFetcher:
        def fetch(self, url: str, **_kwargs: object) -> bytes:
            assert url == packaged_smoke.DEFAULT_COMPONENT_CATALOG_URL
            return envelope

        def security_diagnostics(self) -> dict[str, str]:
            return {
                "trust_source": "packaged-certifi",
                "trust_status": "ready",
                "environment_ca_overrides": "ignored",
                "redirect_policy": "explicit-allowlist",
            }

    class FakeVerifier:
        def __init__(self, *, sequence_store: object) -> None:
            self.sequence_store = sequence_store

        def verify(
            self,
            _envelope: bytes,
            *,
            webjam_version: str,
        ) -> object:
            assert webjam_version == packaged_smoke.__version__
            self.sequence_store.compare_and_record(2, payload_sha256)
            return catalog

    monkeypatch.setattr(
        packaged_smoke.tempfile,
        "gettempdir",
        lambda: str(tmp_path),
    )
    monkeypatch.setattr(packaged_smoke, "SignedCatalogFetcher", FakeFetcher)
    monkeypatch.setattr(packaged_smoke, "ComponentCatalogVerifier", FakeVerifier)
    monkeypatch.setattr(
        packaged_smoke,
        "platform_component_target",
        lambda: ComponentTarget.MACOS_ARM64,
    )

    assert (
        packaged_smoke.run_frozen_component_catalog_smoke(result_path=result_path) == 0
    )
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["catalog_envelope_sha256"] == hashlib.sha256(envelope).hexdigest()
    assert result["catalog_payload_sha256"] == payload_sha256
    assert result["signer_fingerprint_sha256"] == signer_sha256
    assert (directory / "catalog-sequence.json").is_file()


def test_runner_rejects_symlink_result_file(tmp_path: Path) -> None:
    target = tmp_path / "target.json"
    target.write_text("{}\n", encoding="utf-8")
    result_path = tmp_path / "result.json"
    try:
        result_path.symlink_to(target)
    except OSError:
        pytest.skip("This platform cannot create a test symlink.")

    with pytest.raises(SystemExit, match="produced no valid result"):
        _read_result(result_path)


def test_runner_uses_clean_environment_and_exact_catalog_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binary = tmp_path / "WebJam"
    binary.write_bytes(b"frozen")
    binary.chmod(0o755)
    envelope_sha256 = "a" * 64
    payload_sha256 = "b" * 64
    signer_sha256 = "c" * 64
    monkeypatch.setenv("GITHUB_TOKEN", "must-not-reach-frozen-process")
    monkeypatch.setenv("QT_QPA_PLATFORM", "caller-controlled")

    def fake_run(
        command: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        stdout: int,
        stderr: int,
        timeout: int,
        check: bool,
    ) -> SimpleNamespace:
        assert command == [str(binary)]
        assert cwd == tmp_path
        assert "GITHUB_TOKEN" not in env
        assert env["QT_QPA_PLATFORM"] == "offscreen"
        assert set(env) == {
            "HOME",
            "LANG",
            "LC_ALL",
            "PATH",
            "QT_QPA_PLATFORM",
            "TEMP",
            "TMP",
            "TMPDIR",
            "WEBJAM_SMOKE_COMPONENT_CATALOG_RUNTIME",
            "WEBJAM_SMOKE_COMPONENT_CATALOG_RESULT",
            "SSL_CERT_FILE",
            "SSL_CERT_DIR",
        }
        assert stdout == smoke_runner.subprocess.DEVNULL
        assert stderr == smoke_runner.subprocess.DEVNULL
        assert timeout == 60
        assert check is False
        result_path = Path(env["WEBJAM_SMOKE_COMPONENT_CATALOG_RESULT"])
        result_path.write_text(
            json.dumps(
                {
                    "marker": smoke_runner.SUCCESS_MARKER,
                    "status": "passed",
                    "webjam_version": "0.22.1",
                    "catalog_sequence": 2,
                    "component_count": 8,
                    "available_version": "3.12.3",
                    "target": "macos-arm64",
                    "catalog_envelope_sha256": envelope_sha256,
                    "catalog_payload_sha256": payload_sha256,
                    "signer_fingerprint_sha256": signer_sha256,
                    "trust_source": "packaged-certifi",
                    "trust_status": "ready",
                    "environment_ca_overrides": "ignored",
                    "redirect_policy": "explicit-allowlist",
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(smoke_runner.subprocess, "run", fake_run)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_frozen_component_catalog_smoke.py",
            "--binary",
            str(binary),
            "--expected-version",
            "0.22.1",
            "--expected-sequence",
            "2",
            "--expected-target",
            "macos-arm64",
            "--expected-jamulus-version",
            "3.12.3",
            "--expected-catalog-envelope-sha256",
            envelope_sha256,
            "--expected-catalog-payload-sha256",
            payload_sha256,
            "--expected-signer-fingerprint-sha256",
            signer_sha256,
        ],
    )

    assert smoke_runner.main() == 0


def test_windows_runtime_environment_is_isolated_and_minimal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory = tmp_path / "runtime"
    runtime_home = directory / "home"
    directory.mkdir()
    runtime_home.mkdir()
    monkeypatch.setenv("SystemRoot", r"C:\Windows")
    monkeypatch.setenv("WINDIR", r"C:\Windows")
    monkeypatch.setenv("COMSPEC", r"C:\Windows\System32\cmd.exe")
    monkeypatch.setenv("PATHEXT", ".COM;.EXE;.BAT;.CMD")
    monkeypatch.setenv("GITHUB_TOKEN", "must-not-reach-frozen-process")
    monkeypatch.setenv("HTTPS_PROXY", "http://must-not-reach.example")
    monkeypatch.setenv("SSL_CERT_FILE", "caller-controlled.pem")

    environment = _runtime_environment(
        directory,
        runtime_home,
        platform_name="nt",
    )

    assert environment["USERPROFILE"] == str(runtime_home)
    assert environment["APPDATA"] == str(runtime_home / "AppData" / "Roaming")
    assert environment["LOCALAPPDATA"] == str(runtime_home / "AppData" / "Local")
    assert Path(environment["APPDATA"]).is_dir()
    assert Path(environment["LOCALAPPDATA"]).is_dir()
    assert environment["SystemRoot"] == r"C:\Windows"
    assert environment["WINDIR"] == r"C:\Windows"
    assert environment["COMSPEC"] == r"C:\Windows\System32\cmd.exe"
    assert environment["PATHEXT"] == ".COM;.EXE;.BAT;.CMD"
    assert "GITHUB_TOKEN" not in environment
    assert "HTTPS_PROXY" not in environment
    assert environment["SSL_CERT_FILE"].endswith("not-trusted.pem")


def test_runner_rejects_direct_binary_symlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binary = tmp_path / "WebJam"
    binary.write_bytes(b"frozen")
    binary.chmod(0o755)
    binary_link = tmp_path / "WebJam-link"
    try:
        binary_link.symlink_to(binary)
    except OSError:
        pytest.skip("This platform cannot create a test symlink.")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_frozen_component_catalog_smoke.py",
            "--binary",
            str(binary_link),
            "--expected-version",
            "0.22.1",
            "--expected-sequence",
            "2",
            "--expected-target",
            "macos-arm64",
            "--expected-jamulus-version",
            "3.12.3",
            "--expected-catalog-envelope-sha256",
            "a" * 64,
            "--expected-catalog-payload-sha256",
            "b" * 64,
            "--expected-signer-fingerprint-sha256",
            "c" * 64,
        ],
    )

    with pytest.raises(SystemExit, match="missing or not executable"):
        smoke_runner.main()
