"""Native Webex detection and official-installer boundary tests."""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
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


@dataclass(frozen=True, slots=True)
class _FakeApplicationReference:
    path: Path
    device: int
    inode: int


class _FakeMacRuntime:
    def __init__(
        self,
        *,
        applications=(),
        activate_result=True,
        frontmost_result=True,
        reference_verified=True,
        launch_result=None,
        on_launch=None,
    ):
        self._applications = tuple(applications)
        self.activate_result = activate_result
        self.frontmost_result = frontmost_result
        self.reference_verified = reference_verified
        self.launch_result = launch_result
        self.on_launch = on_launch
        self.queries: list[str] = []
        self.activations: list[object] = []
        self.frontmost_checks: list[object] = []
        self.references: list[_FakeApplicationReference] = []
        self.reference_verifications: list[_FakeApplicationReference] = []
        self.launches: list[_FakeApplicationReference] = []
        self.session_entries = 0

    def activation_session(self):
        self.session_entries += 1
        return nullcontext(self)

    def running_applications(self, bundle_identifier):
        self.queries.append(bundle_identifier)
        return self._applications

    def activate(self, application):
        self.activations.append(application)
        if callable(self.activate_result):
            return self.activate_result(application)
        return self.activate_result

    def is_frontmost(self, application):
        self.frontmost_checks.append(application)
        if callable(self.frontmost_result):
            return self.frontmost_result(application)
        return self.frontmost_result

    def application_reference(self, path):
        candidate = Path(path)
        identity = candidate.stat(follow_symlinks=False)
        reference = _FakeApplicationReference(
            path=candidate,
            device=identity.st_dev,
            inode=identity.st_ino,
        )
        self.references.append(reference)
        return nullcontext(reference)

    def application_reference_matches_path(self, reference, path):
        try:
            identity = Path(path).stat(follow_symlinks=False)
            return (
                identity.st_dev == reference.device
                and identity.st_ino == reference.inode
            )
        except OSError:
            return False

    def verify_application_reference(self, reference):
        self.reference_verifications.append(reference)
        return self.reference_verified

    def launch_application(self, reference):
        self.launches.append(reference)
        if self.on_launch is not None:
            return self.on_launch(reference)
        if self.launch_result is not None:
            return self.launch_result
        if len(self._applications) == 1:
            return self._applications[0].process_identifier
        return None


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


@pytest.mark.parametrize(
    ("frontmost_pid", "expected"),
    [
        (4321, True),
        (9876, False),
        (None, False),
    ],
)
def test_native_runtime_frontmost_proof_is_exact_pid_only(
    frontmost_pid,
    expected,
):
    runtime = object.__new__(webex_app._MacOSApplicationRuntime)
    runtime._class = lambda _name: 10
    runtime._selector = lambda name: name

    def send_id(_receiver, selector):
        if selector == "sharedWorkspace":
            return 20
        if selector == "frontmostApplication":
            return 30 if frontmost_pid is not None else 0
        raise AssertionError(selector)

    runtime._send_id = send_id
    runtime._send_int = lambda _application, _selector: frontmost_pid
    application = _running_application(
        Path("/Applications/Webex.app"),
        pid=4321,
    )

    assert runtime.is_frontmost(application) is expected


def test_show_app_reopens_one_pid_verified_running_bundle_without_handoff(
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
    assert runtime.queries == [
        WEBEX_MAC_BUNDLE_ID,
        WEBEX_MAC_BUNDLE_ID,
        WEBEX_MAC_BUNDLE_ID,
    ]
    assert runtime.activations == []
    assert runtime.frontmost_checks == [running, running]
    assert [reference.path for reference in runtime.references] == [app]
    assert runtime.reference_verifications == runtime.references
    assert runtime.launches == runtime.references
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
        ),
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
        ),
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
        ),
    ]
    assert all(
        str(app) not in argument
        for argument in process_calls[0][0]
    )
    assert all(
        arguments[0] == "/usr/bin/codesign"
        for arguments, _timeout in process_calls
    )
    assert result.to_public_dict() == {"state": "activated-running"}


