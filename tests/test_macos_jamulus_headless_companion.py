"""Release contracts for the true-HEADLESS macOS Jamulus companion."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MACOS = ROOT / "packaging" / "macos"
BUILD_PATH = MACOS / "build-jamulus-headless-client.sh"
VERIFY_PATH = MACOS / "verify-jamulus-headless-client.sh"
PATCH_3122_PATH = MACOS / "jamulus-headless-r3_12_2.patch"
PATCH_3123_PATH = MACOS / "jamulus-headless-r3_12_3.patch"
OFFER_PATH = MACOS / "JamulusHeadlessClient-SOURCE-OFFER.txt"
OFFER_3123_PATH = MACOS / "JamulusHeadlessClient-r3_12_3-SOURCE-OFFER.txt"
INSTRUCTIONS_PATH = MACOS / "JamulusHeadlessClient-BUILD-INSTRUCTIONS.txt"
INSTRUCTIONS_3123_PATH = (
    MACOS / "JamulusHeadlessClient-r3_12_3-BUILD-INSTRUCTIONS.txt"
)
QT_NOTICE_PATH = MACOS / "JamulusHeadlessClient-QT-NOTICE.txt"
LOCK_PATH = MACOS / "aqtinstall-3.3.0-lock.txt"
TRUST_PATH = MACOS / "release-trust.sh"
EVIDENCE_PATH = MACOS / "create-jamulus-headless-component-evidence.py"
EVIDENCE_SCHEMA_PATH = MACOS / "jamulus-headless-component-evidence.schema.json"
CI_PATH = ROOT / ".github" / "workflows" / "ci.yml"

BUILD = BUILD_PATH.read_text(encoding="utf-8")
VERIFY = VERIFY_PATH.read_text(encoding="utf-8")
PATCH_3122 = PATCH_3122_PATH.read_text(encoding="utf-8")
PATCH_3123 = PATCH_3123_PATH.read_text(encoding="utf-8")
OFFER = OFFER_PATH.read_text(encoding="utf-8")
OFFER_3123 = OFFER_3123_PATH.read_text(encoding="utf-8")
INSTRUCTIONS = INSTRUCTIONS_PATH.read_text(encoding="utf-8")
INSTRUCTIONS_3123 = INSTRUCTIONS_3123_PATH.read_text(encoding="utf-8")
QT_NOTICE = QT_NOTICE_PATH.read_text(encoding="utf-8")
LOCK = LOCK_PATH.read_text(encoding="utf-8")
TRUST = TRUST_PATH.read_text(encoding="utf-8")
CI = CI_PATH.read_text(encoding="utf-8")

COMMIT_3122 = "ffca974ed4e47b8f4621f3b583c00db2f87974fa"
COMMIT_3123 = "74dc422116983a2173eb917cb4d6a403886b31e5"


def test_build_inputs_and_tools_are_exactly_pinned() -> None:
    for source in (BUILD, VERIFY):
        assert 'VERSION="3.12.2"' in source or 'EXPECTED_VERSION="3.12.2"' in source
        assert 'VERSION="3.12.3"' in source or 'EXPECTED_VERSION="3.12.3"' in source
        assert COMMIT_3122 in source
        assert COMMIT_3123 in source
        assert 'QT_VERSION="6.10.2"' in source
        assert "r3_12_2" in source
        assert "r3_12_3" in source
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
    for patch in (PATCH_3122, PATCH_3123):
        changed = re.findall(r"(?m)^diff --git a/(\S+) b/(\S+)$", patch)
        assert changed == [
            ("src/main.cpp", "src/main.cpp"),
            (
                "src/sound/coreaudio-mac/sound.h",
                "src/sound/coreaudio-mac/sound.h",
            ),
        ]
        assert "defined( Q_OS_MACOS ) && !defined( HEADLESS )" in patch
        assert "#ifndef HEADLESS\n+#    include <QMessageBox>\n+#endif" in patch
        assert "qt_set_sequence_auto_mnemonic" in patch
        assert "SERVER_ONLY" not in patch


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
    assert "packaged license text differs from the reviewed license" in VERIFY
    assert "App Sandbox is forbidden" in VERIFY


def test_complete_patched_corresponding_source_accompanies_the_binary() -> None:
    assert "GNU General Public License" in OFFER
    assert "https://github.com/jamulussoftware/jamulus.git" in OFFER
    assert COMMIT_3122 in OFFER
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


def test_3123_profile_is_evidence_only_with_exact_agpl_source_material() -> None:
    license_path = ROOT / "licenses" / "JAMULUS_COPYING-r3_12_3.txt"
    license_text = license_path.read_text(encoding="utf-8")
    assert "GNU AFFERO GENERAL PUBLIC LICENSE" in license_text
    assert "GNU GENERAL PUBLIC LICENSE" in license_text
    assert "AGPL 3.0 or any later version" in license_text
    assert COMMIT_3123 in OFFER_3123
    assert "jamulus-headless-r3_12_3.patch" in OFFER_3123
    assert "ACTIVATION IS NOT APPROVED" in OFFER_3123
    assert "AGPL section 13" in OFFER_3123
    assert "r3_12_3" in INSTRUCTIONS_3123
    assert "evidence only" in INSTRUCTIONS_3123
    assert "must not be staged into a WebJam desktop" in INSTRUCTIONS_3123
    assert 'LICENSE_NAME="JAMULUS_COPYING-r3_12_3.txt"' in BUILD
    assert '"$VERIFY" "$output_app" "$architecture" "$manifest" "$profile"' in BUILD
    assert 'profile=${4:-r3_12_2}' in VERIFY


def test_3123_headless_workflow_is_manual_quarantined_and_not_a_release_input() -> None:
    assert "build_unapproved_jamulus_3123_headless_evidence:" in CI
    assert "jamulus-3123-headless-evidence:" in CI
    assert "inputs.build_unapproved_jamulus_3123_headless_evidence" in CI
    assert "UNAPPROVED-EVIDENCE.zip" in CI
    assert "UNAPPROVED-EVIDENCE.json" in CI
    assert "--output \"$evidence\"" in CI
    assert 'r3_12_3\n' in CI
    assert 'evidence["activation_approved"] is False' in CI
    assert 'evidence["catalog_signing_automatic"] is False' in CI
    assert "jamulus-headless-r3_12_3-${{ matrix.target }}-unapproved-evidence" in CI
    release_upload = CI.split("Upload non-Windows build artifact", 1)[1].split(
        "# ------------------------------------------------------------------", 1
    )[0]
    assert "JamulusHeadlessClient-r3_12_3" not in release_upload
    assert EVIDENCE_PATH.is_file()
    schema = EVIDENCE_SCHEMA_PATH.read_text(encoding="utf-8")
    assert '"activation_approved": {"const": false}' in schema
    assert '"desktop_release_inventory": {"const": false}' in schema


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
    compile_result = subprocess.run(
        [sys.executable, "-m", "py_compile", str(EVIDENCE_PATH)],
        capture_output=True,
        check=False,
        text=True,
    )
    assert compile_result.returncode == 0, compile_result.stderr
