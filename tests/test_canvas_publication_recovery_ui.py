"""Host canvas actions retain accepted room facts until publication succeeds."""
from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtGui import QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QWidget

from core.creative_modes import CREATOR_PROFILES, get_creator_profile_by_key
from core.session_transfer import SessionControlState, SharedCanvasSessionSnapshot
from core.shared_canvas import SharedCanvasError, SharedCanvasPendingAction as Pending
from tests.test_shared_canvas_coordinator import FakeLauncher
from webjam_qt.controllers.shared_canvas_coordinator import SharedCanvasCoordinator
from webjam_qt.theme import load_stylesheet
from webjam_qt.windows.conductor_window import ConductorWindow
from webjam_qt.windows.shared_canvas import SharedCanvasDialog

SESSION_ID = "d2f915b8-a54e-4202-89aa-b88db0d55b46"
SECRET = "private-canvas-test-password"
FIRST = f"drawpile://example.com/lesson-one?v1&p={SECRET}"
SECOND = f"drawpile://example.org/lesson-two?v1&p={SECRET}"
LONG = f"drawpile://{'a'*50}.{'b'*50}/{'s'*50}?v1&p={SECRET}"


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    previous = app.styleSheet()
    app.setStyleSheet(load_stylesheet())
    yield app
    app.setStyleSheet(previous)


@pytest.fixture()
def room(qapp, tmp_path):
    control = SessionControlState(tmp_path / "room", SESSION_ID, creator_profile_key="art")

    class Peer:
        active = True
        mode = "accept"
        on_publish = None

        def __init__(self):
            self.calls = []

        def publish_shared_canvas_state(self, **values):
            self.calls.append(values)
            if self.on_publish:
                self.on_publish()
            if self.mode == "raise":
                raise RuntimeError("private transport detail")
            if self.mode == "none":
                return None
            if self.mode == "false":
                return False
            if self.mode == "mismatch":
                return SharedCanvasSessionSnapshot()
            return control.publish_shared_canvas(**values)

    peer = Peer()
    launcher = FakeLauncher()
    panel = SharedCanvasDialog(hosting=True)
    guest = SharedCanvasCoordinator(launcher_factory=FakeLauncher)
    guest.begin_guest()
    host = SharedCanvasCoordinator(launcher_factory=lambda: launcher, host_peer_provider=lambda: peer, on_host_snapshot=panel.set_host_snapshot)
    host.begin_host()
    errors = []

    def run(operation):
        try:
            operation()
        except SharedCanvasError as error:
            errors.append(str(error))
        panel.set_host_snapshot(host.host_snapshot)
        guest.observe_host_state(control.snapshot())

    def click(widget, *, keyboard=False):
        assert widget.isVisible()
        assert widget.isEnabled()
        if keyboard:
            panel.activateWindow()
            widget.setFocus()
            qapp.processEvents()
            QTest.keyClick(widget, Qt.Key.Key_Space)
        else:
            QTest.mouseClick(widget, Qt.MouseButton.LeftButton)
        qapp.processEvents()

    def share(invite=FIRST):
        panel._invite_input.setText(invite)
        click(panel._chip)

    panel.host_in_drawpile_requested.connect(lambda: run(host.open_drawpile_to_host))
    panel.share_requested.connect(lambda text: run(lambda: host.share(text)))
    panel.withdraw_requested.connect(lambda: run(host.withdraw))
    panel.retry_publication_requested.connect(lambda: run(host.retry_publication))
    panel.open_canvas_requested.connect(lambda: run(host.open_canvas_as_host))
    panel.set_host_snapshot(host.host_snapshot)
    panel.resize(400, 420)
    panel.show()
    qapp.processEvents()
    click(panel._chip)  # The explicit Host action uses only this fake launcher.
    result = SimpleNamespace(host=host, guest=guest, peer=peer, control=control, panel=panel, launcher=launcher, errors=errors, click=click, share=share, window=None)
    yield result
    peer.mode = "accept"
    peer.on_publish = None
    host.end()
    guest.end()
    qapp.processEvents()
    QTest.qWait(1)
    owner = result.window or panel
    owner.close()
    owner.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()


def _assert_no_capability_text(panel):
    for widget in panel.findChildren(QWidget):
        text = widget.text() if isinstance(widget, (QLabel, QLineEdit)) else ""
        for value in (text, widget.accessibleName(), widget.accessibleDescription(), widget.toolTip()):
            assert SECRET not in value
            assert "drawpile://" not in value
    assert panel._invite_input.echoMode() is QLineEdit.EchoMode.Password
    assert panel._invite_input.text() == ""


