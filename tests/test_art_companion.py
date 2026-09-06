"""The Art companion contract: what may cross, and who may ask for what.

These tests exist to make two promises expensive to break. A companion panel
sits inside a meeting window, which means it is both a second place Art state
is displayed and a second place commands can come from -- so the projection
must carry nothing private, and a command must never be an authority.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import fields

import pytest

from core.art_companion import (
    MAX_SEEK_POSITION_S,
    AiCompanionState,
    ArtCommand,
    ArtCommandRequest,
    ArtCommandStatus,
    ArtCompanionError,
    ArtCompanionProjection,
    ArtRejectionReason,
    ArtScope,
    CanvasCompanionState,
    VideoCompanionState,
    authorize_art_command,
)

ALL_SCOPES = tuple(ArtScope)


def _request(command: ArtCommand, projection: ArtCompanionProjection, **arguments):
    return ArtCommandRequest(
        command_id=str(uuid.uuid4()),
        command=command,
        generation=projection.generation,
        expected_revision=projection.revision,
        arguments=arguments,
    )


def _room(**overrides) -> ArtCompanionProjection:
    base = {
        "generation": 7,
        "revision": 3,
        "in_room": True,
        "canvas": CanvasCompanionState.READY,
        "video": VideoCompanionState.PAUSED,
        "transport_allowed": True,
        "ai": AiCompanionState.IDLE,
    }
    base.update(overrides)
    return ArtCompanionProjection(**base)


# ---------------------------------------------------------------------------
# Nothing private has a field to travel in
# ---------------------------------------------------------------------------


def test_the_projection_has_no_field_for_anything_private():
    """The safety property is structural, not a filter applied afterwards.

    Enumerating the fields is the whole point: if someone later adds a path,
    a file name, a canvas address, a digest, a token, or a prompt, this fails
    before the field can reach a meeting window.
    """

    assert {field.name for field in fields(ArtCompanionProjection)} == {
        "generation",
        "revision",
        "in_room",
        "canvas",
        "video",
        "transport_allowed",
        "ai",
    }


def test_the_public_dict_is_the_whole_projection_with_nothing_withheld():
    projection = _room()
    published = projection.to_public_dict()

    assert published == {
        "generation": 7,
        "revision": 3,
        "in_room": True,
        "canvas": "ready",
        "video": "paused",
        "transport_allowed": True,
        "ai": "idle",
    }
    # Every value is a primitive a JSON bridge can carry, and every state is
    # one of a finite set rather than free text from this computer.
    assert all(isinstance(value, (bool, int, str)) for value in published.values())


def test_the_projection_survives_a_json_round_trip_unchanged():
    """Whatever transport a companion track builds will serialize this, so
    the projection has to be JSON already rather than nearly-JSON."""

    projection = _room()

    restored = json.loads(json.dumps(projection.to_public_dict()))

    assert restored == projection.to_public_dict()
    assert ArtCompanionProjection(**restored) == projection


def test_a_receipt_survives_a_json_round_trip():
    room = _room()
    receipt = authorize_art_command(
        _request(ArtCommand.AI_MAKE, room), room, ALL_SCOPES
    )

    restored = json.loads(json.dumps(receipt.to_public_dict()))

    assert restored["status"] == "needs_local_confirmation"
    assert restored["command"] == "ai_make"


def test_no_projection_value_can_carry_free_text():
    """A finite enum cannot leak; a string field eventually does."""

    for state in (*CanvasCompanionState, *VideoCompanionState, *AiCompanionState):
        assert state.value.replace("_", "").isalpha()


def test_a_desktop_outside_a_room_projects_nothing_at_all():
    empty = ArtCompanionProjection(generation=4)

    assert empty.in_room is False
    assert empty.canvas is CanvasCompanionState.NONE
    assert empty.video is VideoCompanionState.NONE
    assert empty.ai is AiCompanionState.UNAVAILABLE
    assert empty.transport_allowed is False


def test_a_projection_cannot_claim_state_it_has_no_room_for():
    with pytest.raises(ArtCompanionError):
        ArtCompanionProjection(in_room=False, canvas=CanvasCompanionState.READY)
    with pytest.raises(ArtCompanionError):
        ArtCompanionProjection(in_room=False, transport_allowed=True)


def test_the_ai_state_never_claims_to_see_inside_the_generator():
    """"Handed off" is the honest word; "running" would be a guess.

    WebJam launches another program and cannot observe a job inside it, so
    the vocabulary deliberately has no state that would let a companion show
    a spinner it cannot justify.
    """

    assert "running" not in {state.value for state in AiCompanionState}
    assert AiCompanionState.HANDED_OFF.value == "handed_off"


def test_the_canvas_state_never_claims_the_painting_program_actually_opened():
    assert CanvasCompanionState.OPENING.value == "opening"
    assert "open" not in {
        state.value for state in CanvasCompanionState
    }, "'opening' is a launch; 'open' would be a claim about another program"


# ---------------------------------------------------------------------------
# Host-only transport stays host-only
# ---------------------------------------------------------------------------


TRANSPORT_COMMANDS = (
    ArtCommand.PLAY_VIDEO,
    ArtCommand.PAUSE_VIDEO,
    ArtCommand.STOP_VIDEO,
    ArtCommand.SEEK_VIDEO,
)


@pytest.mark.parametrize("command", TRANSPORT_COMMANDS)
def test_a_guests_companion_can_never_drive_the_video(command):
    """The refusal is decided by this desktop's role, not by the panel.

    A companion that lies about being the host still gets nothing, because
    the check reads ``transport_allowed`` out of the projection this desktop
    built for itself.
    """

    guest = _room(transport_allowed=False)
    arguments = {"position_s": 12} if command is ArtCommand.SEEK_VIDEO else {}

    receipt = authorize_art_command(
        _request(command, guest, **arguments), guest, ALL_SCOPES
    )

    assert receipt.status is ArtCommandStatus.REJECTED
    assert receipt.reason is ArtRejectionReason.NOT_HOST


@pytest.mark.parametrize("command", TRANSPORT_COMMANDS)
def test_a_hosts_companion_may_drive_the_video_without_a_local_prompt(command):
    """Transport moves state this desktop already owns, so it just happens."""

    host = _room(transport_allowed=True)
    arguments = {"position_s": 12} if command is ArtCommand.SEEK_VIDEO else {}

    receipt = authorize_art_command(
        _request(command, host, **arguments), host, ALL_SCOPES
    )

    assert receipt.status is ArtCommandStatus.ACCEPTED
    assert receipt.reason is ArtRejectionReason.NONE


def test_every_transport_command_requires_the_transport_scope():
    for command in TRANSPORT_COMMANDS:
        assert command.required_scope is ArtScope.TRANSPORT
        assert command.drives_host_transport is True


@pytest.mark.parametrize("video", [
    VideoCompanionState.NONE, VideoCompanionState.HOST_ATTENTION,
    VideoCompanionState.LOCAL_ATTENTION,
])
@pytest.mark.parametrize("command", TRANSPORT_COMMANDS)
def test_transport_is_refused_when_there_is_nothing_shared_to_move(video, command):
    host = _room(video=video, transport_allowed=True)

    arguments = {"position_s": 10} if command is ArtCommand.SEEK_VIDEO else {}
    receipt = authorize_art_command(
        _request(command, host, **arguments), host, ALL_SCOPES
    )

    assert receipt.reason is ArtRejectionReason.INVALID_STATE


# ---------------------------------------------------------------------------
# Starting a program on someone's computer needs their yes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    (ArtCommand.OPEN_CANVAS, ArtCommand.AI_MAKE, ArtCommand.AI_EDIT),
)
def test_anything_that_launches_a_program_waits_for_a_local_yes(command):
    """A panel in a meeting must not start software on a machine silently."""

    room = _room()

    receipt = authorize_art_command(_request(command, room), room, ALL_SCOPES)

    assert receipt.status is ArtCommandStatus.NEEDS_LOCAL_CONFIRMATION
    assert receipt.reason is ArtRejectionReason.NONE
    assert command.starts_another_program is True


def test_hiding_the_video_is_the_one_thing_that_needs_no_confirmation():
    """It changes this artist's own view and starts nothing."""

    room = _room()

    receipt = authorize_art_command(
        _request(ArtCommand.HIDE_VIDEO, room, hidden=True), room, ALL_SCOPES
    )

    assert receipt.status is ArtCommandStatus.ACCEPTED
    assert ArtCommand.HIDE_VIDEO.starts_another_program is False


