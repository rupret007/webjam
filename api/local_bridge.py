from __future__ import annotations

import logging
import threading
import time
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
        get_diagnostics: Callable[[], Dict[str, Any]],
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

    @staticmethod
    def _host_allowed(host_header: Optional[str]) -> bool:
        """True only for loopback Host headers.

        Defends against DNS-rebinding: a malicious web page can force a browser
        to send requests to 127.0.0.1, but it cannot forge the Host header to a
        loopback name, so requiring one keeps remote origins out even though the
        socket is bound to localhost.
        """
        if not host_header:
            return False
        host = host_header.strip()
        # Strip the port. Handle bracketed IPv6 ("[::1]:8765") and host:port.
        if host.startswith("["):
            host = host[1:host.index("]")] if "]" in host else host[1:]
        elif host.count(":") == 1:
            host = host.split(":", 1)[0]
        return host.lower() in {"localhost", "127.0.0.1", "::1"}

    def _create_app(self, FastAPI: Any, HTTPException: Any) -> Any:
        app = FastAPI(title="WebJam Local Bridge")

        @app.middleware("http")
        async def _reject_non_loopback_host(request: Any, call_next: Any) -> Any:
            from starlette.responses import JSONResponse
            if not self._host_allowed(request.headers.get("host")):
                return JSONResponse(
                    status_code=403,
                    content={"detail": "forbidden: non-loopback Host header"},
                )
            return await call_next(request)

        @app.get("/health")
        def health() -> Dict[str, str]:
            return {"status": "ok"}

        @app.get("/participants")
        def participants() -> Dict[str, List[Dict]]:
            try:
                return {"participants": self.get_participants()}
            except Exception as exc:
                raise HTTPException(
                    status_code=500,
                    detail="participants callback failed",
                ) from exc

        @app.get("/diagnostics")
        def diagnostics() -> Dict[str, Dict[str, Any]]:
            try:
                return {"diagnostics": self.get_diagnostics()}
            except Exception as exc:
                raise HTTPException(
                    status_code=500,
                    detail="diagnostics callback failed",
                ) from exc

        return app

    def start(self) -> bool:
        with self._state_lock:
            if self._running:
                return True
        host = (self.host or "").strip().lower()
        if host not in {"127.0.0.1", "localhost", "::1"}:
            _LOGGER.warning(
                "LocalApiBridge refuses to bind to non-loopback host: %r", self.host
            )
            return False
        if not isinstance(self.host, str) or not self.host.strip():
            _LOGGER.warning("LocalApiBridge invalid host: %r", self.host)
            return False
        try:
            port = int(self.port)
        except (TypeError, ValueError):
            _LOGGER.warning("LocalApiBridge invalid port: %r", self.port)
            return False
        if not (1 <= port <= 65535):
            _LOGGER.warning("LocalApiBridge port out of range: %r", port)
            return False
        try:
            from fastapi import FastAPI, HTTPException  # type: ignore
            import uvicorn  # type: ignore
        except Exception:
            return False

        app = self._create_app(FastAPI, HTTPException)

        try:
            config = uvicorn.Config(app=app, host=self.host, port=port, log_level="warning")
            self._server = uvicorn.Server(config)
        except Exception as exc:
            _LOGGER.warning("LocalApiBridge server config failed: %s", exc)
            return False

        def run_server() -> None:
            try:
                self._server.run()
            finally:
                with self._state_lock:
                    self._running = False

        with self._state_lock:
            self._thread = threading.Thread(target=run_server, daemon=True)
            try:
                self._thread.start()
            except Exception as exc:
                _LOGGER.warning("LocalApiBridge thread start failed: %s", exc)
                self._thread = None
                self._running = False
                return False
            self._running = True
        # Best-effort startup verification so callers don't get false positives
        # when bind/startup fails immediately (for example, port already in use).
        for _ in range(20):
            with self._state_lock:
                thread = self._thread
                server = self._server
                running = self._running
            if server is not None and hasattr(server, "started"):
                if not running:
                    return False
                if thread is not None and not thread.is_alive():
                    return False
                if bool(getattr(server, "started", False)):
                    return True
            else:
                # Compatibility fallback for test doubles that do not implement
                # uvicorn's "started" flag.
                if thread is not None and thread.is_alive() and running:
                    # Guard against transient startup threads that exit
                    # immediately before truly serving.
                    time.sleep(0.01)
                    with self._state_lock:
                        retry_thread = self._thread
                        retry_running = self._running
                    if retry_thread is not None and retry_thread.is_alive() and retry_running:
                        return True
                if server is not None and not running and bool(getattr(server, "should_exit", False)):
                    return True
                if thread is not None and not thread.is_alive() and not running:
                    return False
            time.sleep(0.05)
        with self._state_lock:
            thread = self._thread
            server = self._server
            running = self._running
            if server is not None and not hasattr(server, "started"):
                if thread is not None and thread.is_alive() and running:
                    return True
                return bool(getattr(server, "should_exit", False) and not running)
            return bool(self._thread and self._thread.is_alive() and self._running)

    def stop(self) -> None:
        with self._state_lock:
            self._running = False
            server = self._server
            thread = self._thread
            self._thread = None
        if server is not None:
            try:
                server.should_exit = True
            except Exception:
                pass
        if thread and thread.is_alive():
            thread.join(timeout=2)
            if thread.is_alive():
                _LOGGER.warning("LocalApiBridge server thread did not exit within 2s; may still be shutting down")
