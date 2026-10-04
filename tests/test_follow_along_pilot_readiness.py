"""First-use meeting sharing and saved Art recovery keep explicit owners."""
import sys

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QScrollArea

from core.art_workspace import make_reference
from core.network_invite import create_invite_link
from tests.test_art_room_controller import drain
from webjam_qt.controllers.application_controller import ApplicationController
from webjam_qt.theme import load_stylesheet
from webjam_qt.windows.simple_settings import SimpleSettingsDialog
from tests import test_art_notes_conversation_journey as notes_journey
from tests.test_art_conversation_link_journey import (
    _button, _drive_room_meeting, _drive_settings, _raise_dialog_failures,
    _room_meeting_field, external_handoffs as external_handoffs,
    saved_personal_settings as saved_personal_settings,
)
from tests.test_art_shared_lesson_journey import (
    qapp as qapp, room as room,
    no_unhandled_qt_slot_errors as no_unhandled_qt_slot_errors,
)
from webjam_qt.invitation_ingress import (
    InvitationSource, conversation_url_from_pasted_invitation,
    parse_invitation_at_ingress,
)

MEETING = "https://studio.webex.com/meet/synthetic-pilot-evidence"
LESSON = "https://www.youtube.com/watch?v=M7lc1UVf-VE&t=90s"


def late_meeting(room, qapp, monkeypatch):
    host = room(role="host", profile="art", configured=False).app
    credentials = host.host_peer.credentials
    link = create_invite_link(
        "192.168.1.20", session_name="Synthetic pilot",
        session_id=credentials.session_id, peer_port=22125,
        invite_token=credentials.invite_token,
    )
    # Same pure shareability seam used by test_art_invitation_flow; no socket.
    monkeypatch.setattr(host, "_host_share_readiness", lambda: SimpleNamespace(shareable=True, address="192.168.1.20"))
    monkeypatch.setattr(host, "_current_invite_url", lambda **kwargs: link)
    clipboard = Mock()
    monkeypatch.setattr(QApplication, "clipboard", lambda: clipboard)
    host._copy_band_invite()
    original = clipboard.setText.call_args.args[0]
    parsed = parse_invitation_at_ingress(original, source=InvitationSource.PASTE)
    assert conversation_url_from_pasted_invitation(original) == ""
    monkeypatch.setattr(notes_journey, "invitation", lambda: parsed)
    guest = room(role="lan", profile="art", configured=False).app
    assert guest._reference_video_identity()[1] == credentials.session_id
    assert guest._session_meeting_url == ""
    for app in (host, guest):
        app._show_webex_conversation()
    assert host.window.webex_embed._change_link_btn.text() == "Add Link"

    def save(dialog):
        dialog._video.setText(MEETING)
        QTest.mouseClick(_button(dialog, "Save"), Qt.MouseButton.LeftButton)

    observed = _drive_settings(monkeypatch, save)
    QTest.mouseClick(host.window.webex_embed._change_link_btn, Qt.MouseButton.LeftButton)
    _raise_dialog_failures(observed, accepted=True)
    qapp.processEvents()
    assert host._effective_meeting_url() == MEETING
    guest._room_participant.lan_guest.poll_once()
    qapp.processEvents()
    for app in (host, guest):
        app._open_reference_video()
        app._reference_video_dialog._watch_lesson_button.click()
    qapp.processEvents()
    return host, guest, clipboard, original



def test_existing_copy_link_and_guest_add_link_fix_without_rejoin(room, qapp, monkeypatch, external_handoffs):
    host, guest, clipboard, _original = late_meeting(room, qapp, monkeypatch)
    assert "Ask the host for the meeting link" in guest.window.webex_embed._mode_label.text()
    assert "Copy Link" in host.window.webex_embed._mode_label.text()
    assert not guest.window.webex_embed._fallback_btn.isEnabled()
    owner = guest._room_participant.lan_guest
    generation = guest._room_participant.generation
    QTest.mouseClick(host.window.webex_embed._copy_link_btn, Qt.MouseButton.LeftButton)
    copied = clipboard.setText.call_args.args[0]
    assert copied == MEETING

    def save(dialog):
        _room_meeting_field(dialog).setText(copied)
        QTest.mouseClick(_button(dialog, "OK"), Qt.MouseButton.LeftButton)

    observed = _drive_room_meeting(monkeypatch, save)
    QTest.mouseClick(guest.window.webex_embed._change_link_btn, Qt.MouseButton.LeftButton)
    _raise_dialog_failures(observed, accepted=True)
    qapp.processEvents()
    assert guest._effective_meeting_url() == host._effective_meeting_url()
    assert "Join Webex" in guest.window.webex_embed._mode_label.text()
    assert "Ask the host for the meeting link" not in guest.window.webex_embed._mode_label.text()
    assert "not a WebJam room invitation" in host.window.flash_message.call_args.args[0]
    assert guest.settings.webex_url == ""
    assert guest.window.webex_embed._fallback_btn.isEnabled()
    assert guest._room_participant.lan_guest is owner
    assert guest._room_participant.generation == generation
    for item in external_handoffs:
        item.assert_not_called()


