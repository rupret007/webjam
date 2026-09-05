from __future__ import annotations

import os
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

from core.musician_guidance import GuidanceRecovery, GuidanceState, StudioGuidanceFacts
from core.session_conductor import (
    CleanupState,
    EvidenceState,
    ExportState,
    GuestMediaState,
    MusicPathState,
    ProcessState,
    SessionConductorPhase,
    SessionPrimaryAction,
)
from core.session_lifecycle import SessionLifecyclePhase
from core.settings import AppSettings
from webjam_qt.controllers.application_controller import ApplicationController
from webjam_qt.theme import load_stylesheet
from webjam_qt.widgets.recording_studio import RecordingStudio
from webjam_qt.windows.conductor_window import ConductorWindow


APP = QApplication.instance() or QApplication([])


def _controller(tmp_path: Path, **settings_values):
    old_home = os.environ.get("HOME")
    os.environ["HOME"] = str(tmp_path)
    window = ConductorWindow(
        mode_entries=ApplicationController.mode_entries(),
        initial_mode_key="music_jam",
        initial_title="Unified guidance",
    )
    settings = AppSettings(**settings_values)
    with mock.patch.object(ApplicationController, "_start_routing_scan"):
        controller = ApplicationController(window, settings=settings)
    return controller, window, old_home


def _close(controller, window, old_home):
    controller.shutdown()
    window.close()
    if old_home is None:
        os.environ.pop("HOME", None)
    else:
        os.environ["HOME"] = old_home


def test_one_snapshot_drives_hud_stage_canvas_studio_and_public_surfaces(tmp_path):
    controller, window, old_home = _controller(tmp_path)
    try:
        guidance = controller._last_musician_guidance
        assert guidance is not None
        assert window.session_hud._status.text() == guidance.title
        assert window.participant_grid._empty_title.text() == guidance.title
        assert window.session_canvas._guidance_status.text() == guidance.title
        assert guidance.title in window.recording_studio.accessibleDescription()

        public = guidance.to_public_dict()
        assert controller._companion_get_diagnostics()["musician_guidance"] == public
        report = controller._diagnostics_exporter().artifact().structured_report
        bundled = report["session"]["guidance"]
        assert bundled == {
            key: value for key, value in public.items() if value not in ([], {})
        }
    finally:
        _close(controller, window, old_home)


def test_creative_note_cannot_change_operational_phase_or_action(tmp_path):
    controller, window, old_home = _controller(tmp_path)
    try:
        before = controller._last_musician_guidance
        window.session_canvas.set_notes(
            "Decision: recording finished\nAction: export the final album"
        )
        controller._refresh_session_pulse()
        after = controller._last_musician_guidance

        assert after.phase is before.phase
        assert after.primary_action is before.primary_action
        assert after.output("take").state is GuidanceState.NOT_STARTED
        assert after.creative is not None
        assert after.creative.decisions == ("recording finished",)
        assert "recording finished" not in str(after.to_public_dict())
    finally:
        _close(controller, window, old_home)


