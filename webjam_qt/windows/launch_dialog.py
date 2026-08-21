"""The focused startup experience for WebJam's three creator profiles.

This dialog is intentionally just the creator/role decision (and one pasted
invite when joining). It does not ask WebJam to choose an audio device:
Jamulus owns the live audio route and the main window guides its native setup
after this dialog closes.
"""

from __future__ import annotations

import getpass
import logging
import os
import subprocess
import sys
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QSize, QTimer, Qt
from PySide6.QtGui import QAccessible, QAccessibleEvent, QIcon
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QCommandLinkButton,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from core.creative_modes import (
    CREATOR_PROFILES,
    CreatorProfile,
    CreatorStart,
    get_creator_profile_by_key_or_default,
)
from core.jamulus_endpoint import DEFAULT_JAMULUS_PORT
from core.jamulus_name import (
    DEFAULT_JAMULUS_NAME,
    JamulusNameError,
    validate_jamulus_name,
)
from core.meeting_link import MEETING_DIRECT_CAPTURE_BOUNDARY
from core.network_invite import BandInvite
from core.remote_invitation import RemoteInvitation
from core.settings import (
    AppSettings,
    hosted_server_recordings_dir,
    hosted_server_secret_path,
    save_settings,
)
from webjam_qt.invitation_ingress import (
    Invitation,
    InvitationIngressError,
    InvitationSource,
    invitation_from_arguments,
    parse_invitation_at_ingress,
)
from webjam_qt.theme.brand import BrandMark
from webjam_qt.theme.tokens import Space
from webjam_qt.widgets.jamulus_name_preview import JamulusNamePreview

LOGGER = logging.getLogger("webjam.qt.launch_dialog")


@dataclass(frozen=True)
class _CreatorLaunchCopy:
    host: str
    join: str
    local: str
    host_description: str
    join_description: str
    local_description: str
    helper: str
    join_title: str
    join_subtitle: str


_CREATOR_LAUNCH_COPY = {
    "music": _CreatorLaunchCopy(
        host="Host",
        join="Join",
        local="New Music Project",
        host_description=(
            "Start a live music session on this Mac and create an invitation link."
        ),
        join_description=(
            "Join a live music session using one WebJam invitation link."
        ),
        local_description=(
            "Create a local multitrack music project without starting or joining "
            "a live session."
        ),
        helper="Play live together or build a multitrack music project locally.",
        join_title="Join Music.",
        join_subtitle="Paste the WebJam invitation your host sent you.",
    ),
    "podcast_voice": _CreatorLaunchCopy(
        host="Host Remote Recording",
        join="Join Recording",
        local="New Local Recording",
        host_description=(
            "Start a remote voice recording session on this Mac and create an "
            "invitation link."
        ),
        join_description=(
            "Join a remote voice recording using one WebJam invitation link."
        ),
        local_description=(
            "Create a local multitrack voice recording without starting or joining "
            "a remote session."
        ),
        helper="Record remote voices or start a local multitrack recording.",
        join_title="Join Recording.",
        join_subtitle="Paste the WebJam recording invitation your host sent you.",
    ),
    "review_rehearsal": _CreatorLaunchCopy(
        host="Host Review",
        join="Join Review",
        local="Standalone Review Unavailable",
        host_description=(
            "Start a live review session and create an invitation link. This Preview "
            "does not synchronize visual media, shared notes, or media timecode. "
            f"{MEETING_DIRECT_CAPTURE_BOUNDARY}"
        ),
        join_description=(
            "Join a live review using one WebJam invitation link. This Preview does "
            "not synchronize visual media, shared notes, or media timecode. "
            f"{MEETING_DIRECT_CAPTURE_BOUNDARY}"
        ),
        local_description=(
            "Standalone visual review projects are not available in this Preview."
        ),
        helper=(
            "Preview: host or join a review. Standalone visual projects are not "
            "available yet."
        ),
        join_title="Join Review.",
        join_subtitle="Paste the WebJam review invitation your host sent you.",
    ),
    "art": _CreatorLaunchCopy(
        host="Host",
        join="Join",
        local="Standalone Art Unavailable",
        host_description=(
            "Open a room where artists talk and work, and create one "
            "invitation link that carries whatever you started. This Preview "
            "has no camera feed, no recorded take, and no frame-accurate "
            f"review. {MEETING_DIRECT_CAPTURE_BOUNDARY}"
        ),
        join_description=(
            "Join an art session with one WebJam invitation. The invitation "
            "carries whatever the host started, so there is nothing else to "
            f"choose here. {MEETING_DIRECT_CAPTURE_BOUNDARY}"
        ),
        local_description=(
            "Standalone Art projects are not available in this Preview."
        ),
        helper="Preview: talk and make art together.",
        join_title="Join the room.",
        join_subtitle="Paste the WebJam invitation your host sent you.",
    ),
}

