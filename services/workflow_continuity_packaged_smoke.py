"""Temporary-data proof of the real Qt workspace and Help navigation owners.

The existing frozen Reference Studio hook invokes this proof. Only external
startup/integration boundaries are controlled: actual windows, save guards,
End/Leave, shutdown and replacement controllers execute their production code.
It proves no physical audio, network connection or user-attended feel.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
import os
from pathlib import Path
import tempfile

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QMessageBox

from core.project_playback import ProjectPlaybackState
from core.session_library import SessionLibrary
from core.session_lifecycle import SessionLifecyclePhase
from core.settings import AppSettings, load_settings, save_settings
from services.session_workspace_packaged_smoke import _require, _wait

SUCCESS_MARKER = "WebJam workflow continuity Qt smoke passed"
_APP = None


@contextmanager
def _replace(owner, name, value):
    previous = getattr(owner, name)
    setattr(owner, name, value)
    try:
        yield
    finally:
        setattr(owner, name, previous)


def _application():
    global _APP
    if QApplication.instance() is None:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        _APP = QApplication(["webjam-workflow-continuity-smoke"])
    return QApplication.instance()


@contextmanager
def _isolated_runtime(root: Path):
    """Keep fixture state private and prevent external startup, not navigation."""
    from webjam_qt.controllers.application_controller import ApplicationController
    from webjam_qt.controllers import session_library, session_persistence
    from webjam_qt.controllers.recording_coordinator import RecordingCoordinator

    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    library = SessionLibrary(root / "Library")
    settings_path = root / "settings.json"
    if not settings_path.exists():
        save_settings(AppSettings(
            config_file=str(settings_path), mix_file=str(root / "mix.json"),
            takes_directory=str(root / "Takes"), log_file=str(root / "smoke.log"),
            server_rpc_secret_file=str(root / "server.secret"),
            companion_api_enabled=False,
        ))
    controlled_starts = {"live": [], "companion": []}

    def controlled_start(controller, *_args, **_kwargs):
        controlled_starts["live"].append(id(controller))

    def unexpected_audio(*_args, **_kwargs):
        raise RuntimeError("Packaged continuity smoke dispatched unexpected audio startup")

    with ExitStack() as patches:
        for owner, name, value in (
            (session_persistence, "_persistence_home", lambda: root),
            (session_library, "default_session_library", lambda: library),
            (ApplicationController, "_start_routing_scan", lambda self: None),
            (RecordingCoordinator, "recover_interrupted_recordings", lambda self: None),
            (ApplicationController, "start_desktop_integrations", lambda self, **kw: None),
            (ApplicationController, "start_companion_api",
             lambda self: controlled_starts["companion"].append(id(self))),
            (ApplicationController, "begin_startup_journey", controlled_start),
            (ApplicationController, "_on_launch_audio", unexpected_audio),
            (QMessageBox, "question", lambda *a, **kw: QMessageBox.StandardButton.Yes),
        ):
            patches.enter_context(_replace(owner, name, value))
        yield library, settings_path, controlled_starts


def _close_workspace(app, window, controller):
    _require(controller.shutdown(), "temporary workspace did not prove cleanup")
    window.close()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def _help_route(app, controller, *, offline: bool):
    window = controller.window
    window._workflow_help_action.trigger()
    dialog = window._workflow_help_dialog
    _require(dialog is not None and dialog.isVisible() and not dialog.isModal(),
             "Help menu did not open modeless guidance")
    before_notes = window.session_canvas.current_notes()
    before_take = window.recording_studio._current
    dialog._search.setText("local project" if offline else "rehearsal")
    app.processEvents()
    _require(dialog._topics.count() >= 1, "packaged Help search found no workflow")
    _require(controller.session_library.dialog is None,
             "reading Help navigated without a selected action")
    _require(dialog._navigate.isVisible() and dialog._navigate.isEnabled(),
             "Help lost its explicit navigation action")
    dialog._navigate.click()
    app.processEvents()
    if offline:
        _require(controller.session_library.dialog is None,
                 "local Studio Help opened a session library")
        _require(window.workspace_stack.currentWidget() is window.reference_studio,
                 "local project Help did not return to Studio")
    else:
        editor = controller.session_library.dialog
        _require(editor is not None and editor.isVisible() and not editor.isModal(),
                 "rehearsal Help did not open the existing library")
        _require(editor.tabs.currentIndex() == 1, "Help opened the wrong library tab")
        editor.reject()
        app.processEvents()
    _require(window.session_canvas.current_notes() == before_notes,
             "Help navigation changed notes")
    _require(window.recording_studio._current is before_take,
             "Help navigation substituted a take")
    _require(not window.recording_studio._player.is_playing
             and not controller.recording.is_recording_active,
             "Help navigation started audio or recording")


def run_workflow_continuity_smoke() -> dict:
    """Run two actual Qt/controller round trips with controlled external audio."""
    from webjam_qt import app as app_module
    from webjam_qt.controllers.workspace_navigation import WorkspaceNavigator
    from webjam_qt.windows.launch_dialog import LaunchDialog

    app = _application()
    controllers = []
    with tempfile.TemporaryDirectory(prefix="webjam-workflow-continuity-") as temporary:
        with _isolated_runtime(Path(temporary)) as (library, settings_path, starts):
            def create_workspace(launch):
                result = app_module._create_workspace(app, load_settings(str(settings_path)), launch)
                controllers.append(result[1])
                return result

            navigator = WorkspaceNavigator(
                app, create_launch=lambda: LaunchDialog(load_settings(str(settings_path))),
                create_workspace=create_workspace,
            )
            try:
                window, controller = create_workspace(None)
                navigator.adopt(window, controller)
                app.processEvents()
                record = library.create("music", "Continuity smoke", notes="Keep this rehearsal draft.")
                _require(controller.session_library.continue_record(record), "saved workspace did not open")
                project_bundle = Path(temporary) / "Continuity project.webjam"
                project_identity = None
                project_tracks = ()
                for _round in range(2):
                    live = navigator.controller
                    old_return = navigator._return_slot
                    _help_route(app, live, offline=False)
                    running = {"value": True}

                    def stop_client():
                        running["value"] = False
                        return True

                    with _replace(live, "_is_jamulus_running", lambda: running["value"]), \
                            _replace(live.bridge, "stop_jamulus", stop_client):
                        live._transition_lifecycle(SessionLifecyclePhase.JOINING, "Controlled smoke client")
                        live._transition_lifecycle(SessionLifecyclePhase.CONNECTED, "Controlled smoke client")
                        _require(not navigator.return_to_launch(), "active room bypassed explicit End/Leave")
                        live.audio.stop()
                        _wait(app, lambda: not live.audio.stopping, "End/Leave did not finish")
                        _require(not running["value"] and live.audio.ended_by_user
                                 and not live.audio.cleanup_retry_required,
                                 "End/Leave did not prove controlled cleanup")
                        _require(any(event["to_state"] == "completed"
                                     for event in live.session_lifecycle.public_timeline()),
                                 "End/Leave omitted lifecycle completion")
                        live.window._return_to_launch_action.trigger()
                    _require(navigator.controller is None and live._shutdown
                             and navigator.launch.isVisible(), "retired live owner did not reach launch")
                    called = []
                    app_module._run_workspace_callback(live, lambda: called.append(True))
                    _require(not called, "retired startup callback still executed")
                    navigator.launch._workspace_actions["music"].trigger()
                    app.processEvents()
                    local = navigator.controller
                    _require(local is not live and local._offline_reference_studio
                             and local.window._reference_studio_only,
                             "New Music Project reused the live owner")
                    _require(not local._is_jamulus_running() and not local.bridge.hosted_server_alive(),
                             "local project started a Jamulus process")
                    old_return()
                    _require(navigator.controller is local, "old callback retired the replacement owner")
                    projects = local.reference_studio_projects
                    if _round == 0:
                        project = projects.create_project(project_bundle, "Keep this local project")
                        projects.project_controller.add_track("Retain this track")
                        _require(projects.save(prepare_media=False), "local project did not save")
                        project_identity = project.project_id
                        project_tracks = tuple(
                            track.name for track in projects.project_controller.snapshot.project.tracks
                        )
                    else:
                        project = projects.open_project(project_bundle)
                        _require(project.project_id == project_identity
                                 and tuple(track.name for track in project.tracks) == project_tracks,
                                 "fresh local owner did not reopen the same saved project")
                    _require(projects.project_open and projects.playback.state in {
                                 ProjectPlaybackState.EMPTY, ProjectPlaybackState.READY,
                             },
                             "local project navigation started playback or failed to open")
                    _help_route(app, local, offline=True)
                    local.window._return_to_launch_action.trigger()
                    _require(navigator.controller is None and navigator.launch.isVisible(),
                             "local Studio did not return to launch")
                    navigator.launch._host()
                    app.processEvents()
                    _require(navigator.controller is not local
                             and not navigator.controller._offline_reference_studio,
                             "Host did not create a fresh live owner")
                _require(len({id(item) for item in controllers}) == 5, "controllers were reused")
                _require(len(starts["live"]) == 3 and starts["companion"] == starts["live"],
                         "unexpected live startup or companion replay")
                _require(library.load(record.id).notes == record.notes, "round trips lost saved notes")
                return {"round_trips": 2, "fresh_controllers": 5, "help_routes": 4,
                        "retired_callbacks_ignored": True, "saved_notes_unchanged": True,
                        "local_project_identity_retained": True,
                        "physical_audio": "not_run"}
            finally:
                if navigator.controller is not None:
                    _close_workspace(app, navigator.window, navigator.controller)
                if navigator.launch is not None:
                    navigator.launch.blockSignals(True)
                    navigator.launch.close()
                    navigator.launch.deleteLater()
                navigator.dispose()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                app.processEvents()