def test_studio_review_uses_studio_owned_action_and_truthful_outputs(
    tmp_path, monkeypatch
):
    controller, window, old_home = _controller(tmp_path)
    studio = window.recording_studio
    try:
        monkeypatch.setattr(
            "webjam_qt.platform_permissions.microphone_permission_status",
            lambda: "denied",
        )
        facts = StudioGuidanceFacts(
            take_selected=True,
            take_validated=True,
            arrangement_available=True,
            can_export=True,
        )
        monkeypatch.setattr(studio, "guidance_facts", lambda: facts)
        studio._viewing_live = False
        studio._studio_state = SimpleNamespace()
        controller._conductor_studio_reviewing = True
        controller._update_session_hud()

        guidance = controller._last_musician_guidance
        assert guidance.phase is SessionConductorPhase.REVIEWING
        assert guidance.primary_action is SessionPrimaryAction.EXPORT_TRACKS
        assert guidance.output("studio").state is GuidanceState.ACTIVE
        assert "Verified take" in guidance.output("studio").detail
        assert window.session_hud._action.isHidden()
        assert "Export Tracks" in window.session_canvas._guidance_next.text()
        assert "Non-destructive" in studio._phase.text()

        facts = StudioGuidanceFacts(
            take_selected=True,
            take_validated=True,
            arrangement_available=True,
            dirty=True,
            can_export=True,
        )
        controller._update_session_hud()
        guidance = controller._last_musician_guidance
        assert guidance.primary_action is SessionPrimaryAction.WAIT
        assert guidance.output("studio").state is GuidanceState.WORKING

        facts = StudioGuidanceFacts(
            take_selected=True,
            take_validated=True,
            arrangement_available=True,
            dirty=True,
            save_failed=True,
            can_export=False,
        )
        controller._update_session_hud()
        guidance = controller._last_musician_guidance
        assert guidance.primary_action is SessionPrimaryAction.OPEN_DETAILS
        assert guidance.output("studio").state is GuidanceState.NEEDS_ATTENTION

        controller._conductor_export = ExportState.COMPLETE
        controller._last_studio_guidance_facts = facts
        facts = StudioGuidanceFacts(
            take_revision=facts.take_revision + 1,
            take_selected=True,
            take_validated=True,
            arrangement_available=True,
            can_export=True,
        )
        controller._on_studio_guidance_changed()
        assert controller._conductor_export is ExportState.IDLE
        assert (
            controller._last_musician_guidance.output("export").state
            is GuidanceState.NOT_STARTED
        )
    finally:
        _close(controller, window, old_home)


def test_native_setup_and_exceptional_recovery_share_the_same_copy(tmp_path):
    controller, window, old_home = _controller(tmp_path)
    try:
        controller._conductor_setup_requested = True
        token = controller._start_session_conductor_attempt("guest")
        controller._startup_attempt = {
            "generation": 1,
            "role": "guest",
            "phase": "native_sound_setup",
            "conductor_token": token,
        }
        controller._render_startup_journey()
        guidance = controller._last_musician_guidance
        assert guidance.primary_action is SessionPrimaryAction.OPEN_AUDIO_SETTINGS
        assert guidance.next_step == "Bring Jamulus Forward"
        assert window.session_hud._status.text() == guidance.title
        assert window.participant_grid._empty_title.text() == guidance.title
        assert window.session_canvas._guidance_status.text() == guidance.title

        controller._startup_attempt = None
        controller._remote_invitation_requires_replacement = True
        controller._update_session_hud()
        guidance = controller._last_musician_guidance
        assert guidance.primary_action is SessionPrimaryAction.PASTE_NEW_INVITE
        assert guidance.primary_enabled
        assert guidance.action_label == guidance.next_step == "Paste New Invite"
        assert guidance.recovery is GuidanceRecovery.REPLACE_INVITE
        assert "Next: Paste New Invite" in guidance.accessible_description
        assert "Paste New Invite" in window.session_canvas._guidance_next.text()
        assert controller._conductor_action_kind(guidance.primary_action) == "paste_invite"
        assert guidance.to_public_dict()["primary_action"] == "paste_new_invite"
        controller._refresh_pocket_projection()
        pocket = controller._get_pocket_projection()
        assert pocket.primary_action is SessionPrimaryAction.PASTE_NEW_INVITE
        assert pocket.primary_enabled == guidance.primary_enabled
        assert pocket.cue == guidance.title
        controller._refresh_pocket_projection()
        assert controller._get_pocket_projection().revision == pocket.revision
        with mock.patch.object(controller, "_paste_new_invitation") as paste:
            controller._on_conductor_action_requested(
                controller._conductor_action_kind(guidance.primary_action)
            )
        paste.assert_called_once_with()
        assert window.session_hud._status.text() == guidance.title
        assert window.participant_grid._empty_title.text() == guidance.title
        assert window.session_canvas._guidance_status.text() == guidance.title
    finally:
        controller._startup_attempt = None
        _close(controller, window, old_home)


