"""Art hosts see fresh room names without exposing them as public guidance."""

from __future__ import annotations

import json
from collections import Counter
from types import MethodType, SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from PySide6.QtCore import QPoint, QPointF, QRect, QSize, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from core.session_conductor import ArtRoomState
from core.session_transfer import EnrollmentRegistry, SessionPeerServer
from tests.test_art_lan_host_recovery import (
    ControlledHost,
    Listener,
    _drain,
    _retry,
    host as _host_fixture,
    qapp as _qapp_fixture,
)
from tests.test_art_notes_conversation_journey import room as _guest_room_fixture

host = _host_fixture
qapp = _qapp_fixture
guest_room = _guest_room_fixture


@pytest.fixture(autouse=True)
def keyboard_navigation(qapp):
    # Match the existing UI suite without changing the operating-system
    # preference for whether Tab visits controls or only text fields.
    previous = qapp.styleHints().tabFocusBehavior()
    qapp.styleHints().setTabFocusBehavior(Qt.TabFocusBehavior.TabFocusAllControls)
    yield
    qapp.styleHints().setTabFocusBehavior(previous)

_ALEX = "PRIVATE_ARTIST_Alex sculptor"
_SAM = "PRIVATE_ARTIST_Sam 3D"
_LITERAL = "PRIVATE_ARTIST_<b>Clay & paper</b>"


@pytest.fixture
def named_host(host, monkeypatch):
    """Keep the actual registry and projection behind the controlled listener.

    The reused host fixture replaces OS binding only. Its authenticated poll
    receipt is controlled here; production registry names and reader expiry
    remain in use. Socket authentication is covered by the connection-facts
    tests, rather than silently replaced by a list of UI strings.
    """

    # Failed Quit owns a real cleanup notice. Acknowledge it without changing
    # the controller shutdown/retention path or leaving a modal test blocked.
    monkeypatch.setattr(
        QMessageBox, "information", Mock(return_value=QMessageBox.StandardButton.Ok),
    )
    original_start = ControlledHost.start

    def start(owner, address, **kwargs):
        original_start(owner, address, **kwargs)
        owner.registry = EnrollmentRegistry(
            owner.root / f"room-names-{owner.start_count}", owner.credentials,
        )
        owner.server.registry = owner.registry

    def connection_names(listener, *, now=None):
        return SessionPeerServer.room_connection_names(listener, now=now)

    monkeypatch.setattr(ControlledHost, "start", start)
    monkeypatch.setattr(Listener, "room_connection_names", connection_names, raising=False)

    def create():
        rig = host()
        rig.private_enrollments = []

        def enroll(name, *, owner=None):
            current = owner or rig.app.host_peer
            enrolled = current.registry.enroll(
                str(uuid4()), name, invite_token=current.credentials.invite_token,
            )
            rig.private_enrollments.append(enrolled)
            return enrolled

        def observe(enrolled, *, owner=None):
            current = owner or rig.app.host_peer
            assert current.registry.authenticate(
                enrolled.participant_id, enrolled.participant_token,
            )
            listener = current.server
            with listener._room_poll_lock:
                listener._room_polls[enrolled.participant_id] = listener._room_poll_clock()

        rig.enroll, rig.observe = enroll, observe
        return rig

    return create


def _settle(qapp):
    for _ in range(3):
        qapp.processEvents()


def _names(panel):
    return tuple(panel._connections_list.item(row).text()
                 for row in range(panel._connections_list.count()))


def _assert_cleared(app):
    panel = app.window.art_room_overview
    assert panel._connections_list.count() == 0
    assert not panel._connections_list.isVisibleTo(app.window)


def _assert_named(rig, *expected):
    app = rig.app
    panel = app.window.art_room_overview
    projection = app._room_participant.host_connection_names()
    assert projection is not None
    assert Counter(projection.names) == Counter(expected)
    assert Counter(_names(panel)) == Counter(expected)
    assert panel._connections_list.isVisibleTo(app.window)
    assert panel._connection.text() == "Connected to your room"
    assert panel._connection_detail.text() == (
        "Names guests chose when joining WebJam. Talk and screen sharing are separate."
    )
    return panel


