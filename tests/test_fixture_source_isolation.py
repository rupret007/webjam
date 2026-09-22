"""Imported fixtures receive the same private storage as directly collected tests."""

from __future__ import annotations

import os
from pathlib import Path


pytest_plugins = ("pytester",)


def test_imported_controller_fixture_isolates_storage_before_construction(pytester, monkeypatch):
    project = Path(__file__).resolve().parents[1]
    existing_path = os.environ.get("PYTHONPATH", "")
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(filter(None, (str(project), existing_path))))
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    pytester.makeconftest((project / "tests" / "conftest.py").read_text(encoding="utf-8"))
    pytester.makepyfile(imported_room="""
        import sqlite3
        from pathlib import Path

        import pytest
        from PySide6.QtWidgets import QApplication

        from core import jamulus_rpc_client
        from core.settings import AppSettings
        from services import bridge_service
        from webjam_qt.controllers import session_persistence
        from webjam_qt.controllers.application_controller import ApplicationController
        from webjam_qt.windows.conductor_window import ConductorWindow

        @pytest.fixture
        def imported_room(monkeypatch, tmp_path):
            app = QApplication.instance() or QApplication([])
            real_connect = sqlite3.connect
            opened = []

            def private_connect(database, *args, **kwargs):
                path = Path(database)
                assert path != Path.home() / '.webjam_app.db', 'real home database access'
                assert 'webjam-controller-repository' in str(path)
                opened.append(path)
                return real_connect(database, *args, **kwargs)

            monkeypatch.setattr(sqlite3, 'connect', private_connect)
            # These guards precede construction, which otherwise loads local
            # notes and creates a runtime secret at developer-owned paths.
            assert session_persistence._persistence_home() != Path.home()
            assert 'webjam-native-runtime' in str(jamulus_rpc_client.DEFAULT_SECRET_PATH)
            assert bridge_service.DEFAULT_SECRET_PATH == jamulus_rpc_client.DEFAULT_SECRET_PATH
            window = ConductorWindow(
                mode_entries=ApplicationController.mode_entries(),
                initial_mode_key='music_jam', initial_title='Isolated imported fixture',
            )
            controller = ApplicationController(window, settings=AppSettings(
                config_file=str(tmp_path / 'settings.json'),
                takes_directory=str(tmp_path / 'takes'),
            ))
            try:
                assert opened
                yield controller
            finally:
                assert controller.shutdown()
                window.close()
                window.deleteLater()
                controller.deleteLater()
                app.processEvents()
    """)
    pytester.makepyfile(test_indirect="""
        from imported_room import imported_room

        def test_room_uses_private_storage(imported_room):
            assert imported_room.repository.db_path
    """)
    result = pytester.runpytest_subprocess("-q", "test_indirect.py", timeout=30)
    result.assert_outcomes(passed=1)
    pytester.makepyfile(test_pure="""
        import sys

        def test_pure_module_does_not_load_the_ui():
            assert 'webjam_qt.controllers.application_controller' not in sys.modules
    """)
    pure = pytester.runpytest_subprocess("-q", "test_pure.py", timeout=30)
    pure.assert_outcomes(passed=1)
