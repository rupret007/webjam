"""Own explicit lesson handoffs; external players and meetings stay external."""

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QInputDialog

from core.youtube_lesson import parse_youtube_lesson_url


class FollowAlongController:
    def __init__(self, controller):
        self._c = controller
        self._context = None
        self._choice = None
        self._sources = {}
        self._art_browser_choice = None
        self.panel = controller.window.webex_embed.lesson_handoff
        self.panel.choose_requested.connect(self.choose_lesson)
        self.panel.open_requested.connect(self.open_lesson)
        self.panel.save_requested.connect(self.save_lesson)

    def _workspace(self):
        library = getattr(self._c, "session_library", None)
        return getattr(getattr(library, "current", None), "id", "")

    def _identity(self):
        room = getattr(self._c, "_room_participant", None)
        video = getattr(self._c, "_reference_video", None)
        snapshot = (video.host_snapshot if video.hosting else video.follow_snapshot) if video else None
        source = (video, getattr(snapshot, "identity_digest", ""),
                  getattr(snapshot, "source_kind", ""), getattr(snapshot, "video_id", ""),
                  getattr(getattr(self._c, "_reference_video_dialog", None), "_meeting_lesson_generation", 0))
        return (
            self._c.creator_profile.key, self._workspace(), room, getattr(room, "generation", None),
            self._c._reference_video_identity(), getattr(self._c, "_session_meeting_generation", 0),
            self._c._effective_meeting_url(), source,
            self._c._reference_track_is_host() if self._c.creator_profile.key == "music" else None,
        )

    def _allowed(self, generation):
        context = self._context
        return bool(
            context is not None and context == (self._identity(), self.panel.generation)
            and generation == self.panel.generation and self.panel.hosting is not None
            and self.panel.isVisibleTo(self._c.window) and not self._c._shutdown_cleanup_blocks_action()
            and not QApplication.activeModalWidget() and not QApplication.activePopupWidget()
        )

    @staticmethod
    def _browser_owner(identity):
        return identity[:5] + identity[7:]

    def activate(self, *, hosting, lesson_url=None, resume_browser_choice=False):
        """Bind controls after the existing explicit Conversation navigation."""
        key = (self._c.creator_profile.key, self._workspace())
        # An explicit empty Art source means withdrawn/local, not "reuse the
        # last YouTube link". Only Music's separate local chooser resumes its
        # own remembered choice; guests never borrow a previous host choice.
        url = (self._sources.get(key, "") if hosting and key[0] == "music" else "") if lesson_url is None else lesson_url
        if (resume_browser_choice and hosting and key[0] == "art" and self._art_browser_choice
                and self._art_browser_choice[0] == self._browser_owner(self._identity())):
            url = self._art_browser_choice[1]
        if url:
            url = parse_youtube_lesson_url(url).playback_url
        self.panel.set_context(hosting=hosting, profile=key[0], lesson_url=url)
        self._context = (self._identity(), self.panel.generation)
        if hosting and url:
            self._sources[key] = url
        self._c.window.webex_embed._sync_art_layout()

    def retire(self):
        self._context = None
        if self._choice is not None:
            choice = self._choice
            self._choice = None
            choice.close()
            choice.deleteLater()

    def meeting_changed(self):
        """Keep a chosen lesson after Add Link, rejecting all earlier clicks."""
        if self._context is None:
            return
        previous, generation = self._context
        current = self._identity()
        # Only meeting fields may change. New room, profile, source or work
        # belongs to a new explicit handoff, even when its URL is identical.
        if previous[:5] + previous[7:] != current[:5] + current[7:]:
            self._c._clear_shared_lesson_context()
            return
        if generation != self.panel.generation:
            return
        self.panel.generation += 1
        self._context = (current, self.panel.generation)

    def choose_lesson(self, generation):
        if not self._allowed(generation) or self.panel.hosting is not True:
            return
        url, accepted = QInputDialog.getText(
            self._c.window, "Choose YouTube lesson", "Paste the video link you want to share in your meeting.",
            text=self.panel.lesson_url,
        )
        if not accepted or not self._allowed(generation):
            return
        try:
            lesson = parse_youtube_lesson_url(url)
        except ValueError as error:
            self.panel.set_status(str(error))
            return
        self._c._retire_shared_lesson_requests()
        self.panel.generation += 1
        self.activate(hosting=True, lesson_url=lesson.playback_url)
        if self._c.creator_profile.key == "art":
            self._art_browser_choice = (self._browser_owner(self._identity()), lesson.playback_url)
        room = getattr(self._c, "_room_participant", None)
        if self._c.creator_profile.key == "art" and room is not None:
            room.activate_lesson_requests(hosting=True)
        self.panel.set_status("Lesson selected. Open it when you are ready to share; nothing has started.")

    def open_lesson(self, generation):
        if not self._allowed(generation) or self.panel.hosting is not True or not self.panel.lesson_url:
            return
        url = parse_youtube_lesson_url(self.panel.lesson_url).playback_url
        try:
            opened = QDesktopServices.openUrl(QUrl(url))
        except Exception:
            opened = False
        if self._allowed(generation):
            self.panel.set_status(
                "Lesson handed to your browser. Use the meeting's Share control and confirm sound with your guest."
                if opened else "The browser could not open the lesson. Try Open lesson in browser again."
            )

    def save_lesson(self, generation):
        if not self._allowed(generation) or self._c.creator_profile.key != "art" or not self.panel.lesson_url:
            return
        before = self._identity()
        url, hosting = self.panel.lesson_url, self.panel.hosting
        self._c.session_library.remember_art_lesson(url)
        after = self._identity()
        if (self._context is not None and not before[1] and after[1]
                and before[:1] + before[2:] == after[:1] + after[2:]):
            if self._art_browser_choice == (self._browser_owner(before), url):
                self._art_browser_choice = (self._browser_owner(after), url)
            self.panel.generation += 1
            self.activate(hosting=hosting, lesson_url=url)

    def show_music_choices(self):
        if self._c.creator_profile.key != "music" or self._c._shutdown_cleanup_blocks_action():
            return
        if self._choice is not None:
            if self._choice.binding == self._identity():
                self._choice.show()
                self._choice.raise_()
                return
            self.retire()
        from webjam_qt.windows.play_along import PlayAlongDialog

        hosting = self._c._reference_track_is_host()
        choice = self._choice = PlayAlongDialog(self._c.window, hosting=hosting)
        identity = self._identity()
        choice.binding = identity

        def finished(_result):
            if self._choice is choice:
                self._choice = None
                choice.deleteLater()

        choice.finished.connect(finished)

        def selected(key):
            if (self._choice is not choice or not choice.isVisible() or identity != self._identity()
                    or self._c._shutdown_cleanup_blocks_action()
                    or QApplication.activeModalWidget() or QApplication.activePopupWidget()):
                return
            choice.close()
            if key == "ensemble":
                self._c._clear_shared_lesson_context()
                if hosting:
                    self._c._open_reference_track()
                else:
                    self._c._bring_jamulus_forward()
            elif key == "lesson":
                self._c._show_webex_conversation()
                self._c.window.webex_embed.set_shared_lesson_context(hosting=hosting)
                self.activate(hosting=hosting)

        choice.choice_requested.connect(selected)
        choice.show()
        choice.activateWindow()
        choice.buttons["lesson"].setFocus()
