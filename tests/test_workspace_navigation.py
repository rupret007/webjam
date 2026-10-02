"""Real Qt/controller navigation; external audio is a separate physical gate."""
from __future__ import annotations

import os
import time
from concurrent.futures import Future
from unittest.mock import Mock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QMessageBox

from core.network_invite import create_invite_link, parse_invite_link
from core.session_library import SessionLibrary
from core.session_lifecycle import SessionLifecyclePhase
from core.settings import AppSettings, load_settings, save_settings
from webjam_qt import app as app_module
from webjam_qt.controllers.application_controller import ApplicationController
from webjam_qt.controllers import session_library as library_module
from webjam_qt.controllers import session_persistence as persistence_module
from webjam_qt.controllers.recording_coordinator import RecorderPhase, RecordingCoordinator
from webjam_qt.controllers.workspace_navigation import WorkspaceNavigator
from webjam_qt.windows.launch_dialog import LaunchDialog


class Mailbox(QObject):
    aboutToQuit = Signal()
    invitation_received = Signal(object)
    invitation_error = Signal(str)

    def __init__(self, qapp):
        super().__init__()
        self._pending_invitation = None
        self._pending_invitation_error = ""
        self.quit = Mock()
        self.quitOnLastWindowClosed = qapp.quitOnLastWindowClosed
        self.setQuitOnLastWindowClosed = qapp.setQuitOnLastWindowClosed

    take_pending_invitation = app_module.WebJamApplication.take_pending_invitation
    take_pending_invitation_error = app_module.WebJamApplication.take_pending_invitation_error
    pending_invitation = app_module.WebJamApplication.pending_invitation
    invitation_is_pending = app_module.WebJamApplication.invitation_is_pending
    acknowledge_invitation = app_module.WebJamApplication.acknowledge_invitation


@pytest.fixture
def navigation(tmp_path, monkeypatch):
    qapp = QApplication.instance() or QApplication([])
    private = tmp_path / "private"
    private.mkdir()
    monkeypatch.setattr(persistence_module, "_persistence_home", lambda: private)
    library = SessionLibrary(private / "sessions")
    monkeypatch.setattr(library_module, "default_session_library", lambda: library)
    monkeypatch.setattr(ApplicationController, "_start_routing_scan", lambda self: None)
    monkeypatch.setattr(RecordingCoordinator, "recover_interrupted_recordings", lambda self: None)
    monkeypatch.setattr(ApplicationController, "start_desktop_integrations", Mock())
    monkeypatch.setattr(ApplicationController, "start_companion_api", Mock())
    monkeypatch.setattr(ApplicationController, "begin_startup_journey", Mock())
    settings = AppSettings(config_file=str(private / "settings.json"),
                           takes_directory=str(private / "takes"),
                           mix_file=str(private / "mix.json"),
                           log_file=str(private / "test.log"),
                           server_rpc_secret_file=str(private / "server.secret"),
                           companion_api_enabled=False)
    save_settings(settings)
    mailbox = Mailbox(qapp)
    controllers = []

    def create_workspace(launch):
        window, controller = app_module._create_workspace(
            mailbox, load_settings(settings.config_file), launch,
        )
        controllers.append(controller)
        return window, controller

    navigator = WorkspaceNavigator(
        mailbox, create_launch=lambda: LaunchDialog(load_settings(settings.config_file)),
        create_workspace=create_workspace, invitation_mailbox=True,
    )
    window, controller = create_workspace(None)
    navigator.adopt(window, controller)
    qapp.processEvents()
    yield navigator, mailbox, controllers, qapp
    if navigator.controller is not None:
        navigator.controller._workspace_transition_pending = False
        navigator.shutdown()
        navigator.window.close()
        navigator.window.deleteLater()
    if navigator.launch is not None:
        navigator.launch.blockSignals(True)
        navigator.launch.close()
        navigator.launch.deleteLater()
    navigator.dispose()
    qapp.processEvents()