def _assert_private_and_no_external_work(rig, caplog):
    app = rig.app
    panel = app.window.art_room_overview
    public = json.dumps({
        "room": app.art_room_state().to_public_dict(),
        "diagnostics": app._companion_get_diagnostics(),
        "participants": app._companion_get_participants(),
    }, sort_keys=True, default=str)
    public += repr(panel._overview) + panel.accessibleDescription() + caplog.text
    projection = app._room_participant.host_connection_names()
    public += repr(projection)
    for enrolled in rig.private_enrollments:
        for private in (enrolled.display_name, enrolled.installation_id,
                        enrolled.participant_id, enrolled.participant_token):
            assert private not in public
    app._launch_native_jamulus_for_startup.assert_not_called()
    app._start_hosted_server_for_startup.assert_not_called()
    app.bridge.launch_webex.assert_not_called()
    rig.player.assert_not_called()
    assert not rig.launcher.joined
    assert app._shared_canvas_dialog is None
    assert app._reference_video_dialog is None


def test_only_fresh_authenticated_names_reach_the_actual_host_room(
    named_host, caplog,
):
    rig = named_host()
    app = rig.app
    alex = rig.enroll(_ALEX)
    sam = rig.enroll(_SAM)
    rig.tick()
    assert app._room_participant.state is ArtRoomState.WAITING
    _assert_cleared(app)

    rig.observe(sam)
    rig.tick()
    panel = _assert_named(rig, _SAM)
    assert _ALEX not in _names(panel)
    assert app._room_participant.state is ArtRoomState.CONNECTED
    rig.observe(alex)
    rig.tick()
    _assert_named(rig, _ALEX, _SAM)

    # Enrollment remains durable, but room presence expires at five seconds.
    rig.network.now += 5.0
    rig.tick()
    assert len(rig.owner.registry.participants()) == 2
    assert app._room_participant.state is ArtRoomState.WAITING
    _assert_cleared(app)
    rig.observe(alex)
    rig.tick()
    _assert_named(rig, _ALEX)
    _assert_private_and_no_external_work(rig, caplog)


@pytest.mark.parametrize("loss", ["missing", "changed"])
@pytest.mark.parametrize("expires", [False, True])
def test_network_loss_retires_names_and_restores_only_current_readers(
    named_host, caplog, loss, expires,
):
    rig = named_host()
    enrolled = rig.enroll(_ALEX)
    rig.observe(enrolled)
    rig.tick()
    _assert_named(rig, _ALEX)
    owner, listener = rig.owner, rig.owner.server

    rig.network.key = loss
    rig.tick()
    assert rig.app._room_participant.state is ArtRoomState.RECONNECTING
    assert rig.app._room_participant.host_connection_names() is None
    _assert_cleared(rig.app)
    rig.network.now += 5.0 if expires else 1.0
    rig.network.key = "original"
    rig.tick()
    assert rig.owner is owner and owner.server is listener
    if expires:
        _assert_cleared(rig.app)
        rig.observe(enrolled)
        rig.tick()
    _assert_named(rig, _ALEX)
    _assert_private_and_no_external_work(rig, caplog)


def test_real_retry_rebind_does_not_reuse_old_room_names(
    named_host, qapp, caplog,
):
    rig = named_host()
    old = rig.enroll(_ALEX)
    rig.observe(old)
    rig.tick()
    _assert_named(rig, _ALEX)
    retired = rig.owner.server
    rig.network.key = "changed"
    rig.tick()
    _retry(rig, qapp)
    rig.tick()
    assert rig.owner.server is not retired and retired._httpd.stopping
    _assert_cleared(rig.app)

    # Even a late event on the retired listener belongs to the old room.
    retired._room_polls[old.participant_id] = rig.network.now
    rig.tick()
    _assert_cleared(rig.app)
    current = rig.enroll(_SAM)
    rig.observe(current)
    rig.tick()
    _assert_named(rig, _SAM)
    _assert_private_and_no_external_work(rig, caplog)


