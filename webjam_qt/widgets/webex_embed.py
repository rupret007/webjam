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

Camera and microphone permission requests are granted only for trusted Webex
origins. Screen-capture and notification requests are denied by default. The
view uses a named persistent profile (``webjam_webex``) so cookies and
local-storage survive restarts.

WebEngine objects are created lazily on first ``load_meeting()`` call so the
Chromium subprocess is only started when the user actually joins a meeting.
"""

from __future__ import annotations

import logging
import json
import threading
import time
from pathlib import Path
from typing import Callable, Optional

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

from core.webex_url import is_allowed_webex_url
from core.redaction import redact_webex_url
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
def _make_webex_page(profile, is_trusted_permission_url: Callable[[QUrl], bool]):
    """Build a QWebEnginePage subclass that auto-grants A/V permissions."""
    from PySide6.QtWebEngineCore import QWebEnginePage

    grant_set = _web_page_feature_grant_set()

    class _WebexPage(QWebEnginePage):
        def __init__(self, prof, parent=None):
            super().__init__(prof, parent)
            self.featurePermissionRequested.connect(self._on_permission)

        def _on_permission(self, url, feature):
            trusted_origin = is_allowed_webex_url(url.toString()) or is_trusted_permission_url(url)
            policy = (
                QWebEnginePage.PermissionPolicy.PermissionGrantedByUser
                if feature in grant_set and trusted_origin
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
        # Native Webex is the supported path. Keep this as a compact launch
        # and guidance card rather than reserving stage space for a browser.
        self.setMinimumHeight(112)
        self.setMaximumHeight(156)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        # Optional telemetry hook: called with a metric key string when the
        # widget spawns a token-refresh worker (ATTEMPT) or the worker
        # returns a non-empty token (SUCCESS).  Wired by the controller
        # to MetricsService.increment.
        self.on_refresh_metric: Optional[Callable[[str], None]] = None

        self._load_ready.connect(self._on_load_ready, Qt.ConnectionType.QueuedConnection)

        # Lazy WebEngine objects (None until first load_meeting call)
        self._profile = None
        self._page    = None
        self._view    = None
        self._channel = None
        self._bridge  = None

        self._pending_token: Optional[str] = None
        self._pending_url:   str = ""
        self._audio_mode = "talkback"
        self._talk_break_active = False

        # Unix timestamp of the most recent successful token acquisition.
        # Used by ``maybe_refresh_token`` to decide whether the embedded
        # widget's auth is approaching its 1-hour TTL.  0.0 = no token yet.
        self._token_acquired_at: float = 0.0
        # True while a background refresh thread is in flight, so a 1-Hz
        # poll from the controller doesn't spawn a stampede.
        self._refresh_in_flight: bool = False

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
        if not is_allowed_webex_url(meeting_url):
            LOGGER.warning("WebexEmbed refused untrusted meeting URL")
            self.meeting_state_changed.emit("error")
            return

        self._ensure_webengine()
        self._stack.setCurrentIndex(1)

        if access_token and _HTML_TEMPLATE.exists():
            LOGGER.info("WebexEmbed: guest-widget mode")
            self._pending_token = access_token
            self._pending_url   = meeting_url
            # If _on_load_ready hasn't already stamped this (i.e. caller
            # passed a token directly), record acquisition time now so the
            # refresh timer has a baseline.
            if self._token_acquired_at <= 0.0:
                self._token_acquired_at = time.time()
            local_url = QUrl.fromLocalFile(str(_HTML_TEMPLATE.resolve()))
            self._view.load(local_url)
        else:
            LOGGER.info("WebexEmbed: direct-URL mode")
            self._pending_token = None
            self._pending_url   = meeting_url
            # Direct-URL mode doesn't use a guest token — clear the stamp
            # so refresh polls correctly no-op.
            self._token_acquired_at = 0.0
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
        def _worker() -> None:
            from core.webex_guest_token import generate_guest_jwt, exchange_guest_jwt
            token: Optional[str] = None
            try:
                jwt   = generate_guest_jwt(issuer_id, secret_b64, display_name)
                token = exchange_guest_jwt(jwt)
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning("Guest token failed: %s — using direct-URL mode", exc)
            self._load_ready.emit(token or "", meeting_url)

        # Stay on placeholder while token is fetched; load_meeting() will
        # switch to the webview once the token arrives via _on_load_ready.
        self._stack.setCurrentIndex(0)
        threading.Thread(target=_worker, daemon=True, name="webex-token").start()

    def leave_meeting(self) -> None:
        """Navigate away from the meeting and restore the placeholder.

        Deliberately does NOT clear cookies/cache — the whole point of the
        named persistent profile (``webjam_webex``) is that Webex session
        state survives across joins/leaves and app restarts, so the user
        isn't re-prompted to log in every time they rejoin. Use
        ``shutdown()`` to actually tear down the WebEngine profile.
        """
        if self._view is not None:
            self._view.load(QUrl("about:blank"))
        self._pending_token = None
        self._pending_url   = ""
        self._token_acquired_at = 0.0
        self._stack.setCurrentIndex(0)
        self.meeting_state_changed.emit("left")

    def shutdown(self) -> None:
        """Best-effort teardown of the QWebEngineView on app shutdown.

        Navigates away first so no meeting media keeps running, then
        deletes the view. The persistent profile itself is intentionally
        left alone — Chromium/Qt persists it to disk on the profile's own
        schedule, and destroying it here isn't necessary for a clean exit.
        """
        if self._view is not None:
            try:
                self._view.load(QUrl("about:blank"))
                self._view.setParent(None)
                self._view.deleteLater()
            except Exception:  # noqa: BLE001
                LOGGER.debug("WebEngineView teardown failed", exc_info=True)
            finally:
                self._view = None

    def _is_trusted_widget_permission_url(self, url: QUrl) -> bool:
        """Allow mic/camera for the bundled widget only for a trusted meeting.

        Guest-token mode loads ``webex_widget.html`` from ``file://`` and the
        Webex widget runs inside that local page. The permission prompt can
        therefore originate from the local file, not ``*.webex.com``. Gate that
        local origin on the pending meeting URL so arbitrary file pages still
        fail closed.
        """
        if not self._pending_token or not is_allowed_webex_url(self._pending_url):
            return False
        if not url.isLocalFile():
            return False
        try:
            return Path(url.toLocalFile()).resolve() == _HTML_TEMPLATE.resolve()
        except Exception:  # noqa: BLE001
            return False

    def fallback_button(self) -> QPushButton:
        """Return the external Webex launch button on the status card."""
        return self._fallback_btn

    def set_launch_status(self, status: str) -> None:
        """Show external-launch truth without implying meeting membership."""
        descriptions = {
            "Not opened": "Webex has not been opened from WebJam yet.",
            "Opening…": "Opening Webex externally…",
            "Opened externally": (
                "Webex opened externally. Finish joining and control the "
                "meeting in Webex."
            ),
            "Open failed": "Webex could not be opened. Retry or check Settings.",
        }
        self._status_label.setText(descriptions.get(status, str(status)))
        button_text = (
            "Opening…" if status == "Opening…" else
            "Open Again" if status == "Opened externally" else
            "Open Webex"
        )
        self._fallback_btn.setText(button_text)
        self._fallback_btn.setEnabled(status != "Opening…")
        self._fallback_btn.setAccessibleName(f"{button_text} externally")

    def set_audio_mode(self, mode: str) -> None:
        """Render concise role guidance for the selected Webex audio mode."""
        self._audio_mode = (
            mode if mode in {"talkback", "video_only", "audience_bridge"}
            else "talkback"
        )
        self._render_audio_guidance()

    def set_talk_break_active(self, active: bool) -> None:
        """Persist the PLAY/TALK safety state beside the Webex launcher."""
        self._talk_break_active = bool(active)
        self._render_audio_guidance()

    def _render_audio_guidance(self) -> None:
        titles = {
            "talkback": "Webex talkback",
            "video_only": "Webex video",
            "audience_bridge": "Webex audience feed",
        }
        guidance = {
            "talkback": (
                "TALK · Jamulus send muted — hold Space in Webex to speak."
                if self._talk_break_active else
                "PLAY · Music stays in Jamulus — keep Webex muted."
            ),
            "video_only": "Join Webex without computer audio; music stays in Jamulus.",
            "audience_bridge": (
                "Advanced audience feed: musicians must disconnect Webex audio "
                "to prevent delayed duplicate music."
            ),
        }
        self._title_label.setText(titles[self._audio_mode])
        self._mode_label.setText(guidance[self._audio_mode])

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
        s.setAttribute(QWebEngineSettings.WebAttribute.ScreenCaptureEnabled, False)
        s.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.LocalStorageEnabled, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.WebRTCPublicInterfacesOnly, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)

        self._bridge = _WebexBridge(self)
        self._bridge.page_ready.connect(self._on_page_ready)
        self._bridge.meeting_state.connect(self._on_meeting_state)

        self._channel = QWebChannel(self)
        self._channel.registerObject("webexBridge", self._bridge)

        # Page parented to profile → page is destroyed before profile (Qt child order)
        self._page = _make_webex_page(self._profile, self._is_trusted_widget_permission_url)
        self._page.setWebChannel(self._channel)

        # Inject qwebchannel.js so the page's `new QWebChannel(...)` call works.
        # setWebChannel() wires up qt.webChannelTransport but does NOT define the
        # QWebChannel JS class — without it, the JS→Qt bridge never connects, so
        # in guest-widget mode `page_ready` never fires and the meeting never
        # starts.  Guarded so any API/resource hiccup leaves prior behavior intact.
        self._inject_qwebchannel_js()

        self._view = QWebEngineView(self)
        self._view.setPage(self._page)
        self._view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        # Detect load failures so the controller can show an error state and
        # the user can fall back to opening Webex in the browser.
        self._view.loadFinished.connect(self._on_view_load_finished)
        self._stack.addWidget(self._view)  # index 1

    def _inject_qwebchannel_js(self) -> None:
        """Inject Qt's bundled qwebchannel.js as a document-creation script.

        The HTML calls ``new QWebChannel(qt.webChannelTransport, ...)`` but that
        class is only defined by Qt's ``qwebchannel.js`` resource, which Qt does
        NOT auto-inject when you call ``setWebChannel()``.  We read it from the
        ``:/qtwebchannel/qwebchannel.js`` qrc resource and inject it before the
        page's own scripts run, so the JS→Qt bridge actually connects.

        Fully guarded: any import/resource/API problem is logged and ignored,
        leaving the previous (bridge-less but non-crashing) behavior intact.
        """
        try:
            from PySide6.QtCore import QFile, QIODevice
            from PySide6.QtWebEngineCore import QWebEngineScript

            qfile = QFile(":/qtwebchannel/qwebchannel.js")
            if not qfile.open(QIODevice.OpenModeFlag.ReadOnly):
                LOGGER.warning(
                    "qwebchannel.js resource not found; Webex JS bridge disabled"
                )
                return
            try:
                source = bytes(qfile.readAll().data()).decode("utf-8")
            finally:
                qfile.close()

            script = QWebEngineScript()
            script.setName("qwebchannel.js")
            script.setSourceCode(source)
            script.setInjectionPoint(
                QWebEngineScript.InjectionPoint.DocumentCreation
            )
            script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
            script.setRunsOnSubFrames(False)
            self._page.scripts().insert(script)
            LOGGER.debug("Injected qwebchannel.js into Webex page")
        except Exception:  # noqa: BLE001
            LOGGER.warning("Could not inject qwebchannel.js", exc_info=True)

    # ------------------------------------------------------------------
    # Internal slots
    # ------------------------------------------------------------------
    @Slot(str, str)
    def _on_load_ready(self, token: str, url: str) -> None:
        """Runs on main thread after background token fetch completes."""
        if token:
            self._token_acquired_at = time.time()
            if self.on_refresh_metric is not None:
                try:
                    self.on_refresh_metric("metric_webex_token_refresh_success")
                except Exception:  # noqa: BLE001
                    LOGGER.debug("on_refresh_metric SUCCESS hook failed", exc_info=True)
        # Refresh complete (success or fail) — release the in-flight latch
        self._refresh_in_flight = False
        self.load_meeting(url, access_token=token or None)

    def maybe_refresh_token(
        self,
        issuer_id: str,
        secret_b64: str,
        display_name: str,
    ) -> None:
        """Refresh the embedded Webex auth token if it's near its TTL.

        Safe to call repeatedly (e.g. from a 1 Hz QTimer):
        * No-op if no token has been issued yet (direct-URL mode, or pre-join).
        * No-op if the existing token is still fresh.
        * No-op if guest credentials are missing.
        * No-op if a refresh is already in flight.

        On refresh, fetches a new token in a background thread and reloads
        the embedded widget with the new token via ``_on_load_ready``.
        """
        # No credentials → nothing we can do
        if not issuer_id or not secret_b64:
            return
        # No prior token → nothing to refresh; the initial join will set it.
        if self._token_acquired_at <= 0.0:
            return
        if self._refresh_in_flight:
            return
        # No active meeting URL → nothing to reload into
        if not self._pending_url:
            return

        from core.webex_guest_token import should_refresh_token
        if not should_refresh_token(self._token_acquired_at):
            return

        meeting_url = self._pending_url
        self._refresh_in_flight = True
        LOGGER.info("WebexEmbed: refreshing guest token (TTL approaching)")

        def _worker() -> None:
            from core.webex_guest_token import generate_guest_jwt, exchange_guest_jwt
            token: Optional[str] = None
            try:
                jwt   = generate_guest_jwt(issuer_id, secret_b64, display_name)
                token = exchange_guest_jwt(jwt)
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning("Guest token refresh failed: %s", exc)
            self._load_ready.emit(token or "", meeting_url)

        # Telemetry: count every refresh worker we spawn.
        if self.on_refresh_metric is not None:
            try:
                self.on_refresh_metric("metric_webex_token_refresh_attempt")
            except Exception:  # noqa: BLE001
                LOGGER.debug("on_refresh_metric ATTEMPT hook failed", exc_info=True)
        threading.Thread(target=_worker, daemon=True, name="webex-token-refresh").start()

    @Slot(bool)
    def _on_view_load_finished(self, ok: bool) -> None:
        """QWebEngineView reports load completion — surface failures.

        When the Webex meeting URL fails to load (404, DNS error, network
        offline, blocked by Webex), the embedded view shows a Chromium
        error page.  Without this, the user has no easy way back to a
        functional state.  We emit an 'error' state so the controller can
        restore the status card so the user can retry the external launch.
        """
        if ok:
            return
        # Non-fatal cases: about:blank loads, transient redirects.  Skip
        # those so we don't fire 'error' on every navigation glitch.
        url = self._view.url().toString() if self._view else ""
        if url in ("", "about:blank") or url.startswith("data:"):
            return
        LOGGER.warning("WebexEmbed load failed for %s", redact_webex_url(url))
        self.meeting_state_changed.emit("error")

    @Slot()
    def _on_page_ready(self) -> None:
        """HTML template signalled bridge-live — inject token into widget."""
        if not self._pending_token or not self._pending_url:
            return
        token = json.dumps(self._pending_token)
        url = json.dumps(self._pending_url)
        self._pending_token = None
        self._page.runJavaScript(f"startWebexMeeting({token}, {url});")

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

        self._title_label = QLabel("Webex talkback")
        self._title_label.setObjectName("WebexEmbedTitle")
        self._title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._mode_label = QLabel(
            "Music stays in Jamulus. Join Webex muted; hold Space only "
            "during a Talk Break."
        )
        self._mode_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._mode_label.setWordWrap(True)
        self._mode_label.setObjectName("BodyLabel")

        self._status_label = QLabel("Webex has not been opened from WebJam yet.")
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status_label.setWordWrap(True)
        self._status_label.setObjectName("BodyLabel")

        self._fallback_btn = QPushButton("Open Webex")
        self._fallback_btn.setObjectName("GhostButton")
        self._fallback_btn.setAccessibleName("Open Webex externally")
        self._fallback_btn.setAccessibleDescription(
            "Open the configured meeting in the native Webex app or browser."
        )

        layout = QVBoxLayout(frame)
        layout.setContentsMargins(Space.LG, Space.SM, Space.LG, Space.SM)
        layout.setSpacing(Space.XS)
        layout.addWidget(self._title_label)
        layout.addWidget(self._mode_label)
        layout.addWidget(self._status_label)
        layout.addWidget(
            self._fallback_btn, alignment=Qt.AlignmentFlag.AlignCenter
        )
        return frame
