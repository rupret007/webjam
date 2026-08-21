"""Project-first home surface for WebJam Reference Studio.

This widget contains presentation and semantic intent only.  It never opens a
file, mutates settings, starts an audio device, or persists a recent-project
path.  A controller owns those operations and returns bounded presentation
records through :meth:`set_recent_projects`.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.creative_modes import (
    CreatorProfile,
    get_creator_profile_by_key,
    get_creator_profile_by_key_or_default,
)
from webjam_qt.theme.brand import BrandMark
from webjam_qt.theme.tokens import Space


@dataclass(frozen=True, slots=True)
class RecentStudioProject:
    """One already-sanitized recent-project presentation."""

    path: str
    title: str
    detail: str = ""

    def __post_init__(self) -> None:
        path = str(self.path or "").strip()
        title = " ".join(str(self.title or "").split())
        detail = " ".join(str(self.detail or "").split())
        if not path or "\x00" in path or len(path.encode("utf-8")) > 4_096:
            raise ValueError("Recent project path is invalid.")
        if not title or len(title.encode("utf-8")) > 512:
            raise ValueError("Recent project title is invalid.")
        if len(detail.encode("utf-8")) > 512:
            raise ValueError("Recent project detail is invalid.")
        object.__setattr__(self, "path", path)
        object.__setattr__(self, "title", title)
        object.__setattr__(self, "detail", detail)


class StudioProjectHome(QWidget):
    """Accessible New/Open/Play-Along entry without session complexity."""

    new_project_requested = Signal()
    open_project_requested = Signal()
    play_along_requested = Signal()
    recent_project_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._creator_profile = get_creator_profile_by_key_or_default("music")
        self.setObjectName("StudioProjectHome")
        self.setAccessibleName("Reference Studio home")
        self.setAccessibleDescription(
            "Create or open a songwriting project, or start Play Along and Record."
        )
        self.setMinimumSize(520, 430)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(Space.XL, Space.LG, Space.XL, Space.XL)
        outer.setSpacing(Space.MD)

        brand_row = QHBoxLayout()
        brand_row.setSpacing(Space.SM)
        brand_row.addStretch(1)
        brand_row.addWidget(BrandMark(28))
        wordmark = QLabel('Web<span style="color: #BF5700;">Jam</span>')
        wordmark.setObjectName("StudioHomeWordmark")
        wordmark.setTextFormat(Qt.TextFormat.RichText)
        wordmark.setAccessibleName("WebJam")
        brand_row.addWidget(wordmark)
        brand_row.addStretch(1)
        outer.addLayout(brand_row)

        self.title = QLabel("Reference Studio")
        self.title.setObjectName("StudioHomeTitle")
        self.title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.title.setAccessibleName("Reference Studio")
        outer.addWidget(self.title)

        self.subtitle = QLabel(
            "Play with a backing track, capture ideas, arrange takes, and bounce a demo."
        )
        self.subtitle.setObjectName("StudioHomeSubtitle")
        self.subtitle.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.subtitle.setWordWrap(True)
        outer.addWidget(self.subtitle)

        actions = QFrame()
        actions.setObjectName("StudioHomeActions")
        action_layout = QVBoxLayout(actions)
        action_layout.setContentsMargins(Space.LG, Space.LG, Space.LG, Space.LG)
        action_layout.setSpacing(Space.SM)

        self.play_along_button = QPushButton("Play Along / Record")
        self.play_along_button.setObjectName("LaunchPrimary")
        self.play_along_button.setMinimumHeight(52)
        self.play_along_button.setDefault(True)
        self.play_along_button.setAccessibleName("Play Along or Record")
        self.play_along_button.setAccessibleDescription(
            "Create a song project and choose a local backing track you own or may use."
        )
        self.play_along_button.clicked.connect(self.play_along_requested.emit)
        action_layout.addWidget(self.play_along_button)

        row = QHBoxLayout()
        row.setSpacing(Space.SM)
        self.new_button = QPushButton("New Project")
        self.new_button.setObjectName("StudioHomeNew")
        self.new_button.setAccessibleName("Create a new Reference Studio project")
        self.new_button.clicked.connect(self.new_project_requested.emit)
        self.open_button = QPushButton("Open Project…")
        self.open_button.setObjectName("StudioHomeOpen")
        self.open_button.setAccessibleName("Open a Reference Studio project")
        self.open_button.clicked.connect(self.open_project_requested.emit)
        row.addWidget(self.new_button)
        row.addWidget(self.open_button)
        action_layout.addLayout(row)
        outer.addWidget(actions)

        self.recent_label = QLabel("Recent Projects")
        self.recent_label.setObjectName("StudioHomeRecentTitle")
        outer.addWidget(self.recent_label)
        self.recent_list = QListWidget()
        self.recent_list.setObjectName("StudioHomeRecentList")
        self.recent_list.setAccessibleName("Recent Reference Studio projects")
        self.recent_list.setAccessibleDescription(
            "Select a recent project and press Enter, or double-click it, to open it."
        )
        self.recent_list.setMinimumHeight(120)
        self.recent_list.itemActivated.connect(self._open_recent_item)
        outer.addWidget(self.recent_list, 1)

        self.empty_recent = QLabel(
            "No recent projects yet. Play Along / Record is the quickest way to begin."
        )
        self.empty_recent.setObjectName("StudioHomeEmptyRecent")
        self.empty_recent.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.empty_recent.setWordWrap(True)
        outer.addWidget(self.empty_recent, 1)

        QWidget.setTabOrder(self.play_along_button, self.new_button)
        QWidget.setTabOrder(self.new_button, self.open_button)
        QWidget.setTabOrder(self.open_button, self.recent_list)
        self.set_recent_projects(())
        self.set_creator_profile(self._creator_profile)

    @property
    def creator_profile_key(self) -> str:
        return self._creator_profile.key

    def set_creator_profile(self, value: CreatorProfile | str) -> None:
        """Render only vocabulary and capabilities from the fixed registry."""

        key = value.key if isinstance(value, CreatorProfile) else value
        profile = get_creator_profile_by_key(key)
        if profile is None:
            raise ValueError("creator profile is unsupported.")
        self._creator_profile = profile
        available = profile.capabilities.local_multitrack
        self.play_along_button.setEnabled(available)
        self.new_button.setEnabled(available)
        self.open_button.setEnabled(available)
        self.recent_list.setEnabled(available)

        if profile.key == "music":
            self.setAccessibleDescription(
                "Create or open a songwriting project, or start Play Along and Record."
            )
            self.title.setText("Reference Studio")
            self.title.setAccessibleName("Reference Studio")
            self.subtitle.setText(
                "Play with a backing track, capture ideas, arrange takes, and bounce a demo."
            )
            self.play_along_button.setText("Play Along / Record")
            self.play_along_button.setAccessibleName("Play Along or Record")
            self.play_along_button.setAccessibleDescription(
                "Create a song project and choose a local backing track you own or may use."
            )
            self.new_button.setText("New Project")
            self.new_button.setAccessibleName("Create a new Reference Studio project")
            self.open_button.setText("Open Project…")
            self.open_button.setAccessibleName("Open a Reference Studio project")
            self.recent_label.setText("Recent Projects")
            self.recent_list.setAccessibleName("Recent Reference Studio projects")
            self.empty_recent.setText(
                "No recent projects yet. Play Along / Record is the quickest way to begin."
            )
            return

        if profile.key == "podcast_voice":
            self.setAccessibleDescription(
                "Create or open a podcast or voice project, or start a new recording."
            )
            self.title.setText("Podcast & Voice Studio")
            self.title.setAccessibleName("Podcast and Voice Studio")
            self.subtitle.setText(
                "Record isolated voices, edit an episode, add chapters, and bounce a review copy."
            )
            self.play_along_button.setText("New Recording")
            self.play_along_button.setAccessibleName(
                "Create a podcast or voice recording"
            )
            self.play_along_button.setAccessibleDescription(
                "Create an episode project and optionally choose local reference audio you own or may use."
            )
            self.new_button.setText("New Episode Project")
            self.new_button.setAccessibleName("Create a new episode project")
            self.open_button.setText("Open Project…")
            self.open_button.setAccessibleName("Open a podcast or voice project")
            self.recent_label.setText("Recent Episodes")
            self.recent_list.setAccessibleName("Recent podcast and voice projects")
            self.empty_recent.setText(
                "No recent episodes yet. New Recording is the quickest way to begin."
            )
            return

        self.setAccessibleDescription(
            "Review and Rehearsal Preview does not yet provide local multitrack projects."
        )
        self.title.setText("Review & Rehearsal Preview")
        self.title.setAccessibleName("Review and Rehearsal Preview")
        self.subtitle.setText(
            "Use the live review workflow and completed session takes. Local multitrack projects are not available in this Preview."
        )
        self.play_along_button.setText("Local Studio Unavailable")
        self.play_along_button.setAccessibleName(
            "Local Studio unavailable in Review and Rehearsal Preview"
        )
        self.play_along_button.setAccessibleDescription(
            "This Preview does not support creating a local multitrack project."
        )
        self.new_button.setText("New Project Unavailable")
        self.new_button.setAccessibleName("New local project unavailable")
        self.open_button.setText("Open Project Unavailable")
        self.open_button.setAccessibleName("Open local project unavailable")
        self.recent_label.setText("Local Projects Unavailable")
        self.recent_list.setAccessibleName("Local projects unavailable in Preview")
        self.empty_recent.setText(
            "Review completed session takes below, or return to the live review workspace."
        )

    def set_recent_projects(
        self,
        projects: Iterable[RecentStudioProject],
    ) -> None:
        """Replace recent presentations without opening or inspecting paths."""

        if isinstance(projects, (str, bytes, Path)):
            raise TypeError("projects must be an iterable of RecentStudioProject.")
        records = tuple(projects)
        if len(records) > 20:
            raise ValueError("Reference Studio displays at most 20 recent projects.")
        if any(not isinstance(item, RecentStudioProject) for item in records):
            raise TypeError("projects must contain RecentStudioProject values.")

        self.recent_list.clear()
        for record in records:
            text = record.title
            if record.detail:
                text = f"{text}\n{record.detail}"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, record.path)
            item.setToolTip(record.title)
            item.setData(Qt.ItemDataRole.AccessibleTextRole, record.title)
            self.recent_list.addItem(item)
        has_recent = bool(records)
        self.recent_label.setVisible(has_recent)
        self.recent_list.setVisible(has_recent)
        self.empty_recent.setVisible(not has_recent)

    def _open_recent_item(self, item: QListWidgetItem) -> None:
        path = str(item.data(Qt.ItemDataRole.UserRole) or "").strip()
        if path:
            self.recent_project_requested.emit(path)


__all__ = ["RecentStudioProject", "StudioProjectHome"]