@pytest.mark.parametrize("failure", ["raise", "none", "false", "mismatch"])
def test_failed_first_share_retries_without_reopening_drawpile(room, failure):
    room.peer.mode = failure
    room.share()
    assert room.host.host_snapshot.pending_action is Pending.SHARE
    assert room.host.host_snapshot.shared is False
    assert room.control.snapshot().shared_canvas.shared is False
    assert room.panel._chip.text() == "Try sharing again"
    assert room.panel._chip.isEnabled()
    assert room.panel._quiet.text() == "Stop sharing"
    assert "lesson-one" not in room.panel._headline.text()
    assert "room can open" not in room.panel._status.text().casefold()
    assert room.panel._invite_input.isHidden()
    _assert_no_capability_text(room.panel)

    room.peer.mode = "accept"
    room.click(room.panel._chip, keyboard=True)

    assert room.host.host_snapshot.pending_action is Pending.NONE
    assert room.host.host_snapshot.shared
    assert room.control.snapshot().shared_canvas.shared
    assert room.guest.follow_snapshot.can_open
    assert room.panel._chip.text() == "Open canvas"
    assert room.panel._headline.text().startswith("Canvas offered:")
    assert "painting on" not in room.panel._headline.text().casefold()
    assert room.launcher.host_pages == 1 and room.launcher.joined == []


def test_change_paste_share_failure_and_retry_preserve_the_accepted_offer(room):
    room.share()
    room.click(room.panel._change_button)
    assert room.panel._change_button.text() == "Cancel change"
    assert room.panel._invite_input.isVisible()
    assert room.panel._chip.text() == "Share with the room"
    assert not room.panel._chip.isEnabled()
    assert "lesson-one" in room.panel._headline.text()
    room.peer.mode = "raise"
    room.share(SECOND)
    assert room.panel._chip.text() == "Try sharing again"
    assert "lesson-one" in room.panel._headline.text()
    assert "lesson-two" not in room.panel._headline.text()
    assert room.control.snapshot().shared_canvas.session_label == "lesson-one"
    _assert_no_capability_text(room.panel)
    room.peer.mode = "accept"
    room.click(room.panel._chip)
    assert "lesson-two" in room.panel._headline.text()
    assert room.control.snapshot().shared_canvas.session_label == "lesson-two"
    assert room.guest.follow_snapshot.session_label == "lesson-two"
    assert room.launcher.host_pages == 1 and not room.launcher.joined


@pytest.mark.parametrize("pasted", [False, True])
def test_cancelling_an_empty_or_pasted_change_never_republishes(room, pasted):
    room.share()
    before = len(room.peer.calls)
    room.click(room.panel._change_button)
    if pasted:
        room.panel._invite_input.setText(SECOND)
    room.click(room.panel._change_button)
    assert len(room.peer.calls) == before
    assert room.panel._chip.text() == "Open canvas"
    assert room.panel._change_button.text() == "Change invitation"
    assert "lesson-one" in room.panel._headline.text()
    assert room.panel._invite_input.isHidden()
    _assert_no_capability_text(room.panel)


def test_invalid_change_leaves_the_accepted_canvas_available(room):
    room.share()
    room.click(room.panel._change_button)
    before = len(room.peer.calls)
    room.share("not a canvas invitation")
    assert room.errors
    assert len(room.peer.calls) == before
    assert room.host.host_snapshot.pending_action is Pending.NONE
    assert room.host.host_snapshot.shared
    assert room.panel._chip.text() == "Open canvas"
    assert "lesson-one" in room.panel._headline.text()


@pytest.mark.parametrize("prior_offer", [False, True])
def test_stop_replaces_failed_share_and_retries_the_withdrawal(room, prior_offer):
    if prior_offer:
        room.share()
        room.click(room.panel._change_button)
    room.peer.mode = "raise"
    room.share(SECOND)
    room.click(room.panel._quiet)
    assert room.host.host_snapshot.pending_action is Pending.WITHDRAW
    assert room.host.host_snapshot.shared is prior_offer
    assert room.panel._chip.text() == "Try stop sharing"
    assert room.panel._quiet.isHidden()
    assert room.panel._change_button.isHidden()
    assert "No shared canvas" not in room.panel._headline.text()
    assert room.panel._invite_input.isHidden()
    room.peer.mode = "accept"
    room.click(room.panel._chip)
    assert room.host.host_snapshot.pending_action is Pending.NONE
    assert not room.control.snapshot().shared_canvas.shared
    assert not room.guest.follow_snapshot.can_open
    assert room.panel._headline.text() == "No shared canvas"
    assert room.launcher.host_pages == 1 and not room.launcher.joined