def test_a_guest_may_hide_the_video_for_themselves_without_being_host():
    guest = _room(transport_allowed=False)

    receipt = authorize_art_command(
        _request(ArtCommand.HIDE_VIDEO, guest, hidden=True), guest, ALL_SCOPES
    )

    assert receipt.status is ArtCommandStatus.ACCEPTED


def test_an_image_command_cannot_name_a_file_on_someone_elses_disk():
    """Edit takes no path: the desktop opens its own picker after the yes."""

    room = _room()

    with pytest.raises(ArtCompanionError):
        ArtCommandRequest(
            command_id=str(uuid.uuid4()),
            command=ArtCommand.AI_EDIT,
            generation=room.generation,
            expected_revision=room.revision,
            arguments={"path": "/home/artist/photo.png"},
        )


def test_an_image_command_carries_no_prompt_field_to_bound_or_redact():
    """There is no prompt to leak because WebJam never takes one.

    The generator owns the prompt. That is why the contract has no prompt
    field rather than a length-capped one.
    """

    for command in (ArtCommand.AI_MAKE, ArtCommand.AI_EDIT):
        request = ArtCommandRequest(
            command_id=str(uuid.uuid4()),
            command=command,
            generation=1,
            expected_revision=0,
        )
        assert request.arguments == ()


