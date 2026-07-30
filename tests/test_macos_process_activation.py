"""Exact-PID macOS foreground activation without LaunchServices ambiguity."""

from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
import threading
from unittest import mock

from services.macos_process_activation import (
    JamulusForegroundOutcome,
    JamulusForegroundReason,
    MacOSRunningApplication,
    activate_running_macos_application,
    activate_running_macos_application_outcome,
)
from services.bridge_service import BridgeService


class _Runtime:
    def __init__(
        self,
        application,
        *,
        activate_result: bool = True,
        frontmost_results: tuple[bool, ...] = (True,),
    ) -> None:
        self.application = application
        self.activate_result = activate_result
        self.frontmost_results = list(frontmost_results)
        self.requested_pids: list[int] = []
        self.activated: list[MacOSRunningApplication] = []

    def activation_session(self):
        return nullcontext(self)

    def running_application(self, process_identifier):
        self.requested_pids.append(process_identifier)
        return self.application

    def activate(self, application):
        self.activated.append(application)
        return self.activate_result

    def is_frontmost(self, application):
        assert application is self.application
        if len(self.frontmost_results) > 1:
            return self.frontmost_results.pop(0)
        return self.frontmost_results[0]


def _application(path: Path | None, *, pid: int) -> MacOSRunningApplication:
    return MacOSRunningApplication(
        native_handle=99,
        process_identifier=pid,
        bundle_path=path,
    )


def test_exact_pid_and_bundle_object_are_activated(tmp_path: Path) -> None:
    expected = tmp_path / "Pilot" / "Jamulus.app"
    expected.mkdir(parents=True)
    runtime = _Runtime(_application(expected, pid=4321))

    assert activate_running_macos_application(
        4321,
        expected,
        runtime_factory=lambda: runtime,
    )

    assert runtime.requested_pids == [4321]
    assert runtime.activated == [runtime.application]


def test_typed_outcome_public_state_is_reason_code_only(tmp_path: Path) -> None:
    expected = tmp_path / "Pilot" / "Jamulus.app"
    expected.mkdir(parents=True)
    runtime = _Runtime(_application(expected, pid=4321))

    outcome = activate_running_macos_application_outcome(
        4321,
        expected,
        runtime_factory=lambda: runtime,
    )

    assert outcome
    assert outcome.reason is JamulusForegroundReason.FOREGROUNDED
    assert outcome.to_public_dict() == {"reason_code": "foregrounded"}


def test_second_copy_with_same_bundle_identity_is_never_activated(
    tmp_path: Path,
) -> None:
    expected = tmp_path / "Pilot" / "Jamulus.app"
    wrong_copy = tmp_path / "Installed" / "Jamulus.app"
    expected.mkdir(parents=True)
    wrong_copy.mkdir(parents=True)
    runtime = _Runtime(_application(wrong_copy, pid=4321))

    assert not activate_running_macos_application(
        4321,
        expected,
        runtime_factory=lambda: runtime,
    )

    assert runtime.requested_pids == [4321]
    assert runtime.activated == []


def test_stale_lookup_returning_a_different_pid_fails_closed(
    tmp_path: Path,
) -> None:
    expected = tmp_path / "Pilot" / "Jamulus.app"
    expected.mkdir(parents=True)
    runtime = _Runtime(_application(expected, pid=9876))

    assert not activate_running_macos_application(
        4321,
        expected,
        runtime_factory=lambda: runtime,
    )

    assert runtime.activated == []


def test_activation_acceptance_without_frontmost_proof_fails_closed(
    tmp_path: Path,
) -> None:
    expected = tmp_path / "Pilot" / "Jamulus.app"
    expected.mkdir(parents=True)
    runtime = _Runtime(
        _application(expected, pid=4321),
        frontmost_results=(False,),
    )

    with mock.patch(
        "services.macos_process_activation.time.monotonic",
        side_effect=[100.0, 101.0],
    ):
        outcome = activate_running_macos_application_outcome(
            4321,
            expected,
            runtime_factory=lambda: runtime,
        )

    assert not outcome
    assert outcome.reason is JamulusForegroundReason.FRONTMOST_UNCONFIRMED
    assert runtime.activated == [runtime.application]


