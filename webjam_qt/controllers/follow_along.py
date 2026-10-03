"""Own explicit lesson handoffs; external players and meetings stay external."""

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QInputDialog

from core.youtube_lesson import parse_youtube_lesson_url


class FollowAlongController:
    def __init__(self, controller):
        self._c = controller
        self._context = None
        self._suspended = None
        self._choice = None
        self._sources = {}
        self._art_browser_choice = None
        self.panel = controller.window.webex_embed.lesson_handoff
        self.panel.choose_requested.connect(self.choose_lesson)
        self.panel.open_requested.connect(self.open_lesson)
        self.panel.save_requested.connect(self.save_lesson)
        self.panel.restart_requested.connect(self.restart_requests)

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
            self._c._reference_video_identity() if self._c.creator_profile.key == "art" else None,
            getattr(self._c, "_session_meeting_generation", 0),
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

    def music_practice_visible(self):
        """Read the current presentation owner without authorizing an action."""
        return bool(
            self._c.creator_profile.key == "music"
            and self._context is not None
            and self._context == (self._identity(), self.panel.generation)
            and self.panel.hosting is not None
            and self.panel.isVisibleTo(self._c.window)
        )

    def _refresh_music_setup_guidance(self):
        if (self._c.creator_profile.key == "music"
                and getattr(self._c, "_startup_attempt", None) is not None
                and not any(getattr(self._c, flag, False) for flag in (
                    "_shutdown", "_shutdown_in_progress", "_shutdown_cleanup_pending",
                ))):
            self._c._update_session_hud()

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
        self._suspended = None
        if hosting and url:
            self._sources[key] = url
        self._c.window.webex_embed._sync_art_layout()
        self._refresh_music_setup_guidance()

    def retire(self):
        had_context = self._context is not None
        self._context = None
        self._suspended = None
        if self._choice is not None:
            choice = self._choice
            self._choice = None
            choice.close()
            choice.deleteLater()
        if had_context:
            self._refresh_music_setup_guidance()

    def suspend(self):
        """Retain a local navigation choice without retaining action authority."""
        current = self._identity()
        saved = self._suspended
        if self._context == (current, self.panel.generation):
            saved = (current, self.panel.hosting, self.panel.lesson_url)
        elif saved is not None and saved[0] != current:
            saved = None
        self.retire()
        self._suspended = saved

    def resume(self):
        saved = self._suspended
        self._suspended = None
        if saved is None or saved[0] != self._identity():
            return False
        _identity, hosting, url = saved
        self._c.window.webex_embed.set_shared_lesson_context(hosting=hosting)
        self.activate(hosting=hosting, lesson_url=url)
        if self._c.creator_profile.key == "art":
            self._c._room_participant.activate_lesson_requests(hosting=hosting)
        return True

    def workspace_created(self):
        """The Library materialized this same unsaved workspace, not a switch."""
        current = self._identity()
        previous = current[:1] + ("",) + current[2:]
        active = self._context == (previous, self.panel.generation)
        suspended = self._suspended is not None and self._suspended[0] == previous
        if active or suspended:
            url = self._sources.pop((current[0], ""), None)
            if url:
                self._sources[(current[0], current[1])] = url
        if (self._art_browser_choice is not None
                and self._art_browser_choice[0] == self._browser_owner(previous)):
            self._art_browser_choice = (self._browser_owner(current), self._art_browser_choice[1])
        if active:
            self.panel.generation += 1
            self.activate(hosting=self.panel.hosting, lesson_url=self.panel.lesson_url)
        elif suspended:
            self._suspended = (current, *self._suspended[1:])

    def meeting_changed(self):
        """Keep a chosen lesson after Add Link, rejecting all earlier clicks."""
        if self._context is None:
            if self._suspended is not None:
                previous, hosting, url = self._suspended
                current = self._identity()
                self._suspended = ((current, hosting, url)
                    if self._browser_owner(previous) == self._browser_owner(current) else None)
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

    def restart_requests(self, generation):
        if (not self._allowed(generation) or self._c.creator_profile.key != "art"
                or not self.panel.restart_button.isVisibleTo(self._c.window)):
            return
        self.panel.generation += 1
        self.activate(hosting=self.panel.hosting, lesson_url=self.panel.lesson_url)
        self._c._room_participant.activate_lesson_requests(hosting=self.panel.hosting)

    def save_lesson(self, generation):
        if not self._allowed(generation) or not self.panel.lesson_url:
            return
        if self._c.creator_profile.key == "art":
            self._c.session_library.remember_art_lesson(self.panel.lesson_url)
        elif self._c.creator_profile.key == "music":
            self._c.session_library.remember_music_lesson(self.panel.lesson_url)

    def use_saved_lesson(self, url):
        """Populate a fresh explicit handoff; never open media or a meeting."""
        url = parse_youtube_lesson_url(url).playback_url
        if (self._c._shutdown_cleanup_blocks_action()
                or QApplication.activeModalWidget() or QApplication.activePopupWidget()):
            return False
        profile = self._c.creator_profile.key
        if profile == "art":
            self._c._open_reference_video()
            dialog = self._c._reference_video_dialog
            coordinator = self._c._reference_video
            if dialog is None or coordinator is None:
                return False
            self._c._watch_shared_lesson(coordinator, dialog)
            if not self._allowed(self.panel.generation):
                return False
            hosting = coordinator.hosting
        elif profile == "music":
            hosting = self._c._reference_track_is_host()
            self._c._show_webex_conversation(resume_lesson=False)
            self._c.window.webex_embed.set_shared_lesson_context(hosting=hosting)
        else:
            return False
        self._c._retire_shared_lesson_requests()
        self.panel.generation += 1
        self.activate(hosting=hosting, lesson_url=url)
        if profile == "art":
            if hosting:
                self._art_browser_choice = (self._browser_owner(self._identity()), url)
            self._c._room_participant.activate_lesson_requests(hosting=hosting)
        self.panel.set_status("Saved lesson selected. Nothing has opened." if hosting else
                              "Saved reference selected. Watch the host's shared lesson in the meeting.")
        return True

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
                self._c._show_webex_conversation(resume_lesson=False)
                self._c.window.webex_embed.set_shared_lesson_context(hosting=hosting)
                self.activate(hosting=hosting)

        choice.choice_requested.connect(selected)
        choice.show()
        choice.activateWindow()
        choice.buttons["lesson"].setFocus()
