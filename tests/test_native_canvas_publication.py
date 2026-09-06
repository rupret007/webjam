"""Native canvas receipts prove retained state, never delivery or painting.

These tests import the room adapter but construct no Qt application, sockets,
Drawpile processes, or native transport process.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.room_state import RoomIdentity, RoomState
from core.session_transfer import SharedCanvasSessionSnapshot
from webjam_qt.controllers.room_participant import NativeRoomPublisher

_PRIVATE = "private-canvas-password"
_FIRST = "drawpile://example.com/first?p=" + _PRIVATE
_SECOND = "drawpile://example.com/second?p=" + _PRIVATE
_THIRD = "drawpile://example.com/third?p=" + _PRIVATE


class _Owner:
    def __init__(self):
        self.room_identity = RoomIdentity("canvas-room", "private-room-key")
        self.response = True
        self.on_publish = None
        self.attempts = []
        self.retained = None

    def publish_room_state(self, state):
        assert type(state) is RoomState
        self.attempts.append(state)
        response = self.response
        if response is True:
            self.retained = state
        callback, self.on_publish = self.on_publish, None
        if callback is not None:
            callback(state)
        if isinstance(response, Exception):
            raise response
        return response


@pytest.fixture
def room():
    owner = _Owner()
    app = SimpleNamespace(
        _remote_invite_owner=owner,
        creator_profile=SimpleNamespace(key="art"),
        creator_start=SimpleNamespace(key="talk_and_make"),
    )
    return NativeRoomPublisher(app, owner), app, owner


def _share(publisher, url=_FIRST, **labels):
    return publisher.publish_shared_canvas_state(
        shared=True, join_url=url, server_label=labels.get("server", "example.com"),
        session_label=labels.get("session", "Lesson"),
    )


def _attempt(publisher, *, withdrawing):
    if withdrawing:
        return publisher.publish_shared_canvas_state(shared=False)
    return _share(publisher, _SECOND)


def _video_update(publisher):
    return publisher.publish_reference_video_state(
        shared=True, state="ready", source_display_name="Lesson.mp4",
        identity_digest="a" * 64, duration_s=30.0,
    )


def test_acceptance_returns_the_exact_typed_canvas_retained_by_the_owner(room):
    publisher, _app, owner = room
    receipt = _share(publisher)

    assert type(receipt) is SharedCanvasSessionSnapshot
    assert receipt is publisher.canvas
    assert receipt is owner.retained.shared_canvas
    assert owner.retained.art_start_key == "talk_and_make"
    # No guest or launch acknowledgement is necessary for this local receipt.
    assert not hasattr(owner, "connection_available")
    assert not hasattr(receipt, "opened")


@pytest.mark.parametrize("response", [False, None, 1, "accepted", RuntimeError(_PRIVATE)])
@pytest.mark.parametrize("withdrawing", [False, True])
def test_rejection_preserves_the_prior_canvas_through_other_publications_and_retry(
    room, response, withdrawing,
):
    publisher, _app, owner = room
    first = _share(publisher)
    owner.response = response

    assert _attempt(publisher, withdrawing=withdrawing) is None
    assert publisher.canvas is first
    assert owner.retained.shared_canvas is first

    owner.response = True
    _video_update(publisher)
    assert owner.retained.shared_canvas is first
    assert owner.retained.reference_video.shared
    assert publisher.publish() is True
    assert owner.retained.shared_canvas is first

    receipt = _attempt(publisher, withdrawing=withdrawing)
    assert type(receipt) is SharedCanvasSessionSnapshot
    assert receipt is publisher.canvas is owner.retained.shared_canvas
    assert receipt.generation > first.generation
    assert receipt.shared is not withdrawing
    assert receipt.join_url == ("" if withdrawing else _SECOND)
    assert owner.retained.reference_video.shared


def test_candidate_never_temporarily_replaces_the_accepted_cache(room):
    publisher, _app, owner = room
    first = _share(publisher)
    observed = []
    owner.on_publish = lambda state: observed.append((publisher.canvas, state.shared_canvas))

    second = _share(publisher, _SECOND)

    assert observed == [(first, second)]
    assert first is not second
    assert publisher.canvas is second


@pytest.mark.parametrize("older_response", [True, False, RuntimeError(_PRIVATE)])
@pytest.mark.parametrize("withdrawing", [False, True])
def test_a_nested_newer_canvas_intent_owns_the_cache_and_the_receipt(
    room, older_response, withdrawing,
):
    publisher, _app, owner = room
    _share(publisher)
    owner.response = older_response
    nested = []

    def newer_intent(_state):
        owner.response = True
        nested.append(
            publisher.publish_shared_canvas_state(shared=False)
            if withdrawing else _share(publisher, _THIRD)
        )

    owner.on_publish = newer_intent
    assert _share(publisher, _SECOND) is None

    assert len(nested) == 1
    newest = nested[0]
    assert type(newest) is SharedCanvasSessionSnapshot
    assert publisher.canvas is newest is owner.retained.shared_canvas
    assert newest.join_url == ("" if withdrawing else _THIRD)
    attempts = owner.attempts[-2:]
    assert attempts[0].revision < attempts[1].revision
    assert attempts[0].shared_canvas.generation < attempts[1].shared_canvas.generation
    _video_update(publisher)
    assert owner.retained.shared_canvas is newest


def test_even_a_rejected_newer_intent_retires_an_older_in_flight_receipt(room):
    publisher, _app, owner = room
    first = _share(publisher)

    def rejected_newer_intent(_state):
        owner.response = False
        assert _share(publisher, _THIRD) is None

    owner.on_publish = rejected_newer_intent
    assert _share(publisher, _SECOND) is None
    assert publisher.canvas is first
    owner.response = True
    publisher.publish()
    assert owner.retained.shared_canvas is first


@pytest.mark.parametrize("retirement", ["owner", "identity", "profile"])
@pytest.mark.parametrize("during_publication", [False, True])
def test_retired_canvas_intents_never_commit_or_return_an_accepted_receipt(
    room, retirement, during_publication,
):
    publisher, app, owner = room
    first = _share(publisher)
    attempts_before = len(owner.attempts)

    def retire(_state=None):
        if retirement == "owner":
            app._remote_invite_owner = _Owner()
        elif retirement == "identity":
            owner.room_identity = RoomIdentity("another-room", "another-key")
        else:
            app.creator_profile = SimpleNamespace(key="music")

    if during_publication:
        owner.on_publish = retire
    else:
        retire()

    assert _share(publisher, _SECOND) is None
    assert publisher.canvas is first
    assert len(owner.attempts) == attempts_before + int(during_publication)


@pytest.mark.parametrize("profile", ["music", "podcast_voice", "review_rehearsal"])
def test_canvas_receipts_are_unavailable_when_full_state_would_strip_the_canvas(room, profile):
    publisher, app, owner = room
    app.creator_profile = SimpleNamespace(key=profile)

    assert _share(publisher) is None
    assert not publisher.canvas.shared
    assert owner.attempts == []
    # General room publication remains available outside Art.
    assert publisher.publish() is True
    assert owner.retained.creator_profile_key == profile
    assert not owner.retained.shared_canvas.shared


def test_mac_unicode_normalization_is_preserved_in_the_candidate_and_receipt(room):
    publisher, _app, owner = room
    receipt = _share(publisher, server="Cafe\u0301", session="Cre\u0301ation")

    assert receipt.server_label == "Café"
    assert receipt.session_label == "Création"
    assert owner.retained.shared_canvas is receipt
    assert RoomState.from_mapping(owner.retained.to_mapping()) == owner.retained


@pytest.mark.parametrize("invalid", [
    {"join_url": "https://example.com/not-a-canvas"},
    {"session_label": "x" * 1000},
])
def test_invalid_candidate_is_bounded_and_cannot_retire_the_accepted_cache(room, invalid, caplog):
    publisher, _app, owner = room
    first = _share(publisher)
    attempts_before = len(owner.attempts)
    values = dict(shared=True, join_url=_SECOND, server_label="example.com", session_label="Lesson")
    values.update(invalid)

    assert publisher.publish_shared_canvas_state(**values) is None
    assert publisher.canvas is first
    assert len(owner.attempts) == attempts_before
    assert not caplog.records


def test_private_canvas_data_never_appears_in_receipts_or_failure_diagnostics(room, caplog):
    publisher, _app, owner = room
    receipt = _share(publisher)
    owner.response = RuntimeError(_SECOND)

    assert _share(publisher, _SECOND) is None
    diagnostic = repr((receipt, publisher.canvas, owner.retained, owner.attempts))
    assert _PRIVATE not in diagnostic
    assert _FIRST not in diagnostic
    assert _SECOND not in diagnostic
    assert _PRIVATE not in caplog.text
    assert not caplog.records


@pytest.mark.parametrize("kind", ["full", "video"])
@pytest.mark.parametrize("newer_acceptance", [True, False, None])
@pytest.mark.parametrize("withdrawing", [False, True])
def test_canvas_receipt_matches_the_latest_accepted_full_room_state(
    room, kind, newer_acceptance, withdrawing,
):
    publisher, _app, owner = room
    first = _share(publisher)
    nested = []

    def newer_publication(_state):
        owner.response = newer_acceptance
        nested.append(
            publisher.publish() if kind == "full" else _video_update(publisher)
        )

    owner.on_publish = newer_publication
    receipt = _attempt(publisher, withdrawing=withdrawing)
    older, newer = owner.attempts[-2:]
    assert older.revision < newer.revision
    assert newer.shared_canvas is first
    if newer_acceptance is True:
        # The newer full state superseded this canvas candidate with A.
        assert receipt is None
        assert publisher.canvas is first is owner.retained.shared_canvas
    else:
        # A rejected full state did not replace the accepted candidate.
        assert type(receipt) is SharedCanvasSessionSnapshot
        assert receipt is publisher.canvas is owner.retained.shared_canvas
        assert receipt is older.shared_canvas
        assert receipt.shared is not withdrawing
        assert receipt.join_url == ("" if withdrawing else _SECOND)
    if kind == "full":
        assert nested == [newer_acceptance]
    else:
        assert nested[0] is publisher.video
        assert publisher.video.shared

    owner.response = True
    assert publisher.publish() is True
    assert publisher.canvas is owner.retained.shared_canvas