def test_two_round_trips_create_fresh_controllers_and_retire_callbacks(navigation, monkeypatch):
    navigator, mailbox, controllers, qapp = navigation
    for _ in range(2):
        previous = navigator.controller
        old_slot = navigator._return_slot
        # Exercise the actual End/Leave coordinator and asynchronous cleanup.
        # Only its external Jamulus process boundary is controlled here.
        runtime = {"running": True}
        monkeypatch.setattr(previous, "_is_jamulus_running", lambda: runtime["running"])
        def stop_owned_client():
            runtime["running"] = False
            return True
        monkeypatch.setattr(previous.bridge, "stop_jamulus", stop_owned_client)
        monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
        previous._transition_lifecycle(SessionLifecyclePhase.JOINING, "Controlled test client")
        previous._transition_lifecycle(SessionLifecyclePhase.CONNECTED, "Controlled test client")
        assert not navigator.return_to_launch()
        previous.audio.stop()
        deadline = time.monotonic() + 5
        while previous.audio.stopping and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.005)
        assert not previous.audio.stopping and not previous.audio.cleanup_retry_required
        assert previous.audio.ended_by_user
        assert any(event["to_state"] == "completed" for event in previous.session_lifecycle.public_timeline())
        assert not runtime["running"]
        previous.window._return_to_launch_action.trigger()
        assert navigator.controller is None
        assert previous._shutdown
        assert navigator.launch.isVisible()
        # Late direct startup dispatch cannot revive a retired controller.
        launch_audio = Mock()
        app_module._run_workspace_callback(previous, launch_audio)
        launch_audio.assert_not_called()
        navigator.launch._workspace_actions["music"].trigger()
        qapp.processEvents()
        local = navigator.controller
        assert local is not previous
        assert local._offline_reference_studio
        assert local.window._reference_studio_only
        assert not local.bridge.hosted_server_alive()
        assert not local._is_jamulus_running()
        old_slot()
        assert navigator.controller is local
        assert navigator.return_to_launch()
        navigator.launch._host()
        qapp.processEvents()
        assert navigator.controller is not local
        assert not navigator.controller._offline_reference_studio
        assert not navigator.window._reference_studio_only
    assert len({id(item) for item in controllers}) == 5
    mailbox.quit.assert_not_called()


def test_failed_cleanup_retains_current_window_and_retries_once(navigation, monkeypatch):
    navigator, _mailbox, _controllers, _qapp = navigation
    controller = navigator.controller
    monkeypatch.setattr(controller.api_bridge, "stop", Mock(return_value=False))
    monkeypatch.setattr(QMessageBox, "information", Mock())
    assert not navigator.return_to_launch()
    assert navigator.controller is controller
    assert controller.window.isVisible()
    assert controller._shutdown_cleanup_pending
    assert navigator.launch is None
    assert "Return to launch" in QMessageBox.information.call_args.args[2]
    controller.api_bridge.stop.return_value = True
    assert navigator.return_to_launch()
    assert controller._shutdown


@pytest.mark.parametrize("owner", ["audio", "take", "export", "peer"])
def test_busy_work_keeps_its_controls_and_never_starts_teardown(navigation, monkeypatch, owner):
    navigator, _mailbox, _controllers, _qapp = navigation
    controller = navigator.controller
    if owner == "audio":
        monkeypatch.setattr(controller, "_is_jamulus_running", lambda: True)
    elif owner == "take":
        monkeypatch.setattr(controller.recording, "phase", RecorderPhase.FINALIZING)
    elif owner == "export":
        monkeypatch.setattr(controller.window.recording_studio, "_exporting", True)
    else:
        monkeypatch.setattr(controller, "guest_peer", Mock())
    shutdown = Mock(wraps=controller.shutdown)
    monkeypatch.setattr(controller, "shutdown", shutdown)
    assert not navigator.return_to_launch()
    assert navigator.controller is controller
    assert controller.window.isVisible()
    shutdown.assert_not_called()
    assert not controller._workspace_transition_pending
    # Clear injected owners before fixture teardown.
    if owner == "audio":
        monkeypatch.setattr(controller, "_is_jamulus_running", lambda: False)
    elif owner == "take":
        controller.recording.phase = RecorderPhase.IDLE
    elif owner == "export":
        controller.window.recording_studio._exporting = False
    else:
        controller.guest_peer = None


def test_offline_invite_is_not_replayed_after_return_and_old_generation_is_ignored(navigation):
    navigator, mailbox, _controllers, qapp = navigation
    assert navigator.return_to_launch()
    navigator.launch._workspace_actions["music"].trigger()
    qapp.processEvents()
    local = navigator.controller
    generation = navigator._generation
    invitation = parse_invite_link(create_invite_link("192.168.1.10", port=22124))
    mailbox._pending_invitation = invitation
    mailbox.invitation_received.emit(invitation)
    assert mailbox.pending_invitation() is invitation
    assert local.window.statusBar().currentMessage() == local.window.OFFLINE_INVITATION_GUIDANCE
    assert navigator.return_to_launch()
    assert mailbox.pending_invitation() is None
    navigator.launch._host()
    qapp.processEvents()
    current = navigator.controller
    current.accept_invitation = Mock(return_value=True)
    mailbox._pending_invitation = invitation
    navigator._receive_invitation(invitation, generation=generation)
    current.accept_invitation.assert_not_called()
    assert local.accept_invitation(invitation) is False
    mailbox.invitation_received.emit(invitation)
    current.accept_invitation.assert_called_once_with(invitation)
    assert mailbox.pending_invitation() is None


