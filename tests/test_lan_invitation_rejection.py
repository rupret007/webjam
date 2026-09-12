"""A rejected LAN invitation needs a new paste, not a replayed credential."""

from collections import deque
from dataclasses import asdict
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from core.network_invite import BandInvite
from core.session_conductor import ArtRoomState, SessionPrimaryAction
from core.session_transfer import (
    ParticipantEnrollment,
    RecordingSignal,
    SessionCredentials,
    SessionStateSnapshot,
)
from services.lan_room_guest import LanRoomGuest
from webjam_qt.controllers.room_participant import RoomParticipantController


_PRIVATE_ERROR = "Private server detail must not appear in guidance or logs."


class _HttpScript:
    """Keep the actual HTTP client parser; replace only its socket boundary."""

    def __init__(self):
        self.replies = deque()
        self.requests = []
        self.closed = 0

    def connect(self, *args, **kwargs):
        script = self

        class Connection:
            def request(self, method, path, **kwargs):
                # Never retain a request header or body carrying credentials.
                script.requests.append((method, path))

            def getresponse(self):
                assert script.replies, "unexpected extra HTTP request"
                response = script.replies.popleft()
                if isinstance(response, Exception):
                    raise response
                status, payload = response
                return SimpleNamespace(
                    status=status,
                    read=lambda maximum: json.dumps(payload).encode("utf-8"),
                )

            def close(self):
                script.closed += 1

        return Connection()


@pytest.fixture
def room_rig(monkeypatch):
    script = _HttpScript()
    monkeypatch.setattr("core.session_transfer.http.client.HTTPConnection", script.connect)
    app = SimpleNamespace(
        _shutdown=False,
        audio=SimpleNamespace(stopping=False, cleanup_retry_required=False),
        _remote_invite_owner=None, _remote_session=None, _remote_invitation=None,
        _startup_attempt=None, guest_peer=None, recording=SimpleNamespace(),
        _refresh_readiness=Mock(), _apply_creator_profile_key=Mock(),
    )
    room = RoomParticipantController(app)
    room.observe_creative_state = Mock()
    room.generation = 1
    room.role = "guest"
    room.probing = True
    clock = [100.0]
    credentials = SessionCredentials.create()
    invite = BandInvite(
        "192.168.1.42", session_id=credentials.session_id,
        peer_port=43121, invite_token=credentials.invite_token,
    )
    losses = []

    def on_loss(owner, terminal):
        losses.append((terminal, owner.connection_available))
        room.lose_lan(owner, 1, terminal)

    owner = LanRoomGuest(
        invite, display_name="Test artist", clock=lambda: clock[0],
        on_state=lambda source, state: room.receive_lan(source, 1, state),
        on_loss=on_loss,
    )
    owner._started_at = clock[0]
    room.lan_guest = owner
    state = SessionStateSnapshot(invite.session_id, 0, RecordingSignal.IDLE,
                                 creator_profile_key="art")
    enrollment = ParticipantEnrollment(
        "11111111-1111-4111-8111-111111111111",
        owner._installation_id, "Test artist", "a" * 43,
    )
    waits = []

    def stop_on_wait(delay):
        # A missed immediate-terminal branch must fail deterministically,
        # never poll a real endpoint or wait out the 30-second policy.
        waits.append(delay)
        owner._stop.set()
        return True

    monkeypatch.setattr(owner._stop, "wait", stop_on_wait)
    rig = SimpleNamespace(
        app=app, room=room, owner=owner, clock=clock, script=script,
        state=state, enrollment=enrollment, losses=losses, waits=waits,
    )
    yield rig
    assert owner.stop()


def _connect(rig):
    rig.script.replies.extend([
        (200, asdict(rig.enrollment)),
        (200, {
            "session_id": rig.state.session_id, "generation": rig.state.generation,
            "signal": rig.state.signal.value, "creator_profile_key": "art",
        }),
    ])
    assert rig.owner.poll_once() == rig.state
    assert rig.owner.connection_available
    assert rig.room.state is ArtRoomState.CONNECTED


@pytest.mark.parametrize("stage", ["enrollment", "state"])
def test_actual_client_401_retires_observer_immediately(room_rig, caplog, stage):
    rig = room_rig
    if stage == "state":
        _connect(rig)
    rig.script.replies.append((401, {"message": _PRIVATE_ERROR}))
    rig.owner._run()

    assert rig.waits == []
    assert rig.losses == [(True, False)]
    assert rig.owner._stop.is_set()
    assert rig.owner._enrollment is None
    assert rig.owner.last_state is None
    assert not rig.owner.connection_available
    assert rig.owner.poll_once() is None
    assert not rig.script.replies
    assert rig.script.closed == len(rig.script.requests)
    assert _PRIVATE_ERROR not in caplog.text
    from services.lan_room_guest import LanRoomTerminalReason

    assert rig.owner.terminal_reason is LanRoomTerminalReason.INVITATION_REJECTED
    assert rig.room.lan_invitation_rejected


