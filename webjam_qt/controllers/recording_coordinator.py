"""Band-server recording coordinator — Record button and RPC worker."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from webjam_qt.controllers.application_controller import ApplicationController


class RecordingCoordinator:
    """Owns the band-server multitrack recorder toggle."""

    def __init__(self, controller: ApplicationController) -> None:
        self._c = controller

    def on_record_requested(self) -> None:
        self._c._on_record_requested()

    def toggle_worker(self, target_armed: bool, secret_file: str) -> None:
        self._c._record_toggle_worker(target_armed, secret_file)