# ---------------------------------------------------------------------------
# Scopes, staleness, and state
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("command", tuple(ArtCommand))
def test_no_command_is_honoured_without_its_scope(command):
    room = _room()
    arguments = {}
    if command is ArtCommand.SEEK_VIDEO:
        arguments = {"position_s": 1}
    elif command is ArtCommand.HIDE_VIDEO:
        arguments = {"hidden": True}
    withheld = tuple(
        scope for scope in ArtScope if scope is not command.required_scope
    )

    receipt = authorize_art_command(
        _request(command, room, **arguments), room, withheld
    )

    assert receipt.status is ArtCommandStatus.REJECTED
    assert receipt.reason is ArtRejectionReason.UNAUTHORIZED


def test_a_command_bound_to_an_older_view_is_refused():
    room = _room(revision=9)
    request = ArtCommandRequest(
        command_id=str(uuid.uuid4()),
        command=ArtCommand.OPEN_CANVAS,
        generation=room.generation,
        expected_revision=8,
    )

    receipt = authorize_art_command(request, room, ALL_SCOPES)

    assert receipt.reason is ArtRejectionReason.STALE_REVISION


def test_a_command_formed_in_a_previous_room_cannot_be_replayed():
    room = _room(generation=12)
    request = ArtCommandRequest(
        command_id=str(uuid.uuid4()),
        command=ArtCommand.OPEN_CANVAS,
        generation=11,
        expected_revision=room.revision,
    )

    receipt = authorize_art_command(request, room, ALL_SCOPES)

    assert receipt.reason is ArtRejectionReason.STALE_GENERATION


def test_nothing_is_accepted_outside_a_room():
    empty = ArtCompanionProjection(generation=2, revision=5)
    request = ArtCommandRequest(
        command_id=str(uuid.uuid4()),
        command=ArtCommand.OPEN_CANVAS,
        generation=2,
        expected_revision=5,
    )

    receipt = authorize_art_command(request, empty, ALL_SCOPES)

    assert receipt.reason is ArtRejectionReason.NOT_IN_A_ROOM


@pytest.mark.parametrize(
    "canvas",
    (
        CanvasCompanionState.NONE,
        CanvasCompanionState.MISSING_APP,
        CanvasCompanionState.UNREADABLE,
        CanvasCompanionState.SHARE_PENDING,
        CanvasCompanionState.WITHDRAW_PENDING,
    ),
)
def test_opening_a_canvas_is_refused_when_there_is_nothing_to_open(canvas):
    """Including when the painting program is missing: asking again is not
    a recovery, so the companion is told the state instead."""

    room = _room(canvas=canvas)

    receipt = authorize_art_command(
        _request(ArtCommand.OPEN_CANVAS, room), room, ALL_SCOPES
    )

    assert receipt.reason is ArtRejectionReason.INVALID_STATE


def test_an_image_command_is_refused_where_no_generator_exists():
    room = _room(ai=AiCompanionState.UNAVAILABLE)

    receipt = authorize_art_command(_request(ArtCommand.AI_MAKE, room), room, ALL_SCOPES)

    assert receipt.reason is ArtRejectionReason.INVALID_STATE


def test_a_receipt_carries_only_bounded_reasons_and_no_error_text():
    room = _room(transport_allowed=False)

    receipt = authorize_art_command(
        _request(ArtCommand.STOP_VIDEO, room), room, ALL_SCOPES
    )
    published = receipt.to_public_dict()

    assert set(published) == {"command_id", "command", "status", "reason", "revision"}
    assert published["reason"] in {reason.value for reason in ArtRejectionReason}