def test_show_app_launches_verified_stopped_app_without_url(tmp_path):
    app = _mac_app(tmp_path / "Stopped With Spaces")
    runtime = _FakeMacRuntime(applications=())
    running = _running_application(app, pid=8765)
    command_calls = []
    info = WebexAppInfo(
        state=WebexAppState.INSTALLED,
        publisher_verified=True,
        path=app,
    )

    def launch(reference):
        assert reference == runtime.references[0]
        runtime._applications = (running,)
        return 8765

    runtime.on_launch = launch

    result = show_webex_app(
        info,
        platform_name="darwin",
        detector=lambda **_kwargs: info,
        mac_runtime_factory=lambda: runtime,
        command_runner=_verified_process_runner(command_calls),
    )

    assert result.state is WebexActivationState.LAUNCHED_APP
    assert result.succeeded
    assert runtime.activations == []
    assert runtime.frontmost_checks == [running, running]
    assert command_calls == [
        (
            [
                "/usr/bin/codesign",
                "--verify",
                "--strict",
                "--verbose=2",
                f"-R={WEBEX_MAC_PROCESS_REQUIREMENT}",
                "8765",
            ],
            30.0,
        ),
        (
            [
                "/usr/bin/codesign",
                "--verify",
                "--strict",
                "--verbose=2",
                f"-R={WEBEX_MAC_PROCESS_REQUIREMENT}",
                "8765",
            ],
            30.0,
        ),
    ]
    assert not any(
        "http" in argument.lower()
        or "meet/" in argument.lower()
        or "--args" == argument
        for arguments, _timeout in command_calls
        for argument in arguments
    )
    assert all(
        arguments[0] == "/usr/bin/codesign"
        for arguments, _timeout in command_calls
    )
    assert result.to_public_dict() == {"state": "launched-app"}


def test_show_app_running_uses_launchservices_and_fresh_frontmost_proof(
    tmp_path,
):
    app = _mac_app(tmp_path / "Running")
    running = _running_application(app)
    runtime = _FakeMacRuntime(applications=(running,))
    command_calls = []
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
        command_runner=_verified_process_runner(command_calls),
    )

    assert result.state is WebexActivationState.ACTIVATED_RUNNING
    assert result.succeeded
    assert runtime.activations == []
    assert runtime.frontmost_checks == [running, running]
    assert [arguments[0] for arguments, _timeout in command_calls] == [
        "/usr/bin/codesign",
        "/usr/bin/codesign",
        "/usr/bin/codesign",
    ]
    assert runtime.launches == runtime.references

def test_show_app_never_trusts_native_launch_without_exact_active_process(
    tmp_path,
    monkeypatch,
):
    app = _mac_app(tmp_path / "Stopped")
    runtime = _FakeMacRuntime(applications=(), launch_result=8765)
    calls = []
    info = WebexAppInfo(
        state=WebexAppState.INSTALLED,
        publisher_verified=True,
        path=app,
    )
    monkeypatch.setattr(
        webex_app,
        "_WEBEX_LAUNCH_CONFIRM_TIMEOUT_SECONDS",
        0,
    )
    monkeypatch.setattr(
        webex_app,
        "_WEBEX_LAUNCH_POLL_INTERVAL_SECONDS",
        0,
    )

    result = show_webex_app(
        info,
        platform_name="darwin",
        detector=lambda **_kwargs: info,
        mac_runtime_factory=lambda: runtime,
        command_runner=_verified_process_runner(calls),
    )

    assert result.state is WebexActivationState.FAILED
    assert result.reason_code == "native-launch-unconfirmed"
    assert not result
    assert calls == []
    assert runtime.launches == runtime.references
    assert runtime.activations == []


def test_show_app_never_trusts_exact_verified_pid_that_stays_backgrounded(
    tmp_path,
    monkeypatch,
):
    app = _mac_app(tmp_path / "Running")
    running = _running_application(app, pid=9988)
    runtime = _FakeMacRuntime(
        applications=(running,),
        frontmost_result=False,
    )
    calls = []
    info = WebexAppInfo(
        state=WebexAppState.INSTALLED,
        publisher_verified=True,
        path=app,
    )
    monkeypatch.setattr(
        webex_app,
        "_WEBEX_LAUNCH_CONFIRM_TIMEOUT_SECONDS",
        0,
    )
    monkeypatch.setattr(
        webex_app,
        "_WEBEX_LAUNCH_POLL_INTERVAL_SECONDS",
        0,
    )

    result = show_webex_app(
        info,
        platform_name="darwin",
        detector=lambda **_kwargs: info,
        mac_runtime_factory=lambda: runtime,
        command_runner=_verified_process_runner(calls),
    )

    assert result.state is WebexActivationState.FAILED
    assert result.reason_code == "native-launch-unconfirmed"
    assert [arguments[0] for arguments, _timeout in calls] == [
        "/usr/bin/codesign",
        "/usr/bin/codesign",
    ]
    assert len(runtime.frontmost_checks) == 1


