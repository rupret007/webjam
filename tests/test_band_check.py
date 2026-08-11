from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from core.band_check import (
    BandCheckMode,
    BandCheckObservations,
    BandCheckOutcome,
    BandCheckSession,
    BandCheckStatus,
    BandCheckStep,
    BandCheckStepKey,
    VerificationSignature,
    build_band_check_session,
    build_verification_signature,
    load_verification,
    save_verification,
)
from core.preflight import CheckItem, ReadyCheckReport


def _step(
    key: BandCheckStepKey,
    status: BandCheckStatus,
    *,
    required: bool = True,
    action: str = "",
) -> BandCheckStep:
    return BandCheckStep(key, key.value, status, "detail", action, required=required)


def _session() -> BandCheckSession:
    return BandCheckSession(
        BandCheckMode.PRE_SESSION,
        [
            _step(BandCheckStepKey.MUSIC_ENGINE, BandCheckStatus.PASS),
            _step(
                BandCheckStepKey.AUDIO_INPUT,
                BandCheckStatus.PENDING,
                action="Check Input",
            ),
            _step(
                BandCheckStepKey.HEADPHONES,
                BandCheckStatus.PENDING,
                action="Play Left & Right",
            ),
            _step(
                BandCheckStepKey.TEST_RECORDING,
                BandCheckStatus.PENDING,
                action="Record 5 Seconds",
            ),
            _step(
                BandCheckStepKey.RECORDING_PATH,
                BandCheckStatus.PENDING,
                action="Record 5 Seconds",
            ),
            _step(
                BandCheckStepKey.STUDIO,
                BandCheckStatus.PENDING,
                action="Record 5 Seconds",
            ),
        ],
    )


def test_exact_outcomes_and_single_next_action() -> None:
    session = _session()
    assert session.outcome is BandCheckOutcome.ACTION_NEEDED
    assert session.outcome.value == "Action Needed"
    assert session.primary_action == "Check Input"

    session.observe_input(rms=0.03, peak=0.12, clipped=False)
    session.confirm_headphones(True)
    session.mark_scratch_recording(
        valid=True,
        duration_s=5.0,
        sample_rate=48_000,
        channels=1,
        has_signal=True,
    )
    session.confirm_scratch_playback(True)
    assert session.outcome is BandCheckOutcome.READY
    assert session.outcome.value == "Ready to Jam"

    session.steps.append(
        _step(
            BandCheckStepKey.WEBEX,
            BandCheckStatus.WARNING,
            required=False,
            action="Review Webex audio",
        )
    )
    assert session.outcome is BandCheckOutcome.READY
    assert session.outcome.value == "Ready to Jam"
    assert session.primary_action == "Close Band Check"


def test_local_input_evidence_never_claims_jamulus_path() -> None:
    session = _session()
    session.observe_input(rms=0.1, peak=0.3, clipped=False)
    input_step = session.step(BandCheckStepKey.AUDIO_INPUT)
    assert input_step.status is BandCheckStatus.PASS
    assert "WebJam can hear this input" in input_step.detail
    assert "checked separately" in input_step.detail
    assert "band can hear" not in input_step.detail.lower()


def test_silence_and_clipping_are_distinct() -> None:
    session = _session()
    session.observe_input(rms=0.0, peak=0.0, clipped=False)
    assert session.step(BandCheckStepKey.AUDIO_INPUT).status is BandCheckStatus.RUNNING
    session.observe_input(rms=0.4, peak=1.0, clipped=True)
    step = session.step(BandCheckStepKey.AUDIO_INPUT)
    assert step.status is BandCheckStatus.WARNING
    assert "clipping" in step.detail


def test_silent_scratch_proves_writer_but_not_input_recording() -> None:
    session = _session()
    session.mark_scratch_recording(
        valid=True,
        duration_s=5.0,
        sample_rate=48_000,
        channels=1,
        has_signal=False,
    )
    assert (
        session.step(BandCheckStepKey.TEST_RECORDING).status
        is BandCheckStatus.ACTION_NEEDED
    )
    assert session.step(BandCheckStepKey.RECORDING_PATH).status is BandCheckStatus.PASS
    assert session.step(BandCheckStepKey.STUDIO).status is BandCheckStatus.PASS


