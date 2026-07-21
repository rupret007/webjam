from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

from core.musician_guidance import GuidanceState, StudioGuidanceFacts
from core.session_conductor import (
    EvidenceState,
    ExportState,
    GuestMediaState,
    SessionConductorPhase,
    SessionPrimaryAction,
)
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
        assert guidance.primary_action is SessionPrimaryAction.NONE
        assert guidance.action_label == "New invite needed"
        assert window.session_hud._status.text() == guidance.title
        assert window.participant_grid._empty_title.text() == guidance.title
        assert window.session_canvas._guidance_status.text() == guidance.title
    finally:
        controller._startup_attempt = None
        _close(controller, window, old_home)


def test_guest_media_mapping_uses_only_bounded_transfer_facts(tmp_path):
    controller, window, old_home = _controller(tmp_path)
    source = tmp_path / "preserved.wav"
    source.write_bytes(b"RIFF")
    segment = SimpleNamespace(source=source, status="pending")
    guest = SimpleNamespace(
        active_take_id="",
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

        guest.active_take_id = ""
        segment.status = "verified"
        assert controller._guest_media_state() == (
            GuestMediaState.VERIFIED,
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
        controller._publish_musician_guidance(snapshot)
        controller._publish_musician_guidance(snapshot)
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