@pytest.mark.parametrize("retirement", ["end", "cleanup", "quit", "profile"])
def test_room_retirement_clears_the_private_widget_immediately(
    named_host, qapp, caplog, retirement,
):
    rig = named_host()
    app = rig.app
    enrolled = rig.enroll(_ALEX)
    rig.observe(enrolled)
    rig.tick()
    _assert_named(rig, _ALEX)
    notes = app.window.session_canvas.current_notes()
    if retirement == "end":
        app.window.session_strip._audio_button.click()
        _drain(qapp, lambda: not app.audio.stopping)
        assert not rig.owner.active
    elif retirement == "cleanup":
        rig.network.key = "changed"
        rig.tick()
        rig.owner.stop_outcomes.append(False)
        _retry(rig, qapp, click=False)
        assert app.audio.cleanup_retry_required
        rig.network.key = "original"
        rig.tick()
    elif retirement == "quit":
        rig.owner.stop_outcomes.append(False)
        assert app.shutdown() is False
        assert app._shutdown_cleanup_pending
    else:
        app._apply_creator_profile_key("music")
        assert app.creator_profile.key == "music"
    _assert_cleared(app)
    assert app._room_participant.host_connection_names() is None
    if retirement != "profile":
        assert app.window.session_canvas.current_notes() == notes
    _assert_private_and_no_external_work(rig, caplog)


@pytest.mark.parametrize("changed", ["generation", "owner", "lifecycle"])
def test_name_read_cannot_render_after_its_owner_changes(
    named_host, monkeypatch, caplog, changed,
):
    rig = named_host()
    app, room = rig.app, rig.app._room_participant
    enrolled = rig.enroll(_ALEX)
    rig.observe(enrolled)
    rig.tick()
    _assert_named(rig, _ALEX)
    owner, listener = rig.owner, rig.owner.server
    original = listener.room_connection_names
    changed_once = []

    def read_then_retire(_listener, *, now=None):
        result = original(now=now)
        if not changed_once:
            changed_once.append(True)
            if changed == "generation":
                room.generation += 1
            elif changed == "lifecycle":
                owner._lifecycle_generation += 1
            else:
                newer = ControlledHost(rig.root / "new-owner", rig.network, rig.events)
                newer.start(rig.network.address, creator_profile_key="art")
                app.host_peer = newer
                room.generation += 1
                room.state = ArtRoomState.WAITING
        return result

    monkeypatch.setattr(listener, "room_connection_names", MethodType(read_then_retire, listener))
    app._sync_art_room_overview()
    assert changed_once
    _assert_cleared(app)
    _assert_private_and_no_external_work(rig, caplog)


@pytest.mark.parametrize("role", ["lan", "native"])
def test_guest_connection_does_not_invent_names_from_transport_or_personal_profile(
    guest_room, caplog, role,
):
    pair = guest_room(role=role)
    app = pair.app
    app._tick_creator_start()
    assert app._room_participant.state is ArtRoomState.CONNECTED
    assert app._room_participant.host_connection_names() is None
    _assert_cleared(app)
    panel = app.window.art_room_overview
    assert panel._connection.text() == "Connected to the host"
    assert "does not yet show a full artist list" in panel._connection_detail.text()
    assert "PRIVATE_ARTIST" not in repr(panel._overview) + caplog.text
    app.bridge.launch_webex.assert_not_called()
    pair.player_factory.assert_not_called()
    assert not pair.launcher.joined


