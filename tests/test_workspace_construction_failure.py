"""Failed constructors retain and release real owners without saving defaults."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QTimer
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid

from core.session_library import SessionLibrary
from core import jamulus_profile as startup_module
from core.settings import AppSettings, save_settings
from webjam_qt import app as app_module
from webjam_qt.controllers.application_controller import ApplicationController
from webjam_qt.controllers import session_library as library_module
from webjam_qt.controllers import session_persistence as persistence_module
from webjam_qt.controllers.reference_studio_application import ReferenceStudioApplicationController
from webjam_qt.controllers.workspace_navigation import WorkspaceNavigator
from webjam_qt.windows.launch_dialog import LaunchDialog


class ConstructionFault(RuntimeError):
    pass


@pytest.fixture
def construction(tmp_path, monkeypatch):
    qapp = QApplication.instance() or QApplication([])
    private = tmp_path / "private"
    private.mkdir()
    monkeypatch.setattr(persistence_module, "_persistence_home", lambda: private)
    for name in ("StartupReadinessStore", "StartupAttemptStore"):
        store_type = getattr(startup_module, name)
        monkeypatch.setattr(startup_module, name, lambda store_type=store_type: store_type(home=private))
    library = SessionLibrary(private / "sessions")
    monkeypatch.setattr(library_module, "default_session_library", lambda: library)
    settings = AppSettings(config_file=str(private / "settings.json"),
        takes_directory=str(private / "takes"), companion_api_enabled=False)
    save_settings(settings)
    originals = {
        private / ".webjam_notes.md": b"Original notes that were not loaded yet.\n",
        private / ".webjam_session.json": (
            b'{"schema_version":2,"profiles":{"music":{"title":"Original title","mode_key":"music_jam"}}}'
        ),
    }
    for path, data in originals.items():
        path.write_bytes(data)
    start = Mock()
    monkeypatch.setattr(ApplicationController, "begin_startup_journey", start)
    monkeypatch.setattr(ApplicationController, "start_companion_api", start)
    monkeypatch.setattr(ApplicationController, "start_desktop_integrations", start)
    owners = []
    yield SimpleNamespace(app=qapp, settings=settings, originals=originals, owners=owners, start=start)
    for owner in owners:
        if isValid(owner):
            owner.shutdown()
            owner.window.close()
            owner.window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()


def _capture_resources(owner, monkeypatch):
    projects = owner.findChildren(ReferenceStudioApplicationController)
    for project in projects:
        # Force a real executor thread to exist; cancellation flags alone are
        # not evidence that construction cleanup actually joins its workers.
        project._executor.submit(lambda: None).result(timeout=2)
        monkeypatch.setattr(project.playback, "close", Mock(wraps=project.playback.close))
        monkeypatch.setattr(project._executor, "shutdown", Mock(wraps=project._executor.shutdown))
    return projects


@pytest.mark.parametrize("point", ["early", "before_ui", "project_constructor", "routing"])
def test_constructor_fault_releases_allocated_workers_without_overwriting_notes(construction, monkeypatch, point):
    captured_projects = []

    def fail(owner):
        if isinstance(owner, ReferenceStudioApplicationController):
            owner = owner.parent()
        construction.owners.append(owner)
        assert owner.window._workspace_construction_owner is owner
        captured_projects.extend(_capture_resources(owner, monkeypatch))
        raise ConstructionFault("controlled constructor fault")

    if point == "project_constructor":
        monkeypatch.setattr(ReferenceStudioApplicationController, "_notify_profile_applied", fail)
    else:
        method = {"early": "_register_managed_jamulus_providers", "before_ui": "_bootstrap_ui",
                  "routing": "_start_routing_scan"}[point]
        monkeypatch.setattr(ApplicationController, method, fail)
    with pytest.raises(ConstructionFault):
        app_module._create_workspace(construction.app, construction.settings, None)
    owner, = construction.owners
    assert owner._shutdown
    assert not owner._shutdown_cleanup_pending
    assert owner.window._workspace_construction_owner is None
    assert not any(timer.isActive() for timer in owner.findChildren(QTimer))
    assert owner.window.recording_studio._waveform_shutdown
    assert not owner.window.recording_studio._timer.isActive()
    for project in captured_projects:
        project.playback.close.assert_called()
        project._executor.shutdown.assert_called_with(wait=True, cancel_futures=True)
        assert not any(thread.is_alive() for thread in project._executor._threads)
        assert not project._transport_timer.isActive()
        assert not project._waveform_timer.isActive()
    if point != "early":
        assert captured_projects
    for path, data in construction.originals.items():
        assert path.read_bytes() == data
    construction.start.assert_not_called()


@pytest.mark.parametrize("cleanup_fault", ["playback", "shutdown"])
def test_unproved_constructor_cleanup_retains_one_visible_owner_until_retry(construction, monkeypatch, cleanup_fault):
    close_allowed = False
    playback_calls = []
    created = []

    def fail(owner):
        construction.owners.append(owner)
        project, = _capture_resources(owner, monkeypatch)
        original_close = project.playback.close

        def close_playback():
            playback_calls.append(None)
            if not close_allowed:
                raise OSError("controlled playback cleanup failure")
            return original_close()

        monkeypatch.setattr(project.playback, "close", close_playback)
        if cleanup_fault == "shutdown":
            original_shutdown = owner.shutdown
            first_call = True

            def fail_first_cleanup():
                nonlocal first_call
                if first_call:
                    first_call = False
                    raise RuntimeError("controlled cleanup boundary failure")
                return original_shutdown()

            monkeypatch.setattr(owner, "shutdown", fail_first_cleanup)
        raise ConstructionFault("controlled late constructor fault")

    monkeypatch.setattr(ApplicationController, "_start_routing_scan", fail)

    def create_workspace(launch):
        created.append(None)
        return app_module._create_workspace(construction.app, construction.settings, launch)

    navigator = WorkspaceNavigator(construction.app,
        create_launch=lambda: LaunchDialog(construction.settings), create_workspace=create_workspace)
    try:
        navigator._show_launch()
        navigator.launch._workspace_actions["music"].trigger()
        owner, = construction.owners
        assert navigator.controller is owner
        assert navigator.window is owner.window and owner.window.isVisible()
        assert navigator.launch is None
        assert owner._shutdown_cleanup_pending and not owner._shutdown
        assert owner.window._workspace_construction_owner is owner
        assert not owner.window.workspace_stack.isEnabled()
        assert not navigator.return_to_launch()
        assert navigator.controller is owner and navigator.window.isVisible()
        assert len(created) == 1
        close_allowed = True
        assert navigator.return_to_launch()
        assert owner._shutdown and not owner._shutdown_cleanup_pending
        assert navigator.controller is None and navigator.launch.isVisible()
        assert len(playback_calls) == (3 if cleanup_fault == "playback" else 2)
        assert len(created) == 1
        for path, data in construction.originals.items():
            assert path.read_bytes() == data
        construction.start.assert_not_called()
    finally:
        close_allowed = True
        navigator.shutdown()
        if navigator.launch is not None:
            navigator.launch.blockSignals(True)
            navigator.launch.close()
            navigator.launch.deleteLater()
        navigator.dispose()
