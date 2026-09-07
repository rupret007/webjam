"""Paint along timeline gestures commit exact host intent, never render intent."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QStyle, QStyleFactory, QStyleOptionSlider
from shiboken6 import isValid

from core.reference_video import (
    ReferenceVideoFollowState,
    ReferenceVideoSnapshot,
    ReferenceVideoState,
)
from tests.test_reference_video_coordinator import (
    FakeHostPeer,
    SESSION_ID,
    SESSION_KEY,
    make_coordinator,
    write_video,
)
from tests.test_reference_video_ui import _follow, _shared, qapp as _qapp_fixture
from webjam_qt.windows.reference_video import ReferenceVideoDialog

qapp = _qapp_fixture


def _settle(qapp):
    for _ in range(3):
        qapp.processEvents()


@pytest.fixture
def panel(qapp):
    made = []

    def create(*, hosting=True):
        dialog = ReferenceVideoDialog(hosting=hosting)
        seeks = []
        dialog.seek_requested.connect(seeks.append)
        if hosting:
            dialog.set_host_snapshot(_shared(position_s=120.0))
        else:
            dialog.set_follow_snapshot(_follow(
                ReferenceVideoFollowState.FOLLOWING, target_position_s=120.0,
            ))
        dialog.resize(960, 700)
        dialog.show()
        dialog.activateWindow()
        _settle(qapp)
        made.append(dialog)
        return SimpleNamespace(dialog=dialog, slider=dialog._position, seeks=seeks)

    yield create
    for dialog in reversed(made):
        dialog.close()
        dialog.deleteLater()
        QCoreApplication.sendPostedEvents(dialog, QEvent.Type.DeferredDelete)
        assert not isValid(dialog)
    _settle(qapp)


def _wheel(slider):
    center = slider.rect().center()
    event = QWheelEvent(
        QPointF(center), QPointF(slider.mapToGlobal(center)), QPoint(), QPoint(0, 120),
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase, False,
    )
    QApplication.sendEvent(slider, event)


def _start_drag(rig, qapp):
    slider = rig.slider
    option = QStyleOptionSlider()
    slider.initStyleOption(option)
    handle = slider.style().subControlRect(
        QStyle.ComplexControl.CC_Slider, option,
        QStyle.SubControl.SC_SliderHandle, slider,
    )
    QTest.mousePress(slider, Qt.MouseButton.LeftButton, pos=handle.center())
    assert slider.isSliderDown()
    target = QPoint(slider.width() * 3 // 4, handle.center().y())
    QTest.mouseMove(slider, target)
    _settle(qapp)
    assert slider.sliderPosition() != 120
    assert rig.seeks == []
    return target


@pytest.mark.parametrize(("key", "expected"), [
    (Qt.Key.Key_Right, 121),
    (Qt.Key.Key_Left, 119),
    (Qt.Key.Key_Home, 0),
    (Qt.Key.Key_End, 600),
    (Qt.Key.Key_PageUp, 130),
    (Qt.Key.Key_PageDown, 110),
])
def test_host_keyboard_commits_the_new_slider_position_once(panel, qapp, key, expected):
    rig = panel()
    rig.slider.setFocus(Qt.FocusReason.TabFocusReason)
    _settle(qapp)
    assert rig.slider.hasFocus() and rig.slider.isEnabled()
    QTest.keyClick(rig.slider, key)
    _settle(qapp)
    assert rig.slider.sliderPosition() == expected
    assert rig.seeks == [float(expected)]
    assert rig.seeks[0] == float(rig.slider.sliderPosition())


def test_host_wheel_commits_the_position_it_visibly_changes_once(panel, qapp):
    rig = panel()
    before = rig.slider.sliderPosition()
    _wheel(rig.slider)
    _settle(qapp)
    assert rig.slider.sliderPosition() != before
    assert rig.seeks == [float(rig.slider.sliderPosition())]


def test_passive_snapshot_rendering_never_commits_a_seek(panel, qapp):
    rig = panel()
    for position in (0.0, 17.0, 83.0, 400.0):
        rig.dialog.set_host_snapshot(_shared(
            state=ReferenceVideoState.PLAYING, position_s=position,
        ))
        _settle(qapp)
        assert rig.slider.value() == int(position)
    assert rig.seeks == []


def test_guest_keyboard_and_wheel_have_no_host_transport_authority(panel, qapp):
    rig = panel(hosting=False)
    assert not rig.slider.isEnabled()
    QTest.keyClick(rig.slider, Qt.Key.Key_End)
    _wheel(rig.slider)
    _settle(qapp)
    assert rig.seeks == []


def test_mouse_drag_preserves_in_progress_position_and_commits_only_on_release(panel, qapp):
    rig = panel()
    target = _start_drag(rig, qapp)
    sought = rig.slider.sliderPosition()
    rig.dialog.set_host_snapshot(_shared(
        state=ReferenceVideoState.PLAYING, position_s=125.0,
    ))
    _settle(qapp)
    assert rig.slider.sliderPosition() == sought
    assert rig.seeks == []
    QTest.mouseRelease(rig.slider, Qt.MouseButton.LeftButton, pos=target)
    _settle(qapp)
    assert not rig.slider.isSliderDown()
    assert rig.seeks == [float(sought)]


@pytest.mark.parametrize("unavailable", ["no_video", "zero_duration"])
def test_a_timeline_without_seekable_video_is_disabled(panel, qapp, unavailable):
    rig = panel()
    snapshot = ReferenceVideoSnapshot() if unavailable == "no_video" else _shared(duration_s=0.0)
    rig.dialog.set_host_snapshot(snapshot)
    _settle(qapp)
    assert not rig.slider.isEnabled()
    QTest.keyClick(rig.slider, Qt.Key.Key_End)
    _wheel(rig.slider)
    _settle(qapp)
    assert rig.seeks == []


@pytest.mark.parametrize("change", ["replacement", "failure_then_recovery"])
def test_changed_video_authority_cancels_the_mouse_gesture_before_release(panel, qapp, change):
    rig = panel()
    target = _start_drag(rig, qapp)
    if change == "failure_then_recovery":
        rig.dialog.set_host_snapshot(ReferenceVideoSnapshot(
            state=ReferenceVideoState.FAILED, error="The process video is unavailable.",
        ))
        rig.dialog.set_host_snapshot(_shared(position_s=20.0))
    else:
        rig.dialog.set_host_snapshot(_shared(identity_digest="b" * 64, position_s=20.0))
    _settle(qapp)
    QTest.mouseRelease(rig.slider, Qt.MouseButton.LeftButton, pos=target)
    _settle(qapp)
    assert rig.seeks == []
    assert rig.slider.value() == 20


@pytest.mark.parametrize("state", [
    ReferenceVideoState.READY, ReferenceVideoState.PLAYING, ReferenceVideoState.PAUSED,
])
def test_keyboard_seek_reaches_existing_coordinator_player_and_peer_once(
    panel, qapp, tmp_path, state,
):
    rig = panel()
    peer = FakeHostPeer()
    coordinator, players, _, _ = make_coordinator(peer=peer)
    coordinator._on_host_snapshot = rig.dialog.set_host_snapshot
    try:
        coordinator.begin_host(session_id=SESSION_ID, session_key=SESSION_KEY)
        coordinator.share(str(write_video(tmp_path / "synthetic.mp4", b"synthetic fixture bytes")))
        if state in {ReferenceVideoState.PLAYING, ReferenceVideoState.PAUSED}:
            coordinator.play()
        if state is ReferenceVideoState.PAUSED:
            coordinator.pause()
        coordinator.seek(100.0)
        assert rig.dialog._clock.text() == "1:40 / 5:00"
        assert coordinator.host_snapshot.state is state
        assert rig.seeks == []
        rig.dialog.seek_requested.connect(coordinator.seek)
        players[0].seeks.clear()
        peer.published.clear()
        rig.slider.setFocus(Qt.FocusReason.TabFocusReason)
        _settle(qapp)
        QTest.keyClick(rig.slider, Qt.Key.Key_Right)
        _settle(qapp)
        assert rig.slider.sliderPosition() == 101
        assert rig.seeks == [101.0]
        assert players[0].seeks == [101.0]
        assert len(peer.published) == 1 and peer.published[0]["position_s"] == 101.0
        assert peer.published[0]["state"] == state.value
        assert players[0].muted and players[0].state == state.value
        assert coordinator.host_snapshot.state is state
        assert rig.dialog._clock.text() == "1:41 / 5:00"
        assert rig.dialog._status.text() == {
            ReferenceVideoState.READY: "Ready.",
            ReferenceVideoState.PLAYING: "Playing.",
            ReferenceVideoState.PAUSED: "Paused.",
        }[state]
    finally:
        coordinator.end()


def test_hiding_the_panel_cancels_a_drag_and_reopening_keeps_keyboard_usable(panel, qapp):
    rig = panel()
    target = _start_drag(rig, qapp)
    rig.dialog.hide()
    _settle(qapp)
    assert rig.seeks == []
    rig.dialog.set_host_snapshot(_shared(position_s=25.0))
    rig.dialog.show()
    rig.dialog.activateWindow()
    _settle(qapp)
    QTest.mouseRelease(rig.slider, Qt.MouseButton.LeftButton, pos=target)
    _settle(qapp)
    assert rig.seeks == []
    assert rig.slider.sliderPosition() == 25
    rig.slider.setFocus(Qt.FocusReason.TabFocusReason)
    QTest.keyClick(rig.slider, Qt.Key.Key_Right)
    _settle(qapp)
    assert rig.seeks == [26.0]


def test_held_groove_repeat_cannot_seek_a_replacement_video(panel, qapp):
    rig = panel()
    slider = rig.slider
    # Fusion exposes Qt's page-step groove gesture on every test platform;
    # macOS's native style may instead make an absolute jump with sliderDown.
    # Change this slider only and restore its style before fixture cleanup.
    previous_style = slider.style()
    groove_style = QStyleFactory.create("Fusion")
    assert groove_style is not None
    slider.setStyle(groove_style)
    _settle(qapp)
    option = QStyleOptionSlider()
    slider.initStyleOption(option)
    handle = slider.style().subControlRect(
        QStyle.ComplexControl.CC_Slider, option,
        QStyle.SubControl.SC_SliderHandle, slider,
    )
    groove = QPoint(slider.width() * 3 // 4, handle.center().y())
    assert not handle.contains(groove)
    held = False
    try:
        QTest.mousePress(slider, Qt.MouseButton.LeftButton, pos=groove)
        held = True
        assert not slider.isSliderDown()
        assert slider.sliderPosition() != 120
        assert rig.seeks == []
        rig.dialog.set_host_snapshot(_shared(identity_digest="b" * 64, position_s=20.0))
        assert slider.sliderPosition() == 20
        # Wait through the real default page-repeat threshold once. No fake
        # action signal is emitted: the old Qt mouse gesture owns this timer.
        QTest.qWait(650)
        QTest.mouseRelease(slider, Qt.MouseButton.LeftButton, pos=groove)
        held = False
        _settle(qapp)
        assert rig.seeks == []
        assert slider.sliderPosition() == 20
    finally:
        if held:
            QTest.mouseRelease(slider, Qt.MouseButton.LeftButton, pos=groove)
        slider.setStyle(previous_style)
        groove_style.deleteLater()
        _settle(qapp)


def test_unavailable_slider_release_elsewhere_does_not_block_recovered_keyboard(panel, qapp):
    rig = panel()
    _start_drag(rig, qapp)
    rig.dialog.set_host_snapshot(ReferenceVideoSnapshot(
        state=ReferenceVideoState.FAILED, error="The process video is unavailable.",
    ))
    _settle(qapp)
    assert not rig.slider.isEnabled() and not rig.slider.isVisible()
    # The now-hidden slider cannot receive the real user's release.
    QTest.mouseRelease(rig.dialog, Qt.MouseButton.LeftButton, pos=QPoint(5, 5))
    rig.dialog.set_host_snapshot(_shared(position_s=20.0))
    rig.slider.setFocus(Qt.FocusReason.TabFocusReason)
    _settle(qapp)
    QTest.keyClick(rig.slider, Qt.Key.Key_Right)
    _settle(qapp)
    assert rig.slider.value() == 21
    assert rig.seeks == [21.0]


def test_fresh_keyboard_input_recovers_when_the_pointer_release_was_elsewhere(panel, qapp):
    rig = panel()
    _start_drag(rig, qapp)
    pending = rig.slider.value()
    QTest.mouseRelease(rig.dialog, Qt.MouseButton.LeftButton, pos=QPoint(5, 5))
    QTest.keyClick(rig.slider, Qt.Key.Key_Right)
    _settle(qapp)
    assert rig.slider.value() == pending + 1
    assert rig.seeks == [float(pending + 1)]
