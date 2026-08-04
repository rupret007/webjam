"""Release contract for the reviewed Intel macOS cryptography build."""

from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "packaging" / "macos" / "install-macos-x64-dependencies.sh"
SCRIPT = SCRIPT_PATH.read_text(encoding="utf-8")
VERIFY_WHEEL_PATH = (
    ROOT / "packaging" / "macos" / "verify-cryptography-x64-wheel.py"
)
VERIFY_WHEEL = VERIFY_WHEEL_PATH.read_text(encoding="utf-8")
VERIFY_RUNTIME_PATH = (
    ROOT / "packaging" / "macos" / "verify-cryptography-x64-runtime.sh"
)
VERIFY_RUNTIME = VERIFY_RUNTIME_PATH.read_text(encoding="utf-8")
FILTER_LOCK_PATH = (
    ROOT / "packaging" / "macos" / "prepare-macos-x64-runtime-lock.py"
)
FILTER_LOCK = FILTER_LOCK_PATH.read_text(encoding="utf-8")
PROVENANCE = (
    ROOT / "packaging" / "macos" / "CRYPTOGRAPHY-X64-BUILD-PROVENANCE.txt"
).read_text(encoding="utf-8")
BUILD_INPUT = (
    ROOT / "requirements-lock" / "macos-x64-cryptography-build.in"
).read_text(encoding="utf-8")
BUILD_LOCK = (
    ROOT / "requirements-lock" / "macos-x64-cryptography-build.txt"
).read_text(encoding="utf-8")
TARGET_LOCK = (ROOT / "requirements-lock" / "macos-x64.txt").read_text(
    encoding="utf-8"
)


def _pins(contents: str) -> dict[str, str]:
    return dict(
        re.findall(
            r"(?m)^([A-Za-z0-9_.-]+)==([A-Za-z0-9_.+!-]+)(?: \\)?$",
            contents,
        )
    )


def _hash_block(contents: str, package: str, version: str) -> str:
    header = f"{package}=={version} \\"
    start = contents.find(header)
    assert start >= 0, package
    body_start = contents.find("\n", start) + 1
    next_pin = re.search(r"(?m)^[A-Za-z0-9_.-]+==", contents[body_start:])
    if next_pin is None:
        return contents[body_start:]
    return contents[body_start : body_start + next_pin.start()]


def test_reviewed_toolchain_and_source_identities_are_exact() -> None:
    assert SCRIPT_PATH.stat().st_mode & stat.S_IXUSR
    assert VERIFY_RUNTIME_PATH.stat().st_mode & stat.S_IXUSR
    expected_constants = {
        "CRYPTOGRAPHY_VERSION": "50.0.0",
        "CRYPTOGRAPHY_SDIST_SHA256": (
            "eeac2acb5a20ed25e0ad6d1df9891a520b78b404266b6d11778f25d5d691a6c9"
        ),
        "OPENSSL_VERSION": "3.5.7",
        "OPENSSL_ARCHIVE_SHA256": (
            "a8c0d28a529ca480f9f36cf5792e2cd21984552a3c8e4aa11a24aa31aeac98e8"
        ),
        "RUST_VERSION": "1.88.0",
        "RUST_TOOLCHAIN": "1.88.0-x86_64-apple-darwin",
        "RUST_MANIFEST_SHA256": (
            "431b7c5c0b9a511d8e31d29b378bbc74124e8521f14beb92d3a5a5f7e7e55449"
        ),
        "PYTHON_VERSION": "3.11.9",
        "DEPLOYMENT_TARGET": "13.0",
    }
    for name, value in expected_constants.items():
        assert f'readonly {name}="{value}"' in SCRIPT

    assert "files.pythonhosted.org" in SCRIPT
    assert "cryptography-50.0.0.tar.gz" in SCRIPT
    assert "github.com/openssl/openssl/releases/download/" in SCRIPT
    assert "channel-rust-$RUST_VERSION.toml" in SCRIPT
    assert 'EXPECTED_RUSTC="rustc 1.88.0 (6b00bc388 2025-06-23)"' in SCRIPT
    assert 'EXPECTED_CARGO="cargo 1.88.0 (873a06493 2025-05-10)"' in SCRIPT