if set(_CREATOR_LAUNCH_COPY) != {profile.key for profile in CREATOR_PROFILES}:
    # Launch is the first thing a person touches. Catch a profile added
    # without copy at import time instead of as a KeyError mid-selection.
    raise RuntimeError("Every creator profile requires launch copy.")


class StartCard(QCommandLinkButton):
    """One large, checkable card describing a way to begin.

    A card is a whole clickable rectangle rather than a radio row because the
    point of the Art start pass is that a person reads three short lines and
    hits one big thing, not that they aim at a small dot beside a paragraph.
    ``QCommandLinkButton`` is the stock control for a title plus a line of
    description, so it reports an honest size hint instead of needing labels
    nested inside a button that cannot measure them.
    """

    def __init__(self, start: CreatorStart, parent: Optional[QWidget] = None) -> None:
        # Qt reads "&" in button text as a mnemonic marker, which would render
        # "Talk & make" as "Talk _make". The label is product copy, so it is
        # escaped rather than reworded.
        super().__init__(start.label.replace("&", "&&"), start.summary, parent)
        self.start_key = start.key
        self.setObjectName("LaunchStartCard")
        self.setCheckable(True)
        # A card chooses; it never submits the dialog. Leaving autoDefault on
        # would let Return or focus hand the dialog's default action to a card
        # and away from Host.
        self.setAutoDefault(False)
        self.setDefault(False)
        # The stock control ships a decorative arrow. The card is already
        # obviously pressable, and the glyph only competes with the words.
        self.setIcon(QIcon())
        self.setIconSize(QSize(0, 0))
        # The stock hint assumes a wrapped description and comes out tall
        # enough to push Host and Join off a supported window. Bounding the
        # height keeps three cards, Host, and Join all on screen at the
        # 760x600 floor while staying a large target.
        self.setMinimumHeight(54)
        self.setMaximumHeight(64)
        self.setAccessibleName(start.label)
        self.setAccessibleDescription(f"{start.summary} {start.detail}")
        self.setToolTip(start.detail)


def default_musician_name(settings: AppSettings) -> str:
    """Return a useful identity without turning launch into a form."""

    def _accepted(value: object) -> str:
        try:
            return validate_jamulus_name(value).value
        except JamulusNameError:
            return ""

    configured = str(settings.musician_name or "").strip()
    try:
        configured_is_saved = Path(settings.config_file).expanduser().is_file()
    except OSError:
        configured_is_saved = False
    if configured and (configured != DEFAULT_JAMULUS_NAME or configured_is_saved):
        accepted = _accepted(configured)
        if accepted:
            return accepted
    if os.name == "posix":
        try:
            import pwd

            full_name = pwd.getpwuid(os.getuid()).pw_gecos.split(",", 1)[0].strip()
            name_parts = full_name.split(maxsplit=1)
            first_name = name_parts[0] if name_parts else ""
            # An unsaved OS account name is only a suggestion. Prefer the
            # short first-name form so a new musician starts with a one-line
            # Jamulus tile, while any explicitly saved name above is preserved
            # exactly even when Jamulus truthfully wraps it to a second line.
            for candidate in (first_name, full_name):
                accepted = _accepted(candidate)
                if accepted:
                    return accepted
        except (ImportError, KeyError, OSError):
            pass
    account = getpass.getuser().replace("_", " ").replace(".", " ").strip()
    account_parts = account.split(maxsplit=1)
    short_account = account_parts[0].title() if account_parts else ""
    for candidate in (short_account, account.title()):
        accepted = _accepted(candidate)
        if accepted:
            return accepted
    return "Musician"


def _installed_jamulus(settings: AppSettings) -> bool:
    for candidate in settings.jamulus_candidates:
        try:
            if Path(candidate).expanduser().is_file():
                return True
        except OSError:
            continue
    return False


def _windows_jamulus_installer(settings: AppSettings) -> str:
    if sys.platform != "win32" or _installed_jamulus(settings):
        return ""
    from services.bridge_service import _bundled_jamulus_installer

    return str(_bundled_jamulus_installer() or "")