def test_native_activation_refusal_is_not_reported_as_foreground(
    tmp_path: Path,
) -> None:
    expected = tmp_path / "Pilot" / "Jamulus.app"
    expected.mkdir(parents=True)
    runtime = _Runtime(
        _application(expected, pid=4321),
        activate_result=False,
    )

    assert not activate_running_macos_application(
        4321,
        expected,
        runtime_factory=lambda: runtime,
    )


def test_native_runtime_exception_fails_closed(tmp_path: Path) -> None:
    expected = tmp_path / "Pilot" / "Jamulus.app"
    expected.mkdir(parents=True)

    assert not activate_running_macos_application(
        4321,
        expected,
        runtime_factory=lambda: (_ for _ in ()).throw(
            RuntimeError("native bridge failed")
        ),
    )


def test_typed_failures_distinguish_safe_native_reasons(tmp_path: Path) -> None:
    expected = tmp_path / "Pilot" / "Jamulus.app"
    wrong_copy = tmp_path / "Installed" / "Jamulus.app"
    expected.mkdir(parents=True)
    wrong_copy.mkdir(parents=True)

    wrong_identity = activate_running_macos_application_outcome(
        4321,
        expected,
        runtime_factory=lambda: _Runtime(
            _application(wrong_copy, pid=4321)
        ),
    )
    refused = activate_running_macos_application_outcome(
        4321,
        expected,
        runtime_factory=lambda: _Runtime(
            _application(expected, pid=4321),
            activate_result=False,
        ),
    )
    unavailable = activate_running_macos_application_outcome(
        4321,
        expected,
        runtime_factory=lambda: (_ for _ in ()).throw(
            RuntimeError("/Users/private/native failure")
        ),
    )

    assert wrong_identity.reason is JamulusForegroundReason.IDENTITY_UNVERIFIED
    assert refused.reason is JamulusForegroundReason.ACTIVATION_REFUSED
    assert (
        unavailable.reason
        is JamulusForegroundReason.NATIVE_ACTIVATION_UNAVAILABLE
    )
    encoded = repr(
        (
            wrong_identity.to_public_dict(),
            refused.to_public_dict(),
            unavailable.to_public_dict(),
        )
    )
    assert "/Users/private" not in encoded


def test_invalid_pid_or_non_bundle_target_never_reaches_appkit(
    tmp_path: Path,
) -> None:
    runtime = _Runtime(None)

    assert not activate_running_macos_application(
        0,
        tmp_path / "Jamulus.app",
        runtime_factory=lambda: runtime,
    )
    assert not activate_running_macos_application(
        4321,
        tmp_path / "Jamulus",
        runtime_factory=lambda: runtime,
    )

    assert runtime.requested_pids == []


def _foreground_bridge(
    executable: Path,
    *,
    process_identifier: int = 4321,
) -> tuple[BridgeService, mock.Mock]:
    bridge = BridgeService.__new__(BridgeService)
    bridge._reconnect_lock = threading.Lock()
    process = mock.Mock()
    process.pid = process_identifier
    process.poll.return_value = None
    bridge.jamulus_process = process
    bridge._jamulus_process_generation = 7
    bridge._active_client_component = SimpleNamespace(executable_path=executable)
    return bridge, process


def test_bridge_foregrounds_exact_owned_child_without_osascript_or_launch(
    tmp_path: Path,
) -> None:
    bundle = tmp_path / "Pilot" / "Jamulus.app"
    executable = bundle / "Contents" / "MacOS" / "Jamulus"
    executable.parent.mkdir(parents=True)
    executable.touch()
    bridge, _process = _foreground_bridge(executable)

    with (
        mock.patch(
            "services.bridge_service.sys.platform",
            "darwin",
        ),
        mock.patch(
            "services.bridge_service.activate_running_macos_application_outcome",
            return_value=JamulusForegroundOutcome(
                True,
                JamulusForegroundReason.FOREGROUNDED,
            ),
        ) as activate,
        mock.patch(
            "services.bridge_service.subprocess.Popen",
        ) as popen,
    ):
        assert bridge.bring_jamulus_forward()

    activate.assert_called_once_with(4321, bundle)
    popen.assert_not_called()
    assert bridge.jamulus_foreground_reason_code == "foregrounded"


