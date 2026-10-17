"""LAN admission projects the host's Art start before any video is shared."""

from dataclasses import replace

import pytest

from core.session_conductor import ArtRoomState
from tests.test_art_room_controller import (
    arm_lan,
    controllers as _controllers_fixture,
    drain,
    invitation,
    qapp as _qapp_fixture,
    state,
)

controllers = _controllers_fixture
qapp = _qapp_fixture


@pytest.mark.parametrize("profile", ["music", "art"])
@pytest.mark.parametrize("size", [(800, 600), (1280, 800)])
def test_lan_paint_along_guidance_precedes_video_share(
    controllers, qapp, monkeypatch, profile, size,
):
    invite = invitation()
    arm_lan(monkeypatch, invite)
    app = controllers(invite=invite, profile=profile)
    app.settings.last_creator_start_key = "talk_and_make"
    app.window.resize(*size)
    app.window.show()
    assert app.begin_startup_journey()
    room = app._room_participant
    host_state = replace(state(invite), art_start_key="paint_along")
    assert not host_state.reference_video.shared
    assert not host_state.reference_video.identity_digest
    room.lan_guest.client.state = lambda *args: host_state
    room.lan_guest.poll_once()
    drain(qapp, lambda: room.state is ArtRoomState.CONNECTED)

    # Inspect the automatically refreshed surface, without asking for a sync.
    panel = app.window.art_room_overview
    assert panel.isVisibleTo(app.window)
    assert panel._overview.activity_label == "Paint along is starting"
    assert panel._overview.activity_action == "video"
    button = panel.activity_button()
    assert button.isVisibleTo(app.window)
    assert button.isEnabled()
    assert button.text() == button.accessibleName() == "Open Paint along"
    assert room.borrowed_start == app.creator_start.key == "paint_along"
    assert app._art_room_runs_paint_along()
    assert app.settings.last_creator_start_key == "talk_and_make"
    assert app.settings.last_creator_profile_key == profile


def test_lan_host_start_updates_and_leave_restore_personal_choice(
    controllers, qapp, monkeypatch,
):
    invite = invitation()
    arm_lan(monkeypatch, invite)
    app = controllers(invite=invite, profile="art")
    app.settings.last_creator_start_key = "paint_along"
    assert app.begin_startup_journey()
    room = app._room_participant
    owner, generation = room.lan_guest, room.generation

    for start in ("talk_and_make", "paint_along", "talk_and_make", ""):
        host_state = replace(state(invite), art_start_key=start)
        owner.client.state = lambda *args: host_state
        owner.poll_once()
        qapp.processEvents()
        assert room.state is ArtRoomState.CONNECTED
        assert room.borrowed_start == start
        assert app._art_room_runs_paint_along() is (start == "paint_along")
        if start:
            assert app.creator_start is not None
            assert app.creator_start.key == start
        else:
            # PR #206: a connected guest has no start until the host supplies
            # one. last_creator_start_key must not invent Paint along here.
            assert app.creator_start is None
        assert app.settings.last_creator_start_key == "paint_along"
        overview = app.window.art_room_overview._overview
        assert (overview.activity_label == "Paint along is starting") is (start == "paint_along")

    # A current connection must reject receipts from another owner/generation.
    stale_state = replace(state(invite), art_start_key="paint_along")
    room.receive_lan(object(), generation, stale_state)
    room.receive_lan(owner, generation - 1, stale_state)
    assert room.borrowed_start == ""

    room.receive_lan(owner, generation, stale_state)
    assert room.borrowed_start == "paint_along"
    assert app._stop_session_peer(clear_invite=True)
    assert room.borrowed_start == ""
    assert app.creator_start.key == "paint_along"
    assert not app._art_room_runs_paint_along()
    room.receive_lan(owner, generation, stale_state)
    assert room.borrowed_start == ""
    assert room.state is ArtRoomState.NONE
