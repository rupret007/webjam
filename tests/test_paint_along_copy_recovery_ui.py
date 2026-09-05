"""Real guest actions recover a local copy without claiming host transport."""

from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtGui import QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QWidget

from core.creative_modes import CREATOR_PROFILES, get_creator_profile_by_key
from core.reference_video import ReferenceVideoError, ReferenceVideoFollowState
from core.session_transfer import ReferenceVideoPlaybackState, ReferenceVideoSessionSnapshot
from tests.test_reference_video_coordinator import Clock, FakeHostPeer, FakePlayer, SESSION_ID, SESSION_KEY
from webjam_qt.controllers.reference_video_coordinator import ReferenceVideoCoordinator
from webjam_qt.theme import load_stylesheet
from webjam_qt.windows.conductor_window import ConductorWindow
from webjam_qt.windows.reference_video import ReferenceVideoDialog


class RecoverablePlayer(FakePlayer):
    def __init__(self):
        super().__init__()
        self.fail_load = False
        self.fail_play = False
        self.loaded_paths = []
        self.surface = None

    def load(self, path):
        if self.fail_load:
            raise RuntimeError("synthetic decoder error")
        self.loaded_paths.append(path)
        return super().load(path)

    def play(self):
        if self.fail_play:
            raise RuntimeError("synthetic playback error")
        return super().play()


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    previous = app.styleSheet()
    app.setStyleSheet(load_stylesheet())
    yield app
    app.setStyleSheet(previous)


@pytest.fixture()
def room(qapp, tmp_path, monkeypatch):
    panel = ReferenceVideoDialog(hosting=False)
    player = RecoverablePlayer()
    peer = FakeHostPeer()
    clock = Clock()
    host = ReferenceVideoCoordinator(player_factory=FakePlayer, host_peer_provider=lambda: peer, clock=clock)
    guest = ReferenceVideoCoordinator(player_factory=lambda: player, clock=clock, on_follow_snapshot=panel.set_follow_snapshot)
    host.begin_host(session_id=SESSION_ID, session_key=SESSION_KEY)
    guest.begin_guest(session_id=SESSION_ID, session_key=SESSION_KEY)
    first = tmp_path / ("a" * 230 + ".mp4")
    first.write_bytes(b"first local process video")
    second = tmp_path / "second.mp4"
    second.write_bytes(b"another local process video")
    local = tmp_path / "my-copy.mp4"
    local.write_bytes(first.read_bytes())
    errors = []
    selection = [str(local)]
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_args: (selection[0], ""))

    def open_copy(path):
        try:
            guest.open_local_copy(path)
        except ReferenceVideoError as exc:
            errors.append(str(exc))

    def observe(**changes):
        values = dict(peer.published[-1])
        values.update(changes)
        values["state"] = ReferenceVideoPlaybackState(values["state"])
        projection = ReferenceVideoSessionSnapshot(generation=len(peer.published), playback_generation=len(peer.published), **values)
        guest.observe_host_state(SimpleNamespace(reference_video=projection))
        guest.tick()

    def choose(path=None):
        if path is not None:
            selection[0] = str(path)
        assert panel._open_button.isEnabled()
        QTest.mouseClick(panel._open_button, Qt.MouseButton.LeftButton)
        qapp.processEvents()

    panel.open_local_copy_requested.connect(open_copy)
    panel.close_local_copy_requested.connect(guest.close_local_copy)
    panel.hide_requested.connect(guest.set_hidden)
    host.share(str(first))
    observe()
    data = SimpleNamespace(panel=panel, player=player, host=host, guest=guest, first=first, second=second, local=local, errors=errors, choose=choose, observe=observe, selection=selection, window=None)
    yield data
    player.fail_load = player.fail_play = False
    guest.end()
    host.end()
    # Drain queued focus work while the widgets still exist, then dispatch
    # DeferredDelete explicitly so later stylesheet changes cannot retain them.
    qapp.processEvents()
    QTest.qWait(1)
    owner = data.window or panel
    owner.close()
    owner.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()


def _arrange(room, state):
    if state != "needs_file":
        room.choose()
    if state == "mismatched_file":
        room.host.share(str(room.second))
        room.observe()
    elif state == "file_unavailable":
        room.local.rename(room.local.with_name("moved.mp4"))
        room.guest.tick()
    elif state == "local_attention":
        room.player.fail_play = True
        room.host.play()
        room.observe()
    elif state == "hidden":
        room.guest.set_hidden(True)
    elif state == "no_video":
        room.host.withdraw()
        room.observe()
    elif state == "host_attention":
        room.observe(needs_attention=True)
    assert room.guest.follow_snapshot.state.value == state


