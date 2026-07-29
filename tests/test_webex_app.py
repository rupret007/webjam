"""Native Webex detection and official-installer boundary tests."""

from __future__ import annotations

import json
from pathlib import Path
import plistlib
import subprocess

import pytest

from services import webex_app
from services.webex_app import (
    WEBEX_DOWNLOAD_PAGE,
    WEBEX_MAC_BUNDLE_ID,
    WEBEX_MAC_TEAM_ID,
    WebexAppError,
    WebexAppInfo,
    WebexAppState,
    bring_webex_forward,
    detect_webex_app,
    open_official_webex_installer,
    webex_installer_url,
)


@pytest.mark.parametrize(
    ("platform_name", "machine", "expected"),
    [
        (
            "darwin",
            "arm64",
            "https://binaries.webex.com/"
            "webex-macos-apple-silicon/Webex.pkg",
        ),
        (
            "darwin",
            "x86_64",
            "https://binaries.webex.com/webex-macos-intel/Webex.pkg",
        ),
        (
            "win32",
            "AMD64",
            "https://binaries.webex.com/"
            "WebexOfclDesktop-Win-64-Gold/Webex.msi",
        ),
        ("linux", "x86_64", WEBEX_DOWNLOAD_PAGE),
        ("darwin", "mips", WEBEX_DOWNLOAD_PAGE),
    ],
)
def test_installer_urls_are_exact_official_https_destinations(
    platform_name, machine, expected
):
    assert (
        webex_installer_url(
            platform_name=platform_name,
            machine=machine,
        )
        == expected
    )


def test_installer_handoff_is_explicit_and_rejects_mutated_origin(monkeypatch):
    opened = []
    assert open_official_webex_installer(
        platform_name="darwin",
        machine="arm64",
        opener=lambda url: opened.append(url) or True,
    )
    assert opened == [
        "https://binaries.webex.com/"
        "webex-macos-apple-silicon/Webex.pkg"
    ]

    monkeypatch.setitem(
        webex_app.WEBEX_INSTALLER_URLS,
        "macos-arm64",
        "https://downloads.example.invalid/Webex.pkg",
    )
    with pytest.raises(WebexAppError, match="not approved"):
        webex_installer_url(platform_name="darwin", machine="arm64")


def test_installer_handoff_wraps_browser_failure_without_silent_fallback():
    def fail(_url):
        raise OSError("browser unavailable")

    with pytest.raises(WebexAppError, match="could not be opened"):
        open_official_webex_installer(
            platform_name="win32",
            machine="amd64",
            opener=fail,
        )


def _mac_app(tmp_path: Path) -> Path:
    app = tmp_path / "Applications" / "Webex.app"
    executable = app / "Contents" / "MacOS" / "Webex"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"test executable")
    executable.chmod(0o755)
    (app / "Contents" / "Info.plist").write_bytes(
        plistlib.dumps(
            {
                "CFBundleIdentifier": WEBEX_MAC_BUNDLE_ID,
                "CFBundleShortVersionString": "45.7.0",
            }
        )
    )
    return app


def _completed(arguments, *, returncode=0, stdout=b"", stderr=b""):
    return subprocess.CompletedProcess(
        arguments,
        returncode,
        stdout=stdout,
        stderr=stderr,
    )


def _verified_mac_runner(calls):
    def run(arguments, *, timeout):
        calls.append((tuple(arguments), timeout))
        if arguments[0] == "/usr/bin/codesign" and "--verify" in arguments:
            return _completed(arguments)
        if arguments[0] == "/usr/bin/codesign" and "-d" in arguments:
            return _completed(
                arguments,
                stderr=(
                    f"Identifier={WEBEX_MAC_BUNDLE_ID}\n"
                    f"TeamIdentifier={WEBEX_MAC_TEAM_ID}\n"
                    "Authority=Developer ID Application: "
                    f"Cisco ({WEBEX_MAC_TEAM_ID})\n"
                ).encode(),
            )
        return _completed(
            arguments,
            stderr=b"accepted\nsource=Notarized Developer ID\n",
        )

    return run