def test_build_tool_lock_is_exact_hash_locked_and_binary_installed() -> None:
    expected = {
        "cffi": "2.1.0",
        "maturin": "1.14.1",
        "pycparser": "3.0",
        "setuptools": "81.0.0",
    }
    assert _pins(BUILD_INPUT) == expected
    assert _pins(BUILD_LOCK) == expected
    for package, version in expected.items():
        hashes = re.findall(
            r"--hash=sha256:([0-9a-f]{64})",
            _hash_block(BUILD_LOCK, package, version),
        )
        assert hashes, package
        assert len(hashes) == len(set(hashes)), package

    assert "--require-hashes --only-binary=:all:" in SCRIPT
    assert 'build_venv="$work_dir/build-venv"' in SCRIPT
    assert '"$python_bin" -m venv "$build_venv"' in SCRIPT
    assert 'build_python="$build_venv/bin/python"' in SCRIPT
    assert '"maturin": "1.14.1"' in SCRIPT
    assert 'find_spec("maturin") is not None' in SCRIPT


def test_cryptography_is_the_only_source_distribution_in_runtime_lock() -> None:
    assert _pins(TARGET_LOCK)["cryptography"] == "50.0.0"
    cryptography = _hash_block(TARGET_LOCK, "cryptography", "50.0.0")
    assert (
        "--hash=sha256:"
        "eeac2acb5a20ed25e0ad6d1df9891a520b78b404266b6d11778f25d5d691a6c9"
        in cryptography
    )
    assert "pip wheel --no-deps --no-index --no-build-isolation" in SCRIPT
    assert '"$cryptography_archive"' in SCRIPT
    assert '"$python_bin" "$FILTER_LOCK" "$TARGET_LOCK" "$runtime_lock"' in SCRIPT
    assert "source_distributions_removed" in FILTER_LOCK
    assert "source lock must contain cryptography exactly once" in FILTER_LOCK
    assert "source lock lacks the reviewed cryptography sdist hash" in FILTER_LOCK
    assert "filtered runtime lock still contains cryptography" in FILTER_LOCK
    assert "--require-hashes --only-binary=:all: --no-cache-dir" in SCRIPT
    assert '--find-links "$wheelhouse" --no-deps --require-hashes' in SCRIPT
    assert '"$FILTER_LOCK" "$TARGET_LOCK" "$install_lock"' in SCRIPT
    assert '--wheel-sha256 "$wheel_sha"' in SCRIPT
    assert '--only-binary=:all: -r "$install_lock"' in SCRIPT
    assert "runtime wheelhouse contains a non-wheel artifact" in SCRIPT
    assert 'pip install --force-reinstall --no-index --no-deps "$wheel"' not in SCRIPT


