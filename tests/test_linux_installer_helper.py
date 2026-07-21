"""Behavioral checks for the packaged Linux dependency installer."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "packaging" / "linux" / "install-jamulus.sh"
PACKAGE_NAME = "jamulus_3.12.2_ubuntu_amd64.deb"
EXPECTED_SHA256 = "029f8858f21a5fb36da5144046473575caa2a26f2c7d8db162953b89d8c8ccc9"


def _write_executable(path: Path, contents: str) -> None:
    path.write_text(contents, encoding="utf-8")
    path.chmod(0o755)


def _rehearsal(
    tmp_path: Path, *, checksum_exit: int
) -> tuple[subprocess.CompletedProcess[str], Path, Path, Path]:
    bundle = tmp_path / "WebJam"
    package = bundle / "Jamulus" / PACKAGE_NAME
    package.parent.mkdir(parents=True)
    package.write_bytes(b"inert test package")
    helper = bundle / HELPER.name
    shutil.copy2(HELPER, helper)

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    checksum_capture = tmp_path / "checksum-input"
    sudo_capture = tmp_path / "sudo-argv"
    _write_executable(fake_bin / "apt", "#!/bin/sh\nexit 0\n")
    _write_executable(
        fake_bin / "sha256sum",
        '#!/bin/sh\ncat > "$CHECKSUM_CAPTURE"\nexit "$CHECKSUM_EXIT"\n',
    )
    _write_executable(
        fake_bin / "sudo",
        '#!/bin/sh\nprintf \'%s\\n\' "$@" > "$SUDO_CAPTURE"\n',
    )
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:/usr/bin:/bin",
        "CHECKSUM_CAPTURE": str(checksum_capture),
        "CHECKSUM_EXIT": str(checksum_exit),
        "SUDO_CAPTURE": str(sudo_capture),
    }
    result = subprocess.run(
        [str(helper)],
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )
    return result, package, checksum_capture, sudo_capture


def test_helper_pins_the_audited_jamulus_digest() -> None:
    source = HELPER.read_text(encoding="utf-8")
    assert f"EXPECTED_SHA256={EXPECTED_SHA256}" in source
    assert hashlib.sha256(b"inert test package").hexdigest() != EXPECTED_SHA256


def test_helper_checks_exact_package_before_invoking_sudo(tmp_path: Path) -> None:
    result, package, checksum_capture, sudo_capture = _rehearsal(
        tmp_path, checksum_exit=0
    )

    assert result.returncode == 0, result.stderr
    assert checksum_capture.read_text(encoding="utf-8") == (
        f"{EXPECTED_SHA256}  {package}\n"
    )
    assert sudo_capture.read_text(encoding="utf-8").splitlines() == [
        "apt",
        "install",
        str(package),
    ]


def test_helper_rejects_checksum_failure_without_privilege_escalation(
    tmp_path: Path,
) -> None:
    result, package, checksum_capture, sudo_capture = _rehearsal(
        tmp_path, checksum_exit=1
    )

    assert result.returncode != 0
    assert "failed its SHA-256 check" in result.stderr
    assert checksum_capture.read_text(encoding="utf-8") == (
        f"{EXPECTED_SHA256}  {package}\n"
    )
    assert not sudo_capture.exists()