def test_live_observe_uses_production_evidence_and_has_no_lifecycle_actions() -> None:
    session = BandCheckSession(
        BandCheckMode.LIVE_OBSERVE,
        [
            _step(BandCheckStepKey.MUSIC_ENGINE, BandCheckStatus.PENDING),
            _step(BandCheckStepKey.AUDIO_INPUT, BandCheckStatus.PENDING),
            _step(
                BandCheckStepKey.MUSIC_PATH,
                BandCheckStatus.WARNING,
                required=False,
            ),
        ],
    )
    session.apply_live_observations(
        BandCheckObservations(
            music_engine_running=True,
            music_engine_responsive=True,
            production_local_signal=True,
            production_remote_signal=True,
            peer_connected=True,
            local_meter_active=True,
            local_meter_rms=0.04,
            local_meter_peak=0.08,
        )
    )
    assert session.step(BandCheckStepKey.MUSIC_ENGINE).status is BandCheckStatus.PASS
    assert session.step(BandCheckStepKey.MUSIC_PATH).status is BandCheckStatus.WARNING
    assert "Jamulus reports" in session.step(BandCheckStepKey.MUSIC_PATH).detail
    assert "not proof" in session.step(BandCheckStepKey.MUSIC_PATH).detail
    assert not session.evidence.musician_confirmed_two_way_audibility
    assert session.step(BandCheckStepKey.AUDIO_INPUT).status is BandCheckStatus.PASS


def test_live_observe_never_applies_observations_to_pre_session() -> None:
    session = _session()
    before = list(session.steps)
    session.apply_live_observations(
        BandCheckObservations(
            music_engine_running=False,
            local_meter_active=True,
            local_meter_rms=1.0,
        )
    )
    assert session.steps == before


def test_build_maps_preflight_and_omits_webex_when_unconfigured() -> None:
    report = ReadyCheckReport(
        [
            CheckItem("Jamulus installed", True, "/bundled/Jamulus"),
            CheckItem("Jamulus server set", True, "band:22124"),
            CheckItem("Meter and local recording input", True, "Interface"),
            CheckItem("Host recorder", True, "not configured", required=False),
        ]
    )
    settings = SimpleNamespace(host_server_enabled=False, webex_url="")
    with (
        mock.patch("core.preflight.run_ready_check", return_value=report),
        mock.patch("core.band_check.music_engine_version", return_value="3.12.2"),
    ):
        session = build_band_check_session(settings)
    assert session.mode is BandCheckMode.PRE_SESSION
    assert BandCheckStepKey.WEBEX not in {step.key for step in session.steps}
    assert session.step(BandCheckStepKey.AUDIO_INPUT).status is BandCheckStatus.PENDING
    guest_server = session.step(BandCheckStepKey.BAND_SERVER)
    assert guest_server.status is BandCheckStatus.WARNING
    assert "Reachability is checked" in guest_server.detail


def test_storage_warning_is_visible_without_blocking_band_check() -> None:
    report = ReadyCheckReport(
        [
            CheckItem("Jamulus installed", True, "/bundled/Jamulus"),
            CheckItem("Jamulus server set", True, "band:22124"),
            CheckItem("Meter and local recording input", True, "Interface"),
            CheckItem(
                "Recording storage",
                True,
                "Recording storage is running low.",
                warning=True,
            ),
            CheckItem("Host recorder", True, "not configured", required=False),
        ]
    )
    settings = SimpleNamespace(host_server_enabled=False, webex_url="")
    with (
        mock.patch("core.preflight.run_ready_check", return_value=report),
        mock.patch("core.band_check.music_engine_version", return_value="3.12.2"),
    ):
        session = build_band_check_session(settings)

    storage = session.step(BandCheckStepKey.RECORDING_PATH)
    assert storage.status is BandCheckStatus.WARNING
    assert storage.detail == "Recording storage is running low."
    assert storage.next_action == "Recording Setup"
    session.mark_scratch_recording(
        valid=True,
        duration_s=5.0,
        sample_rate=48_000,
        channels=1,
        has_signal=True,
    )
    storage = session.step(BandCheckStepKey.RECORDING_PATH)
    assert storage.status is BandCheckStatus.WARNING
    assert storage.detail == "Recording storage is running low."