def test_failed_stop_keeps_the_accepted_offer_and_a_retry(room):
    room.share()
    room.peer.mode = "raise"
    room.click(room.panel._quiet)
    assert room.host.host_snapshot.pending_action is Pending.WITHDRAW
    assert room.control.snapshot().shared_canvas.shared
    assert "lesson-one" in room.panel._headline.text()
    assert room.panel._chip.text() == "Try stop sharing"
    room.peer.mode = "accept"
    room.click(room.panel._chip)
    assert not room.host.host_snapshot.shared
    assert not room.control.snapshot().shared_canvas.shared


def test_inflight_snapshot_disables_retry_and_stop_without_changing_intent(room):
    seen = []
    def during_publication():
        snapshot = room.host.host_snapshot
        assert snapshot.pending_action is Pending.SHARE
        assert not snapshot.can_retry_publication
        assert not room.panel._chip.isEnabled()
        assert not room.panel._quiet.isEnabled()
        before = len(room.peer.calls)
        QTest.mouseClick(room.panel._chip, Qt.MouseButton.LeftButton)
        QTest.mouseClick(room.panel._quiet, Qt.MouseButton.LeftButton)
        assert len(room.peer.calls) == before
        seen.append(snapshot)
    room.peer.on_publish = during_publication
    room.share()
    assert seen
    assert room.host.host_snapshot.shared


def test_publication_retry_does_not_require_launching_or_reinstalling_drawpile(room):
    room.peer.mode = "raise"
    room.share()
    room.launcher.installed = False
    room.panel.set_host_snapshot(room.host.host_snapshot)
    assert room.panel._chip.text() == "Try sharing again"
    room.peer.mode = "accept"
    room.click(room.panel._chip)
    assert room.host.host_snapshot.shared
    assert room.panel._chip.text() == "Install Drawpile"
    assert room.launcher.host_pages == 1 and not room.launcher.joined


def arrange(room, state):
    if state in {"accepted", "changing", "replacement", "withdraw"}:
        room.share(LONG)
    if state in {"changing", "replacement"}:
        room.click(room.panel._change_button)
    if state in {"first", "replacement", "empty_withdraw"}:
        room.peer.mode = "raise"
        room.share(SECOND)
    if state in {"withdraw", "empty_withdraw"}:
        room.peer.mode = "raise"
        room.click(room.panel._quiet)


@pytest.mark.parametrize("state", ["first", "accepted", "changing", "replacement", "withdraw", "empty_withdraw"])
@pytest.mark.parametrize("parent_size", [(720, 560), (1040, 720)])
@pytest.mark.parametrize("stretch", [100, 125])
def test_offer_and_recovery_fit_a_narrow_panel_without_clipping(room, qapp, state, parent_size, stretch):
    arrange(room, state)
    window = ConductorWindow(mode_entries=[(p.key, p.label) for p in CREATOR_PROFILES], initial_mode_key="art", initial_title="Art")
    room.window = window
    window.set_creator_profile(get_creator_profile_by_key("art"))
    room.panel.setParent(window, Qt.WindowType.Dialog)
    window.resize(*parent_size)
    window.show()
    room.panel.resize(400, 440)
    room.panel.show()
    qapp.processEvents()
    for widget in room.panel.findChildren(QWidget):
        font = QFont(widget.font())
        font.setStretch(stretch)
        widget.setFont(font)
    qapp.processEvents()
    panel = room.panel
    assert (window.width(), window.height()) == parent_size
    assert panel.width() == 400
    assert panel.height() <= parent_size[1]
    assert panel._headline.fontMetrics().horizontalAdvance(panel._headline.text()) <= panel._headline.contentsRect().width()
    snapshot = room.host.host_snapshot
    if snapshot.shared:
        assert panel._headline.text().startswith(f"Canvas offered: {snapshot.session_label[:4]}")
        full_label = f"Canvas offered: {snapshot.session_label} at {snapshot.server_label}"
        assert panel._headline.toolTip() == full_label
        assert panel._headline.accessibleDescription() == full_label
    controls = [panel._headline, panel._status, panel._chip, panel.findChild(QLabel, "SharedCanvasHint")]
    controls += [w for w in (panel._invite_input, panel._change_button, panel._quiet) if w.isVisible()]
    for widget in controls:
        assert widget.isVisible()
        assert panel.rect().contains(widget.geometry())
        if isinstance(widget, QLabel):
            assert widget.height() >= widget.heightForWidth(widget.width())
    for index, widget in enumerate(controls):
        for other in controls[index + 1:]:
            assert not widget.geometry().intersects(other.geometry())
    _assert_no_capability_text(panel)
