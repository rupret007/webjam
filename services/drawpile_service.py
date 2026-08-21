"""Launching the artist's own Drawpile, and nothing more.

This is the only place in WebJam that starts a Drawpile process.  It is
deliberately thin: discovery and invitation grammar live in
:mod:`core.drawpile`, the product rules live in :mod:`core.shared_canvas`, and
what remains here is one ``Popen`` plus the honesty rules that surround it.

Two of those rules are worth stating out loud.  The executable is re-resolved
immediately before every launch rather than remembered from an earlier probe,
because a path that verified minutes ago is not a path that is still a real
executable now.  And nothing here ever logs or echoes the canvas URL, which
can embed a Drawpile session password.
"""

from __future__ import annotations

import logging
import subprocess

from core.drawpile import (
    INSTALL_DRAWPILE_MESSAGE,
    CanvasInvite,
    DrawpileUnavailableError,
    drawpile_host_arguments,
    drawpile_join_arguments,
    find_drawpile,
)
from core.shared_canvas import SharedCanvasError

LOGGER = logging.getLogger("webjam.services.drawpile")

LAUNCH_FAILED_MESSAGE = (
    "WebJam found Drawpile but could not start it. Open Drawpile yourself, "
    "then try the shared canvas again."
)


def drawpile_candidates(settings: object = None) -> tuple[str, ...]:
    """Return the install locations to check, honoring an explicit override."""

    configured = getattr(settings, "drawpile_candidates", None)
    if isinstance(configured, (list, tuple)):
        entries = tuple(
            str(item).strip() for item in configured if str(item or "").strip()
        )
        if entries:
            return entries
    from core.drawpile import DEFAULT_DRAWPILE_CANDIDATES

    return DEFAULT_DRAWPILE_CANDIDATES


class DrawpileLauncher:
    """Start the installed Drawpile on its Host page or joined to a canvas."""

    def __init__(self, settings: object = None) -> None:
        self._settings = settings

    def executable(self):
        """Re-resolve the Drawpile executable, or ``None`` if there is none."""

        return find_drawpile(drawpile_candidates(self._settings))

    def available(self) -> bool:
        return self.executable() is not None

    def open_host_page(self) -> None:
        executable = self._require_executable()
        self._spawn(drawpile_host_arguments(executable))

    def open_canvas(self, invite: CanvasInvite) -> None:
        executable = self._require_executable()
        self._spawn(drawpile_join_arguments(executable, invite))

    def _require_executable(self):
        executable = self.executable()
        if executable is None:
            raise DrawpileUnavailableError(INSTALL_DRAWPILE_MESSAGE)
        return executable

    @staticmethod
    def _spawn(arguments: list[str]) -> None:
        try:
            subprocess.Popen(arguments, shell=False)
        except OSError as exc:
            # The arguments carry the canvas URL, so only the failure is
            # logged. Repeating the command would put a Drawpile session
            # password into the log file.
            LOGGER.warning("Drawpile could not be started: %s", type(exc).__name__)
            raise SharedCanvasError(LAUNCH_FAILED_MESSAGE) from exc


def create_canvas_launcher(settings: object = None) -> DrawpileLauncher:
    """Build the launcher the shared-canvas coordinator drives."""

    return DrawpileLauncher(settings)


__all__ = [
    "LAUNCH_FAILED_MESSAGE",
    "DrawpileLauncher",
    "create_canvas_launcher",
    "drawpile_candidates",
]
