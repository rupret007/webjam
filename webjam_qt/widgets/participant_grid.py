"""ParticipantGrid — responsive grid of ParticipantCards with flow layout."""

from __future__ import annotations

import math
from typing import Dict, Iterable, Optional

from PySide6 import QtGui
from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QAccessible, QAccessibleEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
    QWidgetItem,
)

from webjam_qt.theme.tokens import Space
from webjam_qt.session_state import SessionUiState
from webjam_qt.widgets.participant_card import ParticipantCard, ParticipantPresentation


class _FlowLayout(QLayout):
    """
    Wrap child widgets onto successive rows based on available width.

    Minimal fork of the Qt FlowLayout example — just enough for our grid.
    """

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        margin: int = 0,
        h_spacing: int = Space.LG,
        v_spacing: int = Space.LG,
    ) -> None:
        super().__init__(parent)
        if parent is not None:
            self.setContentsMargins(margin, margin, margin, margin)
        self._h_space = h_spacing
        self._v_space = v_spacing
        self._items: list[QWidgetItem] = []

    # ------------------------------------------------------------------
    # QLayout overrides
    # ------------------------------------------------------------------
    def addItem(self, item) -> None:  # noqa: N802
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):  # noqa: N802
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int):  # noqa: N802
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self):  # noqa: N802
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self) -> QSize:  # noqa: N802
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        size += QSize(margins.left() + margins.right(), margins.top() + margins.bottom())
        return size

    def _do_layout(self, rect: QRect, *, test_only: bool) -> int:
        margins = self.contentsMargins()
        effective = rect.adjusted(
            margins.left(), margins.top(), -margins.right(), -margins.bottom()
        )
        items = [item for item in self._items if item.widget() is not None]
        count = len(items)
        if count == 0:
            return margins.top() + margins.bottom()

        # Equal-view meeting geometry: fewer musicians become larger, while
        # a small band settles into a predictable balanced grid.
        if count == 1:
            target_columns, max_width, max_height = 1, 780, 440
        elif count == 2:
            target_columns, max_width, max_height = 2, 620, 390
        elif count <= 4:
            target_columns, max_width, max_height = 2, 560, 320
        elif count <= 6:
            target_columns, max_width, max_height = 3, 440, 280
        else:
            target_columns = max(3, math.ceil(math.sqrt(count)))
            max_width, max_height = 400, 260

        available_width = max(1, effective.width())
        # Never force a 3-column meeting layout into a window that can only
        # show two complete people.  Horizontal scrolling is deliberately
        # disabled, so the column count must follow the actual viewport.
        minimum_tile_width = ParticipantCard.CARD_MIN_WIDTH
        columns_that_fit = max(
            1,
            (available_width + self._h_space)
            // (minimum_tile_width + self._h_space),
        )
        columns = min(target_columns, columns_that_fit)
        rows = math.ceil(count / columns)
        cell_width = max(
            1,
            (available_width - self._h_space * (columns - 1)) // columns,
        )
        tile_width = max(minimum_tile_width, min(max_width, cell_width))
        preferred_height = max(ParticipantCard.CARD_MIN_HEIGHT, int(tile_width * 0.58))
        if effective.height() > 0:
            cell_height = max(
                1,
                (effective.height() - self._v_space * (rows - 1)) // rows,
            )
            tile_height = max(
                ParticipantCard.CARD_MIN_HEIGHT,
                min(max_height, preferred_height, cell_height),
            )
        else:
            tile_height = max(
                ParticipantCard.CARD_MIN_HEIGHT,
                min(max_height, preferred_height),
            )

        grid_height = rows * tile_height + self._v_space * (rows - 1)
        y = effective.y()
        if effective.height() > grid_height:
            y += (effective.height() - grid_height) // 2

        index = 0
        for _row in range(rows):
            row_count = min(columns, count - index)
            row_width = row_count * tile_width + self._h_space * (row_count - 1)
            x = effective.x() + max(0, (effective.width() - row_width) // 2)
            for _column in range(row_count):
                if not test_only:
                    items[index].setGeometry(
                        QRect(QPoint(x, y), QSize(tile_width, tile_height))
                    )
                x += tile_width + self._h_space
                index += 1
            y += tile_height + self._v_space

        return grid_height + margins.top() + margins.bottom()


class ParticipantGrid(QScrollArea):
    """
    Scrollable grid of participant cards, with a flow layout that wraps on resize.

    Exposes a minimal CRUD-style API: ``set_participants``, ``update_level``.
    Signals from child cards (fader_changed, mute_toggled, solo_toggled) are
    re-emitted here so the controller can connect to one place.
    """

    # Re-emitted from child ParticipantCards so the controller wires up once
    fader_changed = Signal(int, int)    # channel_id, level
    mute_toggled  = Signal(int, bool)   # channel_id, muted
    solo_toggled  = Signal(int, bool)   # channel_id, solo
    ready_check_requested = Signal()
    start_audio_requested = Signal()
    practice_requested = Signal()
    microphone_settings_requested = Signal()
    participants_changed = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        container = QWidget()
        container.setObjectName("Stage")
        self._flow = _FlowLayout(
            container,
            margin=Space.LG,
            h_spacing=Space.LG,
            v_spacing=Space.LG,
        )
        container.setLayout(self._flow)
        container.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self.setWidget(container)

        self._cards: Dict[int, ParticipantCard] = {}
        # The hero lobby card floats centered over the (empty) stage rather
        # than sitting in the flow layout, which packs children top-left.
        self._empty_state = self._build_empty_state(self.viewport())
        self.set_session_state(SessionUiState.idle())

    def _build_empty_state(self, parent: QWidget) -> QFrame:
        state = QFrame(parent)
        state.setObjectName("StageEmptyState")
        state.setMinimumWidth(320)
        state.setMaximumWidth(620)
        state.setAccessibleName("Live session status")

        self._empty_eyebrow = QLabel("NOT CONNECTED")
        self._empty_eyebrow.setObjectName("StageEmptyEyebrow")
        self._empty_eyebrow.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self._empty_title = QLabel()
        self._empty_title.setObjectName("StageEmptyTitle")
        self._empty_title.setWordWrap(True)
        self._empty_title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self._empty_message = QLabel()
        self._empty_message.setObjectName("StageEmptyMessage")
        self._empty_message.setWordWrap(True)
        self._empty_message.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        self._empty_primary = QPushButton("Start Session")
        self._empty_primary.setObjectName("AudioButton")
        self._empty_primary.setAccessibleName("Start the band session")
        self._empty_primary_action = "start"
        self._empty_primary.clicked.connect(self._on_empty_primary)
        self._empty_practice = QPushButton("Practice Solo")
        self._empty_practice.setObjectName("GhostButton")
        self._empty_practice.setAccessibleName("Start a private practice session")
        self._empty_practice.clicked.connect(self.practice_requested.emit)
        self._empty_ready = QPushButton("Run Band Check")
        self._empty_ready.setObjectName("GhostButton")
        self._empty_ready.setAccessibleName("Run Band Check")
        self._empty_ready.clicked.connect(self.ready_check_requested.emit)

        actions = QHBoxLayout()
        actions.setSpacing(Space.SM)
        actions.addStretch(1)
        actions.addWidget(self._empty_primary)
        actions.addWidget(self._empty_practice)
        actions.addWidget(self._empty_ready)
        actions.addStretch(1)

        self._empty_hint = QLabel()
        self._empty_hint.setObjectName("StageEmptyHint")
        self._empty_hint.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self._empty_hint.setVisible(False)

        layout = QVBoxLayout(state)
        layout.setContentsMargins(Space.XXL, Space.XXL, Space.XXL, Space.XXL)
        layout.setSpacing(Space.MD)
        layout.addWidget(self._empty_eyebrow)
        layout.addWidget(self._empty_title)
        layout.addWidget(self._empty_message)
        layout.addSpacing(Space.SM)
        layout.addLayout(actions)
        layout.addWidget(self._empty_hint)
        return state

    def _center_empty_state(self) -> None:
        """Keep the hero card optically centered over the stage viewport."""
        # A comfortably readable hero on desktop that shrinks with the actual
        # viewport instead of imposing a fixed app-wide minimum width.
        responsive_target = max(360, int(self.viewport().width() * 0.56))
        width = max(
            self._empty_state.minimumWidth(),
            min(responsive_target, self._empty_state.maximumWidth()),
        )
        width = min(width, max(320, self.viewport().width() - 2 * Space.LG))
        layout = self._empty_state.layout()
        height = layout.heightForWidth(width) if layout is not None else -1
        height = max(height, self._empty_state.minimumSizeHint().height())
        x = max(Space.LG, (self.viewport().width() - width) // 2)
        # Slightly above true center reads better in a tall stage.
        y = max(Space.LG, int((self.viewport().height() - height) * 0.42))
        self._empty_state.setGeometry(x, y, width, height)
        self._empty_state.raise_()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._center_empty_state()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def set_participants(self, participants: Iterable[ParticipantPresentation]) -> None:
        incoming = {p.channel_id: p for p in participants}
        previous_names = {
            channel_id: card._presentation.name
            for channel_id, card in self._cards.items()
        }

        # Remove cards not in the new set
        for channel_id in list(self._cards.keys()):
            if channel_id not in incoming:
                self._remove_card(channel_id)

        # Upsert
        for channel_id, presentation in incoming.items():
            if channel_id in self._cards:
                self._cards[channel_id].update_presentation(presentation)
            else:
                self._add_card(presentation)
        self._empty_state.setVisible(not bool(incoming))
        self._center_empty_state()
        self.setAccessibleDescription(
            f"{len(incoming)} musician{'s' if len(incoming) != 1 else ''} connected."
        )
        joined = [
            item.name for channel_id, item in incoming.items()
            if channel_id not in previous_names
        ]
        left = [
            name for channel_id, name in previous_names.items()
            if channel_id not in incoming
        ]
        messages = [f"{name} joined the jam." for name in joined]
        messages.extend(f"{name} left the jam." for name in left)
        if messages and self.isVisible():
            self._announce(" ".join(messages))
        self.participants_changed.emit()

    def set_empty_state(
        self,
        state: str,
        title: str,
        message: str,
        *,
        primary_text: str = "Start Session",
        primary_enabled: bool = True,
        show_primary: bool = True,
        show_ready_check: bool = True,
        show_practice: bool = False,
        hint: str = "",
        primary_action: str = "start",
    ) -> None:
        """Show persistent session truth when no real participants exist."""
        self._empty_state.setProperty("sessionState", state)
        phase_labels = {
            "not_connected": "READY",
            "connecting": "STARTING",
            "practice": "PRIVATE PRACTICE",
            "reconnecting": "RECONNECTING",
            "error": "NEEDS ATTENTION",
            "ending": "ENDING",
            "ended": "ENDED",
        }
        self._empty_eyebrow.setText(
            phase_labels.get(state, state.replace("_", " ").upper())
        )
        self._empty_title.setText(title)
        self._empty_message.setText(message)
        # Escape "&": QPushButton treats it as a mnemonic marker.
        self._empty_primary.setText(primary_text.replace("&", "&&"))
        self._empty_primary.setEnabled(primary_enabled)
        self._empty_primary.setVisible(show_primary)
        self._empty_primary_action = str(primary_action or "start")
        self._empty_ready.setVisible(show_ready_check)
        self._empty_practice.setVisible(show_practice)
        self._empty_hint.setText(hint)
        self._empty_hint.setVisible(bool(hint))
        self._empty_state.setAccessibleDescription(f"{title}. {message}")
        self._empty_state.setVisible(not bool(self._cards))
        style = self._empty_state.style()
        style.unpolish(self._empty_state)
        style.polish(self._empty_state)
        self._center_empty_state()

    def set_session_state(self, state: SessionUiState) -> None:
        """Render a centralized Live-workspace state snapshot."""
        self.set_empty_state(
            state.phase.value,
            state.title,
            state.message,
            primary_text=state.primary_text,
            primary_enabled=state.primary_enabled,
            show_primary=state.show_primary,
            show_ready_check=state.show_ready_check,
            show_practice=state.show_practice,
            hint=state.hint,
            primary_action=state.primary_action,
        )

    def update_level(self, channel_id: int, level: float) -> None:
        card = self._cards.get(channel_id)
        if card is not None:
            card.set_audio_level(level)

    def tick_all_meters(self) -> None:
        """Drive one decay step on every card's level meter.

        Invoked by a single application-wide QTimer in ApplicationController
        instead of each LevelMeter owning its own timer.  With N cards this
        cuts timer events from N to 1 per tick.
        """
        for card in self._cards.values():
            card.tick_meter()

    def cards(self) -> list[ParticipantCard]:
        return list(self._cards.values())

    def _on_empty_primary(self) -> None:
        if self._empty_primary_action == "microphone_settings":
            self.microphone_settings_requested.emit()
        else:
            self.start_audio_requested.emit()

    def _announce(self, message: str) -> None:
        event_type = getattr(QtGui, "QAccessibleAnnouncementEvent", None)
        try:
            if event_type is not None:
                QAccessible.updateAccessibility(event_type(self, message))
            else:
                self.setAccessibleDescription(message)
                QAccessible.updateAccessibility(
                    QAccessibleEvent(self, QAccessible.Event.DescriptionChanged)
                )
        except (RuntimeError, TypeError):
            pass

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _add_card(self, presentation: ParticipantPresentation) -> None:
        card = ParticipantCard(presentation)
        # Re-emit card signals at the grid level so the controller connects once
        card.fader_changed.connect(self.fader_changed)
        card.mute_toggled.connect(self.mute_toggled)
        card.solo_toggled.connect(self.solo_toggled)
        self._flow.addWidget(card)
        self._cards[presentation.channel_id] = card

    def _remove_card(self, channel_id: int) -> None:
        card = self._cards.pop(channel_id, None)
        if card is None:
            return
        # Explicitly disconnect the signal forwards we wired in _add_card.
        # Without this, the connection survives the deleteLater() and Qt
        # accumulates dangling slots over a long session of join/leave churn.
        try:
            card.fader_changed.disconnect(self.fader_changed)
            card.mute_toggled.disconnect(self.mute_toggled)
            card.solo_toggled.disconnect(self.solo_toggled)
        except (TypeError, RuntimeError):
            # Already disconnected (Qt raises) — safe to ignore.
            pass
        self._flow.removeWidget(card)
        card.setParent(None)
        card.deleteLater()
