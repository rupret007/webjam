"""Saved-work entry restores the selected workspace without authorizing a jam."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QPushButton

from core.network_invite import create_invite_link, parse_invite_link
from core.session_library import SessionLibrary
from core import settings as settings_module
from core.settings import AppSettings, load_settings, save_settings
from core.take_library import load_take
from core.take_review import take_source_identity
from webjam_qt import app as app_module
from webjam_qt.controllers.application_controller import ApplicationController
from webjam_qt.controllers import session_library as library_module
from webjam_qt.controllers import session_persistence as persistence_module
from webjam_qt.controllers.recording_coordinator import RecordingCoordinator
from webjam_qt.windows.launch_dialog import LaunchDialog
from webjam_qt.windows.session_library import SessionLibraryDialog


class _Signals(QObject):
    aboutToQuit = Signal()
    invitation_received = Signal(object)
    invitation_error = Signal(str)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def bootstrap(qapp, tmp_path, monkeypatch):
    """Keep real window/controller restoration; stop at external startup owners."""
    home = tmp_path / "persistence"
    home.mkdir()
    monkeypatch.setattr(persistence_module, "_persistence_home", lambda: home)
    library = SessionLibrary(home / ".webjam_sessions")
    monkeypatch.setattr(library_module, "default_session_library", lambda: library)
    monkeypatch.setattr(settings_module, "hosted_server_recordings_dir", lambda: tmp_path / "takes")
    monkeypatch.setattr(settings_module, "hosted_server_secret_path", lambda: tmp_path / "host.secret")
    settings = AppSettings(
        config_file=str(tmp_path / "settings.json"),
        last_creator_profile_key="music",
        jamulus_server="saved-band.example",
        host_server_enabled=True,
        takes_directory=str(tmp_path / "takes"),
    )
    save_settings(settings)
    settings = load_settings(settings.config_file)
    signals = _Signals()
    loop = MagicMock(spec=app_module.WebJamApplication)
    loop.aboutToQuit = signals.aboutToQuit
    loop.invitation_received = signals.invitation_received
    loop.invitation_error = signals.invitation_error
    loop._pending_invitation = None
    loop._pending_invitation_error = ""
    # Exercise the application's real mailbox semantics without constructing
    # a second QApplication in pytest's existing native Qt process.
    for name in (
        "take_pending_invitation", "pending_invitation", "invitation_is_pending",
        "acknowledge_invitation", "take_pending_invitation_error",
    ):
        getattr(loop, name).side_effect = getattr(app_module.WebJamApplication, name).__get__(loop)

    boundaries = {}
    for name in (
        "begin_startup_journey", "begin_reference_studio_journey", "_on_launch_audio",
        "start_companion_api", "start_desktop_integrations", "accept_invitation",
        "_start_routing_scan",
    ):
        boundaries[name] = Mock(name=name)
        monkeypatch.setattr(ApplicationController, name, boundaries[name])
    monkeypatch.setattr(RecordingCoordinator, "recover_interrupted_recordings", Mock())
    monkeypatch.setattr(sys, "argv", ["WebJam"])
    monkeypatch.setenv("WEBJAM_SMOKE_AUTOSTART_AUDIO", "0")
    monkeypatch.setenv("WEBJAM_COMPANION_API", "0")
    monkeypatch.setattr(app_module, "configure_logging", Mock())
    monkeypatch.setattr(app_module, "_configure_default_font", Mock())
    monkeypatch.setattr(app_module, "load_stylesheet", lambda: "")
    monkeypatch.setattr(app_module, "QApplication", SimpleNamespace(instance=lambda: loop))
    monkeypatch.setattr(app_module, "load_settings", lambda path=None: load_settings(path or settings.config_file))
    scheduled = []
    monkeypatch.setattr(app_module, "QTimer", SimpleNamespace(singleShot=lambda _ms, action: scheduled.append(action)))
    controllers, launchers, dialogs = [], [], []

    def make_controller(window, **kwargs):
        controller = ApplicationController(window, **kwargs)
        controllers.append(controller)
        return controller

    constructor = Mock(side_effect=make_controller)
    constructor.mode_entries = ApplicationController.mode_entries
    monkeypatch.setattr(app_module, "ApplicationController", constructor)

    def run(record, *, cancel=False, pending_invitation=None, after_open=None):
        def choose_record(dialog):
            dialogs.append(dialog)
            dialog.select_id(record.id)
            assert dialog.record.id == record.id
            if cancel:
                dialog.reject()
            else:
                dialog.continue_button.click()
                assert dialog.selected_record.id == record.id
            return dialog.result()

        def choose_library(launcher):
            launchers.append(launcher)
            launcher._session_library_action.trigger()
            if cancel:
                assert launcher.selected_role == ""
                assert launcher.showing_choices
                launcher.reject()
            else:
                assert launcher.selected_role == "library"
                # A queued FileOpen event may still own an earlier invitation
                # when the modal saved-work choice is accepted.
                loop._pending_invitation = pending_invitation
                loop._pending_invitation_error = "Earlier invitation could not be opened"
            return launcher.result()

        def event_loop():
            # Run every callback selected by bootstrap: accidentally scheduling
            # audio or replaying an invitation must fail the boundary assertions.
            while scheduled:
                scheduled.pop(0)()
            if after_open:
                after_open(controllers[0])
            return 0

        monkeypatch.setattr(SessionLibraryDialog, "exec", choose_record)
        monkeypatch.setattr(LaunchDialog, "exec", choose_library)
        loop.exec.side_effect = event_loop
        assert app_module._run_app() == 0
        for name in (
            "begin_startup_journey", "begin_reference_studio_journey", "_on_launch_audio",
            "start_companion_api", "accept_invitation",
        ):
            boundaries[name].assert_not_called()
        return controllers[0] if controllers else None

    yield SimpleNamespace(run=run, library=library, settings=settings, loop=loop,
                          constructor=constructor, boundaries=boundaries,
                          controllers=controllers, dialogs=dialogs)
    for controller in controllers:
        controller.shutdown()
        controller.window.close()
        controller.window.deleteLater()
        controller.deleteLater()
    for dialog in launchers:
        dialog.deleteLater()
    qapp.processEvents()


@pytest.mark.parametrize("profile", ["music", "art", "podcast_voice", "review_rehearsal"])
def test_launch_library_restores_exact_profile_title_and_notes_without_starting_audio(bootstrap, profile):
    record = bootstrap.library.create(profile, "Selected workspace", notes="Decision: keep this exact draft")
    bootstrap.library.create("music", "More recent unrelated workspace", notes="Never substitute these notes")

    def check(controller):
        assert controller.creator_profile.key == profile
        assert controller.session_library.current.id == record.id
        assert controller.window.session_strip.current_title() == record.title
        assert controller.window.session_canvas.current_notes() == record.notes
        assert not controller.audio.connected
        assert not controller.recording.is_recording_active
        assert not controller.host_peer.active

    controller = bootstrap.run(record, after_open=check)
    assert controller._shutdown
    kwargs = bootstrap.constructor.call_args.kwargs
    assert kwargs["session_invite"] is None
    assert kwargs["remote_invitation"] is None
    assert kwargs["session_meeting_url"] == ""
    saved = load_settings(bootstrap.settings.config_file)
    assert saved.last_creator_profile_key == profile
    assert saved.jamulus_server == bootstrap.settings.jamulus_server
    assert saved.host_server_enabled


def test_cancelling_launch_library_keeps_door_open_and_quits_without_controller(bootstrap):
    record = bootstrap.library.create("art", "Unopened art", notes="Keep me")
    settings_before = Path(bootstrap.settings.config_file).read_bytes()
    assert bootstrap.run(record, cancel=True) is None
    bootstrap.constructor.assert_not_called()
    bootstrap.loop.exec.assert_not_called()
    assert Path(bootstrap.settings.config_file).read_bytes() == settings_before
    assert bootstrap.library.load(record.id).notes == "Keep me"


def test_saved_work_retires_old_pending_invitation_and_error(bootstrap):
    record = bootstrap.library.create("music", "Local rehearsal", notes="Offline decisions")
    invitation = parse_invite_link(create_invite_link("192.168.1.42", session_name="Old room"))

    def check(_controller):
        assert bootstrap.loop.pending_invitation() is None
        assert bootstrap.loop.take_pending_invitation_error() == ""

    bootstrap.run(record, pending_invitation=invitation, after_open=check)


@pytest.mark.parametrize("damage", [None, "missing_media", "changed_manifest"])
def test_library_take_action_opens_only_selected_verified_take_in_existing_studio(
    bootstrap, tmp_path, monkeypatch, damage,
):
    from tests.test_recording_studio import _schema2_studio_take

    takes = tmp_path / "takes"
    target, _ = _schema2_studio_take(takes)
    target = target.rename(takes / "Selected take")
    fallback, _ = _schema2_studio_take(takes)
    original = load_take(target)
    reference = {"take_id": original.take_id, "take_path": str(target),
                 "source_identity": take_source_identity(original), "title": "Selected take"}
    record = bootstrap.library.create("music", "Linked rehearsal", take_links=(reference,))
    manifest_before = (target / "webjam-take.json").read_bytes()

    def check(controller):
        studio = controller.window.recording_studio
        assert studio.open_take(fallback)
        if damage == "missing_media":
            (target / "media" / "server.wav").unlink()
        elif damage == "changed_manifest":
            (target / "webjam-take.json").write_bytes(manifest_before + b"\n")
        play = Mock()
        monkeypatch.setattr(studio, "_toggle_play", play)
        opened = []

        def open_selected(dialog):
            dialog.select_id(record.id)
            dialog.tabs.setCurrentIndex(4)
            dialog.takes.setCurrentRow(0)
            button = next(button for button in dialog.findChildren(QPushButton)
                          if button.text() == "Open selected take in Studio")
            button.click()
            opened.append(studio._current.path)
            return dialog.result()

        monkeypatch.setattr(SessionLibraryDialog, "exec", open_selected)
        controller.session_library.show()
        assert opened == [target if damage is None else fallback]
        assert controller.window.recording_studio is studio
        assert not studio._player.is_playing
        play.assert_not_called()
        if damage is None:
            assert studio.current_take_reference()["take_id"] == original.take_id
            assert (target / "webjam-take.json").read_bytes() == manifest_before

    bootstrap.run(record, after_open=check)