@pytest.mark.parametrize("conversation_open", [False, True])
def test_compact_named_room_keeps_every_name_and_conversation_keyboard_reachable(
    named_host, qapp, caplog, conversation_open,
):
    rig = named_host()
    window = rig.app.window
    expected = [_LITERAL] + [f"PRIVATE_ARTIST_{index:02d} own tools" for index in range(11)]
    for name in expected:
        rig.observe(rig.enroll(name))
    rig.tick()
    window.webex_embed.setVisible(conversation_open)
    window.resize(760, 600)
    window.activateWindow()
    _settle(qapp)
    assert window.size() == QSize(760, 600)
    panel = _assert_named(rig, *expected)
    names = panel._connections_list
    assert _LITERAL in _names(panel)
    assert names.horizontalScrollBarPolicy() is Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    assert names.verticalScrollBar().maximum() > 0
    assert names.height() < 5 * names.sizeHintForRow(0)
    names.setCurrentRow(0)
    names.setFocus(Qt.FocusReason.TabFocusReason)
    _settle(qapp)
    assert names.hasFocus()
    QTest.keyClick(names, Qt.Key.Key_End)
    _settle(qapp)
    assert names.currentRow() == names.count() - 1
    assert names.viewport().rect().intersects(names.visualItemRect(names.currentItem()))
    current, scroll = names.currentRow(), names.verticalScrollBar().value()
    for _ in range(4):
        rig.tick()
        _settle(qapp)
    assert names.hasFocus() and names.currentRow() == current
    assert names.verticalScrollBar().value() == scroll
    assert Counter(_names(panel)) == Counter(expected)

    # The bounded name list must not trap Tab or push the existing action
    # outside the room's scrollable viewport when Conversation is also open.
    QTest.keyClick(names, Qt.Key.Key_Tab)
    _settle(qapp)
    action = panel.conversation_button()
    assert action.hasFocus()
    action_rect = QRect(action.mapTo(panel.viewport(), QPoint()), action.size())
    assert panel.viewport().rect().contains(action_rect)
    assert panel.horizontalScrollBar().maximum() == 0
    QTest.keyClick(action, Qt.Key.Key_Space)
    _settle(qapp)
    assert window.webex_embed.isVisibleTo(window)
    _assert_private_and_no_external_work(rig, caplog)


def test_membership_changes_keep_the_keyboard_readers_selected_name_visible(
    named_host, qapp, caplog,
):
    rig = named_host()
    enrolled = []
    for index in range(10, 22):
        item = rig.enroll(f"PRIVATE_ARTIST_{index:02d} own tools")
        enrolled.append(item)
        rig.observe(item)
    rig.tick()
    window = rig.app.window
    window.resize(760, 600)
    window.activateWindow()
    _settle(qapp)
    panel = _assert_named(rig, *(item.display_name for item in enrolled))
    names = panel._connections_list
    names.setCurrentRow(8)
    names.setFocus(Qt.FocusReason.TabFocusReason)
    names.scrollToItem(names.currentItem())
    _settle(qapp)
    chosen = names.currentItem().text()
    before_row, before_scroll = names.currentRow(), names.verticalScrollBar().value()
    assert before_scroll > 0 and names.hasFocus()

    arriving = rig.enroll("PRIVATE_ARTIST_00 new guest")
    rig.observe(arriving)
    rig.tick()
    _settle(qapp)
    assert names.hasFocus() and names.currentItem().text() == chosen
    assert names.currentRow() == before_row + 1
    assert names.viewport().rect().contains(names.visualItemRect(names.currentItem()))

    # Another artist stops polling. The name being read remains current;
    # rebuilding rows must not silently select a different collaborator.
    departed = enrolled[0]
    rig.owner.server._room_polls[departed.participant_id] = rig.network.now - 5.0
    rig.tick()
    _settle(qapp)
    assert departed.display_name not in _names(panel)
    assert names.hasFocus() and names.currentItem().text() == chosen
    assert names.currentRow() == before_row
    assert names.viewport().rect().contains(names.visualItemRect(names.currentItem()))
    _assert_private_and_no_external_work(rig, caplog)


