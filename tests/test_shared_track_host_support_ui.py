"""Shared Track support directs musicians to actions this computer can perform."""

from dataclasses import replace
from types import SimpleNamespace
import sys
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog

from core.creative_modes import get_creator_profile_by_key
from core.reference_track import (
    ReferenceTrackSnapshot,
    ReferenceTrackState,
    reference_track_host_backend_unavailable,
)
from services.reference_track_backend import (
    MacOSBlackHoleReferenceBackend,
    _UnavailableReferenceBackend,
)
from tests.test_reference_track_backend import _LiveRouteProbe, _SoundDevice, _device, _scan
from webjam_qt.widgets.session_strip import SessionStrip
from webjam_qt.theme import load_stylesheet
from webjam_qt.windows.reference_track import ReferenceTrackDialog, ReferenceTrackPrimaryGate


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def no_slot_errors_or_external_actions(monkeypatch):
    errors, external = [], Mock()
    monkeypatch.setattr(sys, "excepthook", lambda kind, error, tb: errors.append(kind.__name__))
    monkeypatch.setattr("PySide6.QtGui.QDesktopServices.openUrl", external)
    yield external
    assert errors == []
    external.assert_not_called()


def _snapshot(capability, *, loaded=True, cleanup=False):
    return ReferenceTrackSnapshot(
        state=ReferenceTrackState.FAILED if cleanup else ReferenceTrackState.READY
        if loaded else ReferenceTrackState.UNAVAILABLE,
        capability=capability,
        source_name="Rehearsal reference.wav" if loaded else "",
        duration_s=60 if loaded else 0,
        source_format="WAV" if loaded else "",
        source_samplerate=48000 if loaded else 0,
        source_channels=2 if loaded else 0,
        cleanup_pending=cleanup,
    )


def _mac_capability(*, channels=16, missing=False, live_error=""):
    name = f"BlackHole {channels}ch"
    sound = _SoundDevice(name=name, channels=channels)
    live = _LiveRouteProbe(capability_error=live_error)
    backend = MacOSBlackHoleReferenceBackend(
        platform="darwin", scanner=lambda: _scan() if missing else _scan(
            _device(name=name, channels=channels),
        ),
        sounddevice_module=sound, process_route_probe=live,
    )
    capability = backend.capability()
    assert sound.streams == [] and live.calls == []
    return capability


@pytest.fixture
def panel(qapp):
    dialog = ReferenceTrackDialog()
    dialog.set_primary_gate(ReferenceTrackPrimaryGate.READY)
    events = []
    for name in ("play_requested", "restart_requested", "stop_requested", "remove_requested",
                 "recheck_route_requested"):
        getattr(dialog, name).connect(lambda action=name: events.append(action))
    dialog.load_requested.connect(lambda path: events.append(("load", path)))
    yield dialog, events
    dialog.close()
    dialog.deleteLater()
    qapp.processEvents()


@pytest.mark.parametrize("platform", ["win32", "linux", "unknown"])
@pytest.mark.parametrize("loaded", [False, True])
def test_unavailable_sender_keeps_inspection_and_a_safe_keyboard_return(
    panel, qapp, platform, loaded,
):
    dialog, events = panel
    snapshot = _snapshot(_UnavailableReferenceBackend(platform).capability(), loaded=loaded)
    dialog.set_snapshot(snapshot)
    dialog.show()
    qapp.processEvents()

    assert "unavailable" in dialog._status.text().lower()
    assert "without a track" in dialog._route_guidance.text()
    assert "Mac as host" in dialog._route_guidance.text()
    assert "Recheck" not in dialog._route_guidance.text()
    assert "set up" not in dialog._route_guidance.text()
    assert not dialog._recheck_route.isVisible()
    assert not dialog._recheck_route.isEnabled()
    assert not dialog._blackhole_setup.isVisible()
    assert not dialog._blackhole_setup.isEnabled()
    assert not dialog._play.isEnabled() and not dialog._restart.isEnabled()
    assert dialog._load.isEnabled()
    assert dialog._remove.isEnabled() is loaded
    assert dialog._done.text() == "Back to rehearsal"
    assert QApplication.focusWidget() is dialog._done

    # Neither a disabled click nor a callback queued before the capability
    # changed can turn unavailable hosting into route setup or an external URL.
    dialog._play.click()
    dialog._recheck_route.click()
    dialog._emit_recheck_route()
    dialog._open_blackhole_setup()
    QTest.keyClick(dialog._done, Qt.Key.Key_Space)
    qapp.processEvents()
    assert not dialog.isVisible()
    assert dialog._snapshot is snapshot
    assert dialog._source.text() == (snapshot.source_name or "No song loaded")
    assert events == []


