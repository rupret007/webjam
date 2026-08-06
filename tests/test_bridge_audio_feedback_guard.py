from __future__ import annotations

import base64
from pathlib import Path
from unittest import mock

from core.audio_feedback_guard import AudioFeedbackRisk
from core.coreaudio_devices import CoreAudioDevice, CoreAudioScan
from core.jamulus_profile import (
    PINNED_JAMULUS_VERSION,
    JamulusNativeProfileManager,
)
from services.bridge_service import BridgeService


def _bridge_with_profile(tmp_path: Path, selector: str) -> BridgeService:
    manager = JamulusNativeProfileManager(home=tmp_path, platform="darwin")
    plan = manager.plan(jamulus_version=PINNED_JAMULUS_VERSION)
    encoded = base64.b64encode(selector.encode("utf-8")).decode("ascii")
    plan.profile_path.write_text(
        f"<client><auddev_base64>{encoded}</auddev_base64></client>",
        encoding="utf-8",
    )
    bridge = BridgeService.__new__(BridgeService)
    bridge._native_profile_manager = manager
    bridge._active_native_profile = None
    bridge._last_resolved_client_component = None
    return bridge


def _device(
    uid: str,
    name: str,
    *,
    inputs: int = 0,
    outputs: int = 0,
) -> CoreAudioDevice:
    return CoreAudioDevice(
        uid=uid,
        name=name,
        object_id=len(uid) + 1,
        input_channels=inputs,
        output_channels=outputs,
        nominal_rate=48_000.0,
    )


def test_explicit_builtin_profile_warns_without_returning_device_names(
    tmp_path: Path,
) -> None:
    bridge = _bridge_with_profile(
        tmp_path,
        "in: Built-in Microphone/out: Built-in Output",
    )

    with mock.patch("services.bridge_service.sys.platform", "darwin"):
        result = bridge.prelaunch_audio_feedback_assessment()

    assert result.risk is AudioFeedbackRisk.BUILTIN_MIC_AND_SPEAKERS
    assert not hasattr(result, "input_name")
    assert not hasattr(result, "output_name")


def test_system_default_selector_resolves_current_coreaudio_defaults(
    tmp_path: Path,
) -> None:
    bridge = _bridge_with_profile(tmp_path, "System Default In/Out Devices")
    scan = CoreAudioScan(
        devices=(
            _device("mic", "MacBook Pro Microphone", inputs=2),
            _device("speakers", "MacBook Pro Speakers", outputs=2),
        ),
        default_input_uid="mic",
        default_output_uid="speakers",
    )

    with mock.patch("services.bridge_service.sys.platform", "darwin"), mock.patch(
        "core.coreaudio_devices.scan_coreaudio_devices",
        return_value=scan,
    ):
        result = bridge.prelaunch_audio_feedback_assessment()

    assert result.risk is AudioFeedbackRisk.BUILTIN_MIC_AND_SPEAKERS


def test_external_system_default_output_does_not_warn(tmp_path: Path) -> None:
    bridge = _bridge_with_profile(tmp_path, "System Default In/Out Devices")
    scan = CoreAudioScan(
        devices=(
            _device("mic", "MacBook Pro Microphone", inputs=2),
            _device("usb", "Scarlett USB Audio Interface", outputs=2),
        ),
        default_input_uid="mic",
        default_output_uid="usb",
    )

    with mock.patch("services.bridge_service.sys.platform", "darwin"), mock.patch(
        "core.coreaudio_devices.scan_coreaudio_devices",
        return_value=scan,
    ):
        result = bridge.prelaunch_audio_feedback_assessment()

    assert result.risk is AudioFeedbackRisk.NOT_DETECTED


def test_unavailable_or_non_macos_evidence_remains_unknown(tmp_path: Path) -> None:
    bridge = _bridge_with_profile(tmp_path, "System Default In/Out Devices")
    with mock.patch("services.bridge_service.sys.platform", "linux"):
        assert (
            bridge.prelaunch_audio_feedback_assessment().risk
            is AudioFeedbackRisk.UNKNOWN
        )
    with mock.patch("services.bridge_service.sys.platform", "darwin"), mock.patch(
        "core.coreaudio_devices.scan_coreaudio_devices",
        return_value=CoreAudioScan(error="unavailable"),
    ):
        assert (
            bridge.prelaunch_audio_feedback_assessment().risk
            is AudioFeedbackRisk.UNKNOWN
        )
