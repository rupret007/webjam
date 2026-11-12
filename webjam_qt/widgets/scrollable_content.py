"""Keep wrapped guidance readable and reveal keyboard-focused actions."""

from PySide6.QtCore import QEvent, QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import QApplication, QFrame, QScrollArea


class ScrollableContent(QScrollArea):
    viewport_resized = Signal()
    content_height_changed = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._fitting = False
        self._content_height = 0
        self._focus_reveal = QTimer(self)
        self._focus_reveal.setSingleShot(True)
        self._focus_reveal.timeout.connect(self._reveal_current_focus)
        self.viewport().installEventFilter(self)
        QApplication.instance().focusChanged.connect(self._reveal_focus)

    def setWidget(self, widget):
        super().setWidget(widget)
        widget.installEventFilter(self)
        self.fit_content()

    def minimumSizeHint(self):
        return QSize(0, 0)

    def eventFilter(self, watched, event):
        if watched is self.viewport() and event.type() == QEvent.Type.Resize:
            self.viewport_resized.emit()
            self.fit_content()
            self._focus_reveal.start(0)
        elif watched is self.widget() and event.type() == QEvent.Type.LayoutRequest:
            self.fit_content()
            self._focus_reveal.start(0)
        return super().eventFilter(watched, event)

    def fit_content(self):
        content = self.widget()
        if self._fitting or content is None or content.layout() is None:
            return
        self._fitting = True
        try:
            layout = content.layout()
            height = max(layout.minimumSize().height(),
                         layout.totalHeightForWidth(max(1, self.viewport().width())))
            if content.minimumHeight() != height:
                content.setMinimumHeight(height)
            if height != self._content_height:
                self._content_height = height
                self.content_height_changed.emit(height)
        finally:
            self._fitting = False

    def _reveal_focus(self, _old, current):
        if self._reveal(current):
            # A queued reflow can move the action after the initial focus event.
            self._focus_reveal.start(0)

    def _reveal_current_focus(self):
        self._reveal(QApplication.focusWidget())

    def _reveal(self, current):
        content = self.widget()
        if (current is None or content is None or not self.isVisible()
                or not content.isAncestorOf(current)):
            return False
        self.ensureWidgetVisible(current, 8, 8)
        return True