def test_bridge_never_claims_success_after_process_generation_swap(
    tmp_path: Path,
) -> None:
    bundle = tmp_path / "Pilot" / "Jamulus.app"
    executable = bundle / "Contents" / "MacOS" / "Jamulus"
    executable.parent.mkdir(parents=True)
    executable.touch()
    bridge, old_process = _foreground_bridge(executable)
    replacement = mock.Mock()
    replacement.pid = 9876
    replacement.poll.return_value = None

    def replace_during_activation(_pid, _bundle):
        bridge.jamulus_process = replacement
        bridge._jamulus_process_generation = 8
        return JamulusForegroundOutcome(
            True,
            JamulusForegroundReason.FOREGROUNDED,
        )

    with (
        mock.patch(
            "services.bridge_service.sys.platform",
            "darwin",
        ),
        mock.patch(
            "services.bridge_service.activate_running_macos_application_outcome",
            side_effect=replace_during_activation,
        ),
    ):
        assert not bridge.bring_jamulus_forward()

    old_process.terminate.assert_not_called()
    replacement.terminate.assert_not_called()
    assert bridge.jamulus_foreground_reason_code == "process-changed"


def test_bridge_refuses_activation_when_precheck_poll_is_unproved(
    tmp_path: Path,
) -> None:
    bundle = tmp_path / "Pilot" / "Jamulus.app"
    executable = bundle / "Contents" / "MacOS" / "Jamulus"
    executable.parent.mkdir(parents=True)
    executable.touch()
    bridge, process = _foreground_bridge(executable)
    process.poll.side_effect = RuntimeError("poll unavailable")

    with (
        mock.patch(
            "services.bridge_service.sys.platform",
            "darwin",
        ),
        mock.patch(
            "services.bridge_service.activate_running_macos_application_outcome",
        ) as activate,
    ):
        outcome = bridge.bring_jamulus_forward_outcome()

    assert not outcome
    assert outcome.reason is JamulusForegroundReason.IDENTITY_UNVERIFIED
    activate.assert_not_called()
    assert bridge.jamulus_foreground_reason_code == "identity-unverified"


def test_bridge_never_claims_success_when_postcheck_poll_is_unproved(
    tmp_path: Path,
) -> None:
    bundle = tmp_path / "Pilot" / "Jamulus.app"
    executable = bundle / "Contents" / "MacOS" / "Jamulus"
    executable.parent.mkdir(parents=True)
    executable.touch()
    bridge, process = _foreground_bridge(executable)
    process.poll.side_effect = [None, RuntimeError("poll unavailable")]

    with (
        mock.patch(
            "services.bridge_service.sys.platform",
            "darwin",
        ),
        mock.patch(
            "services.bridge_service.activate_running_macos_application_outcome",
            return_value=JamulusForegroundOutcome(
                True,
                JamulusForegroundReason.FOREGROUNDED,
            ),
        ) as activate,
    ):
        outcome = bridge.bring_jamulus_forward_outcome()

    assert not outcome
    assert outcome.reason is JamulusForegroundReason.PROCESS_CHANGED
    activate.assert_called_once_with(4321, bundle)
    assert bridge.jamulus_foreground_reason_code == "process-changed"


def test_bridge_distinguishes_not_running_from_live_activation_failure(
    tmp_path: Path,
) -> None:
    bundle = tmp_path / "Pilot" / "Jamulus.app"
    executable = bundle / "Contents" / "MacOS" / "Jamulus"
    executable.parent.mkdir(parents=True)
    executable.touch()
    bridge, process = _foreground_bridge(executable)

    process.poll.return_value = 1
    not_running = bridge.bring_jamulus_forward_outcome()
    assert not not_running
    assert not_running.reason is JamulusForegroundReason.NOT_RUNNING
    assert bridge.jamulus_foreground_reason_code == "not-running"

    process.poll.return_value = None
    with (
        mock.patch(
            "services.bridge_service.sys.platform",
            "darwin",
        ),
        mock.patch(
            "services.bridge_service.activate_running_macos_application_outcome",
            return_value=JamulusForegroundOutcome(
                False,
                JamulusForegroundReason.ACTIVATION_REFUSED,
            ),
        ),
    ):
        live_failure = bridge.bring_jamulus_forward_outcome()
        assert not bridge.bring_jamulus_forward()

    assert not live_failure
    assert live_failure.reason is JamulusForegroundReason.ACTIVATION_REFUSED
    assert bridge.jamulus_foreground_reason_code == "activation-refused"
