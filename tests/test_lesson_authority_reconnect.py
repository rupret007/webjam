"""Room ownership survives temporary observations; no sockets or live media."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from core.lesson_request import LessonRequestCommand, LessonRequestStore
from core.session_conductor import ArtRoomState
from services.lan_room_guest import LessonRequestGuestState
from tests.test_lan_lesson_request_worker import rig as _worker_rig_fixture
from webjam_qt.controllers.room_participant import RoomParticipantController, RoomShareReadiness

worker_rig = _worker_rig_fixture


@pytest.fixture
def rig():
    clock = [100.0]
    store = LessonRequestStore(clock=lambda: clock[0])
    participant = "10000000-0000-4000-8000-000000000001"
    server = SimpleNamespace(
        activate_lesson_requests=store.activate,
        retire_lesson_requests=Mock(side_effect=store.retire),
        lesson_request_notices=store.host_notices,
        lesson_request_names=Mock(return_value={participant: "Artist"}),
        acknowledge_lesson_request=store.acknowledge,
        room_participants=lambda: frozenset({participant}),
    )
    host = SimpleNamespace(active=True, server=server, _lifecycle_generation=1)
    panel = Mock(_shared_lesson_hosting=True)
    app = SimpleNamespace(
        creator_profile=SimpleNamespace(key="art"), host_peer=host,
        window=SimpleNamespace(webex_embed=panel),
        _remote_session=None, _remote_invite_owner=None, _reference_video=object(),
        _session_meeting_generation=1, _shutdown=False,
        audio=SimpleNamespace(stopping=False, cleanup_retry_required=False),
        _update_session_hud=Mock(), _refresh_readiness=Mock(),
        _apply_creator_profile_key=Mock(),
    )
    room = RoomParticipantController(app)
    room.role, room.state = "host", ArtRoomState.CONNECTED
    room.readiness = Mock(return_value=RoomShareReadiness("192.168.1.20", True))
    room.activate_lesson_requests(hosting=True)
    view = store.record_state_read(participant, read_at=clock[0])
    store.submit(participant, LessonRequestCommand(view.context_id, view.admission_id, 1, "pause"))
    room.project_lesson_requests()
    panel.reset_mock()
    return SimpleNamespace(room=room, app=app, panel=panel, host=host, server=server,
                           store=store, clock=clock, participant=participant)


@pytest.mark.parametrize("interruption", ["route", "missing_snapshot", "aged_snapshot", "missing_names"])
def test_binding_and_accepted_notice_survive_observation_loss(rig, interruption):
    room, panel = rig.room, rig.panel
    binding, notices = room._lesson_binding, rig.store.host_notices()
    if interruption == "route":
        room.readiness.return_value = RoomShareReadiness()
        room.tick()
        assert room.state is ArtRoomState.RECONNECTING
    elif interruption == "missing_snapshot":
        room._host_names_owner = None
    elif interruption == "aged_snapshot":
        room._host_names_observed_at -= 6
    else:
        rig.server.lesson_request_names.return_value = None
    room.project_lesson_requests()
    assert room._lesson_binding is binding
    rig.server.retire_lesson_requests.assert_not_called()
    assert rig.store.host_notices() == notices
    panel.set_lesson_request_host.assert_called_with(())
    panel.lesson_handoff.restart_button.setVisible.assert_not_called()
    room.readiness.return_value = RoomShareReadiness("192.168.1.20", True)
    rig.server.lesson_request_names.return_value = {rig.participant: "Artist"}
    room.tick()
    assert room._lesson_binding is binding
    panel.set_lesson_request_host.assert_called_with((("Artist", notices[0]),))
    panel.lesson_handoff.restart_button.hide.assert_called()


def test_names_losing_freshness_during_read_are_hidden_without_retiring_lesson(rig):
    binding = rig.room._lesson_binding

    def lose_observation(_participants):
        rig.room._host_names_owner = None
        return {rig.participant: "Artist"}

    rig.server.lesson_request_names.side_effect = lose_observation
    rig.room.project_lesson_requests()
    assert rig.room._lesson_binding is binding
    rig.server.retire_lesson_requests.assert_not_called()
    rig.panel.set_lesson_request_host.assert_called_once_with(())


@pytest.mark.parametrize("boundary", ["generation", "profile", "blocked", "role", "owner", "server", "lifecycle", "end"])
def test_host_authority_still_retires_at_real_boundaries(rig, boundary):
    room = rig.room
    binding = room._lesson_binding
    if boundary == "generation":
        room.generation += 1
    elif boundary == "profile":
        rig.app.creator_profile.key = "music"
    elif boundary == "blocked":
        room.stopping = True
    elif boundary == "role":
        room.role = "guest"
    elif boundary == "owner":
        rig.app.host_peer = SimpleNamespace(server=None)
    elif boundary == "server":
        rig.host.server = None
    elif boundary == "lifecycle":
        rig.host._lifecycle_generation += 1
    else:
        rig.host.active = False
    room.project_lesson_requests()
    assert room._lesson_binding is None
    assert not room._lesson_current(binding)
    assert rig.store.host_notices() == ()
    rig.server.retire_lesson_requests.assert_called_once()
    assert room._lesson_slots == []


def guest_rig(rig):
    room = rig.room
    room.retire_lesson_requests()
    rig.panel._shared_lesson_hosting = False
    room.role = "guest"
    owner = SimpleNamespace(
        connection_available=True, terminal_reason=None,
        set_lesson_requests_enabled=Mock(),
        lesson_request_state=LessonRequestGuestState(), stop=Mock(return_value=True),
    )
    room.lan_guest = owner
    room.activate_lesson_requests(hosting=False)
    owner.set_lesson_requests_enabled.reset_mock()
    return owner


def test_guest_transient_loss_keeps_binding_and_resumes_only_after_current_receipt(rig):
    owner = guest_rig(rig)
    room = rig.room
    binding = room._lesson_binding
    owner.connection_available = False
    owner.lesson_request_state = LessonRequestGuestState(error="unavailable")
    room.lose_lan(owner, room.generation, False)
    room.project_lesson_requests()
    assert room.state is ArtRoomState.RECONNECTING
    assert room._lesson_binding is binding
    owner.set_lesson_requests_enabled.assert_not_called()
    assert not room._lesson_available(binding)
    assert rig.panel.set_lesson_request_guest.call_args.kwargs["can_pause"] is False
    owner.connection_available = True
    room.receive_lan(owner, room.generation, SimpleNamespace(creator_profile_key="art"))
    owner.set_lesson_requests_enabled.assert_called_once_with(True)
    assert room._lesson_binding is binding
    assert room.state is ArtRoomState.CONNECTED


@pytest.mark.parametrize("boundary", ["generation", "profile", "blocked", "owner", "terminal", "leave"])
def test_guest_authority_still_retires_at_real_boundaries(rig, boundary):
    owner = guest_rig(rig)
    room = rig.room
    binding = room._lesson_binding
    if boundary == "generation":
        room.generation += 1
    elif boundary == "profile":
        rig.app.creator_profile.key = "music"
    elif boundary == "blocked":
        room.stopping = True
    elif boundary == "owner":
        room.lan_guest = None
    elif boundary == "terminal":
        room.lose_lan(owner, room.generation, True)
    else:
        assert room.stop_lan()
    room.project_lesson_requests()
    assert room._lesson_binding is None
    assert not room._lesson_current(binding)
    owner.set_lesson_requests_enabled.assert_called_once_with(False)
    assert room._lesson_slots == []


@pytest.mark.parametrize("error", ["unsupported", "context_stale", ""])
def test_room_receipt_does_not_reenable_other_retired_worker_states(rig, error):
    owner = guest_rig(rig)
    owner.lesson_request_state = LessonRequestGuestState(error=error)
    rig.room.receive_lan(owner, rig.room.generation, SimpleNamespace(creator_profile_key="art"))
    owner.set_lesson_requests_enabled.assert_not_called()


def test_real_guest_worker_recovers_evidence_without_replaying_request(rig, worker_rig):
    room, owner = rig.room, worker_rig.owner
    room.retire_lesson_requests()
    room.role = "guest"
    rig.panel._shared_lesson_hosting = False
    room.lan_guest = owner
    owner._on_state = lambda source, state: room.receive_lan(source, room.generation, state)
    room.observe_creative_state = Mock()
    owner.poll_once()
    room.activate_lesson_requests(hosting=False)
    owner.poll_once()
    binding = room._lesson_binding
    assert owner.queue_lesson_request("pause")
    worker_rig.peer.get_hook = Mock(side_effect=OSError("offline"))
    with pytest.raises(OSError, match="offline"):
        owner.poll_once()
    room.lose_lan(owner, room.generation, False)
    room.project_lesson_requests()
    assert room._lesson_binding is binding
    assert not owner.lesson_request_state.can_submit
    assert len(worker_rig.peer.commands) == 1
    worker_rig.peer.get_hook = None
    owner.poll_once()
    owner.poll_once()
    assert room._lesson_binding is binding
    assert owner.lesson_request_state.can_submit
    assert owner.lesson_request_state.status == "accepted"
    assert not owner.lesson_request_state.can_retry
    assert len(worker_rig.peer.commands) == 1