def test_new_guest_startup_cannot_terminalize_on_previous_stopped_state(tmp_path):
    """The generation-bound attempt outranks the prior session's clean Stop."""

    controller, window, old_home = _controller(
        tmp_path,
        host_server_enabled=False,
        jamulus_server="192.168.1.42",
    )
    try:
        controller.bridge.jamulus_state = "Stopped"
        controller.bridge.jamulus_launch_intended = False
        controller._conductor_setup_requested = True
        token = controller._start_session_conductor_attempt("guest")
        controller._startup_attempt = {
            "generation": 2,
            "role": "guest",
            "phase": "launching_client",
            "conductor_token": token,
            "cancel_event": threading.Event(),
        }

        controller._render_startup_journey()

        snapshot = controller.session_conductor.snapshot
        assert snapshot.token == token
        assert snapshot.facts.music_path is MusicPathState.STARTING
        assert snapshot.presentation.phase is SessionConductorPhase.JOINING
        assert snapshot.presentation.phase is not SessionConductorPhase.FAILED

        controller.audio.connected = True
        controller.bridge.jamulus_state = "Running"
        controller._render_startup_journey()

        authenticated = controller.session_conductor.snapshot
        assert authenticated.token == token
        assert authenticated.facts.music_path is MusicPathState.AUTHENTICATED
        assert authenticated.presentation.phase is not SessionConductorPhase.FAILED
    finally:
        controller.audio.connected = False
        controller._startup_attempt = None
        _close(controller, window, old_home)


def test_new_host_startup_marks_prelaunch_server_and_music_as_starting(tmp_path):
    """The first host render is truthful before its server worker is queued."""

    controller, window, old_home = _controller(
        tmp_path,
        host_server_enabled=True,
        jamulus_server="127.0.0.1",
    )
    try:
        controller.bridge.jamulus_state = "Stopped"
        controller.bridge.jamulus_launch_intended = False
        controller.bridge.hosted_server_alive = mock.Mock(return_value=False)
        controller._conductor_setup_requested = True
        token = controller._start_session_conductor_attempt("host")
        controller._startup_attempt = {
            "generation": 2,
            "role": "host",
            "phase": "starting_server",
            "conductor_token": token,
            "cancel_event": threading.Event(),
        }

        controller._render_startup_journey()

        snapshot = controller.session_conductor.snapshot
        assert snapshot.token == token
        assert snapshot.facts.music_path is MusicPathState.STARTING
        assert snapshot.facts.host_server_process is ProcessState.STARTING
        assert snapshot.presentation.phase is SessionConductorPhase.STARTING_HOST
        assert snapshot.presentation.phase is not SessionConductorPhase.FAILED

        controller.bridge.hosted_server_alive.return_value = True
        controller.audio.connected = True
        controller.bridge.jamulus_state = "Running"
        controller._render_startup_journey()

        authenticated = controller.session_conductor.snapshot
        assert authenticated.token == token
        assert authenticated.facts.music_path is MusicPathState.AUTHENTICATED
        assert authenticated.facts.host_server_process is ProcessState.RUNNING
        assert authenticated.presentation.phase is not SessionConductorPhase.FAILED
    finally:
        controller.audio.connected = False
        controller.bridge.hosted_server_alive.return_value = False
        controller._startup_attempt = None
        _close(controller, window, old_home)