def test_runtime_lock_filter_removes_or_hash_binds_only_cryptography(
    tmp_path: Path,
) -> None:
    filtered = tmp_path / "filtered.txt"
    completed = subprocess.run(
        [
            sys.executable,
            str(FILTER_LOCK_PATH),
            str(ROOT / "requirements-lock" / "macos-x64.txt"),
            str(filtered),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    evidence = json.loads(completed.stdout)
    assert evidence == {
        "cryptography_version": "50.0.0",
        "cryptography_wheel_bound": False,
        "remaining_distributions": len(_pins(TARGET_LOCK)) - 1,
        "source_distributions_removed": 1,
    }
    assert stat.S_IMODE(filtered.stat().st_mode) == 0o600
    filtered_text = filtered.read_text(encoding="utf-8")
    assert "cryptography==" not in filtered_text
    expected_pins = _pins(TARGET_LOCK)
    expected_pins.pop("cryptography")
    assert _pins(filtered_text) == expected_pins
    crypto_start = TARGET_LOCK.index("cryptography==50.0.0 \\")
    crypto_body_start = TARGET_LOCK.index("\n", crypto_start) + 1
    next_pin = re.search(
        r"(?m)^[A-Za-z0-9_.-]+==",
        TARGET_LOCK[crypto_body_start:],
    )
    assert next_pin is not None
    crypto_end = crypto_body_start + next_pin.start()
    assert filtered_text == TARGET_LOCK[:crypto_start] + TARGET_LOCK[crypto_end:]

    rebound = tmp_path / "rebound.txt"
    wheel_sha256 = "a" * 64
    subprocess.run(
        [
            sys.executable,
            str(FILTER_LOCK_PATH),
            str(ROOT / "requirements-lock" / "macos-x64.txt"),
            str(rebound),
            "--wheel-sha256",
            wheel_sha256,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    rebound_text = rebound.read_text(encoding="utf-8")
    expected_rebound = (
        filtered_text
        + "cryptography==50.0.0 \\\n"
        + f"    --hash=sha256:{wheel_sha256}\n"
        + "    # via verified Intel macOS source build\n"
    )
    assert rebound_text == expected_rebound
    rebound_block = _hash_block(rebound_text, "cryptography", "50.0.0")
    assert re.findall(r"--hash=sha256:([0-9a-f]{64})", rebound_block) == [
        wheel_sha256
    ]


def test_archives_are_hash_checked_safely_before_locked_offline_build() -> None:
    assert "curl --proto '=https' --tlsv1.2" in SCRIPT
    assert "shasum -a 256 -c -" in SCRIPT
    assert SCRIPT.count("archive contains an unsafe path") == 2
    assert SCRIPT.count('tar -xzf "$') == 2
    assert "Cargo.toml" in SCRIPT
    assert "Cargo.lock" in SCRIPT

    fetch = SCRIPT.index("cargo fetch --locked --manifest-path")
    offline = SCRIPT.index("export CARGO_NET_OFFLINE=true")
    openssl_build = SCRIPT.index("./Configure darwin64-x86_64-cc")
    runtime_download = SCRIPT.index('"$python_bin" -m pip download')
    runtime_install = SCRIPT.index('"$python_bin" -m pip install --force-reinstall')
    assert fetch < offline < openssl_build < runtime_download < runtime_install
    assert "--no-build-isolation" in SCRIPT
    assert "--no-index" in SCRIPT


def test_static_openssl_and_native_x86_64_output_fail_closed() -> None:
    assert '[[ "$(uname -s)" == Darwin ]]' in SCRIPT
    assert '[[ "$(uname -m)" == x86_64 ]]' in SCRIPT
    assert "./Configure darwin64-x86_64-cc" in SCRIPT
    for option in (
        "no-zlib",
        "no-shared",
        "no-module",
        "no-comp",
        "no-apps",
        "no-docs",
        "no-tests",
    ):
        assert option in SCRIPT
    assert "export OPENSSL_STATIC=1" in SCRIPT
    assert "OPENSSL_VERSION_STR" in SCRIPT
    assert "private OpenSSL prefix unexpectedly contains a dynamic library" in SCRIPT
    assert "libcrypto.a libssl.a" in SCRIPT
    assert 'lipo "$library" -archs' in SCRIPT
    assert 'lipo "$extension" -archs' in VERIFY_RUNTIME
    assert "x86_64 Mach-O shared library" in VERIFY_RUNTIME
    assert "otool -L \"$extension\"" in VERIFY_RUNTIME
    assert "lib(crypto|ssl)" in VERIFY_RUNTIME
    assert "otool -l \"$extension\"" in VERIFY_RUNTIME
    assert "LC_RPATH" in VERIFY_RUNTIME
    assert "^/(Users|private|tmp|opt|usr/local)/" in VERIFY_RUNTIME
    assert "OpenSSL 3.5.7 9 Jun 2026" in VERIFY_RUNTIME
    # With pipefail, grep -q can close a native-tool pipeline early and turn a
    # valid match into the producer's SIGPIPE status (141). Consume all output.
    assert re.search(r"grep\s+[^\n]*q[^\n]*", VERIFY_RUNTIME) is None

    executable_lines = "\n".join(
        line
        for contents in (SCRIPT, VERIFY_RUNTIME)
        for line in contents.splitlines()
        if not line.lstrip().startswith("#")
    )
    assert re.search(r"(?m)^\s*(?:sudo|brew)(?:\s|$)", executable_lines) is None
    assert "/opt/homebrew" not in executable_lines
    assert "/usr/local/opt" not in executable_lines


def test_runtime_verifier_fails_when_otool_cannot_inspect_linkage(
    tmp_path: Path,
) -> None:
    bundle = tmp_path / "WebJam.app"
    extension = bundle / "cryptography" / "hazmat" / "bindings" / "_rust.abi3.so"
    extension.parent.mkdir(parents=True)
    extension.write_bytes(b"fixture")
    tools = tmp_path / "tools"
    tools.mkdir()

    fixtures = {
        "lipo": "#!/bin/sh\nprintf '%s\\n' x86_64\n",
        "file": (
            "#!/bin/sh\nprintf '%s\\n' "
            "'fixture: Mach-O 64-bit dynamically linked shared library x86_64'\n"
        ),
        "strings": "#!/bin/sh\nprintf '%s\\n' 'OpenSSL 3.5.7 9 Jun 2026'\n",
        "otool": "#!/bin/sh\nexit 42\n",
    }
    for name, contents in fixtures.items():
        path = tools / name
        path.write_text(contents, encoding="utf-8")
        path.chmod(0o755)

    completed = subprocess.run(
        [str(VERIFY_RUNTIME_PATH), str(bundle)],
        capture_output=True,
        check=False,
        env={**os.environ, "PATH": f"{tools}:{os.environ['PATH']}"},
        text=True,
    )
    assert completed.returncode != 0
    assert "otool linkage inspection failed" in completed.stderr


def test_runtime_verifier_fails_when_otool_cannot_inspect_load_commands(
    tmp_path: Path,
) -> None:
    bundle = tmp_path / "WebJam.app"
    extension = bundle / "cryptography" / "hazmat" / "bindings" / "_rust.abi3.so"
    extension.parent.mkdir(parents=True)
    extension.write_bytes(b"fixture")
    tools = tmp_path / "tools"
    tools.mkdir()

    fixtures = {
        "lipo": "#!/bin/sh\nprintf '%s\\n' x86_64\n",
        "file": (
            "#!/bin/sh\nprintf '%s\\n' "
            "'fixture: Mach-O 64-bit dynamically linked shared library x86_64'\n"
        ),
        "strings": "#!/bin/sh\nprintf '%s\\n' 'OpenSSL 3.5.7 9 Jun 2026'\n",
        "otool": (
            "#!/bin/sh\n"
            "if [ \"$1\" = -L ]; then\n"
            "  printf '%s\\n' \"$2:\" '/usr/lib/libSystem.B.dylib'\n"
            "  exit 0\n"
            "fi\n"
            "exit 42\n"
        ),
    }
    for name, contents in fixtures.items():
        path = tools / name
        path.write_text(contents, encoding="utf-8")
        path.chmod(0o755)

    completed = subprocess.run(
        [str(VERIFY_RUNTIME_PATH), str(bundle)],
        capture_output=True,
        check=False,
        env={**os.environ, "PATH": f"{tools}:{os.environ['PATH']}"},
        text=True,
    )
    assert completed.returncode != 0
    assert "otool load-command inspection failed" in completed.stderr


def test_built_wheel_identity_archive_and_record_are_verified() -> None:
    for expected in (
        "cryptography-50\\.0\\.0-",
        "x86_64",
        '"arm64" in wheel.name',
        '"universal2" in wheel.name',
        "wheel contains an unsafe member path",
        "wheel contains a symbolic link",
        "wheel contains duplicate member names",
        "wheel member CRC validation failed",
        "cryptography-50.0.0.dist-info",
        "licenses/LICENSE.APACHE",
        "licenses/LICENSE.BSD",
        "wheel METADATA has the wrong version",
        "Root-Is-Purelib",
        "non-Intel compatibility tag",
        "wheel must contain exactly one Rust extension",
        "wheel unexpectedly contains a dynamic library",
        "wheel RECORD does not exactly cover its files",
        "wheel RECORD digest validation failed",
    ):
        assert expected in VERIFY_WHEEL
    assert "hashlib.sha256(wheel.read_bytes()).hexdigest()" in VERIFY_WHEEL
    assert "hashlib.sha256(extension_data).hexdigest()" in VERIFY_WHEEL


def test_provenance_matches_the_executable_source_build_contract() -> None:
    for expected in (
        "cryptography 50.0.0",
        "native macos-15-intel GitHub runner",
        "maturin 1.14.1",
        "Rust 1.88.0",
        "OpenSSL 3.5.7 LTS",
        "Python is exactly 3.11.9",
        "cargo fetch --locked",
        "private, module-free, static",
        "x86_64 prefix",
        "complete application environment is installed offline and binary-only",
        "static OpenSSL",
        "linkage, absence of build-machine runtime paths",
        "install-macos-x64-dependencies.sh",
    ):
        assert expected in PROVENANCE
    for digest in (
        "eeac2acb5a20ed25e0ad6d1df9891a520b78b404266b6d11778f25d5d691a6c9",
        "431b7c5c0b9a511d8e31d29b378bbc74124e8521f14beb92d3a5a5f7e7e55449",
        "a8c0d28a529ca480f9f36cf5792e2cd21984552a3c8e4aa11a24aa31aeac98e8",
    ):
        assert digest in PROVENANCE