@pytest.mark.parametrize("connected", [False, True])
@pytest.mark.parametrize("late_profile", ["art", "music"])
def test_rejected_invite_gets_fresh_paste_and_late_receipts_cannot_revive(
    room_rig, connected, late_profile,
):
    rig = room_rig
    if connected:
        _connect(rig)
    # An old-enough clock also reaches the old code's terminal path, exposing
    # its incorrect same-invitation retry independently of immediate timing.
    rig.clock[0] += 31
    rig.script.replies.append((401, {"message": _PRIVATE_ERROR}))
    rig.owner._run()
    assert not rig.room.can_retry_lan
    assert not rig.room.retry_lan_guest()
    guidance = rig.room.guidance()
    assert guidance.primary_action is SessionPrimaryAction.PASTE_NEW_INVITE
    assert guidance.action_label == "Paste New Invite"
    assert "fresh invitation" in guidance.message
    assert _PRIVATE_ERROR not in str(guidance)
    assert rig.room.lan_failed
    assert rig.room.state is ArtRoomState.FAILED

    rig.app._apply_creator_profile_key.reset_mock()
    rig.room.observe_creative_state.reset_mock()
    late_state = SessionStateSnapshot(
        rig.state.session_id, 1, RecordingSignal.IDLE,
        creator_profile_key=late_profile,
    )
    rig.room.receive_lan(rig.owner, 1, late_state)
    rig.room.lose_lan(rig.owner, 1, False)
    assert rig.room.lan_failed
    assert rig.room.state is ArtRoomState.FAILED
    assert rig.room.guidance().primary_action is SessionPrimaryAction.PASTE_NEW_INVITE
    rig.app._apply_creator_profile_key.assert_not_called()
    rig.room.observe_creative_state.assert_not_called()


@pytest.mark.parametrize("status", [401, 503])
def test_replaced_observer_cannot_change_new_room(room_rig, status):
    rig = room_rig
    rig.clock[0] += 31
    rig.script.replies.append((status, {"message": _PRIVATE_ERROR}))
    replacement = SimpleNamespace(terminal_reason=None)
    rig.room.lan_guest = replacement
    rig.room.generation += 1
    rig.room.state = ArtRoomState.CONNECTED
    rig.room.probing = False
    rig.owner._run()
    rig.room.receive_lan(rig.owner, 1, rig.state)
    assert rig.room.lan_guest is replacement
    assert rig.room.state is ArtRoomState.CONNECTED
    assert not rig.room.lan_failed
    assert rig.room.guidance() is None


@pytest.mark.parametrize("error", [OSError("unavailable"), (503, {"message": _PRIVATE_ERROR})])
def test_transient_connection_failure_keeps_existing_retry_policy(room_rig, error):
    rig = room_rig
    rig.script.replies.append(error)
    rig.owner._run()
    assert rig.losses == [(False, False)]
    assert rig.waits == [0.5]
    assert not rig.room.lan_failed
    assert not rig.room.can_retry_lan
    assert rig.room.guidance().primary_action is SessionPrimaryAction.WAIT


@pytest.mark.parametrize("connected", [False, True])
def test_terminal_unavailable_host_still_allows_same_invite_retry(room_rig, connected):
    rig = room_rig
    if connected:
        _connect(rig)
    rig.clock[0] += 31
    rig.script.replies.append(OSError("unavailable"))
    rig.owner._run()
    assert rig.room.can_retry_lan
    assert rig.room.guidance().primary_action is SessionPrimaryAction.RETRY_SETUP
    assert rig.room.guidance().action_label == "Try Again"
    assert rig.owner.invite.peer_enabled


def test_explicit_stop_wins_before_pending_401_is_delivered(room_rig, monkeypatch):
    rig = room_rig
    original = rig.script.connect

    def connect(*args, **kwargs):
        connection = original(*args, **kwargs)
        getresponse = connection.getresponse

        def stopped_response():
            assert rig.owner.stop()
            return getresponse()

        connection.getresponse = stopped_response
        return connection

    monkeypatch.setattr("core.session_transfer.http.client.HTTPConnection", connect)
    rig.script.replies.append((401, {"message": _PRIVATE_ERROR}))
    rig.owner._run()
    assert rig.losses == []
    assert not rig.room.lan_failed
