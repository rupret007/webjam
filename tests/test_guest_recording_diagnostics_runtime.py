"""Guest-owned failures travel with fresh inventory and never authorize capture."""

from __future__ import annotations

import hashlib
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from core.local_capture import LocalCapturePreflight, LocalCapturePreflightError, LocalCaptureTrack
from core.network_invite import BandInvite
from core.session_transfer_runtime import GuestPeerSession, HostPeerSession
from tests.test_session_transfer_presence_v2 import peer as _peer_fixture, _install
from tests.test_art_lan_retry import controllers as _controllers_fixture
from tests.test_art_room_controller import qapp as _qapp_fixture

peer = _peer_fixture


pytestmark = pytest.mark.requires_local_socket


def _id():
    return str(uuid.uuid4())


@pytest.fixture
def runtime(tmp_path, peer):
    credentials, registry, server, _client = peer
    digest = hashlib.sha256(b"diagnostic-roster").hexdigest()
    _install(registry, digest, 1)
    state = SimpleNamespace(code="unsupported_sample_rate", enabled=True)

    def tracks():
        if state.code:
            raise LocalCapturePreflightError(LocalCapturePreflight(
                False, (state.code,), 1, 2, (2,), 48_000,
            ))
        return (LocalCaptureTrack("PRIVATE_INPUT", (0, 1)),)

    guest = GuestPeerSession(
        BandInvite("127.0.0.1", 22124, "Test", credentials.session_id,
                   server.address[1], credentials.invite_token),
        display_name="Alex", takes_root=tmp_path / "guest",
        installation_path=tmp_path / "installation.json",
        capture_enabled=lambda: state.enabled,
        capture_config=lambda: (0, 48_000, 128), capture_tracks=tracks,
        capture_factory=Mock(side_effect=AssertionError("capture started")),
    )
    host = HostPeerSession()
    host.registry = registry
    observation = dict(
        ordered_roster_digest=digest, roster_count=1, self_ordinal=0,
        process_generation=1, rpc_connection_generation=2,
        audio_connection_generation=3,
    )
    guest.poll_once()
    return SimpleNamespace(guest=guest, host=host, registry=registry,
                           state=state, observation=observation)


def test_changed_failure_republishes_unknown_topology_and_repair_clears(runtime):
    r = runtime
    r.guest.observe_presence_v2("Alex", **r.observation)
    r.guest.poll_once()
    (first,) = r.registry.recording_presence_snapshot()
    assert first.local_original_failure_codes == ("unsupported_sample_rate",)
    assert first.local_original_track_count is None
    assert r.host.prepare_local_original_obligations(_id())[1]
    r.state.code = "input_device_or_format_unavailable"
    # No fresh roster event is required to update the diagnostic itself.
    r.guest.poll_once()
    (second,) = r.registry.recording_presence_snapshot()
    assert second.local_original_failure_codes == (r.state.code,)
    assert second.presence_generation > first.presence_generation
    assert second.local_original_track_count is None
    assert r.host.recording_local_original_diagnostics() == (second,)
    assert r.host.prepare_local_original_obligations(_id())[1]
    r.state.code = ""
    r.guest.poll_once()
    (repaired,) = r.registry.recording_presence_snapshot()
    assert repaired.local_original_failure_codes == ()
    assert repaired.local_original_required_input_channels == 0
    assert repaired.local_original_topology_exact
    assert r.host.recording_local_original_diagnostics() == ()
    assert r.host.prepare_local_original_obligations(_id())[1] == ()
    r.guest.capture_factory.assert_not_called()


def test_effective_opt_out_clears_only_after_fresh_proof(runtime):
    r = runtime
    r.guest.observe_presence_v2("Alex", **r.observation)
    r.guest.poll_once()
    (before,) = r.host.recording_local_original_diagnostics()
    r.state.enabled = False
    assert r.host.recording_local_original_diagnostics() == (before,)
    assert r.host.prepare_local_original_obligations(_id())[1]
    r.guest.poll_once()
    (after,) = r.registry.recording_presence_snapshot()
    assert after.presence_generation > before.presence_generation
    assert after.local_original_track_count == 0
    assert after.local_original_topology_exact
    assert r.host.recording_local_original_diagnostics() == ()
    assert r.host.prepare_local_original_obligations(_id())[1] == ()
    r.guest.capture_factory.assert_not_called()


