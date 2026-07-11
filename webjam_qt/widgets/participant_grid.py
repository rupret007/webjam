"""ParticipantGrid — responsive grid of ParticipantCards with flow layout."""

from __future__ import annotations

from typing import Dict, Iterable, Optional

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
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
        x = effective.x()
        y = effective.y()
        row_height = 0

        for item in self._items:
            widget = item.widget()
            if widget is None:
                continue
            item_size = item.sizeHint()
            next_x = x + item_size.width() + self._h_space
            if next_x - self._h_space > effective.right() and row_height > 0:
                x = effective.x()
                y = y + row_height + self._v_space
                next_x = x + item_size.width() + self._h_space
                row_height = 0

            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), item_size))

            x = next_x
            row_height = max(row_height, item_size.height())

        return y + row_height - rect.y() + margins.bottom()


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
        self._empty_state = self._build_empty_state(container)
        self._flow.addWidget(self._empty_state)
        self.set_session_state(SessionUiState.idle())

    def _build_empty_state(self, parent: QWidget) -> QFrame:
        state = QFrame(parent)
        state.setObjectName("StageEmptyState")
        state.setMinimumSize(440, 250)
        state.setAccessibleName("Live session status")

        self._empty_eyebrow = QLabel("NOT CONNECTED")
        self._empty_eyebrow.setObjectName("StageEmptyEyebrow")
        self._empty_title = QLabel()
        self._empty_title.setObjectName("StageEmptyTitle")
        self._empty_title.setWordWrap(True)
        self._empty_message = QLabel()
        self._empty_message.setObjectName("StageEmptyMessage")
        self._empty_message.setWordWrap(True)

        self._empty_primary = QPushButton("Start Audio")
        self._empty_primary.setObjectName("AudioButton")
        self._empty_primary.setAccessibleName("Start Jamulus audio")
        self._empty_primary.clicked.connect(self.start_audio_requested.emit)
        self._empty_ready = QPushButton("Run Ready Check")
        self._empty_ready.setObjectName("GhostButton")
        self._empty_ready.clicked.connect(self.ready_check_requested.emit)

        actions = QHBoxLayout()
        actions.setSpacing(Space.SM)
        actions.addWidget(self._empty_primary)
        actions.addWidget(self._empty_ready)
        actions.addStretch(1)

        layout = QVBoxLayout(state)
        layout.setContentsMargins(Space.XL, Space.XL, Space.XL, Space.XL)
        layout.setSpacing(Space.MD)
        layout.addStretch(1)
        layout.addWidget(self._empty_eyebrow)
        layout.addWidget(self._empty_title)
        layout.addWidget(self._empty_message)
        layout.addLayout(actions)
        layout.addStretch(1)
        return state

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def set_participants(self, participants: Iterable[ParticipantPresentation]) -> None:
        incoming = {p.channel_id: p for p in participants}

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

    def set_empty_state(
        self,
        state: str,
        title: str,
        message: str,
        *,
        primary_text: str = "Start Audio",
        primary_enabled: bool = True,
        show_primary: bool = True,
        show_ready_check: bool = True,
    ) -> None:
        """Show persistent session truth when no real participants exist."""
        self._empty_state.setProperty("sessionState", state)
        self._empty_eyebrow.setText(state.replace("_", " ").upper())
        self._empty_title.setText(title)
        self._empty_message.setText(message)
        self._empty_primary.setText(primary_text)
        self._empty_primary.setEnabled(primary_enabled)
        self._empty_primary.setVisible(show_primary)
        self._empty_ready.setVisible(show_ready_check)
        self._empty_state.setAccessibleDescription(f"{title}. {message}")
        self._empty_state.setVisible(not bool(self._cards))
        style = self._empty_state.style()
        style.unpolish(self._empty_state)
        style.polish(self._empty_state)

    def set_session_state(self, state: SessionUiState) -> None:
        """Render a centralized Live-workspace state snapshot."""
        self.set_empty_state(
            state.phase.value,
            state.title,
            state.message,
            primary_text=state.primary_text,
            primary_enabled=state.primary_enabled,
            show_ready_check=state.show_ready_check,
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