def test_music_engine_must_launch_exact_compatible_version() -> None:
    report = ReadyCheckReport(
        [
            CheckItem("Jamulus installed", True, "/bundled/Jamulus"),
            CheckItem("Jamulus server set", True),
            CheckItem("Meter and local recording input", True),
            CheckItem("Host recorder", True, required=False),
        ]
    )
    settings = SimpleNamespace(host_server_enabled=False, webex_url="")
    with (
        mock.patch("core.preflight.run_ready_check", return_value=report),
        mock.patch("core.band_check.music_engine_version", return_value="3.11.0"),
    ):
        session = build_band_check_session(settings)
    engine = session.step(BandCheckStepKey.MUSIC_ENGINE)
    assert engine.status is BandCheckStatus.ACTION_NEEDED
    assert "version=3.11.0" in engine.technical_details
    assert "required_version=3.12.2" in engine.technical_details


def test_missing_guest_server_explains_how_to_get_a_fresh_invite() -> None:
    report = ReadyCheckReport(
        [
            CheckItem("Jamulus installed", True),
            CheckItem(
                "Jamulus server set",
                False,
                "not configured",
                required=True,
            ),
            CheckItem("Meter and local recording input", True),
        ]
    )
    settings = SimpleNamespace(
        host_server_enabled=False,
        webex_url="",
        audio_input_device_index=-1,
    )
    with (
        mock.patch("core.preflight.run_ready_check", return_value=report),
        mock.patch("core.band_check.music_engine_version", return_value="3.12.2"),
    ):
        session = build_band_check_session(settings)

    server = session.step(BandCheckStepKey.BAND_SERVER)
    assert server.status is BandCheckStatus.ACTION_NEEDED
    assert "paste a fresh invite" in server.detail
    assert "open it again" in server.detail
    assert server.next_action == "Close Band Check"


def test_host_server_certification_promotes_real_lifecycle_to_pass() -> None:
    report = ReadyCheckReport(
        [
            CheckItem("Jamulus installed", True),
            CheckItem("Jamulus server set", True),
            CheckItem("Band server (hosted)", True),
            CheckItem("Meter and local recording input", True),
            CheckItem("Host recorder", True),
        ]
    )
    settings = SimpleNamespace(host_server_enabled=True, webex_url="")
    certification = SimpleNamespace(
        ok=True,
        warning=False,
        detail="Production lifecycle passed.",
        technical_details=("version_verified=True", "ports_released=True"),
    )
    with (
        mock.patch("core.preflight.run_ready_check", return_value=report),
        mock.patch("core.band_check.music_engine_version", return_value="3.12.2"),
    ):
        session = build_band_check_session(
            settings,
            host_server_certification=certification,
        )
    server = session.step(BandCheckStepKey.BAND_SERVER)
    assert server.status is BandCheckStatus.PASS
    assert server.detail == "Production lifecycle passed."
    assert server.next_action == ""
    assert "ports_released=True" in server.technical_details


def test_authenticated_external_host_server_is_truthful_warning() -> None:
    report = ReadyCheckReport(
        [
            CheckItem("Jamulus installed", True),
            CheckItem("Jamulus server set", True),
            CheckItem("Band server (hosted)", True),
            CheckItem("Meter and local recording input", True),
            CheckItem("Host recorder", True),
        ]
    )
    settings = SimpleNamespace(host_server_enabled=True, webex_url="")
    certification = SimpleNamespace(
        ok=True,
        warning=True,
        detail="Authenticated external server; version and stop unverified.",
        technical_details=("external_server=True",),
    )
    with (
        mock.patch("core.preflight.run_ready_check", return_value=report),
        mock.patch("core.band_check.music_engine_version", return_value="3.12.2"),
    ):
        session = build_band_check_session(
            settings,
            host_server_certification=certification,
        )
    server = session.step(BandCheckStepKey.BAND_SERVER)
    assert server.status is BandCheckStatus.WARNING
    assert "external" in server.detail.lower()
    assert server.next_action == ""


def test_optional_configured_webex_can_only_warn() -> None:
    report = ReadyCheckReport(
        [
            CheckItem("Jamulus installed", True),
            CheckItem("Jamulus server set", True),
            CheckItem("Meter and local recording input", True),
            CheckItem("Host recorder", True, required=False),
            CheckItem("Conversation companion", False, "bad URL", required=False),
        ]
    )
    settings = SimpleNamespace(
        host_server_enabled=False,
        webex_url="https://example.com/not-webex",
    )
    with (
        mock.patch("core.preflight.run_ready_check", return_value=report),
        mock.patch("core.band_check.music_engine_version", return_value="3.12.2"),
    ):
        session = build_band_check_session(settings)
    webex = session.step(BandCheckStepKey.WEBEX)
    assert webex.status is BandCheckStatus.WARNING
    assert not webex.required
    assert webex.next_action == ""
    assert session.primary_action != "Open Settings"


