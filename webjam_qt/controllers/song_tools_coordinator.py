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
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from PySide6.QtWidgets import QFileDialog, QMessageBox

from core.meeting_companion import (
    describe_mutes,
    meeting_recording_note,
    end_session_prompt,
    service_name_for_link,
)
from core.music_ai_catalog import SongToolCatalog, failed_catalog, resolve_song_tools
from core.music_ai_client import (
    MusicAIClient,
    MusicAIError,
    missing_key_message,
)
from core.music_ai_results import download_artifacts, interpret_job
from core.music_companion import (
    COMMAND_SUGGEST_CHORDS,
    COMMAND_WRITE_HELP,
    MusicCompanionDecision,
    MusicCompanionSnapshot,
    build_snapshot,
    evaluate_command,
    parse_command,
)
from core.song_workbench import (
    SOURCE_PICKED_FILE,
    SOURCE_SHARED_TRACK,
    JobBudget,
    SharedTrackView,
    SongWorkbench,
    evaluate_upload,
    evaluate_upload_preconditions,
)
from core.stem_bench import StemBenchError, bounce_stems

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
        self._tick_timer = None
        self._last_suggestion = None
        self._last_suggestion_section = ""
        self._companion_revision = 0
        self._last_companion_public: dict | None = None
        # A job WebJam stopped waiting for -- usually because the machine
        # slept -- is still running at Music AI. It is remembered so the
        # status can be reported honestly instead of silently restarted.
        self._unfinished_job = ""
        # A jam should not be able to fire the same separation twenty times.
        self._budget = JobBudget()

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
        # Retry a discovery that failed. A network blip while the panel was
        # open once should not disable Song tools for the rest of the night;
        # a successful list is kept and not re-fetched.
        catalog = self._catalog
        if self._api_key() and (catalog is None or not catalog.discovered):
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
        overlay.suggestion_kept.connect(self.keep_suggestion)
        overlay.suggestions_dismissed.connect(self.dismiss_suggestions)
        overlay.clock_toggled.connect(self.toggle_clock)
        overlay.section_located.connect(self.locate_section)
        overlay.stem_mute_toggled.connect(self.toggle_stem_mute)
        overlay.stem_solo_toggled.connect(self.toggle_stem_solo)
        overlay.sing_this_one_requested.connect(self.sing_this_one)
        overlay.send_stems_to_jam_requested.connect(self.send_stems_to_jam)
        overlay.closed.connect(self._on_panel_closed)

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------
    def refresh(self) -> None:
        """Re-render every page from current session state."""

        overlay = self.overlay
        if overlay is None or not overlay.isVisible():
            return
        self._sync_workbench()
        self._sync_clock_to_shared_track()
        overlay.set_sections(
            self.workbench.section_names(),
            current=self.workbench.clock_snapshot().section_label,
        )
        overlay.set_song_state(
            catch_up=self.workbench.catch_up(
                shared_track=self._shared_track_view(),
                elapsed_seconds=self._elapsed_seconds(),
                is_host=self._is_host(),
            ),
            form_summary=self.workbench.conductor_line(),
            form_rows=self.workbench.form_overlay(),
            clock=self.workbench.clock_snapshot(),
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
        self._render_stems()
        self._render_song_line()
        service = service_name_for_link(self._meeting_url())
        overlay.set_meeting_state(
            mutes=describe_mutes(
                webjam_muted_participants=self._muted_count(),
                participant_count=self._participant_count(),
                meeting_configured=self._meeting_configured(),
                meeting_service=service,
            ),
            end_note=end_session_prompt(
                hosting=self._is_host(),
                meeting_configured=self._meeting_configured(),
                meeting_service=service,
            ).meeting_note,
            recording_note=(
                meeting_recording_note(meeting_service=service)
                if self._meeting_configured()
                else ""
            ),
            meeting_configured=self._meeting_configured(),
        )

    # ------------------------------------------------------------------
    # Companion surface (see core.music_companion)
    # ------------------------------------------------------------------
    def invite_song_line(self) -> str:
        """Return the song a joiner is joining, or ``""``.

        Key, tempo, and shape only. No file path travels in an invite, and a
        joiner is never asked to pick a song the room has already chosen.
        """

        if not self.is_available():
            return ""
        self._sync_workbench()
        form = self.workbench.form
        if not form.has_content:
            return ""
        parts = [part for part in (form.title,) if part]
        if form.key is not None:
            parts.append(f"Key {form.key.value}")
        if form.tempo is not None:
            parts.append(f"{form.tempo.value} BPM")
        sections = " → ".join(section.label for section in form.sections[:4])
        if sections:
            parts.append(sections)
        return " · ".join(parts)

    def companion_snapshot(self) -> MusicCompanionSnapshot:
        """Return what a companion may know about this Music session.

        Safe to call from any surface; it reads state and publishes nothing.
        The bounding and scrubbing live in ``core.music_companion`` so this
        cannot leak a path or a key by forgetting to.
        """

        if not self.is_available():
            return self._with_revision(
                build_snapshot(revision=0, is_music_session=False)
            )

        self._sync_workbench()
        self._sync_clock_to_shared_track()
        catalog = self._catalog
        available = (
            tuple(item.key for item in catalog.available)
            if catalog is not None
            else ()
        )
        reason = ""
        if not self._api_key():
            reason = missing_key_message()
        elif catalog is not None and not catalog.usable:
            reason = catalog.summary_line()

        form = self.workbench.form
        lyric = ""
        section_label = self.workbench.clock_snapshot().section_label
        for section in form.sections:
            if section.label == section_label and section.lyrics:
                lyric = section.lyrics[0]
                break

        return self._with_revision(build_snapshot(
            revision=0,
            is_music_session=True,
            clock=self.workbench.clock_snapshot(),
            form_rows=self.workbench.form_overlay(),
            lyric_line=lyric,
            shared_track_loaded=bool(self._shared_track_path()),
            is_host=self._is_host(),
            tools_available=available,
            tools_unavailable_reason=reason,
            job_verb=self._running_verb,
            job_label=self._verb_label(self._running_verb)
            if self._running_verb
            else "",
            suggestion=self._last_suggestion,
            suggestion_section=self._last_suggestion_section,
        ))

    def _with_revision(
        self, snapshot: MusicCompanionSnapshot
    ) -> MusicCompanionSnapshot:
        """Stamp a revision that changes whenever anything published changes.

        A companion polls on this, so a counter that only moved when a
        suggestion changed would leave a panel showing bar one all night.
        Comparing the produced wire form is exact and stays monotonic.
        """

        published = snapshot.to_public_dict()
        published.pop("revision", None)
        if published != self._last_companion_public:
            self._last_companion_public = published
            self._companion_revision += 1
        return replace(snapshot, revision=self._companion_revision)

    def handle_companion_command(self, payload) -> MusicCompanionDecision:
        """Answer one companion request. The desktop decides; this is where.

        An accepted tool request runs on the Shared Track the host already
        chose. There is no path in the contract, so no companion can point
        Song tools anywhere else, and none of these paths opens a file picker.
        """

        command = parse_command(payload)
        snapshot = self.companion_snapshot()
        decision = evaluate_command(command, snapshot)
        if not decision.accepted or decision.command is None:
            return decision

        accepted = decision.command
        if accepted.name == COMMAND_WRITE_HELP:
            self.show_writing_help()
        elif accepted.name == COMMAND_SUGGEST_CHORDS:
            self.show_chords(accepted.section)
        else:
            self.run_song_tool(accepted.verb, from_companion=True)
        return decision

    # ------------------------------------------------------------------
    # The shared clock
    # ------------------------------------------------------------------
    def toggle_clock(self) -> None:
        """Start or stop the room's shared bar and section count."""

        self._sync_workbench()
        self._sync_clock_to_shared_track()
        clock = self.workbench.clock
        if clock.snapshot().follows_shared_track:
            self._flash(
                "The Shared Track is the clock while it holds a song. Use its "
                "own transport.",
                ms=7000,
            )
            return
        if clock.snapshot().running:
            clock.stop()
            self._stop_ticking()
            self._flash("Song clock stopped.")
        elif clock.start():
            self._start_ticking()
            self._flash(
                "Song clock running. It counts from your tempo; it does not "
                "follow the band."
            )
        else:
            self._flash(
                "Write a tempo and one section header, then start the clock.",
                ms=7000,
            )
        self.workbench.clock_publisher.publish(force=True)
        self.refresh()

    def locate_section(self, section: str) -> None:
        """Move the clock to the top of a chosen part."""

        name = str(section or "")
        if not name:
            self.workbench.clock.stop()
        elif not self.workbench.clock.locate_section(name):
            self._flash(f"{name} is not in this song's form.")
            return
        self.workbench.clock_publisher.publish(force=True)
        self.refresh()

    def _start_ticking(self) -> None:
        if self._tick_timer is None:
            from PySide6.QtCore import QObject, QTimer

            # Parented to the panel, whose lifetime the repaint follows. The
            # clock itself keeps counting regardless; only the redraw stops.
            overlay = self.overlay
            parent = overlay if isinstance(overlay, QObject) else None
            timer = QTimer(parent)
            timer.setInterval(250)
            timer.timeout.connect(self._on_tick)
            self._tick_timer = timer
        self._tick_timer.start()

    def _stop_ticking(self) -> None:
        if self._tick_timer is not None:
            self._tick_timer.stop()

    def _on_tick(self) -> None:
        """Repaint the position and publish it. Never takes focus."""

        overlay = self.overlay
        self._sync_clock_to_shared_track()
        snapshot = self.workbench.clock_publisher.publish()
        # The strip keeps reporting whether or not the panel is open, so a
        # closed panel still shows the part you are on and the job you started.
        self._render_song_line()
        if overlay is not None and overlay.isVisible():
            overlay.set_clock(snapshot)
        if (
            not snapshot.running
            and not self._running_verb
            and not snapshot.follows_shared_track
        ):
            self._stop_ticking()

    def _on_panel_closed(self) -> None:
        # The clock belongs to the room, not the panel, so closing the panel
        # stops the repaint but never the count -- and the strip keeps showing
        # whatever the musician turned on.
        self._render_song_line()
        if not self.workbench.clock.snapshot().running:
            self._stop_ticking()

    def _render_song_line(self) -> None:
        """Put the song's one quiet line on the strip, or clear it.

        Priority is what a musician needs mid-take: a job they started, then
        where the song is, then nothing. Never a control, never a spinner.
        """

        strip = getattr(self._c.window, "session_strip", None)
        setter = getattr(strip, "set_song_line", None)
        if setter is None:
            return
        if not self.is_available():
            setter("")
            return

        if self._running_verb:
            label = self._verb_label(self._running_verb)
            setter(
                f"{label}…",
                description=(
                    f"{label} is running on a file you chose. The jam is not "
                    "affected and nothing is uploaded from the live mix."
                ),
            )
            return

        if self._unfinished_job:
            setter(
                f"{self._unfinished_job} — still at Music AI",
                description=(
                    f"WebJam stopped waiting for {self._unfinished_job}, but "
                    "Music AI is still working on it. Nothing was cancelled "
                    "and nothing was restarted. Check the Music AI dashboard."
                ),
            )
            return

        snapshot = self.workbench.clock_snapshot()
        if snapshot.running and snapshot.position_label:
            chords = " ".join(snapshot.chords_now[:4])
            line = snapshot.position_label
            setter(
                f"{line} · {chords}" if chords else line,
                description=(
                    "Where the song is. "
                    + (
                        "Counting with the Shared Track."
                        if snapshot.follows_shared_track
                        else "A shared reference the host runs; it does not "
                        "follow the band."
                    )
                ),
            )
            return

        setter("")

    def _verb_label(self, verb_key: str) -> str:
        capability = (
            self._catalog.capability(verb_key) if self._catalog is not None else None
        )
        return capability.label if capability is not None else "Song tools"

    # ------------------------------------------------------------------
    # Stems beside the jam
    # ------------------------------------------------------------------
    def toggle_stem_mute(self, name: str) -> None:
        self.workbench.stem_bench.toggle_mute(str(name or ""))
        self._render_stems()

    def toggle_stem_solo(self, name: str) -> None:
        self.workbench.stem_bench.toggle_solo(str(name or ""))
        self._render_stems()

    def sing_this_one(self) -> None:
        """Mute the record's vocal so the room sings it."""

        if not self.workbench.stem_bench.sing_this_one():
            self._flash(
                "These stems have no separate vocal to mute.",
                ms=7000,
            )
            return
        self._render_stems()
        self._flash("Vocal muted. Sing it.")

    def send_stems_to_jam(self) -> None:
        """Route what you can hear into the jam through the Shared Track."""

        if not self._is_host():
            self._flash("Only the host can send a track into the jam.")
            return
        bench = self.workbench.stem_bench
        path, note = bench.shared_track_plan()
        if path:
            self._load_shared_track(path, note)
            return
        mix = bench.mix()
        if len(mix.audible) < 2:
            self._flash(note, ms=8000)
            return

        destination = self._results_directory() / bench.bounce_name()
        audible = list(mix.audible)
        self._flash("Mixing stems for the jam…")

        def work() -> None:
            try:
                mixed = bounce_stems(audible, destination)
            except StemBenchError as exc:
                message = str(exc)
                self._on_ui(lambda: self._flash(message, ms=9000))
                return
            except Exception:  # noqa: BLE001 - a worker must never escape
                LOGGER.warning("Stem bounce failed", exc_info=True)
                self._on_ui(
                    lambda: self._flash("WebJam could not mix those stems.", ms=9000)
                )
                return
            self._on_ui(
                lambda: self._load_shared_track(
                    mixed, f"Sending {len(audible)} stems."
                )
            )

        threading.Thread(target=work, daemon=True, name="stem-bounce").start()

    def _load_shared_track(self, path: str, note: str) -> None:
        loader = getattr(self._c, "_load_reference_track", None)
        if loader is None:
            self._flash("Shared Track is unavailable in this session.")
            return
        try:
            loader(path)
        except Exception:  # noqa: BLE001 - a failed load must not end the jam
            LOGGER.warning("Sending stems to the jam failed", exc_info=True)
            self._flash("WebJam could not load that into the jam.", ms=9000)
            return
        self._flash(f"{note} Playback stays under host control.", ms=8000)

    def _render_stems(self) -> None:
        overlay = self.overlay
        if overlay is None:
            return
        bench = self.workbench.stem_bench
        path, note = bench.shared_track_plan()
        can_send = bool(path) or len(bench.mix().audible) > 1
        overlay.set_stems(
            stems=bench.stems,
            mix=bench.mix() if bench.loaded else None,
            note="" if not bench.loaded else note,
            can_send=can_send and self._is_host(),
        )

    def show_writing_help(self) -> None:
        """Answer "help me write" from the room's own song. Nothing leaves."""

        overlay = self.overlay
        if overlay is None:
            return
        self._sync_workbench()
        overlay.set_sections(
            self.workbench.section_names(),
            current=self.workbench.clock_snapshot().section_label,
        )
        overlay.set_song_state(
            catch_up=None,
            form_summary=self.workbench.conductor_line(),
            form_rows=self.workbench.form_overlay(),
            clock=self.workbench.clock_snapshot(),
            advice=self.workbench.writing_advice(),
            results=tuple(run.summary_line() for run in self.workbench.runs[-2:]),
            sheet_shareable=bool(self.workbench.shareable_sheet()),
        )

    def show_chords(self, section: str = "") -> None:
        """Suggest changes for one part of the song, in the context around it.

        A named part is treated as a region to rewrite, and the moves that
        could follow what it already plays are offered alongside. With no
        selection, the next part the song is missing is answered instead.
        """

        overlay = self.overlay
        if overlay is None:
            return
        self._sync_workbench()
        selection = str(section or "")
        advice = self.workbench.chord_advice(section_name=selection)
        # Retained so a companion surface can show the same suggestion the
        # panel is showing, rather than asking for its own.
        self._last_suggestion = (
            advice.suggestions[0] if advice.available else None
        )
        self._last_suggestion_section = advice.section_label
        overlay.set_song_state(
            catch_up=None,
            form_summary=self.workbench.conductor_line(),
            form_rows=self.workbench.form_overlay(),
            clock=self.workbench.clock_snapshot(),
            chords=advice,
            next_chords=(
                self.workbench.next_chord_advice(section_name=selection)
                if selection
                else None
            ),
            results=tuple(run.summary_line() for run in self.workbench.runs[-2:]),
            sheet_shareable=bool(self.workbench.shareable_sheet()),
        )

    def keep_suggestion(self, section_label: str, chord_line: str) -> None:
        """Write one accepted suggestion into the song sheet, on request.

        Nothing reaches the notes until this runs, and it only ever touches
        the musician's own notes -- the Studio arrangement stays theirs.
        """

        canvas = getattr(self._c.window, "session_canvas", None)
        writer = getattr(canvas, "set_notes", None)
        if writer is None:
            return
        self._sync_workbench()
        chords = tuple(str(chord_line or "").split())
        updated = self.workbench.keep_progression(
            section_label=str(section_label or ""), chords=chords
        )
        if updated == self.workbench._notes:
            self._flash("Nothing to keep.")
            return
        writer(updated)
        self.workbench.set_notes(updated)
        overlay = self.overlay
        if overlay is not None:
            overlay.clear_suggestions()
        self._flash(f"Kept {chord_line} under {section_label}.")
        self.refresh()

    def dismiss_suggestions(self) -> None:
        """Clear the suggestions on screen. Nothing was written, so nothing undoes."""

        self._last_suggestion = None
        self._last_suggestion_section = ""
        overlay = self.overlay
        if overlay is not None:
            overlay.clear_suggestions()

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

    def run_song_tool(self, verb_key: str, *, from_companion: bool = False) -> None:
        """Run one Music AI verb on a file the host picks and confirms.

        ``from_companion`` means the request arrived from outside the desktop
        window. A file picker there would be a dialog nobody asked for in front
        of a musician who is looking at something else, so a companion request
        acts only on the Shared Track the host already chose and is refused
        otherwise.
        """

        key = str(verb_key or "")
        # Song tools exist where a song form does. Every caller checks this
        # already, which is exactly why it is checked here too: the gate
        # belongs with the thing it guards, not with each way in.
        if not self.is_available():
            self._flash("Song tools are part of a Music session.", ms=6000)
            return
        if self._running_verb:
            self._flash("One Song tool is already running.")
            return
        capability = (
            self._catalog.capability(key) if self._catalog is not None else None
        )
        api_key = self._api_key()

        # Refuse on everything that does not need a file before opening a
        # dialog. A guest, or a session with no key, should never be shown a
        # picker only to be told no once they have chosen something.
        precheck = evaluate_upload_preconditions(
            capability=capability,
            is_host=self._is_host(),
            has_api_key=bool(api_key),
            budget=self._budget,
            now=time.monotonic(),
        )
        if precheck.blocked:
            self._flash(precheck.reason, ms=9000)
            return

        source_kind, path = self._choose_source(
            capability, from_companion=from_companion
        )
        if not path:
            if from_companion:
                self._flash(
                    "Load a Shared Track on the desktop first. WebJam never "
                    "uploads the live jam.",
                    ms=8000,
                )
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
        self._unfinished_job = ""
        # Counted at the moment a job actually starts, not when one is asked
        # for, so a refused attempt costs the room nothing.
        self._budget.record(time.monotonic())
        self._cancelled.clear()
        overlay = self.overlay
        if overlay is not None:
            overlay.set_busy(key, f"Running {capability.label}…")
        self._render_song_line()
        self._flash(f"{capability.label} started. The jam keeps running.")

        results_dir = self._results_directory()
        session_name = self._session_title()
        self._start_ticking()

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
                # A timeout means WebJam stopped waiting, not that Music AI
                # stopped working. Sleeping a laptop mid-job lands here, and
                # restarting it would spend the account's credits twice.
                left_running = getattr(exc, "code", "") == "TIMEOUT"
                label = capability.label
                self._on_ui(
                    lambda: self._finish_tool(
                        None,
                        message,
                        unfinished=label if left_running else "",
                    )
                )
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

    def _finish_tool(self, run: Any, error: str, *, unfinished: str = "") -> None:
        self._running_verb = ""
        self._unfinished_job = str(unfinished or "")
        self._render_song_line()
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

    def _choose_source(
        self,
        capability: Any,
        *,
        from_companion: bool = False,
    ) -> tuple[str, str]:
        """Return the file the host chose. Never discovers one on its own."""

        if capability is None or not getattr(capability, "supported", False):
            # Let evaluate_upload own the refusal copy so there is one place
            # that decides, rather than two that can disagree.
            return SOURCE_PICKED_FILE, "unavailable"

        # One dialog, not two. When the session already holds a Shared Track
        # that is the obvious subject, and the confirmation names it, so an
        # extra "which file?" box would only add a modal over the jam.
        shared = self._shared_track_path()
        if shared:
            return SOURCE_SHARED_TRACK, shared
        if from_companion:
            # No picker for a request made from outside this window.
            return SOURCE_SHARED_TRACK, ""

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

    def _shared_track_snapshot(self):
        """The host's Shared Track truth, host-local or guest projection."""

        strip = getattr(self._c.window, "session_strip", None)
        return getattr(strip, "_shared_track_last_snapshot", None)

    def _shared_track_view(self) -> SharedTrackView:
        snapshot = self._shared_track_snapshot()
        if snapshot is None:
            return SharedTrackView()
        raw_state = getattr(snapshot, "state", "")
        state = str(getattr(raw_state, "value", raw_state) or "").lower()
        name = str(getattr(snapshot, "source_name", "") or "") or str(
            getattr(snapshot, "source_display_name", "") or ""
        )
        return SharedTrackView(
            loaded=bool(name),
            playing="play" in state,
            source_name=name,
            position_s=float(getattr(snapshot, "position_s", 0.0) or 0.0),
            duration_s=float(getattr(snapshot, "duration_s", 0.0) or 0.0),
            host_controlled=True,
            count_in=bool(getattr(snapshot, "count_in_active", False)),
        )

    def _sync_clock_to_shared_track(self) -> None:
        """Hand the bar count to the Shared Track whenever it holds a song.

        Shared Track is the session's clock for audio. Counting separately
        while it plays would put two positions on one screen.
        """

        view = self._shared_track_view()
        # The count-in is the click before bar one. Holding through it keeps
        # the overlay on the downbeat everyone is counting toward.
        self.workbench.clock.follow_shared_track(
            loaded=view.loaded,
            position_s=view.position_s,
            playing=view.carries_the_form,
        )
        # A musician who joined late is watching a song that is already
        # moving. Nobody pressed start here, so nothing would repaint their
        # overlay unless the Shared Track itself starts the tick.
        if view.carries_the_form:
            self._start_ticking()

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

    def _meeting_url(self) -> str:
        """The saved meeting link, whichever service it points at."""

        return str(getattr(self._c.settings, "webex_url", "") or "").strip()

    def _meeting_configured(self) -> bool:
        return bool(self._meeting_url())

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