@pytest.mark.parametrize("invalidate", [False, True])
def test_delayed_observation_cannot_restore_old_diagnostic(runtime, invalidate):
    r = runtime
    entered, release = threading.Event(), threading.Event()
    calls = []

    def tracks():
        older = not calls
        calls.append(True)
        if older:
            entered.set()
            assert release.wait(2)
        code = "unsupported_sample_rate" if older else "invalid_track_map"
        raise LocalCapturePreflightError(LocalCapturePreflight(
            False, (code,), 1, 2, (2,), 48_000,
        ))

    r.guest.capture_tracks = tracks
    with ThreadPoolExecutor(max_workers=1) as pool:
        old = pool.submit(r.guest.observe_presence_v2, "Alex", **r.observation)
        try:
            assert entered.wait(2)
            if invalidate:
                r.guest.invalidate_recording_presence()
            else:
                r.guest.observe_presence_v2("Alex", **r.observation)
        finally:
            release.set()
        old.result(timeout=2)
    desired = r.guest._desired_presence_v2
    if invalidate:
        assert desired is None
    else:
        assert desired.local_original_failure_codes == ("invalid_track_map",)


def test_unknown_native_failure_uses_generic_diagnostic_and_no_private_text(runtime):
    r = runtime
    r.guest.capture_tracks = Mock(side_effect=RuntimeError("/private/input token=SECRET"))
    r.guest.observe_presence_v2("Alex", **r.observation)
    r.guest.poll_once()
    (proof,) = r.host.recording_local_original_diagnostics()
    assert proof.local_original_diagnostic_version == 1
    assert proof.local_original_failure_codes == ()
    assert proof.local_original_track_count is None
    from core.guest_recording_guidance import guest_recording_failure_summary
    summary = guest_recording_failure_summary((proof,))
    assert "Alex" in summary and "could not be verified" in summary
    assert "SECRET" not in summary and "/private" not in summary
    assert r.host.prepare_local_original_obligations(_id())[1]


@pytest.mark.parametrize(("code", "expected"), [
    ("invalid_capture_settings", "audio settings are invalid"),
    ("unsupported_sample_rate", "requires 48 kHz"),
    ("invalid_block_size", "buffer size is invalid"),
    ("invalid_track_map", "track map is invalid"),
    ("insufficient_input_channels", "needs 2 input channels"),
    ("input_device_or_format_unavailable", "input is unavailable"),
])
def test_host_guidance_names_guest_and_specific_bounded_failure(runtime, code, expected):
    from core.guest_recording_guidance import guest_recording_failure_summary
    r = runtime
    r.state.code = code
    r.guest.observe_presence_v2("Alex", **r.observation)
    r.guest.poll_once()
    summary = guest_recording_failure_summary(r.host.recording_local_original_diagnostics())
    assert "Alex" in summary and expected in summary
    assert "PRIVATE_INPUT" not in summary and len(summary) <= 540


def test_duplicate_names_are_disambiguated_without_exposing_identity(runtime):
    from core.guest_recording_guidance import guest_recording_failure_summary
    r = runtime
    r.guest.observe_presence_v2("Alex", **r.observation)
    r.guest.poll_once()
    (proof,) = r.host.recording_local_original_diagnostics()
    first = replace(proof, roster_count=2)
    second = replace(first, participant_id=_id(), self_ordinal=1,
                     local_original_failure_codes=("invalid_track_map",))
    summary = guest_recording_failure_summary((first, second))
    assert "Alex (participant 1)" in summary
    assert "Alex (participant 2)" in summary
    assert "requires 48 kHz" in summary and "track map is invalid" in summary
    assert first.participant_id not in summary and second.participant_id not in summary


def test_summary_bounds_many_guests_and_redacts_private_names(runtime):
    from core.guest_recording_guidance import guest_recording_failure_summary
    r = runtime
    r.guest.observe_presence_v2("Alex", **r.observation)
    r.guest.poll_once()
    (proof,) = r.host.recording_local_original_diagnostics()
    reports = tuple(replace(
        proof, participant_id=_id(), roster_count=10, self_ordinal=index,
        display_name="/Users/private/secret.wav token=SUPERSECRET",
    ) for index in range(10))
    summary = guest_recording_failure_summary(reports)
    assert len(summary) <= 540
    assert "more guests also need attention" in summary
    assert "SUPERSECRET" not in summary and "/Users/private" not in summary


