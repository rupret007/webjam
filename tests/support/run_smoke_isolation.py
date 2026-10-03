"""Prove smoke ownership without pytest's in-process isolation fixtures."""
import logging
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile

from core.settings import load_settings
import jamulus_controller
import webex_integration
from services import workflow_continuity_packaged_smoke as runtime
from services.workspace_portability_smoke import _launch_saved, _workspace


def main():
    app = runtime._application()
    with tempfile.TemporaryDirectory(prefix="webjam-smoke-isolation-") as temporary:
        base = Path(temporary).resolve()
        outside = base / "Outside"
        outside.mkdir()
        sentinel = outside / "existing.log"
        handler = logging.FileHandler(sentinel)
        logger = logging.getLogger("webjam")
        logger.addHandler(handler)
        logger.setLevel(logging.WARNING)
        logger.propagate = True
        state = (tuple(logger.handlers), logger.level, logger.propagate)
        environment = {
            "WEBJAM_TAKES_DIRECTORY": str(outside / "Takes"),
            "WEBJAM_SERVER_RPC_SECRET_FILE": str(outside / "server.secret"),
            "WEBJAM_LOG_FILE": str(outside / "new.log"),
            "WEBJAM_AUDIO_SAMPLERATE": "22050",
            "WEBJAM_COMPANION_API": "1",
            "MUSIC_AI_API_KEY": "owned-test-sentinel",
        }
        os.environ.update(environment)
        saved_environment = dict(os.environ)
        real_connect = sqlite3.connect
        try:
            for fail in (False, True):
                root = base / ("Exceptional" if fail else "Normal")
                before = {p.name: p.read_bytes() for p in outside.iterdir()}
                attempts = []

                def owned_settings(path=None):
                    assert path is not None, "smoke read default user settings"
                    assert Path(path).resolve().is_relative_to(root), "settings escaped owned root"
                    return load_settings(path)

                def owned_connect(path, *args, **kwargs):
                    assert Path(path).resolve().is_relative_to(root), "database escaped owned root"
                    attempts.append(Path(path))
                    return real_connect(path, *args, **kwargs)

                with runtime._replace(runtime, "load_settings", owned_settings), \
                        runtime._replace(jamulus_controller, "load_settings", owned_settings), \
                        runtime._replace(webex_integration, "load_settings", owned_settings), \
                        runtime._replace(sqlite3, "connect", owned_connect):
                    try:
                        with runtime._isolated_runtime(root) as (library, settings_path, _starts):
                            record = library.create("music", "Owned smoke")
                            launch = _launch_saved(app, settings_path, record.id)
                            with _workspace(app, settings_path, launch) as (_window, controller, _sink):
                                launch.deleteLater()
                                assert controller._startup_readiness_store.path.is_relative_to(root)
                                assert controller._startup_attempt_store.path.is_relative_to(root)
                                for settings in (controller.settings, controller.jamulus.settings, controller.webex.settings):
                                    assert Path(settings.log_file).is_relative_to(root)
                                    assert Path(settings.takes_directory).is_relative_to(root)
                                    assert Path(settings.server_rpc_secret_file).is_relative_to(root)
                                    assert settings.audio_samplerate == 48000
                                    assert not settings.companion_api_enabled and not settings.music_ai_api_key
                                logger.warning("owned smoke marker")
                                created_handlers = tuple(logger.handlers)
                            if fail:
                                raise RuntimeError("owned injected exit")
                    except RuntimeError as error:
                        assert fail and str(error) == "owned injected exit"
                    else:
                        assert not fail
                assert attempts and all(path.is_relative_to(root) for path in attempts)
                assert dict(os.environ) == saved_environment
                assert (tuple(logger.handlers), logger.level, logger.propagate) == state
                assert all(h._closed for h in created_handlers)
                assert {p.name: p.read_bytes() for p in outside.iterdir()} == before
                assert "owned smoke marker" in (root / "smoke.log").read_text()
                # Windows refuses this if an owned file handler or DB remains open.
                shutil.rmtree(root)
                logger.warning("restored handler still works")
                handler.flush()
                assert "restored handler still works" in sentinel.read_text()
        finally:
            logger.removeHandler(handler)
            handler.close()
    print("WebJam smoke ownership and cleanup passed")


if __name__ == "__main__":
    main()