def test_failed_native_attempt_still_exposes_terminal_stopped_state(tmp_path):
    """Only active startup phases may mask the bridge's terminal evidence."""

    controller, window, old_home = _controller(
        tmp_path,
        host_server_enabled=False,
        jamulus_server="192.168.1.42",
    )
    try:
        controller.bridge.jamulus_state = "Stopped"
        controller.bridge.jamulus_launch_intended = False
        controller._conductor_setup_requested = True
        token = controller._start_session_conductor_attempt("guest")
        controller._startup_attempt = {
            "generation": 2,
            "role": "guest",
            "phase": "failed",
            "conductor_token": token,
            "cancel_event": threading.Event(),
        }
        controller._transition_lifecycle(
            SessionLifecyclePhase.FAILED_RECOVERABLE,
            "Native startup failed",
        )

        controller._render_startup_journey()

        snapshot = controller.session_conductor.snapshot
        assert snapshot.facts.music_path is MusicPathState.FAILED
        assert snapshot.presentation.phase is SessionConductorPhase.FAILED
        assert snapshot.presentation.retry_safe is True
    finally:
        controller._startup_attempt = None
        _close(controller, window, old_home)


def test_stale_or_cancelled_startup_cannot_mask_terminal_bridge_truth(tmp_path):
    controller, window, old_home = _controller(
        tmp_path,
        host_server_enabled=False,
        jamulus_server="192.168.1.42",
    )
    try:
        controller.bridge.jamulus_state = "Stopped"
        controller.bridge.jamulus_launch_intended = False
        controller._conductor_setup_requested = True
        stale_token = controller._start_session_conductor_attempt("guest")
        controller.session_conductor.reset_to_idle("guest")
        controller._start_session_conductor_attempt("guest")
        controller._startup_attempt = {
            "generation": 2,
            "role": "guest",
            "phase": "launching_client",
            "conductor_token": stale_token,
            "cancel_event": threading.Event(),
        }
        assert controller._session_conductor_facts().music_path is MusicPathState.FAILED

        current_token = controller.session_conductor.token
        cancelled = threading.Event()
        cancelled.set()
        controller._startup_attempt = {
            "generation": 3,
            "role": "guest",
            "phase": "native_sound_setup",
            "conductor_token": current_token,
            "cancel_event": cancelled,
        }
        assert controller._session_conductor_facts().music_path is MusicPathState.FAILED
    finally:
        controller._startup_attempt = None
        _close(controller, window, old_home)


def test_cancelling_startup_with_cleanup_underway_remains_ending(tmp_path):
    controller, window, old_home = _controller(
        tmp_path,
        host_server_enabled=False,
        jamulus_server="192.168.1.42",
    )
    try:
        controller.bridge.jamulus_state = "Stopped"
        controller.bridge.jamulus_launch_intended = False
        controller._conductor_setup_requested = True
        token = controller._start_session_conductor_attempt("guest")
        cancelled = threading.Event()
        cancelled.set()
        controller._startup_attempt = {
            "generation": 2,
            "role": "guest",
            "phase": "cancelling",
            "conductor_token": token,
            "cancel_event": cancelled,
        }
        controller.audio.stopping = True

        controller._render_startup_journey()

        snapshot = controller.session_conductor.snapshot
        assert snapshot.facts.cleanup is CleanupState.ENDING
        assert snapshot.presentation.phase is SessionConductorPhase.ENDING
    finally:
        controller.audio.stopping = False
        controller._startup_attempt = None
        _close(controller, window, old_home)