def test_show_app_refuses_wrong_running_target_after_native_launch(tmp_path):
    app = _mac_app(tmp_path / "Verified")
    other = _mac_app(tmp_path / "Other")
    runtime = _FakeMacRuntime(applications=())
    info = WebexAppInfo(
        state=WebexAppState.INSTALLED,
        publisher_verified=True,
        path=app,
    )

    def launch(_reference):
        runtime._applications = (_running_application(other),)
        return 4321

    runtime.on_launch = launch

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


def test_show_app_cancellation_after_launch_stops_post_launch_activation(
    tmp_path,
):
    app = _mac_app(tmp_path / "Verified")
    runtime = _FakeMacRuntime(applications=())
    cancelled = {"value": False}
    info = WebexAppInfo(
        state=WebexAppState.INSTALLED,
        publisher_verified=True,
        path=app,
    )

    def launch(_reference):
        runtime._applications = (_running_application(app),)
        cancelled["value"] = True
        return 4321

    runtime.on_launch = launch

    result = show_webex_app(
        info,
        platform_name="darwin",
        detector=lambda **_kwargs: info,
        mac_runtime_factory=lambda: runtime,
        command_runner=_verified_process_runner([]),
        cancelled=lambda: cancelled["value"],
    )

    assert result.state is WebexActivationState.REFUSED
    assert result.reason_code == "activation-cancelled"
    assert runtime.activations == []


def test_show_app_launchservices_failure_is_typed_and_never_claims_success(
    tmp_path,
):
    app = _mac_app(tmp_path / "Verified")
    runtime = _FakeMacRuntime(applications=())
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
    assert result.reason_code == "native-launch-failed"
    assert runtime.frontmost_checks == []


def test_show_app_refuses_ambiguous_instances_after_native_launch(tmp_path):
    app = _mac_app(tmp_path / "Verified")
    runtime = _FakeMacRuntime(applications=())
    info = WebexAppInfo(
        state=WebexAppState.INSTALLED,
        publisher_verified=True,
        path=app,
    )

    def launch(_reference):
        runtime._applications = (
            _running_application(app, pid=1001),
            _running_application(app, pid=1002),
        )
        return 1001

    runtime.on_launch = launch

    result = show_webex_app(
        info,
        platform_name="darwin",
        detector=lambda **_kwargs: info,
        mac_runtime_factory=lambda: runtime,
        command_runner=_verified_process_runner([]),
    )

    assert result.state is WebexActivationState.REFUSED
    assert result.reason_code == "ambiguous-running-instances"


def test_show_app_refuses_post_launch_publisher_failure(tmp_path):
    app = _mac_app(tmp_path / "Verified")
    runtime = _FakeMacRuntime(applications=())
    info = WebexAppInfo(
        state=WebexAppState.INSTALLED,
        publisher_verified=True,
        path=app,
    )

    def launch(_reference):
        runtime._applications = (
            _running_application(app, pid=7007),
        )
        return 7007

    runtime.on_launch = launch

    def runner(arguments, *, timeout):
        del timeout
        return _completed(arguments, returncode=1)

    result = show_webex_app(
        info,
        platform_name="darwin",
        detector=lambda **_kwargs: info,
        mac_runtime_factory=lambda: runtime,
        command_runner=runner,
    )

    assert result.state is WebexActivationState.REFUSED
    assert result.reason_code == "process-publisher-unverified"
    assert runtime.frontmost_checks == []


def test_show_app_refuses_running_pid_change_after_reopen_request(tmp_path):
    app = _mac_app(tmp_path / "Verified")
    original = _running_application(app, pid=7001)
    replacement = _running_application(app, pid=7002)
    runtime = _FakeMacRuntime(applications=(original,))
    info = WebexAppInfo(
        state=WebexAppState.INSTALLED,
        publisher_verified=True,
        path=app,
    )

    def launch(_reference):
        runtime._applications = (replacement,)
        return original.process_identifier

    runtime.on_launch = launch

    result = show_webex_app(
        info,
        platform_name="darwin",
        detector=lambda **_kwargs: info,
        mac_runtime_factory=lambda: runtime,
        command_runner=_verified_process_runner([]),
    )

    assert result.state is WebexActivationState.REFUSED
    assert result.reason_code == "running-target-changed"
    assert runtime.frontmost_checks == []


