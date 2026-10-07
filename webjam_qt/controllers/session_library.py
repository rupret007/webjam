"""Connect saved creative work to the existing session, notes, and take owners."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QObject, QTimer

from core.art_workspace import art_summary
from core.session_intelligence import build_session_pulse
from core.session_library import SessionLibrary, SessionLibraryConflict


_EDITABLE_FIELDS = ("title", "mode_key", "notes", "decisions", "actions", "blockers",
                    "recaps", "take_links", "rehearsal", "art")


def _same_snapshot(left, right) -> bool:
    return bool(left is not None and right is not None and left == right
                and left._store_token == right._store_token
                and left.recovered == right.recovered)


def _merge_editor_changes(base, edited, latest):
    """Merge only disjoint known edits; competing content stays with its owners."""
    if (base is None or any(getattr(base, key) != getattr(edited, key)
                           or getattr(base, key) != getattr(latest, key)
                           for key in ("id", "profile", "created_at", "source_key", "import_provenance", "media_provenance"))):
        raise SessionLibraryConflict("The workspace editor no longer matches its original snapshot.")
    changes = {}
    for name in _EDITABLE_FIELDS:
        before, draft, current = getattr(base, name), getattr(edited, name), getattr(latest, name)
        if draft == before:
            changes[name] = current
        elif current == before or current == draft:
            changes[name] = draft
        else:
            raise SessionLibraryConflict(
                f"Workspace {name.replace('_', ' ')} changed while this editor was open. "
                "Your draft and the newer workspace changes are both retained; save a separate copy."
            )
    return replace(latest, **deepcopy(changes))


def default_session_library() -> SessionLibrary:
    from webjam_qt.controllers.session_persistence import _persistence_home

    return SessionLibrary(_persistence_home() / ".webjam_sessions")


def import_legacy_workspaces(library: SessionLibrary, home: Path | None = None) -> tuple[str, ...]:
    """Copy each readable legacy scratchpad once; originals/recovery stay owned."""
    from webjam_qt.controllers.session_persistence import (
        _PROFILE_NOTES_FILES, _SESSION_FILE, _load_profile_records, _read_bounded_notes,
        _persistence_home,
    )
    home = home or _persistence_home()
    warnings = []
    try:
        metadata = _load_profile_records(home / _SESSION_FILE)
    except (OSError, ValueError):
        metadata = {}
        warnings.append("Some existing session titles could not be imported.")
    for profile, filename in _PROFILE_NOTES_FILES.items():
        try:
            notes = _read_bounded_notes(home / filename)
            if notes is None:
                continue
            info = metadata.get(profile, {})
            library.import_snapshot(profile, info.get("title", f"Previous {profile.replace('_', ' ')} work"),
                                    notes, mode_key=info.get("mode", ""))
        except (OSError, ValueError):
            warnings.append(f"Previous {profile.replace('_', ' ')} notes still need review; the original was kept.")
    return tuple(warnings)


class SessionLibraryCoordinator(QObject):
    def __init__(self, controller, *, library=None):
        super().__init__(controller)
        self._c = controller
        self.library = library or default_session_library()
        self.current = None
        self.dialog = None
        self._pending = {}
        self._applying = False
        self._run_id = ""
        self._live_take_ids = set()
        self._recording_owners = {}
        self._pending_recap = None
        self._recap_owner = None
        self._imported = False
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(800)
        self.timer.timeout.connect(lambda: self.flush(include_editor=False))
        studio = getattr(controller.window, "recording_studio", None)
        if studio is not None:
            studio.workspace_owner_token = self._studio_owner_token

    def _studio_owner_token(self):
        # Saved comparisons can belong to another browsed workspace; bind
        # the active runtime owner at dispatch rather than the slot's origin.
        return (id(self), str(self.library.root), self.current.id if self.current else None,
                self._profile(), self._run_id)

    def _flash(self, text):
        self._c.window.flash_message(text, ms=7000)

    def _profile(self):
        return self._c.creator_profile.key

    def _context(self):
        strip = self._c.window.session_strip
        return strip.current_title(), strip.current_mode_key(), self._c.window.session_canvas.current_notes()

    def ensure_current(self) -> bool:
        if self.current is not None and self.current.profile == self._profile():
            return True
        try:
            if not self._imported:
                warnings = import_legacy_workspaces(self.library)
                self._imported = True
                if warnings:
                    self._flash(" ".join(warnings))
            title, mode, notes = self._context()
            first_workspace = self.current is None
            self.current = self.library.create(self._profile(), title or "Untitled workspace",
                                               notes=notes, mode_key=mode)
            follow_along = getattr(self._c, "follow_along", None)
            if first_workspace and follow_along is not None:
                follow_along.workspace_created()
            return True
        except (OSError, ValueError):
            self._flash("Workspace history could not be saved. Your current Notes remain available; retry Session library.")
            return False

    def changed(self, *_args):
        if not self._applying and self.current is not None:
            self.timer.start()

    def _capture_current_notes(self):
        """Keep live Notes ahead of editor saves without dropping pending facts."""
        if self._applying:
            return
        if self.current is not None and self.current.profile == self._profile():
            latest = self._pending.get(self.current.id, self.current)
            title, mode, notes = self._context()
            changes = {}
            if title != self.current.title:
                changes["title"] = title or latest.title
            if mode != self.current.mode_key:
                changes["mode_key"] = mode
            if notes != self.current.notes or notes == latest.notes:
                pulse = build_session_pulse(creator_profile_key=self._profile(), title=title, notes=notes)
                changes.update(notes=notes, decisions=pulse.decisions,
                    actions=tuple((f"@{a.owner} " if a.owner else "") + a.text for a in pulse.actions),
                    blockers=pulse.blockers)
            candidate = replace(latest, **changes)
            if candidate != self.current or self.current.id in self._pending:
                self._pending[candidate.id] = candidate

    def flush(self, *, include_editor=True) -> bool:
        self.timer.stop()
        if self._applying:
            return True
        self._capture_current_notes()
        if self.dialog is not None and getattr(self.dialog, "_dirty", False):
            # The editor owns its autosave timer. An unrelated live-Notes
            # timer or recording completion must not consume a draft that
            # import deliberately retained. Keep pending facts too, rather
            # than advancing the disk token behind that editor.
            if not include_editor or not self.dialog.save_current():
                return False
        for key, record in tuple(self._pending.items()):
            try:
                saved = self.library.save(record)
            except (OSError, ValueError):
                self._flash("Workspace changes are still here but not saved. Open Session library to retry or save a separate copy.")
                return False
            self._pending.pop(key, None)
            if self.current is not None and key == self.current.id:
                self.current = saved
        return True

    def remember_art_lesson(self, url):
        """Save an explicit lesson reference through the ordinary draft owner."""
        from core.youtube_lesson import parse_youtube_lesson_url

        lesson = parse_youtube_lesson_url(url)
        if self._profile() != "art" or self.media_operation_pending or self.studio_media_pending:
            self._flash("Finish the current workspace operation before remembering the lesson.")
            return False
        if not self.ensure_current():
            return False
        current_id = self.current.id
        self.show(tab="plan")
        dialog = self.dialog
        if dialog is None:
            return False
        dialog.select_id(current_id)
        if (self._profile() != "art" or self.current is None or self.current.id != current_id
                or dialog is not self.dialog or dialog.record is None or dialog.record.id != current_id
                or self.media_operation_pending):
            self._flash("Your existing workspace draft is retained. Select the current Art project and try again.")
            return False
        try:
            references = dialog.art.payload()["references"]
            if not any(ref["kind"] == "url" and ref["locator"] == lesson.playback_url for ref in references):
                position = f" at {lesson.start_s // 60}:{lesson.start_s % 60:02d}" if lesson.start_s else ""
                dialog.art.add_reference(lesson.playback_url, kind="url", title=f"YouTube lesson{position}")
        except ValueError as error:
            dialog.art.status.setText(str(error))
            return False
        saved = dialog.save_current()
        dialog.art.status.setText(
            "Lesson remembered. Use saved lesson returns to setup; WebJam does not track browser playback."
            if saved else "The lesson is in your retained draft. Use Save to retry."
        )
        return saved

    def profile_changing(self):
        if self.media_operation_pending or self.studio_media_pending:
            self._flash("Wait for the workspace operation result before changing profiles.")
            return False
        self.flush()
        self.current = None
        self._live_take_ids.clear()
        return True

    def remember_music_lesson(self, url):
        """Use the rehearsal editor's current song and ordinary save/conflict path."""
        from PySide6.QtWidgets import QInputDialog
        from core.youtube_lesson import parse_youtube_lesson_url

        lesson = parse_youtube_lesson_url(url)
        if self._profile() != "music" or self.media_operation_pending or self.studio_media_pending:
            self._flash("Finish the current workspace operation before remembering the lesson.")
            return False
        if not self.ensure_current():
            return False
        current_id = self.current.id
        self.show(tab="plan")
        dialog = self.dialog
        if dialog is None:
            return False
        dialog.select_id(current_id)
        binding = self._c.follow_along._identity()

        def current():
            return (self.dialog is dialog and self._profile() == "music"
                    and self.current is not None and self.current.id == current_id
                    and dialog.record is not None and dialog.record.id == current_id
                    and not self.media_operation_pending and not self.studio_media_pending
                    and binding == self._c.follow_along._identity()
                    and not self._c._shutdown_cleanup_blocks_action())

        if not current():
            self._flash("Your draft is retained. Select the current rehearsal workspace and try again.")
            return False
        song_id = dialog.rehearsal._plan.active_song_id
        title = None
        if not song_id:
            title, accepted = QInputDialog.getText(
                dialog, "Remember lesson", "Name a song for this lesson:", text="YouTube practice",
            )
            if not accepted or not title.strip() or not current():
                return False
        try:
            if not dialog.rehearsal.set_lesson_url(
                    lesson.playback_url, expected_song_id=song_id, new_title=title):
                return False
        except ValueError as error:
            dialog.rehearsal._feedback.setText(str(error))
            return False
        saved = dialog.save_current()
        dialog.rehearsal._feedback.setText(
            "Lesson remembered with this song. Use saved lesson returns to video practice."
            if saved else "The lesson is in your retained draft. Use Save to retry."
        )
        return saved

    def _use_saved_lesson(self, origin, request):
        binding, workspace_id, profile, item_id, url = request
        if (origin is not self.dialog or not origin.isVisible() or origin.record is None
                or self.current is None or self.current.id != workspace_id
                or origin.record.id != workspace_id or origin.record.profile != profile
                or self._profile() != profile or self.media_operation_pending or self.studio_media_pending
                or binding != self._c.follow_along._identity()
                or self._c._shutdown_cleanup_blocks_action()):
            self._flash("Continue the saved workspace first, then choose its lesson from Session library.")
            return
        if profile == "music":
            song = origin.rehearsal._plan.current or {}
            selected = (song.get("id"), song.get("lesson_url"))
        else:
            selected = origin.art.selected_lesson()
        if selected != (item_id, url):
            return
        if profile == "art" and not all(self._c._reference_video_identity()):
            from core.session_conductor import ArtRoomState

            room = self._c._room_participant
            if room.state is ArtRoomState.NONE and not self._c._completed_art_room_role():
                next_step = (
                    "Close Session library and choose Start Session to host."
                    if self._c.settings.host_server_enabled else
                    "Close Session library, choose Start Session and paste the host's invitation."
                )
            else:
                next_step = "Close Session library and finish the room's connection or recovery steps."
            message = (
                f"{next_step} Then reopen Session library and choose Use saved lesson. "
                "Your saved reference and edits stay here."
            )
            origin.show_art_lesson_feedback(message)
            self._flash(message)
            return
        if self._c.follow_along.use_saved_lesson(url):
            # Keep the same draft owner. show() reuses this retained dialog.
            origin.hide()
            self._c.window.activateWindow()
        else:
            self._flash("Return to the current room, then use this saved lesson again.")

    def show(self, *, tab=None):
        from webjam_qt.windows.session_library import SessionLibraryDialog
        if self.dialog is not None:
            self.dialog._lesson_binding = self._c.follow_along._identity() if hasattr(self._c, "follow_along") else None
            if tab == "plan":
                self.dialog.tabs.setCurrentIndex(1 if self._profile() == "music" else 2)
            self.dialog.show()
            self.dialog.raise_()
            self.dialog.activateWindow()
            return
        if not self.ensure_current():
            return
        self.flush()
        dialog = SessionLibraryDialog(self.library, self._c.window,
            profile=self._profile(), current_id=self.current.id, pending_records=self._pending,
            save_record=self.save_editor_record, prepare_take_open=self.prepare_take_open)
        self.dialog = dialog
        dialog.record_saved.connect(self._record_saved)
        dialog.copy_saved.connect(self._copy_saved)
        dialog.bookmark_requested.connect(self.mark_moment)
        dialog.bookmark_open_requested.connect(self.open_bookmark)
        dialog.take_open_requested.connect(self.open_take)
        dialog.song_selected.connect(self.song_selected)
        dialog._lesson_binding = self._c.follow_along._identity() if hasattr(self._c, "follow_along") else None
        dialog.lesson_requested.connect(lambda request: self._use_saved_lesson(dialog, request))
        if tab == "plan":
            dialog.tabs.setCurrentIndex(1 if self._profile() == "music" else 2)
        def finished(_result):
            selected = dialog.selected_record
            if self.dialog is dialog:
                self.dialog = None
            if selected is not None:
                self.continue_record(selected)
            dialog.deleteLater()

        # Keep live Stop/End and Notes reachable even when a disk failure
        # requires the library editor to retain an unsaved draft.
        dialog.finished.connect(finished)
        dialog.setModal(False)
        dialog.show()

    @property
    def media_operation_pending(self):
        return bool(self.dialog is not None and self.dialog.media_operation_pending)

    @property
    def studio_media_pending(self):
        studio = getattr(self._c.window, "recording_studio", None)
        return bool(getattr(studio, "media_open_pending", False))

    def prepare_close(self):
        return self.dialog is None or self.dialog.prepare_close()

    def save_editor_record(self, base, edited):
        """Reconcile the editor before writing, including late take/recap facts.

        Only our known in-memory snapshot can advance an editor's disk token.
        The store still rejects external writes we have not read and owned.
        """
        self._capture_current_notes()
        latest = self._pending.get(base.id)
        if latest is None and self.current is not None and self.current.id == base.id:
            latest = self.current
        latest = deepcopy(latest or base)
        merged = _merge_editor_changes(base, edited, latest)
        saved = self.library.save(merged)
        if _same_snapshot(self._pending.get(saved.id), latest):
            self._pending.pop(saved.id, None)
        self._record_saved(saved)
        return saved

    def _record_saved(self, record):
        pending = self._pending.get(record.id)
        if pending is not None:
            if not _same_snapshot(pending, record):
                self._flash("The editor was saved; newer workspace changes are still retained for retry or a separate copy.")
                return
            self._pending.pop(record.id, None)
        if self.current is not None and self.current.id == record.id:
            self.current = record
            self._applying = True
            try:
                self._c.window.session_strip.set_session_title(record.title)
                self._c.window.session_canvas.set_notes(record.notes)
            finally:
                self._applying = False
            self._refresh_song_tools()

    def _copy_saved(self, previous, copied):
        self._capture_current_notes()
        pending = self._pending.get(previous.id)
        if pending is not None and not _same_snapshot(pending, previous):
            self._flash("Your separate copy is saved. Newer changes remain with the original workspace; reopen its retained draft to review them.")
            return
        if pending is not None:
            self._pending.pop(previous.id, None)
        if self.current is not None and previous.id == self.current.id:
            if pending is None and not _same_snapshot(self.current, previous):
                self._flash("Your separate copy is saved. The original workspace also has newer changes and remains active.")
                return
            self.current = copied
            self._record_saved(copied)

    def _busy(self):
        audio = getattr(self._c, "audio", None)
        recording = getattr(self._c, "recording", None)
        attempt = getattr(self._c, "_startup_attempt", None)
        starting = bool(attempt and attempt.get("phase") not in {"failed", "complete", "connected"})
        return bool(getattr(audio, "connected", False) or getattr(audio, "stopping", False)
                    or getattr(self._c, "_art_room_role", "")
                    or getattr(recording, "is_recording_active", False)
                    or getattr(recording, "take_in_progress", False)
                    or starting
                    or self._c._is_jamulus_running())

    def continue_record(self, record) -> bool:
        if self.media_operation_pending or self.studio_media_pending:
            self._flash("Wait for the workspace operation result before continuing another workspace.")
            return False
        if self.current is not None and self.current.id == record.id:
            self._c._on_rail_view_changed("canvas")
            return True
        if self._busy():
            self._flash("End or leave this session before continuing a different workspace. Its saved work is ready when you are.")
            return False
        if not self._c._save_notes() or not self.flush():
            return False
        try:
            record = self.library.load(record.id, profile=record.profile)
        except (OSError, ValueError):
            self._flash("This workspace could not be reopened. Open Session library to review it.")
            return False
        self._applying = True
        try:
            self._c._apply_creator_profile_key(record.profile, host_owned=False)
            self.current = record
            self._run_id = ""
            self._live_take_ids.clear()
            self._c.window.session_strip.set_session_title(record.title)
            if record.mode_key:
                picker = self._c.window.session_strip._mode_picker
                index = picker.findData(record.mode_key)
                if index >= 0:
                    picker.setCurrentIndex(index)
            self._c.window.session_canvas.set_notes(record.notes)
        finally:
            self._applying = False
        self._refresh_song_tools()
        self._c._on_rail_view_changed("canvas")
        self._flash("Workspace reopened locally. Host or Join when you want to meet.")
        return True

    def start_session(self):
        if not self._run_id:
            self._run_id = uuid4().hex
            self._live_take_ids.clear()
        self.ensure_current()

    def capture_summary(self):
        if not self.ensure_current():
            return
        self.flush()
        from webjam_qt.windows.session_library import workspace_summary
        snapshot = self._pending.get(self.current.id, self.current)
        self._pending_recap = {"run_id": self._run_id or uuid4().hex,
            "ended_at": datetime.now(timezone.utc).isoformat(),
            "duration_seconds": int(getattr(self._c.window.session_strip, "_elapsed_seconds", 0)),
            "summary": (art_summary(snapshot.title, snapshot.art)
                        if snapshot.profile == "art" else workspace_summary(replace(snapshot, recaps=()))),
            "take_ids": sorted(self._live_take_ids)}
        self._recap_owner = self.current

    def finish_session(self):
        if self._pending_recap is None or self._recap_owner is None:
            return
        owner = self._recap_owner
        if self.current is not None and self.current.id == owner.id:
            owner = self.current
            recap = dict(self._pending_recap, take_ids=sorted(self._live_take_ids))
        else:
            recap = self._pending_recap
            try:
                owner = self._pending.get(owner.id) or self.library.load(owner.id)
            except (OSError, ValueError):
                # The captured owner is still a usable retained draft. Its
                # original store token prevents overwriting changed disk data;
                # expose it through the usual pending-record recovery path.
                pass
        self._pending_recap = None
        self._recap_owner = None
        updated = replace(owner, recaps=(*owner.recaps, recap))
        if self.current is not None and self.current.id == owner.id:
            self.current = updated
        self._pending[owner.id] = updated
        saved = self.flush()
        self._run_id = ""
        if saved:
            self._flash("Art progress and next steps saved in Session library." if owner.profile == "art"
                        else "Rehearsal recap saved in Session library.")

    def recording_started(self, take_id: str, session_id: str = ""):
        if not self.ensure_current() or self.current.profile == "art":
            return
        self._recording_owners[take_id] = (self.current.id, self._run_id)
        entry = {"take_id": take_id, "take_path": "", "run_id": self._run_id,
                 "recording_session_id": session_id, "validated": False,
                 "status": "pending", "title": "Recording requested"}
        self.current = replace(self.current, take_links=(*self.current.take_links, entry))
        self._pending[self.current.id] = self.current
        self.flush()

    def recording_completed(self, take, *, validated: bool):
        # The recording-start reservation, not whichever editor is now visible,
        # owns this exact take. Its durable ID also survives interrupted takes.
        if not take.take_id:
            return
        owner = None
        binding = self._recording_owners.get(take.take_id)
        if binding is not None:
            owner_id, run_id = binding
            if self.current is not None and self.current.id == owner_id:
                owner = self.current
            else:
                try:
                    owner = self._pending.get(owner_id) or self.library.load(owner_id)
                except (OSError, ValueError):
                    return
        else:
            matches = []
            try:
                for record in self.library.list():
                    for ref in record.take_links:
                        if (ref.get("take_id") == take.take_id and ref.get("recording_session_id")
                                and ref["recording_session_id"] == getattr(take, "session_id", "")):
                            matches.append((record, ref.get("run_id", "")))
            except (OSError, ValueError):
                return
            if len(matches) != 1:
                return
            owner, run_id = matches[0]
        if owner.profile == "art":
            return
        from core.take_review import take_source_identity
        try:
            identity = take_source_identity(take)
        except (OSError, ValueError):
            return
        ref = {"take_id": take.take_id, "take_path": str(take.path), "title": take.display_name,
               "source_identity": identity, "validated": bool(validated), "run_id": run_id,
               "recording_session_id": getattr(take, "session_id", ""), "status": "complete" if validated else "needs_attention"}
        links = tuple(item for item in owner.take_links if item.get("take_id") != take.take_id)
        recaps = tuple(dict(recap, take_ids=sorted(set(recap.get("take_ids", [])) | {take.take_id}))
                       if run_id and recap.get("run_id") == run_id else recap for recap in owner.recaps)
        owner = replace(owner, take_links=(*links, ref), recaps=recaps)
        if self.current is not None and self.current.id == owner.id:
            self.current = owner
            if not self._run_id or self._run_id == run_id:
                self._live_take_ids.add(take.take_id)
        self._pending[owner.id] = owner
        self.flush(include_editor=False)

    def current_take_status(self):
        if self.current is None:
            return None
        refs = [item for item in self.current.take_links if item.get("take_id") in self._live_take_ids]
        return ("Ready" if refs[-1].get("validated") else "Needs attention") if refs else None

    def open_take(self, ref):
        if ref.get("_verification") is not None:
            self._open_prepared_reference(ref)
            return
        if not isinstance(ref.get("take_path"), str) or not ref["take_path"].strip():
            self._flash("This recording has no completed take yet. Wait for recording and finalization to finish.")
            return
        if not self._imported_reference_can_open(ref):
            return
        if self._profile() == "art":
            self._flash("Switch to this Music workspace before opening its take.")
            return
        studio = self._c.window.recording_studio
        self._c._on_rail_view_changed("takes")
        if studio.jump_to_bookmark(ref.get("take_path", ""), 0, take_id=ref.get("take_id"),
                                   source_identity=ref.get("source_identity")):
            if self.dialog:
                self.dialog.accept()
        else:
            self._flash("The linked take is missing or changed. Locate the same take in Session library.")

    def prepare_take_open(self, _reference):
        recording = getattr(self._c, "recording", None)
        if (self._profile() == "art" or getattr(self._c, "_shutdown", False)
                or getattr(self._c, "_shutdown_in_progress", False)
                or getattr(recording, "is_recording_active", False) or getattr(recording, "take_in_progress", False)):
            self._flash("Finish the current recording and use a Music workspace before opening this take.")
            return None
        studio = self._c.window.recording_studio
        state = studio.prepare_workspace_open()
        if state is None:
            self._flash("Finish the Studio operation or save its draft before opening this take.")
            return None
        return {"studio": studio, "state": state, "owner": self.current.id if self.current else None,
                "profile": self._profile()}

    def open_verified_launch_reference(self, reference):
        """Bind a consumed launch intent to this newly constructed runtime."""
        snapshot = reference.get("_workspace_snapshot")
        if (self.dialog is not None or not _same_snapshot(self.current, snapshot)
                or reference.get("_studio_request") is not None
                or reference.get("_origin_dialog") is not None):
            self._flash("Workspace changed after launch selection. Open its take again from Session library.")
            return
        request = self.prepare_take_open(reference)
        if request is None:
            return
        studio = request["studio"]
        if not studio.open_prepared_take(reference.get("_verification"), workspace=snapshot,
                expected_state=request["state"], position_seconds=reference.get("position_seconds", 0)):
            self._flash("The recording changed after launch selection. Verify and open it again from Session library.")
            return
        self._c._on_rail_view_changed("takes")

    def _open_prepared_reference(self, reference):
        from PySide6.QtCore import QTimer
        origin = reference.get("_origin_dialog")
        request = reference.get("_studio_request")
        snapshot = reference.get("_workspace_snapshot")
        recording = getattr(self._c, "recording", None)
        if (origin is None or self.dialog is not origin or not request or snapshot is None
                or origin.record is None or origin.record.id != snapshot.id
                or request["owner"] != (self.current.id if self.current else None)
                or request["profile"] != self._profile()
                or getattr(self._c, "_shutdown", False) or getattr(self._c, "_shutdown_in_progress", False)
                or getattr(recording, "is_recording_active", False) or getattr(recording, "take_in_progress", False)):
            self._flash("Workspace ownership changed while checking the take. Open it again when ready.")
            return
        studio = self._c.window.recording_studio
        if request["studio"] is not studio or not studio.open_prepared_take(
                reference["_verification"], workspace=snapshot, expected_state=request["state"],
                position_seconds=reference.get("position_seconds", 0)):
            origin.status.setText("Studio or the recording changed during verification. Open the selected take again.")
            return
        self._c._on_rail_view_changed("takes")

        def close_origin():
            if (self.dialog is origin and not origin.media_operation_pending
                    and not origin._dirty and _same_snapshot(origin.record, snapshot)
                    and origin._edited_record() == snapshot):
                origin.accept()
        # The flow gate remains held through activation. Close only its exact
        # originating dialog after the terminal callback has returned.
        QTimer.singleShot(0, close_origin)

    def mark_moment(self, note):
        if self.dialog is None:
            return
        reference = self._c.window.recording_studio.current_take_reference()
        record = getattr(self.dialog, "record", None)
        recording = getattr(self._c, "recording", None)
        capturing = bool(getattr(recording, "is_recording_active", False)
                         or getattr(recording, "take_in_progress", False))
        if not capturing and reference and record and record.profile == "music" and any(
                all(item.get(key) and item.get(key) == reference.get(key)
                    for key in ("take_id", "source_identity", "take_path"))
                for item in record.take_links):
            self.dialog.rehearsal.add_bookmark(note, timing_verified=True, **reference)
        else:
            self.dialog.rehearsal.add_bookmark(note)

    def open_bookmark(self, mark):
        if self._profile() == "art" or not isinstance(mark.get("take_path"), str) or not mark["take_path"].strip():
            return
        if not self._imported_reference_can_open(mark):
            return
        self._c._on_rail_view_changed("takes")
        opened = self._c.window.recording_studio.jump_to_bookmark(mark.get("take_path", ""),
            mark.get("position_seconds", 0), take_id=mark.get("take_id"),
            source_identity=mark.get("source_identity"))
        if opened and self.dialog:
            self.dialog.accept()

    def _imported_reference_can_open(self, reference):
        record = getattr(self.dialog, "record", None) if self.dialog else self.current
        imported = reference.get("_imported_link") or (record is not None and record.import_provenance)
        if imported and not all(isinstance(reference.get(key), str) and reference[key].strip()
                                for key in ("take_id", "source_identity")):
            self._flash("This imported link has no complete take identity. Open its original recording separately in Studio.")
            return False
        return True

    def song_selected(self, _song):
        if self.dialog and self.current and self.dialog.record.id == self.current.id:
            self.dialog.save_current()
            self._refresh_song_tools()

    def song_context(self):
        if self.current is None or self.current.profile != "music":
            return None
        plan = self.current.rehearsal
        song = next((item for item in plan.get("songs", [])
                     if item.get("id") == plan.get("active_song_id")), None)
        if song is None:
            return None
        notes = [song.get("notes", "")]
        if song.get("key"):
            notes.append(f"Key: {song['key']}")
        if song.get("tempo"):
            notes.append(f"Tempo: {song['tempo']} BPM")
        return song.get("title", ""), "\n".join(notes)

    def _refresh_song_tools(self):
        coordinator = getattr(self._c, "_song_tools", None)
        if coordinator is not None:
            coordinator._sync_workbench()
            coordinator.refresh()