def test_guest_media_mapping_uses_only_bounded_transfer_facts(tmp_path):
    controller, window, old_home = _controller(tmp_path)
    source = tmp_path / "preserved.wav"
    source.write_bytes(b"RIFF")
    segment = SimpleNamespace(source=source, status="pending")
    guest = SimpleNamespace(
        active_take_id="",
        capture_finalization_needs_attention=False,
        recovered_captures=(),
        pending_segments=(segment,),
        stop=lambda: None,
    )
    try:
        controller.settings.local_capture_enabled = True
        controller.guest_peer = guest
        assert controller._guest_media_state() == (
            GuestMediaState.TRANSFERRING,
            EvidenceState.VERIFIED,
        )

        guest.active_take_id = "private-take-id"
        assert controller._guest_media_state() == (
            GuestMediaState.WAITING,
            EvidenceState.IN_PROGRESS,
        )

        guest.capture_finalization_needs_attention = True
        assert controller._guest_media_state() == (
            GuestMediaState.NEEDS_ATTENTION,
            EvidenceState.UNKNOWN,
        )
        controller.settings.local_capture_enabled = False
        assert controller._guest_media_state() == (
            GuestMediaState.NEEDS_ATTENTION,
            EvidenceState.UNKNOWN,
        )
        controller.settings.local_capture_enabled = True
        guest.capture_finalization_needs_attention = False

        guest.active_take_id = ""
        segment.status = "verified"
        assert controller._guest_media_state() == (
            GuestMediaState.VERIFIED,
            EvidenceState.VERIFIED,
        )

        segment.status = "recovery_only"
        assert controller._guest_media_state() == (
            GuestMediaState.NEEDS_ATTENTION,
            EvidenceState.VERIFIED,
        )

        segment.status = "missing_local_original"
        source.unlink()
        assert controller._guest_media_state() == (
            GuestMediaState.NEEDS_ATTENTION,
            EvidenceState.FAILED,
        )
    finally:
        controller.guest_peer = None
        _close(controller, window, old_home)


def test_studio_guidance_distinguishes_warning_from_persistence_failure():
    studio = RecordingStudio()
    try:
        studio._viewing_live = False
        studio._current = SimpleNamespace(
            validation_status="complete",
            manifest_errors=(),
            manifest_warnings=("Review the recovered tail",),
            review_only=False,
            export_block_reason="",
        )
        studio._studio_state = SimpleNamespace()
        studio._studio_state_dirty = False
        studio._studio_state_error = "Saved choices were recovered safely."
        studio._studio_persistence_failed = False
        studio._can_export_current_take = lambda: True

        facts = studio.guidance_facts()
        assert facts.take_validated
        assert not facts.take_needs_attention
        assert facts.can_export
        assert not facts.save_failed

        studio._studio_persistence_failed = True
        assert studio.guidance_facts().save_failed
    finally:
        studio.shutdown()
        studio.close()


def test_guidance_is_idempotent_and_studio_tick_does_not_announce(tmp_path):
    controller, window, old_home = _controller(tmp_path)
    studio = window.recording_studio
    canvas = window.session_canvas
    deliveries = []
    announcements = []
    original = canvas.set_musician_guidance
    try:
        canvas.set_musician_guidance = lambda value: (
            deliveries.append(value),
            original(value),
        )[-1]
        studio.guidance_changed.connect(lambda: announcements.append("changed"))
        snapshot = controller._last_session_conductor_snapshot
        display_override = controller._last_guidance_display_override
        controller._publish_musician_guidance(
            snapshot,
            display_override=display_override,
        )
        controller._publish_musician_guidance(
            snapshot,
            display_override=display_override,
        )
        studio._tick()
        QCoreApplication.processEvents()

        assert deliveries == []
        assert announcements == []
    finally:
        _close(controller, window, old_home)


def test_canvas_keeps_notes_and_chat_usable_at_760_by_600(tmp_path):
    controller, window, old_home = _controller(tmp_path)
    try:
        window.setStyleSheet(load_stylesheet())
        window.resize(760, 600)
        window.show()
        controller._on_rail_view_changed("canvas")
        QCoreApplication.processEvents()

        canvas = window.session_canvas
        assert canvas.isVisibleTo(window)
        assert canvas._notes.height() >= 72
        assert canvas._chat_input.isVisibleTo(window)
        assert canvas.rect().contains(canvas._notes.geometry())
        assert canvas.rect().contains(canvas._chat_input.geometry())
        assert canvas._notes.geometry().bottom() < canvas._chat_input.geometry().top()
    finally:
        _close(controller, window, old_home)