def test_busy_name_inventory_clears_rows_until_a_current_read_is_available(
    named_host, monkeypatch, caplog,
):
    rig = named_host()
    enrolled = rig.enroll(_ALEX)
    rig.observe(enrolled)
    rig.tick()
    _assert_named(rig, _ALEX)
    listener = rig.owner.server
    with monkeypatch.context() as patch:
        patch.setattr(listener, "room_connection_names", lambda **kwargs: None)
        rig.tick()
        assert rig.app._room_participant.state is ArtRoomState.CONNECTED
        assert rig.app._room_participant.host_connection_names() is None
        _assert_cleared(rig.app)
        assert "does not yet show a full artist list" in (
            rig.app.window.art_room_overview._connection_detail.text()
        )
    rig.tick()
    _assert_named(rig, _ALEX)
    _assert_private_and_no_external_work(rig, caplog)


def test_rendering_current_names_does_not_probe_the_operating_system_network(
    named_host, monkeypatch, caplog,
):
    rig = named_host()
    enrolled = rig.enroll(_ALEX)
    rig.observe(enrolled)
    rig.tick()
    _assert_named(rig, _ALEX)
    readiness = Mock(side_effect=AssertionError("Names rendering must not probe room readiness"))
    network = Mock(side_effect=AssertionError("Names rendering must not inspect network interfaces"))
    with monkeypatch.context() as patch:
        patch.setattr(rig.app._room_participant, "readiness", readiness)
        patch.setattr("core.network_invite.local_band_address", network)
        for _ in range(3):
            projection = rig.app._room_participant.host_connection_names()
            assert projection is not None and projection.names == (_ALEX,)
            rig.app._sync_art_room_overview()
            _assert_named(rig, _ALEX)
        readiness.assert_not_called()
        network.assert_not_called()
    _assert_private_and_no_external_work(rig, caplog)


@pytest.mark.parametrize("elapsed", [4.9, 5.0, -0.1])
def test_cached_route_observation_must_be_fresh_before_rendering_names(
    named_host, monkeypatch, caplog, elapsed,
):
    # Freeze only the room controller's clock. Readers keep their separate
    # production five-second clock, so this tests route evidence aging even
    # when the server still has fresh authenticated polling artists.
    clock = [1000.0]
    monkeypatch.setattr(
        "webjam_qt.controllers.room_participant.time",
        SimpleNamespace(monotonic=lambda: clock[0]),
    )
    rig = named_host()
    enrolled = rig.enroll(_ALEX)
    rig.observe(enrolled)
    rig.tick()
    _assert_named(rig, _ALEX)
    clock[0] += elapsed
    assert rig.owner.server.room_connection_names().names == (_ALEX,)
    rig.app._sync_art_room_overview()
    if 0 <= elapsed < 5.0:
        _assert_named(rig, _ALEX)
    else:
        assert rig.app._room_participant.host_connection_names() is None
        _assert_cleared(rig.app)
    # Only the normal owner tick can observe the route again and authorize
    # the existing readers; rendering itself did not refresh this evidence.
    rig.tick()
    _assert_named(rig, _ALEX)
    _assert_private_and_no_external_work(rig, caplog)


