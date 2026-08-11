"""Truthful external meeting-link launcher for WebJam.

WebJam does not embed, join, monitor, or control an external meeting. The
selected service (or system browser) owns authentication, media devices, and
meeting state. This compatibility-named module reports only whether handing a
validated meeting URL to the operating system succeeded.
"""

from __future__ import annotations

import webbrowser
from enum import Enum

from core.logging_config import configure_logging
from core.settings import load_settings
from core.meeting_link import (
    GENERIC_MEETING_SERVICE_KEY,
    identify_meeting_service,
    is_allowed_meeting_link,
    meeting_link_hostname,
    normalize_meeting_url,
)


class WebexLaunchState(str, Enum):
    """The complete external meeting-handoff state exposed by WebJam."""

    NOT_OPENED = "Not opened"
    OPENING = "Opening…"
    OPENED_EXTERNALLY = "Opened externally"
    OPEN_FAILED = "Open failed"


def _meeting_hostname(url: str) -> str:
    """Return a safe logging label without meeting path/query/fragment."""

    service = identify_meeting_service(url)
    if service == GENERIC_MEETING_SERVICE_KEY:
        # A custom/company domain is private diagnostic data.  The musician can
        # see it in the editable UI field, but logs receive only this category.
        return "generic"
    return meeting_link_hostname(url) or "unknown"


class WebexController:
    """Open one validated meeting URL through the operating system."""

    def __init__(self, meeting_url: str):
        self.settings = load_settings()
        self.logger = configure_logging(self.settings).getChild("webex_controller")
        self.meeting_url = meeting_url
        self.launch_state = WebexLaunchState.NOT_OPENED
        self.browser_opened = False
        self.last_error = ""
        # Compatibility truth for callers migrating from the placeholder
        # monitor: external launch never proves meeting membership.
        self.is_connected = False

    def start(self) -> None:
        """Compatibility no-op; external Webex has no observable live state."""

    def stop(self) -> None:
        """Compatibility no-op; WebJam does not own the external process."""

    def join_meeting(self, name: str | None = None, email: str | None = None) -> bool:
        """Hand the meeting URL to the OS and return whether launch succeeded.

        ``name`` and ``email`` are retained for one call-signature compatibility
        cycle but are intentionally unused; the meeting service owns identity.
        """
        del name, email
        return self.join_meeting_url(self.meeting_url)

    def join_meeting_url(self, meeting_url: str) -> bool:
        """Hand one immutable, caller-authorized URL to the operating system."""

        self.launch_state = WebexLaunchState.OPENING
        requested_url = str(meeting_url or "")
        hostname = _meeting_hostname(requested_url)
        try:
            launch_url = normalize_meeting_url(requested_url)
            if not is_allowed_meeting_link(launch_url):
                raise RuntimeError("invalid or untrusted meeting URL")
            if not webbrowser.open(launch_url):
                raise RuntimeError("browser refused meeting URL")
        except Exception as exc:  # noqa: BLE001
            self.browser_opened = False
            self.launch_state = WebexLaunchState.OPEN_FAILED
            self.last_error = type(exc).__name__
            self.logger.warning(
                "Meeting-link external launch failed (host=%s, error=%s)",
                hostname,
                type(exc).__name__,
            )
            return False

        self.browser_opened = True
        self.launch_state = WebexLaunchState.OPENED_EXTERNALLY
        self.last_error = ""
        self.logger.info("Meeting link opened externally (host=%s)", hostname)
        return True


def open_webex_meeting(url: str) -> bool:
    """Open a validated meeting URL without a long-lived controller."""
    controller = WebexController(url)
    return controller.join_meeting()


def create_webex_controller(meeting_url: str) -> WebexController:
    """Construct the external launcher (``start`` remains a harmless no-op)."""
    controller = WebexController(meeting_url)
    controller.start()
    return controller