def test_embedded_studio_keeps_guidance_and_primary_controls_visible_at_760_by_600(
    tmp_path,
):
    controller, window, old_home = _controller(tmp_path)
    try:
        window.setStyleSheet(load_stylesheet())
        window.resize(760, 600)
        window.show()
        controller._on_rail_view_changed("takes")
        QCoreApplication.processEvents()

        studio = window.recording_studio
        guidance = controller._last_musician_guidance
        assert studio.isVisibleTo(window)
        assert guidance is not None
        assert window.session_hud._status.text() == "No takes yet"
        assert window.session_hud._action.isHidden()
        assert guidance.title in studio.accessibleDescription()
        # Compact embedded Studio uses the shared HUD instead of repeating
        # the longer phase line inside the constrained workspace.
        assert not studio._phase.isVisibleTo(studio)
        assert studio._hint.isVisibleTo(studio)
        assert studio._record_btn.isVisibleTo(studio)
        assert not studio._hint.wordWrap()

        bounds = studio.contentsRect()
        for widget in (studio._hint, studio._record_btn):
            top_left = widget.mapTo(studio, widget.rect().topLeft())
            bottom_right = widget.mapTo(studio, widget.rect().bottomRight())
            assert bounds.contains(top_left)
            assert bounds.contains(bottom_right)
    finally:
        _close(controller, window, old_home)


def test_art_idle_guidance_supports_own_tools_without_recording_claim(tmp_path):
    controller, window, old_home = _controller(tmp_path, last_creator_profile_key="art")
    try:
        controller._conductor_setup_requested = False
        controller._render_session_conductor()
        hint = window.participant_grid._empty_hint.text()
        assert "paint, clay, paper or a printer" in hint
        assert "Conversation" in hint
        assert "recording" not in hint.casefold()
        assert controller.creator_start.talk_only
    finally:
        _close(controller, window, old_home)


def test_notes_autosave_failure_retries_without_changing_session_truth(tmp_path, monkeypatch):
    from PySide6.QtTest import QTest
    from webjam_qt.controllers import session_persistence as persistence_module

    monkeypatch.setattr(persistence_module, "_persistence_home", lambda: tmp_path)
    controller, window, old_home = _controller(tmp_path, last_creator_profile_key="art")
    canvas = window.session_canvas
    try:
        window.setStyleSheet(load_stylesheet())
        window.resize(760, 600)
        window.show()
        controller._on_rail_view_changed("canvas")
        before = controller._last_musician_guidance
        with mock.patch.object(persistence_module, "atomic_write_text", side_effect=OSError("private path must stay hidden")):
            canvas._notes.setPlainText("Try a larger clay form next time")
            QTest.qWait(850)
            assert controller._persistence.has_unsaved_notes
            assert canvas._notes_save_state == "failed"
            assert controller.shutdown() is False
            assert controller._shutdown is False
            assert controller._shutdown_cleanup_pending is False
            QCoreApplication.processEvents()
            assert canvas._notes.height() >= 72
            assert canvas._save_notes_button.isVisibleTo(window)
            assert "private path" not in canvas._notes_save_status.text()
            assert "private path" not in canvas._notes_save_status.accessibleDescription()
            assert canvas.current_notes() == "Try a larger clay form next time"
        canvas._save_notes_button.click()
        assert not controller._persistence.has_unsaved_notes
        assert (tmp_path / ".webjam_notes.art.md").read_text() == canvas.current_notes()
        assert canvas._save_notes_button.isHidden()
        after = controller._last_musician_guidance
        assert after.phase == before.phase
        assert after.primary_action == before.primary_action
        assert "clay form" not in str(after.to_public_dict())
    finally:
        _close(controller, window, old_home)


