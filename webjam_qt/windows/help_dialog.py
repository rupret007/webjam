"""Searchable, modeless workflow help with explicit navigation."""

from __future__ import annotations

from PySide6.QtCore import QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
)

from core.workflow_help import HelpTopic, search_topics, workflow_topics
from webjam_qt.controllers.window_layout import centered_window_rect
from webjam_qt.theme.brand import render_brand_pixmap
from webjam_qt.theme.tokens import Layout, Space


class HelpDialog(QDialog):
    """Keep long or enlarged help inside the chosen display, not a native sheet."""

    navigation_requested = Signal(str)

    def __init__(self, body: str, available_geometry: QRect | None = None, *,
                 parent=None, profile: str = "music", offline_studio: bool = False) -> None:
        super().__init__(parent)
        self.setObjectName("HelpDialog")
        self.setWindowTitle("WebJam Help")
        self.setWindowModality(Qt.WindowModality.NonModal)
        self._available_geometry = QRect(available_geometry) if available_geometry else QRect()

        layout = QVBoxLayout(self)
        self._layout = layout
        layout.setContentsMargins(Space.XL, Space.XL, Space.XL, Space.LG)
        layout.setSpacing(Space.MD)
        content = QHBoxLayout()
        content.setSpacing(Space.LG)
        self._brand = QLabel()
        self._brand.setObjectName("HelpBrand")
        self._brand.setAccessibleName("WebJam")
        self._brand.setPixmap(render_brand_pixmap(64))
        content.addWidget(self._brand)
        self._heading = QLabel("Find a workflow")
        self._heading.setWordWrap(True)
        content.addWidget(self._heading, 1)
        layout.addLayout(content)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search help, for example: unsaved draft")
        self._search.setAccessibleName("Search workflow help")
        self._search.setMaxLength(256)
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._filter_topics)
        layout.addWidget(self._search)
        self._topics = QListWidget()
        self._topics.setAccessibleName("Help topics")
        self._topics.setWordWrap(True)
        self._topics.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._topics.setMinimumHeight(48)
        self._topics.setMaximumHeight(104)
        self._topics.currentRowChanged.connect(self._show_topic)
        layout.addWidget(self._topics)

        self._body = QTextBrowser()
        self._body.setObjectName("HelpBody")
        self._body.setAccessibleName("Workflow help")
        self._body.setAccessibleDescription(
            "Read-only help. Use the arrow or Page Down keys to scroll."
        )
        self._body.setOpenLinks(False)
        self._body.setOpenExternalLinks(False)
        self._body.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._body.setMaximumHeight(Layout.HELP_BODY_MAX_HEIGHT)
        self._body.setMinimumSize(0, 72)
        layout.addWidget(self._body, 1)

        self._navigate = QPushButton()
        self._navigate.setObjectName("GhostButton")
        self._navigate.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._navigate.setAutoDefault(False)
        self._navigate.clicked.connect(self._request_navigation)
        layout.addWidget(self._navigate)

        self._buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        self._buttons.setObjectName("HelpActions")
        self._buttons.accepted.connect(self.accept)
        self._ok = self._buttons.button(QDialogButtonBox.StandardButton.Ok)
        self._ok.setAccessibleName("Close help")
        # Cocoa otherwise skips this button when tabbing out of the text.
        self._ok.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._ok.setAutoDefault(False)
        self._ok.setDefault(False)
        layout.addWidget(self._buttons)
        for current, following in ((self._search, self._topics), (self._topics, self._body),
                                   (self._body, self._navigate), (self._navigate, self._ok),
                                   (self._ok, self._search)):
            self.setTabOrder(current, following)
        self._find_shortcut = QShortcut(QKeySequence.StandardKey.Find, self)
        self._find_shortcut.activated.connect(self._focus_search)
        self.set_context(body, profile=profile, offline_studio=offline_studio)
        self.resize(640, 600)

    def set_context(self, body: str, *, profile: str, offline_studio: bool) -> None:
        self._all_topics = (HelpTopic("overview", "Current workspace", body),
                            *workflow_topics(profile, offline_studio=offline_studio))
        self._filter_topics()

    def _filter_topics(self, query: str | None = None) -> None:
        selected = self._topics.currentItem()
        key = selected.data(Qt.ItemDataRole.UserRole).key if selected and query is None else ""
        self._matches = search_topics(self._all_topics, self._search.text())
        self._topics.blockSignals(True)
        self._topics.clear()
        for topic in self._matches:
            item = QListWidgetItem(topic.title)
            item.setData(Qt.ItemDataRole.UserRole, topic)
            self._topics.addItem(item)
        row = next((i for i, topic in enumerate(self._matches) if topic.key == key), 0)
        self._topics.setCurrentRow(row if self._matches else -1)
        self._topics.blockSignals(False)
        self._show_topic(self._topics.currentRow())
        self._update_density()

    def _show_topic(self, row: int) -> None:
        topic = self._matches[row] if 0 <= row < len(self._matches) else None
        self._body.setHtml(topic.body if topic else
            "<p>No matching help topics. Try <b>saved work</b>, <b>draft</b>, "
            "<b>export</b> or <b>reference</b>, or clear the search.</p>")
        self._route = topic.route if topic else ""
        self._navigate.setText(topic.action if topic else "Open workflow")
        self._navigate.setVisible(bool(self._route))
        self._navigate.setEnabled(bool(self._route))

    def _request_navigation(self) -> None:
        if self.isVisible() and self._route:
            self.navigation_requested.emit(self._route)

    def _focus_search(self) -> None:
        self._search.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self._search.selectAll()

    def keyPressEvent(self, event) -> None:
        # QDialogButtonBox may nominate OK as the native default even with
        # autoDefault disabled. Enter while searching/reading must do nothing.
        if event.key() in {Qt.Key.Key_Return, Qt.Key.Key_Enter}:
            if self._ok.hasFocus():
                self.accept()
            elif self._navigate.hasFocus():
                self._request_navigation()
            event.accept()
            return
        super().keyPressEvent(event)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._fit_to_screen()
        # Native frame margins settle when the window is first shown. The
        # text browser keeps wrapping/scrolling independent of dialog height.
        QTimer.singleShot(0, self._fit_to_screen)
        self._focus_search()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_density()

    def _update_density(self) -> None:
        compact = self.width() < 520 or self.height() < 520
        self._brand.setVisible(not compact)
        self._heading.setVisible(not compact)
        margin = Space.LG if compact else Space.XL
        self._layout.setContentsMargins(margin, margin, margin, Space.LG)
        self._layout.setSpacing(Space.SM if compact else Space.MD)
        rows_height = sum(max(1, self._topics.sizeHintForRow(row))
                          for row in range(min(2, self._topics.count()))) + 4
        self._topics.setFixedHeight(min(78 if compact else 104, max(48, rows_height)))

    def _fit_to_screen(self) -> None:
        available = self._available_geometry
        if available.isEmpty():
            return
        frame, inner = self.frameGeometry(), self.geometry()
        extra_width = frame.width() - inner.width()
        extra_height = frame.height() - inner.height()
        target = centered_window_rect(
            available.adjusted(12, 12, -12, -12),
            QSize(640 + extra_width, 600 + extra_height),
        )
        if target.isEmpty():
            return
        self.setGeometry(
            target.x() + inner.x() - frame.x(),
            target.y() + inner.y() - frame.y(),
            max(1, target.width() - extra_width),
            max(1, target.height() - extra_height),
        )