def test_local_dirty_save_cancel_and_failure_keep_project(navigation, tmp_path, monkeypatch):
    navigator, _mailbox, _controllers, qapp = navigation
    assert navigator.return_to_launch()
    navigator.launch._workspace_actions["music"].trigger()
    qapp.processEvents()
    controller = navigator.controller
    local = controller.reference_studio_projects
    local.create_project(tmp_path / "song", "Keep this song")
    bundle = local.project_controller.snapshot.bundle_path
    local.project_controller.add_track("Unsaved track")
    monkeypatch.setattr(local, "_ask_unsaved_choice", Mock(return_value="cancel"))
    assert not navigator.return_to_launch()
    assert local.project_open and local.project_controller.snapshot.dirty
    local._ask_unsaved_choice.return_value = "save"
    with monkeypatch.context() as change:
        change.setattr(local, "save", Mock(return_value=False))
        assert not navigator.return_to_launch()
    assert navigator.controller is controller
    assert local.project_controller.snapshot.dirty
    assert navigator.return_to_launch()
    assert (bundle / "webjam-project.json").is_file()


def test_local_bounce_result_must_be_consumed_before_navigation(navigation, monkeypatch):
    navigator, _mailbox, _controllers, qapp = navigation
    assert navigator.return_to_launch()
    navigator.launch._workspace_actions["music"].trigger()
    qapp.processEvents()
    local = navigator.controller.reference_studio_projects
    future = Future()
    future.set_result(None)
    monkeypatch.setattr(local, "_bounce_future", future)
    assert not navigator.return_to_launch()
    assert "bounce result" in local._status
    local._bounce_future = None
    assert navigator.return_to_launch()


@pytest.mark.parametrize("pending", ["save_as", "recording", "recovery"])
def test_local_pending_work_is_kept_visible(navigation, monkeypatch, pending):
    navigator, _mailbox, _controllers, qapp = navigation
    assert navigator.return_to_launch()
    navigator.launch._workspace_actions["music"].trigger()
    qapp.processEvents()
    controller = navigator.controller
    local = controller.reference_studio_projects
    if pending == "save_as":
        local._save_as_future = Future()
    elif pending == "recording":
        local._recording_commit_future = Future()
    else:
        local._recording_recovery_pending = True
    assert not navigator.return_to_launch()
    assert navigator.controller is controller
    assert controller.window.isVisible()
    local._save_as_future = None
    local._recording_commit_future = None
    local._recording_recovery_pending = False
    assert navigator.return_to_launch()


def test_failed_launch_allocation_retains_current_owner(navigation, monkeypatch):
    navigator, _mailbox, _controllers, _qapp = navigation
    controller = navigator.controller
    monkeypatch.setattr(navigator, "_create_launch", Mock(side_effect=OSError("private path")))
    assert not navigator.return_to_launch()
    assert navigator.controller is controller
    assert not controller._shutdown
    assert not controller._shutdown_cleanup_pending
    assert "private path" not in controller.window.statusBar().currentMessage()


def test_reentrant_return_and_close_are_rejected_during_save(navigation, monkeypatch):
    navigator, _mailbox, _controllers, _qapp = navigation
    controller = navigator.controller
    original = controller.prepare_return_to_launch
    def reenter():
        assert not navigator.return_to_launch()
        assert not controller.window.close()
        assert controller.window.isVisible()
        return original()
    monkeypatch.setattr(controller, "prepare_return_to_launch", reenter)
    assert navigator.return_to_launch()