def test_built_live_session_exposes_no_device_or_lifecycle_action() -> None:
    report = ReadyCheckReport(
        [
            CheckItem("Jamulus installed", True),
            CheckItem("Jamulus server set", True),
            CheckItem("Meter and local recording input", True),
            CheckItem("Host recorder", True, required=False),
        ]
    )
    settings = SimpleNamespace(host_server_enabled=False, webex_url="")
    observations = BandCheckObservations(
        music_engine_running=True,
        music_engine_responsive=True,
        local_meter_active=True,
        local_meter_rms=0.1,
        local_meter_peak=0.2,
    )
    with (
        mock.patch("core.preflight.run_ready_check", return_value=report),
        mock.patch("core.band_check.music_engine_version", return_value="3.12.2"),
    ):
        session = build_band_check_session(
            settings,
            mode=BandCheckMode.LIVE_OBSERVE,
            observations=observations,
        )
    for key in (
        BandCheckStepKey.HEADPHONES,
        BandCheckStepKey.TEST_RECORDING,
        BandCheckStepKey.RECORDING_PATH,
        BandCheckStepKey.STUDIO,
    ):
        step = session.step(key)
        assert not step.required
        assert not step.next_action
    assert session.outcome is BandCheckOutcome.WARNING


def test_verification_round_trip_private_and_invalidates_on_any_signature_change(
    tmp_path: Path,
) -> None:
    session = _session()
    session.observe_input(rms=0.1, peak=0.2, clipped=False)
    session.confirm_headphones(True)
    session.mark_scratch_recording(
        valid=True,
        duration_s=5.0,
        sample_rate=48_000,
        channels=1,
        has_signal=True,
    )
    session.confirm_scratch_playback(True)
    signature = VerificationSignature(
        "1.0.0", "3.12.2", "portaudio:0:SSL", 48_000, (0,)
    )
    path = tmp_path / "verification.json"
    saved = save_verification(
        path,
        signature=signature,
        session=session,
        now=datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc),
    )
    loaded = load_verification(path)
    assert loaded == saved
    assert loaded is not None and loaded.matches(signature)
    assert not loaded.matches(
        VerificationSignature("1.0.1", "3.12.2", "portaudio:0:SSL", 48_000, (0,))
    )
    assert not loaded.matches(
        VerificationSignature("1.0.0", "3.12.2", "portaudio:1:SSL", 48_000, (0,))
    )
    assert not loaded.matches(
        VerificationSignature("1.0.0", "3.12.2", "portaudio:0:SSL", 44_100, (0,))
    )
    unavailable = VerificationSignature(
        "1.0.0",
        "3.12.2",
        "portaudio:0:SSL",
        48_000,
        (0,),
        output_device_id="unavailable:Missing Interface",
    )
    unavailable_saved = save_verification(
        tmp_path / "unavailable.json",
        signature=unavailable,
        session=session,
    )
    assert unavailable_saved.usable
    assert not unavailable_saved.matches(unavailable)
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600


def test_action_needed_verification_is_persisted_but_not_usable(tmp_path: Path) -> None:
    session = _session()
    signature = VerificationSignature("1", "3", "device", 48_000, (0,))
    saved = save_verification(
        tmp_path / "failed.json", signature=signature, session=session
    )
    assert saved.outcome is BandCheckOutcome.ACTION_NEEDED
    assert not saved.usable
    assert not saved.matches(signature)


def test_mono_headphone_confirmation_is_persisted_as_usable(tmp_path: Path) -> None:
    session = _session()
    session.observe_input(rms=0.1, peak=0.2, clipped=False)
    session.confirm_headphones(True, stereo=False)
    session.mark_scratch_recording(
        valid=True,
        duration_s=5.0,
        sample_rate=48_000,
        channels=1,
        has_signal=True,
    )
    session.confirm_scratch_playback(True)
    signature = VerificationSignature("1", "3.12.2", "mono", 48_000, (0,))
    saved = save_verification(
        tmp_path / "mono.json",
        signature=signature,
        session=session,
    )
    assert "headphones_output" in saved.manual_confirmations
    assert saved.usable