def test_show_app_refuses_unverified_identity_bound_application(tmp_path):
    app = _mac_app(tmp_path / "Verified")
    runtime = _FakeMacRuntime(
        applications=(),
        reference_verified=False,
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
    assert result.reason_code == "application-reference-unverified"
    assert process_calls == []
    assert runtime.launches == []


def test_show_app_launch_target_survives_path_replacement_after_binding(
    tmp_path,
):
    app = _mac_app(tmp_path / "Verified")
    moved = tmp_path / "Moved" / "Webex.app"
    moved.parent.mkdir()
    runtime = _FakeMacRuntime(applications=())
    info = WebexAppInfo(
        state=WebexAppState.INSTALLED,
        publisher_verified=True,
        path=app,
    )

    def launch(reference):
        app.rename(moved)
        replacement = _mac_app(tmp_path / "Verified")
        assert replacement == app
        assert replacement.stat().st_ino != reference.inode
        assert moved.stat().st_ino == reference.inode
        runtime._applications = (
            _running_application(moved, pid=8123),
        )
        return 8123

    runtime.on_launch = launch
    result = show_webex_app(
        info,
        platform_name="darwin",
        detector=lambda **_kwargs: info,
        mac_runtime_factory=lambda: runtime,
        command_runner=_verified_process_runner([]),
    )

    assert result.state is WebexActivationState.LAUNCHED_APP
    assert result.succeeded
    assert runtime.launches == runtime.references
    assert runtime.frontmost_checks == [
        _running_application(moved, pid=8123),
        _running_application(moved, pid=8123),
    ]


@pytest.mark.skipif(
    webex_app.sys.platform != "darwin",
    reason="Core Foundation file references are macOS-only",
)
def test_native_file_reference_follows_original_bundle_after_path_replacement(
    tmp_path,
):
    original = tmp_path / "Original Webex.app"
    original.mkdir()
    (original / "original-marker").write_text("original", encoding="utf-8")
    moved = tmp_path / "Moved Webex.app"
    runtime = webex_app._MacOSApplicationRuntime()

    with runtime.application_reference(original) as reference:
        assert runtime.application_reference_matches_path(reference, original)
        original.rename(moved)
        original.mkdir()
        (original / "replacement-marker").write_text(
            "replacement",
            encoding="utf-8",
        )

        assert runtime.application_reference_matches_path(reference, moved)
        assert not runtime.application_reference_matches_path(
            reference,
            original,
        )


def test_native_launch_uses_only_bound_application_url_and_empty_config():
    runtime = object.__new__(webex_app._MacOSApplicationRuntime)
    runtime._pool = lambda: nullcontext()
    runtime._class = lambda name: name
    runtime._selector = lambda name: name
    runtime._send_id = (
        lambda receiver, selector: (
            20
            if (receiver, selector) == ("NSWorkspace", "sharedWorkspace")
            else 30
            if (receiver, selector) == ("NSDictionary", "dictionary")
            else 0
        )
    )
    launch_calls = []

    def launch(receiver, selector, url, options, configuration, error):
        launch_calls.append(
            (receiver, selector, url, options, configuration, error)
        )
        return 40

    runtime._send_id_id_ulong_id_ptr = launch
    runtime._send_int = lambda application, selector: (
        4567
        if (application, selector) == (40, "processIdentifier")
        else 0
    )
    reference = webex_app._MacApplicationReference(native_url=77)

    assert runtime.launch_application(reference) == 4567
    assert len(launch_calls) == 1
    receiver, selector, url, options, configuration, _error = (
        launch_calls[0]
    )
    assert receiver == 20
    assert selector == (
        "launchApplicationAtURL:options:configuration:error:"
    )
    assert url == 77
    assert options == 0
    assert configuration == 30


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


def test_show_app_returns_typed_native_failure_without_disclosing_path(
    tmp_path,
):
    app = _mac_app(tmp_path / "Private Folder")
    info = WebexAppInfo(
        state=WebexAppState.INSTALLED,
        publisher_verified=True,
        path=app,
    )

    result = show_webex_app(
        info,
        platform_name="darwin",
        detector=lambda **_kwargs: info,
        mac_runtime_factory=lambda: (_ for _ in ()).throw(OSError("no")),
    )

    assert result.state is WebexActivationState.FAILED
    assert result.reason_code == "activation-exception"
    assert str(tmp_path) not in json.dumps(result.to_public_dict())


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
    def cannot_match(_reference, _path):
        raise OSError("unavailable")

    runtime.application_reference_matches_path = cannot_match
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
        return cancellation_checks >= 4

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
    assert runtime.launches == []
