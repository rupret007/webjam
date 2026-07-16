"""
ApplicationController — the brain.

Owns session state and wires ConductorWindow signals to the service layer.

Participant lifecycle:
  1. Startup and connection transitions show explicit, actionable empty states.
  2. Only confirmed Jamulus participants appear as mixer cards.
  3. Real level values are polled every 100 ms after connection.

Mixer signals (fader/mute/solo) route directly to ``JamulusController`` which
sends them to Jamulus via JSON-RPC (preferred) or UDP protocol (fallback).
"""

from __future__ import annotations

import logging
import re
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, Qt, QTimer
from PySide6.QtWidgets import QMessageBox

from core.creative_modes import CREATIVE_MODES, get_mode_by_key_or_default
from core.network_invite import BandInvite
from core.remote_invitation import RemoteInvitation
from core.session_health import SessionHealth
from core.session_conductor import (
    CleanupState,
    EvidenceState,
    ExportState,
    FailureDisposition,
    GuestMediaState,
    MusicPathState,
    ProcessState,
    RecorderState,
    ReviewState,
    SessionConductorFacts,
    SessionConductorPhase,
    SessionPrimaryAction,
    SessionRole,
    TakeValidationState,
    derive_session_conductor,
)
from core.session_lifecycle import SessionLifecycle, SessionLifecyclePhase
from core.session_intelligence import build_session_pulse
from core.settings import AppSettings, load_settings
from core.session_transfer_runtime import (
    GuestPeerSession,
    HostPeerSession,
    default_installation_identity_path,
)
from services.bridge_service import BridgeService
from storage.repository import WebJamRepository
from ui.services import MetricsService

from webjam_qt.controllers.mix_manager import MixManager
from webjam_qt.controllers.session_persistence import SessionPersistence
from webjam_qt.controllers.ui_thread import UiThreadInvoker
from webjam_qt.controllers.audio_coordinator import AudioCoordinator
from webjam_qt.controllers.video_coordinator import VideoCoordinator
from webjam_qt.controllers.recording_coordinator import RecordingCoordinator
from webjam_qt.widgets.participant_card import ParticipantPresentation
from webjam_qt.windows.conductor_window import ConductorWindow
from webjam_qt.session_state import SessionPhase, SessionUiState

LOGGER = logging.getLogger("webjam.qt.application_controller")


