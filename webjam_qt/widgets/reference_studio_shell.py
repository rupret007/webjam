"""One Studio destination for projects and completed-session take review."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from core.creative_modes import (
    CreatorProfile,
    get_creator_profile_by_key,
    get_creator_profile_by_key_or_default,
)
from webjam_qt.theme.tokens import Space
from webjam_qt.widgets.recording_studio import RecordingStudio
from webjam_qt.widgets.reference_studio_workspace import ReferenceStudioWorkspace
from webjam_qt.widgets.studio_project_home import StudioProjectHome


class ReferenceStudioShell(QWidget):
    """Switch between project home, song workspace, and legacy take review."""

    new_project_requested = Signal()
    open_project_requested = Signal()
    play_along_requested = Signal()
    recent_project_requested = Signal(str)
    take_review_requested = Signal()

    def __init__(
        self,
        take_review: RecordingStudio,
        parent: QWidget | None = None,
    ) -> None:
        if not isinstance(take_review, RecordingStudio):
            raise TypeError("take_review must be a RecordingStudio.")
        super().__init__(parent)
        self._creator_profile = get_creator_profile_by_key_or_default("music")
        self.setObjectName("ReferenceStudioShell")
        self.setAccessibleName("Reference Studio")
        self._take_review = take_review

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.stack = QStackedWidget()
        self.stack.setObjectName("ReferenceStudioStack")
        self.stack.setAccessibleName("Reference Studio view")

        home_container = QWidget()
        home_layout = QVBoxLayout(home_container)
        home_layout.setContentsMargins(0, 0, 0, Space.MD)
        home_layout.setSpacing(Space.XS)
        self.home = StudioProjectHome()
        home_layout.addWidget(self.home, 1)
        review_row = QHBoxLayout()
        review_row.addStretch(1)
        self.review_takes_button = QPushButton("Review Session Takes")
        self.review_takes_button.setObjectName("ReferenceStudioReviewTakes")
        self.review_takes_button.setAccessibleName("Review completed session takes")
        self.review_takes_button.setAccessibleDescription(
            "Open the existing synchronized multitrack take review workspace."
        )
        review_row.addWidget(self.review_takes_button)
        review_row.addStretch(1)
        home_layout.addLayout(review_row)

        self.workspace = ReferenceStudioWorkspace()
        self.stack.addWidget(home_container)
        self.stack.addWidget(self.workspace)
        self.stack.addWidget(self._take_review)
        root.addWidget(self.stack)

        self.home.new_project_requested.connect(self.new_project_requested.emit)
        self.home.open_project_requested.connect(self.open_project_requested.emit)
        self.home.play_along_requested.connect(self.play_along_requested.emit)
        self.home.recent_project_requested.connect(self.recent_project_requested.emit)
        self.review_takes_button.clicked.connect(self._request_take_review)
        self.set_creator_profile(self._creator_profile)
        self.show_home()

    @property
    def take_review(self) -> RecordingStudio:
        return self._take_review

    @property
    def creator_profile_key(self) -> str:
        return self._creator_profile.key

    def set_creator_profile(self, value: CreatorProfile | str) -> None:
        """Apply one canonical profile across every standalone Studio surface."""

        key = value.key if isinstance(value, CreatorProfile) else value
        profile = get_creator_profile_by_key(key)
        if profile is None:
            raise ValueError("creator profile is unsupported.")
        self._creator_profile = profile
        self.home.set_creator_profile(profile)
        self.workspace.set_creator_profile(profile)
        if profile.key == "music":
            self.setAccessibleDescription(
                "Create or open a music project, or review completed session takes."
            )
            self.review_takes_button.setText("Review Session Takes")
            self.review_takes_button.setAccessibleName("Review completed session takes")
        elif profile.key == "podcast_voice":
            self.setAccessibleDescription(
                "Create or open an episode project, or review completed session recordings."
            )
            self.review_takes_button.setText("Review Session Recordings")
            self.review_takes_button.setAccessibleName(
                "Review completed session recordings"
            )
        else:
            self.setAccessibleDescription(
                "Review and Rehearsal Preview supports completed take review but not local multitrack projects."
            )
            self.review_takes_button.setText("Review Existing Session Takes")
            self.review_takes_button.setAccessibleName(
                "Review existing completed session takes"
            )

    def _request_take_review(self) -> None:
        self.show_take_review()
        self.take_review_requested.emit()

    def show_home(self) -> None:
        self.stack.setCurrentIndex(0)
        if self.home.play_along_button.isEnabled():
            self.home.play_along_button.setFocus()
        else:
            self.review_takes_button.setFocus()

    def show_project(self) -> None:
        self.stack.setCurrentWidget(self.workspace)
        self.workspace.setFocus()

    def show_take_review(self) -> None:
        self.stack.setCurrentWidget(self._take_review)
        self._take_review.reload()
        self._take_review.setFocus()

    def current_view(self) -> str:
        current = self.stack.currentWidget()
        if current is self.workspace:
            return "project"
        if current is self._take_review:
            return "takes"
        return "home"


__all__ = ["ReferenceStudioShell"]
