"""
WebexEmbed — embedded Webex meeting pane.

Integrates the Webex Meetings Widget inside a QWebEngineView so the meeting
lives inside WebJam rather than opening a separate browser tab.

Two join modes, chosen automatically based on AppSettings:

  Guest-widget mode  — when ``webex_guest_issuer_id`` and
                       ``webex_guest_issuer_secret`` are both non-empty.
                       Generates a guest JWT, exchanges it for an API access
                       token, then loads a local HTML page running the Webex
                       Meetings Widget. The user appears as a named guest with
                       no Webex login required.

  Direct-URL mode    — fallback (and default). Loads ``settings.webex_url``
                       directly in the WebEngine view using a Chrome
                       user-agent. The Webex web client handles auth via the
                       browser session stored in the persistent profile.

Camera, microphone and screen-capture permission requests are granted
automatically.  The view uses a named persistent profile (``webjam_webex``)
so cookies and local-storage survive restarts.

WebEngine objects are created lazily on first ``load_meeting()`` call so the
Chromium subprocess is only started when the user actually joins a meeting.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, QUrl, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from webjam_qt.theme.tokens import Space

LOGGER = logging.getLogger("webjam.qt.webex_embed")

_CHROME_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36"
)

_HTML_TEMPLATE = Path(__file__).parent.parent / "webex_widget.html"

# ---------------------------------------------------------------------------
# Lazy imports of Qt WebEngine modules — deferred until first use so that
# the Chromium subprocess isn't launched at import time.
# ---------------------------------------------------------------------------
def _web_page_feature_grant_set():
    from PySide6.QtWebEngineCore import QWebEnginePage
    return frozenset({
        QWebEnginePage.Feature.MediaAudioCapture,
        QWebEnginePage.Feature.MediaVideoCapture,
        QWebEnginePage.Feature.MediaAudioVideoCapture,
        QWebEnginePage.Feature.DesktopVideoCapture,
        QWebEnginePage.Feature.DesktopAudioVideoCapture,
        QWebEnginePage.Feature.Notifications,
    })


# ---------------------------------------------------------------------------
# QWebChannel bridge object  (slots callable from JS)
# ---------------------------------------------------------------------------
class _WebexBridge(QObject):
    """Exposed to JavaScript as ``channel.objects.webexBridge``."""

    page_ready    = Signal()
    meeting_state = Signal(str)

    @Slot()
    def on_page_ready(self) -> None:
        self.page_ready.emit()

    @Slot(str)
    def on_state(self, state: str) -> None:
        self.meeting_state.emit(state)


# ---------------------------------------------------------------------------
# Custom QWebEnginePage — auto-grants A/V permissions (created lazily)
# ---------------------------------------------------------------------------
def _make_webex_page(profile):
    """Build a QWebEnginePage subclass that auto-grants A/V permissions."""
    from PySide6.QtWebEngineCore import QWebEnginePage

    grant_set = _web_page_feature_grant_set()

    class _WebexPage(QWebEnginePage):
        def __init__(self, prof, parent=None):
            super().__init__(prof, parent)
            self.featurePermissionRequested.connect(self._on_permission)

        def _on_permission(self, url, feature):
            policy = (
                QWebEnginePage.PermissionPolicy.PermissionGrantedByUser
                if feature in grant_set
                else QWebEnginePage.PermissionPolicy.PermissionDeniedByUser
            )
            self.setFeaturePermission(url, feature, policy)

        def javaScriptConsoleMessage(self, level, message, line, source):
            if level == QWebEnginePage.JavaScriptConsoleMessageLevel.ErrorMessageLevel:
                LOGGER.debug("JS error [%s:%d] %s", source, line, message)

    return _WebexPage(profile, profile)  # parented to profile so page < profile lifetime


# ---------------------------------------------------------------------------
# Main widget
# ---------------------------------------------------------------------------
class WebexEmbed(QFrame):
    """
    Signals
    -------
    meeting_state_changed(str)
        Emitted when the embedded Webex widget reports a state transition.
        Common values: ``"joining"``, ``"ACTIVE"``, ``"ENDED"``, ``"left"``,
        ``"error"``.
    """

    meeting_state_changed = Signal(str)
    # Internal: carries (access_token, meeting_url) from worker thread → main thread
    _load_ready = Signal(str, str)

    # ------------------------------------------------------------------
    # Construction — lightweight, no WebEngine objects yet
    # ------------------------------------------------------------------
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("WebexEmbed")
        self.setMinimumHeight(180)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self._load_ready.connect(self._on_load_ready, Qt.ConnectionType.QueuedConnection)

        # Lazy WebEngine objects (None until first load_meeting call)
        self._profile = None
        self._page    = None
        self._view    = None
        self._channel = None
        self._bridge  = None

        self._pending_token: Optional[str] = None
        self._pending_url:   str = ""

        # Placeholder page (shown before joining)
        self._placeholder = self._build_placeholder()

        # Stack: 0 = placeholder, 1 = webview (slot reserved, filled lazily)
        self._stack = QStackedWidget(self)
        self._stack.addWidget(self._placeholder)
        self._stack.setCurrentIndex(0)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self._stack)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def load_meeting(
        self,
        meeting_url: str,
        *,
        access_token: Optional[str] = None,
    ) -> None:
        """Load a Webex meeting inside the embedded pane.

        WebEngine is initialised on the first call; subsequent calls reuse
        the same persistent profile (keeping Webex session cookies).

        Args:
            meeting_url:  Webex meeting URL (``AppSettings.webex_url``).
            access_token: Optional Webex API token.  Triggers guest-widget mode
                          (local HTML + Webex Meetings Widget) when set.
                          Omit to use direct-URL mode.
        """
        self._ensure_webengine()
        self._stack.setCurrentIndex(1)

        if access_token and _HTML_TEMPLATE.exists():
            LOGGER.info("WebexEmbed: guest-widget mode")
            self._pending_token = access_token
            self._pending_url   = meeting_url
            local_url = QUrl.fromLocalFile(str(_HTML_TEMPLATE.resolve()))
            self._view.load(local_url)
        else:
            LOGGER.info("WebexEmbed: direct-URL mode → %s", meeting_url)
            self._pending_token = None
            self._pending_url   = meeting_url
            self._view.load(QUrl(meeting_url))
            self.meeting_state_changed.emit("joining")

    def load_meeting_with_guest_token(
        self,
        meeting_url: str,
        issuer_id: str,
        secret_b64: str,
        display_name: str,
    ) -> None:
        """Fetch a Webex guest token in a background thread, then load the meeting.

        Falls back to direct-URL mode if the token exchange fails.
        Safe to call from the UI thread.
        """
        # Show loading HTML immediately while token is fetched
        self._ensure_webengine()
        self._stack.setCurrentIndex(1)
        if _HTML_TEMPLATE.exists():
            self._view.load(QUrl.fromLocalFile(str(_HTML_TEMPLATE.resolve())))

        def _worker() -> None:
            from core.webex_guest_token import generate_guest_jwt, exchange_guest_jwt
            token: Optional[str] = None
            try:
                jwt   = generate_guest_jwt(issuer_id, secret_b64, display_name)
                token = exchange_guest_jwt(jwt)
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning("Guest token failed: %s — using direct-URL mode", exc)
            self._load_ready.emit(token or "", meeting_url)

        threading.Thread(target=_worker, daemon=True, name="webex-token").start()

    def leave_meeting(self) -> None:
        """Navigate away from the meeting and restore the placeholder."""
        if self._view is not None:
            self._view.load(QUrl("about:blank"))
        self._pending_token = None
        self._pending_url   = ""
        self._stack.setCurrentIndex(0)
        self.meeting_state_changed.emit("left")

    def fallback_button(self) -> QPushButton:
        """Return the 'Open Webex in browser' button on the placeholder."""
        return self._fallback_btn

    # ------------------------------------------------------------------
    # Lazy WebEngine init
    # ------------------------------------------------------------------
    def _ensure_webengine(self) -> None:
        """Create WebEngine objects on first use."""
        if self._view is not None:
            return

        from PySide6.QtWebChannel import QWebChannel
        from PySide6.QtWebEngineCore import QWebEngineProfile, QWebEngineSettings
        from PySide6.QtWebEngineWidgets import QWebEngineView

        self._profile = QWebEngineProfile("webjam_webex", self)
        self._profile.setHttpUserAgent(_CHROME_UA)
        s = self._profile.settings()
        s.setAttribute(QWebEngineSettings.WebAttribute.ScreenCaptureEnabled, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.LocalStorageEnabled, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.WebRTCPublicInterfacesOnly, False)
        s.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)

        self._bridge = _WebexBridge(self)
        self._bridge.page_ready.connect(self._on_page_ready)
        self._bridge.meeting_state.connect(self._on_meeting_state)

        self._channel = QWebChannel(self)
        self._channel.registerObject("webexBridge", self._bridge)

        # Page parented to profile → page is destroyed before profile (Qt child order)
        self._page = _make_webex_page(self._profile)
        self._page.setWebChannel(self._channel)

        self._view = QWebEngineView(self)
        self._view.setPage(self._page)
        self._view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._stack.addWidget(self._view)  # index 1

    # ------------------------------------------------------------------
    # Internal slots
    # ------------------------------------------------------------------
    @Slot(str, str)
    def _on_load_ready(self, token: str, url: str) -> None:
        """Runs on main thread after background token fetch completes."""
        self.load_meeting(url, access_token=token or None)

    @Slot()
    def _on_page_ready(self) -> None:
        """HTML template signalled bridge-live — inject token into widget."""
        if not self._pending_token or not self._pending_url:
            return
        safe_token = self._pending_token.replace("\\", "\\\\").replace("'", "\\'")
        safe_url   = self._pending_url.replace("\\", "\\\\").replace("'", "\\'")
        self._page.runJavaScript(f"startWebexMeeting('{safe_token}', '{safe_url}');")

    @Slot(str)
    def _on_meeting_state(self, state: str) -> None:
        LOGGER.debug("Webex state: %s", state)
        self.meeting_state_changed.emit(state)

    # ------------------------------------------------------------------
    # Placeholder
    # ------------------------------------------------------------------
    def _build_placeholder(self) -> QWidget:
        frame = QWidget(self)
        frame.setObjectName("WebexPlaceholder")

        title = QLabel("Video conferencing")
        title.setObjectName("WebexEmbedTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        subtitle = QLabel(
            "Click \u201cJoin Video\u201d in the session strip\n"
            "to embed the Webex meeting here."
        )
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setWordWrap(True)
        subtitle.setObjectName("BodyLabel")

        self._fallback_btn = QPushButton("Open Webex in browser")
        self._fallback_btn.setObjectName("GhostButton")

        layout = QVBoxLayout(frame)
        layout.setContentsMargins(Space.LG, Space.LG, Space.LG, Space.LG)
        layout.setSpacing(Space.MD)
        layout.addStretch(1)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addWidget(
            self._fallback_btn, alignment=Qt.AlignmentFlag.AlignCenter
        )
        layout.addStretch(1)
        return frame