@pytest.mark.parametrize("hosting", [True, False])
def test_start_session_preserves_continued_art_workspace_for_saved_role(room, qapp, monkeypatch, external_handoffs, hosting):
    from PySide6.QtCore import QTimer
    from core.settings import save_settings
    from tests.test_art_room_controller import arm_lan, drain, invitation
    from webjam_qt.windows.launch_dialog import LaunchDialog

    app = room(role="host", profile="art", configured=False, connect=False).app
    app.settings.host_server_enabled = hosting
    save_settings(app.settings)
    reference = make_reference(LESSON, kind="url", title="Synthetic saved lesson")
    record = app.session_library.library.create("art", "Continued saved Art", art={"version": 1, "references": [reference]})
    assert app.session_library.continue_record(record)
    app.window.show()
    app._refresh_readiness()
    qapp.processEvents()
    assert app.window.session_strip._audio_button.text() == "Start Session"
    app.session_library.show(tab="plan")
    original_dialog = app.session_library.dialog
    original_dialog.art.references.setCurrentRow(0)
    save_status = original_dialog.status.text()
    app.window.flash_message.reset_mock()
    original_dialog.art.use_lesson_button.click()
    qapp.processEvents()
    assert original_dialog.isVisible()
    assert "Start Session" in original_dialog.art.status.text()
    assert ("to host" if hosting else "host's invitation") in original_dialog.art.status.text()
    assert original_dialog.status.text() == save_status
    assert app.window.flash_message.call_count == 1
    assert app._reference_video_identity() == ("", "", "")
    assert not app.follow_along.panel.isVisibleTo(app.window)
    original_dialog.reject()
    qapp.processEvents()
    entered = []
    if not hosting:
        invite = invitation()
        arm_lan(monkeypatch, invite)
        link = create_invite_link(invite.host, session_name=invite.session_name,
            session_id=invite.session_id, peer_port=invite.peer_port, invite_token=invite.invite_token)
        original_exec = LaunchDialog.exec

        def join_modal(dialog):
            def submit():
                entered.append(dialog._join_button_primary.text())
                assert dialog.accept_invite(link)
            QTimer.singleShot(0, submit)
            return original_exec(dialog)

        monkeypatch.setattr(LaunchDialog, "exec", join_modal)
    app.window.session_strip._audio_button.click()
    if not hosting:
        owner = app._room_participant.lan_guest
        assert owner is not None
        owner.poll_once()
        drain(qapp, lambda: app._room_participant.state.value == "connected")
    qapp.processEvents()
    assert app.session_library.current.id == record.id
    app.session_library.show(tab="plan")
    dialog = app.session_library.dialog
    dialog.art.references.setCurrentRow(0)
    qapp.processEvents()
    dialog.art.use_lesson_button.click()
    qapp.processEvents()
    assert app._reference_video_identity()[0] == ("host" if hosting else "guest")
    assert app.follow_along.panel.isVisibleTo(app.window)
    assert app.follow_along.panel.lesson_url == LESSON
    assert app.session_library.library.load(record.id).art["references"] == [reference]
    for handoff in external_handoffs:
        handoff.assert_not_called()


@pytest.fixture
def isolate_machine(monkeypatch):
    clip = SimpleNamespace(value='sentinel')
    clip.setText = lambda value: setattr(clip, 'value', value)
    clip.text = lambda: clip.value
    monkeypatch.setattr(QApplication, 'clipboard', lambda: clip)
    monkeypatch.setitem(sys.modules, 'sounddevice', None)
    monkeypatch.setattr('webjam_qt.platform_permissions.microphone_permission_status', lambda: 'unavailable')
    guards = []
    for target in ('subprocess.Popen', 'socket.create_connection', 'webbrowser.open',
                   'PySide6.QtGui.QDesktopServices.openUrl', 'webex_integration.open_webex_meeting',
                   'core.audio_engine.RealAudioEngine.start'):
        guard = Mock(side_effect=AssertionError('No process, device, or media action permitted'))
        monkeypatch.setattr(target, guard)
        guards.append(guard)
    yield clip
    for guard in guards:
        guard.assert_not_called()