def test_macos_detection_requires_bundle_publisher_team_and_notarization(tmp_path):
    app = _mac_app(tmp_path / "Home With Spaces")
    calls = []
    info = webex_app._detect_macos_webex(
        (app,),
        command_runner=_verified_mac_runner(calls),
    )

    assert info.state is WebexAppState.INSTALLED
    assert info.version == "45.7.0"
    assert info.publisher_verified
    assert info.path == app
    assert [call[0][0] for call in calls] == [
        "/usr/bin/codesign",
        "/usr/bin/codesign",
        "/usr/sbin/spctl",
    ]
    assert all(str(app) == arguments[-1] for arguments, _timeout in calls)

    public = info.to_public_dict()
    assert "path" not in public
    assert str(tmp_path) not in json.dumps(public)


@pytest.mark.parametrize(
    ("details", "assessment", "reason"),
    [
        (
            (
                f"Identifier={WEBEX_MAC_BUNDLE_ID}\n"
                "TeamIdentifier=WRONGTEAM\n"
                "Authority=Developer ID Application: Cisco (WRONGTEAM)\n"
            ),
            "accepted\nsource=Notarized Developer ID\n",
            "publisher-unverified",
        ),
        (
            (
                f"Identifier={WEBEX_MAC_BUNDLE_ID}\n"
                f"TeamIdentifier={WEBEX_MAC_TEAM_ID}\n"
                "Authority=Developer ID Application: "
                f"Cisco ({WEBEX_MAC_TEAM_ID})\n"
            ),
            "accepted\nsource=Developer ID\n",
            "publisher-unverified",
        ),
    ],
)
def test_macos_detection_rejects_wrong_publisher_or_missing_notarization(
    tmp_path, details, assessment, reason
):
    app = _mac_app(tmp_path)

    def runner(arguments, *, timeout):
        del timeout
        if "--verify" in arguments:
            return _completed(arguments)
        if "-d" in arguments:
            return _completed(arguments, stderr=details.encode())
        return _completed(arguments, stderr=assessment.encode())

    info = webex_app._detect_macos_webex((app,), command_runner=runner)
    assert info.state is WebexAppState.INVALID
    assert not info.publisher_verified
    assert info.path is None
    assert info.reason_code == reason
    assert "path" not in info.to_public_dict()
    assert str(tmp_path) not in json.dumps(info.to_public_dict())


def test_missing_invalid_and_unsupported_states_are_truthful_and_path_free(
    tmp_path,
):
    missing = webex_app._detect_macos_webex(
        (tmp_path / "missing.app",),
        command_runner=lambda *_args, **_kwargs: pytest.fail(
            "missing app must not invoke signature tools"
        ),
    )
    assert missing.state is WebexAppState.NOT_INSTALLED
    assert missing.to_public_dict()["reason_code"] == ""

    malformed = tmp_path / "Malformed.app"
    malformed.mkdir()
    invalid = webex_app._detect_macos_webex(
        (malformed,),
        command_runner=lambda *_args, **_kwargs: pytest.fail(
            "invalid metadata must not invoke signature tools"
        ),
    )
    assert invalid.state is WebexAppState.INVALID
    assert invalid.reason_code == "metadata-invalid"

    unsupported = detect_webex_app(platform_name="plan9", home=tmp_path)
    assert unsupported.state is WebexAppState.UNSUPPORTED
    assert unsupported.reason_code == "unsupported-platform"

    for info in (missing, invalid, unsupported):
        diagnostic = info.to_public_dict()
        assert "path" not in diagnostic
        assert str(tmp_path) not in json.dumps(diagnostic)


def test_regular_executable_detection_rejects_symlinks(tmp_path):
    executable = tmp_path / "CiscoCollabHost.exe"
    executable.write_bytes(b"test")
    executable.chmod(0o755)
    linked = tmp_path / "Webex.exe"
    linked.symlink_to(executable)

    assert (
        webex_app._detect_regular_executable((linked,)).state
        is WebexAppState.NOT_INSTALLED
    )
    installed = webex_app._detect_regular_executable((executable,))
    assert installed.state is WebexAppState.INSTALLED
    assert installed.reason_code == "publisher-check-deferred"
    assert "path" not in installed.to_public_dict()


def test_bring_forward_targets_verified_mac_bundle_without_a_shell(tmp_path):
    app = _mac_app(tmp_path / "Home With Spaces")
    calls: list[list[str]] = []
    info = WebexAppInfo(
        state=WebexAppState.INSTALLED,
        version="46.7.0",
        publisher_verified=True,
        path=app,
    )

    assert bring_webex_forward(
        info,
        platform_name="darwin",
        launcher=lambda arguments: calls.append(arguments) or True,
        detector=lambda **_kwargs: info,
    )

    assert calls == [["/usr/bin/open", str(app)]]
    assert all("meet/" not in argument for argument in calls[0])


