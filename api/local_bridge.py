from __future__ import annotations

import threading
from typing import Callable, Dict, List, Optional


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
        self._thread: Optional[threading.Thread] = None
        self._running = False

    def start(self) -> bool:
        if self._running:
            return True
        try:
            from fastapi import FastAPI  # type: ignore
            import uvicorn  # type: ignore
        except Exception:
            return False

        app = FastAPI(title="WebJam Local Bridge")

        @app.get("/health")
        def health() -> Dict[str, str]:
            return {"status": "ok"}

        @app.get("/participants")
        def participants() -> Dict[str, List[Dict]]:
            return {"participants": self.get_participants()}

        @app.get("/diagnostics")
        def diagnostics() -> Dict[str, Dict[str, str]]:
            return {"diagnostics": self.get_diagnostics()}

        def run_server() -> None:
            uvicorn.run(app, host=self.host, port=self.port, log_level="warning")

        self._thread = threading.Thread(target=run_server, daemon=True)
        self._thread.start()
        self._running = True
        return True

    def stop(self) -> None:
        # Uvicorn stop signaling is omitted in this lightweight integration.
        self._running = False