@pytest.mark.parametrize("boundary", ["readiness", "readers"])
@pytest.mark.parametrize("changed", ["owner", "server", "generation", "lifecycle"])
def test_tick_cannot_stamp_old_route_or_readers_onto_a_changed_room(
    named_host, monkeypatch, caplog, boundary, changed,
):
    rig = named_host()
    app, room = rig.app, rig.app._room_participant
    old_artist = rig.enroll(_ALEX)
    rig.observe(old_artist)
    rig.tick()
    _assert_named(rig, _ALEX)
    original_host, original_server = rig.owner, rig.owner.server
    retired = []

    def change_room_once():
        if retired:
            return
        retired.append(True)
        if changed == "owner":
            newer = ControlledHost(rig.root / "new-tick-owner", rig.network, rig.events)
            newer.start(rig.network.address, creator_profile_key="art")
            app.host_peer = newer
        elif changed == "server":
            newer_server = Listener(rig.network.address, rig.network, 99)
            newer_server.registry = original_host.registry
            original_host.server = newer_server
        elif changed == "generation":
            room.generation += 1
        else:
            original_host._lifecycle_generation += 1
        # The newer room has chosen its opening state. A callback returning
        # the old room's route/readers cannot promote that state to connected.
        room.role = "host"
        room.state = ArtRoomState.STARTING
        app.host_peer.server._room_polls.clear()
        arriving = rig.enroll(_SAM)
        rig.observe(arriving)

    with monkeypatch.context() as patch:
        if boundary == "readiness":
            original = room.readiness

            def read_then_change():
                result = original()
                change_room_once()
                return result

            patch.setattr(room, "readiness", read_then_change)
        else:
            original = original_server.room_participants

            def read_then_change(*, now=None):
                result = original(now=now)
                change_room_once()
                return result

            patch.setattr(original_server, "room_participants", read_then_change)
        rig.tick()
        assert retired
        assert room.state is ArtRoomState.STARTING
        assert room.host_connection_names() is None
        _assert_cleared(app)

    # A subsequent ordinary tick must be able to prove the current owner and
    # fresh readers. The fence discards old work without stranding recovery.
    rig.tick()
    assert room.state is ArtRoomState.CONNECTED
    _assert_named(rig, _SAM)
    _assert_private_and_no_external_work(rig, caplog)


def test_membership_change_preserves_wheel_scroll_without_selecting_a_name(
    named_host, qapp, caplog,
):
    rig = named_host()
    for index in range(10, 22):
        rig.observe(rig.enroll(f"PRIVATE_ARTIST_{index:02d} own tools"))
    rig.tick()
    window = rig.app.window
    window.resize(760, 600)
    window.activateWindow()
    _settle(qapp)
    panel = window.art_room_overview
    names = panel._connections_list
    panel.conversation_button().setFocus(Qt.FocusReason.TabFocusReason)
    names.clearSelection()
    names.setCurrentRow(-1)
    panel.ensureWidgetVisible(names)
    _settle(qapp)
    local = names.viewport().rect().center()
    wheel = QWheelEvent(
        QPointF(local), QPointF(names.viewport().mapToGlobal(local)),
        QPoint(0, -60), QPoint(0, -120), Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False,
    )
    QApplication.sendEvent(names.viewport(), wheel)
    _settle(qapp)
    previous_scroll = names.verticalScrollBar().value()
    assert previous_scroll > 0 and names.currentRow() == -1
    assert not names.selectedItems()

    rig.observe(rig.enroll("PRIVATE_ARTIST_00 arriving"))
    rig.tick()
    _settle(qapp)
    assert names.verticalScrollBar().value() == previous_scroll
    assert names.currentRow() == -1 and not names.selectedItems()
    _assert_private_and_no_external_work(rig, caplog)


def test_small_named_room_keeps_its_next_action_visible_at_760_by_600(named_host, qapp):
    rig = named_host()
    for name in ("Alex", "Mira", "Sam"):
        rig.observe(rig.enroll(name))
    rig.tick()
    window = rig.app.window
    window.webex_embed.hide()
    window.resize(760, 600)
    _settle(qapp)
    panel = window.art_room_overview
    panel.verticalScrollBar().setValue(0)
    _settle(qapp)
    action = panel.conversation_button()
    action_rect = QRect(action.mapTo(panel.viewport(), QPoint()), action.size())
    assert panel.viewport().rect().contains(action_rect)
    assert panel._connections_list.isVisibleTo(window)
    assert panel.horizontalScrollBar().maximum() == 0
