"""Launching Krita for AI image work, and looking only at loopback.

This is the only place in WebJam that starts Krita or touches an image
backend. It is deliberately thin: discovery and the network boundary live in
:mod:`core.krita_ai`, the product rules in :mod:`core.ai_image`, and what
remains here is one ``Popen`` and one read-only status request.

Two rules are worth stating out loud. The executable is re-resolved
immediately before every launch rather than remembered from an earlier probe,
because a path that verified a minute ago is not a path that is still a real
executable now. And the backend request is a plain ``GET`` to a loopback
status endpoint with a short deadline and no redirects: WebJam never sends the
artist's prompt, image, or canvas anywhere, and there is no code path here
that could.
"""

from __future__ import annotations

import logging
import subprocess
import urllib.error
import urllib.request

from core.ai_image import AiImageAvailability
from core.external_program import configured_candidates
from core.krita_ai import (
    DEFAULT_KRITA_CANDIDATES,
    DEFAULT_KRITA_RESOURCE_DIRS,
    AiImageError,
    AiImageUnavailableError,
    INSTALL_KRITA_MESSAGE,
    LocalImage,
    backend_probe_url,
    find_ai_plugin,
    find_krita,
    krita_edit_arguments,
    krita_make_arguments,
    normalize_local_backend_url,
)

LOGGER = logging.getLogger("webjam.services.krita_ai")

LAUNCH_FAILED_MESSAGE = (
    "WebJam found Krita but could not start it. Open Krita yourself, then try "
    "AI Image again."
)

#: Long enough for a local server to answer, short enough that a panel opening
#: never feels like it stalled.
_BACKEND_TIMEOUT_S = 0.75


class KritaAiStudio:
    """Probe the local image stack and open Krita on a canvas or a file."""

    def __init__(self, settings: object = None) -> None:
        self._settings = settings

    # -- discovery -----------------------------------------------------

    def executable(self):
        return find_krita(
            configured_candidates(
                self._settings, "krita_candidates", DEFAULT_KRITA_CANDIDATES
            )
        )

    def plugin(self):
        return find_ai_plugin(
            configured_candidates(
                self._settings,
                "krita_resource_dirs",
                DEFAULT_KRITA_RESOURCE_DIRS,
            )
        )

    def backend_url(self) -> str:
        """Return the configured loopback backend, or "" if it is refused.

        A misconfigured address must not stop the panel from opening and
        saying what is wrong, so the refusal is reported as "no backend"
        rather than raised out of a probe.
        """

        try:
            return normalize_local_backend_url(
                getattr(self._settings, "comfyui_url", None)
            )
        except AiImageError:
            LOGGER.warning("A non-loopback image backend address was refused")
            return ""

    def probe(self) -> AiImageAvailability:
        executable = self.executable()
        if executable is None:
            return AiImageAvailability()
        plugin = self.plugin()
        if plugin is None:
            return AiImageAvailability(krita_found=True)
        return AiImageAvailability(
            krita_found=True,
            plugin_found=True,
            backend_url=self._reachable_backend(),
        )

    def _reachable_backend(self) -> str:
        base = self.backend_url()
        if not base:
            return ""
        request = urllib.request.Request(
            backend_probe_url(base), method="GET", headers={"Accept": "*/*"}
        )
        try:
            # No redirect handler and no proxy handler: a status check must not
            # be talked into leaving this machine.
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({}),
                _NoRedirects(),
            )
            with opener.open(request, timeout=_BACKEND_TIMEOUT_S) as response:
                if 200 <= int(getattr(response, "status", 0)) < 300:
                    return base
        except (urllib.error.URLError, OSError, ValueError):
            return ""
        except Exception:  # noqa: BLE001 - a probe never breaks the room
            LOGGER.debug("Image backend probe failed", exc_info=True)
            return ""
        return ""

    # -- launching -----------------------------------------------------

    def open_new_image(self) -> None:
        self._spawn(krita_make_arguments(self._require_executable()))

    def open_image(self, image: LocalImage) -> None:
        self._spawn(krita_edit_arguments(self._require_executable(), image))

    def _require_executable(self):
        executable = self.executable()
        if executable is None:
            raise AiImageUnavailableError(INSTALL_KRITA_MESSAGE)
        return executable

    @staticmethod
    def _spawn(arguments: list[str]) -> None:
        try:
            subprocess.Popen(arguments, shell=False)
        except OSError as exc:
            # The arguments name the artist's own file, so only the failure is
            # logged. Repeating the command would put their directory layout
            # into the log.
            LOGGER.warning("Krita could not be started: %s", type(exc).__name__)
            raise AiImageError(LAUNCH_FAILED_MESSAGE) from exc


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """Refuse to follow a redirect off the loopback address that was checked."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        return None


def create_ai_image_studio(settings: object = None) -> KritaAiStudio:
    """Build the studio the AI image controller drives."""

    return KritaAiStudio(settings)


__all__ = [
    "LAUNCH_FAILED_MESSAGE",
    "KritaAiStudio",
    "create_ai_image_studio",
]