@pytest.mark.parametrize("platform", ["win32", "linux"])
def test_unsupported_host_can_choose_inspection_or_explicit_removal(
    panel, qapp, monkeypatch, tmp_path, platform,
):
    dialog, events = panel
    capability = _UnavailableReferenceBackend(platform).capability()
    snapshot = _snapshot(capability, loaded=False)
    dialog.set_snapshot(snapshot)
    dialog.show()
    qapp.processEvents()
    selected = str(tmp_path / "reference.wav")
    picker = Mock(return_value=(selected, "Audio files"))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", picker)
    QTest.mouseClick(dialog._load, Qt.MouseButton.LeftButton)
    assert events == [("load", selected)]
    picker.assert_called_once()
    # The widget emits an inspection intent; the source controller owns file
    # validation. Receiving that loaded snapshot still cannot start playback.
    snapshot = _snapshot(capability)
    dialog.set_snapshot(snapshot)
    assert dialog._snapshot is snapshot
    assert not dialog._play.isEnabled()
    QTest.mouseClick(dialog._remove, Qt.MouseButton.LeftButton)
    assert events == [("load", selected), "remove_requested"]


@pytest.mark.parametrize("channels", [16, 64])
@pytest.mark.parametrize("gate", list(ReferenceTrackPrimaryGate))
def test_supported_mac_route_eligibility_preserves_every_primary_gate(panel, channels, gate):
    dialog, events = panel
    missing = _snapshot(_mac_capability(missing=True))
    dialog.set_primary_gate(gate)
    dialog.set_snapshot(missing)
    assert not dialog._play.isEnabled()
    assert not dialog._recheck_route.isHidden()
    assert not dialog._blackhole_setup.isHidden()
    assert "Install it, then choose Recheck Route" in dialog._route_guidance.text()

    ready = _snapshot(_mac_capability(channels=channels))
    assert ready.capability.available
    dialog.set_snapshot(ready)
    assert dialog._done.text() == "Done"
    assert dialog._play.isEnabled() is (gate is ReferenceTrackPrimaryGate.READY)
    assert not dialog._blackhole_setup.isVisible()
    dialog._play.click()
    assert events == (["play_requested"] if gate is ReferenceTrackPrimaryGate.READY else [])


@pytest.mark.parametrize("platform", ["win32", "linux"])
def test_route_and_source_refreshes_cannot_restore_unsupported_setup(panel, qapp, platform):
    dialog, events = panel
    dialog.set_snapshot(_snapshot(_mac_capability(missing=True)))
    dialog.show()
    dialog._recheck_route.setFocus()
    qapp.processEvents()
    assert QApplication.focusWidget() is dialog._recheck_route
    unsupported = _snapshot(_UnavailableReferenceBackend(platform).capability())
    dialog.set_snapshot(unsupported)
    qapp.processEvents()
    assert QApplication.focusWidget() is dialog._done
    for checking, loading in ((True, False), (False, True), (False, False)):
        dialog.set_route_checking(checking)
        dialog.set_source_load_queued(loading)
        dialog.set_snapshot(unsupported)
        assert not dialog._recheck_route.isVisible()
        assert not dialog._recheck_route.isEnabled()
        assert not dialog._play.isEnabled()
        assert dialog._load.isEnabled() is (not loading)
        dialog._emit_recheck_route()
    assert events == []


