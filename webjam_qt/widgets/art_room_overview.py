"""Art's room context and offered activities, separate from the Music mixer."""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtGui import QAccessible, QAccessibleEvent
from PySide6.QtWidgets import (
    QBoxLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core.art_room_overview import ArtRoomOverview
from webjam_qt.theme.tokens import Space


class ArtRoomOverviewWidget(QScrollArea):
    """Render current room facts without implying an audio or video roster."""

    activity_requested = Signal(str)
    conversation_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ArtRoomOverview")
        self.setAccessibleName("Art room overview")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._overview: ArtRoomOverview | None = None
        self._announcement = ""

        body = QWidget()
        body.setObjectName("ArtRoomOverviewBody")
        self.setWidget(body)
        self._body_layout = QVBoxLayout(body)
        self._body_layout.setContentsMargins(Space.XL, Space.LG, Space.XL, Space.LG)
        self._body_layout.addStretch(1)

        self._content = QFrame()
        self._content.setObjectName("ArtRoomOverviewContent")
        self._content.setMaximumWidth(760)
        self._content.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum
        )
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(Space.LG)
        content_row = QHBoxLayout()
        content_row.addStretch(1)
        content_row.addWidget(self._content, 1000)
        content_row.addStretch(1)
        self._body_layout.addLayout(content_row)
        self._body_layout.addStretch(2)

        self._phase = self._label("ArtRoomOverviewPhase", "Room connection phase")
        self._role = self._label("ArtRoomOverviewRole", "Your room role")
        context = QHBoxLayout()
        context.setSpacing(Space.LG)
        context.addWidget(self._phase)
        context.addStretch(1)
        context.addWidget(self._role)
        self._content_layout.addLayout(context)
        self._title = self._label("ArtRoomOverviewTitle", "Art room")
        self._content_layout.addWidget(self._title)

        connection = QVBoxLayout()
        connection.setSpacing(Space.XS)
        self._connection = self._label("ArtRoomOverviewHeading", "Room connection")
        self._connection_detail = self._label(
            "ArtRoomOverviewDetail", "Room connection details"
        )
        connection.addWidget(self._connection)
        connection.addWidget(self._connection_detail)
        self._content_layout.addLayout(connection)

        divider = QFrame()
        divider.setObjectName("ArtRoomOverviewDivider")
        divider.setFixedHeight(1)
        self._content_layout.addWidget(divider)

        activity = QVBoxLayout()
        activity.setSpacing(Space.XS)
        self._activity = self._label("ArtRoomOverviewHeading", "Room activity")
        self._activity_detail = self._label(
            "ArtRoomOverviewDetail", "Room activity details"
        )
        activity.addWidget(self._activity)
        activity.addWidget(self._activity_detail)
        self._activity_row = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self._activity_row.setSpacing(Space.LG)
        self._activity_row.addLayout(activity, 1)
        self._activity_button = QPushButton()
        self._activity_button.setObjectName("PrimaryButton")
        self._activity_button.setVisible(False)
        self._activity_button.clicked.connect(self._open_activity)
        self._activity_row.addWidget(self._activity_button)
        self._content_layout.addLayout(self._activity_row)

        self._secondary_activity_row = QFrame()
        secondary_layout = QBoxLayout(
            QBoxLayout.Direction.LeftToRight, self._secondary_activity_row
        )
        secondary_layout.setContentsMargins(0, 0, 0, 0)
        secondary_layout.setSpacing(Space.LG)
        self._secondary_activity_layout = secondary_layout
        secondary_text = QVBoxLayout()
        secondary_text.setSpacing(Space.XS)
        self._secondary_activity = self._label(
            "ArtRoomOverviewHeading", "Other room activity"
        )
        self._secondary_activity_detail = self._label(
            "ArtRoomOverviewDetail", "Other room activity details"
        )
        secondary_text.addWidget(self._secondary_activity)
        secondary_text.addWidget(self._secondary_activity_detail)
        secondary_layout.addLayout(secondary_text, 1)
        self._secondary_activity_button = QPushButton()
        self._secondary_activity_button.setObjectName("GhostButton")
        self._secondary_activity_button.clicked.connect(self._open_secondary_activity)
        secondary_layout.addWidget(self._secondary_activity_button)
        self._secondary_activity_row.hide()
        self._content_layout.addWidget(self._secondary_activity_row)

        self._actions = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self._actions.setSpacing(Space.SM)
        self._conversation_button = QPushButton("Conversation")
        self._conversation_button.setObjectName("GhostButton")
        self._conversation_button.setAccessibleName("Show Conversation controls")
        self._conversation_button.setAccessibleDescription(
            "Open the existing Conversation panel. Opening the panel does not "
            "open a meeting or change its audio."
        )
        self._conversation_button.setToolTip(
            "Optional. Show Conversation controls to talk and show your work."
        )
        self._conversation_button.setEnabled(False)
        self._conversation_button.clicked.connect(self._open_conversation)
        for button in self._navigation_buttons():
            button.installEventFilter(self)
            button.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self._actions.addWidget(self._conversation_button)
        self._actions.addStretch(1)
        self._content_layout.addLayout(self._actions)
        QWidget.setTabOrder(self._activity_button, self._secondary_activity_button)
        QWidget.setTabOrder(self._secondary_activity_button, self._conversation_button)

    @staticmethod
    def _label(object_name: str, accessible_name: str) -> QLabel:
        label = QLabel()
        label.setObjectName(object_name)
        label.setAccessibleName(accessible_name)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        label.setMinimumWidth(0)
        label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        return label

    def set_overview(self, overview: ArtRoomOverview) -> None:
        self._overview = overview
        self.setProperty("roomPhase", overview.phase)
        for label, text in (
            (self._phase, overview.phase_label),
            (self._title, overview.title),
            (self._role, overview.role_label),
            (self._connection, overview.connection_label),
            (self._connection_detail, overview.connection_detail),
            (self._activity, overview.activity_label),
            (self._activity_detail, overview.activity_detail),
            (self._secondary_activity, overview.secondary_activity_label),
            (self._secondary_activity_detail, overview.secondary_activity_detail),
        ):
            label.setText(text)
            label.setAccessibleDescription(text)
            label.setVisible(bool(text))
        offered = bool(overview.activity_action and overview.activity_action_label)
        self._activity_button.setText(overview.activity_action_label.replace("&", "&&"))
        self._activity_button.setAccessibleName(overview.activity_action_label)
        self._activity_button.setAccessibleDescription(overview.activity_detail)
        self._activity_button.setToolTip(overview.activity_detail)
        self._activity_button.setVisible(offered)
        self._activity_button.setEnabled(offered and overview.activity_enabled)
        secondary_offered = bool(
            overview.secondary_activity_action and overview.secondary_activity_action_label
        )
        secondary = self._secondary_activity_button
        secondary.setText(overview.secondary_activity_action_label.replace("&", "&&"))
        secondary.setAccessibleName(overview.secondary_activity_action_label)
        secondary.setAccessibleDescription(overview.secondary_activity_detail)
        secondary.setToolTip(overview.secondary_activity_detail)
        secondary.setEnabled(secondary_offered and overview.secondary_activity_enabled)
        self._secondary_activity_row.setVisible(secondary_offered)
        self._conversation_button.setEnabled(overview.conversation_enabled)
        secondary_description = (
            (overview.secondary_activity_label, overview.secondary_activity_detail)
            if secondary_offered else ()
        )
        description = ". ".join(
            text.rstrip(".") for text in (
                overview.phase_label, overview.role_label,
                overview.connection_label, overview.connection_detail,
                overview.activity_label, overview.activity_detail,
                *secondary_description,
            ) if text
        )
        self.setAccessibleDescription(description)
        if description != self._announcement:
            self._announcement = description
            if self.isVisible():
                try:
                    QAccessible.updateAccessibility(
                        QAccessibleEvent(self, QAccessible.Event.DescriptionChanged)
                    )
                except (RuntimeError, TypeError):
                    pass
        self._sync_density()

    def _open_activity(self) -> None:
        overview = self._overview
        if overview is not None and overview.activity_enabled and overview.activity_action:
            self.activity_requested.emit(overview.activity_action)

    def _open_secondary_activity(self) -> None:
        overview = self._overview
        if (
            overview is not None and overview.secondary_activity_enabled
            and overview.secondary_activity_action
        ):
            self.activity_requested.emit(overview.secondary_activity_action)

    def _open_conversation(self) -> None:
        if self._overview is not None and self._overview.conversation_enabled:
            self.conversation_requested.emit()

    def activity_button(self) -> QPushButton:
        return self._activity_button

    def secondary_activity_button(self) -> QPushButton:
        return self._secondary_activity_button

    def _navigation_buttons(self) -> tuple[QPushButton, ...]:
        return (
            self._activity_button, self._secondary_activity_button,
            self._conversation_button,
        )

    def conversation_button(self) -> QPushButton:
        return self._conversation_button

    def eventFilter(self, watched, event) -> bool:
        if event.type() == QEvent.Type.FocusIn and watched in self._navigation_buttons():
            # Conversation can shorten the room on a compact display. A
            # keyboard action must scroll into view just like a pointer target.
            self.ensureWidgetVisible(watched, Space.SM, Space.SM)
        return super().eventFilter(watched, event)

    def _reveal_focused_action(self) -> None:
        if not self.isVisible():
            return
        focused = self.focusWidget()
        if focused in self._navigation_buttons():
            self.ensureWidgetVisible(focused, Space.SM, Space.SM)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._sync_density()

    def _sync_density(self) -> None:
        height = self.viewport().height()
        two_activities = bool(
            self._overview is not None and self._overview.secondary_activity_action
        )
        compact = (
            self.viewport().width() < 500 or height < 340
            or (two_activities and height < 480)
        )
        margin = Space.MD if compact else Space.XL
        short = height < 300 or (two_activities and height < 400)
        vertical_margin = (
            Space.XS if short and two_activities else Space.SM if short else Space.LG
        )
        self._body_layout.setContentsMargins(margin, vertical_margin, margin, vertical_margin)
        # Short rooms with two activities or Conversation retain every fact
        # and action while omitting the additional display heading.
        if self._overview is not None:
            self._title.setVisible(bool(self._overview.title) and not short)
        self._content_layout.setSpacing(Space.SM if compact else Space.LG)
        direction = (
            QBoxLayout.Direction.TopToBottom
            if self.viewport().width() < 420
            else QBoxLayout.Direction.LeftToRight
        )
        for layout in (
            self._activity_row, self._secondary_activity_layout, self._actions,
        ):
            layout.setDirection(direction)
        # Resizing for Conversation may move a button that already owns
        # focus, so it will not receive another FocusIn event. Wait until
        # the queued layout has settled before keeping that action visible.
        # Bind the deferred work to this widget so closing its window
        # cancels the callback before the underlying C++ object is deleted.
        QTimer.singleShot(0, self, self._reveal_focused_action)
