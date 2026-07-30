"""Native Webex detection and official-installer boundary tests."""

from __future__ import annotations

from contextlib import nullcontext
import json
from pathlib import Path
import plistlib
import subprocess

import pytest

from services import webex_app
from services.webex_app import (
    WEBEX_DOWNLOAD_PAGE,
    WEBEX_MAC_BUNDLE_ID,
    WEBEX_MAC_PROCESS_REQUIREMENT,
    WEBEX_MAC_TEAM_ID,
    WebexActivationState,
    WebexActivationResult,
    WebexAppError,
    WebexAppInfo,
    WebexAppState,
    bring_webex_forward,
    detect_webex_app,
    open_official_webex_installer,
    show_webex_app,
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


class _FakeMacRuntime:
    def __init__(
        self,
        *,
        applications=(),
        activate_result=True,
    ):
        self._applications = tuple(applications)
        self.activate_result = activate_result
        self.queries: list[str] = []
        self.activations: list[object] = []
        self.session_entries = 0

    def activation_session(self):
        self.session_entries += 1
        return nullcontext(self)

    def running_applications(self, bundle_identifier):
        self.queries.append(bundle_identifier)
        return self._applications

    def activate(self, application):
        self.activations.append(application)
        return self.activate_result


def _running_application(path: Path | None, *, pid=4321, handle=99):
    return webex_app._MacRunningApplication(
        native_handle=handle,
        process_identifier=pid,
        path=path,
    )


def _verified_process_runner(calls):
    def run(arguments, *, timeout):
        calls.append((list(arguments), timeout))
        return _completed(arguments)

    return run


def test_show_app_activates_one_pid_verified_running_bundle_without_handoff(
    tmp_path,
    monkeypatch,
):
    app = _mac_app(tmp_path / "Home With Spaces")
    running = _running_application(app, pid=7654)
    runtime = _FakeMacRuntime(applications=(running,))
    process_calls = []
    info = WebexAppInfo(
        state=WebexAppState.INSTALLED,
        version="46.7.0",
        publisher_verified=True,
        path=app,
    )
    monkeypatch.setattr(
        webex_app.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail(
            "Show Webex App must not invoke /usr/bin/open"
        ),
    )
    monkeypatch.setattr(
        webex_app.webbrowser,
        "open",
        lambda *_args, **_kwargs: pytest.fail(
            "Show Webex App must not open a browser"
        ),
    )

    result = show_webex_app(
        info,
        platform_name="darwin",
        detector=lambda **_kwargs: info,
        mac_runtime_factory=lambda: runtime,
        command_runner=_verified_process_runner(process_calls),
    )

    assert result.state is WebexActivationState.ACTIVATED_RUNNING
    assert result.succeeded
    assert runtime.queries == [WEBEX_MAC_BUNDLE_ID]
    assert runtime.activations == [running]
    assert runtime.activations[0] is running
    assert process_calls == [
        (
            [
                "/usr/bin/codesign",
                "--verify",
                "--strict",
                "--verbose=2",
                f"-R={WEBEX_MAC_PROCESS_REQUIREMENT}",
                "7654",
            ],
            30.0,
        )
    ]
    assert all(str(app) not in argument for argument in process_calls[0][0])
    assert result.to_public_dict() == {"state": "activated-running"}


def test_show_app_refuses_when_verified_webex_is_not_running(tmp_path):
    app = _mac_app(tmp_path / "Stopped")
    runtime = _FakeMacRuntime(applications=())
    process_calls = []
    info = WebexAppInfo(
        state=WebexAppState.INSTALLED,
        publisher_verified=True,
        path=app,
    )

    result = show_webex_app(
        info,
        platform_name="darwin",
        detector=lambda **_kwargs: info,
        mac_runtime_factory=lambda: runtime,
        command_runner=_verified_process_runner(process_calls),
    )

    assert result.state is WebexActivationState.REFUSED
    assert result.reason_code == "app-not-running"
    assert runtime.activations == []
    assert process_calls == []
    assert not hasattr(runtime, "launch_exact")


def test_show_app_running_activation_failure_is_truthful(tmp_path):
    app = _mac_app(tmp_path / "Running")
    running = _running_application(app)
    runtime = _FakeMacRuntime(
        applications=(running,),
        activate_result=False,
    )
    info = WebexAppInfo(
        state=WebexAppState.INSTALLED,
        publisher_verified=True,
        path=app,
    )

    result = show_webex_app(
        info,
        platform_name="darwin",
        detector=lambda **_kwargs: info,
        mac_runtime_factory=lambda: runtime,
        command_runner=_verified_process_runner([]),
    )

    assert result.state is WebexActivationState.FAILED
    assert result.reason_code == "native-activation-failed"
    assert not result
    assert runtime.activations == [running]


def test_native_activation_unhides_requests_all_windows_and_proves_active():
    runtime = object.__new__(webex_app._MacOSApplicationRuntime)
    calls = []
    runtime._selector = lambda name: name
    runtime._send_void = (
        lambda application, selector: calls.append(
            ("void", application, selector)
        )
    )
    runtime._send_bool_ulong = (
        lambda application, selector, options: calls.append(
            ("bool", application, selector, options)
        )
        or True
    )
    runtime._send_bool = (
        lambda application, selector: calls.append(
            ("active", application, selector)
        )
        or True
    )

    assert runtime._activate(42)
    assert calls == [
        ("void", 42, "unhide"),
        ("bool", 42, "activateWithOptions:", 3),
        ("active", 42, "isActive"),
    ]


def test_typed_activation_result_rejects_unbounded_private_reason_text():
    with pytest.raises(ValueError, match="unsupported Webex activation reason"):
        WebexActivationResult(
            WebexActivationState.FAILED,
            "/Users/private/meet/secret",
        )


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
    result = bring_webex_forward(
        info,
        platform_name="darwin",
    )
    assert result.state is WebexActivationState.REFUSED
    assert result.reason_code == "verified-app-unavailable"


def test_bring_forward_rejects_stale_and_symlinked_targets(tmp_path):
    real = tmp_path / "Webex"
    real.write_bytes(b"binary")
    real.chmod(0o755)
    linked = tmp_path / "Webex link"
    linked.symlink_to(real)

    for path in (linked, tmp_path / "missing"):
        info = WebexAppInfo(
            state=WebexAppState.INSTALLED,
            publisher_verified=True,
            path=path,
            reason_code="publisher-check-deferred",
        )
        result = bring_webex_forward(
            info,
            platform_name="darwin",
        )
        assert result.state is WebexActivationState.REFUSED
        assert result.reason_code == "target-invalid"


def test_show_app_wraps_native_failure_without_disclosing_path(tmp_path):
    app = _mac_app(tmp_path / "Private Folder")
    info = WebexAppInfo(
        state=WebexAppState.INSTALLED,
        publisher_verified=True,
        path=app,
    )

    with pytest.raises(WebexAppError) as raised:
        show_webex_app(
            info,
            platform_name="darwin",
            detector=lambda **_kwargs: info,
            mac_runtime_factory=lambda: (_ for _ in ()).throw(OSError("no")),
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
    result = bring_webex_forward(
        info,
        platform_name="win32",
        detector=lambda **_kwargs: info,
    )
    assert result.state is WebexActivationState.REFUSED
    assert result.reason_code == "verified-app-unavailable"


def test_windows_direct_activation_remains_unavailable_without_authenticode(
    tmp_path,
):
    executable = tmp_path / "CiscoCollabHost.exe"
    executable.write_bytes(b"binary")
    info = WebexAppInfo(
        state=WebexAppState.INSTALLED,
        publisher_verified=True,
        path=executable,
    )

    result = show_webex_app(
        info,
        platform_name="win32",
        detector=lambda **_kwargs: info,
    )

    assert result.state is WebexActivationState.REFUSED
    assert result.reason_code == "native-activation-unavailable"


def test_bring_forward_revalidates_the_exact_target_before_activation(tmp_path):
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
    result = bring_webex_forward(
        original,
        platform_name="darwin",
        detector=lambda **_kwargs: replacement,
    )
    assert result.state is WebexActivationState.REFUSED
    assert result.reason_code == "reverification-refused"


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

    result = bring_webex_forward(
        original,
        platform_name="darwin",
        detector=lambda **_kwargs: fresh,
    )
    assert result.state is WebexActivationState.REFUSED
    assert result.reason_code == "reverification-refused"


def test_show_app_refuses_running_same_bundle_from_a_different_path(tmp_path):
    verified = _mac_app(tmp_path / "Verified")
    other = _mac_app(tmp_path / "Other")
    info = WebexAppInfo(
        state=WebexAppState.INSTALLED,
        publisher_verified=True,
        path=verified,
    )
    runtime = _FakeMacRuntime(
        applications=(_running_application(other),)
    )

    result = show_webex_app(
        info,
        platform_name="darwin",
        detector=lambda **_kwargs: info,
        mac_runtime_factory=lambda: runtime,
        command_runner=_verified_process_runner([]),
    )

    assert result.state is WebexActivationState.REFUSED
    assert result.reason_code == "running-target-mismatch"
    assert runtime.activations == []


def test_show_app_refuses_ambiguous_running_instances(tmp_path):
    app = _mac_app(tmp_path / "Verified")
    runtime = _FakeMacRuntime(
        applications=(
            _running_application(app, pid=1001, handle=11),
            _running_application(app, pid=1002, handle=12),
        )
    )
    info = WebexAppInfo(
        state=WebexAppState.INSTALLED,
        publisher_verified=True,
        path=app,
    )
    process_calls = []

    result = show_webex_app(
        info,
        platform_name="darwin",
        detector=lambda **_kwargs: info,
        mac_runtime_factory=lambda: runtime,
        command_runner=_verified_process_runner(process_calls),
    )

    assert result.state is WebexActivationState.REFUSED
    assert result.reason_code == "ambiguous-running-instances"
    assert process_calls == []
    assert runtime.activations == []


def test_show_app_refuses_when_live_path_identity_cannot_be_proven(
    tmp_path,
    monkeypatch,
):
    app = _mac_app(tmp_path / "Verified")
    runtime = _FakeMacRuntime(
        applications=(_running_application(app),)
    )
    info = WebexAppInfo(
        state=WebexAppState.INSTALLED,
        publisher_verified=True,
        path=app,
    )
    path_checks = 0

    def samefile(*_args):
        nonlocal path_checks
        path_checks += 1
        if path_checks == 1:
            return True
        raise OSError("unavailable")

    monkeypatch.setattr(webex_app.os.path, "samefile", samefile)
    process_calls = []

    result = show_webex_app(
        info,
        platform_name="darwin",
        detector=lambda **_kwargs: info,
        mac_runtime_factory=lambda: runtime,
        command_runner=_verified_process_runner(process_calls),
    )

    assert result.state is WebexActivationState.REFUSED
    assert result.reason_code == "application-path-unverified"
    assert path_checks == 2
    assert process_calls == []
    assert runtime.activations == []


def test_same_path_running_process_must_pass_dynamic_publisher_requirement(
    tmp_path,
):
    app = _mac_app(tmp_path / "Verified")
    running = _running_application(app, pid=8765)
    runtime = _FakeMacRuntime(applications=(running,))
    info = WebexAppInfo(
        state=WebexAppState.INSTALLED,
        publisher_verified=True,
        path=app,
    )
    calls = []

    def unverified(arguments, *, timeout):
        calls.append((arguments, timeout))
        return _completed(
            arguments,
            returncode=1,
            stderr=b"/Users/private/meet/secret",
        )

    result = show_webex_app(
        info,
        platform_name="darwin",
        detector=lambda **_kwargs: info,
        mac_runtime_factory=lambda: runtime,
        command_runner=unverified,
    )

    assert result.state is WebexActivationState.REFUSED
    assert result.reason_code == "process-publisher-unverified"
    assert calls[0][0][-1] == "8765"
    assert runtime.activations == []
    assert "private" not in json.dumps(result.to_public_dict()).lower()


def test_cancellation_after_disk_reverification_never_enters_runtime(tmp_path):
    app = _mac_app(tmp_path / "Verified")
    info = WebexAppInfo(
        state=WebexAppState.INSTALLED,
        publisher_verified=True,
        path=app,
    )
    cancelled = {"value": False}
    runtime_factory_calls = []

    def detector(**_kwargs):
        cancelled["value"] = True
        return info

    result = show_webex_app(
        info,
        platform_name="darwin",
        detector=detector,
        mac_runtime_factory=lambda: runtime_factory_calls.append(True),
        cancelled=lambda: cancelled["value"],
    )

    assert result.state is WebexActivationState.REFUSED
    assert result.reason_code == "activation-cancelled"
    assert runtime_factory_calls == []


def test_cancellation_immediately_before_pid_validation_never_activates(
    tmp_path,
):
    app = _mac_app(tmp_path / "Verified")
    running = _running_application(app)
    runtime = _FakeMacRuntime(applications=(running,))
    info = WebexAppInfo(
        state=WebexAppState.INSTALLED,
        publisher_verified=True,
        path=app,
    )
    cancellation_checks = 0
    process_calls = []

    def cancelled():
        nonlocal cancellation_checks
        cancellation_checks += 1
        return cancellation_checks >= 2

    result = show_webex_app(
        info,
        platform_name="darwin",
        detector=lambda **_kwargs: info,
        mac_runtime_factory=lambda: runtime,
        command_runner=_verified_process_runner(process_calls),
        cancelled=cancelled,
    )

    assert result.reason_code == "activation-cancelled"
    assert process_calls == []
    assert runtime.activations == []


def test_cancellation_after_pid_validation_never_activates(tmp_path):
    app = _mac_app(tmp_path / "Verified")
    running = _running_application(app)
    runtime = _FakeMacRuntime(applications=(running,))
    info = WebexAppInfo(
        state=WebexAppState.INSTALLED,
        publisher_verified=True,
        path=app,
    )
    cancellation_checks = 0
    process_calls = []

    def cancelled():
        nonlocal cancellation_checks
        cancellation_checks += 1
        return cancellation_checks >= 3

    result = show_webex_app(
        info,
        platform_name="darwin",
        detector=lambda **_kwargs: info,
        mac_runtime_factory=lambda: runtime,
        command_runner=_verified_process_runner(process_calls),
        cancelled=cancelled,
    )

    assert result.reason_code == "activation-cancelled"
    assert len(process_calls) == 1
    assert runtime.activations == []