@pytest.mark.parametrize(
    "info",
    [
        WebexAppInfo(state=WebexAppState.NOT_INSTALLED),
        WebexAppInfo(
            state=WebexAppState.INSTALLED,
            publisher_verified=False,
            path=Path("/Applications/Webex.app"),
        ),
        WebexAppInfo(
            state=WebexAppState.INVALID,
            publisher_verified=True,
            path=Path("/Applications/Webex.app"),
        ),
    ],
)
def test_bring_forward_fails_closed_without_a_verified_detected_mac_app(info):
    calls = []

    assert not bring_webex_forward(
        info,
        platform_name="darwin",
        launcher=lambda arguments: calls.append(arguments) or True,
    )
    assert calls == []


def test_bring_forward_rejects_stale_and_symlinked_targets(tmp_path):
    real = tmp_path / "Webex"
    real.write_bytes(b"binary")
    real.chmod(0o755)
    linked = tmp_path / "Webex link"
    linked.symlink_to(real)

    for path in (linked, tmp_path / "missing"):
        calls = []
        info = WebexAppInfo(
            state=WebexAppState.INSTALLED,
            path=path,
            reason_code="publisher-check-deferred",
        )
        assert not bring_webex_forward(
            info,
            platform_name="linux",
            launcher=lambda arguments: calls.append(arguments) or True,
        )
        assert calls == []


def test_bring_forward_wraps_launcher_failure_without_disclosing_path(tmp_path):
    executable = tmp_path / "Private Folder" / "CiscoCollabHost.exe"
    executable.parent.mkdir()
    executable.write_bytes(b"binary")
    executable.chmod(0o755)
    info = WebexAppInfo(
        state=WebexAppState.INSTALLED,
        publisher_verified=True,
        path=executable,
    )

    with pytest.raises(WebexAppError) as raised:
        bring_webex_forward(
            info,
            platform_name="win32",
            launcher=lambda _arguments: (_ for _ in ()).throw(OSError("no")),
            detector=lambda **_kwargs: info,
        )

    assert str(tmp_path) not in str(raised.value)


def test_unverified_cross_platform_executable_is_never_activated(tmp_path):
    executable = tmp_path / "CiscoCollabHost.exe"
    executable.write_bytes(b"binary")
    executable.chmod(0o755)
    info = WebexAppInfo(
        state=WebexAppState.INSTALLED,
        publisher_verified=False,
        path=executable,
        reason_code="publisher-check-deferred",
    )
    calls: list[list[str]] = []

    assert not bring_webex_forward(
        info,
        platform_name="win32",
        launcher=lambda arguments: calls.append(arguments) or True,
        detector=lambda **_kwargs: info,
    )
    assert calls == []


def test_bring_forward_revalidates_the_exact_target_before_launch(tmp_path):
    app = _mac_app(tmp_path / "Verified Webex")
    original = WebexAppInfo(
        state=WebexAppState.INSTALLED,
        version="46.7.0",
        publisher_verified=True,
        path=app,
    )
    replacement = WebexAppInfo(
        state=WebexAppState.INVALID,
        reason_code="signature-invalid",
        path=app,
    )
    calls: list[list[str]] = []

    assert not bring_webex_forward(
        original,
        platform_name="darwin",
        launcher=lambda arguments: calls.append(arguments) or True,
        detector=lambda **_kwargs: replacement,
    )
    assert calls == []


def test_bring_forward_rejects_a_different_fresh_install_path(tmp_path):
    original_path = _mac_app(tmp_path / "Original")
    other_path = _mac_app(tmp_path / "Replacement")
    original = WebexAppInfo(
        state=WebexAppState.INSTALLED,
        publisher_verified=True,
        path=original_path,
    )
    fresh = WebexAppInfo(
        state=WebexAppState.INSTALLED,
        publisher_verified=True,
        path=other_path,
    )

    assert not bring_webex_forward(
        original,
        platform_name="darwin",
        launcher=lambda _arguments: True,
        detector=lambda **_kwargs: fresh,
    )
