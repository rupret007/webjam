"""Room membership must not depend on, or manufacture, Music readiness."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from core.network_invite import BandInvite
from core.session_conductor import (
    ArtRoomState,
    CleanupState,
    EvidenceState,
    MusicPathState,
    ExportState,
    ReviewState,
    RecorderState,
    TakeValidationState,
    FailureDisposition,
    SessionConductorPhase,
    SessionFacts,
    SessionPrimaryAction,
    SessionRole,
    derive_session_presentation,
)
from core.session_transfer import (
    SessionCredentials,
    SessionStateSnapshot,
    RecordingSignal,
    SessionTransferError,
)
from services.lan_room_guest import LanRoomGuest


@pytest.mark.parametrize("role", [SessionRole.HOST, SessionRole.GUEST])
def test_art_room_can_connect_with_no_audio_evidence(role):
    facts = SessionFacts(
        role=role,
        creator_profile_key="art",
        setup_requested=True,
        art_room=ArtRoomState.CONNECTED,
    )
    view = derive_session_presentation(facts)
    assert view.phase is SessionConductorPhase.CONNECTED
    assert view.title == "You’re in"
    assert view.primary_action is SessionPrimaryAction.NONE
    assert facts.music_path is MusicPathState.NOT_STARTED
    assert facts.local_participant is EvidenceState.NOT_STARTED
    assert facts.human_two_way_audibility is EvidenceState.NOT_STARTED
    assert "Jamulus" not in view.message


def test_room_membership_does_not_make_music_connected():
    facts = SessionFacts(creator_profile_key="music", art_room=ArtRoomState.CONNECTED)
    assert derive_session_presentation(facts).phase is SessionConductorPhase.IDLE


@pytest.mark.parametrize(
    "state,phase,action",
    [
        (
            ArtRoomState.STARTING,
            SessionConductorPhase.JOINING,
            SessionPrimaryAction.WAIT,
        ),
        (
            ArtRoomState.WAITING,
            SessionConductorPhase.JOINING,
            SessionPrimaryAction.WAIT,
        ),
        (
            ArtRoomState.RECONNECTING,
            SessionConductorPhase.RECONNECTING,
            SessionPrimaryAction.WAIT,
        ),
        (
            ArtRoomState.FAILED,
            SessionConductorPhase.FAILED,
            SessionPrimaryAction.PASTE_NEW_INVITE,
        ),
    ],
)
def test_art_participant_has_truthful_next_action(state, phase, action):
    facts = SessionFacts(
        role=SessionRole.GUEST,
        creator_profile_key="art",
        setup_requested=True,
        art_room=state,
    )
    view = derive_session_presentation(facts)
    assert view.phase is phase
    assert view.primary_action is action
    assert "mute" not in view.message.lower()


def test_art_cleanup_outranks_late_connection():
    facts = SessionFacts(
        creator_profile_key="art", setup_requested=True, art_room=ArtRoomState.CONNECTED
    )
    assert (
        derive_session_presentation(replace(facts, cleanup=CleanupState.ENDING)).phase
        is SessionConductorPhase.ENDING
    )
    assert (
        derive_session_presentation(replace(facts, cleanup=CleanupState.COMPLETE)).phase
        is SessionConductorPhase.ENDED
    )
    assert (
        derive_session_presentation(replace(facts, cleanup=CleanupState.UNKNOWN)).phase
        is SessionConductorPhase.INDETERMINATE
    )


def _guest(monkeypatch, clock, received):
    credentials = SessionCredentials.create()
    invite = BandInvite(
        "192.168.1.20",
        session_id=credentials.session_id,
        peer_port=22125,
        invite_token=credentials.invite_token,
    )
    monkeypatch.setattr(
        "services.lan_room_guest.SessionPeerClient",
        lambda *a, **kw: SimpleNamespace(
            enroll=lambda *a: object(),
            state=lambda *a: SessionStateSnapshot(
                credentials.session_id,
                0,
                RecordingSignal.IDLE,
                creator_profile_key="art",
            ),
        ),
    )
    return LanRoomGuest(
        invite,
        display_name="Artist",
        on_state=lambda owner, state: received.append((owner, state)),
        on_loss=lambda *a: None,
        clock=lambda: clock[0],
    )


def test_first_idle_art_snapshot_is_delivered_and_freshness_expires(
    monkeypatch, tmp_path
):
    monkeypatch.chdir(tmp_path)
    clock, received = [100.0], []
    guest = _guest(monkeypatch, clock, received)
    assert not guest.connection_available
    state = guest.poll_once()
    assert received == [(guest, state)]
    assert guest.connection_available
    clock[0] += 5
    assert not guest.connection_available
    guest.poll_once()
    assert guest.connection_available
    assert len(received) == 2
    assert list(tmp_path.iterdir()) == []
    assert guest.stop()
    assert guest.poll_once() is None
    assert not guest.connection_available
    assert guest.last_state is None


def test_stop_during_receipt_cannot_deliver_stale_state(monkeypatch):
    guest = _guest(monkeypatch, [100.0], [])
    received = []
    guest._on_state = lambda *args: received.append(args)

    def late_state(_):
        guest.stop()
        return SimpleNamespace(creator_profile_key="art")

    guest.client.state = late_state
    assert guest.poll_once() is None
    assert received == []
    assert not guest.connection_available


def test_room_reader_rejects_public_lan_plane(monkeypatch):
    credentials = SessionCredentials.create()
    with pytest.raises(ValueError):
        LanRoomGuest(
            BandInvite(
                "8.8.8.8",
                session_id=credentials.session_id,
                peer_port=22125,
                invite_token=credentials.invite_token,
            ),
            display_name="Artist",
            on_state=lambda *a: None,
            on_loss=lambda *a: None,
        )


@pytest.mark.requires_local_socket
def test_host_counts_only_fresh_authenticated_room_readers(tmp_path, monkeypatch):
    import uuid
    from core.session_transfer import (
        EnrollmentRegistry,
        SessionControlState,
        SessionPeerClient,
        SessionPeerServer,
        TransferAuthenticationError,
        TransferStore,
    )

    credentials = SessionCredentials.create()
    root = tmp_path / "host"
    server = SessionPeerServer(
        "127.0.0.1",
        0,
        registry=EnrollmentRegistry(root, credentials),
        control=SessionControlState(
            root, credentials.session_id, creator_profile_key="art"
        ),
        transfers=TransferStore(root, credentials.session_id),
    )
    clock = [100.0]
    monkeypatch.setattr(server, "_room_poll_clock", lambda: clock[0])
    server.start()
    try:
        client = SessionPeerClient(*server.address, credentials=credentials)
        enrolled = client.enroll(str(uuid.uuid4()), "Artist")
        assert server.room_participants() == frozenset()
        assert client.state(enrolled).creator_profile_key == "art"
        assert server.room_participants() == frozenset({enrolled.participant_id})
        clock[0] += 5.0
        assert server.room_participants() == frozenset()
        invalid = replace(
            enrolled,
            participant_token=SessionCredentials.create().participant_token(
                enrolled.participant_id
            ),
        )
        with pytest.raises(TransferAuthenticationError):
            client.state(invalid)
        assert server.room_participants() == frozenset()
        client.state(enrolled)
        assert server.room_participants() == frozenset({enrolled.participant_id})
    finally:
        server.stop()
    assert server.room_participants() == frozenset()


@pytest.mark.parametrize(
    "changes,phase",
    [
        ({"setup_requested": False}, SessionConductorPhase.IDLE),
        ({"startup_cleanup_pending": True}, SessionConductorPhase.INDETERMINATE),
        (
            {"failure": FailureDisposition.INDETERMINATE},
            SessionConductorPhase.INDETERMINATE,
        ),
        ({"failure": FailureDisposition.RETRYABLE}, SessionConductorPhase.FAILED),
        ({"recorder": RecorderState.RECORDING}, SessionConductorPhase.RECORDING),
        ({"recorder": RecorderState.FAILED}, SessionConductorPhase.FAILED),
        (
            {"take_validation": TakeValidationState.NEEDS_ATTENTION},
            SessionConductorPhase.TAKE_NEEDS_ATTENTION,
        ),
        ({"export": ExportState.EXPORTING}, SessionConductorPhase.EXPORTING),
        ({"export": ExportState.FAILED}, SessionConductorPhase.TAKE_NEEDS_ATTENTION),
        ({"studio": ReviewState.REVIEWING}, SessionConductorPhase.REVIEWING),
    ],
)
def test_late_room_connection_cannot_replace_cleanup_or_local_work(changes, phase):
    facts = SessionFacts(
        creator_profile_key="art", setup_requested=True, art_room=ArtRoomState.CONNECTED
    )
    view = derive_session_presentation(replace(facts, **changes))
    assert view.phase is phase
    assert view.title != "You’re in"
    if changes.get("startup_cleanup_pending"):
        assert view.primary_action is SessionPrimaryAction.CLOSE_SETUP


def test_host_waiting_can_share_while_guest_waiting_cannot():
    facts = SessionFacts(
        creator_profile_key="art",
        setup_requested=True,
        role=SessionRole.HOST,
        art_room=ArtRoomState.WAITING,
    )
    assert (
        derive_session_presentation(facts).primary_action
        is SessionPrimaryAction.COPY_INVITE
    )
    guest = derive_session_presentation(replace(facts, role=SessionRole.GUEST))
    assert guest.primary_action is SessionPrimaryAction.WAIT
    assert guest.title == "Waiting for the host"


def test_foreign_room_cannot_refresh_or_publish_guest_state(monkeypatch):
    received = []
    guest = _guest(monkeypatch, [100.0], received)
    foreign = SessionStateSnapshot(
        SessionCredentials.create().session_id, 0, RecordingSignal.IDLE
    )
    guest.client.state = lambda _: foreign
    with pytest.raises(SessionTransferError, match="different room"):
        guest.poll_once()
    assert received == []
    assert not guest.connection_available
    assert guest.last_state is None


def test_stop_during_enrollment_does_not_start_a_state_request(monkeypatch):
    guest = _guest(monkeypatch, [100.0], [])
    requests = []

    def enroll(*args):
        guest.stop()
        return object()

    guest.client.enroll = enroll
    guest.client.state = lambda _: requests.append("state")
    assert guest.poll_once() is None
    assert requests == []