def test_host_invalidation_clears_diagnostics_without_reusing_cached_report(runtime):
    r = runtime
    r.guest.observe_presence_v2("Alex", **r.observation)
    r.guest.poll_once()
    assert r.host.recording_local_original_diagnostics()
    r.host.invalidate_recording_presence()
    assert r.host.recording_local_original_diagnostics() == ()
    r.guest.poll_once()
    assert r.host.recording_local_original_diagnostics() == ()


controllers = _controllers_fixture
qapp = _qapp_fixture


@pytest.mark.parametrize("legacy", [False, True])
def test_real_host_start_reports_guest_reason_before_any_recorder(
    runtime, controllers, monkeypatch, tmp_path, legacy,
):
    from core.recording_readiness import RecordingStorageCheck, RecordingStorageStatus
    from webjam_qt.controllers.recording_coordinator import _private_secret_file_identity
    r = runtime
    r.guest.observe_presence_v2("Alex", **r.observation)
    r.guest.poll_once()
    if legacy:
        (proof,) = r.registry.recording_presence_snapshot()
        values = asdict(proof)
        values.pop("participant_id")
        values.pop("protocol_version")
        values.update(presence_generation=proof.presence_generation + 1,
                      local_original_diagnostic_version=0,
                      local_original_failure_codes=(),
                      local_original_required_input_channels=0)
        r.guest.client.bind_presence_v2(r.guest.enrollment, **values)
    app = controllers()
    app.settings.local_capture_enabled = False
    app.settings.server_rpc_port = 43210
    secret = tmp_path / "private-server-secret"
    secret.write_text("test-secret", encoding="utf-8")
    app.settings.server_rpc_secret_file = str(secret)
    app.settings.takes_directory = str(tmp_path / "host-takes")
    (tmp_path / "host-takes").mkdir()
    prior_host = app.host_peer
    monkeypatch.setattr(r.host, "credentials", r.guest.client.credentials)
    monkeypatch.setattr(r.host, "server", object())
    app.host_peer = r.host
    participant = SimpleNamespace(channel_id=4, participant_id=r.guest.participant_id,
                                  name="Alex", role="Musician")
    readiness = SimpleNamespace(
        context=SimpleNamespace(
            server_rpc_port=43210, server_rpc_secret_file=str(secret),
            server_rpc_secret_identity=_private_secret_file_identity(str(secret)),
        ),
        musician_ids_by_channel=((4, r.guest.participant_id),),
        reference_channels=(), channel_counts_by_channel=((4, 2),),
    )
    monkeypatch.setattr(
        "webjam_qt.controllers.recording_coordinator.check_recording_storage",
        lambda *args, **kwargs: RecordingStorageCheck(RecordingStorageStatus.READY, "Ready"),
    )
    message = Mock()
    start = Mock(side_effect=AssertionError("recorder started"))
    arm = Mock(side_effect=AssertionError("guest armed"))
    monkeypatch.setattr(app, "_show_actionable_error", message)
    monkeypatch.setattr(app.recording, "_continue_recording_start", start)
    monkeypatch.setattr(app.recording, "_arm_guest_capture_before_server_start", arm)
    try:
        app.recording._begin_recording_start([participant], str(secret), hosted_readiness=readiness)
        assert message.call_count == 1
        assert message.call_args.args[0] == "Guest Recording Plan Needs Attention"
        visible = message.call_args.kwargs["what_failed"]
        if legacy:
            assert "couldn't prove" in visible
            assert "Alex" not in visible and "48 kHz" not in visible
        else:
            assert "Alex" in visible and "requires 48 kHz" in visible
        assert "No recorder was started" in visible
        assert "Recording Setup" in message.call_args.kwargs["next_action"]
        assert not app.recording._take_id
        start.assert_not_called()
        arm.assert_not_called()
        r.guest.capture_factory.assert_not_called()
    finally:
        app.host_peer = prior_host
