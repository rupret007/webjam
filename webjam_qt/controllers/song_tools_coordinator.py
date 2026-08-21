"""Drives the in-session song panel: writing help, and Music AI when asked.

The controller owns the session; this owns the song. It keeps a
:class:`~core.song_workbench.SongWorkbench` in step with the notes the room is
typing, answers writing and chord requests entirely locally, and runs Music AI
jobs on a worker thread so a five-minute stem separation never freezes a live
jam.

Two rules are enforced on the way out rather than in the widget:

* **Nothing is uploaded without the host saying yes to that exact file.** The
  decision is made by :func:`core.song_workbench.evaluate_upload`, and the
  confirmation names the file, the size, and the tool before anything leaves.
* **Nothing appears on its own.** WebJam is expected to sit beside a Webex
  window, so the panel is only ever shown by a musician's own click, and
  background job progress updates it in place instead of raising a window.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

from PySide6.QtWidgets import QFileDialog, QMessageBox

from core.meeting_companion import (
    DEFAULT_MEETING_SERVICE,
    build_invite_message,
    describe_mutes,
    end_session_prompt,
)
from core.music_ai_catalog import SongToolCatalog, failed_catalog, resolve_song_tools
from core.music_ai_client import (
    MusicAIClient,
    MusicAIError,
    missing_key_message,
)
from core.music_ai_results import download_artifacts, interpret_job
from core.song_workbench import (
    SOURCE_PICKED_FILE,
    SOURCE_SHARED_TRACK,
    SharedTrackView,
    SongWorkbench,
    evaluate_upload,
)

LOGGER = logging.getLogger("webjam.qt.song_tools")

AUDIO_FILTER = (
    "Audio (*.wav *.mp3 *.flac *.m4a *.aif *.aiff *.ogg);;All files (*)"
)
_RESULTS_DIRNAME = "WebJam Song Tools"


class SongToolsCoordinator:
    """Owns the song panel for one live Music session."""

    def __init__(self, controller: Any) -> None:
        self._c = controller
        self.workbench = SongWorkbench()
        self._catalog: SongToolCatalog | None = None
        self._discovering = False
        self._running_verb = ""
        self._cancelled = threading.Event()

    # ------------------------------------------------------------------
    # Panel lifecycle
    # ------------------------------------------------------------------
    @property
    def overlay(self):
        return getattr(self._c.window, "song_overlay", None)

    def is_available(self) -> bool:
        """Song tools exist only where a song form does: Music sessions."""

        profile = getattr(self._c, "creator_profile", None)
        return bool(profile is not None and profile.key == "music")

    def toggle_panel(self) -> None:
        """Show or hide the panel in response to an explicit click."""

        overlay = self.overlay
        if overlay is None:
            return
        if overlay.isVisible():
            overlay.setVisible(False)
            return
        overlay.setVisible(True)
        self.refresh()
        if self._api_key() and self._catalog is None:
            self.discover_workflows()

    def close_panel(self) -> None:
        overlay = self.overlay
        if overlay is not None:
            overlay.setVisible(False)

    def connect(self) -> None:
        """Wire the panel's signals once, at controller construction."""

        overlay = self.overlay
        if overlay is None:
            return
        overlay.write_help_requested.connect(self.show_writing_help)
        overlay.chords_requested.connect(self.show_chords)
        overlay.song_tool_requested.connect(self.run_song_tool)
        overlay.share_sheet_requested.connect(self.share_sheet_to_chat)
        overlay.api_key_requested.connect(self._open_settings)
        overlay.mute_help_requested.connect(self._open_meeting_mute)
        overlay.invite_requested.connect(self._copy_invite)
        overlay.closed.connect(lambda: None)

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------
    def refresh(self) -> None:
        """Re-render every page from current session state."""

        overlay = self.overlay
        if overlay is None or not overlay.isVisible():
            return
        self._sync_workbench()
        overlay.set_song_state(
            catch_up=self.workbench.catch_up(
                shared_track=self._shared_track_view(),
                elapsed_seconds=self._elapsed_seconds(),
                is_host=self._is_host(),
            ),
            form_summary=self.workbench.conductor_line(),
            results=tuple(
                run.summary_line() for run in self.workbench.runs[-2:]
            ),
            sheet_shareable=bool(self.workbench.shareable_sheet()),
        )
        overlay.set_tools_state(
            catalog=self._catalog,
            has_api_key=bool(self._api_key()),
            is_host=self._is_host(),
            missing_key_text=missing_key_message(),
        )
        overlay.set_meeting_state(
            mutes=describe_mutes(
                webjam_muted_participants=self._muted_count(),
                participant_count=self._participant_count(),
                meeting_configured=self._meeting_configured(),
                meeting_service=DEFAULT_MEETING_SERVICE,
            ),
            end_note=end_session_prompt(
                hosting=self._is_host(),
                meeting_configured=self._meeting_configured(),
            ).meeting_note,
            meeting_configured=self._meeting_configured(),
        )

    def show_writing_help(self) -> None:
        """Answer "help me write" from the room's own song. Nothing leaves."""

        overlay = self.overlay
        if overlay is None:
            return
        self._sync_workbench()
        overlay.set_song_state(
            catch_up=None,
            form_summary=self.workbench.conductor_line(),
            advice=self.workbench.writing_advice(),
            results=tuple(run.summary_line() for run in self.workbench.runs[-2:]),
            sheet_shareable=bool(self.workbench.shareable_sheet()),
        )

    def show_chords(self, role: str = "") -> None:
        """Suggest changes for a part the song does not have yet."""

        overlay = self.overlay
        if overlay is None:
            return
        self._sync_workbench()
        overlay.set_song_state(
            catch_up=None,
            form_summary=self.workbench.conductor_line(),
            chords=self.workbench.chord_advice(str(role or "")),
            results=tuple(run.summary_line() for run in self.workbench.runs[-2:]),
            sheet_shareable=bool(self.workbench.shareable_sheet()),
        )

    def share_sheet_to_chat(self) -> None:
        """Post the song sheet into band chat so a late arrival can catch up."""

        self._sync_workbench()
        sheet = self.workbench.shareable_sheet()
        if not sheet:
            self._flash("Write a key, tempo, or section in the notes first.")
            return
        jamulus = getattr(self._c, "jamulus", None)
        sender = getattr(jamulus, "send_chat", None)
        if sender is None:
            self._flash("Band chat is unavailable right now.")
            return
        try:
            # Sent straight to chat rather than through the canvas handler,
            # which echoes into the local notes -- and the notes are where the
            # sheet was read from in the first place.
            accepted = bool(sender(sheet))
        except Exception:  # noqa: BLE001 - chat is best-effort, never fatal
            LOGGER.warning("Sharing the song sheet to chat failed", exc_info=True)
            accepted = False
        self._flash(
            "Song sheet posted to band chat."
            if accepted
            else "WebJam could not post the sheet to chat."
        )

    # ------------------------------------------------------------------
    # Music AI
    # ------------------------------------------------------------------
    def discover_workflows(self) -> None:
        """Ask the account which Song tools it can actually run."""

        key = self._api_key()
        if not key or self._discovering:
            return
        self._discovering = True

        def work() -> None:
            try:
                client = MusicAIClient(key)
                catalog = resolve_song_tools(client.list_workflows())
            except MusicAIError as exc:
                catalog = failed_catalog(str(exc))
            except Exception:  # noqa: BLE001 - a worker must never escape
                LOGGER.warning("Music AI workflow discovery failed", exc_info=True)
                catalog = failed_catalog(
                    "WebJam could not read this account's Music AI workflows."
                )
            self._on_ui(lambda: self._apply_catalog(catalog))

        threading.Thread(
            target=work, daemon=True, name="music-ai-workflows"
        ).start()

    def run_song_tool(self, verb_key: str) -> None:
        """Run one Music AI verb on a file the host picks and confirms."""

        key = str(verb_key or "")
        if self._running_verb:
            self._flash("One Song tool is already running.")
            return
        capability = (
            self._catalog.capability(key) if self._catalog is not None else None
        )
        api_key = self._api_key()

        source_kind, path = self._choose_source(capability)
        if not path:
            return

        decision = evaluate_upload(
            capability=capability,
            source_kind=source_kind,
            path=path,
            is_host=self._is_host(),
            has_api_key=bool(api_key),
        )
        if decision.blocked:
            self._flash(decision.reason, ms=9000)
            return
        if not self._confirm_upload(decision):
            return

        assert capability is not None  # evaluate_upload rejects None
        self._running_verb = key
        self._cancelled.clear()
        overlay = self.overlay
        if overlay is not None:
            overlay.set_busy(key, f"Running {capability.label}…")
        self._flash(f"{capability.label} started. The jam keeps running.")

        results_dir = self._results_directory()
        session_name = self._session_title()

        def work() -> None:
            try:
                client = MusicAIClient(api_key)
                job = client.run_file_workflow(
                    path,
                    workflow=capability.workflow_slug,
                    name=f"WebJam · {capability.label} · {session_name}"[:120],
                    should_cancel=self._cancelled.is_set,
                )
                run = interpret_job(
                    job, capability, source_name=Path(path).name
                )
                run = download_artifacts(
                    run,
                    transport=client.transport,
                    directory=results_dir,
                )
            except MusicAIError as exc:
                # Bind the text now: ``exc`` is cleared when the except block
                # ends, so a lambda closing over it would raise on the UI
                # thread instead of reporting the failure.
                message = str(exc)
                self._on_ui(lambda: self._finish_tool(None, message))
                return
            except Exception:  # noqa: BLE001 - a worker must never escape
                LOGGER.warning("Song tool run failed", exc_info=True)
                self._on_ui(
                    lambda: self._finish_tool(
                        None, "That Song tool did not finish. Try again."
                    )
                )
                return
            self._on_ui(lambda: self._finish_tool(run, ""))

        threading.Thread(target=work, daemon=True, name="music-ai-job").start()

    def cancel(self) -> None:
        """Stop waiting on a running job; the session is never blocked by one."""

        self._cancelled.set()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _apply_catalog(self, catalog: SongToolCatalog) -> None:
        self._discovering = False
        self._catalog = catalog
        self.workbench.set_catalog(catalog)
        self.refresh()

    def _finish_tool(self, run: Any, error: str) -> None:
        self._running_verb = ""
        overlay = self.overlay
        if overlay is not None:
            overlay.set_busy("", "")
        if error:
            self._flash(error, ms=9000)
            self.refresh()
            return
        self.workbench.attach_run(run)
        self._flash(run.summary_line(), ms=8000)
        self.refresh()

    def _choose_source(self, capability: Any) -> tuple[str, str]:
        """Return the file the host chose. Never discovers one on its own."""

        if capability is None or not getattr(capability, "supported", False):
            # Let evaluate_upload own the refusal copy so there is one place
            # that decides, rather than two that can disagree.
            return SOURCE_PICKED_FILE, "unavailable"

        shared = self._shared_track_path()
        if shared:
            answer = QMessageBox.question(
                self._c.window,
                "Which file?",
                (
                    "Use the session's Shared Track, or pick another file?\n\n"
                    f"Shared Track: {Path(shared).name}"
                ),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if answer == QMessageBox.StandardButton.Yes:
                return SOURCE_SHARED_TRACK, shared

        path, _filter = QFileDialog.getOpenFileName(
            self._c.window,
            "Choose an audio file you own",
            "",
            AUDIO_FILTER,
        )
        return SOURCE_PICKED_FILE, str(path or "")

    def _confirm_upload(self, decision: Any) -> bool:
        """The host's explicit yes for this exact file, or nothing happens."""

        answer = QMessageBox.question(
            self._c.window,
            decision.confirmation_title,
            decision.confirmation_body,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def _sync_workbench(self) -> None:
        canvas = getattr(self._c.window, "session_canvas", None)
        if canvas is not None:
            self.workbench.set_notes(canvas.current_notes())
        strip = getattr(self._c.window, "session_strip", None)
        if strip is not None:
            self.workbench.set_title(strip.current_title())

    def _shared_track_view(self) -> SharedTrackView:
        snapshot = getattr(self._c, "_last_shared_track_snapshot", None)
        if snapshot is None:
            return SharedTrackView()
        state = str(getattr(snapshot, "state", "") or "").lower()
        name = str(getattr(snapshot, "source_name", "") or "")
        return SharedTrackView(
            loaded=bool(name),
            playing="play" in state,
            source_name=name,
            position_s=float(getattr(snapshot, "position_s", 0.0) or 0.0),
            duration_s=float(getattr(snapshot, "duration_s", 0.0) or 0.0),
            host_controlled=True,
        )

    def _shared_track_path(self) -> str:
        """Return a readable Shared Track path, or ``""``.

        ``core.reference_track`` deliberately never persists the selected media
        path, so this is usually empty and the host simply picks a file. It is
        read opportunistically rather than required.
        """

        candidate = getattr(self._c, "_reference_track_load_pending", None)
        if isinstance(candidate, str) and candidate:
            try:
                if Path(candidate).is_file():
                    return candidate
            except OSError:
                return ""
        return ""

    def _results_directory(self) -> Path:
        configured = str(
            getattr(self._c.settings, "takes_directory", "") or ""
        ).strip()
        base = Path(configured) if configured else Path.home()
        return base / _RESULTS_DIRNAME

    def _api_key(self) -> str:
        return str(
            getattr(self._c.settings, "music_ai_api_key", "") or ""
        ).strip()

    def _is_host(self) -> bool:
        checker = getattr(self._c, "_reference_track_is_host", None)
        if checker is None:
            return False
        try:
            return bool(checker())
        except Exception:  # noqa: BLE001 - a role check must never break the UI
            return False

    def _meeting_configured(self) -> bool:
        return bool(str(getattr(self._c.settings, "webex_url", "") or "").strip())

    def _session_title(self) -> str:
        strip = getattr(self._c.window, "session_strip", None)
        if strip is None:
            return "WebJam"
        return strip.current_title() or "WebJam"

    def _elapsed_seconds(self) -> float:
        strip = getattr(self._c.window, "session_strip", None)
        return float(getattr(strip, "_elapsed_seconds", 0) or 0)

    def _participant_count(self) -> int:
        try:
            return len(self._c._snapshot_participants())
        except Exception:  # noqa: BLE001 - presentation only
            return 0

    def _muted_count(self) -> int:
        try:
            return sum(
                1
                for participant in self._c._snapshot_participants()
                if getattr(participant, "muted", False)
            )
        except Exception:  # noqa: BLE001 - presentation only
            return 0

    def _open_settings(self) -> None:
        opener = getattr(self._c, "_open_settings_wizard", None)
        if opener is not None:
            opener()

    def _open_meeting_mute(self) -> None:
        opener = getattr(self._c, "_focus_webex_mute", None)
        if opener is not None:
            opener()

    def _copy_invite(self) -> None:
        """Copy one message carrying the jam link and the meeting link."""

        from PySide6.QtWidgets import QApplication

        readiness = self._c._host_share_readiness()
        join_link = self._c._current_invite_url(readiness=readiness)
        if not join_link:
            self._flash("Connect this Mac to Wi-Fi, then try again.")
            return
        message = build_invite_message(
            join_link=join_link,
            session_name=self._session_title(),
            meeting_url=str(getattr(self._c.settings, "webex_url", "") or ""),
            participant_noun=(
                self._c.creator_profile.vocabulary.participant_singular
            ),
        )
        QApplication.clipboard().setText(message.text)
        self._flash(
            "One invite copied — jam link and meeting link."
            if message.includes_meeting
            else "Invite copied. Add a meeting link in Settings to include it."
        )

    def _flash(self, message: str, *, ms: int = 6000) -> None:
        window = getattr(self._c, "window", None)
        flash = getattr(window, "flash_message", None)
        if flash is not None:
            flash(str(message), ms=int(ms))

    def _on_ui(self, callback) -> None:
        invoker = getattr(self._c, "_ui_invoker", None)
        if invoker is None:
            callback()
            return
        invoker.invoke(callback)


__all__ = ["AUDIO_FILTER", "SongToolsCoordinator"]