class ApplicationController(QObject):
    """Glue layer between ConductorWindow and the service layer."""

    _LEVEL_POLL_MS = 100  # how often to push meter updates to the grid
    _METER_TICK_MS = 40  # global LevelMeter decay tick (was per-meter)
    _CONNECTION_TIMEOUT_MS = 30_000
    _WAKE_REVALIDATION_GAP_SECONDS = 12.0

    def __init__(
        self,
        window: ConductorWindow,
        settings: Optional[AppSettings] = None,
        session_invite: BandInvite | None = None,
        remote_invitation: RemoteInvitation | None = None,
        *,
        operator_mode: bool | None = None,
    ) -> None:
        super().__init__(window)
        self.window = window
        self.settings = settings or load_settings()
        self._operator_mode = bool(
            getattr(window, "operator_mode", False)
            if operator_mode is None
            else operator_mode
        )
        self._pilot_ledger = None
        self._pilot_run_state = "not_started"
        self._pilot_check_status: dict[str, str] = {}
        self._pilot_last_conductor_phase = ""
        self._test_night_dialog = None
        if session_invite is not None and remote_invitation is not None:
            raise ValueError("only one invitation may be active")
        if remote_invitation is not None and not isinstance(
            remote_invitation, RemoteInvitation
        ):
            raise TypeError("remote_invitation must be a RemoteInvitation")
        # Remote invitation material remains typed and memory-only.  A
        # dedicated session transport consumes it; it is never copied into
        # AppSettings, Jamulus arguments, logs, or the Session HUD.
        self._remote_invitation = remote_invitation
        self._remote_invitation_requires_replacement = False
        self._remote_session = None
        self._remote_invite_owner = None
        self._remote_host_preparing = False
        self._remote_route_base_settings: AppSettings | None = None
        self._remote_route_generation = 0
        # Saved Band Check verification covers stable local hardware only. A
        # v3 transport path is transient and must be acknowledged separately
        # for each prepared/connected generation and path.
        self._remote_band_check_token: tuple[str, int, str, str] | None = None
        self._remote_band_check_completed_token: tuple[str, int, str, str] | None = None
        # Any Band Check report is evidence for one concrete settings object.
        # Replacing that object invalidates every visible report and every
        # queued start signal that was produced from it.
        self._settings_generation = 0
        self.session_health = SessionHealth()
        self.session_lifecycle = SessionLifecycle(
            role=(
                "host"
                if bool(getattr(self.settings, "host_server_enabled", False))
                else "join"
            )
        )

        self._ui_invoker = UiThreadInvoker(self)

        self.repository = WebJamRepository()
        self.metrics = MetricsService(self.repository)

        from jamulus_controller import JamulusController
        from webex_integration import WebexController

        self.jamulus = JamulusController(
            host=self.settings.jamulus_server,
            port=self.settings.jamulus_port,
            rpc_port=self.settings.jamulus_rpc_port,
        )
        self.webex = WebexController(meeting_url=self.settings.webex_url)

        self._attach_jamulus_callbacks()
        self._server_recording = False
        # Last-known band-server recorder ARMED state (set by the Record
        # button worker; distinct from _server_recording, which is "actually
        # rolling right now" from recorderState notifications).
        self._recorder_armed = False

        self._shutdown = False
        # Compatibility state for AudioCoordinator while the nonexistent
        # Jamulus 3.12.2 live-send mute is retired. These values remain false;
        # no UI or reconnect path may promote them.
        self._talk_break_intended = False
        self._self_transmit_muted = False

        self.bridge = BridgeService(
            jamulus_controller=self.jamulus,
            webex_controller=self.webex,
            metrics_service=self.metrics,
            repository=self.repository,
            settings=self.settings,
            ui_callbacks={
                "set_status_banner": self._set_status_banner,
                "refresh_readiness": self._refresh_readiness,
                "show_actionable_error": self._show_actionable_error,
                "show_message": self._show_message,
                "shutdown_requested": lambda: self._shutdown,
                "schedule_ui_callback": self._ui_invoker.invoke,
                "retry_audio_launch": self.begin_startup_journey,
            },
        )

        # Participant map — keyed by channel_id
        self.participants: dict[int, ParticipantPresentation] = {}

        # True once JamulusController has pushed at least one real update
        self.audio = AudioCoordinator(self)
        self.video = VideoCoordinator(self)
        self.recording = RecordingCoordinator(self)

        # The peer plane is independent from Jamulus: it carries only durable
        # identity, confirmed recording state, and isolated local originals.
        # Host service binding is lazy until a real private Wi-Fi address is
        # known; joiners enroll in a worker so startup/UI never blocks on LAN.
        self._host_peer_warning = ""
        # A LAN invite is only useful while this Mac advertises the same
        # private address.  Keep this tiny, memory-only fact out of settings
        # and diagnostics: it exists solely to make a Wi-Fi/interface change
        # visible before a host sends an already-stale link.
        self._last_shared_lan_address = ""
        self.host_peer = HostPeerSession(
            on_take_updated=self._on_peer_take_updated,
        )
        # A v2 invite credential is intentionally memory-only for the active
        # join. A successful Leave/End clears it with the peer runtime, so a
        # stale bearer cannot silently reopen on a later Start.
        self._guest_invite = None
        self._guest_peer_configuration_failed = False
        self.guest_peer: GuestPeerSession | None = None
        if session_invite is not None and bool(
            getattr(session_invite, "peer_enabled", False)
        ):
            self._configure_guest_peer(session_invite)

        # Companion API — optional localhost HTTP bridge for DAWs/editors.
        # Constructed here (no side effects) but only started in
        # start_companion_api(), which the app bootstrap calls; this keeps
        # tests that build a controller directly from binding a real port.
        from api.local_bridge import LocalApiBridge

        self.api_bridge = LocalApiBridge(
            get_participants=self._companion_get_participants,
            get_diagnostics=self._companion_get_diagnostics,
            port=self.settings.companion_api_port,
        )

        # Latch so the "Jamulus disconnected" flash only fires once per crash
        self._reconnect_banner_shown = False
        # Latch so the 'gave up after 5 tries' message fires once.
        self._reconnect_gave_up = False
        # Latch for the "RPC hung" banner — fires once when activity stalls,
        # cleared when activity resumes.
        self._rpc_hang_banner_shown = False
        # If RPC is silent for this many seconds, we consider it hung.
        # Generous: poll cadence is 5s and SSE level events fire ~50ms,
        # so 15s is plenty of margin.
        self._RPC_HANG_THRESHOLD_S = 15.0
        # Mix-dirty tracking: True when fader/mute/solo state has changed
        # since the last successful save.  shutdown() auto-saves if True
        # AND _jamulus_connected (so we don't overwrite a real saved mix
        # with stale disconnected state).
        self._mix_dirty = False
        self._local_audio_seen = False
        self._remote_audio_seen = False
        # Only one saved-verification probe may be in flight.  Every user
        # path that starts production audio goes through that probe (or the
        # visible Band Check it opens); the raw toggle remains private to a
        # completed gate and the frozen-build smoke hook.
        self._band_check_start_pending = False
        # The conductor is a pure projection of authoritative subsystem facts.
        # These are the few controller-owned facts needed to bridge older
        # lifecycle callbacks into that projection; no rendered label or
        # provider detail is persisted here.
        self._conductor_setup_requested = bool(
            session_invite is not None or remote_invitation is not None
        )
        self._conductor_band_check = EvidenceState.NOT_STARTED
        self._conductor_had_authenticated_connection = False
        self._conductor_studio_reviewing = False
        self._conductor_export = ExportState.IDLE
        self._last_session_conductor = None
        # One role-aware, generation-scoped startup journey replaces the old
        # modal device picker + pre-session Band Check chain. Its durable
        # confirmation record is deliberately profile-hash-only: no invite,
        # Webex link, device identifier, or local path can enter it.
        self._startup_generation = 0
        self._startup_attempt: dict[str, object] | None = None
        self._startup_profile_plan = None
        from core.jamulus_profile import StartupAttemptStore, StartupReadinessStore

        self._startup_readiness_store = StartupReadinessStore()
        self._startup_attempt_store = StartupAttemptStore()
        # A prior incomplete journey may be resumed only after its dedicated
        # Jamulus profile is proven identical.  The record contains no invite,
        # Webex link, device choice, path, or raw diagnostic text.
        self._startup_recovery_record = self._startup_attempt_store.load()

        # Timers
        self._level_timer = QTimer(self)
        self._level_timer.setInterval(self._LEVEL_POLL_MS)
        self._level_timer.timeout.connect(self._poll_levels)

        # Auto-reconnect: poll BridgeService every 3 s to retry dropped services
        self._reconnect_timer = QTimer(self)
        self._reconnect_timer.setInterval(3_000)
        self._reconnect_timer.timeout.connect(self._on_reconnect_tick)
        self._reconnect_timer.start()
        self._last_reconnect_tick_monotonic = time.monotonic()

        # Single global LevelMeter decay tick — replaces N per-card timers.
        # See LevelMeter docstring; started in _bootstrap_ui, stopped in shutdown.
        self._meter_tick_timer = QTimer(self)
        self._meter_tick_timer.setInterval(self._METER_TICK_MS)
        self._meter_tick_timer.timeout.connect(
            self.window.participant_grid.tick_all_meters
        )

        # Retained as an inert compatibility attribute for extensions that
        # inspect controller timers. Native Webex launch uses no guest token.
        self._token_refresh_timer = QTimer(self)

        # Rebuilding the pulse scans the note text. Coalesce rapid typing
        # while retaining an immediate refresh before a brief is exported.
        self._pulse_refresh_timer = QTimer(self)
        self._pulse_refresh_timer.setSingleShot(True)
        self._pulse_refresh_timer.setInterval(200)
        self._pulse_refresh_timer.timeout.connect(self._refresh_session_pulse)

        self._connection_timer = QTimer(self)
        self._connection_timer.setSingleShot(True)
        self._connection_timer.setInterval(self._CONNECTION_TIMEOUT_MS)
        self._connection_timer.timeout.connect(self._on_connection_timeout)

        # Register real participant callback
        self.jamulus.register_callback(self._on_jamulus_participants)

        # Session metadata persistence (notes + title + mode)
        self._persistence = SessionPersistence(
            window.session_strip,
            window.session_canvas,
            logger=LOGGER,
        )

        # Mix save/load/restore (~/.webjam_mix.json).
        # Adapt flash_message's keyword-only ``ms=`` to MixManager's positional
        # ``(text, ms)`` callback contract so MixManager doesn't have to know
        # about ConductorWindow's signature quirks.
        self._mix_manager = MixManager(
            self.jamulus,
            lambda text, ms: self.window.flash_message(text, ms=ms),
            logger=LOGGER,
            metrics=self.metrics,
        )

        self._wire_signals()
        self._bootstrap_ui()
        self._start_routing_scan()
        # Give the visible shell one event-loop turn before surfacing any
        # interrupted local recording or private evidence checkpoint.  The
        # scan is bounded and never exposes a local path or journal payload.
        QTimer.singleShot(0, self.recording.recover_interrupted_recordings)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def shutdown(self) -> None:
        if self._shutdown:
            return  # closeEvent + app.py both call this; run teardown once
        # An unfinished Test Night record is durable.  Mark it paused before
        # the normal teardown begins so a restart never makes a physical
        # pilot look complete or silently discards its earlier evidence.
        self._pause_test_night()
        self._shutdown = True
        self._level_timer.stop()
        self._reconnect_timer.stop()
        self._meter_tick_timer.stop()
        self._token_refresh_timer.stop()
        self._pulse_refresh_timer.stop()
        self._connection_timer.stop()
        self.window.recording_studio.shutdown()
        # Quitting mid-recording must keep the audio, not discard it.
        self.recording.salvage_on_shutdown()
        self._save_notes()
        self._save_session_title()
        # Auto-save mix if user touched anything since last save AND we were
        # connected to a real Jamulus (don't overwrite a real saved mix with
        # disconnected data). Best-effort: failures are caught by _on_save_mix.
        if self._mix_dirty and self._jamulus_connected:
            try:
                self._on_save_mix()
            except Exception:  # noqa: BLE001
                LOGGER.exception("Auto-save mix on shutdown failed")
        # A hosted band server dies with WebJam: stop any recording cleanly
        # first so the server finalizes every musician's track, then
        # terminate the server itself. Ownership—not the latest role setting—
        # decides cleanup, so changing Host to Join can never leak a process.
        hosted_server_alive = self.bridge.hosted_server_alive()
        hosted_recording_safe = True
        if hosted_server_alive:
            try:
                if self.bridge.hosted_server_owned():
                    hosted_recording_safe = bool(
                        self.recording.stop_server_recording_for_shutdown()
                    )
            except Exception:  # noqa: BLE001
                LOGGER.exception("Hosted recording shutdown failed")
                hosted_recording_safe = False
        if hosted_server_alive and not hosted_recording_safe:
            LOGGER.critical(
                "Leaving hosted services running because recording finalization "
                "could not be confirmed"
            )
        # Terminate the Jamulus subprocess so it doesn't outlive WebJam.
        # bridge.stop_jamulus() also calls jamulus_controller.stop() internally.
        if not hosted_server_alive or hosted_recording_safe:
            try:
                self.bridge.stop_jamulus()
            except Exception:  # noqa: BLE001
                LOGGER.exception("Jamulus shutdown failed")
        if hosted_server_alive and hosted_recording_safe:
            try:
                self.bridge.stop_hosted_server()
            except Exception:  # noqa: BLE001
                LOGGER.exception("Hosted server shutdown failed")
        # Keep the host peer reachable until the recorder is stopped and local
        # joiners have had their final control snapshot. Joiners then preserve
        # any still-active original and retain a resumable upload queue.
        self._stop_session_peer(clear_invite=True)
        self._clear_remote_invite_owner()
        self._stop_remote_transport(restore_route=False)
        self._remote_invitation = None
        try:
            self.webex.stop()
        except Exception:  # noqa: BLE001
            LOGGER.exception("Webex stop failed")
        # Tear down the embedded QWebEngineView so its Chromium subprocess
        # doesn't outlive WebJam — it was otherwise never explicitly closed.
        try:
            self.window.webex_embed.shutdown()
        except Exception:  # noqa: BLE001
            LOGGER.exception("Webex embed shutdown failed")
        try:
            self.api_bridge.stop()
        except Exception:  # noqa: BLE001
            LOGGER.exception("Companion API stop failed")

    def _configure_guest_peer(self, invite) -> None:
        self._stop_session_peer(clear_invite=True)
        self._guest_invite = invite
        self._guest_peer_configuration_failed = False
        try:
            self.guest_peer = GuestPeerSession(
                invite,
                display_name=self.settings.musician_name,
                takes_root=(
                    self.settings.takes_directory
                    or str(Path.home() / "Music" / "WebJam Takes")
                ),
                installation_path=default_installation_identity_path(
                    self.settings.config_file
                ),
                capture_enabled=lambda: bool(self.settings.local_capture_enabled),
                capture_config=lambda: (
                    int(self.settings.audio_input_device_index),
                    int(self.settings.audio_samplerate),
                    int(self.settings.audio_blocksize),
                ),
                on_originals_changed=self._on_guest_originals_changed,
            )
            self._on_guest_originals_changed(self.guest_peer.originals_root)
            if self.guest_peer.recovered_captures:
                self.window.flash_message(
                    "WebJam recovered unfinished local audio from an earlier "
                    "session. Open Studio to review it.",
                    ms=9000,
                )
        except Exception:  # noqa: BLE001
            LOGGER.exception("Could not configure private recording transfer")
            self.guest_peer = None
            # Keep the parsed v2 invite in memory. A musician can repair an
            # invalid Takes folder in Recording Setup and rebuild this peer
            # without pasting the credential again.
            self._guest_peer_configuration_failed = True

    def _invalidate_band_check_evidence(self) -> tuple[bool, bool]:
        """Invalidate any report built before an in-place setup change."""

        self._settings_generation = getattr(self, "_settings_generation", 0) + 1
        dialog = getattr(self, "_ready_check_dialog", None)
        visible = bool(dialog is not None and dialog.isVisible())
        start_when_ready = bool(
            visible and getattr(dialog, "_start_session_when_ready", False)
        )
        if visible:
            dialog.close()
        return visible, start_when_ready

    def _remote_band_check_required(self) -> bool:
        """Return whether the current v3 path still needs its transient gate."""

        token = getattr(self, "_remote_band_check_token", None)
        return token is not None and token != getattr(
            self,
            "_remote_band_check_completed_token",
            None,
        )

    def _mark_remote_band_check_path(
        self,
        snapshot,
        *,
        connected: bool,
        invalidation: tuple[bool, bool] | None = None,
    ) -> bool:
        """Invalidate Band Check for one new v3 generation/path fact.

        This records no packet, decoded-audio, or hearing evidence. If audio is
        already live, the replacement dialog is observation-only.
        """

        raw_role = getattr(snapshot, "role", "")
        raw_path = getattr(snapshot, "path", "unknown")
        role = getattr(raw_role, "value", raw_role)
        path = getattr(raw_path, "value", raw_path)
        try:
            generation = int(getattr(snapshot, "generation", 0) or 0)
        except (TypeError, ValueError):
            generation = 0
        token = (
            str(role),
            generation,
            str(path),
            "connected" if connected else "prepared",
        )
        if token == getattr(self, "_remote_band_check_token", None):
            return False
        self._remote_band_check_token = token
        visible, start_when_ready = (
            invalidation
            if invalidation is not None
            else self._invalidate_band_check_evidence()
        )
        if self._is_jamulus_running():
            self._open_band_check()
        elif visible:
            self._reopen_invalidated_band_check(visible, start_when_ready)
        return True

    def _replace_settings_object(self, settings: AppSettings) -> tuple[bool, bool]:
        """Install new settings and invalidate evidence for the old setup.

        Returns ``(dialog_was_visible, start_session_when_ready)`` so callers
        can reopen the same kind of Band Check only after all long-lived
        services have been reconfigured for the replacement settings.
        """

        if settings is self.settings:
            return False, False
        self.settings = settings
        return self._invalidate_band_check_evidence()

    def _reopen_invalidated_band_check(
        self, dialog_was_visible: bool, start_session_when_ready: bool
    ) -> None:
        """Restore a visible check only after its replacement setup is current."""

        if not dialog_was_visible:
            return
        generation = self._settings_generation

        def reopen_for_current_settings() -> None:
            if generation == getattr(self, "_settings_generation", 0) and not getattr(
                self, "_shutdown", False
            ):
                self._open_band_check(start_session_when_ready=start_session_when_ready)

        QTimer.singleShot(0, reopen_for_current_settings)

    def _stop_session_peer(self, *, clear_invite: bool = False) -> bool:
        cleanup_ok = True
        guest = getattr(self, "guest_peer", None)
        if guest is not None:
            try:
                guest.stop()
            except Exception:  # noqa: BLE001
                LOGGER.exception("Guest recording transfer cleanup failed")
                cleanup_ok = False
        self.guest_peer = None
        if clear_invite:
            self._guest_invite = None
            self._guest_peer_configuration_failed = False
        host = getattr(self, "host_peer", None)
        if host is not None:
            try:
                host.stop()
            except Exception:  # noqa: BLE001
                LOGGER.exception("Host recording service cleanup failed")
                cleanup_ok = False
        self._host_peer_warning = ""
        return cleanup_ok

    def _on_peer_take_updated(
        self,
        _take_id: str,
        take_dir: Path,
        attached_new_media: bool,
    ) -> None:
        """Reveal late verified transfers in the open Studio immediately."""

        def refresh() -> None:
            if self._shutdown:
                return
            self.window.recording_studio.refresh_take(take_dir)
            if attached_new_media:
                self.window.flash_message(
                    "A bandmate's Local Original arrived and is now visible in Studio.",
                    ms=7000,
                )

        self._ui_invoker.invoke(refresh)

    def _on_guest_originals_changed(self, path: Path) -> None:
        """Keep one safe Finder action current as guest originals arrive."""

        def refresh() -> None:
            if self._shutdown:
                return
            self.window.recording_studio.set_local_originals_directory(path)

        self._ui_invoker.invoke(refresh)

    def _sync_local_originals_action(self) -> None:
        takes_root = Path(
            self.settings.takes_directory or str(Path.home() / "Music" / "WebJam Takes")
        ).expanduser()
        self.window.recording_studio.set_local_originals_directory(
            takes_root / "WebJam Local Originals"
        )

    def _local_originals_available(self) -> bool:
        """Whether this session has a real host or v2 guest capture path."""

        return bool(
            getattr(self.settings, "host_server_enabled", False)
            or getattr(self, "guest_peer", None) is not None
            or (
                getattr(self, "_guest_invite", None) is not None
                and not getattr(self, "_guest_peer_configuration_failed", False)
            )
        )

    def _effective_band_check_settings(self) -> AppSettings:
        """Snapshot only the capabilities this concrete session can use.

        A musician's saved local-original opt-in is preserved for later Host
        or v2 sessions, but a legacy/fallback guest must not be blocked by a
        two-channel capture path that does not exist in the current session.
        """

        from copy import deepcopy

        settings = deepcopy(self.settings)
        if not self._local_originals_available():
            settings.local_capture_enabled = False
        return settings

    def _guest_recording_reason(self) -> str:
        if self._local_originals_available():
            return (
                "The host controls take start and stop. Local Originals are "
                "optional in Recording Setup."
            )
        return (
            "The host controls take start and stop. Local originals are "
            "unavailable for this session."
        )

    def _ensure_host_peer(self, address: str) -> bool:
        if not bool(getattr(self.settings, "host_server_enabled", False)):
            self._host_peer_warning = ""
            return False
        if self.host_peer.active:
            self._host_peer_warning = ""
            return True
        try:
            self.host_peer.start(
                address,
                takes_root=(
                    self.settings.takes_directory
                    or str(Path.home() / "Music" / "WebJam Takes")
                ),
                installation_path=default_installation_identity_path(
                    self.settings.config_file
                ),
                display_name=self.settings.musician_name,
            )
            self._host_peer_warning = ""
            return True
        except Exception:  # noqa: BLE001
            LOGGER.exception("Could not start private recording service")
            self._host_peer_warning = (
                "Bandmates can still join and play. Automatic Local Originals "
                "are unavailable, so use the band take or have each musician "
                "record separately."
            )
            return False

    def peer_participant_id_for_channel(self, channel_id: int) -> str:
        if self.host_peer.active:
            return self.host_peer.participant_id_for_channel(channel_id) or ""
        if self.guest_peer is not None:
            local = self.guest_peer.participant_id
            participant = self.participants.get(int(channel_id))
            if local and participant is not None and participant.is_local:
                return local
        return ""

    def signal_peer_recording_started(
        self, take_id: str, *, started_utc: str = ""
    ) -> None:
        if not self.host_peer.active:
            return
        try:
            self.host_peer.begin_take(
                take_id,
                started_utc=(
                    started_utc
                    or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                ),
            )
        except Exception:  # noqa: BLE001
            LOGGER.exception("Could not publish confirmed recording start")

    def signal_peer_recording_stopped(
        self,
        take_id: str,
        *,
        stopped_utc: str = "",
        needs_attention: bool = False,
        message: str = "",
    ) -> None:
        if not self.host_peer.active:
            return
        try:
            self.host_peer.finish_take(
                take_id,
                stopped_utc=(
                    stopped_utc
                    or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                ),
                needs_attention=needs_attention,
                message=message,
            )
        except Exception:  # noqa: BLE001
            LOGGER.exception("Could not publish confirmed recording stop")

    def _confirm_close(self) -> bool:
        """Never let a live jam disappear from a close-button accident."""
        studio = getattr(self.window, "recording_studio", None)
        if bool(getattr(studio, "export_in_progress", False)):
            QMessageBox.information(
                self.window,
                "Track export still running",
                "Wait for ‘Track export ready’ before quitting WebJam. "
                "Your original take is safe.",
            )
            return False
        recording_was_active = self.recording.is_recording_active
        take_in_progress = bool(
            getattr(self.recording, "take_in_progress", recording_was_active)
        )
        hosting_owned = bool(
            getattr(self.settings, "host_server_enabled", False)
            and getattr(self.bridge, "hosted_server_owned", lambda: False)()
        )
        if take_in_progress and hosting_owned:
            QMessageBox.information(
                self.window,
                "Finish the take first",
                (
                    "Press Stop Rec, then wait for ‘Take saved’ before quitting WebJam. "
                    if recording_was_active
                    else "Wait for ‘Take saved’ before quitting WebJam. "
                )
                + "This keeps every musician's track complete and verified.",
            )
            return False
        if not self.recording.confirm_quit():
            return False
        if recording_was_active:
            # The recording dialog already explained the full shutdown impact.
            return True
        active = self._is_jamulus_running() or self.bridge.hosted_server_alive()
        if not active:
            return True
        hosting = bool(getattr(self.settings, "host_server_enabled", False))
        title = "End jam and quit?" if hosting else "Leave jam and quit?"
        body = (
            "Quitting WebJam ends this jam for every connected musician."
            if hosting
            else "Quitting WebJam disconnects you; the band can keep playing."
        )
        reply = QMessageBox.question(
            self.window,
            title,
            body,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return reply == QMessageBox.StandardButton.Yes

    # ------------------------------------------------------------------
    # Companion API (optional localhost bridge for DAWs/editors/scripts)
    # ------------------------------------------------------------------
    def start_companion_api(self) -> bool:
        """Start the localhost companion API if enabled (best-effort).

        Called from the app bootstrap, not __init__, so unit tests that build a
        controller don't bind a real port.  Returns True if it started.
        """
        if not self.settings.companion_api_enabled:
            LOGGER.info("Companion API disabled in settings — not starting")
            return False
        try:
            started = self.api_bridge.start()
        except Exception:  # noqa: BLE001
            LOGGER.exception("Companion API failed to start")
            return False
        if started:
            LOGGER.info(
                "Companion API on http://127.0.0.1:%d", self.settings.companion_api_port
            )
        else:
            LOGGER.info(
                "Companion API not started (fastapi/uvicorn missing or port busy)"
            )
        return started

    @property
    def _jamulus_connected(self) -> bool:
        return self.audio.connected

    @_jamulus_connected.setter
    def _jamulus_connected(self, value: bool) -> None:
        self.audio.connected = value

    def _transition_lifecycle(
        self,
        phase: SessionLifecyclePhase,
        reason: str = "",
        *,
        recovery_attempt: int | None = None,
        role: str | None = None,
    ) -> bool:
        """Record one controller-owned, secret-free lifecycle fact.

        Process and UI callbacks may race during shutdown.  The lifecycle
        rejects stale terminal transitions rather than letting a late worker
        turn a completed session back into a false live state.
        """

        lifecycle = getattr(self, "session_lifecycle", None)
        if lifecycle is None:
            # Focused controller tests and lightweight extensions sometimes
            # construct a controller shell without running QObject.__init__.
            # Give those paths the same truthful lifecycle behavior instead of
            # making diagnostics instrumentation a new launch dependency.
            settings = getattr(self, "settings", None)
            lifecycle = SessionLifecycle(
                role=(
                    "host"
                    if bool(getattr(settings, "host_server_enabled", False))
                    else "join"
                )
            )
            self.session_lifecycle = lifecycle
        lifecycle.set_role(
            role
            if role is not None
            else (
                "host"
                if bool(
                    getattr(getattr(self, "settings", None), "host_server_enabled", False)
                )
                else "join"
            )
        )
        accepted = lifecycle.transition(
            phase,
            reason=reason,
            recovery_attempt=recovery_attempt,
        )
        if not accepted:
            LOGGER.debug(
                "Ignored stale session lifecycle transition to %s",
                phase.value,
            )
        else:
            # A recording manifest needs the same redacted lifecycle facts as
            # the support timeline, but only the coordinator knows whether a
            # server-confirmed take is actually active. Never hand it the raw
            # caller reason; SessionLifecycle already bounded/redacted it.
            recorder = getattr(self, "recording", None)
            record_event = getattr(recorder, "record_lifecycle_event", None)
            if callable(record_event):
                try:
                    recording_reason = re.sub(
                        r"(?i)\bwebjam:(?://)?\[redacted\]",
                        "private invite",
                        lifecycle.snapshot.last_reason,
                    )
                    record_event(
                        phase,
                        reason=recording_reason,
                        recovery_attempt=lifecycle.snapshot.recovery_attempt,
                    )
                except Exception:  # noqa: BLE001
                    LOGGER.debug(
                        "Could not attach lifecycle evidence to active take",
                        exc_info=True,
                    )
            self._record_pilot_lifecycle_completion(phase)
        return accepted

    def _snapshot_participants(self) -> list:
        """Copy the participants list; retries the copy if the UI thread
        mutates the dict mid-iteration (the companion API reads from its own
        uvicorn thread). Returns a list of ParticipantPresentation."""
        for _ in range(5):
            try:
                return list(self.participants.values())
            except RuntimeError:
                continue  # "dict changed size during iteration" — retry
        return []

    def _companion_get_participants(self) -> list[dict]:
        """Snapshot of the current mixer participants for the companion API."""
        out: list[dict] = []
        for p in self._snapshot_participants():
            out.append(
                {
                    "channel_id": p.channel_id,
                    "name": p.name,
                    "fader_level": p.fader_level,
                    "pan": getattr(p, "pan", 50),
                    "muted": bool(p.muted),
                    "solo": bool(p.solo),
                    "is_local": bool(getattr(p, "is_local", False)),
                }
            )
        return out

    def _companion_get_diagnostics(self) -> dict:
        """Non-sensitive session state for the companion API (no secrets)."""
        return {
            "jamulus_state": str(self.bridge.jamulus_state),
            "webex_state": str(self.bridge.webex_state),
            "jamulus_connected": str(self._jamulus_connected),
            "participant_count": str(len(self._snapshot_participants())),
            "jamulus_server": f"{self.settings.jamulus_server}:{self.settings.jamulus_port}",
            "session_health": self.session_health.to_public_dict(),
            "session_lifecycle": self.session_lifecycle.snapshot.to_public_dict(),
        }

    def _attach_jamulus_callbacks(self) -> None:
        """Attach UI callbacks to the current JamulusController instance."""
        self.jamulus.chat_callback = self._on_jamulus_chat
        self.jamulus.recorder_state_callback = self._on_recorder_state

    # ------------------------------------------------------------------
    # Initial wiring
    # ------------------------------------------------------------------
    def _wire_signals(self) -> None:
        strip = self.window.session_strip
        strip.mode_changed.connect(self._on_mode_changed)
        strip.session_title_changed.connect(self._on_title_changed)
        strip.launch_audio_requested.connect(self._on_session_audio_requested)
        strip.join_video_requested.connect(self._on_join_video)
        strip.practice_requested.connect(self._on_practice_requested)
        strip.record_requested.connect(self._on_record_requested)
        strip.ready_check_requested.connect(self._on_ready_check)
        strip.invite_requested.connect(self._copy_band_invite)
        strip.reset_invite_requested.connect(self._reset_remote_invite)
        strip.tool_requested.connect(self._on_rail_view_changed)
        if self._operator_mode:
            self.window.test_night_requested.connect(self._open_test_night)
        self.window.session_hud.action_requested.connect(
            self._on_conductor_action_requested
        )
        self.window.session_hud.secondary_action_requested.connect(
            self._on_conductor_secondary_action_requested
        )
        # Both launch affordances share URL validation and truthful state.
        self.window.webex_embed.fallback_button().clicked.connect(self._on_join_video)
        self.window.close_requested.connect(self.shutdown)
        self.window.confirm_close = self._confirm_close
        # Settings shortcut (Ctrl+,) and side-rail Settings button → wizard
        self.window._settings_shortcut.activated.connect(self._open_settings_wizard)
        self.window.side_rail.view_changed.connect(self._on_rail_view_changed)

        # Participant grid re-emits card signals — connect once here
        grid = self.window.participant_grid
        grid.fader_changed.connect(self._on_fader_changed)
        grid.mute_toggled.connect(self._on_mute_toggled)
        grid.solo_toggled.connect(self._on_solo_toggled)
        grid.ready_check_requested.connect(self._on_ready_check)
        grid.start_audio_requested.connect(self._on_session_audio_requested)
        grid.practice_requested.connect(self._on_practice_requested)
        grid.microphone_settings_requested.connect(self._open_microphone_settings)

        studio = self.window.recording_studio
        export_started = getattr(studio, "export_started", None)
        export_finished = getattr(studio, "export_finished", None)
        if export_started is not None:
            export_started.connect(self._on_studio_export_started)
        if export_finished is not None:
            export_finished.connect(self._on_studio_export_finished)

        # Save/Load mix shortcuts
        self.window._save_mix_shortcut.activated.connect(self._on_save_mix)
        self.window._load_mix_shortcut.activated.connect(self._on_load_mix)
        # Save Mix As... / Load Mix... shortcuts (multi-slot named mixes)
        self.window._save_mix_as_shortcut.activated.connect(self._on_save_mix_as)
        self.window._load_mix_from_shortcut.activated.connect(self._on_load_mix_from)
        # Mute all shortcut
        self.window._mute_all_shortcut.activated.connect(self._on_mute_all)
        # Practice mode shortcut (Ctrl+P)
        self.window._practice_shortcut.activated.connect(self._on_practice_requested)
        # Diagnostics export shortcut (Ctrl+Shift+D)
        self.window._diagnostics_shortcut.activated.connect(self._on_export_diagnostics)
        self.window._ready_check_shortcut.activated.connect(self._on_ready_check)
        self.window.session_canvas.chat_submitted.connect(self._on_chat_submitted)
        self.window.session_canvas.notes_changed.connect(
            self._schedule_session_pulse_refresh
        )
        self.window.session_canvas.brief_export_requested.connect(
            self._refresh_session_pulse
        )
        studio = self.window.recording_studio
        studio.record_requested.connect(self._on_record_requested)
        studio.return_live_requested.connect(
            lambda: self.window.side_rail.trigger("stage")
        )
        studio.live_fader_changed.connect(self._on_fader_changed)
        studio.live_mute_toggled.connect(self._on_mute_toggled)
        studio.live_solo_toggled.connect(self._on_solo_toggled)
        studio.output_device_changed.connect(self._save_take_playback_output)
        studio.recording_setup_requested.connect(self._open_recording_setup)
        # Reset all faders shortcut (Ctrl+Shift+R)
        self.window._reset_faders_shortcut.activated.connect(self._on_reset_all_faders)

    def _bootstrap_ui(self) -> None:
        self.participants.clear()
        self._push_participants_to_grid()
        self.window.participant_grid.set_session_state(
            SessionUiState.idle(
                server=self.bridge.effective_server(),
                hosting=bool(getattr(self.settings, "host_server_enabled", False)),
            )
        )

        mode = get_mode_by_key_or_default(
            self.window.session_strip.current_mode_key() or "music_jam"
        )
        self._apply_mode(mode)
        self.window.set_status_audio("Ready to launch")
        self.window.set_status_video("Not opened")
        self.window.set_status_latency("Not connected")
        self.window.set_status_routing("")
        self.session_health.reset_live_truth()
        self.session_health.mark_process(self.bridge.jamulus_state)
        self.window.session_strip.reset_session_clock()
        # Start the global meter decay tick (continuous; one timer for the
        # whole grid instead of one per LevelMeter).
        self._meter_tick_timer.start()
        self.window.session_strip.set_video_configured(
            bool(str(self.settings.webex_url or "").strip())
        )
        self.window.session_strip.set_tools_enabled(True)
        self.window.webex_embed.set_audio_mode(self._webex_audio_mode())
        self.window.recording_studio.set_takes_directory(self.settings.takes_directory)
        self._sync_local_originals_action()
        self.window.recording_studio.set_output_device(
            self.settings.take_playback_output_device
        )
        hosting = bool(getattr(self.settings, "host_server_enabled", False))
        # Recording, video, conversation, notes, and settings live behind Session
        # Tools.  The live header keeps only the one action needed now.
        self.window.session_strip.set_recording_available(False)
        self.window.session_strip.set_invite_available(False)
        self.window.session_strip.set_reset_invite_available(False)
        self.window.recording_studio.set_can_record(
            hosting,
            self._guest_recording_reason() if not hosting else "",
        )
        self._load_notes()
        self._load_session_title()
        self._refresh_session_pulse()
        self._update_session_hud()

    def _push_participants_to_grid(self) -> None:
        self.window.participant_grid.set_participants(self.participants.values())
        self._sync_self_mute_button()
        self._refresh_session_pulse()

    def _sync_self_mute_button(self) -> None:
        """Compatibility hook that can only clear unsupported transmit state."""
        self._talk_break_intended = False
        self._self_transmit_muted = False

    def _webex_audio_mode(self) -> str:
        mode = getattr(self.settings, "webex_audio_mode", "talkback")
        return (
            mode
            if mode in {"talkback", "video_only", "audience_bridge"}
            else "talkback"
        )

    # ------------------------------------------------------------------
    # Real Jamulus participant callback (called from background thread)
    # ------------------------------------------------------------------
    def _on_jamulus_participants(self, jamulus_participants: list) -> None:
        """Receive live participant list from JamulusController — runs on a worker thread."""
        self._ui_invoker.invoke(
            lambda: self._apply_jamulus_participants(jamulus_participants)
        )

    def _on_ready_check(self) -> None:
        """F2 — observe a live jam; Jamulus owns setup before it starts."""
        if not self._is_jamulus_running():
            self.window.flash_message(
                "Start or join a jam first. Jamulus owns your sound setup; "
                "Verify Sound is available once music is connected.",
                ms=7000,
            )
            return
        self._open_band_check()

    def _open_band_check(self, *, start_session_when_ready: bool = False) -> None:
        """Open Band Check; optionally make it the unverified-start gate."""
        from core.band_check import BandCheckMode

        self._conductor_setup_requested = True
        self._conductor_band_check = EvidenceState.IN_PROGRESS

        if start_session_when_ready and not self._is_jamulus_running():
            self._transition_lifecycle(
                SessionLifecyclePhase.RUNNING_PREFLIGHT,
                "Band Check is verifying this setup before audio starts",
            )

        existing = getattr(self, "_ready_check_dialog", None)
        if existing is not None and existing.isVisible():
            generation = getattr(self, "_settings_generation", 0)
            if getattr(existing, "_settings_generation", -1) != generation:
                # A report built for replaced settings is never eligible for
                # promotion into a production-audio start gate.
                existing.close()
                existing = None
        if existing is not None and existing.isVisible():
            existing_mode = getattr(existing, "_mode", None)
            if start_session_when_ready and existing_mode is BandCheckMode.LIVE_OBSERVE:
                # A live-observe report must never be promoted into permission
                # to start a new audio stream.  If the old session ended while
                # it was open, replace it with the ordinary pre-session gate.
                existing.close()
            else:
                if start_session_when_ready and not bool(
                    getattr(existing, "_start_session_when_ready", False)
                ):
                    # Preserve progress when F2 Band Check is already open,
                    # while making the final explicit action start the session.
                    existing._start_session_when_ready = True
                    existing.session_start_requested.connect(
                        lambda generation=getattr(existing, "_settings_generation", -1): (
                            self._on_band_check_session_start_requested(generation)
                        )
                    )
                    existing._refresh_action_button()
                if existing_mode is not None:
                    existing._refresh_live_observations()
                existing.raise_()
                existing.activateWindow()
                return
        from webjam_qt.windows.ready_check import BandCheckDialog

        # An already-running client is observation-only.  A hosted server by
        # itself does not own the musician input device, so a retry may still
        # use the pre-session workflow without disrupting anyone connected to
        # that server.
        live = self._is_jamulus_running() or (
            not start_session_when_ready and self.bridge.hosted_server_alive()
        )
        dialog = BandCheckDialog(
            self._effective_band_check_settings,
            parent=self.window,
            mode=(BandCheckMode.LIVE_OBSERVE if live else BandCheckMode.PRE_SESSION),
            observations_provider=self._band_check_observations,
            host_server_service=self.bridge,
            start_session_when_ready=start_session_when_ready,
            settings_generation_provider=lambda: getattr(
                self, "_settings_generation", 0
            ),
        )
        dialog._settings_generation = getattr(self, "_settings_generation", 0)
        dialog.settings_requested.connect(self._bring_jamulus_forward)
        dialog.recording_settings_requested.connect(self._open_recording_setup)
        # Old extensions may still emit this compatibility signal. It must
        # never mutate a live-music device choice; foreground Jamulus instead.
        dialog.system_input_requested.connect(self._bring_jamulus_forward)
        dialog.microphone_settings_requested.connect(self._open_microphone_settings)
        dialog.practice_requested.connect(self._on_practice_requested)
        dialog.support_requested.connect(self._on_save_support_bundle)
        if start_session_when_ready:
            dialog.session_start_requested.connect(
                lambda generation=dialog._settings_generation: (
                    self._on_band_check_session_start_requested(generation)
                )
            )

        def _clear_dialog(_result) -> None:
            # Replacing a stale LIVE_OBSERVE dialog may deliver its finished
            # signal after the new pre-session gate exists.  Never let that
            # older signal discard the current dialog reference.
            if getattr(self, "_ready_check_dialog", None) is dialog:
                self._ready_check_dialog = None
                if self._conductor_band_check is EvidenceState.IN_PROGRESS:
                    # Closing an unfinished check is not a failure or a pass.
                    self._conductor_band_check = EvidenceState.NOT_STARTED
                self._update_session_hud()

        dialog.finished.connect(_clear_dialog)
        self._ready_check_dialog = dialog
        dialog.show()

    def _start_after_band_check(self, settings_generation: int | None = None) -> None:
        current_generation = getattr(self, "_settings_generation", 0)
        if (
            settings_generation is not None
            and settings_generation != current_generation
        ):
            # A late signal from a dialog closed during an invite/settings
            # replacement must fail closed into a fresh report.
            if (
                not getattr(self, "_shutdown", False)
                and not self._is_jamulus_running()
                and not bool(getattr(getattr(self, "audio", None), "stopping", False))
            ):
                self._open_band_check(start_session_when_ready=True)
            return
        if not self._is_jamulus_running():
            self._remote_band_check_completed_token = getattr(
                self,
                "_remote_band_check_token",
                None,
            )
            self._on_launch_audio()

    def _on_band_check_session_start_requested(
        self, settings_generation: int | None = None
    ) -> None:
        """Promote completed Band Check evidence before starting audio."""

        self._conductor_band_check = EvidenceState.VERIFIED
        self._update_session_hud()
        self._start_after_band_check(settings_generation)

    def _on_session_audio_requested(self) -> None:
        """Start/cancel the native journey, or end an already live jam."""
        if bool(getattr(getattr(self, "audio", None), "stopping", False)):
            return
        # Lightweight legacy/controller-extension fixtures may construct this
        # QObject without the normal initializer. Keep that narrow compatibility
        # path on the old verifier instead of inventing incomplete startup
        # state for a partially constructed controller.
        if not hasattr(self, "_startup_generation"):
            if self._is_jamulus_running():
                self._on_launch_audio()
            else:
                self.start_session_or_band_check()
            return
        attempt = getattr(self, "_startup_attempt", None)
        if attempt is not None:
            phase = str(attempt.get("phase", ""))
            if phase in {"invite_ready", "live"} and self._is_jamulus_running():
                self._on_launch_audio()
            else:
                self._cancel_startup_journey()
            return
        if self._is_jamulus_running():
            self._on_launch_audio()
            return
        self.begin_startup_journey()

    # ------------------------------------------------------------------
    # Jamulus-native startup journey
    # ------------------------------------------------------------------
    def begin_startup_journey(self) -> None:
        """Start one non-modal host/join journey without a WebJam device gate."""

        if getattr(self, "_shutdown", False) or bool(
            getattr(getattr(self, "audio", None), "stopping", False)
        ):
            return
        active = getattr(self, "_startup_attempt", None)
        if active is not None and str(active.get("phase", "")) not in {"failed"}:
            return
        if bool(getattr(self, "_remote_invitation_requires_replacement", False)):
            self._render_remote_fresh_invitation_hud()
            return
        # The v3 transport has its own authenticated enrollment state. It is
        # intentionally kept out of the LAN/Jamulus-native profile flow.
        if getattr(self, "_remote_invitation", None) is not None:
            self._begin_remote_join()
            return
        if bool(getattr(self.settings, "host_server_enabled", False)):
            from services.native_remote_transport import reference_local_host_requested

            if (
                reference_local_host_requested()
                and getattr(self, "_remote_invite_owner", None) is None
            ):
                self._begin_remote_host()
                return

        role = (
            "host"
            if bool(getattr(self.settings, "host_server_enabled", False))
            else "guest"
        )
        recovery = getattr(self, "_startup_recovery_record", None)
        if recovery is None:
            recovery = self._startup_attempt_store.load()
            self._startup_recovery_record = recovery
        try:
            stored_generation = self._startup_attempt_store.next_generation()
        except Exception:  # noqa: BLE001 - a fresh in-memory generation is safe
            stored_generation = self._startup_generation + 1
        self._startup_generation = max(
            self._startup_generation + 1,
            int(stored_generation),
        )
        generation = self._startup_generation
        self._startup_attempt = {
            "generation": generation,
            "role": role,
            "phase": "starting_server" if role == "host" else "launching_client",
            "cancel_event": threading.Event(),
            "started_at": time.monotonic(),
            "setup_finished": False,
            "human_confirmed": False,
            "fast_path": False,
            "webex_decision": None,
        }
        if recovery is not None and str(getattr(recovery.role, "value", recovery.role)) == role:
            # The record is not trusted yet—only copied into this transient
            # attempt so the profile comparison below can decide whether it is
            # safe to resume.  It is deliberately never shown to the user.
            self._startup_attempt["recovery_record"] = recovery
            self._startup_attempt["attempt_id"] = recovery.attempt_id
        self._startup_profile_plan = None
        self._conductor_setup_requested = True
        self._conductor_band_check = EvidenceState.NOT_STARTED
        self.window.session_strip.set_recording_available(False)
        self._render_startup_journey()

        if role == "host":
            self._transition_lifecycle(
                SessionLifecyclePhase.STARTING_HOST,
                "Starting the private band server before native sound setup",
                role="host",
            )
            self._start_hosted_server_for_startup(generation)
        else:
            self._transition_lifecycle(
                SessionLifecyclePhase.JOINING,
                "Opening Jamulus for the invited band",
                role="join",
            )
            self._launch_native_jamulus_for_startup(generation)

    def _startup_attempt_for(self, generation: int) -> dict[str, object] | None:
        attempt = getattr(self, "_startup_attempt", None)
        if (
            attempt is None
            or int(attempt.get("generation", -1)) != int(generation)
            or getattr(self, "_shutdown", False)
        ):
            return None
        return attempt

    def _start_hosted_server_for_startup(self, generation: int) -> None:
        """Start the host's private server off the UI thread exactly once."""

        attempt = self._startup_attempt_for(generation)
        if attempt is None:
            return
        cancel_event = attempt.get("cancel_event")

        def cancelled() -> bool:
            current = self._startup_attempt_for(generation)
            if current is not attempt or str(attempt.get("phase", "")) == "cancelling":
                return True
            return bool(getattr(cancel_event, "is_set", lambda: False)())

        def worker() -> None:
            if cancelled():
                return
            try:
                ok, _detail = self.bridge.ensure_hosted_server(
                    cancel_requested=cancelled,
                )
            except Exception:  # noqa: BLE001 - fixed-copy recovery below
                LOGGER.exception("Hosted server startup failed")
                ok = False

            def deliver() -> None:
                if cancelled():
                    return
                if not ok:
                    self._fail_startup_journey(
                        generation,
                        "WebJam couldn't start your private jam. Try again, or close another WebJam window first.",
                    )
                    return
                self._launch_native_jamulus_for_startup(generation)

            try:
                self._ui_invoker.invoke(deliver)
            except RuntimeError:
                LOGGER.debug("Hosted server startup finished after Qt shutdown")

        threading.Thread(
            target=worker,
            daemon=True,
            name="webjam-startup-host-server",
        ).start()

    def _launch_native_jamulus_for_startup(self, generation: int) -> None:
        """Launch the visible Jamulus client without WebJam permission/device UI."""

        attempt = self._startup_attempt_for(generation)
        if (
            attempt is None
            or str(attempt.get("phase", "")) == "cancelling"
            or bool(getattr(attempt.get("cancel_event"), "is_set", lambda: False)())
        ):
            return
        attempt["phase"] = "native_sound_setup"
        self._transition_lifecycle(
            (
                SessionLifecyclePhase.STARTING_HOST
                if attempt["role"] == "host"
                else SessionLifecyclePhase.JOINING
            ),
            "Opening Jamulus for native sound setup",
        )
        self._render_startup_journey()
        if not self._is_jamulus_running():
            self.audio.ended_by_user = False
            self.audio.connection_timed_out = False
            self.audio.recovering = False
            self._local_audio_seen = False
            self._remote_audio_seen = False
            accepted = bool(self.bridge.launch_jamulus(manual=True))
            if not accepted:
                self._fail_startup_journey(
                    generation,
                    "WebJam couldn't open Jamulus. Reinstall this WebJam build, then try again.",
                )
                return
            self._connection_timer.start()
        self._schedule_startup_poll(generation)

    def _schedule_startup_poll(self, generation: int) -> None:
        QTimer.singleShot(
            350,
            lambda current_generation=generation: self._poll_startup_connection(
                current_generation
            ),
        )

    def _poll_startup_connection(self, generation: int) -> None:
        """Advance only on proven process/RPC/roster truth, never a meter."""

        attempt = self._startup_attempt_for(generation)
        if attempt is None:
            return
        phase = str(attempt.get("phase", ""))
        if phase in {"failed", "cancelling", "invite_ready", "live"}:
            return
        terminal = {"Stopped", "Launch failed", "Not found", "Port in use"}
        state = str(getattr(self.bridge, "jamulus_state", "") or "")
        if state in terminal:
            self._fail_startup_journey(
                generation,
                "Jamulus couldn't open the music connection. Check Jamulus, then try again.",
            )
            return

        plan = getattr(self.bridge, "native_profile_plan", None)
        if plan is not None:
            self._startup_profile_plan = plan
            self._apply_matching_startup_recovery(attempt, plan)
            try:
                from core.jamulus_profile import StartupRole

                fast_path = self._startup_readiness_store.is_current(
                    plan,
                    StartupRole(str(attempt["role"])),
                )
            except Exception:  # noqa: BLE001 - safe fallback is native setup
                fast_path = False
            attempt["fast_path"] = bool(
                attempt.get("fast_path", False) or fast_path
            )
            if fast_path:
                attempt["setup_finished"] = True
                attempt["human_confirmed"] = True

        if not self._is_jamulus_running() or not self._startup_music_is_proven(attempt):
            if bool(attempt.get("setup_finished", False)):
                attempt["phase"] = "verifying_music"
            else:
                attempt["phase"] = "native_sound_setup"
            self._render_startup_journey()
            self._schedule_startup_poll(generation)
            return

        # A v2 invitation's authenticated peer plane carries only enrollment,
        # durable presence, and opt-in Local Originals. Start it only after
        # this exact native Jamulus connection is proven—never at app boot or
        # before a cancelled launch has a chance to clean up.
        self._start_guest_peer_for_native_startup(attempt)
        if (
            str(attempt.get("phase", "")) == "cancelling"
            or bool(getattr(attempt.get("cancel_event"), "is_set", lambda: False)())
        ):
            return

        if not bool(attempt.get("setup_finished", False)):
            attempt["phase"] = "native_sound_setup"
            self._render_startup_journey()
            return
        if bool(attempt.get("fast_path", False)):
            self._continue_after_music_ready(generation)
            return
        attempt["phase"] = "confirm_sound"
        self._render_startup_journey()

    def _start_guest_peer_for_native_startup(
        self, attempt: dict[str, object]
    ) -> None:
        """Start a v2 recording peer after Jamulus identity is proven.

        This stays out of v3, Host, and disconnected paths.  The peer has no
        live-music device authority; it only enrolls and later follows a
        host-confirmed recording signal for opted-in Local Originals.
        """

        if (
            attempt.get("role") != "guest"
            or bool(attempt.get("peer_started", False))
            or str(attempt.get("phase", "")) == "cancelling"
            or bool(getattr(attempt.get("cancel_event"), "is_set", lambda: False)())
            or getattr(self, "_remote_invitation", None) is not None
            or getattr(self, "_remote_session", None) is not None
            or getattr(self, "_remote_invite_owner", None) is not None
            or getattr(self.bridge, "remote_guest_mode_enabled", False) is True
        ):
            return
        guest = getattr(self, "guest_peer", None)
        invite = getattr(self, "_guest_invite", None)
        if guest is None and invite is not None:
            self._configure_guest_peer(invite)
            guest = getattr(self, "guest_peer", None)
        # A broken optional Local Originals path must never retry in a poll
        # loop or block the musician from playing the shared Jamulus take.
        attempt["peer_started"] = True
        if guest is None:
            return
        try:
            guest.start()
        except Exception:  # noqa: BLE001 - peer transfer cannot block music
            LOGGER.exception("Could not start guest recording transfer")

    def _apply_matching_startup_recovery(self, attempt: dict[str, object], plan) -> None:
        """Resume only a strictly matching, path-free prior journey record.

        A process can survive a desktop restart, but an old profile cannot
        establish sound readiness by itself.  Matching profile evidence may
        restore the *next safe prompt*; any changed/missing profile returns to
        native Jamulus setup without consuming or exposing private session data.
        """

        if bool(attempt.get("recovery_checked", False)):
            return
        attempt["recovery_checked"] = True
        record = attempt.get("recovery_record")
        if record is None:
            return
        try:
            from core.jamulus_profile import (
                StartupClientPhase,
                StartupConnectionState,
                StartupRole,
            )

            role = StartupRole(str(attempt["role"]))
            if (
                record.role is not role
                or record.profile_fingerprint != plan.profile_fingerprint
            ):
                return
            if record.client_phase in {
                StartupClientPhase.VERIFYING,
                StartupClientPhase.READY,
            }:
                attempt["setup_finished"] = True
            if bool(record.human_confirmed) and record.connection_state in {
                StartupConnectionState.CONNECTED,
                StartupConnectionState.CONNECTING,
            }:
                # A current profile plus a fresh live proof below makes this a
                # real returning-musician fast path, not a blind replay.
                attempt["human_confirmed"] = True
                attempt["fast_path"] = True
            decision = getattr(record.webex_decision, "value", record.webex_decision)
            if decision in {"skipped", "open_requested"}:
                attempt["webex_decision"] = decision
            attempt["resumed"] = True
        except Exception:  # noqa: BLE001 - recovery must fail closed
            LOGGER.info("Startup recovery did not match the active profile", exc_info=True)

    def _clear_startup_recovery(self) -> None:
        """Forget only completed/cancelled operational recovery state."""

        self._startup_attempt = None
        self._startup_profile_plan = None
        self._startup_recovery_record = None
        try:
            self._startup_attempt_store.clear()
        except Exception:  # noqa: BLE001 - a stale private prompt is harmless
            LOGGER.debug("Could not clear completed startup recovery", exc_info=True)

    def _startup_music_is_proven(self, attempt: dict[str, object]) -> bool:
        """Return only software facts WebJam can honestly verify."""

        rpc = getattr(self.jamulus, "rpc_client", None)
        if not (
            self._is_jamulus_running()
            and bool(getattr(rpc, "available", False))
            and bool(self._jamulus_connected)
        ):
            return False
        if attempt.get("role") == "host" and not self.bridge.hosted_server_alive():
            return False
        local = [
            person
            for person in self.participants.values()
            if self._is_local_participant(person)
        ]
        # Require exactly one local identity rather than guessing from a
        # process or an input meter.
        return len(local) == 1

    def _finish_native_sound_setup(self) -> None:
        attempt = getattr(self, "_startup_attempt", None)
        if attempt is None:
            return
        attempt["setup_finished"] = True
        attempt["phase"] = "verifying_music"
        self._render_startup_journey()
        self._schedule_startup_poll(int(attempt["generation"]))

    def _confirm_startup_audible(self) -> None:
        """Persist one explicit human audibility confirmation after proof."""

        attempt = getattr(self, "_startup_attempt", None)
        if attempt is None:
            return
        generation = int(attempt["generation"])
        if not self._startup_music_is_proven(attempt):
            attempt["phase"] = "verifying_music"
            self._render_startup_journey()
            self._schedule_startup_poll(generation)
            return
        try:
            refreshed = self.bridge.refresh_native_profile_plan()
            if refreshed is not None:
                self._startup_profile_plan = refreshed
                from core.jamulus_profile import StartupRole

                self._startup_readiness_store.save_for_plan(
                    refreshed,
                    StartupRole(str(attempt["role"])),
                    human_confirmed=True,
                )
        except Exception:  # noqa: BLE001 - confirmation remains useful this run
            LOGGER.info("Could not save native sound readiness", exc_info=True)
        attempt["human_confirmed"] = True
        self._continue_after_music_ready(generation)

    def _continue_after_music_ready(self, generation: int) -> None:
        attempt = self._startup_attempt_for(generation)
        if attempt is None:
            return
        if str(attempt.get("phase", "")) in {
            "conversation",
            "conversation_link",
            "invite_ready",
            "live",
        }:
            return
        # A returning musician already made a human confirmation for this
        # exact profile. Do not make the optional conversation question a new
        # gate on every launch.
        if bool(attempt.get("fast_path", False)):
            attempt["webex_decision"] = "skipped"
            self._show_startup_invite_ready(generation)
            return
        attempt["phase"] = "conversation"
        self._render_startup_journey()

    def _show_startup_conversation_input(self) -> None:
        attempt = getattr(self, "_startup_attempt", None)
        if attempt is None:
            return
        attempt["phase"] = "conversation_link"
        self._render_startup_journey()
        self.window.session_hud.focus_input()

    def _save_startup_webex_link(self) -> None:
        attempt = getattr(self, "_startup_attempt", None)
        if attempt is None:
            return
        from core.settings import save_settings
        from core.webex_url import normalize_webex_url, webex_url_error

        raw = self.window.session_hud.input_text()
        value = normalize_webex_url(raw)
        error = webex_url_error(value) if value else "Paste a valid Webex link, or choose Not now."
        if error:
            attempt["input_error"] = error
            self._render_startup_journey()
            return
        previous_url = self.settings.webex_url
        previous_mode = self.settings.webex_audio_mode
        try:
            self.settings.webex_url = value
            self.settings.webex_audio_mode = "talkback"
            save_settings(self.settings)
        except OSError:
            self.settings.webex_url = previous_url
            self.settings.webex_audio_mode = previous_mode
            attempt["input_error"] = "WebJam couldn't save that link. Try again or choose Not now."
            self._render_startup_journey()
            return
        self.webex.meeting_url = value
        self.bridge.webex_controller = self.webex
        self.window.session_strip.set_video_configured(True)
        attempt["webex_decision"] = "open_requested"
        attempt.pop("input_error", None)
        self._show_startup_invite_ready(int(attempt["generation"]))

    def _skip_startup_webex(self) -> None:
        attempt = getattr(self, "_startup_attempt", None)
        if attempt is None:
            return
        attempt["webex_decision"] = "skipped"
        self._show_startup_invite_ready(int(attempt["generation"]))

    def _show_startup_invite_ready(self, generation: int) -> None:
        attempt = self._startup_attempt_for(generation)
        if attempt is None:
            return
        attempt["phase"] = "invite_ready"
        self.window.session_strip.set_recording_available(
            bool(attempt["role"] == "host" and self._jamulus_connected)
        )
        self._render_startup_journey()

    def _enter_startup_jam(self) -> None:
        attempt = getattr(self, "_startup_attempt", None)
        if attempt is None:
            return
        self._clear_startup_recovery()
        self._update_session_hud()

    def _fail_startup_journey(self, generation: int, message: str) -> None:
        attempt = self._startup_attempt_for(generation)
        if attempt is None:
            return
        attempt["phase"] = "failed"
        attempt["failure"] = str(message)
        self._transition_lifecycle(
            SessionLifecyclePhase.FAILED_RECOVERABLE,
            "Jamulus-native startup needs attention",
        )
        self._render_startup_journey()

    def _retry_startup_journey(self) -> None:
        attempt = getattr(self, "_startup_attempt", None)
        if attempt is None:
            self.begin_startup_journey()
            return
        role = str(attempt.get("role", "guest"))
        self._startup_attempt = None
        self._startup_profile_plan = None
        # A healthy owned host server remains available while only the client
        # is retried. This avoids duplicate servers and preserves invite truth.
        if role == "host" and self.bridge.hosted_server_alive():
            self._startup_generation += 1
            generation = self._startup_generation
            self._startup_attempt = {
                "generation": generation,
                "role": "host",
                "phase": "launching_client",
                "cancel_event": threading.Event(),
                "started_at": time.monotonic(),
                "setup_finished": False,
                "human_confirmed": False,
                "fast_path": False,
                "webex_decision": None,
            }
            self._launch_native_jamulus_for_startup(generation)
            return
        self.begin_startup_journey()

    def _cancel_startup_journey(self) -> None:
        attempt = getattr(self, "_startup_attempt", None)
        if attempt is None:
            return
        generation = int(attempt["generation"])
        role = str(attempt.get("role", "guest"))
        cancel_event = attempt.get("cancel_event")
        cancel = getattr(cancel_event, "set", None)
        if callable(cancel):
            cancel()
        attempt["phase"] = "cancelling"
        self._render_startup_journey()

        def worker() -> None:
            try:
                self._stop_session_peer()
                self.bridge.stop_jamulus()
                if role == "host":
                    self.bridge.stop_hosted_server()
            except Exception:  # noqa: BLE001 - cleanup state remains conservative
                LOGGER.exception("Startup cancellation cleanup failed")

            def deliver() -> None:
                if self._startup_attempt_for(generation) is None:
                    return
                self._clear_startup_recovery()
                self.audio.ended_by_user = False
                self.audio.reset_to_idle()

            try:
                self._ui_invoker.invoke(deliver)
            except RuntimeError:
                LOGGER.debug("Startup cancellation finished after Qt shutdown")

        threading.Thread(
            target=worker,
            daemon=True,
            name="webjam-startup-cancel",
        ).start()

    def _bring_jamulus_forward(self) -> None:
        brought_forward = bool(self.bridge.bring_jamulus_forward())
        if brought_forward:
            self.window.flash_message(
                "Jamulus is in front. In Jamulus, choose Settings → Audio/Network Settings.",
                ms=7000,
            )
            return
        self.window.flash_message(
            "Jamulus is still opening. Try Bring Jamulus Forward again in a moment.",
            ms=6000,
        )

    def _persist_startup_attempt(self, attempt: dict[str, object]) -> None:
        """Write only the allowlisted recovery facts once a profile exists."""

        plan = self._startup_profile_plan or getattr(
            self.bridge, "native_profile_plan", None
        )
        if plan is None:
            return
        try:
            from core.jamulus_profile import (
                StartupAttemptRecord,
                StartupClientPhase,
                StartupConnectionState,
                StartupNextAction,
                StartupRole,
                StartupServerPhase,
                StartupWebexDecision,
            )

            phase = str(attempt.get("phase", ""))
            role = StartupRole(str(attempt.get("role", "guest")))
            server_phase = (
                StartupServerPhase.NOT_REQUIRED
                if role is StartupRole.GUEST
                else (
                    StartupServerPhase.STARTING
                    if phase == "starting_server"
                    else StartupServerPhase.FAILED
                    if phase == "failed"
                    else StartupServerPhase.READY
                )
            )
            client_phase = {
                "native_sound_setup": StartupClientPhase.NATIVE_SOUND_SETUP,
                "verifying_music": StartupClientPhase.VERIFYING,
                "confirm_sound": StartupClientPhase.VERIFYING,
                "conversation": StartupClientPhase.READY,
                "conversation_link": StartupClientPhase.READY,
                "invite_ready": StartupClientPhase.READY,
                "failed": StartupClientPhase.FAILED,
            }.get(phase, StartupClientPhase.LAUNCHING)
            connection = (
                StartupConnectionState.CONNECTED
                if self._startup_music_is_proven(attempt)
                else StartupConnectionState.FAILED
                if phase == "failed"
                else StartupConnectionState.CONNECTING
            )
            next_action = {
                "starting_server": StartupNextAction.WAIT_FOR_SERVER,
                "launching_client": StartupNextAction.OPEN_JAMULUS,
                "native_sound_setup": StartupNextAction.FINISH_SOUND_SETUP,
                "verifying_music": StartupNextAction.FINISH_SOUND_SETUP,
                "confirm_sound": StartupNextAction.CONFIRM_AUDIBLE,
                "conversation": StartupNextAction.OPTIONAL_WEBEX,
                "conversation_link": StartupNextAction.OPTIONAL_WEBEX,
                "invite_ready": (
                    StartupNextAction.COPY_INVITE
                    if role is StartupRole.HOST
                    else StartupNextAction.ENTER_JAM
                ),
                "failed": StartupNextAction.RETRY,
            }.get(phase, StartupNextAction.NONE)
            decision = attempt.get("webex_decision")
            webex_decision = (
                StartupWebexDecision(decision)
                if decision in {"skipped", "open_requested"}
                else None
            )
            record_kwargs = {
                "generation": int(attempt["generation"]),
                "role": role,
                "server_phase": server_phase,
                "client_phase": client_phase,
                "profile_fingerprint": plan.profile_fingerprint,
                "connection_state": connection,
                "human_confirmed": bool(attempt.get("human_confirmed", False)),
                "webex_decision": webex_decision,
                "next_action": next_action,
            }
            attempt_id = attempt.get("attempt_id")
            if attempt_id:
                record = StartupAttemptRecord(
                    attempt_id=str(attempt_id),
                    **record_kwargs,
                )
            else:
                record = StartupAttemptRecord.new(**record_kwargs)
                attempt["attempt_id"] = record.attempt_id
            self._startup_attempt_store.save(record)
        except Exception:  # noqa: BLE001 - recovery persistence must not block music
            LOGGER.info("Could not persist startup recovery state", exc_info=True)

    def _render_startup_journey(self) -> None:
        """Project the one current setup step into the always-visible HUD."""

        attempt = getattr(self, "_startup_attempt", None)
        if attempt is None:
            return
        role = str(attempt.get("role", "guest"))
        phase = str(attempt.get("phase", ""))
        end_label = "End Session" if role == "host" else "Leave Jam"
        self.window.session_strip.set_audio_state(
            end_label,
            enabled=phase != "cancelling",
        )
        if phase == "starting_server":
            self.window.session_hud.set_state(
                "Starting your private jam",
                "WebJam is starting the band server. Your sound setup comes next in Jamulus.",
                action_visible=False,
            )
        elif phase in {"launching_client", "native_sound_setup"}:
            self.window.session_hud.set_state(
                "Set up your sound in Jamulus",
                "Choose your interface, input channels, headphones, and buffer in Jamulus. WebJam will watch the connection here.",
                action_text="I Finished Sound Setup",
                action_visible=True,
                action_kind="native_setup_finished",
                secondary_action_text="Bring Jamulus Forward",
                secondary_action_visible=True,
                secondary_action_kind="bring_jamulus",
            )
        elif phase == "verifying_music":
            self.window.session_hud.set_state(
                "Checking your music connection",
                "WebJam is confirming the Jamulus client, private server, and your place in the band.",
                action_visible=False,
                secondary_action_text="Bring Jamulus Forward",
                secondary_action_visible=True,
                secondary_action_kind="bring_jamulus",
            )
        elif phase == "confirm_sound":
            self.window.session_hud.set_state(
                "Listen for your instrument",
                "Can you hear your instrument returning cleanly from the jam?",
                action_text="Yes, It Sounds Right",
                action_visible=True,
                action_kind="sound_confirmed",
                secondary_action_text="Fix Audio in Jamulus",
                secondary_action_visible=True,
                secondary_action_kind="fix_audio",
            )
        elif phase == "conversation":
            self.window.session_hud.set_state(
                "Add conversation if you use it",
                "Jamulus carries the music. Webex is optional for talking or video.",
                action_text="Add Webex",
                action_visible=True,
                action_kind="add_webex",
                secondary_action_text="Not Now",
                secondary_action_visible=True,
                secondary_action_kind="skip_webex",
            )
        elif phase == "conversation_link":
            error = str(attempt.get("input_error", "") or "")
            detail = (
                error
                or "Paste a Webex link if your band uses one. WebJam will only open it when you ask."
            )
            self.window.session_hud.set_state(
                "Add Webex",
                detail,
                action_text="Save Webex",
                action_visible=True,
                action_kind="save_webex",
                secondary_action_text="Not Now",
                secondary_action_visible=True,
                secondary_action_kind="skip_webex",
                input_visible=True,
                input_placeholder="https://…",
                input_value=self.window.session_hud.input_text(),
                input_accessible_name="Optional Webex meeting link",
            )
        elif phase == "invite_ready":
            if role == "host":
                self.window.session_hud.set_state(
                    "Your jam is ready",
                    "Invite your band when you are ready. Jamulus carries the music.",
                    invite_available=True,
                    action_text="Copy Invite",
                    action_visible=True,
                    action_kind="copy_invite",
                    ready=True,
                    secondary_action_text="Enter Jam",
                    secondary_action_visible=True,
                    secondary_action_kind="enter_jam",
                )
            else:
                self.window.session_hud.set_state(
                    "Ready to play",
                    "Your Jamulus connection is ready. Enter the jam when you are ready.",
                    action_text="Enter Jam",
                    action_visible=True,
                    action_kind="enter_jam",
                    ready=True,
                )
        elif phase == "cancelling":
            self.window.session_hud.set_state(
                "Closing this setup",
                "WebJam is safely releasing the private music session.",
                action_visible=False,
            )
        else:
            self.window.session_hud.set_state(
                "Music setup needs attention",
                str(
                    attempt.get(
                        "failure",
                        "WebJam couldn't finish this music setup. Try again.",
                    )
                ),
                action_text="Try Again",
                action_visible=True,
                action_kind="retry_startup",
                secondary_action_text="Cancel",
                secondary_action_visible=True,
                secondary_action_kind="cancel_startup",
            )
        self._persist_startup_attempt(attempt)

    def start_session_or_band_check(self) -> None:
        """Reuse a matching verification or gate startup with Band Check.

        Signature probing runs off the UI thread and never opens an audio
        stream. A missing, corrupt, failed, or changed verification fails
        closed into the guided check.
        """

        self._conductor_setup_requested = True
        if bool(getattr(self, "_remote_invitation_requires_replacement", False)):
            self._render_remote_fresh_invitation_hud()
            return

        self._transition_lifecycle(
            SessionLifecyclePhase.PREPARING,
            "Preparing the session",
        )

        if getattr(self, "_remote_invitation", None) is not None:
            self._begin_remote_join()
            return

        if bool(getattr(self.settings, "host_server_enabled", False)):
            from services.native_remote_transport import (
                reference_local_host_requested,
            )

            if (
                reference_local_host_requested()
                and getattr(self, "_remote_invite_owner", None) is None
            ):
                self._begin_remote_host()
                return

        if (
            (getattr(self, "bridge", None) is not None and self._is_jamulus_running())
            or bool(getattr(getattr(self, "audio", None), "stopping", False))
            or getattr(self, "_band_check_start_pending", False)
        ):
            return

        self._band_check_start_pending = True
        self._conductor_band_check = EvidenceState.IN_PROGRESS
        source_settings = self.settings
        settings = self._effective_band_check_settings()
        settings_generation = getattr(self, "_settings_generation", 0)
        remote_band_check_required = self._remote_band_check_required()
        self.window.session_hud.set_state(
            (
                "Checking this connection…"
                if remote_band_check_required
                else "Checking this setup…"
            ),
            (
                "Band Check must confirm this new private path before audio starts."
                if remote_band_check_required
                else "WebJam is confirming whether your verified audio setup changed."
            ),
        )
        self._transition_lifecycle(
            SessionLifecyclePhase.RUNNING_PREFLIGHT,
            "Checking saved Band Check evidence",
        )

        def worker() -> None:
            verified = False
            try:
                from core.band_check import (
                    build_verification_signature,
                    load_verification,
                    verification_path,
                )
                from webjam_qt import __version__

                signature = build_verification_signature(
                    settings,
                    app_version=__version__,
                )
                saved = load_verification(verification_path(settings))
                verified = bool(
                    not remote_band_check_required
                    and saved
                    and saved.matches(signature)
                )
            except Exception:  # noqa: BLE001
                LOGGER.exception("Band Check verification could not be inspected")

            def deliver() -> None:
                self._band_check_start_pending = False
                if getattr(self, "_shutdown", False):
                    return
                if (
                    source_settings is not self.settings
                    or settings_generation != getattr(self, "_settings_generation", 0)
                ):
                    # An invite or Settings save replaced the setup while the
                    # worker was inspecting it, or Recording Setup changed the
                    # object in place. Never apply old verification to the new
                    # setup; immediately inspect the current one.
                    self.start_session_or_band_check()
                    return
                if (
                    getattr(self, "bridge", None) is not None
                    and self._is_jamulus_running()
                ):
                    return
                if verified:
                    self._conductor_band_check = EvidenceState.VERIFIED
                    self._on_launch_audio()
                else:
                    self._open_band_check(start_session_when_ready=True)

            try:
                self._ui_invoker.invoke(deliver)
            except RuntimeError:
                self._band_check_start_pending = False
                LOGGER.debug("Startup Band Check finished after Qt shutdown")

        threading.Thread(
            target=worker,
            daemon=True,
            name="band-check-startup",
        ).start()

    def _band_check_observations(self):
        """Return read-only evidence; never start, stop, or restart a service."""
        from core.band_check import BandCheckObservations

        rpc = getattr(self.jamulus, "rpc_client", None)
        rpc_available = bool(getattr(rpc, "available", False))
        responsive = rpc_available
        if rpc_available:
            try:
                age = rpc.last_activity_age()
                if age is not None:
                    responsive = float(age) <= self._RPC_HANG_THRESHOLD_S
            except Exception:  # noqa: BLE001
                responsive = False
        meter_active = False
        meter_rms = 0.0
        try:
            diagnostics = self.jamulus.audio_engine.diagnostics()
            meter_active = bool(getattr(diagnostics, "active", False))
            meter_rms = float(self.jamulus.audio_engine.get_level(-1))
        except Exception:  # noqa: BLE001
            pass
        peer_connected = any(
            not self._is_local_participant(person)
            for person in self.participants.values()
        )
        hosting = bool(getattr(self.settings, "host_server_enabled", False))
        remote_runtime = getattr(self, "_remote_session", None)
        remote_snapshot = getattr(remote_runtime, "snapshot", None)
        remote_path_facts: dict[str, object] = {}
        if remote_snapshot is not None:
            remote_path_facts = {
                "connection_path": getattr(remote_snapshot, "path", None),
                "connection_quality": getattr(
                    remote_snapshot,
                    "quality",
                    "unknown",
                ),
                "path_generation": int(getattr(remote_snapshot, "generation", 0) or 0),
                # These stay false until the transport or an opt-in decoded
                # fixture publishes the corresponding independent fact.
                "transport_datagrams_flowed": bool(
                    getattr(
                        remote_snapshot,
                        "transport_datagrams_flowed",
                        getattr(
                            remote_runtime,
                            "transport_datagrams_flowed",
                            False,
                        ),
                    )
                ),
                "remote_decoded_test_observed": bool(
                    getattr(
                        remote_snapshot,
                        "remote_decoded_test_observed",
                        getattr(
                            remote_runtime,
                            "remote_decoded_test_observed",
                            False,
                        ),
                    )
                ),
            }
        return BandCheckObservations(
            music_engine_running=self._is_jamulus_running(),
            music_engine_responsive=responsive,
            band_server_running=(
                self.bridge.hosted_server_alive() if hosting else None
            ),
            recorder_ready=(
                rpc_available and self.bridge.hosted_server_alive() if hosting else None
            ),
            production_local_signal=bool(self._local_audio_seen),
            production_remote_signal=bool(self._remote_audio_seen),
            peer_connected=peer_connected,
            local_meter_active=meter_active,
            local_meter_rms=meter_rms,
            # The existing local meter exposes RMS only. Do not invent a peak
            # or clipping result from it during LIVE_OBSERVE.
            local_meter_peak=meter_rms,
            local_meter_clipped=False,
            **remote_path_facts,
        )

    def _on_chat_submitted(self, text: str) -> None:
        """User sent a chat message from the canvas box — send to the band and
        echo it locally so the sender sees their own message."""
        text = (text or "").strip()
        if not text:
            return
        self.jamulus.send_chat(text)
        self.window.session_canvas.append_line(f"You: {text}")

    def _on_recorder_state(self, recording: bool, raw_state: int) -> None:
        """Server recorder state changed (arrives on the RPC reader thread)."""
        self._ui_invoker.invoke(lambda: self._apply_recorder_state(recording))

    def _apply_recorder_state(self, recording: bool) -> None:
        self.recording.on_server_state(recording)

    def _on_jamulus_chat(self, text: str) -> None:
        """Incoming band chat (arrives on the RPC reader thread).

        Jamulus chat text can contain HTML markup (sender/time formatting);
        strip it to plain text and append it to the shared canvas so the whole
        band's conversation lives in the session record.
        """
        import re

        plain = re.sub(r"<[^>]+>", "", text or "").strip()
        if not plain:
            return
        self._ui_invoker.invoke(lambda: self.window.session_canvas.append_line(plain))

    def _apply_jamulus_participants(self, jamulus_participants: list) -> None:
        """Update the participant grid on the UI thread from real Jamulus data."""
        self.audio.apply_participants(jamulus_participants)
        # Bind only this process's authenticated local participant. The host
        # resolves remote channels from each joiner's signed presence update,
        # so duplicate or renamed display names never become identity keys.
        for person in jamulus_participants:
            if not self._is_local_participant(person):
                continue
            if self.host_peer.active:
                try:
                    self.host_peer.bind_host_presence(
                        int(person.channel_id),
                        str(person.name or self.settings.musician_name),
                        capture_enabled=bool(self.settings.local_capture_enabled),
                    )
                except Exception:  # noqa: BLE001
                    LOGGER.exception("Could not bind host participant presence")
            if self.guest_peer is not None:
                self.guest_peer.observe_presence(
                    int(person.channel_id),
                    str(person.name or self.settings.musician_name),
                )
        for channel_id, presentation in self.participants.items():
            durable = self.peer_participant_id_for_channel(channel_id)
            if durable:
                presentation.participant_id = durable
        self.window.recording_studio.set_live_participants(self.participants.values())
        self._update_session_hud()

    @staticmethod
    def _is_local_participant(person) -> bool:
        """Return one consistent local-person answer during RPC startup."""
        # Modern transport objects carry explicit truth. Only old fixtures or
        # extensions without the field use the historical channel-0 fallback;
        # treating every remote channel 0 as local would mask a failed host
        # connection when guests remain on the server.
        if hasattr(person, "is_local"):
            return bool(getattr(person, "is_local"))
        return getattr(person, "channel_id", -1) == 0

    @staticmethod
    def _profile_label(value) -> str:
        """Hide Jamulus's empty-profile sentinel strings from musicians."""
        text = str(value or "").strip()
        if text.lower() in {"none", "null", "unknown", "n/a", "-"}:
            return ""
        return text

    @staticmethod
    def _role_label(jp) -> str:
        bits: list[str] = []
        # Use the same classification as readiness and tile presentation.
        if ApplicationController._is_local_participant(jp):
            bits.append("You")
        instrument = ApplicationController._profile_label(getattr(jp, "instrument", ""))
        if instrument:
            bits.append(instrument.title())
        if not bits:
            bits.append("Musician")
        # Skill badge from the musician's Jamulus profile (stage view v2).
        skill = ApplicationController._profile_label(getattr(jp, "skill_level", ""))
        if skill:
            bits.append(skill.title())
        return " · ".join(bits)

    # ------------------------------------------------------------------
    # Level polling — real audio engine values
    # ------------------------------------------------------------------
    def _poll_levels(self) -> None:
        """Called every 100 ms; pushes audio engine levels to participant grid."""
        try:
            diag = self.jamulus.audio_engine.diagnostics()
            source = getattr(diag, "backend", "unknown")
        except Exception:  # noqa: BLE001
            source = "unknown"
        self.session_health.mark_levels(source)
        studio_levels: dict[int, float] = {}
        truth_changed = False
        engine = self.jamulus.audio_engine
        for channel_id, person in self.participants.items():
            has_channel_level = engine.has_level_override(channel_id)
            level = engine.get_level(channel_id)
            is_local = self._is_local_participant(person)
            if is_local and not has_channel_level:
                # The hardware stream is local input truth only. Never paint a
                # musician's microphone level onto every remote participant.
                # It may still differ from the input selected by Jamulus, so
                # show it on the local card without promoting session readiness.
                level = engine.get_level(-1)
            self.window.participant_grid.update_level(channel_id, level)
            studio_levels[channel_id] = level
            if level > 0.01 and has_channel_level:
                if is_local and not self._local_audio_seen:
                    self._local_audio_seen = True
                    truth_changed = True
                elif not is_local and not self._remote_audio_seen:
                    self._remote_audio_seen = True
                    truth_changed = True
        self.window.recording_studio.set_live_levels(studio_levels)
        if truth_changed:
            self._update_session_hud()

    def _connected_audio_detail(self, count: int) -> str:
        prefix = f"{count} musician{'s' if count != 1 else ''} connected"
        if self._local_audio_seen and self._remote_audio_seen:
            return f"{prefix} · Your input and band audio are detected."
        if self._local_audio_seen:
            return f"{prefix} · Your input is detected."
        if self._remote_audio_seen:
            return f"{prefix} · Band audio detected; play a note to check your input."
        return f"{prefix} · Play a note to check your input."

    # ------------------------------------------------------------------
    # Session strip handlers
    # ------------------------------------------------------------------
    def _on_mode_changed(self, mode_key: str) -> None:
        # Persist the mode change immediately so a crash before clean
        # shutdown still saves the user's preference.
        self._save_session_title()
        mode = get_mode_by_key_or_default(mode_key)
        self._apply_mode(mode)
        self._refresh_session_pulse()
        self.window.flash_message(f"Switched to {mode.label}")

    def _on_title_changed(self, title: str) -> None:
        # Session titles are user content. Persist them locally, but never put
        # them into logs that may later be included in a support bundle.
        LOGGER.info("Session title updated")
        # Persist immediately so a crash before clean shutdown doesn't lose it
        self._save_session_title()
        self._refresh_session_pulse()

    def _apply_mode(self, mode) -> None:
        # Modes remain as compatibility metadata, but the hidden selector
        # must not inject infrastructure-heavy instructions into the simple
        # session surface.
        LOGGER.debug("Session mode active: %s", mode.label)

    def _on_launch_audio(self) -> None:
        """Toggle handler — launches Jamulus if stopped, stops it if running."""
        if bool(getattr(self, "_remote_invitation_requires_replacement", False)):
            # An attempted v3 enrollment may have consumed its one-use
            # capability. Do not let a generic Start Audio action turn that
            # failure into a legacy Jamulus launch.
            self._render_remote_fresh_invitation_hud()
            return
        if not self._is_jamulus_running():
            self._transition_lifecycle(
                (
                    SessionLifecyclePhase.STARTING_HOST
                    if bool(
                        getattr(
                            getattr(self, "settings", None),
                            "host_server_enabled",
                            False,
                        )
                    )
                    else SessionLifecyclePhase.JOINING
                ),
                "Starting band audio",
            )
        if not self._is_jamulus_running():
            owner = getattr(self, "_remote_invite_owner", None)
            try:
                if owner is not None:
                    # The owner is the in-memory v3 host discriminator.  Set
                    # the loopback-only constraint before AudioCoordinator can
                    # ask BridgeService to launch JamulusServer.
                    self._install_remote_invite_owner(owner)
                elif not self.bridge.hosted_server_alive():
                    # A completed v3 session must not affect a later legacy
                    # v1/v2 LAN host in the same app process.
                    disable_remote_host_mode = getattr(
                        self.bridge,
                        "disable_remote_host_mode",
                        None,
                    )
                    if callable(disable_remote_host_mode):
                        disable_remote_host_mode()
            except RuntimeError:
                LOGGER.error("Remote host mode was configured after server launch")
                self.window.flash_message(
                    "End the current jam, then start the remote jam again.",
                    ms=7000,
                )
                return
        launch_allowed = self.audio.on_launch_toggle()
        if launch_allowed:
            # V1/v2 GuestPeerSession is a separate same-LAN plaintext service.
            # It must never be started for a v3 session, even if stale state is
            # reintroduced by a future invitation-transition regression.
            if (
                getattr(self, "_remote_invitation", None) is not None
                or getattr(self, "_remote_session", None) is not None
                or getattr(self, "_remote_invite_owner", None) is not None
                or getattr(self.bridge, "remote_guest_mode_enabled", False) is True
            ):
                return
            guest = getattr(self, "guest_peer", None)
            invite = getattr(self, "_guest_invite", None)
            if guest is None and invite is not None:
                self._configure_guest_peer(invite)
                guest = getattr(self, "guest_peer", None)
            if guest is not None:
                # The v2 peer can arm an opted-in Local Original from the
                # host's recording signal.  Start it only after Band Check has
                # authorized this production-audio attempt and microphone
                # preflight allows it, never at app boot or inside either gate.
                guest.start()

    def _install_remote_invite_owner(self, owner: object) -> None:
        """Install the v3 host owner and arm loopback binding in memory only.

        Future remote-host orchestration calls this before starting the local
        Jamulus server.  It intentionally does not save settings or serialize
        invitation material.
        """

        if owner is None:
            raise TypeError("remote invitation owner is required")
        if not bool(getattr(self.settings, "host_server_enabled", False)):
            raise RuntimeError("remote host mode requires local server ownership")
        existing = getattr(self, "_remote_invite_owner", None)
        if existing is not None and existing is not owner:
            raise RuntimeError("a different remote invitation owner is active")
        self.bridge.enable_remote_host_mode()
        self._remote_invite_owner = owner

    def _clear_remote_invite_owner(self) -> bool:
        """Revoke v3 invitation state and clear mode after server ownership ends."""

        cleanup_ok = True
        owner = getattr(self, "_remote_invite_owner", None)
        if owner is not None:
            try:
                owner.stop()
            except Exception as exc:  # noqa: BLE001 - never log private detail
                LOGGER.error(
                    "Remote invitation cleanup failed; exception_type=%s",
                    type(exc).__name__,
                )
                cleanup_ok = False
        self._remote_invite_owner = None
        if getattr(self, "_remote_session", None) is owner:
            self._remote_session = None
        if self.bridge.hosted_server_alive():
            # A failed stop remains owned. Keep its launch constraint intact;
            # the mode is ephemeral and disappears with this app process.
            return False
        try:
            disable_remote_host_mode = getattr(
                self.bridge,
                "disable_remote_host_mode",
                None,
            )
            if callable(disable_remote_host_mode):
                disable_remote_host_mode()
        except RuntimeError:
            cleanup_ok = False
        return cleanup_ok

    def _stop_remote_transport(self, *, restore_route: bool = True) -> bool:
        """Stop v3 only after Jamulus has released its loopback proxy."""

        cleanup_ok = True
        runtime = getattr(self, "_remote_session", None)
        self._remote_session = None
        if runtime is not None:
            try:
                runtime.stop()
                from services.remote_session_runtime import RemoteSessionErrorCode

                if (
                    getattr(getattr(runtime, "snapshot", None), "error_code", None)
                    is RemoteSessionErrorCode.STOP_FAILED
                ):
                    cleanup_ok = False
            except Exception as exc:  # noqa: BLE001 - never log private detail
                LOGGER.error(
                    "Remote transport cleanup failed; exception_type=%s",
                    type(exc).__name__,
                )
                cleanup_ok = False
        disable_remote_guest_mode = getattr(
            self.bridge,
            "disable_remote_guest_mode",
            None,
        )
        if callable(disable_remote_guest_mode):
            try:
                disable_remote_guest_mode()
            except RuntimeError:
                LOGGER.error("Remote guest mode remained active during cleanup")
                cleanup_ok = False
        base_settings = getattr(self, "_remote_route_base_settings", None)
        self._remote_route_base_settings = None
        self._remote_route_generation = 0
        self._remote_band_check_token = None
        self._remote_band_check_completed_token = None
        if restore_route and base_settings is not None and base_settings is not self.settings:
            old_settings = self.settings
            self._replace_settings_object(base_settings)
            self._reconfigure_services_after_settings(old_settings)
        if cleanup_ok:
            # A deliberate replacement/leave has released the old v3 state.
            # A new invitation may now begin a separate enrollment attempt.
            self._remote_invitation_requires_replacement = False
        return cleanup_ok

    def _show_private_session_cleanup_failure(self) -> None:
        self.window.flash_message(
            "WebJam couldn’t close the previous private session safely. "
            "Close WebJam, reopen it, then try the invitation again.",
            ms=8000,
        )

    def _on_record_requested(self) -> None:
        """Start a take after the one explicit Local Originals decision.

        The shared host take is the default.  A first-time host can opt into
        separate interface stems here, at the moment recording matters,
        without turning Host/Join or Jamulus setup into a recording wizard.
        """

        settings = getattr(self, "settings", None)
        needs_choice = bool(
            settings is not None
            and getattr(settings, "host_server_enabled", False)
            and not getattr(settings, "local_capture_enabled", False)
            and not getattr(settings, "local_capture_choice_made", False)
        )
        if needs_choice:
            from webjam_qt.windows.recording_setup import LocalOriginalsChoiceDialog

            choice_dialog = LocalOriginalsChoiceDialog(parent=self.window)
            if choice_dialog.exec() != LocalOriginalsChoiceDialog.DialogCode.Accepted:
                return
            choice = choice_dialog.choice
            if choice not in {"shared", "local"}:
                return
            previous_choice = bool(settings.local_capture_choice_made)
            previous_capture = bool(settings.local_capture_enabled)
            settings.local_capture_choice_made = True
            if choice == "shared":
                settings.local_capture_enabled = False
            try:
                from core.settings import save_settings

                save_settings(settings)
            except OSError:
                settings.local_capture_choice_made = previous_choice
                settings.local_capture_enabled = previous_capture
                self.window.flash_message(
                    "WebJam couldn't save your recording choice. Try again.",
                    ms=6000,
                )
                return
            if choice == "local":
                self._open_recording_setup()
                return
        self.recording.on_record_requested()

    def _copy_band_invite(self) -> None:
        """Copy one complete invitation; never make a musician parse it."""
        from PySide6.QtWidgets import QApplication

        owner = getattr(self, "_remote_invite_owner", None)
        if owner is not None:
            try:
                invite_url = owner.copy_for_clipboard()
            except Exception:  # noqa: BLE001 - private owner exposes fixed state
                self._update_session_hud()
                self.window.flash_message(
                    "Reset the invitation under More, then copy the fresh link.",
                    ms=6000,
                )
                return
        else:
            readiness = self._host_share_readiness()
            invite_url = self._current_invite_url(readiness=readiness)
        if not invite_url:
            self._update_session_hud()
            self.window.flash_message(
                "Connect this Mac to Wi-Fi, then try again.",
                ms=6000,
            )
            return
        QApplication.clipboard().setText(invite_url)
        invite_url = ""
        if owner is None:
            # Mark the address only after the complete link was copied. A
            # failed link generation must not suppress the Wi-Fi-change
            # warning for a link the musician never received.
            self._last_shared_lan_address = readiness.address
        if self._host_peer_warning:
            # The legacy invitation is intentionally still usable, but the
            # host must never miss why automatic originals are unavailable.
            self._update_session_hud()
        self.window.flash_message(
            "Invite link copied — send it to your bandmate.",
            ms=7000,
        )

    def accept_invite_url(self, value: str) -> bool:
        """Compatibility boundary for an explicit in-app paste.

        Serialized invitation text is parsed exactly once and is never kept on
        the controller.  New code should call :meth:`accept_invitation` with
        the already-parsed typed object from the application ingress.
        """
        from webjam_qt.invitation_ingress import (
            InvitationIngressError,
            InvitationSource,
            parse_invitation_at_ingress,
        )

        try:
            invite = parse_invitation_at_ingress(
                value,
                source=InvitationSource.PASTE,
            )
        except InvitationIngressError as exc:
            self.window.flash_message(str(exc), ms=6000)
            return False
        return self.accept_invitation(invite)

    def accept_invitation(self, invitation: BandInvite | RemoteInvitation) -> bool:
        """Join one typed invitation delivered by the trusted UI boundary."""

        if isinstance(invitation, RemoteInvitation):
            return self._accept_remote_invitation(invitation)
        if isinstance(invitation, BandInvite):
            return self._accept_band_invitation(invitation)
        raise TypeError("invitation must be a BandInvite or RemoteInvitation")

    def _accept_remote_invitation(self, invitation: RemoteInvitation) -> bool:
        """Retain one typed v3 capability until the transport consumes it.

        A remote session cannot be passed into the legacy settings/Jamulus
        launch path: doing so would start Jamulus against a loopback port that
        has no authenticated peer. The transport coordinator owns the later
        enrollment and keeps the capability memory-only until the route opens
        or enrollment outcome is known. Retry remains available only when the
        sidecar could not start before it received the capability; any later
        failure requires a fresh invitation and never falls into legacy audio.
        """

        if invitation.advisory_expired():
            self.window.flash_message(
                "That invitation expired. Ask the host for a fresh one.",
                ms=7000,
            )
            return False
        if self._is_jamulus_running() or self.bridge.hosted_server_alive():
            self.window.flash_message(
                "End this jam first, then open the new invitation again.",
                ms=7000,
            )
            return False
        if (
            getattr(self, "_remote_invite_owner", None) is not None
            and not self._clear_remote_invite_owner()
        ):
            self._show_private_session_cleanup_failure()
            return False
        if not self._stop_remote_transport():
            self._show_private_session_cleanup_failure()
            return False
        # V2's peer/original-transfer service is an intentionally isolated
        # same-LAN plaintext channel. Stop and forget it before v3 enrollment
        # so a later audio launch cannot reopen the previous session beside the
        # authenticated transport.
        if not self._stop_session_peer(clear_invite=True):
            self._show_private_session_cleanup_failure()
            return False
        self._remote_invitation = invitation
        self.window.session_strip.set_invite_available(False)
        self.window.session_strip.set_reset_invite_available(False)
        self.window.session_hud.set_state(
            "Preparing your jam",
            "WebJam is finding the fastest secure path to the host.",
        )
        self._begin_remote_join()
        return True

    def _accept_band_invitation(self, invite: BandInvite) -> bool:
        """Preserve the existing v1/v2 same-LAN join flow."""

        busy = bool(self._is_jamulus_running() or self.bridge.hosted_server_alive())
        if (
            busy
            and bool(
                getattr(
                    self.recording,
                    "take_in_progress",
                    self.recording.is_recording_active,
                )
            )
            and self.bridge.hosted_server_owned()
        ):
            QMessageBox.information(
                self.window,
                "Finish the take first",
                "Stop the recording if it is still running, then wait for ‘Take "
                "saved’ before joining another jam. Your current tracks will stay "
                "protected.",
            )
            return False
        if busy:
            reply = QMessageBox.question(
                self.window,
                "Join this jam?",
                "WebJam will safely end your current jam, then join the new one.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return False
            self.audio.stopping = True
            self.window.session_strip.set_audio_state("Switching…", enabled=False)

        self.window.session_hud.set_state(
            "Joining your jam…",
            "WebJam is switching the band connection safely."
            if busy
            else "WebJam is connecting your music.",
        )

        def _apply_and_launch() -> bool:
            from core.settings import load_settings, save_settings
            from webjam_qt.windows.launch_dialog import apply_join_invite

            # Accepting a legacy v1/v2 invitation replaces any armed v3 host,
            # even when that host never reached a live server. Revoke its
            # bearer and clear the ephemeral loopback constraint before the
            # settings object becomes a Join profile; otherwise the next
            # launch would see a stale owner and reject the legacy join.
            if (
                getattr(self, "_remote_invite_owner", None) is not None
                and not self._clear_remote_invite_owner()
            ):
                self._show_private_session_cleanup_failure()
                return False
            # Clear all v3 guest state, including a pending capability or an
            # orphaned remote-mode marker, before the v1/v2 settings and peer
            # are installed. Otherwise a new startup journey could re-enter
            # the stopped v3 join after applying the legacy invite.
            if not self._stop_remote_transport():
                self._show_private_session_cleanup_failure()
                return False
            self._remote_invitation = None
            old_settings = self.settings
            settings_path = self.settings.config_file
            new_settings = load_settings(settings_path)
            apply_join_invite(new_settings, invite)
            try:
                save_settings(new_settings)
            except Exception:  # noqa: BLE001
                LOGGER.exception("Could not save incoming invitation")
                self.window.flash_message(
                    "WebJam couldn't use that invite yet. Try opening it again.",
                    ms=7000,
                )
                return False
            self._replace_settings_object(load_settings(settings_path))
            if busy:
                # The worker has finalized the old host recording and stopped
                # its services. Clear local recorder/Studio truth only now;
                # doing this before the RPC stop can race and truncate a take.
                self.recording.on_audio_session_stopped()
                self.window.session_strip.reset_session_clock()
            self._reconfigure_services_after_settings(old_settings)
            if bool(getattr(invite, "peer_enabled", False)):
                self._configure_guest_peer(invite)
            else:
                self._stop_session_peer(clear_invite=True)
            self.window.session_strip.set_session_title(invite.session_name)
            self._save_session_title()
            self.window.recording_studio.set_takes_directory(
                self.settings.takes_directory
            )
            self._sync_local_originals_action()
            self.window.recording_studio.set_output_device(
                self.settings.take_playback_output_device
            )
            self.window.recording_studio.set_can_record(
                False,
                self._guest_recording_reason(),
            )
            self.window.session_strip.set_recording_available(False)
            self.window.session_strip.set_invite_available(False)
            self.audio.connected = False
            self.audio.stopping = False
            self.audio.ended_by_user = False
            self.audio.reset_to_idle()
            self.begin_startup_journey()
            return True

        if not busy:
            return _apply_and_launch()

        def _switch_worker() -> None:
            cleanup_ok = True
            try:
                if self.bridge.hosted_server_owned():
                    cleanup_ok = bool(
                        self.recording.stop_server_recording_for_shutdown()
                    )
                if not cleanup_ok:
                    raise RuntimeError(
                        "Hosted recording finalization was not confirmed"
                    )
                cleanup_ok = bool(self.bridge.stop_jamulus()) and cleanup_ok
                if self.bridge.hosted_server_alive():
                    cleanup_ok = bool(self.bridge.stop_hosted_server()) and cleanup_ok
            except Exception:  # noqa: BLE001
                LOGGER.exception("Could not safely leave the current jam")
                cleanup_ok = False
            if cleanup_ok:
                self._ui_invoker.invoke(_apply_and_launch)
                return

            def _show_switch_failure() -> None:
                self.audio.stopping = False
                self.audio.ended_by_user = False
                self.window.participant_grid.set_session_state(
                    SessionUiState.stop_failed()
                )
                self.window.session_hud.set_state(
                    "WebJam couldn’t switch jams safely",
                    "Close WebJam, then open the new invitation again.",
                )
                self.window.session_strip.set_audio_state("Close WebJam", enabled=False)

            self._ui_invoker.invoke(_show_switch_failure)

        threading.Thread(
            target=_switch_worker,
            daemon=True,
            name="webjam-invite-switch",
        ).start()
        return True

    def _retry_session(self) -> None:
        if bool(getattr(self, "_remote_invitation_requires_replacement", False)):
            self._render_remote_fresh_invitation_hud()
            return
        if self._remote_join_retry_pending():
            self._begin_remote_join()
            return
        if self._is_jamulus_running():
            # A running host may only be missing its Wi-Fi address. Re-evaluate
            # that truth in place instead of pretending the audio must restart.
            self._update_session_hud()
            if self._current_invite_url():
                self.window.flash_message("The invite is ready to copy.")
            else:
                self.window.flash_message(
                    "WebJam still can’t see the band network. Check Wi-Fi and try again."
                )
            return
        # Keep narrow compatibility for lightweight extension/test fixtures
        # that predate the Jamulus-native journey and intentionally construct
        # only the old Band Check surface.
        if not hasattr(self, "_startup_generation"):
            self.start_session_or_band_check()
            return
        self.begin_startup_journey()

    def _begin_remote_join(self) -> None:
        """Enroll a v3 guest before Jamulus can see its loopback proxy."""

        from services.native_remote_transport import NativeGuestTransportBackend
        from services.remote_session_runtime import (
            RemoteSessionPhase,
            RemoteSessionRuntime,
        )

        invitation = getattr(self, "_remote_invitation", None)
        if invitation is None or getattr(self, "_shutdown", False):
            return
        self._transition_lifecycle(
            SessionLifecyclePhase.PREPARING,
            "Preparing the private music path",
        )
        runtime = getattr(self, "_remote_session", None)
        if runtime is not None and runtime.snapshot.phase in {
            RemoteSessionPhase.PREPARING,
            RemoteSessionPhase.CONNECTED,
        }:
            return

        self.window.participant_grid.set_session_state(
            SessionUiState.connecting("secure session")
        )
        self.window.session_hud.set_state(
            "Finding the fastest path",
            "WebJam is opening your private music connection.",
        )
        try:
            runtime = RemoteSessionRuntime(
                NativeGuestTransportBackend(),
                on_snapshot=self._on_remote_session_snapshot,
                schedule_callback=self._ui_invoker.invoke,
            )
        except Exception as exc:  # noqa: BLE001 - never expose private detail
            LOGGER.error(
                "Remote transport startup failed; exception_type=%s",
                type(exc).__name__,
            )
            self._show_remote_session_failure(
                guest_enrollment=True,
                retry_safe=True,
            )
            return
        self._remote_session = runtime
        runtime.start_guest(invitation)

    def _begin_remote_host(self) -> None:
        """Prepare the opt-in local reference host before Band Check starts."""

        if getattr(self, "_remote_host_preparing", False):
            return
        self._transition_lifecycle(
            SessionLifecyclePhase.PREPARING,
            "Preparing the private host path",
        )
        self._remote_host_preparing = True
        self.window.session_hud.set_state(
            "Preparing your jam",
            "WebJam is creating one private invitation.",
        )

        def worker() -> None:
            owner = None
            try:
                from services.native_remote_transport import NativeHostTransportOwner

                owner = NativeHostTransportOwner(
                    target_port=int(self.settings.jamulus_port),
                    on_snapshot=self._on_remote_session_snapshot,
                    schedule_callback=self._ui_invoker.invoke,
                )
            except Exception as exc:  # noqa: BLE001 - fixed musician copy only
                LOGGER.error(
                    "Remote host preparation failed; exception_type=%s",
                    type(exc).__name__,
                )

            def deliver() -> None:
                self._remote_host_preparing = False
                if getattr(self, "_shutdown", False):
                    if owner is not None:
                        owner.stop()
                    return
                if owner is None:
                    self._show_remote_session_failure()
                    return
                try:
                    self._install_remote_invite_owner(owner)
                except Exception as exc:  # noqa: BLE001
                    owner.stop()
                    LOGGER.error(
                        "Remote host activation failed; exception_type=%s",
                        type(exc).__name__,
                    )
                    self._show_remote_session_failure()
                    return
                self._remote_session = owner
                snapshot = getattr(owner, "snapshot", None)
                if snapshot is not None:
                    self._mark_remote_band_check_path(
                        snapshot,
                        connected=False,
                    )
                self._update_session_hud()
                self.begin_startup_journey()

            try:
                self._ui_invoker.invoke(deliver)
            except RuntimeError:
                if owner is not None:
                    owner.stop()

        threading.Thread(
            target=worker,
            daemon=True,
            name="webjam-remote-host",
        ).start()

    def _on_remote_session_snapshot(self, snapshot) -> None:
        """Apply one safe transport snapshot on Qt's owning thread."""

        from services.remote_session_runtime import RemoteSessionPhase

        if getattr(self, "_shutdown", False):
            return
        if snapshot.phase is RemoteSessionPhase.PREPARING:
            self.window.session_hud.set_state(
                "Finding the fastest path",
                "WebJam is opening your private music connection.",
            )
            return
        if snapshot.phase is RemoteSessionPhase.CONNECTED:
            if snapshot.role.value == "guest":
                self._activate_remote_guest_route(snapshot)
            else:
                self._remote_route_generation = snapshot.generation
                self._mark_remote_band_check_path(
                    snapshot,
                    connected=True,
                )
                self.window.session_hud.set_state(
                    "Bandmate connected",
                    "The private music path is ready for Band Check.",
                )
                self._update_session_hud()
            return
        if snapshot.phase is RemoteSessionPhase.FAILED:
            self._show_remote_session_failure(
                guest_enrollment=(snapshot.role.value == "guest"),
                retry_safe=bool(
                    getattr(snapshot, "invitation_retry_safe", False)
                ),
            )

    def _activate_remote_guest_route(self, snapshot) -> None:
        """Point Jamulus at the authenticated proxy without persisting it."""

        if snapshot.generation == getattr(self, "_remote_route_generation", 0):
            if self._mark_remote_band_check_path(snapshot, connected=True):
                if not self._is_jamulus_running():
                    self.begin_startup_journey()
            return
        from copy import deepcopy

        old_settings = self.settings
        if self._remote_route_base_settings is None:
            self._remote_route_base_settings = old_settings
        routed = deepcopy(old_settings)
        routed.host_server_enabled = False
        routed.jamulus_server = "127.0.0.1"
        routed.jamulus_port = int(snapshot.loopback_port)
        invalidation = self._replace_settings_object(routed)
        self._reconfigure_services_after_settings(old_settings)
        enable_remote_guest_mode = getattr(
            self.bridge,
            "enable_remote_guest_mode",
            None,
        )
        if callable(enable_remote_guest_mode):
            enable_remote_guest_mode()
        self._remote_route_generation = snapshot.generation
        self._mark_remote_band_check_path(
            snapshot,
            connected=True,
            invalidation=invalidation,
        )
        self._remote_invitation = None
        self._remote_invitation_requires_replacement = False
        self.window.session_hud.set_state(
            snapshot.musician_status,
            "Jamulus is opening your music connection.",
        )
        self.begin_startup_journey()

    def _show_remote_session_failure(
        self,
        *,
        guest_enrollment: bool = False,
        retry_safe: bool = False,
    ) -> None:
        """Render a remote failure without replaying an uncertain invitation."""

        self._transition_lifecycle(
            SessionLifecyclePhase.FAILED_RECOVERABLE,
            "The private music path could not open",
        )
        if (
            guest_enrollment
            and retry_safe
            and getattr(self, "_remote_invitation", None) is not None
        ):
            self._remote_invitation_requires_replacement = False
            self.window.participant_grid.set_session_state(
                SessionUiState.remote_session_retry_available()
            )
            self._render_remote_retry_hud()
            flash_message = "Try Again to start the private connection."
        elif guest_enrollment:
            # The sidecar entered open_guest(), so the reference service may
            # have atomically consumed this one-use capability. Remove the
            # controller copy before any generic start path can see it.
            self._remote_invitation = None
            self._remote_invitation_requires_replacement = True
            self.window.participant_grid.set_session_state(
                SessionUiState.remote_session_fresh_invitation_required()
            )
            self._render_remote_fresh_invitation_hud()
            flash_message = "Ask the host for a fresh private invitation."
        else:
            self.window.participant_grid.set_session_state(
                SessionUiState.session_unavailable()
            )
            self.window.session_hud.set_state(
                "The private music path could not open",
                "Ask the host to confirm the session, then try again.",
                action_visible=False,
            )
            flash_message = "The private music connection couldn’t open."
        self.window.session_strip.set_tools_enabled(True)
        self.window.flash_message(
            flash_message,
            ms=7000,
        )

    def _remote_join_retry_pending(self) -> bool:
        """Return whether a failed v3 enrollment proved no invite was used."""

        runtime = getattr(self, "_remote_session", None)
        snapshot = getattr(runtime, "snapshot", None)
        return bool(
            getattr(self, "_remote_invitation", None) is not None
            and getattr(snapshot, "invitation_retry_safe", False)
        )

    def _render_remote_retry_hud(self) -> None:
        """Offer retry only after a proven pre-enrollment failure."""

        self.window.session_hud.set_state(
            "Private connection unavailable",
            "WebJam could not start its secure connection. Try again with this invitation.",
            action_text="Try Again",
            action_visible=True,
            action_kind="retry",
        )

    def _render_remote_fresh_invitation_hud(self) -> None:
        """Keep a consumed-or-uncertain v3 invite out of generic retry paths."""

        self.window.session_hud.set_state(
            "Fresh invitation required",
            "This invitation cannot be reused safely. Ask the host for a new link, then open it here.",
            action_visible=False,
        )

    def _reset_remote_invite(self) -> None:
        """Revoke/replace the host invite through its owning transport."""

        owner = getattr(self, "_remote_invite_owner", None)
        if owner is None:
            self.window.flash_message(
                "Start a remote jam before resetting its invitation.",
                ms=5000,
            )
            return
        try:
            owner.reset()
        except Exception:  # noqa: BLE001
            LOGGER.exception("Remote invitation reset failed")
            self.window.flash_message(
                "WebJam couldn’t reset the invitation yet. Try again.",
                ms=6000,
            )
            return
        self.window.flash_message(
            "Old invitation revoked. A new private link is ready.",
            ms=6000,
        )
        self._update_session_hud()

    def _on_connection_timeout(self) -> None:
        """Turn an endless spinner into one plain recovery action."""
        if self._jamulus_connected or not self.bridge.jamulus_launch_intended:
            return
        self.audio.connection_timed_out = True
        self._transition_lifecycle(
            SessionLifecyclePhase.FAILED_RECOVERABLE,
            "The music engine did not establish a verified connection in time",
        )
        self.window.participant_grid.set_session_state(self._connection_failure_state())
        self.window.session_hud.set_state(
            "Something needs attention",
            "WebJam is getting ready to try again.",
        )
        self.window.session_strip.set_tools_enabled(True)
        threading.Thread(
            target=self.bridge.stop_jamulus,
            daemon=True,
            name="webjam-connect-timeout",
        ).start()

    def _connection_failure_state(self) -> SessionUiState:
        if bool(getattr(self.settings, "host_server_enabled", False)):
            return SessionUiState.host_start_failed()
        return SessionUiState.session_unavailable()

    def _host_share_readiness(self):
        """Return observable pre-share truth for the supported LAN topology."""

        from core.host_share_readiness import evaluate_host_share_readiness
        from core.network_invite import local_band_address

        server_alive = bool(self.bridge.hosted_server_alive())
        port_bound: bool | None = None
        if server_alive:
            probe = getattr(self.bridge, "_port_free", None)
            if callable(probe):
                try:
                    free = probe(int(self.settings.jamulus_port), udp=True)
                    if isinstance(free, bool):
                        port_bound = not free
                except Exception:  # noqa: BLE001 - fail closed before sharing
                    port_bound = None
        return evaluate_host_share_readiness(
            server_alive=server_alive,
            audio_port_bound=port_bound,
            private_lan_address=local_band_address() if server_alive else "",
        )

    def _current_invite_url(self, *, readiness=None) -> str:
        """Return one invite only after same-LAN pre-share facts are true."""
        if not bool(getattr(self.settings, "host_server_enabled", False)):
            return ""
        readiness = readiness or self._host_share_readiness()
        if not readiness.shareable:
            return ""
        from core.network_invite import create_invite_link

        address = readiness.address
        try:
            if self._ensure_host_peer(address):
                return self.host_peer.invite_link(
                    host=address,
                    jamulus_port=self.settings.jamulus_port,
                    session_name=(
                        self.window.session_strip.current_title() or "Band Rehearsal"
                    ),
                )
            # A legacy link remains available for older hosts/tests, but only
            # v2 carries stable enrollment and guest isolated recording.
            return create_invite_link(
                address,
                port=self.settings.jamulus_port,
                session_name=(
                    self.window.session_strip.current_title() or "Band Rehearsal"
                ),
            )
        except ValueError:
            return ""

    def _lan_invite_needs_refresh(self, readiness) -> bool:
        """Return whether a copied same-LAN invite names an old address.

        A legacy/v2 invite embeds the advertised host address. WebJam cannot
        revoke a link that a musician has already received, but it can avoid
        presenting the host as simply ready after Wi-Fi, sleep/wake, or a
        network-interface change. The address stays process-local and is
        deliberately absent from lifecycle/support evidence.
        """

        previous = str(getattr(self, "_last_shared_lan_address", "") or "")
        return bool(
            previous
            and readiness is not None
            and readiness.shareable
            and readiness.address != previous
        )

    def _clear_lan_invite_address(self) -> None:
        """Forget the prior share only after a session cleanly returns idle."""

        self._last_shared_lan_address = ""

    def _update_session_hud_legacy(self) -> None:
        """Maintain exceptional legacy status copy while facts are projected.

        The conductor below owns the normal musician-facing lifecycle and its
        single next action.  This retained renderer is intentionally limited
        to established, topology-specific recovery copy (for example a
        consumed one-use private invitation) that the compact conductor model
        must not try to invent.
        """
        hosting = bool(getattr(self.settings, "host_server_enabled", False))
        connected = bool(self._jamulus_connected)
        participants = list(self.participants.values())
        remote_owner = getattr(self, "_remote_invite_owner", None)
        remote_invite_available = bool(
            hosting and remote_owner is not None and remote_owner.invitation_available
        )
        share_readiness = (
            self._host_share_readiness()
            if hosting and remote_owner is None
            else None
        )
        invite_url = (
            self._current_invite_url(readiness=share_readiness)
            if share_readiness is not None
            else ""
        )
        invite_available = remote_invite_available or bool(invite_url)
        self._last_observed_invite_available = invite_available
        self.window.session_strip.set_invite_available(invite_available)
        self.window.session_strip.set_reset_invite_available(
            bool(hosting and remote_owner is not None)
        )
        self.window.session_strip.set_recording_available(hosting and connected)
        if self.audio.stopping:
            self.window.session_hud.set_state(
                "Ending this jam…" if hosting else "Leaving the jam…",
                (
                    "WebJam is safely finishing recordings and disconnecting everyone."
                    if hosting
                    else "WebJam is disconnecting your audio safely."
                ),
            )
            return
        if self.audio.ended_by_user:
            self.window.session_hud.set_state(
                "Jam ended" if hosting else "You left the jam",
                (
                    "Start again whenever your band is ready."
                    if hosting
                    else "The band can keep playing without you."
                ),
            )
            return
        if self._remote_join_retry_pending():
            self._render_remote_retry_hud()
            return
        if bool(getattr(self, "_remote_invitation_requires_replacement", False)):
            self._render_remote_fresh_invitation_hud()
            return
        from webjam_qt.platform_permissions import microphone_permission_status

        if not connected and microphone_permission_status() in {"denied", "restricted"}:
            self.window.session_hud.set_state(
                "Microphone access is off",
                "Open System Settings below, allow access, then return to WebJam.",
            )
            return
        if self.audio.connection_timed_out:
            self.window.session_hud.set_state(
                "Something needs attention",
                "Make sure you’re on the same Wi-Fi, then use Try Again below.",
                action_visible=False,
            )
            return
        if self.audio.recovering:
            self.window.session_hud.set_state(
                "Connection interrupted",
                "WebJam is reconnecting automatically. Your mix is safe.",
            )
            return
        if (
            not connected
            and not self.bridge.jamulus_launch_intended
            and self.bridge.jamulus_state in {"Not launched", "Not running"}
        ):
            self.window.session_hud.set_state(
                "Ready when you are",
                "WebJam will handle the connection automatically.",
            )
            return
        if hosting:
            if not connected and self.bridge.jamulus_state in {
                "Stopped",
                "Launch failed",
                "Not found",
                "Port in use",
            }:
                self.window.session_hud.set_state(
                    "Something needs attention",
                    "This Mac couldn’t join the jam. Use Try Again below.",
                    action_visible=False,
                )
                return
            server_ready = self.bridge.hosted_server_alive()
            if not server_ready:
                self._transition_lifecycle(
                    SessionLifecyclePhase.WAITING_FOR_REACHABILITY,
                    "Waiting for the hosted server to become available",
                )
                stopped = self.bridge.jamulus_state in {
                    "Stopped",
                    "Launch failed",
                    "Not found",
                    "Port in use",
                }
                if stopped:
                    self.window.session_hud.set_state(
                        "Something needs attention",
                        "Use Try Again below to start the jam again.",
                        action_visible=False,
                    )
                else:
                    self.window.session_hud.set_state(
                        "Starting your jam…",
                        "WebJam is getting the band audio ready.",
                    )
                return
            if (
                remote_owner is None
                and self._lan_invite_needs_refresh(share_readiness)
            ):
                self._transition_lifecycle(
                    SessionLifecyclePhase.DEGRADED,
                    "The private Wi-Fi address changed; a new invite is required",
                )
                self.window.session_hud.set_state(
                    "Your Wi-Fi changed",
                    "Copy a new invite before asking your bandmate to join.",
                    invite_available=True,
                    action_text="Copy New Invite",
                    action_visible=True,
                    action_kind="invite",
                )
                return
            if not invite_available:
                if remote_owner is not None:
                    self.window.session_hud.set_state(
                        "Create a fresh invitation",
                        "Open More and choose Reset Invite, then copy the new link.",
                        action_visible=False,
                    )
                    return
                if share_readiness is not None:
                    self._transition_lifecycle(
                        SessionLifecyclePhase.WAITING_FOR_REACHABILITY,
                        share_readiness.detail,
                    )
                    self.window.session_hud.set_state(
                        share_readiness.title,
                        share_readiness.detail,
                        action_text=share_readiness.action,
                        action_visible=(share_readiness.action != "Wait for WebJam"),
                        action_kind="retry",
                    )
                    return
                self.window.session_hud.set_state(
                    "Something needs attention",
                    "Connect this Mac to Wi-Fi, then try again.",
                    action_text="Try Again",
                    action_visible=True,
                    action_kind="retry",
                )
                return
            bandmates = sum(
                1 for person in participants if not self._is_local_participant(person)
            )
            self._transition_lifecycle(
                (
                    SessionLifecyclePhase.CONNECTED
                    if bandmates and connected
                    else SessionLifecyclePhase.READY_TO_SHARE
                ),
                (
                    "A bandmate is present in the live roster"
                    if bandmates and connected
                    else "A private same-LAN invitation is ready to share"
                ),
            )
            if self._host_peer_warning:
                self.window.session_hud.set_state(
                    "Automatic Local Originals are off",
                    self._host_peer_warning,
                    invite_available=invite_available,
                    action_visible=False,
                    ready=connected,
                )
                return
            if bandmates and not connected:
                self.window.session_hud.set_state(
                    "Connecting your audio…",
                    "A bandmate is here. WebJam is reconnecting this Mac.",
                    invite_available=invite_available,
                    action_visible=False,
                )
            elif bandmates:
                total = len(participants)
                self.window.session_hud.set_state(
                    "Ready to play" if self._local_audio_seen else "Bandmate connected",
                    self._connected_audio_detail(total),
                    invite_available=invite_available,
                    action_visible=False,
                    ready=self._local_audio_seen,
                )
            else:
                detail = (
                    (
                        "Your input is detected. Invite a bandmate on the same Wi-Fi."
                        if remote_owner is None and self._local_audio_seen
                        else "Your input is detected. Invite your bandmate."
                        if self._local_audio_seen
                        else "Play a note to check your input, then invite your bandmate."
                    )
                    if connected
                    else (
                        "Share this link with a bandmate on the same Wi-Fi."
                        if remote_owner is None
                        else "Share this private link with your bandmate."
                    )
                )
                self.window.session_hud.set_state(
                    "Ready to share",
                    detail,
                    invite_available=invite_available,
                    action_visible=False,
                    ready=connected and self._local_audio_seen,
                )
            return

        if connected:
            count = len(participants)
            self.window.session_hud.set_state(
                "You’re ready" if self._local_audio_seen else "You’re connected",
                self._connected_audio_detail(count),
                ready=self._local_audio_seen,
            )
        elif self.bridge.jamulus_state in {
            "Stopped",
            "Launch failed",
            "Not found",
            "Port in use",
        }:
            self.window.session_hud.set_state(
                "Something needs attention",
                "Use Try Again below to join the jam again.",
                action_visible=False,
            )
        else:
            self.window.session_hud.set_state(
                "Joining your jam…",
                "WebJam is connecting your music.",
            )

    def _session_conductor_facts(self) -> SessionConductorFacts:
        """Collect authoritative facts for the pure musician-facing conductor.

        This adapter intentionally maps only bounded state from the existing
        service owners.  It does not call a provider, inspect a path, or turn
        a button press into recording/connection success.
        """

        hosting = bool(getattr(self.settings, "host_server_enabled", False))
        practice = bool(getattr(getattr(self, "bridge", None), "practice_mode", False))
        role = (
            SessionRole.PRACTICE
            if practice
            else SessionRole.HOST
            if hosting
            else SessionRole.GUEST
        )
        bridge = getattr(self, "bridge", None)
        audio = getattr(self, "audio", None)
        lifecycle = getattr(getattr(self, "session_lifecycle", None), "snapshot", None)
        lifecycle_phase = getattr(lifecycle, "phase", SessionLifecyclePhase.IDLE)
        connected = bool(getattr(audio, "connected", False))
        if connected:
            self._conductor_had_authenticated_connection = True

        jamulus_state = str(getattr(bridge, "jamulus_state", "") or "")
        terminal_jamulus_states = {
            "Stopped",
            "Launch failed",
            "Not found",
            "Port in use",
        }
        launch_intended = bool(getattr(bridge, "jamulus_launch_intended", False))
        setup_requested = bool(
            getattr(self, "_conductor_setup_requested", False)
            or launch_intended
            or connected
            or bool(getattr(audio, "recovering", False))
            or bool(getattr(self, "_band_check_start_pending", False))
            or lifecycle_phase is not SessionLifecyclePhase.IDLE
            or getattr(self, "_remote_invitation", None) is not None
            # A host-side v3 owner is explicit session intent too. Its
            # invitation may already be valid before this Mac's Jamulus
            # client reconnects, so keep the truthful Copy Invite action
            # available rather than falling back to an idle lobby.
            or getattr(self, "_remote_invite_owner", None) is not None
        )

        if bool(getattr(audio, "recovering", False)):
            music_path = MusicPathState.RECONNECTING
        elif connected:
            # AudioCoordinator promotes this only after fresh roster truth,
            # including the local host entry when hosting.
            music_path = MusicPathState.AUTHENTICATED
        elif launch_intended or jamulus_state in {"Running", "Already running"}:
            music_path = MusicPathState.STARTING
        elif self._conductor_had_authenticated_connection:
            music_path = MusicPathState.DISCONNECTED
        elif jamulus_state in terminal_jamulus_states and setup_requested:
            music_path = MusicPathState.FAILED
        else:
            music_path = MusicPathState.NOT_STARTED

        server_alive = False
        if hosting and bridge is not None:
            try:
                server_alive = bool(bridge.hosted_server_alive())
            except Exception:  # noqa: BLE001 - a missing probe is not proof
                server_alive = False
        if server_alive:
            host_process = ProcessState.RUNNING
        elif hosting and launch_intended:
            host_process = ProcessState.STARTING
        elif hosting and jamulus_state in terminal_jamulus_states and setup_requested:
            host_process = ProcessState.FAILED
        else:
            host_process = ProcessState.NOT_STARTED

        # ``ensure_hosted_server`` only exposes an owned server after its
        # recorder RPC has authenticated; an adopted server follows the same
        # probe.  Treating that service fact as verified is therefore stronger
        # than merely observing a child PID.
        host_rpc = EvidenceState.VERIFIED if server_alive else EvidenceState.NOT_STARTED
        observed_invite = bool(getattr(self, "_last_observed_invite_available", False))
        host_listener = (
            EvidenceState.VERIFIED
            if observed_invite
            else EvidenceState.IN_PROGRESS
            if server_alive
            else EvidenceState.NOT_STARTED
        )
        invite = (
            EvidenceState.VERIFIED
            if observed_invite
            else EvidenceState.NOT_STARTED
        )

        participants = list(getattr(self, "participants", {}).values())
        remote_participant = (
            EvidenceState.VERIFIED
            if any(not self._is_local_participant(person) for person in participants)
            else EvidenceState.NOT_STARTED
        )
        local_participant = (
            EvidenceState.VERIFIED if connected else EvidenceState.NOT_STARTED
        )
        participant_identity = (
            EvidenceState.VERIFIED
            if remote_participant is EvidenceState.VERIFIED
            else EvidenceState.NOT_STARTED
        )

        remote_session = getattr(self, "_remote_session", None)
        remote_snapshot = getattr(remote_session, "snapshot", None)
        remote_phase = str(getattr(getattr(remote_snapshot, "phase", None), "value", ""))
        if remote_phase in {"preparing", "connecting"}:
            guest_enrollment = EvidenceState.IN_PROGRESS
        elif connected and not hosting:
            guest_enrollment = EvidenceState.VERIFIED
        elif remote_phase == "failed":
            guest_enrollment = EvidenceState.FAILED
        elif getattr(self, "_remote_invitation", None) is not None:
            guest_enrollment = EvidenceState.IN_PROGRESS
        else:
            guest_enrollment = EvidenceState.NOT_STARTED

        recorder = getattr(self, "recording", None)
        recorder_phase = str(
            getattr(getattr(recorder, "phase", None), "value", "idle") or "idle"
        )
        recorder_state = {
            "preflight": RecorderState.REQUESTED,
            "starting": RecorderState.STARTING,
            "recording": RecorderState.RECORDING,
            "stopping": RecorderState.STOPPING,
            "validating": RecorderState.STOPPED,
            "stop_failed": RecorderState.FAILED,
            "error": RecorderState.FAILED,
        }.get(recorder_phase, RecorderState.IDLE)
        validation = getattr(recorder, "last_validation", None)
        completed_take = getattr(recorder, "last_completed_take", None)
        if recorder_phase == "validating":
            take_validation = TakeValidationState.VALIDATING
        elif recorder_phase == "needs_attention":
            take_validation = TakeValidationState.NEEDS_ATTENTION
        elif validation is not None:
            take_validation = (
                TakeValidationState.VALID
                if bool(getattr(validation, "ok", False))
                else TakeValidationState.NEEDS_ATTENTION
            )
        elif recorder_phase == "complete" and completed_take is not None:
            take_validation = TakeValidationState.VALID
        else:
            take_validation = TakeValidationState.NOT_STARTED
        take_available = completed_take is not None
        media_preservation = (
            EvidenceState.VERIFIED
            if take_available
            else EvidenceState.UNKNOWN
            if recorder_state is RecorderState.FAILED
            else EvidenceState.NOT_REQUIRED
        )

        band_check = getattr(
            self, "_conductor_band_check", EvidenceState.NOT_STARTED
        )
        ready_dialog = getattr(self, "_ready_check_dialog", None)
        if ready_dialog is not None and bool(getattr(ready_dialog, "isVisible", lambda: False)()):
            band_check = EvidenceState.IN_PROGRESS
        elif bool(getattr(self, "_band_check_start_pending", False)):
            band_check = EvidenceState.IN_PROGRESS
        elif (
            band_check is EvidenceState.NOT_STARTED
            and (launch_intended or connected)
        ):
            # A live/launching attempt cannot be sent backwards into an
            # imaginary pre-session gate.  This means only that the old gate
            # is no longer the current action; it does not claim a saved
            # Band Check report or human audibility proof.
            band_check = EvidenceState.NOT_REQUIRED

        failure = FailureDisposition.NONE
        if bool(getattr(audio, "connection_timed_out", False)) or self._remote_join_retry_pending():
            failure = FailureDisposition.RETRYABLE
        elif bool(getattr(self, "_remote_invitation_requires_replacement", False)):
            failure = FailureDisposition.BLOCKED
        elif lifecycle_phase is SessionLifecyclePhase.FAILED_FINAL:
            failure = FailureDisposition.FINAL
        elif lifecycle_phase is SessionLifecyclePhase.FAILED_RECOVERABLE:
            failure = FailureDisposition.RETRYABLE

        cleanup = (
            CleanupState.ENDING
            if bool(getattr(audio, "stopping", False))
            or lifecycle_phase
            in {SessionLifecyclePhase.ENDING, SessionLifecyclePhase.FINALIZING_RECORDINGS}
            else CleanupState.COMPLETE
            if lifecycle_phase is SessionLifecyclePhase.COMPLETED
            else CleanupState.NOT_REQUESTED
        )
        return SessionConductorFacts(
            role=role,
            setup_requested=setup_requested,
            # WebJam has no separate identity wizard in the production path;
            # Band Check remains the real, explicit sound setup gate.
            identity=EvidenceState.NOT_REQUIRED,
            sound=EvidenceState.NOT_REQUIRED,
            band_check=band_check,
            host_server_process=host_process,
            host_server_rpc=host_rpc,
            host_listener=host_listener,
            invite=invite,
            guest_enrollment=guest_enrollment,
            music_path=music_path,
            local_participant=local_participant,
            remote_participant=remote_participant,
            participant_identity=participant_identity,
            had_authenticated_connection=bool(
                getattr(self, "_conductor_had_authenticated_connection", False)
            ),
            recorder=recorder_state,
            take_validation=take_validation,
            take_available=take_available,
            guest_media=GuestMediaState.NOT_EXPECTED,
            media_preservation=media_preservation,
            studio=(
                ReviewState.REVIEWING
                if bool(getattr(self, "_conductor_studio_reviewing", False))
                else ReviewState.IDLE
            ),
            export=getattr(self, "_conductor_export", ExportState.IDLE),
            cleanup=cleanup,
            failure=failure,
        )

    def _conductor_requires_legacy_copy(self, facts: SessionConductorFacts) -> bool:
        """Keep topology-specific recovery copy out of the generic conductor."""

        if self._remote_join_retry_pending() or bool(
            getattr(self, "_remote_invitation_requires_replacement", False)
        ):
            return True
        if bool(getattr(self, "_host_peer_warning", "")):
            return True
        try:
            from webjam_qt.platform_permissions import microphone_permission_status

            if (
                facts.music_path is not MusicPathState.AUTHENTICATED
                and microphone_permission_status() in {"denied", "restricted"}
            ):
                return True
        except Exception:  # noqa: BLE001 - no permission observation is not a block
            pass
        status_widget = getattr(getattr(self.window, "session_hud", None), "_status", None)
        status_text = str(getattr(status_widget, "text", lambda: "")())
        return status_text in {
            "Your Wi-Fi changed",
            "Create a fresh invitation",
        }

    @staticmethod
    def _conductor_stage_phase(phase: SessionConductorPhase) -> SessionPhase:
        if phase in {
            SessionConductorPhase.RECONNECTING,
            SessionConductorPhase.FAILED,
            SessionConductorPhase.BLOCKED,
            SessionConductorPhase.INDETERMINATE,
            SessionConductorPhase.TAKE_NEEDS_ATTENTION,
        }:
            return SessionPhase.ERROR
        if phase is SessionConductorPhase.ENDING:
            return SessionPhase.ENDING
        if phase in {
            SessionConductorPhase.STARTING_HOST,
            SessionConductorPhase.WAITING_FOR_HOST_READINESS,
            SessionConductorPhase.JOINING,
            SessionConductorPhase.BAND_CHECK_IN_PROGRESS,
            SessionConductorPhase.RECORDING_STARTING,
            SessionConductorPhase.RECORDING_STOPPING,
            SessionConductorPhase.TAKE_VALIDATING,
            SessionConductorPhase.GUEST_MEDIA_TRANSFERRING,
            SessionConductorPhase.EXPORTING,
        }:
            return SessionPhase.CONNECTING
        return SessionPhase.NOT_CONNECTED

    @staticmethod
    def _conductor_action_kind(action: SessionPrimaryAction) -> str:
        return {
            SessionPrimaryAction.START_SESSION: "start_session",
            SessionPrimaryAction.CONFIRM_SOUND: "confirm_sound",
            SessionPrimaryAction.RUN_BAND_CHECK: "run_band_check",
            SessionPrimaryAction.COPY_INVITE: "copy_invite",
            SessionPrimaryAction.TRY_RECONNECT: "try_reconnect",
            SessionPrimaryAction.RECORD: "record",
            SessionPrimaryAction.STOP_RECORDING: "stop_recording",
            SessionPrimaryAction.REVIEW_TAKE: "review_take",
            SessionPrimaryAction.EXPORT_TRACKS: "export_tracks",
            SessionPrimaryAction.END_SESSION: "end_session",
            SessionPrimaryAction.OPEN_DETAILS: "open_details",
            SessionPrimaryAction.CHECK_SESSION: "check_session",
        }.get(action, "primary")

    def _render_session_conductor(self) -> None:
        """Render the canonical conductor without replacing real UI evidence."""

        facts = self._session_conductor_facts()
        presentation = derive_session_conductor(facts)
        self._last_session_conductor = presentation
        self._record_pilot_conductor_presentation(presentation)
        if self._conductor_requires_legacy_copy(facts):
            return

        action = presentation.primary_action
        header_owned = action in {
            SessionPrimaryAction.RECORD,
            SessionPrimaryAction.STOP_RECORDING,
            SessionPrimaryAction.END_SESSION,
        }
        action_visible = action not in {
            SessionPrimaryAction.NONE,
            SessionPrimaryAction.WAIT,
        } and not header_owned
        detail = presentation.message
        if presentation.preservation:
            detail = f"{detail} {presentation.preservation}"
        # Keep the quiet lobby's useful one-line recording context even
        # though its Start button is intentionally hidden in favor of the
        # single HUD action.
        stage_hint = ""
        if presentation.phase is SessionConductorPhase.IDLE:
            stage_hint = (
                "Multitrack recording is ready on this Mac"
                if facts.role is SessionRole.HOST
                else "Your host records everyone as separate tracks"
            )
        self.window.session_hud.set_state(
            presentation.title,
            detail,
            invite_available=action is SessionPrimaryAction.COPY_INVITE,
            action_text=action.label,
            action_visible=action_visible,
            action_kind=self._conductor_action_kind(action),
            ready=presentation.phase
            in {SessionConductorPhase.LIVE, SessionConductorPhase.TAKE_READY},
        )
        # The HUD owns the focused action.  The empty stage stays explanatory
        # so an initial 760x600 window never presents a Start/Check/Practice
        # pile alongside the same action in the HUD.
        self.window.participant_grid.set_session_state(
            SessionUiState(
                self._conductor_stage_phase(presentation.phase),
                presentation.title,
                presentation.message,
                primary_text=action.label or "Continue",
                primary_enabled=presentation.primary_enabled,
                show_primary=False,
                show_ready_check=False,
                show_practice=False,
                hint=stage_hint,
                primary_action="start",
            )
        )
        # The strip copy button is an older duplicate of the HUD action.  Its
        # reset command remains under More when a v3 host owns that invite.
        if action is SessionPrimaryAction.COPY_INVITE:
            self.window.session_strip.set_invite_available(False)

    def _update_session_hud(self) -> None:
        """Refresh legacy exceptional copy, then the canonical conductor."""

        if getattr(self, "_startup_attempt", None) is not None:
            self._render_startup_journey()
            return
        self._update_session_hud_legacy()
        self._render_session_conductor()

    def _on_conductor_action_requested(self, action_kind: str) -> None:
        """Route the one visible conductor action to its real owner."""

        action = str(action_kind or "").strip().lower()
        if action == "native_setup_finished":
            self._finish_native_sound_setup()
        elif action == "sound_confirmed":
            self._confirm_startup_audible()
        elif action == "add_webex":
            self._show_startup_conversation_input()
        elif action == "save_webex":
            self._save_startup_webex_link()
        elif action == "retry_startup":
            self._retry_startup_journey()
        elif action == "enter_jam":
            self._enter_startup_jam()
        elif action in {"invite", "copy_invite"}:
            self._copy_band_invite()
        elif action in {"retry", "try_reconnect", "check_session"}:
            self._retry_session()
        elif action in {"primary", "start_session"}:
            self._on_session_audio_requested()
        elif action in {"confirm_sound", "run_band_check"}:
            self._open_band_check(start_session_when_ready=True)
        elif action in {"record", "stop_recording"}:
            self._on_record_requested()
        elif action == "review_take":
            self._on_rail_view_changed("takes")
        elif action == "export_tracks":
            export = getattr(self.window.recording_studio, "_export_tracks", None)
            if callable(export):
                export()
        elif action == "end_session":
            self._on_session_audio_requested()
        elif action == "open_details":
            self._on_ready_check()

    def _on_conductor_secondary_action_requested(self, action_kind: str) -> None:
        """Route quiet journey actions without adding a second primary path."""

        action = str(action_kind or "").strip().lower()
        if action in {"bring_jamulus", "fix_audio"}:
            self._bring_jamulus_forward()
            if action == "fix_audio":
                attempt = getattr(self, "_startup_attempt", None)
                if attempt is not None:
                    attempt["setup_finished"] = False
                    attempt["phase"] = "native_sound_setup"
                    self._render_startup_journey()
        elif action == "skip_webex":
            self._skip_startup_webex()
        elif action == "enter_jam":
            self._enter_startup_jam()
        elif action == "cancel_startup":
            self._cancel_startup_journey()

    def _on_recorder_phase_changed(self, _phase=None) -> None:
        """Refresh the conductor after recorder-owned state changes."""

        self._update_session_hud()

    def _on_studio_export_started(self) -> None:
        self._conductor_export = ExportState.EXPORTING
        self._update_session_hud()

    def _on_studio_export_finished(self, succeeded: bool) -> None:
        self._conductor_export = (
            ExportState.COMPLETE if succeeded else ExportState.NEEDS_ATTENTION
        )
        # Export callbacks can legitimately outlive an operator pausing or
        # completing Test Night. They still refresh ordinary musician UI, but
        # must not mutate a paused or completed pilot ledger.
        if getattr(self, "_pilot_run_state", "not_started") != "running":
            self._update_session_hud()
            return
        from core.pilot_evidence import (
            EvidenceOutcome,
            EvidenceReference,
            PilotObservationClass,
        )

        self._pilot_append_automatic(
            PilotObservationClass.TRACK_EXPORT,
            EvidenceOutcome.VERIFIED if succeeded else EvidenceOutcome.FAILED,
            state_after=self._pilot_state_from_presentation(),
            evidence_reference=EvidenceReference.EXPORT_MANIFEST,
        )
        self._update_session_hud()

    def _reset_session_conductor_attempt(self) -> None:
        """Forget live-attempt facts after owned cleanup reaches idle.

        Completed-take facts remain with RecordingCoordinator so Studio can
        still offer honest review after a session ends.
        """

        self._conductor_setup_requested = False
        self._conductor_band_check = EvidenceState.NOT_STARTED
        self._conductor_had_authenticated_connection = False
        self._conductor_studio_reviewing = False
        self._conductor_export = ExportState.IDLE

    # ------------------------------------------------------------------
    # Operator-only closed-pilot evidence
    # ------------------------------------------------------------------
    def _pilot_storage_dir(self) -> Path:
        """Return the local-only application-support root for pilot records."""

        from core.settings import webjam_application_support_dir

        return webjam_application_support_dir() / "Pilot"

    def _pilot_role(self):
        from core.pilot_evidence import PilotRole

        facts = self._session_conductor_facts()
        return PilotRole.GUEST if facts.role is SessionRole.GUEST else PilotRole.HOST

    @staticmethod
    def _pilot_outcome_key(value) -> str:
        """Turn a bounded ledger result into a dialog status key."""

        return str(getattr(value, "value", value) or "waiting").lower().replace(
            " ", "_"
        )

    def _pilot_state_from_presentation(self, presentation=None):
        from core.pilot_evidence import PilotSessionState

        if presentation is None:
            presentation = getattr(self, "_last_session_conductor", None)
        value = str(getattr(getattr(presentation, "phase", None), "value", ""))
        try:
            return PilotSessionState(value)
        except ValueError:
            return PilotSessionState.INDETERMINATE

    def _pilot_current_state(self):
        from core.pilot_evidence import PilotSessionState

        ledger = getattr(self, "_pilot_ledger", None)
        if ledger is not None and ledger.events:
            return ledger.events[-1].state_after
        return PilotSessionState.IDLE

    def _pilot_refresh_dialog(self) -> None:
        dialog = getattr(self, "_test_night_dialog", None)
        if dialog is None:
            return
        dialog.set_run_state(self._pilot_run_state)
        dialog.set_check_statuses(self._pilot_check_status)
        dialog.set_export_available(getattr(self, "_pilot_ledger", None) is not None)

    def _pilot_append_automatic(
        self,
        observation_class,
        outcome,
        *,
        state_after,
        evidence_reference,
        limitations=(),
    ) -> bool:
        """Append one bounded automatic fact without exposing implementation data."""

        from core.pilot_evidence import PilotEvidenceError, save_pilot_ledger

        ledger = getattr(self, "_pilot_ledger", None)
        if ledger is None:
            return False
        try:
            updated = ledger.record_observation(
                observation_class,
                outcome,
                state_before=self._pilot_current_state(),
                state_after=state_after,
                evidence_reference=evidence_reference,
                limitations=limitations,
            )
            save_pilot_ledger(self._pilot_storage_dir(), updated)
        except (OSError, PilotEvidenceError):
            LOGGER.warning("Could not append private pilot evidence", exc_info=True)
            if not getattr(self, "_shutdown", False):
                self.window.flash_message(
                    "WebJam couldn't save the local pilot record. The session is unchanged.",
                    ms=7000,
                )
            return False
        self._pilot_ledger = updated
        return True

    def _pilot_append_human(
        self,
        observation_class,
        outcome,
        *,
        state_after,
        limitations=(),
    ) -> bool:
        """Append an explicit operator assertion; never infer it from meters."""

        from core.pilot_evidence import PilotEvidenceError, save_pilot_ledger

        ledger = getattr(self, "_pilot_ledger", None)
        if ledger is None:
            return False
        try:
            updated = ledger.record_human_observation(
                observation_class,
                outcome,
                state_before=self._pilot_current_state(),
                state_after=state_after,
                limitations=limitations,
            )
            save_pilot_ledger(self._pilot_storage_dir(), updated)
        except (OSError, PilotEvidenceError):
            LOGGER.warning("Could not append human pilot evidence", exc_info=True)
            if not getattr(self, "_shutdown", False):
                self.window.flash_message(
                    "WebJam couldn't save the local pilot record. The session is unchanged.",
                    ms=7000,
                )
            return False
        self._pilot_ledger = updated
        return True

    def _pilot_rebuild_check_statuses(self) -> None:
        """Restore fixed dialog rows from allowlisted ledger events only."""

        from core.pilot_evidence import PilotObservationClass

        mapping = {
            PilotObservationClass.CONNECTION: "connection_truth",
            PilotObservationClass.PARTICIPANT_PRESENCE: "connection_truth",
            PilotObservationClass.RECORDING_REQUEST: "record_take",
            PilotObservationClass.RECORDER_CONFIRMATION: "record_take",
            PilotObservationClass.RECORDING_STOP: "record_take",
            PilotObservationClass.TAKE_VALIDATION: "validate_take",
            PilotObservationClass.STUDIO_SIDECAR: "studio_playback",
            PilotObservationClass.OWNED_PROCESS_CLEANUP: "closeout",
            PilotObservationClass.RECONNECTION: "failure_recovery",
            PilotObservationClass.HUMAN_HOST_HEARD_BANDMATE: "hear_each_other",
            PilotObservationClass.HUMAN_BANDMATE_HEARD_HOST: "hear_each_other",
            PilotObservationClass.HUMAN_HEADPHONES_CORRECT: "headphones_correct",
            PilotObservationClass.HUMAN_SESSION_PLAYABLE: "session_playable",
            PilotObservationClass.HUMAN_STUDIO_PLAYBACK: "studio_playback",
            PilotObservationClass.HUMAN_STUDIO_ALIGNMENT: "studio_alignment",
            PilotObservationClass.HUMAN_REHEARSAL_USEFUL: "rehearsal_moment_useful",
        }
        statuses: dict[str, str] = {}
        ledger = getattr(self, "_pilot_ledger", None)
        for event in getattr(ledger, "events", ()):
            key = mapping.get(event.observation_class)
            if key:
                statuses[key] = self._pilot_outcome_key(event.result)
        self._pilot_check_status = statuses

    def _pilot_restore_latest(self) -> bool:
        """Offer the most recent durable run for resume after an app restart."""

        from core.pilot_evidence import (
            PilotEvidenceError,
            PilotObservationClass,
            list_pilot_ledgers,
        )

        if getattr(self, "_pilot_ledger", None) is not None:
            return True
        try:
            ledgers = list_pilot_ledgers(self._pilot_storage_dir())
        except PilotEvidenceError:
            LOGGER.warning("Could not inspect local pilot evidence", exc_info=True)
            self.window.flash_message(
                "WebJam couldn't open the local pilot record. Start a new Test Night run.",
                ms=7000,
            )
            return False
        if not ledgers:
            return False
        ledger = ledgers[0]
        self._pilot_ledger = ledger
        last = ledger.events[-1] if ledger.events else None
        if last is not None and last.observation_class is PilotObservationClass.PILOT_ABANDONED:
            self._pilot_run_state = "abandoned"
        elif (
            last is not None
            and last.observation_class is PilotObservationClass.OWNED_PROCESS_CLEANUP
            and self._pilot_outcome_key(last.result) == "verified"
        ):
            self._pilot_run_state = "completed"
        else:
            # A running ledger recovered after process exit is deliberately
            # paused until the operator explicitly resumes it.
            self._pilot_run_state = "paused"
        self._pilot_rebuild_check_statuses()
        return True

    def _open_test_night(self) -> None:
        """Open the hidden operator workflow only when explicitly launched."""

        if not self._operator_mode:
            return
        from webjam_qt.windows.test_night import TestNightDialog

        dialog = getattr(self, "_test_night_dialog", None)
        if dialog is not None and dialog.isVisible():
            dialog.raise_()
            dialog.activateWindow()
            return
        self._pilot_restore_latest()
        dialog = TestNightDialog(self.window)
        dialog.start_requested.connect(self._start_test_night)
        dialog.pause_requested.connect(self._pause_test_night)
        dialog.resume_requested.connect(self._resume_test_night)
        dialog.abandon_requested.connect(self._abandon_test_night)
        dialog.restart_requested.connect(self._restart_test_night)
        dialog.manual_outcome_requested.connect(self._record_test_night_manual_outcome)
        dialog.export_report_requested.connect(self._export_test_night_report)
        dialog.finished.connect(lambda _result: setattr(self, "_test_night_dialog", None))
        self._test_night_dialog = dialog
        self._pilot_refresh_dialog()
        dialog.show()

    def _start_test_night(self) -> None:
        """Create a new local-only ledger and record package availability."""

        from core.build_info import build_id
        from core.pilot_evidence import (
            EvidenceLimitation,
            EvidenceOutcome,
            EvidenceReference,
            PilotEvidenceError,
            PilotObservationClass,
            create_pilot_ledger,
            save_pilot_ledger,
        )
        from webjam_qt import __version__

        if getattr(self, "_pilot_ledger", None) is not None:
            if self._pilot_run_state == "paused":
                self._resume_test_night()
            return
        artifact_identity = "not_available"
        if getattr(sys, "frozen", False) and sys.platform == "darwin":
            artifact_identity = f"webjam-v{__version__}-test-night-macos-arm64"
        try:
            ledger = create_pilot_ledger(
                app_version=__version__,
                build_commit=build_id() or "not_available",
                artifact_identity=artifact_identity,
                role=self._pilot_role(),
            )
            save_pilot_ledger(self._pilot_storage_dir(), ledger)
        except (OSError, PilotEvidenceError):
            LOGGER.warning("Could not create local pilot evidence", exc_info=True)
            self.window.flash_message(
                "WebJam couldn't start the local Test Night record.", ms=7000
            )
            return
        self._pilot_ledger = ledger
        self._pilot_run_state = "running"
        self._pilot_check_status = {}
        current_state = self._pilot_state_from_presentation()
        self._pilot_append_automatic(
            PilotObservationClass.APP_LAUNCHED,
            EvidenceOutcome.VERIFIED,
            state_after=current_state,
            evidence_reference=EvidenceReference.PACKAGE_METADATA,
        )
        self._pilot_append_automatic(
            PilotObservationClass.PACKAGE_IDENTITY,
            (
                EvidenceOutcome.VERIFIED
                if artifact_identity != "not_available"
                else EvidenceOutcome.NOT_AVAILABLE
            ),
            state_after=current_state,
            evidence_reference=EvidenceReference.PACKAGE_METADATA,
            limitations=(
                ()
                if artifact_identity != "not_available"
                else (EvidenceLimitation.PARTIAL_EVIDENCE,)
            ),
        )
        self._pilot_last_conductor_phase = ""
        self._pilot_refresh_dialog()
        self._record_pilot_conductor_presentation(self._last_session_conductor)

    def _pause_test_night(self) -> None:
        from core.pilot_evidence import (
            EvidenceOutcome,
            EvidenceReference,
            PilotObservationClass,
            PilotSessionState,
        )

        if (
            getattr(self, "_pilot_run_state", "not_started") != "running"
            or getattr(self, "_pilot_ledger", None) is None
        ):
            return
        if self._pilot_append_automatic(
            PilotObservationClass.PILOT_PAUSED,
            EvidenceOutcome.VERIFIED,
            state_after=PilotSessionState.PAUSED,
            evidence_reference=EvidenceReference.SESSION_STATE,
        ):
            self._pilot_run_state = "paused"
            self._pilot_refresh_dialog()

    def _resume_test_night(self) -> None:
        from core.pilot_evidence import (
            EvidenceOutcome,
            EvidenceReference,
            PilotObservationClass,
        )

        if self._pilot_run_state != "paused" or self._pilot_ledger is None:
            return
        target = self._pilot_state_from_presentation()
        if self._pilot_append_automatic(
            PilotObservationClass.PILOT_RESUMED,
            EvidenceOutcome.VERIFIED,
            state_after=target,
            evidence_reference=EvidenceReference.SESSION_STATE,
        ):
            self._pilot_run_state = "running"
            self._pilot_last_conductor_phase = ""
            self._pilot_refresh_dialog()
            self._record_pilot_conductor_presentation(self._last_session_conductor)

    def _abandon_test_night(self) -> None:
        from core.pilot_evidence import (
            EvidenceLimitation,
            EvidenceOutcome,
            EvidenceReference,
            PilotObservationClass,
            PilotSessionState,
        )

        if self._pilot_run_state not in {"running", "paused"} or self._pilot_ledger is None:
            return
        if self._pilot_append_automatic(
            PilotObservationClass.PILOT_ABANDONED,
            EvidenceOutcome.NOT_RUN,
            state_after=PilotSessionState.ABANDONED,
            evidence_reference=EvidenceReference.SESSION_STATE,
            limitations=(EvidenceLimitation.HARDWARE_NOT_EXERCISED,),
        ):
            self._pilot_run_state = "abandoned"
            self._pilot_refresh_dialog()

    def _restart_test_night(self) -> None:
        if self._pilot_run_state not in {"abandoned", "completed"}:
            return
        self._pilot_ledger = None
        self._pilot_run_state = "not_started"
        self._pilot_check_status = {}
        self._pilot_last_conductor_phase = ""
        self._start_test_night()

    def _record_test_night_manual_outcome(self, key: str, outcome_key: str) -> None:
        """Record one selected human result under the explicit human API."""

        from core.pilot_evidence import (
            EvidenceLimitation,
            EvidenceOutcome,
            PilotObservationClass,
        )

        if (
            getattr(self, "_pilot_run_state", "not_started") != "running"
            or getattr(self, "_pilot_ledger", None) is None
        ):
            return
        outcomes = {
            "verified": EvidenceOutcome.VERIFIED,
            "failed": EvidenceOutcome.FAILED,
            "blocked": EvidenceOutcome.BLOCKED,
            "not_run": EvidenceOutcome.NOT_RUN,
            "indeterminate": EvidenceOutcome.INDETERMINATE,
        }
        outcome = outcomes.get(str(outcome_key).strip().lower())
        if outcome is None:
            return
        observations = {
            "hear_each_other": (
                PilotObservationClass.HUMAN_HOST_HEARD_BANDMATE,
                PilotObservationClass.HUMAN_BANDMATE_HEARD_HOST,
            ),
            "headphones_correct": (PilotObservationClass.HUMAN_HEADPHONES_CORRECT,),
            "session_playable": (PilotObservationClass.HUMAN_SESSION_PLAYABLE,),
            "studio_playback": (PilotObservationClass.HUMAN_STUDIO_PLAYBACK,),
            "studio_alignment": (PilotObservationClass.HUMAN_STUDIO_ALIGNMENT,),
            "rehearsal_moment_useful": (
                PilotObservationClass.HUMAN_REHEARSAL_USEFUL,
            ),
        }.get(str(key).strip(), ())
        if not observations:
            return
        limitations = (
            (EvidenceLimitation.HARDWARE_NOT_EXERCISED,)
            if outcome in {EvidenceOutcome.NOT_RUN, EvidenceOutcome.BLOCKED}
            else ()
        )
        target = self._pilot_state_from_presentation()
        if all(
            self._pilot_append_human(
                observation,
                outcome,
                state_after=target,
                limitations=limitations,
            )
            for observation in observations
        ):
            self._pilot_check_status[str(key)] = self._pilot_outcome_key(outcome)
            self._pilot_refresh_dialog()

    def _record_pilot_conductor_presentation(self, presentation) -> None:
        """Record one automatic fact after a real conductor phase transition."""

        from core.pilot_evidence import (
            EvidenceOutcome,
            EvidenceReference,
            PilotObservationClass,
        )

        if (
            getattr(self, "_pilot_run_state", "not_started") != "running"
            or getattr(self, "_pilot_ledger", None) is None
        ):
            return
        phase = str(getattr(getattr(presentation, "phase", None), "value", ""))
        if not phase or phase == self._pilot_last_conductor_phase:
            return
        self._pilot_last_conductor_phase = phase
        target = self._pilot_state_from_presentation(presentation)
        event = None
        status_key = None
        if phase == SessionConductorPhase.BAND_CHECK_IN_PROGRESS.value:
            event = (
                PilotObservationClass.BAND_CHECK,
                EvidenceOutcome.INDETERMINATE,
                EvidenceReference.BAND_CHECK_RESULT,
            )
        elif phase == SessionConductorPhase.READY_TO_START.value:
            event = (
                PilotObservationClass.BAND_CHECK,
                EvidenceOutcome.VERIFIED,
                EvidenceReference.BAND_CHECK_RESULT,
            )
        elif phase in {
            SessionConductorPhase.STARTING_HOST.value,
            SessionConductorPhase.WAITING_FOR_HOST_READINESS.value,
        }:
            event = (
                PilotObservationClass.SERVER_AUTHENTICATION,
                EvidenceOutcome.INDETERMINATE,
                EvidenceReference.SESSION_STATE,
            )
        elif phase == SessionConductorPhase.INVITE_READY.value:
            event = (
                PilotObservationClass.INVITE_AVAILABILITY,
                EvidenceOutcome.VERIFIED,
                EvidenceReference.SESSION_STATE,
            )
        elif phase in {
            SessionConductorPhase.CONNECTED.value,
            SessionConductorPhase.LIVE.value,
        }:
            event = (
                PilotObservationClass.CONNECTION,
                EvidenceOutcome.VERIFIED,
                EvidenceReference.SESSION_STATE,
            )
            status_key = "connection_truth"
        elif phase == SessionConductorPhase.RECONNECTING.value:
            event = (
                PilotObservationClass.RECONNECTION,
                EvidenceOutcome.INDETERMINATE,
                EvidenceReference.SESSION_STATE,
            )
            status_key = "failure_recovery"
        elif phase == SessionConductorPhase.RECORDING_STARTING.value:
            event = (
                PilotObservationClass.RECORDING_REQUEST,
                EvidenceOutcome.VERIFIED,
                EvidenceReference.RECORDER_STATE,
            )
            status_key = "record_take"
        elif phase == SessionConductorPhase.RECORDING.value:
            event = (
                PilotObservationClass.RECORDER_CONFIRMATION,
                EvidenceOutcome.VERIFIED,
                EvidenceReference.RECORDER_STATE,
            )
            status_key = "record_take"
        elif phase == SessionConductorPhase.RECORDING_STOPPING.value:
            event = (
                PilotObservationClass.RECORDING_STOP,
                EvidenceOutcome.INDETERMINATE,
                EvidenceReference.RECORDER_STATE,
            )
        elif phase == SessionConductorPhase.TAKE_VALIDATING.value:
            event = (
                PilotObservationClass.TAKE_VALIDATION,
                EvidenceOutcome.INDETERMINATE,
                EvidenceReference.TAKE_MANIFEST,
            )
            status_key = "validate_take"
        elif phase == SessionConductorPhase.TAKE_READY.value:
            event = (
                PilotObservationClass.TAKE_VALIDATION,
                EvidenceOutcome.VERIFIED,
                EvidenceReference.TAKE_MANIFEST,
            )
            status_key = "validate_take"
        elif phase == SessionConductorPhase.TAKE_NEEDS_ATTENTION.value:
            event = (
                PilotObservationClass.TAKE_VALIDATION,
                EvidenceOutcome.FAILED,
                EvidenceReference.TAKE_MANIFEST,
            )
            status_key = "validate_take"
        elif phase == SessionConductorPhase.REVIEWING.value:
            event = (
                PilotObservationClass.STUDIO_SIDECAR,
                EvidenceOutcome.VERIFIED,
                EvidenceReference.STUDIO_STATE,
            )
        elif phase == SessionConductorPhase.EXPORTING.value:
            event = (
                PilotObservationClass.TRACK_EXPORT,
                EvidenceOutcome.INDETERMINATE,
                EvidenceReference.EXPORT_MANIFEST,
            )
        elif phase == SessionConductorPhase.FAILED.value:
            event = (
                PilotObservationClass.CONNECTION,
                EvidenceOutcome.FAILED,
                EvidenceReference.SESSION_STATE,
            )
            status_key = "connection_truth"
        if event and self._pilot_append_automatic(
            event[0],
            event[1],
            state_after=target,
            evidence_reference=event[2],
        ):
            if status_key:
                self._pilot_check_status[status_key] = self._pilot_outcome_key(event[1])
            self._pilot_refresh_dialog()

    def _record_pilot_lifecycle_completion(self, phase: SessionLifecyclePhase) -> None:
        """Capture the one cleanup verdict that can precede the idle reset."""

        from core.pilot_evidence import (
            EvidenceOutcome,
            EvidenceReference,
            PilotObservationClass,
            PilotSessionState,
        )

        if (
            getattr(self, "_pilot_run_state", "not_started") != "running"
            or getattr(self, "_pilot_ledger", None) is None
        ):
            return
        if phase is SessionLifecyclePhase.ENDING:
            self._pilot_append_automatic(
                PilotObservationClass.OWNED_PROCESS_CLEANUP,
                EvidenceOutcome.INDETERMINATE,
                state_after=PilotSessionState.ENDING,
                evidence_reference=EvidenceReference.PROCESS_CLEANUP,
            )
        elif phase is SessionLifecyclePhase.COMPLETED:
            if self._pilot_append_automatic(
                PilotObservationClass.OWNED_PROCESS_CLEANUP,
                EvidenceOutcome.VERIFIED,
                state_after=PilotSessionState.ENDED,
                evidence_reference=EvidenceReference.PROCESS_CLEANUP,
            ):
                self._pilot_check_status["closeout"] = "verified"
                self._pilot_run_state = "completed"
                self._pilot_refresh_dialog()

    def _export_test_night_report(self) -> None:
        """Write an allowlisted report only after an operator asks for it."""

        from PySide6.QtWidgets import QFileDialog

        from core.file_io import atomic_write_text
        from core.pilot_evidence import build_sanitized_pilot_report
        import json

        ledger = getattr(self, "_pilot_ledger", None)
        if ledger is None or getattr(self, "_pilot_run_state", "not_started") not in {
            "paused",
            "abandoned",
            "completed",
        }:
            return
        destination = QFileDialog.getExistingDirectory(
            self.window,
            "Export WebJam Test Night report",
        )
        if not destination:
            return
        try:
            report = build_sanitized_pilot_report(ledger)
            atomic_write_text(
                Path(destination) / "WebJam-private-pilot-report.json",
                json.dumps(report, indent=2, sort_keys=True) + "\n",
                mode=0o600,
            )
        except (OSError, ValueError):
            LOGGER.warning("Could not export local pilot report", exc_info=True)
            self.window.flash_message("WebJam couldn't export the pilot report.", ms=7000)
            return
        self.window.flash_message("Private pilot report exported.", ms=5000)

    def _record_toggle_worker(self, target_armed: bool, secret_file: str) -> None:
        self.recording.toggle_worker(target_armed, secret_file)

    def _apply_record_toggle_result(self, armed: bool) -> None:
        self.recording.apply_toggle_result(armed)

    def _apply_record_toggle_failure(self, message: str) -> None:
        self.recording.apply_toggle_failure(message)

    def _on_practice_requested(self) -> None:
        self._conductor_setup_requested = True
        self.audio.on_practice_requested()

    def _use_system_input(self) -> None:
        """Compatibility shim: live device changes belong to Jamulus."""

        self._bring_jamulus_forward()

    def _open_microphone_settings(self) -> None:
        """Open the only advanced surface needed to recover a TCC denial."""
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        opened = QDesktopServices.openUrl(
            QUrl(
                "x-apple.systempreferences:com.apple.preference.security"
                "?Privacy_Microphone"
            )
        )
        self.window.participant_grid.set_session_state(
            SessionUiState.permission_retry()
        )
        self.window.session_hud.set_state(
            "Allow microphone access, then return",
            "When WebJam is enabled in System Settings, choose Try Again below.",
        )
        if not opened:
            self.window.flash_message(
                "Open System Settings → Privacy & Security → Microphone.",
                ms=7000,
            )

    def _stop_audio(self) -> None:
        """Confirm with the user, then stop Jamulus and reset UI state."""
        self.audio.stop()

    def _reset_to_demo_state(self) -> None:
        """Compatibility entry point: reset to truthful disconnected state."""
        self.audio.reset_to_idle()

    def _is_jamulus_running(self) -> bool:
        return self.bridge.jamulus_state in ("Running", "Already running")

    def _on_join_video(self) -> None:
        """Open the configured meeting externally without claiming join state."""
        from core.webex_url import normalize_webex_url, webex_url_error

        url = normalize_webex_url(self.settings.webex_url)
        if not url:
            self._show_actionable_error(
                "No Meeting URL",
                what_failed="No Webex meeting URL is configured.",
                likely_cause="A meeting link hasn't been entered yet.",
                next_action="Go to Settings and enter your Webex meeting link.",
            )
            return
        error = webex_url_error(url)
        if error:
            self._show_actionable_error(
                "Invalid Webex URL",
                what_failed="WebJam will not open this meeting link.",
                likely_cause=error,
                next_action="Open Settings and paste the HTTPS webex.com meeting link.",
            )
            return

        self.webex.meeting_url = url
        self.bridge.webex_state = "Opening…"
        self.window.set_status_video("Opening…")
        self.window.session_strip.set_video_state("Opening…", enabled=False)
        self.window.webex_embed.set_launch_status("Opening…")
        self.bridge.launch_webex(manual=True)

    def _is_video_active(self) -> bool:
        """Return whether an external launch is in progress or succeeded.

        This deliberately does not mean "in a meeting"; native Webex does not
        expose that truth to this local application.
        """
        return self.bridge.webex_state in ("Opening…", "Opened externally")

    def _leave_video(self) -> None:
        """Compatibility entry point; WebJam cannot close external Webex."""
        self.window.flash_message(
            "Close or leave the meeting in Webex. WebJam does not control it.",
            ms=5000,
        )

    def _on_webex_state(self, state: str) -> None:
        """Ignore obsolete embedded-meeting callbacks.

        Kept for one compatibility cycle so a late signal from an old widget
        cannot overwrite the truthful external-launch state.
        """
        LOGGER.debug("Ignoring obsolete embedded Webex state: %s", state)

    # ------------------------------------------------------------------
    # Mixer card handlers → JamulusController
    # ------------------------------------------------------------------
    def _on_fader_changed(self, channel_id: int, level: int) -> None:
        p = self.participants.get(channel_id)
        if p is not None:
            p.fader_level = level
        self._mix_dirty = True
        if self._jamulus_connected:
            self.jamulus.set_fader_level(channel_id, level)

    def _on_mute_toggled(self, channel_id: int, muted: bool) -> None:
        p = self.participants.get(channel_id)
        if p is not None:
            p.muted = muted
        self._mix_dirty = True
        if self._jamulus_connected:
            self.jamulus.set_mute(channel_id, muted)

    def _on_solo_toggled(self, channel_id: int, solo: bool) -> None:
        p = self.participants.get(channel_id)
        if p is not None:
            p.solo = solo
        self._mix_dirty = True
        if self._jamulus_connected:
            self.jamulus.set_solo(channel_id, solo)

    # ------------------------------------------------------------------
    # BridgeService callbacks (already on UI thread via invoker)
    # ------------------------------------------------------------------
    def _set_status_banner(self, text: str, color: str | None = None) -> None:
        self.window.flash_message(text, color=color)

    def _refresh_readiness(self) -> None:
        # A launched process is not the same as a proven Jamulus session.  Keep
        # the "Running" wording out of the UI until participant/RPC truth has
        # arrived; the button can still offer Stop Audio for a live subprocess.
        jamulus_up = self.bridge.jamulus_state in ("Running", "Already running")
        terminal_states = {"Stopped", "Launch failed", "Not found", "Port in use"}
        rpc = getattr(self.jamulus, "rpc_client", None)
        self.session_health.mark_process(
            self.bridge.jamulus_state,
            rpc_available=bool(getattr(rpc, "available", False)),
        )
        audio_state = self.bridge.jamulus_state
        if jamulus_up:
            if self.bridge.practice_mode:
                audio_state = (
                    "Practice live" if self._jamulus_connected else "Practice starting…"
                )
            else:
                audio_state = "Connected" if self._jamulus_connected else "Connecting…"
        else:
            self.session_health.reset_live_truth()
            if self.bridge.jamulus_state in terminal_states:
                self._connection_timer.stop()
        if getattr(self.settings, "host_server_enabled", False):
            if self.bridge.hosted_server_owned():
                server_state = "Hosting"
            elif self.bridge.hosted_server_adopted():
                server_state = "Connected"
            else:
                server_state = "not running"
            self.window.set_status_server(server_state)
        else:
            self.window.set_status_server("")
        self.audio.on_readiness_refresh(jamulus_up)
        if (
            not jamulus_up
            and self.bridge.jamulus_state in terminal_states
            and not self.audio.ended_by_user
            and not self.audio.stopping
            and not self._remote_join_retry_pending()
            and not bool(
                getattr(self, "_remote_invitation_requires_replacement", False)
            )
        ):
            self.audio.recovering = False
            from webjam_qt.platform_permissions import microphone_permission_status

            state = (
                SessionUiState.permission_denied()
                if microphone_permission_status() in {"denied", "restricted"}
                else self._connection_failure_state()
            )
            self.window.participant_grid.set_session_state(state)
        # Settings and Troubleshooting remain available precisely when a
        # connection is slow or failed.
        self.window.session_strip.set_tools_enabled(True)
        self.window.set_status_audio(audio_state)
        self.window.set_status_video(self.bridge.webex_state)
        if jamulus_up:
            audio_action = (
                "End Session"
                if bool(getattr(self.settings, "host_server_enabled", False))
                else "Leave Jam"
            )
        elif self.audio.stopping:
            audio_action = (
                "Ending…"
                if bool(getattr(self.settings, "host_server_enabled", False))
                else "Leaving…"
            )
        else:
            audio_action = "Start Session"
        self.window.session_strip.set_audio_state(
            audio_action, enabled=not self.audio.stopping
        )
        if self.bridge.webex_state == "Opening…":
            webex_label, enabled = "Opening…", False
        elif self.bridge.webex_state == "Opened externally":
            webex_label, enabled = "Open Again", True
        else:
            webex_label, enabled = "Open Webex", True
        self.window.session_strip.set_video_state(webex_label, enabled=enabled)
        self.window.session_strip.set_video_configured(
            bool(str(self.settings.webex_url or "").strip())
        )
        try:
            self.window.webex_embed.set_launch_status(self.bridge.webex_state)
        except AttributeError:
            pass
        self._update_session_hud()

    def _show_actionable_error(
        self,
        title: str,
        *,
        what_failed: str,
        likely_cause: str,
        next_action: str,
        retry_callback=None,
        copy_text: str = "",
    ) -> None:
        from core.redaction import redact_text

        def safe_text(value: str, fallback: str) -> str:
            import re

            cleaned = " ".join(str(value or "").split())
            cleaned = redact_text(cleaned)
            cleaned = re.sub(
                r"\$HOME(?:[/\\][^\s,;]+)+|"
                r"(?<![:\\/\w])/(?:[^/\s]+/)*[^,;:\s]+|"
                r"(?i:(?<![\w])(?:[a-z]:\\|\\\\)[^\r\n,;]+)",
                "[private path]",
                cleaned,
            )
            return cleaned[:600] or fallback

        # Keep infrastructure out of the first layer. Qt's built-in Details
        # disclosure retains a bounded, redacted diagnosis without exposing
        # filesystem paths, invite material, device command lines, or secrets.
        box = QMessageBox(self.window)
        box.setWindowTitle("Something needs attention")
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.setText(safe_text(what_failed, "WebJam needs attention."))
        box.setInformativeText(
            safe_text(next_action, "Close this message and try again.")
        )
        box.setDetailedText(
            safe_text(title, "WebJam")
            + "\n\nLikely cause: "
            + safe_text(likely_cause, "The session setup changed or is unavailable.")
            + "\n\nIf this repeats, open Band Check and choose Save Support Bundle."
        )
        box.setIcon(QMessageBox.Icon.Warning)
        retry_btn = None
        settings_btn = None
        copy_btn = None
        if retry_callback:
            retry_btn = box.addButton("Try Again", QMessageBox.ButtonRole.AcceptRole)
        elif "settings" in str(next_action).lower():
            settings_btn = box.addButton(
                "Open Settings", QMessageBox.ButtonRole.AcceptRole
            )
        elif copy_text:
            copy_btn = box.addButton(
                "Copy Meeting Link", QMessageBox.ButtonRole.ActionRole
            )
        box.addButton("Close", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if retry_btn is not None and clicked is retry_btn:
            try:
                retry_callback()
            except Exception:  # noqa: BLE001
                LOGGER.exception("Retry callback failed")
        elif settings_btn is not None and clicked is settings_btn:
            self._open_settings_wizard()
        elif copy_btn is not None and clicked is copy_btn:
            from PySide6.QtWidgets import QApplication

            QApplication.clipboard().setText(copy_text)

    def _show_message(self, title: str, message: str) -> None:
        QMessageBox.information(self.window, title, message)

    # ------------------------------------------------------------------
    # Auto-reconnect
    # ------------------------------------------------------------------
    def _revalidate_after_wake_gap(self) -> None:
        """Drop stale live truth after a long event-loop pause.

        A Mac can sleep while the Jamulus process object still exists. Process
        existence alone is not connection evidence after wake, so a delayed
        reconnect timer clears the roster/connected claim and waits for fresh
        RPC/roster evidence. This is intentionally conservative and portable;
        it does not claim to distinguish every platform sleep notification.
        """

        now = time.monotonic()
        previous = float(getattr(self, "_last_reconnect_tick_monotonic", now))
        self._last_reconnect_tick_monotonic = now
        if now - previous < self._WAKE_REVALIDATION_GAP_SECONDS:
            return
        if (
            self._shutdown
            or self.audio.stopping
            or not self.bridge.jamulus_launch_intended
            or not self._jamulus_connected
        ):
            return
        self.audio.connected = False
        self.audio.recovering = True
        self._local_audio_seen = False
        self._remote_audio_seen = False
        self.participants.clear()
        self._push_participants_to_grid()
        self.window.set_status_audio("Checking connection…")
        self.window.set_status_latency("Checking after wake")
        self.window.participant_grid.set_session_state(SessionUiState.reconnecting())
        self._transition_lifecycle(
            SessionLifecyclePhase.RECONNECTING,
            "WebJam is rechecking the music connection after a long pause",
        )
        self._connection_timer.start()
        try:
            self.metrics.increment("metric_session_wake_revalidation")
        except Exception:  # noqa: BLE001
            LOGGER.debug("wake revalidation metric failed", exc_info=True)

    def _on_reconnect_tick(self) -> None:
        """Called every 3 s; lets BridgeService retry dropped services.

        Also detects Jamulus crashes mid-session and shows a banner so the
        conductor knows something is happening (auto-reconnect is otherwise
        invisible).
        """
        self._revalidate_after_wake_gap()

        # Detect Jamulus dying: launch was intended, process exists but exited.
        proc = self.bridge.jamulus_process
        if (
            self.bridge.jamulus_launch_intended
            and proc is not None
            and proc.poll() is not None
            and not self._reconnect_banner_shown
        ):
            attempts = self.bridge.jamulus_reconnect_attempts + 1
            self.window.flash_message(
                f"Band audio disconnected — reconnecting (attempt {attempts}/5)…",
                ms=5000,
            )
            self.window.set_status_audio("Reconnecting…")
            self.audio.recovering = True
            self._local_audio_seen = False
            self._remote_audio_seen = False
            self.participants.clear()
            self._push_participants_to_grid()
            self.window.participant_grid.set_session_state(
                SessionUiState.reconnecting(attempts)
            )
            self._transition_lifecycle(
                SessionLifecyclePhase.RECONNECTING,
                "The music engine exited and WebJam is retrying",
                recovery_attempt=attempts,
            )
            self._connection_timer.start()
            self._reconnect_banner_shown = True
        elif (
            self.bridge.jamulus_state in ("Running", "Already running")
            and self._reconnect_banner_shown
            and self._jamulus_connected
        ):
            # A process restart is not success. Proven roster/RPC truth is.
            self._reconnect_banner_shown = False
            self._reconnect_gave_up = False
            self.audio.recovering = False
            self.window.flash_message("Band audio reconnected.", ms=3000)
        elif (
            self.bridge.jamulus_launch_intended
            and proc is not None
            and proc.poll() is not None
            and self.bridge.jamulus_reconnect_attempts >= 5
            and not getattr(self, "_reconnect_gave_up", False)
        ):
            # Auto-reconnect exhausted its 5 attempts and the process is still
            # dead. Tell the user once, and stop showing "Reconnecting…"
            # forever (which the crash branch above would otherwise leave up).
            self._reconnect_gave_up = True
            self.window.set_status_audio("Not connected")
            self.window.session_strip.set_audio_state("Start Session", enabled=True)
            self.window.participant_grid.set_session_state(
                SessionUiState.reconnect_failed()
            )
            self._transition_lifecycle(
                SessionLifecyclePhase.FAILED_RECOVERABLE,
                "Automatic reconnect attempts were exhausted",
                recovery_attempt=self.bridge.jamulus_reconnect_attempts,
            )
            self.window.flash_message(
                "Couldn't reconnect after 5 tries — press Start Session to try again.",
                ms=8000,
            )

        # Detect RPC hang: process is alive AND was previously responsive
        # (we got past _jamulus_connected=True) AND the RPC heartbeat hasn't
        # fired in a while.  Distinct from a crash (proc.poll != None) — here
        # the process is still alive but unresponsive.
        if self._jamulus_connected and proc is not None and proc.poll() is None:
            try:
                age = self.jamulus.rpc_client.last_activity_age()
            except AttributeError:
                age = 0.0
            if age > self._RPC_HANG_THRESHOLD_S and not self._rpc_hang_banner_shown:
                self.window.flash_message(
                    f"The music engine stopped responding ({int(age)}s of silence). "
                    "WebJam is preparing a safe retry.",
                    ms=8000,
                )
                self.window.set_status_audio("Not responding")
                self._rpc_hang_banner_shown = True
                self.audio.connected = False
                self.audio.recovering = True
                self._local_audio_seen = False
                self._remote_audio_seen = False
                self.participants.clear()
                self._push_participants_to_grid()
                self.window.participant_grid.set_session_state(
                    SessionUiState.reconnecting()
                )
                self._transition_lifecycle(
                    SessionLifecyclePhase.DEGRADED,
                    "The music engine stopped responding",
                )
                self.window.session_hud.set_state(
                    "Connection interrupted",
                    "The music engine stopped responding. WebJam is preparing a safe retry.",
                )
                self._connection_timer.start()
                try:
                    self.metrics.increment("metric_jamulus_hang_detected")
                except Exception:  # noqa: BLE001
                    LOGGER.debug("hang metric failed", exc_info=True)
            elif age <= self._RPC_HANG_THRESHOLD_S and self._rpc_hang_banner_shown:
                self._rpc_hang_banner_shown = False
                self.window.flash_message(
                    "The music engine is responding again.", ms=3000
                )

        self.bridge.attempt_auto_reconnects()
        # A private LAN address may change on Wi-Fi roaming, sleep/wake, or
        # interface changes without killing the local Jamulus process. Polling
        # the small, fail-closed pre-share check keeps an old copied link from
        # silently looking current; it never claims Internet reachability.
        if bool(getattr(self.settings, "host_server_enabled", False)):
            self._update_session_hud()

    def _on_token_refresh_tick(self) -> None:
        """Compatibility no-op: native Webex owns its authentication."""

    # ------------------------------------------------------------------
    # Save / Load mix (Ctrl+S / Ctrl+O)
    # ------------------------------------------------------------------
    def _on_mute_all(self) -> None:
        """Ctrl+M — toggle mute state for every participant.

        If any channel is unmuted, mute all. If all are already muted, unmute
        all.  Applies to soloed channels too — a panic "mute everything" must
        actually silence the room, solo or not.
        """
        if not self.participants:
            return
        any_unmuted = any(not p.muted for p in self.participants.values())
        target_muted = (
            any_unmuted  # mute all if anything is playing; unmute if all silent
        )
        for channel_id, p in self.participants.items():
            if p.muted != target_muted:
                p.muted = target_muted
                if self._jamulus_connected:
                    self.jamulus.set_mute(channel_id, target_muted)
        self._mix_dirty = True
        # Push updated state to grid so buttons reflect the change
        self._push_participants_to_grid()
        verb = "Muted" if target_muted else "Unmuted"
        self.window.flash_message(f"{verb} all participants  ·  Ctrl+M to toggle")

    def _on_reset_all_faders(self) -> None:
        """Ctrl+Shift+R — reset every participant's fader to 0 dB (level 100).

        Asks for confirmation since it's a destructive bulk action.  The
        saved mix on disk is unchanged — user can Ctrl+O to restore.
        """
        reply = QMessageBox.question(
            self.window,
            "Reset all faders?",
            "Reset all faders to 0 dB?\n\nYour saved mix on disk is unchanged.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        for channel_id, p in self.participants.items():
            p.fader_level = 100
            if self._jamulus_connected:
                self.jamulus.set_fader_level(channel_id, 100)
        self._mix_dirty = True
        self._push_participants_to_grid()
        self.window.flash_message(
            "All faders reset to 0 dB  ·  Ctrl+O to restore your saved mix",
            ms=4000,
        )

    def _on_export_diagnostics(self) -> None:
        """Ctrl+Shift+D — build a Markdown diagnostics summary and copy it
        to the system clipboard so the user can paste it into a GitHub issue.
        """
        try:
            from PySide6.QtWidgets import QApplication

            exporter = self._diagnostics_exporter()
            summary = exporter.build_summary()
            QApplication.clipboard().setText(summary)
            self.window.flash_message(
                "Diagnostics copied to clipboard — paste into a GitHub issue at "
                "github.com/rupret007/webjam/issues",
                ms=8000,
            )
        except Exception:  # noqa: BLE001
            LOGGER.exception("Failed to export diagnostics")
            self.window.flash_message(
                "Couldn't export diagnostics. Open Band Check and save a Support "
                "Bundle if this repeats.",
                ms=6000,
            )

    def _diagnostics_exporter(self):
        """Build one canonical privacy-safe artifact adapter."""
        from core.build_info import build_id
        from webjam_qt import __version__
        from webjam_qt.controllers.diagnostics import DiagnosticsExporter

        return DiagnosticsExporter(
            settings=self.settings,
            bridge=self.bridge,
            jamulus_controller=self.jamulus,
            window_version=__version__,
            build_id=build_id(),
            session_health=self.session_health,
            session_lifecycle=self.session_lifecycle,
            recording_coordinator=self.recording,
            metrics_service=self.metrics,
        )

    def _on_save_support_bundle(self) -> None:
        """Preview the exact sanitized artifact, then save it on confirmation."""
        try:
            from pathlib import Path

            from PySide6.QtWidgets import QFileDialog

            from webjam_qt.windows.support_bundle_preview import (
                SupportBundlePreviewDialog,
            )

            exporter = self._diagnostics_exporter()
            preview = exporter.build_preview()
            dialog = SupportBundlePreviewDialog(preview, parent=self.window)
            if dialog.exec() != SupportBundlePreviewDialog.DialogCode.Accepted:
                return
            default = Path.home() / "Documents" / "webjam_support.zip"
            selected, _filter = QFileDialog.getSaveFileName(
                self.window,
                "Save Support Bundle",
                str(default),
                "ZIP archives (*.zip)",
            )
            if not selected:
                return
            requested = Path(selected).expanduser()
            saved = exporter.save_bundle(requested.parent, requested.name)
            self.window.flash_message(
                f"Support bundle saved as {saved.name}.",
                ms=7000,
            )
        except Exception:  # noqa: BLE001
            LOGGER.exception("Failed to save support bundle")
            QMessageBox.warning(
                self.window,
                "Support bundle not saved",
                "WebJam couldn't save the support bundle. Choose another folder "
                "and try again.",
            )

    # Public method names retained as a thin compatibility surface; the real
    # implementation lives in ``MixManager`` (~/.webjam_mix.json).
    def _on_save_mix(self) -> None:
        """Serialize current mixer state to ~/.webjam_mix.json."""
        # Only clear the dirty flag if the write actually succeeded.  If the
        # save failed (permissions, disk full), keeping it dirty preserves the
        # shutdown auto-save safety net so mid-session tweaks aren't lost.
        if self._mix_manager.save():
            self._mix_dirty = False

    def _on_load_mix(self) -> None:
        """Load mixer state from ~/.webjam_mix.json and apply to Jamulus."""
        self._mix_manager.load()

    def _on_save_mix_as(self) -> None:
        """Ctrl+Shift+S — open a Save dialog and write the mix to a chosen path.

        Lets users keep multiple named mixes (one per song, per band-mate
        setup, etc.) instead of overwriting the single default slot.
        """
        from pathlib import Path
        from PySide6.QtWidgets import QFileDialog

        path, _ = QFileDialog.getSaveFileName(
            self.window,
            "Save Mix As...",
            str(Path.home()),
            "Mix files (*.json);;All files (*)",
        )
        if not path:
            return
        # Treat a successful explicit "Save As" as a checkpoint of the current
        # state, the same way Ctrl+S does — but only if the write succeeded.
        if self._mix_manager.save_to(Path(path)):
            self._mix_dirty = False

    def _on_load_mix_from(self) -> None:
        """Ctrl+Shift+O — open a Load dialog and apply the chosen mix file."""
        from pathlib import Path
        from PySide6.QtWidgets import QFileDialog

        path, _ = QFileDialog.getOpenFileName(
            self.window,
            "Load Mix...",
            str(Path.home()),
            "Mix files (*.json);;All files (*)",
        )
        if not path:
            return
        self._mix_manager.load_from(Path(path))

    def _restore_saved_mix(self) -> None:
        """Auto-apply ~/.webjam_mix.json when Jamulus first connects (best-effort)."""
        self._mix_manager.auto_restore()

    # ------------------------------------------------------------------
    # Settings wizard (Phase 6)
    # ------------------------------------------------------------------
    def _reconfigure_services_after_settings(self, old_settings: AppSettings) -> None:
        """Apply freshly saved settings to all long-lived integration objects."""
        self.bridge.settings = self.settings

        # Webex browser fallback controller is long-lived; keep its meeting URL
        # in sync with the settings object used by the embedded pane.
        self.webex.meeting_url = self.settings.webex_url
        self.bridge.webex_controller = self.webex
        self._talk_break_intended = False
        self._self_transmit_muted = False
        self.window.session_strip.set_video_configured(
            bool(str(self.settings.webex_url or "").strip())
        )
        self.window.webex_embed.set_audio_mode(self._webex_audio_mode())
        self._start_routing_scan()

        # JamulusController owns RPC, its dormant legacy adapter, and the
        # audio-engine settings.
        # Reconfigure in place so BridgeService, MixManager, and tests keep the
        # same object identity while still seeing the new target.
        self.jamulus.settings = self.settings
        self.jamulus.host = (self.settings.jamulus_server or "").strip() or "127.0.0.1"
        self.jamulus.port = self.settings.jamulus_port
        self.jamulus.rpc_port = self.settings.jamulus_rpc_port
        protocol = getattr(self.jamulus, "protocol", None)
        if protocol is not None:
            protocol.host = self.jamulus.host
            protocol.port = self.jamulus.port
        audio_engine = getattr(self.jamulus, "audio_engine", None)
        if audio_engine is not None:
            audio_engine.settings = self.settings

        old_rpc_port = getattr(old_settings, "jamulus_rpc_port", None)
        if old_rpc_port != self.settings.jamulus_rpc_port:
            old_rpc = getattr(self.jamulus, "rpc_client", None)
            if old_rpc is not None:
                try:
                    old_rpc.stop()
                except Exception:  # noqa: BLE001
                    LOGGER.debug("Old Jamulus RPC client stop failed", exc_info=True)
            from core.jamulus_rpc_client import JamulusRpcClient

            self.jamulus.rpc_client = JamulusRpcClient(
                port=self.settings.jamulus_rpc_port,
                on_participants_changed=self.jamulus._on_rpc_participants,
                on_levels=self.jamulus._on_rpc_levels,
                on_chat=self.jamulus._on_rpc_chat,
                on_recorder_state=self.jamulus._on_rpc_recorder_state,
            )

        self.bridge.jamulus_controller = self.jamulus
        self._mix_manager._jamulus = self.jamulus

        api_port_changed = self.api_bridge.port != self.settings.companion_api_port
        api_enabled_changed = (
            old_settings.companion_api_enabled != self.settings.companion_api_enabled
        )
        was_api_running = bool(getattr(self.api_bridge, "_running", False))
        if (
            api_port_changed
            or api_enabled_changed
            or not self.settings.companion_api_enabled
        ):
            try:
                self.api_bridge.stop()
            except Exception:  # noqa: BLE001
                LOGGER.debug(
                    "Companion API stop during settings apply failed", exc_info=True
                )
        self.api_bridge.port = self.settings.companion_api_port
        if self.settings.companion_api_enabled and (
            api_enabled_changed or api_port_changed or was_api_running
        ):
            self.start_companion_api()

    def _open_settings_wizard(self) -> None:
        from webjam_qt.windows.simple_settings import SimpleSettingsDialog

        # Snapshot relevant fields before the wizard so we can detect changes.
        old_settings = self.settings
        old_webex_url = self.settings.webex_url
        old_jamulus_server = (self.settings.jamulus_server, self.settings.jamulus_port)
        # In-session reopen — skip the welcome page since the user already
        # knows what WebJam is and is here to change a specific setting.
        wizard = SimpleSettingsDialog(self.settings, parent=self.window)
        wizard.audio_settings_requested.connect(self._bring_jamulus_forward)
        if wizard.exec() == SimpleSettingsDialog.DialogCode.Accepted:
            from core.settings import load_settings

            run_band_check = getattr(wizard, "run_band_check_after_save", False) is True
            reopen_band_check, reopen_start_when_ready = self._replace_settings_object(
                load_settings(self.settings.config_file)
            )
            self._reconfigure_services_after_settings(old_settings)
            self.window.recording_studio.set_takes_directory(
                self.settings.takes_directory
            )
            self._sync_local_originals_action()
            self.window.recording_studio.set_output_device(
                self.settings.take_playback_output_device
            )
            hosting = bool(getattr(self.settings, "host_server_enabled", False))
            self.window.recording_studio.set_can_record(
                hosting,
                self._guest_recording_reason() if not hosting else "",
            )
            self._update_session_hud()

            self._reopen_invalidated_band_check(
                reopen_band_check, reopen_start_when_ready
            )

            if run_band_check:
                self.window.flash_message(
                    "Settings saved. Band Check is using this setup."
                )
                if not reopen_band_check:
                    QTimer.singleShot(0, self._on_ready_check)
                return

            # Build a context-aware confirmation message so the user knows
            # whether they need to take any action for the change to apply.
            warnings: list[str] = []
            if self.settings.webex_url != old_webex_url and self._is_video_active():
                warnings.append("Press Open Again to use the new Webex URL.")
            if (
                self.settings.jamulus_server,
                self.settings.jamulus_port,
            ) != old_jamulus_server and self._is_jamulus_running():
                warnings.append("End and restart the session to use the new band host.")

            if warnings:
                self.window.flash_message(
                    "Settings saved. " + " ".join(warnings),
                    ms=8000,
                )
            else:
                self.window.flash_message(
                    "Settings saved — they take effect next time you start the session."
                )

    def _open_take_deck(self) -> None:
        """Compatibility entry point: reveal the integrated Studio workspace."""
        self.window.side_rail.set_active_key("takes")
        self._on_rail_view_changed("takes")

    def _open_recording_setup(self) -> None:
        """Open the focused Studio preferences without exposing RPC plumbing."""
        from core.settings import load_settings
        from webjam_qt.windows.recording_setup import RecordingSetupDialog

        local_originals_available = self._local_originals_available()
        session_running = self._is_jamulus_running()
        takes_folder_editable = not session_running and not bool(
            getattr(self.host_peer, "active", False)
        )
        old_settings = self.settings
        settings_path = self.settings.config_file
        retained_invite = getattr(self, "_guest_invite", None)
        dialog = RecordingSetupDialog(
            self.settings,
            parent=self.window,
            local_originals_available=local_originals_available,
            takes_folder_editable=takes_folder_editable,
        )
        if dialog.exec() != RecordingSetupDialog.DialogCode.Accepted:
            return
        reopen_band_check, reopen_start_when_ready = self._replace_settings_object(
            load_settings(settings_path)
        )
        self._reconfigure_services_after_settings(old_settings)
        if (
            not session_running
            and retained_invite is not None
            and self.settings.takes_directory != old_settings.takes_directory
        ):
            # GuestPeerSession owns a concrete transfer queue/root. Rebuild it
            # before Start so Local Originals and Studio agree on the newly
            # committed folder.
            self._configure_guest_peer(retained_invite)
        self._reopen_invalidated_band_check(reopen_band_check, reopen_start_when_ready)
        self.window.recording_studio.set_takes_directory(self.settings.takes_directory)
        self._sync_local_originals_action()
        self.window.recording_studio.set_output_device(
            self.settings.take_playback_output_device
        )
        capture = bool(
            local_originals_available and self.settings.local_capture_enabled
        )
        if local_originals_available:
            message = (
                "Recording setup saved · local originals will be kept for the "
                "next confirmed take."
                if capture
                else "Recording setup saved · local originals are off."
            )
        else:
            message = (
                "Recording setup saved · this session keeps the host's "
                "server track, but local originals are unavailable."
            )
        self.window.flash_message(message, ms=7000)
        for participant in self.participants.values():
            if not participant.is_local:
                continue
            if self.guest_peer is not None:
                self.guest_peer.observe_presence(
                    participant.channel_id, participant.name
                )
            if self.host_peer.active:
                try:
                    self.host_peer.bind_host_presence(
                        participant.channel_id,
                        participant.name,
                        capture_enabled=capture,
                    )
                except Exception:  # noqa: BLE001
                    LOGGER.exception("Could not refresh local recording preference")

    def _save_take_playback_output(self, device_name: str) -> None:
        value = str(device_name or "")
        previous = self.settings.take_playback_output_device
        if value == previous:
            return
        self.settings.take_playback_output_device = value
        try:
            from core.settings import save_settings

            save_settings(self.settings)
        except Exception:  # noqa: BLE001
            self.settings.take_playback_output_device = previous
            LOGGER.exception("Failed to persist Take Deck output device")
            self.window.recording_studio.set_output_device(previous)
            self.window.flash_message(
                "WebJam couldn't change the playback output. The previous "
                "output is still selected.",
                ms=6000,
            )
            return
        reopen_band_check, reopen_start_when_ready = (
            self._invalidate_band_check_evidence()
        )
        self._reopen_invalidated_band_check(reopen_band_check, reopen_start_when_ready)

    def _on_rail_view_changed(self, key: str) -> None:
        splitter = self.window.center_splitter
        total = sum(splitter.sizes()) or self.window.DEFAULT_WIDTH

        # Keys that represent actual view changes (persist selection)
        _CONTENT_KEYS = frozenset({"stage", "canvas", "takes"})

        if key == "diagnostics":
            self._on_ready_check()
        elif key == "audio_settings":
            self._bring_jamulus_forward()
        elif key == "recording_setup":
            self._open_recording_setup()
        elif key == "support":
            self._on_save_support_bundle()
        elif key == "settings":
            # Restore rail to the previous content view before opening wizard
            prev = getattr(self, "_last_content_key", "stage")
            self.window.side_rail.set_active_key(prev)
            self._open_settings_wizard()
        elif key in _CONTENT_KEYS:
            self._last_content_key = key
            self._conductor_studio_reviewing = key == "takes"
            self.window.session_strip.set_recording_available(
                key != "takes"
                and bool(getattr(self.settings, "host_server_enabled", False))
                and bool(self._jamulus_connected)
            )
            if key == "stage":
                self.window.workspace_stack.setCurrentWidget(
                    self.window.center_splitter
                )
                self.window.session_canvas.setVisible(False)
                splitter.setSizes([total, 0])
            elif key == "canvas":
                self.window.workspace_stack.setCurrentWidget(
                    self.window.center_splitter
                )
                self.window.session_canvas.setVisible(True)
                # Canvas: expand the notes panel
                splitter.setSizes([int(total * 0.28), int(total * 0.72)])
            elif key == "takes":
                self.window.recording_studio.reload()
                self.window.workspace_stack.setCurrentWidget(
                    self.window.recording_studio
                )
            self._update_session_hud()

    # ------------------------------------------------------------------
    # Session notes persistence
    # ------------------------------------------------------------------
    # Session notes / metadata persistence
    # Public method names retained as a thin compatibility surface; the real
    # implementation lives in ``SessionPersistence`` (~/.webjam_notes.md and
    # ~/.webjam_session.json).
    def _load_notes(self) -> None:
        """Restore session notes from disk (best-effort)."""
        self._persistence._load_notes_only()

    def _save_notes(self) -> None:
        """Persist current session notes to disk (best-effort)."""
        self._persistence._save_notes_only()

    def _load_session_title(self) -> None:
        """Restore the session title and last-used mode from disk."""
        self._persistence._load_session_metadata()

    def _save_session_title(self) -> None:
        """Persist the current session title and mode to disk."""
        self._persistence._save_session_metadata()

    def _schedule_session_pulse_refresh(self, _notes: str) -> None:
        """Coalesce note edits before recalculating the local session pulse."""
        if not self._shutdown:
            self._pulse_refresh_timer.start()

    def _session_pulse_participants(self) -> list[ParticipantPresentation]:
        """Return confirmed, non-preview participants for the local pulse."""
        if not self._jamulus_connected:
            return []
        # Normal startup no longer creates previews, but retain the guard for
        # older extensions and restored in-memory fixtures.
        return [
            participant
            for participant in self._snapshot_participants()
            if not participant.role.startswith("Preview ·")
        ]

    def _refresh_session_pulse(self) -> None:
        """Rebuild the local pulse from the current UI session state."""
        try:
            pulse = build_session_pulse(
                mode_key=self.window.session_strip.current_mode_key(),
                title=self.window.session_strip.current_title(),
                notes=self.window.session_canvas.current_notes(),
                participants=self._session_pulse_participants(),
            )
            self.window.session_canvas.set_session_pulse(pulse)
        except Exception:  # noqa: BLE001
            # Never leave stale derived content beside newer raw notes. Brief
            # export then safely falls back to the notes themselves.
            self.window.session_canvas.clear_session_pulse()
            LOGGER.warning("Session pulse refresh failed", exc_info=True)

    # ------------------------------------------------------------------
    # Audio routing detection (Phase 5)
    # ------------------------------------------------------------------
    def _start_routing_scan(self) -> None:
        """Routing is automatic; keep infrastructure out of the main UI."""
        self.window.set_status_routing("")
        return

        def _scan() -> None:
            from core.audio_routing import AudioRoutingStatus, scan_loopback_devices

            try:
                status = scan_loopback_devices()
            except Exception as exc:  # noqa: BLE001
                # scan_loopback_devices() is contracted never to raise, but guard
                # anyway so an unexpected failure can't silently kill this thread
                # and leave the routing status blank forever.
                LOGGER.warning("routing scan failed: %s", exc, exc_info=True)
                status = AudioRoutingStatus(scan_error=str(exc))
            try:
                self._ui_invoker.invoke(lambda: self._apply_routing_status(status))
            except RuntimeError:
                # The Qt invoker was destroyed while we were scanning (app
                # shut down mid-scan).  Nobody is left to show the status —
                # drop it instead of dying with a traceback.
                LOGGER.debug("routing status dropped — UI already gone")

        threading.Thread(target=_scan, daemon=True, name="routing-scan").start()

    def _apply_routing_status(self, status) -> None:
        if self._webex_audio_mode() != "audience_bridge":
            self.window.set_status_routing("Not required")
            return
        if status.ok:
            label = f"{status.device_name} \u2713"
            self.window.set_status_routing(label)
            try:
                self.metrics.increment("metric_audio_device_blackhole_found")
            except Exception:  # noqa: BLE001
                LOGGER.debug("audio device metric failed", exc_info=True)
        else:
            self.window.set_status_routing("No audio device")
            self.window.flash_message(
                f"No virtual audio device found. {status.install_hint}",
                ms=8000,
            )
            try:
                self.metrics.increment("metric_audio_device_missing")
            except Exception:  # noqa: BLE001
                LOGGER.debug("audio device metric failed", exc_info=True)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def mode_entries() -> list[tuple[str, str]]:
        return [(mode.key, mode.label) for mode in CREATIVE_MODES]
