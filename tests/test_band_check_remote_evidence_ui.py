"""Qt and controller wiring for compact remote Band Check evidence."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace  # noqa: E402
from unittest import mock  # noqa: E402

from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

from core.band_check import (  # noqa: E402
    BandCheckMode,
    BandCheckObservations,
    BandCheckSession,
    BandCheckStatus,
    BandCheckStep,
    BandCheckStepKey,
)
from core.session_transport import ConnectionQuality, TransportPath  # noqa: E402
from services.bridge_service import (  # noqa: E402
    JamulusRecoverySnapshot,
    JamulusRpcFreshness,
)
from webjam_qt.controllers.application_controller import (  # noqa: E402
    ApplicationController,
)
from webjam_qt.windows.ready_check import BandCheckDialog  # noqa: E402


APP = QApplication.instance() or QApplication([])


def live_session() -> BandCheckSession:
    session = BandCheckSession(
        mode=BandCheckMode.LIVE_OBSERVE,
        steps=[
            BandCheckStep(
                BandCheckStepKey.MUSIC_ENGINE,
                "Music engine",
                BandCheckStatus.PENDING,
                "Waiting",
                "Start Session",
            ),
            BandCheckStep(
                BandCheckStepKey.MUSIC_PATH,
                "Connection and music path",
                BandCheckStatus.WARNING,
                "Waiting",
                "Check Again",
                required=True,
            ),
        ],
    )
    session.apply_live_observations(
        BandCheckObservations(
            music_engine_running=True,
            music_engine_responsive=True,
            peer_connected=True,
            transport_datagrams_flowed=True,
            connection_path=TransportPath.INTERNET_DIRECT,
            connection_quality=ConnectionQuality.PLAYABLE,
            path_generation=1,
        )
    )
    return session


def open_dialog(
    session: BandCheckSession,
    observations_provider=lambda: BandCheckObservations(),
) -> BandCheckDialog:
    settings = SimpleNamespace(
        audio_input_device_index=-1,
        audio_samplerate=48_000,
        audio_blocksize=0,
        take_playback_output_device="",
        config_file="/tmp/.webjam-band-check-remote-test.json",
    )
    with mock.patch(
        "webjam_qt.windows.ready_check.build_band_check_session",
        return_value=session,
    ):
        dialog = BandCheckDialog(
            lambda: settings,
            mode=BandCheckMode.LIVE_OBSERVE,
            observations_provider=observations_provider,
        )
        dialog._live_timer.stop()
        dialog.show()
        for _ in range(40):
            APP.processEvents()
            if dialog._session is not None:
                break
    return dialog


def test_compact_music_path_action_records_only_explicit_hearing_confirmation() -> None:
    session = live_session()
    dialog = open_dialog(session)
    try:
        assert dialog._primary.text() == "We Can Hear Each Other"
        assert not session.evidence.musician_confirmed_two_way_audibility
        assert session.evidence.transport_datagrams_flowed

        dialog._run_primary_action()

        assert session.evidence.musician_confirmed_two_way_audibility
        assert session.evidence.transport_datagrams_flowed
        assert session.step(BandCheckStepKey.MUSIC_PATH).status is BandCheckStatus.PASS
        assert dialog._summary.text() == "Ready to Jam"
        assert dialog._primary.text() == "Close Band Check"
    finally:
        dialog.close()


def test_changed_path_renders_one_plain_recheck_action() -> None:
    session = live_session()
    session.confirm_two_way_audibility(True)
    session.apply_live_observations(
        BandCheckObservations(
            music_engine_running=True,
            music_engine_responsive=True,
            peer_connected=True,
            transport_datagrams_flowed=True,
            connection_path=TransportPath.SECURE_RELAY,
            connection_quality=ConnectionQuality.PLAYABLE,
            path_generation=2,
        )
    )
    dialog = open_dialog(session)
    try:
        assert dialog._primary.text() == "We Can Still Hear Each Other"
        rendered = " ".join(label.text() for label in dialog.findChildren(QLabel))
        assert rendered.count("Connection and music path") == 1
        assert "Using a secure relay" in rendered
        for protocol_word in ("ICE", "STUN", "TURN", "QUIC", "NAT"):
            assert protocol_word not in rendered
    finally:
        dialog.close()


def test_controller_maps_remote_snapshot_without_inventing_hearing() -> None:
    controller = ApplicationController.__new__(ApplicationController)
    controller.jamulus = SimpleNamespace(
        rpc_client=SimpleNamespace(
            available=True,
            last_activity_age=lambda: 0.1,
        ),
        audio_engine=SimpleNamespace(
            diagnostics=lambda: SimpleNamespace(active=True),
            get_level=lambda _channel: 0.04,
        ),
    )
    controller._RPC_HANG_THRESHOLD_S = 5
    controller._is_jamulus_running = lambda: True
    controller.participants = {1: SimpleNamespace(is_local=False, channel_id=1)}
    controller.settings = SimpleNamespace(host_server_enabled=False)
    controller.bridge = mock.Mock()
    controller.bridge.jamulus_recovery_snapshot.return_value = (
        JamulusRecoverySnapshot(
            generation=1,
            recovery_generation=0,
            launch_intended=True,
            pending=False,
            active=False,
            attempts_started=0,
            max_attempts=5,
            inflight=False,
            exhausted=False,
            next_attempt_at=0.0,
            process_id=4500,
            process_alive=True,
            rpc_freshness=JamulusRpcFreshness.FRESH,
            rpc_age_seconds=0.1,
        )
    )
    controller._local_audio_seen = True
    controller._remote_audio_seen = True
    controller._remote_session = SimpleNamespace(
        snapshot=SimpleNamespace(
            path=TransportPath.SECURE_RELAY,
            quality=ConnectionQuality.DIFFICULT,
            generation=7,
            transport_datagrams_flowed=True,
            remote_decoded_test_observed=False,
        )
    )

    observations = ApplicationController._band_check_observations(controller)

    assert observations.music_engine_running
    assert observations.music_engine_responsive
    assert observations.peer_connected
    assert observations.transport_datagrams_flowed
    assert not observations.remote_decoded_test_observed
    assert observations.musician_confirmed_two_way_audibility is None
    assert observations.connection_path is TransportPath.SECURE_RELAY
    assert observations.connection_quality is ConnectionQuality.DIFFICULT
    assert observations.path_generation == 7