def test_kept_song_suggestion_is_autosaved_without_typing(tmp_path, monkeypatch):
    from PySide6.QtTest import QTest
    from webjam_qt.controllers import session_persistence as persistence_module
    from webjam_qt.controllers.song_tools_coordinator import SongToolsCoordinator

    monkeypatch.setattr(persistence_module, "_persistence_home", lambda: tmp_path)
    controller, window, old_home = _controller(tmp_path)
    try:
        window.session_canvas.set_notes("Key: A minor\nTempo: 104\n[Verse]\nAm F C G\n")
        coordinator = SongToolsCoordinator(controller)
        coordinator.keep_suggestion("Verse", "Am G F E")
        QTest.qWait(850)
        assert "Am G F E" in window.session_canvas.current_notes()
        assert (tmp_path / ".webjam_notes.md").read_text() == window.session_canvas.current_notes()
        assert not controller._persistence.has_unsaved_notes
    finally:
        _close(controller, window, old_home)


def test_unconfirmed_startup_cleanup_is_not_retryable_in_any_projection(tmp_path):
    from core.session_conductor import FailureDisposition
    controller, window, old_home = _controller(tmp_path)
    try:
        controller._startup_attempt = {"generation": 1, "role": "guest", "phase": "cancelling"}
        controller._fail_startup_journey(1, "cleanup_failed", retryable=False)
        guidance = controller._last_musician_guidance
        assert "Session needs attention" in guidance.to_markdown()
        assert "Safe retry available" not in guidance.to_markdown()
        assert "Safe retry available" not in window.session_canvas.accessibleDescription()
        assert "Session needs attention" in window.session_canvas._guidance_recent.text()
        assert "Safe retry available" not in window.session_canvas._guidance_recent.text()
        facts = controller._session_conductor_facts()
        assert facts.cleanup is CleanupState.FAILED
        assert facts.failure is FailureDisposition.BLOCKED
        assert facts.startup_cleanup_pending
        controller._render_startup_journey()
        snapshot = controller.session_conductor.snapshot
        assert snapshot.presentation.primary_action is SessionPrimaryAction.CLOSE_SETUP
        assert not snapshot.presentation.retry_safe
        assert controller._last_musician_guidance.primary_action is SessionPrimaryAction.CLOSE_SETUP
        controller._refresh_pocket_projection()
        assert controller._get_pocket_projection().primary_action is SessionPrimaryAction.CLOSE_SETUP
        assert "Try Again" not in window.session_canvas._guidance_next.text()
        controller._startup_attempt["phase"] = "cancelling"
        controller._render_startup_journey()
        assert controller.session_conductor.snapshot.presentation.primary_action is SessionPrimaryAction.WAIT
        controller._refresh_pocket_projection()
        assert controller._get_pocket_projection().primary_action is SessionPrimaryAction.WAIT
        assert not controller._get_pocket_projection().primary_enabled
    finally:
        controller._startup_attempt = None
        _close(controller, window, old_home)


def test_pocket_ignores_older_guidance_from_the_same_session_generation(tmp_path):
    from dataclasses import replace

    controller, window, old_home = _controller(tmp_path)
    try:
        controller._remote_invitation_requires_replacement = True
        controller._update_session_hud()
        guidance = controller._last_musician_guidance
        assert guidance.primary_action is SessionPrimaryAction.PASTE_NEW_INVITE
        old_snapshot = controller.session_conductor.snapshot
        assert controller.session_conductor.observe(
            old_snapshot.token,
            old_snapshot.revision + 1,
            replace(old_snapshot.facts, cleanup=CleanupState.ENDING),
        )
        current = controller.session_conductor.snapshot
        assert current.token.generation == guidance.generation
        assert current.revision > guidance.revision
        controller._refresh_pocket_projection()
        pocket = controller._get_pocket_projection()
        assert pocket.primary_action is SessionPrimaryAction.WAIT
        assert not pocket.primary_enabled
        assert pocket.cue == current.presentation.title
    finally:
        controller._remote_invitation_requires_replacement = False
        _close(controller, window, old_home)
