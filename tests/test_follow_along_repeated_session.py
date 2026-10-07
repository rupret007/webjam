"""End then start a new Art lesson session in the same controller process."""

from __future__ import annotations

import logging
import sys
from unittest.mock import Mock

import pytest

from core.art_workspace import make_reference
from core.lesson_request import LessonRequestCommand, LessonRequestError
from core.session_conductor import ArtRoomState
from tests.support.follow_along_lesson_rig import LESSON, pause_guest
from tests.test_art_room_controller import drain
from tests.test_art_shared_lesson_journey import (
    qapp as _qapp_fixture,
    room as _room_fixture,
)

pytest_plugins = ("tests.support.follow_along_lesson_rig",)

qapp = _qapp_fixture
room = _room_fixture
pytestmark = pytest.mark.requires_local_socket


@pytest.fixture(autouse=True)
def no_swallowed_ui_errors(monkeypatch, caplog):
    errors = []
    monkeypatch.setattr(sys, "excepthook", lambda *args: errors.append(args[0].__name__))
    yield
    assert errors == []
    assert not [record for record in caplog.records if record.levelno >= logging.ERROR]


def _end_art_session(app, qapp, *, hosting):
    app.audio._begin_session_stop(hosting, art_room=True)
    drain(qapp, lambda: not app.audio.stopping)
    assert not app.audio.cleanup_retry_required


def _restart_host_session(rig, qapp):
    host = rig.host.app
    host.host_peer.start("192.168.1.20", creator_profile_key="art")
    host.host_peer.server = rig.server
    host.host_peer.active = True
    host.begin_startup_journey()
    drain(qapp, lambda: host._room_participant.state is ArtRoomState.CONNECTED)


def test_second_session_rejects_old_requests_and_reuses_saved_lesson(lesson_pair, qapp, monkeypatch):
    rig = lesson_pair
    rig.enter(rig.host, choose=True)
    rig.enter(rig.guests[0])
    rig.poll()
    notice = pause_guest(rig, 0, qapp)
    participant_id = notice.participant_id
    binding = rig.host.app._room_participant._lesson_binding
    old_generation = rig.host.app.follow_along.panel.generation
    old_context = notice.context_id
    reference = make_reference(LESSON, kind="url", title="Saved for next session")
    record = rig.host.app.session_library.library.create(
        "art", "Next session art", art={"version": 1, "references": [reference]},
    )
    rig.host.app.session_library.continue_record(record)
    meeting = Mock()
    monkeypatch.setattr("webex_integration.open_webex_meeting", meeting)
    browser = Mock()
    monkeypatch.setattr("PySide6.QtGui.QDesktopServices.openUrl", browser)

    _end_art_session(rig.guests[0].app, qapp, hosting=False)
    _end_art_session(rig.host.app, qapp, hosting=True)
    assert rig.server.lesson_request_notices() == ()
    assert not rig.host.app._room_participant.acknowledge_lesson_request(
        binding, notice.context_id, notice.admission_id, notice.revision,
    )

    _restart_host_session(rig, qapp)
    rig.poll()
    rig.enter(rig.host, choose=False)
    rig.poll()
    new_context = rig.host.app._room_participant._lesson_binding.context_id
    assert new_context != old_context
    with pytest.raises(LessonRequestError, match="context_stale"):
        rig.server._submit_lesson_request(
            participant_id,
            LessonRequestCommand(old_context, notice.admission_id, notice.revision, "pause"),
        )
    assert rig.guests[0].app._room_participant.lan_guest is None
    assert not rig.host.app.follow_along._allowed(old_generation)
    meeting.assert_not_called()
    browser.assert_not_called()

    assert rig.host.app.follow_along.use_saved_lesson(LESSON)
    assert rig.host.app.follow_along.panel.lesson_url
    assert "Nothing has opened" in rig.host.app.follow_along.panel.status.text()
    meeting.assert_not_called()
    browser.assert_not_called()
    assert rig.host.app.session_library.library.load(record.id).art["references"] == [reference]