def test_authorization_never_touches_the_room():
    """It answers a question and performs nothing, so the same rules can be
    applied wherever a companion transport is eventually wired in."""

    room = _room()
    before = room.to_public_dict()

    authorize_art_command(_request(ArtCommand.AI_MAKE, room), room, ALL_SCOPES)

    assert room.to_public_dict() == before


# ---------------------------------------------------------------------------
# Bounded parsing
# ---------------------------------------------------------------------------


def test_a_seek_beyond_a_days_worth_of_video_is_refused():
    with pytest.raises(ArtCompanionError):
        ArtCommandRequest(
            command_id=str(uuid.uuid4()),
            command=ArtCommand.SEEK_VIDEO,
            generation=1,
            expected_revision=0,
            arguments={"position_s": MAX_SEEK_POSITION_S + 1},
        )


@pytest.mark.parametrize("position", (-1, 1.5, "12", True, None))
def test_a_seek_position_must_be_a_whole_non_negative_number(position):
    with pytest.raises(ArtCompanionError):
        ArtCommandRequest(
            command_id=str(uuid.uuid4()),
            command=ArtCommand.SEEK_VIDEO,
            generation=1,
            expected_revision=0,
            arguments={"position_s": position},
        )


@pytest.mark.parametrize("hidden", ("true", 1, None))
def test_hiding_the_video_needs_a_real_boolean(hidden):
    with pytest.raises(ArtCompanionError):
        ArtCommandRequest(
            command_id=str(uuid.uuid4()),
            command=ArtCommand.HIDE_VIDEO,
            generation=1,
            expected_revision=0,
            arguments={"hidden": hidden},
        )


def test_a_command_id_must_be_a_canonical_uuid():
    with pytest.raises(ArtCompanionError):
        ArtCommandRequest(
            command_id="not-a-uuid",
            command=ArtCommand.OPEN_CANVAS,
            generation=1,
            expected_revision=0,
        )


def test_an_unknown_command_is_refused_rather_than_guessed_at():
    with pytest.raises(ValueError):
        ArtCommandRequest(
            command_id=str(uuid.uuid4()),
            command="delete_everything",
            generation=1,
            expected_revision=0,
        )


def test_an_unknown_scope_is_refused_rather_than_ignored():
    room = _room()

    with pytest.raises(ArtCompanionError):
        authorize_art_command(
            _request(ArtCommand.OPEN_CANVAS, room), room, ("canvas", "root")
        )


def test_a_scope_string_is_not_mistaken_for_a_collection_of_scopes():
    """"canvas" must not read as the letters c, a, n, v, ..."""

    room = _room()

    with pytest.raises(ArtCompanionError):
        authorize_art_command(_request(ArtCommand.OPEN_CANVAS, room), room, "canvas")


_ARGUMENTS_FOR = {
    ArtCommand.HIDE_VIDEO: {"hidden": True},
    ArtCommand.SEEK_VIDEO: {"position_s": 1},
}


def test_every_command_has_a_scope_and_an_argument_contract():
    """A new command cannot be added without deciding both."""

    for command in ArtCommand:
        assert isinstance(command.required_scope, ArtScope)
        request = ArtCommandRequest(
            command_id=str(uuid.uuid4()),
            command=command,
            generation=1,
            expected_revision=0,
            arguments=_ARGUMENTS_FOR.get(command, {}),
        )
        assert isinstance(request.arguments, tuple)


def test_arguments_must_match_the_command_exactly():
    """Extra keys are refused rather than ignored, so a companion cannot
    smuggle a field past a command that does not expect one."""

    with pytest.raises(ArtCompanionError):
        ArtCommandRequest(
            command_id=str(uuid.uuid4()),
            command=ArtCommand.HIDE_VIDEO,
            generation=1,
            expected_revision=0,
            arguments={"hidden": True, "position_s": 1},
        )
    with pytest.raises(ArtCompanionError):
        ArtCommandRequest(
            command_id=str(uuid.uuid4()),
            command=ArtCommand.PLAY_VIDEO,
            generation=1,
            expected_revision=0,
            arguments={"position_s": 1},
        )


def test_the_commands_are_exactly_the_agreed_set():
    """The contract is closed. Widening it is a decision, not a detail."""

    assert {command.value for command in ArtCommand} == {
        "open_canvas",
        "hide_video",
        "play_video",
        "pause_video",
        "stop_video",
        "seek_video",
        "ai_make",
        "ai_edit",
    }