def apply_host_defaults(settings: AppSettings) -> None:
    """Derive every host-side implementation detail from one role choice."""
    settings.musician_name = default_musician_name(settings)
    settings.host_server_enabled = True
    settings.jamulus_server = "127.0.0.1"
    settings.jamulus_port = DEFAULT_JAMULUS_PORT
    settings.jamulus_rpc_port = 22222
    settings.server_rpc_port = 22240
    settings.server_rpc_secret_file = str(hosted_server_secret_path())
    settings.takes_directory = str(hosted_server_recordings_dir())
    settings.webex_audio_mode = "talkback"
    # Recording Setup is an explicit host preference, not connection plumbing.
    # Keep it across launches so reopening WebJam cannot silently drop the two
    # isolated interface stems the musician asked us to capture.


def apply_join_invite(settings: AppSettings, invite: BandInvite) -> None:
    """Apply an invitation without exposing its connection components."""
    settings.musician_name = default_musician_name(settings)
    settings.host_server_enabled = False
    settings.jamulus_server = invite.host
    settings.jamulus_port = invite.port
    settings.jamulus_rpc_port = 22222
    settings.server_rpc_secret_file = ""
    if not settings.takes_directory:
        settings.takes_directory = str(Path.home() / "Music" / "WebJam Takes")
    settings.webex_audio_mode = "talkback"
    # Isolated local recording is an explicit musician preference. Joining a
    # band must never silently disable a choice already made in Recording
    # Setup; the host's authenticated recording signal controls when it runs.


def apply_remote_join_defaults(settings: AppSettings) -> None:
    """Persist only role/local defaults; remote endpoints stay runtime-only."""

    settings.musician_name = default_musician_name(settings)
    settings.host_server_enabled = False
    settings.jamulus_server = "127.0.0.1"
    settings.jamulus_port = DEFAULT_JAMULUS_PORT
    settings.jamulus_rpc_port = 22222
    settings.server_rpc_secret_file = ""
    if not settings.takes_directory:
        settings.takes_directory = str(Path.home() / "Music" / "WebJam Takes")
    settings.webex_audio_mode = "talkback"