@pytest.mark.parametrize("cleanup_blocked", [False, True])
def test_factory_failure_retires_or_retains_real_partial_owner(navigation, monkeypatch, cleanup_blocked):
    navigator, _mailbox, _controllers, qapp = navigation
    assert navigator.return_to_launch()
    launch = navigator.launch
    allocated = []
    def fail_after_construction(controller, **_kwargs):
        allocated.append(controller)
        if cleanup_blocked:
            controller.api_bridge.stop = Mock(return_value=False)
        raise OSError("private startup detail")
    monkeypatch.setattr(ApplicationController, "start_desktop_integrations", fail_after_construction)
    monkeypatch.setattr(QMessageBox, "information", Mock())
    launch._workspace_actions["music"].trigger()
    assert len(allocated) == 1
    owner = allocated[0]
    if cleanup_blocked:
        assert navigator.controller is owner
        assert owner.window.isVisible()
        assert owner._shutdown_cleanup_pending
        assert not owner._shutdown
        owner.api_bridge.stop.return_value = True
        assert navigator.return_to_launch()
        assert owner._shutdown
    else:
        assert owner._shutdown
        assert navigator.controller is None
        assert navigator.launch is launch
        assert launch.isVisible()
        assert launch.selected_role == ""
        assert launch._workspace_actions["music"].isEnabled()
        assert "private startup detail" not in launch._choice_error.text()
    qapp.processEvents()


@pytest.mark.parametrize("owner", ["playback", "waveforms", "executor"])
def test_local_shutdown_failure_cannot_claim_success_on_retry(navigation, monkeypatch, owner):
    navigator, _mailbox, _controllers, qapp = navigation
    assert navigator.return_to_launch()
    navigator.launch._workspace_actions["music"].trigger()
    qapp.processEvents()
    controller = navigator.controller
    local = controller.reference_studio_projects
    target, method = {
        "playback": (local.playback, "close"),
        "waveforms": (local._waveforms, "shutdown"),
        "executor": (local._executor, "shutdown"),
    }[owner]
    original = getattr(target, method)
    calls = []
    def fail_once(*args, **kwargs):
        calls.append(True)
        if len(calls) == 1:
            raise RuntimeError("controlled cleanup failure")
        return original(*args, **kwargs)
    monkeypatch.setattr(target, method, fail_once)
    monkeypatch.setattr(QMessageBox, "information", Mock())
    assert not navigator.return_to_launch()
    assert navigator.controller is controller
    assert local._closed and not local._shutdown_complete
    assert controller._shutdown_cleanup_pending
    assert navigator.return_to_launch()
    assert len(calls) == 2
    assert local._shutdown_complete
    assert local._executor._shutdown


def test_failed_notes_save_keeps_current_workspace(navigation, monkeypatch):
    navigator, _mailbox, _controllers, _qapp = navigation
    controller = navigator.controller
    monkeypatch.setattr(controller, "_save_notes", Mock(return_value=False))
    assert not navigator.return_to_launch()
    assert navigator.controller is controller
    assert controller.window.isVisible()
    assert not controller._shutdown_cleanup_pending
    assert "Save Notes" in controller.window.statusBar().currentMessage()
    controller._save_notes.return_value = True
    assert navigator.return_to_launch()


@pytest.mark.parametrize("profile", ["music", "podcast_voice"])
def test_local_profile_is_initialized_again_on_each_route(navigation, tmp_path, profile):
    navigator, _mailbox, _controllers, qapp = navigation
    assert navigator.return_to_launch()
    navigator.launch._workspace_actions[profile].trigger()
    qapp.processEvents()
    if profile == "podcast_voice":
        assert navigator.launch._studio_button.isVisible()
        navigator.launch._studio_button.click()
        qapp.processEvents()
    local = navigator.controller
    assert local.creator_profile.key == profile
    assert local._offline_reference_studio
    project = local.reference_studio_projects.create_project(
        tmp_path / profile, "Profile-specific project",
    )
    assert project.creator_profile_key == profile
    assert navigator.return_to_launch()
    # The established launch front door remains Music/Art. Podcast is an
    # explicit menu choice again, without losing its saved profile or rights.
    assert load_settings(local.settings.config_file).last_creator_profile_key == profile
    if profile == "podcast_voice":
        assert navigator.launch.selected_creator_profile_key == "music"
        navigator.launch._workspace_actions[profile].trigger()
    assert navigator.launch.selected_creator_profile_key == profile
    navigator.launch._host()
    qapp.processEvents()
    assert navigator.controller.creator_profile.key == profile
    assert not navigator.controller._offline_reference_studio
    assert navigator.controller.reference_studio_projects is not local.reference_studio_projects


def test_preview_profile_cannot_enter_local_studio_from_returned_launch(navigation):
    navigator, _mailbox, controllers, qapp = navigation
    assert navigator.return_to_launch()
    launch = navigator.launch
    launch._workspace_actions["review_rehearsal"].trigger()
    qapp.processEvents()
    assert launch.selected_creator_profile_key == "review_rehearsal"
    assert launch._studio_button.isHidden()
    launch._studio()
    assert navigator.launch is launch
    assert navigator.controller is None
    assert launch.selected_role == ""
    assert len(controllers) == 1