@pytest.mark.parametrize("platform", ["win32", "linux"])
@pytest.mark.parametrize("gate", [ReferenceTrackPrimaryGate.READY, ReferenceTrackPrimaryGate.SESSION_CHANGING])
def test_cleanup_retains_its_existing_owner_and_action(panel, platform, gate):
    dialog, events = panel
    snapshot = _snapshot(_UnavailableReferenceBackend(platform).capability(), cleanup=True)
    dialog.set_primary_gate(gate)
    dialog.set_snapshot(snapshot)
    assert "cleanup is still pending" in dialog._status.text()
    assert "without a track" not in dialog._route_guidance.text()
    assert not dialog._load.isEnabled()
    assert not dialog._remove.isEnabled()
    assert not dialog._play.isEnabled()
    assert not dialog._recheck_route.isEnabled()
    assert dialog._done.text() == "Done"
    dialog._stop.click()
    assert events == (["stop_requested"] if gate is ReferenceTrackPrimaryGate.READY else [])


@pytest.mark.parametrize("capability", [
    None,
    SimpleNamespace(reason_code="unavailable"),
    SimpleNamespace(reason_code="live_route_unavailable"),
    SimpleNamespace(reason_code="not_windows_backend_unavailable"),
    SimpleNamespace(reason_code="ready", detail="linux_backend_unavailable"),
])
def test_unknown_or_guest_capability_is_not_reinterpreted_as_missing_sender(capability):
    assert not reference_track_host_backend_unavailable(capability)


def test_unavailable_live_mac_proof_keeps_its_actual_reason(panel):
    dialog, _ = panel
    capability = _mac_capability(live_error="This Mac cannot prove the current input and output route.")
    assert capability.reason_code == "live_route_unavailable"
    assert not reference_track_host_backend_unavailable(capability)
    dialog.set_snapshot(_snapshot(capability))
    assert capability.detail in dialog._route.text()
    assert "setup above is complete" not in dialog._route_guidance.text()
    assert "Install" not in dialog._route_guidance.text()
    assert not dialog._play.isEnabled()


@pytest.fixture
def deck(qapp):
    strip = SessionStrip(mode_entries=[("music_jam", "Music")], initial_mode_key="music_jam")
    strip.set_creator_profile(get_creator_profile_by_key("music"))
    strip.set_reference_track_available(True)
    strip.set_tools_enabled(True)
    events = []
    strip.tool_requested.connect(lambda name: events.append(("tool", name)))
    strip.shared_track_play_requested.connect(lambda: events.append(("play",)))
    strip.shared_track_pause_requested.connect(lambda: events.append(("pause",)))
    strip.show()
    yield strip, events
    strip.close()
    strip.deleteLater()
    qapp.processEvents()


@pytest.mark.parametrize("platform", ["win32", "linux", "unknown"])
def test_deck_offers_support_options_without_requesting_playback(deck, qapp, platform):
    strip, events = deck
    snapshot = _snapshot(_UnavailableReferenceBackend(platform).capability())
    strip.set_shared_track_snapshot(snapshot)
    qapp.processEvents()
    assert "unavailable" in strip._reference_track_button.text().lower()
    assert "set up" not in strip._reference_track_button.toolTip()
    assert "Recheck" not in strip._reference_track_button.toolTip()
    assert "hosting options" in strip._reference_track_button.toolTip()
    QTest.mouseClick(strip._reference_track_button, Qt.MouseButton.LeftButton)
    QTest.mouseClick(strip._shared_track_transport, Qt.MouseButton.LeftButton)
    assert events == [("tool", "reference_track"), ("tool", "reference_track")]
    assert strip._shared_track_last_snapshot is snapshot


def test_guest_projection_keeps_the_host_transport_without_sender_authority(deck):
    strip, events = deck
    strip.set_reference_track_available(False)
    snapshot = SimpleNamespace(
        state="playing", source_name="Reference.wav", duration_s=60,
        position_s=4, cleanup_pending=False, count_in_active=False,
    )
    strip.set_shared_track_snapshot(snapshot)
    assert strip._shared_track_state.text() == "Playing"
    assert strip._shared_track_transport.isHidden()
    strip._emit_shared_track_transport()
    strip._emit_shared_track_next_step()
    assert events == []


