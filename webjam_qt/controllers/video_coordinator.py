"""Video session coordinator — Join/Leave Webex and embed lifecycle."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from webjam_qt.controllers.application_controller import ApplicationController


class VideoCoordinator:
    """Owns Webex join/leave flows delegated from ApplicationController."""

    def __init__(self, controller: ApplicationController) -> None:
        self._c = controller

    def is_active(self) -> bool:
        return self._c._is_video_active()

    def leave(self) -> None:
        self._c._leave_video()