def test_host_server_failure_still_allows_meeting_handoff(room, qapp, monkeypatch, isolate_machine):
    pair = room(role='host', profile='music', configured=False, connect=False)
    app = pair.app
    for timer in app.findChildren(QTimer):
        timer.stop()
    monkeypatch.setattr(app.bridge, 'ensure_hosted_server', Mock(return_value=(False, 'unavailable')))
    monkeypatch.setattr(app.bridge, 'hosted_server_alive', lambda: False)
    monkeypatch.setattr(app, '_start_hosted_server_for_startup',
                        ApplicationController._start_hosted_server_for_startup.__get__(app))
    app.window.resize(960, 760)
    app.window.show()
    assert app.begin_startup_journey()
    drain(qapp, lambda: app._startup_attempt['phase'] == 'failed')
    assert app._startup_attempt['failure_reason'] == 'host_start_failed'
    app.window.session_strip._play_along_button.click()
    assert app.follow_along._choice.isVisible()
    app.follow_along._choice.buttons['lesson'].click()
    qapp.processEvents()
    panel = app.window.webex_embed
    assert app.follow_along.music_practice_visible()
    readiness = app._host_share_readiness()
    assert not readiness.shareable
    assert app._current_invite_url(readiness=readiness) == ''
    app._copy_band_invite()
    assert isolate_machine.value == 'sentinel'
    assert not panel._fallback_btn.isEnabled()
    assert not panel._copy_link_btn.isEnabled()
    assert panel._change_link_btn.isEnabled()
    def accept_meeting(wizard):
        wizard._video.setText(MEETING)
        wizard._save_and_accept()
        return wizard.result()
    monkeypatch.setattr(SimpleSettingsDialog, 'exec', accept_meeting)
    panel._change_link_btn.click()
    assert app._effective_meeting_url() == MEETING
    assert app.follow_along.music_practice_visible()
    assert panel._copy_link_btn.isEnabled()
    panel._copy_link_btn.click()
    assert isolate_machine.value == MEETING
    open_meeting = Mock(return_value=True)
    monkeypatch.setattr(app.bridge, 'launch_webex', open_meeting)
    assert panel._fallback_btn.isEnabled()
    panel._fallback_btn.click()
    open_meeting.assert_called_once_with(manual=True, meeting_url=MEETING)
    assert app._startup_attempt['phase'] == 'failed'
    assert app._startup_attempt['failure_reason'] == 'host_start_failed'
    app._launch_native_jamulus_for_startup.assert_not_called()
    assert 'video practice does not require a WebJam room' in panel._sound_tips.text()
    assert any(
        'not a WebJam room invitation' in call.args[0] for call in app.window.flash_message.call_args_list
    )


@pytest.mark.parametrize("font_px", [13, 22])
def test_saved_art_recovery_is_visible_and_preserves_failed_save(
        room, qapp, monkeypatch, external_handoffs, font_px):
    app = room(role="host", profile="art", configured=False, connect=False).app
    reference = make_reference(LESSON, kind="url", title="Saved lesson")
    record = app.session_library.library.create(
        "art", "Saved paint-along", art={"version": 1, "references": [reference]})
    assert app.session_library.continue_record(record)
    app.window.show()
    app.session_library.show(tab="plan")
    dialog = app.session_library.dialog
    dialog.art.references.setCurrentRow(0)
    draft = "My unsaved painting notes must stay here."
    dialog.art.brief.setPlainText(draft)
    dialog.timer.stop()
    ordinary_save = dialog._save_record
    monkeypatch.setattr(dialog, "_save_record", Mock(side_effect=OSError("Synthetic save failure")))
    assert not dialog.save_current()
    save_status = dialog.status.text()
    dialog.setStyleSheet(load_stylesheet() + f"QWidget {{ font-size: {font_px}px; }}")
    dialog.resize(760, 640)
    dialog.activateWindow()
    for _ in range(5):
        qapp.processEvents()
    button = dialog.art.use_lesson_button
    button.setFocus(Qt.FocusReason.TabFocusReason)
    dialog._reveal_focus(None, button)
    for _ in range(5):
        qapp.processEvents()
    assert not button.visibleRegion().isEmpty()
    QTest.keyClick(button, Qt.Key.Key_Space)
    for _ in range(5):
        qapp.processEvents()
    label = dialog.art.status
    assert "Start Session" in label.text()
    # Intersect with every viewport: isVisible() alone accepts off-screen text.
    visible = label.rect()
    parent = label.parentWidget()
    while parent is not None and parent is not dialog:
        if isinstance(parent, QScrollArea):
            viewport = parent.viewport()
            visible = visible.intersected(viewport.rect().translated(label.mapFrom(viewport, viewport.rect().topLeft())))
        parent = parent.parentWidget()
    monkeypatch.setattr(dialog, "_save_record", ordinary_save)
    assert visible == label.rect()
    assert label.height() >= label.heightForWidth(label.width())
    assert dialog.status.text() == save_status
    assert dialog.art.brief.toPlainText() == draft
    assert dialog._dirty
    assert app.session_library.current.id == record.id
    assert app._reference_video_identity() == ("", "", "")
    for handoff in external_handoffs:
        handoff.assert_not_called()