def test_source_name_cannot_override_a_recoverable_mac_setup(deck):
    strip, _ = deck
    snapshot = replace(_snapshot(_mac_capability(missing=True)), source_name="windows_backend_unavailable.wav")
    strip.set_shared_track_snapshot(snapshot)
    assert strip._reference_track_button.text() == "Set up the audio device"
    assert "Recheck Route" in strip._reference_track_button.toolTip()


def test_delay_guidance_preserves_per_listener_uncertainty_and_recording_consequence(panel):
    dialog, _ = panel
    guidance = dialog._safety.text()
    assert "device" in guidance and "network" in guidance
    assert "does not remove latency" in guidance
    assert "recorded separately" in guidance
    assert "same delay" not in guidance
    assert "not a click track" not in guidance


@pytest.mark.parametrize("platform", ["win32", "linux", "unknown"])
@pytest.mark.parametrize("loaded", [False, True])
@pytest.mark.parametrize("width,height", [(500, 500), (620, 540), (760, 540)])
def test_compact_support_explanation_and_focused_return_are_visible_together(
    panel, qapp, platform, loaded, width, height,
):
    dialog, events = panel
    snapshot = _snapshot(_UnavailableReferenceBackend(platform).capability(), loaded=loaded)
    dialog.setStyleSheet(load_stylesheet())
    dialog.set_snapshot(snapshot)
    dialog.resize(width, height)
    dialog.show()
    dialog.activateWindow()
    qapp.processEvents()
    viewport = dialog._scroll_area.viewport()
    assert dialog.width() <= width and dialog.height() <= height
    assert dialog._scroll_area.horizontalScrollBar().maximum() == 0
    assert dialog._scroll_area.verticalScrollBar().value() == 0
    for widget in (dialog._status, dialog._route, dialog._route_guidance, dialog._done):
        bounds = QRect(widget.mapTo(viewport, QPoint()), widget.size())
        assert viewport.rect().contains(bounds), (widget.objectName(), bounds)
        assert widget.isVisibleTo(dialog)
        if widget is not dialog._done:
            assert widget.height() >= widget.heightForWidth(widget.width())
    assert QApplication.focusWidget() is dialog._done
    assert dialog._done.text() == "Back to rehearsal"
    QTest.keyClick(dialog._done, Qt.Key.Key_Space)
    assert not dialog.isVisible()
    assert dialog._snapshot is snapshot
    assert events == []


def test_support_return_reuses_one_button_and_restores_the_normal_cleanup_footer(panel, qapp):
    dialog, events = panel
    dialog.setStyleSheet(load_stylesheet())
    ready = _snapshot(_mac_capability())
    dialog.set_snapshot(ready)
    original_parent = dialog._done.parentWidget()
    button = dialog._done
    dialog.resize(500, 500)
    dialog.show()
    qapp.processEvents()
    unavailable = _snapshot(_UnavailableReferenceBackend("win32").capability())
    dialog.set_snapshot(unavailable)
    dialog._done.setFocus()
    qapp.processEvents()
    for _ in range(3):
        dialog.set_snapshot(unavailable)
        qapp.processEvents()
        assert dialog._done is button
        assert dialog._support_footer_layout.indexOf(button) >= 0
        assert dialog._done_footer.indexOf(button) == -1
        assert QApplication.focusWidget() is button
    cleanup = replace(unavailable, state=ReferenceTrackState.FAILED, cleanup_pending=True)
    dialog.set_snapshot(cleanup)
    qapp.processEvents()
    assert dialog._done is button
    assert dialog._done.parentWidget() is original_parent
    assert dialog._done_footer.indexOf(button) >= 0
    assert dialog._support_footer_layout.indexOf(button) == -1
    assert dialog._support_footer.isHidden()
    assert button.text() == "Done"
    assert dialog._stop.isEnabled()
    assert not dialog._remove.isEnabled()
    dialog.set_snapshot(ready)
    assert dialog._done is button
    assert dialog._done_footer.indexOf(button) >= 0
    assert dialog._play.isEnabled()
    assert events == []