class LaunchDialog(QDialog):
    """Three creator profiles and one pasted link after choosing Join.

    Host/Join is persisted once before the main window opens.  Reference
    Studio is an offline workspace choice and does not rewrite live-session
    settings.  Invitation bearers remain memory-only; only the non-secret
    host/port fields needed by the legacy LAN client are saved.
    """

    def __init__(
        self,
        settings: AppSettings,
        parent: QWidget | None = None,
        *,
        initial_invitation: Invitation | None = None,
        initial_invite_url: str = "",
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._submitting = False
        self._host_available = sys.platform == "darwin"
        self._jamulus_installer = _windows_jamulus_installer(settings)
        if self._jamulus_installer:
            LOGGER.info("Verified bundled Jamulus installer is available")
        self.selected_role = ""
        self.session_name = "Band Rehearsal"
        self.band_invite: BandInvite | None = None
        self.remote_invitation: RemoteInvitation | None = None
        self.setObjectName("LaunchDialog")
        self.setWindowTitle("WebJam")
        self.setModal(True)
        self.setMinimumSize(460, 480)
        # A 520 px client area plus a conservative 40 px native-title-bar
        # allowance remains inside the supported physical 760×600 floor.
        self.resize(620, 520)

        root = QVBoxLayout(self)
        root.setContentsMargins(Space.XXL, Space.MD, Space.XXL, Space.MD)
        root.setSpacing(Space.MD)

        brand_row = QHBoxLayout()
        brand_row.setContentsMargins(0, 0, 0, 0)
        brand_row.setSpacing(Space.SM)
        self._logo = BrandMark(30)
        self._logo.setObjectName("LaunchBrandMark")
        self._wordmark = QLabel('Web<span style="color: #BF5700;">Jam</span>')
        self._wordmark.setObjectName("LaunchLogo")
        self._wordmark.setTextFormat(Qt.TextFormat.RichText)
        self._wordmark.setAccessibleName("WebJam")
        self._wordmark.setAccessibleDescription("WebJam")
        brand_row.addStretch(1)
        brand_row.addWidget(self._logo)
        brand_row.addWidget(self._wordmark)
        brand_row.addStretch(1)
        root.addLayout(brand_row)

        name_row = QHBoxLayout()
        name_row.setContentsMargins(0, 0, 0, 0)
        name_row.setSpacing(Space.SM)
        name_label = QLabel("Your Jamulus name")
        name_label.setObjectName("LaunchNameLabel")
        self._name_input = QLineEdit(default_musician_name(settings))
        self._name_input.setObjectName("LaunchNameInput")
        self._name_input.setPlaceholderText("Short stage name")
        self._name_input.setAccessibleName("Your Jamulus musician name")
        name_label.setBuddy(self._name_input)
        name_row.addWidget(name_label)
        name_row.addWidget(self._name_input, 1)
        root.addLayout(name_row)
        self._name_preview = JamulusNamePreview(
            self._name_input,
            compact=True,
        )
        self._name_preview.setObjectName("LaunchNamePreview")
        root.addWidget(self._name_preview)
        self._name_error = QLabel("")
        self._name_error.setObjectName("LaunchError")
        self._name_error.setAccessibleName("Musician name error")
        self._name_error.setWordWrap(True)
        self._name_error.setVisible(False)
        root.addWidget(self._name_error)
        self._name_input.textChanged.connect(self._clear_name_error)

        self._pages = QStackedWidget()
        self._choice_page = self._build_choice_page()
        self._join_page = self._build_join_page()
        self._pages.addWidget(self._choice_page)
        self._pages.addWidget(self._join_page)
        root.addWidget(self._pages, 1)
        self.setTabOrder(self._name_input, self._creator_profile_selector)
        previous: QWidget = self._creator_profile_selector
        # Hidden cards are skipped by Qt, so chaining every profile's cards
        # keeps one correct order without rebuilding it on each selection.
        for cards in self._start_cards.values():
            for card in cards:
                self.setTabOrder(previous, card)
                previous = card
        self.setTabOrder(previous, self._host_button)
        self.setTabOrder(self._host_button, self._join_button)
        self.setTabOrder(self._join_button, self._studio_button)
        self.setTabOrder(self._studio_button, self._install_jamulus_button)

        if initial_invitation is not None and initial_invite_url:
            raise ValueError("provide one initial invitation")
        if initial_invitation is not None and not isinstance(
            initial_invitation, (BandInvite, RemoteInvitation)
        ):
            raise TypeError("initial_invitation must be typed")
        if initial_invitation is None and initial_invite_url:
            # Backward compatibility for v1/v2 callers only. The argv policy
            # rejects v3 before this dialog can retain it in a closure.
            initial_invitation = invitation_from_arguments(
                ["WebJam", initial_invite_url]
            )
        if initial_invitation is not None:
            QTimer.singleShot(
                0,
                lambda invitation=initial_invitation: self.accept_invitation(
                    invitation
                ),
            )

    def _build_choice_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        # The top-row trinity mark already carries the identity. Keep the
        # choice page compact enough for the editable Jamulus-name preview and
        # the optional Windows installer without squeezing role buttons.
        layout.setSpacing(Space.XS)

        self._choice_title = QLabel("Create together.")
        title = self._choice_title
        title.setObjectName("LaunchTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self._choice_subtitle = QLabel(
            "Choose a workflow, then start with one clear action."
        )
        self._choice_subtitle.setObjectName("LaunchSubtitle")
        self._choice_subtitle.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self._choice_subtitle.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(self._choice_subtitle)

        creator_row = QHBoxLayout()
        creator_row.setContentsMargins(0, 0, 0, 0)
        creator_row.setSpacing(Space.SM)
        self._creator_profile_label = QLabel("What are you creating?")
        self._creator_profile_label.setObjectName("LaunchCreatorProfileLabel")
        self._creator_profile_selector = QComboBox()
        self._creator_profile_selector.setObjectName("LaunchCreatorProfileSelector")
        self._creator_profile_selector.setAccessibleName("What are you creating?")
        self._creator_profile_selector.setAccessibleDescription(
            "Choose Music, Podcast and Voice, Review and Rehearsal, or Art. "
            "Each option states whether it is Ready or Preview."
        )
        self._creator_profile_label.setBuddy(self._creator_profile_selector)
        for profile in CREATOR_PROFILES:
            status = "Preview" if profile.is_preview else "Ready"
            self._creator_profile_selector.addItem(
                f"{profile.label} ({status})", profile.key
            )
        saved_profile = get_creator_profile_by_key_or_default(
            getattr(self._settings, "last_creator_profile_key", "music")
        )
        saved_index = self._creator_profile_selector.findData(saved_profile.key)
        if saved_index >= 0:
            self._creator_profile_selector.setCurrentIndex(saved_index)
        self._creator_profile_selector.currentIndexChanged.connect(
            self._apply_creator_profile_presentation
        )
        creator_row.addWidget(self._creator_profile_label)
        creator_row.addWidget(self._creator_profile_selector, 1)
        layout.addLayout(creator_row)
        layout.addStretch(1)
        layout.addWidget(self._build_start_cards())

        self._host_button = QPushButton()
        self._host_button.setObjectName("LaunchPrimary")
        self._host_button.setMinimumHeight(52)
        self._host_button.setDefault(True)
        self._join_button = QPushButton()
        self._join_button.setObjectName("LaunchSecondary")
        self._join_button.setMinimumHeight(52)
        self._studio_button = QPushButton()
        self._studio_button.setObjectName("LaunchSecondary")
        self._studio_button.setMinimumHeight(52)
        self._host_button.clicked.connect(self._host)
        self._join_button.clicked.connect(self.show_join)
        self._studio_button.clicked.connect(self._studio)
        layout.addWidget(self._host_button)
        layout.addWidget(self._join_button)
        layout.addWidget(self._studio_button)

        self._install_jamulus_button = QPushButton("Install Jamulus")
        self._install_jamulus_button.setObjectName("GhostButton")
        self._install_jamulus_button.setAccessibleName("Install Jamulus")
        self._install_jamulus_button.setAccessibleDescription(
            "Open the Jamulus installer included with this Windows build."
        )
        self._install_jamulus_button.clicked.connect(self._install_jamulus)
        self._install_jamulus_button.setVisible(bool(self._jamulus_installer))
        layout.addWidget(self._install_jamulus_button)

        self._choice_helper = QLabel()
        self._choice_helper.setObjectName("LaunchHelper")
        self._choice_helper.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self._choice_helper.setWordWrap(True)
        layout.addWidget(self._choice_helper)

        self._choice_error = QLabel("")
        self._choice_error.setObjectName("LaunchError")
        self._choice_error.setAccessibleName("Launch error")
        self._choice_error.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self._choice_error.setWordWrap(True)
        self._choice_error.setVisible(False)
        layout.addWidget(self._choice_error)
        layout.addStretch(1)
        self._apply_creator_profile_presentation()
        return page

    def _build_start_cards(self) -> QWidget:
        """Build one card group per profile that offers starts.

        Every profile's cards are built once and hidden, rather than rebuilt
        on each selection, so switching profiles cannot momentarily show a
        card belonging to the profile a person just left.
        """

        container = QWidget()
        container.setObjectName("LaunchStartCards")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Space.XS)

        self._start_cards: dict[str, list[StartCard]] = {}
        self._start_groups: dict[str, QButtonGroup] = {}
        self._start_container = container
        for profile in CREATOR_PROFILES:
            if not profile.starts:
                continue
            group = QButtonGroup(self)
            group.setExclusive(True)
            cards: list[StartCard] = []
            for start in profile.starts:
                card = StartCard(start, container)
                card.setVisible(False)
                card.toggled.connect(self._on_start_card_toggled)
                group.addButton(card)
                layout.addWidget(card)
                cards.append(card)
            self._start_cards[profile.key] = cards
            self._start_groups[profile.key] = group
        return container

    def _on_start_card_toggled(self, checked: bool) -> None:
        if checked:
            self._refresh_start_presentation()

    def _visible_start_cards(self) -> list[StartCard]:
        return self._start_cards.get(self._selected_creator_profile.key, [])

    def _apply_start_card_visibility(self) -> None:
        """Show only the selected profile's cards, with one already chosen."""

        selected_key = self._selected_creator_profile.key
        for profile_key, cards in self._start_cards.items():
            visible = profile_key == selected_key
            for card in cards:
                card.setVisible(visible)
                card.setEnabled(visible and not self._submitting)
        self._start_container.setVisible(bool(self._visible_start_cards()))

        cards = self._visible_start_cards()
        if not cards:
            return
        if not any(card.isChecked() for card in cards):
            remembered = str(
                getattr(self._settings, "last_creator_start_key", "") or ""
            )
            chosen = next(
                (card for card in cards if card.start_key == remembered), cards[0]
            )
            # Blocking signals keeps restoring a remembered choice from being
            # mistaken for the artist making one.
            was_blocked = chosen.blockSignals(True)
            chosen.setChecked(True)
            chosen.blockSignals(was_blocked)

    @property
    def selected_start_key(self) -> str:
        """The start the artist chose, or "" for a profile without starts."""

        profile = self._selected_creator_profile
        for card in self._start_cards.get(profile.key, []):
            if card.isChecked():
                return card.start_key
        default = profile.default_start
        return default.key if default is not None else ""

    @property
    def selected_start(self) -> CreatorStart | None:
        return self._selected_creator_profile.start_or_default(
            self.selected_start_key
        )

    def _refresh_start_presentation(self) -> None:
        """Bind Host to the chosen card without repeating the card's words.

        The card already states what it does, so the helper line below only
        carries what the card cannot: whether hosting is possible here at all.
        Saying the same sentence twice on one screen is noise, not clarity.
        """

        start = self.selected_start
        if start is None:
            return
        copy = _CREATOR_LAUNCH_COPY[self._selected_creator_profile.key]
        self._choice_helper.setText(
            "" if self._host_available else "Hosting is available in the macOS app."
        )
        self._host_button.setAccessibleDescription(
            f"Start {start.label} as the host. {start.detail} "
            f"{copy.host_description}"
        )

    def _install_jamulus(self) -> None:
        """Open only the checksum-pinned installer shipped in this package."""

        if not self._jamulus_installer:
            return
        from services.bridge_service import _is_pinned_jamulus_installer

        if not _is_pinned_jamulus_installer(self._jamulus_installer):
            self._jamulus_installer = ""
            self._install_jamulus_button.setEnabled(False)
            self._choice_error.setText(
                "The included Jamulus installer failed its integrity check. "
                "Re-extract an official WebJam download and try again."
            )
            self._choice_error.setVisible(True)
            self._announce_error(self._choice_error)
            return
        try:
            subprocess.Popen([self._jamulus_installer], shell=False)
        except OSError:
            self._choice_error.setText(
                "Jamulus couldn’t open. Re-extract WebJam and try Install Jamulus again."
            )
            self._choice_error.setVisible(True)
            self._announce_error(self._choice_error)
            return
        self._install_jamulus_button.setEnabled(False)
        self._choice_helper.setText(
            "Finish the Jamulus installer, then return here and choose Join a Jam."
        )

    def _build_join_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(Space.MD, Space.XL, Space.MD, 0)
        layout.setSpacing(Space.MD)

        self._join_title = QLabel()
        self._join_title.setObjectName("LaunchTitle")
        self._join_title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self._join_subtitle = QLabel()
        self._join_subtitle.setObjectName("LaunchSubtitle")
        self._join_subtitle.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self._join_subtitle.setWordWrap(True)
        layout.addWidget(self._join_title)
        layout.addWidget(self._join_subtitle)
        layout.addStretch(1)

        self._invite_input = QLineEdit()
        self._invite_input.setObjectName("LaunchInviteInput")
        # Version-2 invitations contain a private bearer credential. Keep the
        # value available to the parser without rendering it as ordinary text
        # on screen or exposing it as an ordinary editable value to assistive
        # technologies.
        self._invite_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._invite_input.setPlaceholderText("Paste your WebJam invite link")
        self._invite_input.setAccessibleName("WebJam invite link")
        self._invite_input.setAccessibleDescription(
            "Paste the complete invitation from your host."
        )
        self._invite_input.returnPressed.connect(self._join)
        self._invite_input.textChanged.connect(lambda *_: self._join_error.clear())
        layout.addWidget(self._invite_input)

        self._join_error = QLabel("")
        self._join_error.setObjectName("LaunchError")
        self._join_error.setAccessibleName("Join error")
        self._join_error.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self._join_error.setWordWrap(True)
        layout.addWidget(self._join_error)

        self._join_button_primary = QPushButton()
        self._join_button_primary.setObjectName("LaunchPrimary")
        self._join_button_primary.setMinimumHeight(48)
        self._join_button_primary.setDefault(True)
        self._join_button_primary.clicked.connect(self._join)
        layout.addWidget(self._join_button_primary)
        layout.addStretch(2)

        back = QPushButton("Back")
        back.setObjectName("GhostButton")
        back.clicked.connect(self.show_choices)
        layout.addWidget(back, 0, Qt.AlignmentFlag.AlignHCenter)
        self._apply_creator_profile_presentation()
        return page

    @property
    def showing_choices(self) -> bool:
        return self._pages.currentWidget() is self._choice_page

    @property
    def selected_creator_profile_key(self) -> str:
        """Return the canonical creator-profile key selected at launch."""

        key = self._creator_profile_selector.currentData()
        return get_creator_profile_by_key_or_default(key).key

    @property
    def _selected_creator_profile(self) -> CreatorProfile:
        return get_creator_profile_by_key_or_default(
            self._creator_profile_selector.currentData()
        )

    def _apply_creator_profile_presentation(self, *_args: object) -> None:
        """Keep every visible action honest for the selected creator profile."""

        profile = self._selected_creator_profile
        copy = _CREATOR_LAUNCH_COPY[profile.key]
        self._host_button.setText(copy.host)
        self._host_button.setAccessibleName(copy.host)
        host_description = copy.host_description
        if not self._host_available:
            host_description += " Hosting is available in the macOS app."
        self._host_button.setAccessibleDescription(host_description)
        self._host_button.setEnabled(self._host_available and not self._submitting)

        self._join_button.setText(copy.join)
        self._join_button.setAccessibleName(copy.join)
        self._join_button.setAccessibleDescription(copy.join_description)
        self._join_button.setEnabled(not self._submitting)

        local_available = profile.capabilities.local_multitrack
        self._studio_button.setText(copy.local)
        self._studio_button.setAccessibleName(copy.local)
        self._studio_button.setAccessibleDescription(copy.local_description)
        self._studio_button.setEnabled(local_available and not self._submitting)
        self._studio_button.setHidden(not local_available)

        helper = copy.helper
        if not self._host_available:
            helper += " Hosting is available in the macOS app."
        self._choice_helper.setText(helper)
        if hasattr(self, "_start_cards"):
            self._apply_start_card_visibility()
            # Three cards say "choose a workflow, then start with one clear
            # action" better than a headline and a line of prose above them
            # do, and they need the room those two were using. The wordmark at
            # the top already carries the identity.
            has_cards = bool(self._visible_start_cards())
            self._choice_title.setVisible(not has_cards)
            self._choice_subtitle.setVisible(not has_cards)
            self._refresh_start_presentation()
        if hasattr(self, "_join_title"):
            self._join_title.setText(copy.join_title)
            self._join_subtitle.setText(copy.join_subtitle)
            self._join_button_primary.setText(copy.join)
            self._join_button_primary.setAccessibleName(copy.join)
            self._join_button_primary.setAccessibleDescription(copy.join_description)

    def show_choices(self) -> None:
        self._restore_submission()
        self._invite_input.clear()
        self._pages.setCurrentWidget(self._choice_page)
        self._creator_profile_selector.setFocus()

    def show_join(self) -> None:
        if not self._submitting:
            self._join_error.clear()
        self._pages.setCurrentWidget(self._join_page)
        self._invite_input.setFocus()

    def _persist_role_choice(self, candidate: AppSettings) -> bool:
        """Commit the non-audio role intent before starting the main journey."""

        candidate.last_creator_profile_key = self.selected_creator_profile_key
        candidate.last_creator_start_key = self.selected_start_key
        try:
            save_settings(candidate)
        except OSError:
            self._choice_error.setText(
                "WebJam couldn’t save this choice. Check available disk space and try again."
            )
            self._choice_error.setVisible(True)
            self._announce_error(self._choice_error)
            return False
        self._settings = candidate
        return True

    def _host(self) -> None:
        musician_name = self._validated_musician_name()
        if musician_name is None:
            return
        if not self._begin_submission(self._host_button, "Starting…"):
            return
        candidate = deepcopy(self._settings)
        apply_host_defaults(candidate)
        candidate.musician_name = musician_name
        if not self._persist_role_choice(candidate):
            self._restore_submission()
            return
        self.selected_role = "host"
        self.session_name = "Band Rehearsal"
        self.band_invite = None
        self.remote_invitation = None
        self.accept()

    def _studio(self) -> None:
        if not self._begin_submission(self._studio_button, "Opening…"):
            return
        candidate = deepcopy(self._settings)
        if not self._persist_role_choice(candidate):
            self._restore_submission()
            return
        self.selected_role = "studio"
        preset = self._selected_creator_profile.default_studio_preset
        self.session_name = (
            "Reference Studio"
            if self.selected_creator_profile_key == "music"
            else preset.label if preset is not None else "Local Recording"
        )
        self.band_invite = None
        self.remote_invitation = None
        self.accept()

    def _join(self) -> None:
        value = self._invite_input.text()
        self._invite_input.clear()
        self.accept_invite(value)

    def accept_invite(self, value: str) -> bool:
        """Compatibility wrapper for an explicit paste into the one field."""
        if not self._begin_submission(self._join_button_primary, "Joining…"):
            return False
        raw = str(value or "")
        self._invite_input.clear()
        try:
            invitation = parse_invitation_at_ingress(
                raw,
                source=InvitationSource.PASTE,
            )
        except InvitationIngressError as exc:
            self._pages.setCurrentWidget(self._join_page)
            is_remote_shape = any(
                part == "v=3" for part in raw.partition("?")[2].split("&")
            )
            if not is_remote_shape:
                self._invite_input.setText(raw)
            self._join_error.setText(str(exc))
            self._announce_error(self._join_error, focus=self._invite_input)
            self._restore_submission()
            return False
        return self.accept_invitation(invitation, submission_started=True)

    def accept_invitation(
        self,
        invitation: Invitation,
        *,
        submission_started: bool = False,
    ) -> bool:
        """Accept one already-parsed invitation without retaining its URL."""

        if not isinstance(invitation, (BandInvite, RemoteInvitation)):
            raise TypeError("invitation must be typed")
        musician_name = self._validated_musician_name()
        if musician_name is None:
            if submission_started:
                self._restore_submission()
            return False
        if not submission_started and not self._begin_submission(
            self._join_button_primary, "Joining…"
        ):
            return False
        self._invite_input.clear()
        candidate = deepcopy(self._settings)
        if isinstance(invitation, RemoteInvitation):
            apply_remote_join_defaults(candidate)
            session_name = "Band Rehearsal"
        else:
            apply_join_invite(candidate, invitation)
            session_name = invitation.session_name
        candidate.musician_name = musician_name
        if not self._persist_role_choice(candidate):
            # _persist_role_choice() owns the same save failure for Host and
            # Join, so it initially writes the role-choice error. A pasted or
            # cold deep-link invitation returns to the Join page, where that
            # label is hidden. Move the already-safe message to the visible
            # Join error before restoring the retry action.
            message = self._choice_error.text()
            self._choice_error.clear()
            self._choice_error.setVisible(False)
            self._pages.setCurrentWidget(self._join_page)
            self._invite_input.clear()
            self._restore_submission()
            self._join_error.setText(
                message
                or (
                    "WebJam couldn’t save this choice. Check available disk "
                    "space and try again."
                )
            )
            self._announce_error(self._join_error, focus=self._invite_input)
            return False
        self.selected_role = "join"
        self.session_name = session_name
        self.band_invite = invitation if isinstance(invitation, BandInvite) else None
        self.remote_invitation = (
            invitation if isinstance(invitation, RemoteInvitation) else None
        )
        self._invite_input.clear()
        self.accept()
        return True

    def show_ingress_error(self, message: str) -> None:
        """Show only fixed-copy errors emitted by the application ingress."""

        self._pages.setCurrentWidget(self._join_page)
        self._invite_input.clear()
        self._join_error.setText(
            str(message or "WebJam could not open that invitation.")
        )
        self._announce_error(self._join_error, focus=self._invite_input)

    def take_remote_invitation(self) -> RemoteInvitation | None:
        """Move the typed bearer to the runtime owner exactly once."""

        invitation = self.remote_invitation
        self.remote_invitation = None
        return invitation

    def done(self, result: int) -> None:
        self._invite_input.clear()
        super().done(result)

    def _begin_submission(self, button: QPushButton, label: str) -> bool:
        """Accept exactly one Host/Join activation until it succeeds or fails."""
        if self._submitting:
            return False
        self._submitting = True
        self._host_button.setEnabled(False)
        self._join_button.setEnabled(False)
        self._studio_button.setEnabled(False)
        self._join_button_primary.setEnabled(False)
        self._creator_profile_selector.setEnabled(False)
        for cards in self._start_cards.values():
            for card in cards:
                card.setEnabled(False)
        self._name_input.setEnabled(False)
        button.setText(label)
        button.setAccessibleName(label)
        return True

    def _restore_submission(self) -> None:
        self._submitting = False
        self._creator_profile_selector.setEnabled(True)
        self._apply_creator_profile_presentation()
        self._name_input.setEnabled(True)
        if hasattr(self, "_join_button_primary"):
            self._join_button_primary.setEnabled(True)

    def _validated_musician_name(self) -> str | None:
        try:
            return validate_jamulus_name(self._name_input.text()).value
        except JamulusNameError as exc:
            self._name_error.setText(str(exc))
            self._name_error.setVisible(True)
            self._announce_error(self._name_error, focus=self._name_input)
            return None

    def _clear_name_error(self, *_args: object) -> None:
        self._name_error.clear()
        self._name_error.setAccessibleDescription("")
        self._name_error.setVisible(False)

    @staticmethod
    def _announce_error(label: QLabel, *, focus: QWidget | None = None) -> None:
        label.setAccessibleDescription(label.text())
        try:
            QAccessible.updateAccessibility(
                QAccessibleEvent(label, QAccessible.Event.DescriptionChanged)
            )
        except (RuntimeError, TypeError):
            pass
        if focus is not None:
            focus.setFocus(Qt.FocusReason.OtherFocusReason)