def test_corrupt_or_unknown_verification_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "verification.json"
    path.write_text("not json", encoding="utf-8")
    assert load_verification(path) is None
    path.write_text(json.dumps({"schema": 999}), encoding="utf-8")
    assert load_verification(path) is None


def test_signature_preserves_device_zero_and_channel_configuration() -> None:
    settings = SimpleNamespace(
        audio_input_device_index=0,
        audio_samplerate=48_000,
        audio_blocksize=128,
        local_capture_enabled=True,
        take_playback_output_device="Studio Output",
        takes_directory="/tmp/webjam-takes",
    )

    def query_device(_device, kind):
        if kind == "input":
            return {"name": "SSL 2+"}
        return {"name": "Studio Output", "max_output_channels": 2}

    with mock.patch("sounddevice.query_devices", side_effect=query_device) as query:
        signature = build_verification_signature(
            settings,
            app_version="1.0.0",
            engine_version="3.12.2",
        )
    assert query.call_args_list == [
        mock.call(0, "input"),
        mock.call("Studio Output", "output"),
    ]
    assert signature.input_device_id == "portaudio:0:SSL 2+"
    assert signature.input_channels == (0, 1)
    assert signature.sample_rate == 48_000
    assert signature.audio_blocksize == 128
    assert signature.output_device_id.endswith(":Studio Output:2")
    assert len(signature.recording_path_id) == 64


def test_signature_invalidates_changed_recording_path_or_output_topology(
    tmp_path: Path,
) -> None:
    settings = SimpleNamespace(
        audio_input_device_index=0,
        audio_samplerate=48_000,
        audio_blocksize=0,
        local_capture_enabled=False,
        host_server_enabled=False,
        take_playback_output_device="SSL 2+",
        takes_directory=str(tmp_path / "takes-a"),
        config_file=str(tmp_path / "settings.json"),
    )

    def connected(_device, kind):
        if kind == "input":
            return {"name": "SSL 2+"}
        return {"name": "SSL 2+", "max_output_channels": 2}

    with mock.patch("sounddevice.query_devices", side_effect=connected):
        original = build_verification_signature(
            settings,
            app_version="1.0.0",
            engine_version="3.12.2",
        )
        settings.takes_directory = str(tmp_path / "takes-b")
        moved = build_verification_signature(
            settings,
            app_version="1.0.0",
            engine_version="3.12.2",
        )

    assert moved.recording_path_id != original.recording_path_id
    assert moved != original

    settings.takes_directory = str(tmp_path / "takes-a")

    def disconnected(_device, kind):
        if kind == "input":
            return {"name": "SSL 2+"}
        raise ValueError("device not found")

    with mock.patch("sounddevice.query_devices", side_effect=disconnected):
        unplugged = build_verification_signature(
            settings,
            app_version="1.0.0",
            engine_version="3.12.2",
        )

    assert unplugged.output_device_id == "unavailable:SSL 2+"
    assert unplugged != original


def test_signature_hashes_the_macos_jamulus_route_separately_from_portaudio(
    tmp_path: Path,
) -> None:
    settings = SimpleNamespace(
        audio_input_device_index=0,
        audio_samplerate=48_000,
        audio_blocksize=0,
        local_capture_enabled=False,
        host_server_enabled=False,
        take_playback_output_device="SSL 2+",
        takes_directory=str(tmp_path / "takes"),
        config_file=str(tmp_path / "settings.json"),
        jamulus_audio_input_uid="coreaudio-input-a",
        jamulus_audio_output_uid="coreaudio-output-a",
    )

    def device(_device, kind):
        return {
            "name": "SSL 2+",
            "max_output_channels": 2 if kind == "output" else 0,
        }

    with mock.patch("core.band_check.sys.platform", "darwin"), mock.patch(
        "sounddevice.query_devices", side_effect=device
    ), mock.patch(
        "core.coreaudio_devices.scan_coreaudio_devices",
        side_effect=RuntimeError("hardware not available to this test"),
    ):
        original = build_verification_signature(
            settings,
            app_version="1.0.0",
            engine_version="3.12.2",
        )
        settings.jamulus_audio_output_uid = "coreaudio-output-b"
        changed = build_verification_signature(
            settings,
            app_version="1.0.0",
            engine_version="3.12.2",
        )

    assert original.input_device_id == changed.input_device_id
    assert original.jamulus_route_id.startswith("coreaudio:")
    assert original.jamulus_route_id != changed.jamulus_route_id