@pytest.mark.parametrize("failure", ["mismatched_file", "file_unavailable", "local_attention", "load_failure"])
def test_open_my_copy_recovers_through_the_actual_button(room, qapp, failure):
    room.panel.show()
    qapp.processEvents()
    if failure == "load_failure":
        room.player.fail_load = True
        room.choose()
        assert room.guest.follow_snapshot.state is ReferenceVideoFollowState.LOCAL_ATTENTION
    else:
        _arrange(room, failure)
    assert room.panel._open_button.isVisible()
    assert room.panel._open_button.isEnabled()
    assert "Following the host" not in room.panel._status.text()
    room.player.fail_load = room.player.fail_play = False
    replacement = room.second if failure == "mismatched_file" else room.first
    room.errors.clear()
    room.choose(replacement)
    room.guest.tick()
    assert room.errors == []
    assert room.player.loaded_paths[-1] == replacement
    assert room.guest.follow_snapshot.state is ReferenceVideoFollowState.FOLLOWING
    assert room.panel._status.text() == "Following the host."
    assert room.panel._open_button.isHidden()
    assert room.panel._hide_button.isVisible()
    assert room.panel._position.isEnabled() is False
    assert not hasattr(room.panel, "_play_button")
    assert not hasattr(room.panel, "_pause_button")
    assert room.player.muted is True


def test_keyboard_open_and_cancel_keeps_the_recovery_available(room, qapp):
    room.panel.show()
    _arrange(room, "file_unavailable")
    room.selection[0] = ""
    room.panel.activateWindow()
    room.panel._open_button.setFocus()
    qapp.processEvents()
    QTest.keyClick(room.panel._open_button, Qt.Key.Key_Space)
    qapp.processEvents()
    assert room.errors == []
    assert room.guest.follow_snapshot.state is ReferenceVideoFollowState.FILE_UNAVAILABLE
    assert room.panel._open_button.isVisible()
    assert room.panel._open_button.hasFocus()


@pytest.mark.parametrize("state", ["hidden", "no_video", "host_attention", "local_attention"])
def test_retained_copy_can_be_closed_from_the_real_more_menu(room, qapp, state):
    room.panel.show()
    _arrange(room, state)
    assert room.guest.follow_snapshot.can_close_local_copy
    assert room.panel._more_button.isVisible()
    assert room.panel._close_action.isVisible()
    room.panel._more_menu.popup(room.panel._more_button.mapToGlobal(room.panel._more_button.rect().bottomLeft()))
    qapp.processEvents()
    QTest.mouseClick(room.panel._more_menu, Qt.MouseButton.LeftButton, pos=room.panel._more_menu.actionGeometry(room.panel._close_action).center())
    qapp.processEvents()
    assert room.guest.follow_snapshot.can_close_local_copy is False
    assert room.panel._close_action.isVisible() is False


@pytest.mark.parametrize("state", ["needs_file", "mismatched_file", "file_unavailable", "local_attention", "following"])
@pytest.mark.parametrize("size", [(720, 560), (760, 600), (1040, 720)])
@pytest.mark.parametrize("stretch", [100, 125])
def test_recovery_and_room_navigation_fit_without_overlap(room, qapp, state, size, stretch):
    _arrange(room, state)
    window = ConductorWindow(mode_entries=[(p.key, p.label) for p in CREATOR_PROFILES], initial_mode_key="art", initial_title="Art")
    room.window = window
    window.set_creator_profile(get_creator_profile_by_key("art"))
    window.session_hud.set_state("Connected to the Art room", "Make with your own tools. Paint along is optional.", action_visible=False, ready=True)
    window.show_paint_along(room.panel)
    window.resize(*size)
    window.show()
    qapp.processEvents()
    for widget in room.panel.findChildren(QWidget):
        font = QFont(widget.font())
        font.setStretch(stretch)
        widget.setFont(font)
    qapp.processEvents()
    panel = room.panel
    primary = panel._hide_button if state == "following" else panel._open_button
    assert (window.width(), window.height()) == size
    assert panel._surface_holder.height() >= 40
    for widget in (panel._headline, panel._status, panel._surface_holder, primary, panel._more_button, panel._back_button, panel._hint):
        assert widget.isVisible()
        assert panel.rect().contains(widget.geometry())
    assert panel._status.height() >= panel._status.heightForWidth(panel._status.width())
    assert panel._hint.height() >= panel._hint.heightForWidth(panel._hint.width())
    assert panel._surface_holder.geometry().bottom() < primary.geometry().top()
    assert primary.geometry().bottom() < panel._hint.geometry().top()
    assert not primary.geometry().intersects(panel._more_button.geometry())
    assert panel._back_button.geometry().bottom() < panel._headline.geometry().top()
    assert panel._headline.fontMetrics().horizontalAdvance(panel._headline.text()) <= panel._headline.contentsRect().width()
    assert panel._headline.toolTip() == room.guest.follow_snapshot.source_display_name
    assert panel._headline.accessibleDescription() == room.guest.follow_snapshot.source_display_name
    if state == "following":
        assert panel._surface_holder.geometry().bottom() < panel._position.geometry().top()
        assert panel._position.geometry().bottom() < primary.geometry().top()
    assert panel._position.isEnabled() is False
