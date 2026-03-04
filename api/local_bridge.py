from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Dict, List, Optional

_LOGGER = logging.getLogger(__name__)


class LocalApiBridge:
    """
    Companion bridge server wrapper.

    Starts a localhost FastAPI server if fastapi/uvicorn are installed.
    """

    def __init__(
        self,
        get_participants: Callable[[], List[Dict]],
        get_diagnostics: Callable[[], Dict[str, str]],
        host: str = "127.0.0.1",
        port: int = 8765,
    ):
        self.get_participants = get_participants
        self.get_diagnostics = get_diagnostics
        self.host = host
        self.port = port
        self._state_lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._server = None
        self._running = False

    def _create_app(self, FastAPI: Any, HTTPException: Any) -> Any:
        app = FastAPI(title="WebJam Local Bridge")

        @app.get("/health")
        def health() -> Dict[str, str]:
            return {"status": "ok"}

        @app.get("/participants")
        def participants() -> Dict[str, List[Dict]]:
            try:
                return {"participants": self.get_participants()}
            except Exception as exc:
                raise HTTPException(status_code=500, detail=f"participants callback failed: {exc}") from exc

        @app.get("/diagnostics")
        def diagnostics() -> Dict[str, Dict[str, str]]:
            try:
                return {"diagnostics": self.get_diagnostics()}
            except Exception as exc:
                raise HTTPException(status_code=500, detail=f"diagnostics callback failed: {exc}") from exc

        return app

    def start(self) -> bool:
        with self._state_lock:
            if self._running:
                return True
        try:
            from fastapi import FastAPI, HTTPException  # type: ignore
            import uvicorn  # type: ignore
        except Exception:
            return False

        app = self._create_app(FastAPI, HTTPException)

        config = uvicorn.Config(app=app, host=self.host, port=self.port, log_level="warning")
        self._server = uvicorn.Server(config)

        def run_server() -> None:
            try:
                self._server.run()
            finally:
                with self._state_lock:
                    self._running = False

        with self._state_lock:
            self._thread = threading.Thread(target=run_server, daemon=True)
            self._thread.start()
            self._running = True
        return True

    def stop(self) -> None:
        with self._state_lock:
            self._running = False
            server = self._server
            thread = self._thread
        if server is not None:
            try:
                server.should_exit = True
            except Exception:
                pass
        if thread and thread.is_alive():
            thread.join(timeout=2)
            if thread.is_alive():
                _LOGGER.warning("LocalApiBridge server thread did not exit within 2s; may still be shutting down")

