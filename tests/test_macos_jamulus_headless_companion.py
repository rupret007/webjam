"""Release contracts for the true-HEADLESS macOS Jamulus companion."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MACOS = ROOT / "packaging" / "macos"
BUILD_PATH = MACOS / "build-jamulus-headless-client.sh"
VERIFY_PATH = MACOS / "verify-jamulus-headless-client.sh"
PATCH_PATH = MACOS / "jamulus-headless-r3_12_2.patch"
OFFER_PATH = MACOS / "JamulusHeadlessClient-SOURCE-OFFER.txt"
INSTRUCTIONS_PATH = MACOS / "JamulusHeadlessClient-BUILD-INSTRUCTIONS.txt"
QT_NOTICE_PATH = MACOS / "JamulusHeadlessClient-QT-NOTICE.txt"
LOCK_PATH = MACOS / "aqtinstall-3.3.0-lock.txt"
TRUST_PATH = MACOS / "release-trust.sh"
CI_PATH = ROOT / ".github" / "workflows" / "ci.yml"

BUILD = BUILD_PATH.read_text(encoding="utf-8")
VERIFY = VERIFY_PATH.read_text(encoding="utf-8")
PATCH = PATCH_PATH.read_text(encoding="utf-8")
OFFER = OFFER_PATH.read_text(encoding="utf-8")
INSTRUCTIONS = INSTRUCTIONS_PATH.read_text(encoding="utf-8")
QT_NOTICE = QT_NOTICE_PATH.read_text(encoding="utf-8")
LOCK = LOCK_PATH.read_text(encoding="utf-8")
TRUST = TRUST_PATH.read_text(encoding="utf-8")
CI = CI_PATH.read_text(encoding="utf-8")

COMMIT = "ffca974ed4e47b8f4621f3b583c00db2f87974fa"


def test_build_inputs_and_tools_are_exactly_pinned() -> None:
    for source in (BUILD, VERIFY):
        assert 'VERSION="3.12.2"' in source or 'EXPECTED_VERSION="3.12.2"' in source
        assert COMMIT in source
        assert 'QT_VERSION="6.10.2"' in source
    assert 'AQTINSTALL_VERSION="3.3.0"' in BUILD
    assert 'EXPECTED_AQT_VERSION="3.3.0"' in VERIFY
    assert "aqtinstall==3.3.0 \\\n" in LOCK
    requirement_lines = [
        line
        for line in LOCK.splitlines()
        if line and not line.startswith(("#", " "))
    ]
    assert len(requirement_lines) == 24
    assert LOCK.count("--hash=sha256:") >= len(requirement_lines)
    for line in requirement_lines:
        assert "==" in line
        assert line.endswith(" \\")
        assert not any(operator in line for operator in (">=", "<=", "~=", "!="))


def test_reviewed_patch_is_minimal_and_headless_only() -> None:
    changed = re.findall(r"(?m)^diff --git a/(\S+) b/(\S+)$", PATCH)
    assert changed == [
        ("src/main.cpp", "src/main.cpp"),
        (
            "src/sound/coreaudio-mac/sound.h",
            "src/sound/coreaudio-mac/sound.h",
        ),
    ]
    assert "defined( Q_OS_MACOS ) && !defined( HEADLESS )" in PATCH
    assert "#ifndef HEADLESS\n+#    include <QMessageBox>\n+#endif" in PATCH
    assert "qt_set_sequence_auto_mnemonic" in PATCH
    assert "SERVER_ONLY" not in PATCH


def test_build_is_client_capable_and_never_server_only() -> None:
    assert '"CONFIG+=headless"' in BUILD
    assert '"CONFIG-=serveronly"' in BUILD
    assert "CONFIG+=serveronly" not in BUILD
    assert '"TARGET=$EXECUTABLE_NAME"' in BUILD
    assert 'EXECUTABLE_NAME="JamulusHeadlessClient"' in BUILD
    assert '"CClientRpc capability is missing"' in VERIFY
    assert "__ZN7CClient24OnControllerInFaderLevelEii" in VERIFY
    assert "jamulusclient/setFaderLevel" in VERIFY
    assert 'response["result"] = "ok";' in BUILD
    assert "make -C \"$build_dir\" -j 1" in BUILD


def test_build_stages_only_non_gui_qt_frameworks() -> None:
    allow_list = ("QtConcurrent", "QtCore", "QtNetwork", "QtXml")
    assert "macdeployqt" not in BUILD
    for framework in allow_list:
        assert framework in BUILD
        assert f"{framework}.framework" in VERIFY
    for forbidden in ("QtGui", "QtWidgets", "QtMultimedia"):
        assert forbidden not in BUILD
        assert forbidden in VERIFY
    assert 'install_name_tool -delete_rpath "$qt_dir/lib"' in BUILD
    assert "build-machine path leaked into LC_RPATH" in VERIFY
    assert "HEADLESS companion must not ship Qt plugins" in VERIFY


def test_bundle_identity_signature_checksum_and_source_material_fail_closed() -> None:
    assert "JamulusHeadlessClient.app" in BUILD
    assert "JamulusHeadlessClient.sha256" in BUILD
    assert 'APP_NAME="JamulusHeadlessClient.app"' in VERIFY
    assert 'manifest_name="$APP_NAME/Contents/MacOS/$EXECUTABLE_NAME"' in VERIFY
    assert "codesign --verify --deep --strict" in BUILD
    assert "codesign --verify --deep --strict --verbose=2" in VERIFY
    assert "CFBundleShortVersionString" in BUILD
    assert "CFBundleVersion" in BUILD
    assert "main executable checksum mismatch" in VERIFY
    assert 'EXPECTED_DEPLOYMENT_TARGET="13.0"' in VERIFY
    assert "main executable minimum macOS version" in VERIFY
    assert "main executable macOS SDK version is missing or malformed" in VERIFY
    assert "provenance Apple clang version is malformed" in VERIFY
    assert "provenance macOS SDK version is malformed" in VERIFY
    assert "provenance macOS SDK version does not match the executable" in VERIFY
    assert "packaged source patch differs from the reviewed patch" in VERIFY
    assert "packaged GPL text differs from the reviewed license" in VERIFY
    assert "App Sandbox is forbidden" in VERIFY


def test_complete_patched_corresponding_source_accompanies_the_binary() -> None:
    assert "GNU General Public License" in OFFER
    assert "https://github.com/jamulussoftware/jamulus.git" in OFFER
    assert COMMIT in OFFER
    assert "JamulusHeadlessClient-CORRESPONDING-SOURCE.tar.gz" in OFFER
    assert "included source archive" in OFFER
    assert "git -C \"$source_dir\" archive" in BUILD
    assert "--prefix=JamulusHeadlessClient-source/" in BUILD
    assert "source_tree=" in BUILD
    assert "corresponding_source_sha256=" in BUILD
    assert "corresponding-source archive checksum mismatch" in VERIFY
    assert "corresponding-source archive is unexpectedly incomplete" in VERIFY
    assert "corresponding-source archive contains a traversal path" in VERIFY
    assert 'main_source="$(tar -xOzf' in VERIFY
    assert 'sound_header="$(tar -xOzf' in VERIFY
    assert "webjam-packaging/build-jamulus-headless-client.sh" in VERIFY
    assert "webjam-packaging/verify-jamulus-headless-client.sh" in VERIFY
    assert "corresponding source contains an unreviewed" in VERIFY
    assert "Jamulus.entitlements" in VERIFY
    assert "jamulus-headless-r3_12_2.patch" in OFFER
    assert "Qt 6.10.2" in OFFER
    assert "aqtinstall 3.3.0" in OFFER
    assert "CONFIG+=headless, CONFIG-=serveronly" in OFFER
    assert "not a claim that binaries" in INSTRUCTIONS


def test_qt_lgpl_notice_and_exact_source_accompany_the_frameworks() -> None:
    qt_source = "qtbase-everywhere-src-6.10.2.tar.xz"
    qt_sha = "aeb78d29291a2b5fd53cb55950f8f5065b4978c25fb1d77f627d695ab9adf21e"
    assert "GNU Lesser General Public License version 3" in QT_NOTICE
    assert qt_source in QT_NOTICE
    assert qt_sha in QT_NOTICE
    assert "JamulusHeadlessClient-QT-NOTICE.txt" in BUILD
    assert qt_source in BUILD
    assert qt_sha in BUILD
    assert "LICENSES/LGPL-3.0-only.txt" in BUILD
    assert "packaged Qt source archive checksum mismatch" in VERIFY
    assert "packaged Qt source archive contains a traversal path" in VERIFY
    assert "packaged Qt notice differs from the reviewed notice" in VERIFY


def test_candidate_refreshes_headless_checksum_at_final_signing_boundary() -> None:
    refresh = (
        "> dist/WebJam.app/Contents/Resources/"
        "JamulusHeadlessClient.sha256"
    )
    outer_sign = "codesign --force --sign - dist/WebJam.app"
    assert refresh in CI
    assert CI.index(refresh) < CI.index(outer_sign)
    assert CI.count("verify-jamulus-headless-client.sh") >= 7


def test_both_macos_ci_architectures_build_and_verify_the_companion() -> None:
    assert "Build verified Jamulus HEADLESS Reference Track client (macOS)" in CI
    assert "packaging/macos/aqtinstall-3.3.0-lock.txt" in CI
    assert "--no-deps" in CI
    assert "--require-hashes" in CI
    assert "--only-binary=:all:" in CI
    assert '"$tool_venv/bin/python" -m pip check' in CI
    assert "build-jamulus-headless-client.sh" in CI
    assert "macos-arm64" in CI and "macos-x64" in CI
    assert "expected_machine=arm64" in CI
    assert "expected_machine=x86_64" in CI
    assert (
        "$RUNNER_TEMP/jamulus-headless-${{ matrix.target }}"
        "/JamulusHeadlessClient.app"
    ) in CI
    assert CI.count("verify-jamulus-headless-client.sh") >= 6
    assert "Contents/Resources/JamulusHeadlessClient.sha256" in CI


def test_protected_release_trust_recognizes_and_rehashes_third_nested_app() -> None:
    assert 'JAMULUS_HEADLESS_APP=""' in TRUST
    assert 'JAMULUS_HEADLESS_EXECUTABLE=""' in TRUST
    allow_case = '"$JAMULUS_APP"|"$JAMULUS_SERVER_APP"|"$JAMULUS_HEADLESS_APP")'
    assert allow_case in TRUST
    assert 'sign_target "$target" "$JAMULUS_ENTITLEMENTS"' in TRUST
    assert "JamulusHeadlessClient.app/Contents/MacOS/JamulusHeadlessClient" in TRUST
    assert 'verify_headless_manifest "$app"' in TRUST
    assert TRUST.index('> "$headless_manifest"') < TRUST.index(
        'sign_target "$app" "$WEBJAM_ENTITLEMENTS"'
    )


def test_shell_helpers_are_executable_and_parse() -> None:
    for path in (BUILD_PATH, VERIFY_PATH):
        assert os.access(path, os.X_OK)
    result = subprocess.run(
        ["bash", "-n", str(BUILD_PATH), str(VERIFY_PATH), str(TRUST_PATH)],
        capture_output=True,
        check=False,
        text=True,
    )
    assert result.returncode == 0, result.stderr
