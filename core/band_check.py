"""Truthful, GUI-free state model for WebJam's musician-facing Band Check.

Band Check deliberately separates three kinds of evidence:

* configuration evidence (the engine binary and selected devices exist),
* local hardware evidence (WebJam can meter and record the selected input), and
* production-path evidence (Jamulus reports local send and remote receive audio).

The first two must never be promoted into a claim about the third.  The Qt
dialog is only a renderer/driver for this model; tests and future front ends can
exercise every transition without opening an audio device.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
import json
import hashlib
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Iterable, Mapping

from core.session_transport import ConnectionQuality, TransportPath


class BandCheckMode(str, Enum):
    """Whether Band Check may own temporary resources or only observe."""

    PRE_SESSION = "pre_session"
    LIVE_OBSERVE = "live_observe"


class BandCheckOutcome(str, Enum):
    READY = "Ready to Jam"
    WARNING = "Ready with a Warning"
    ACTION_NEEDED = "Action Needed"


class BandCheckStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PASS = "pass"
    WARNING = "warning"
    ACTION_NEEDED = "action_needed"
    NOT_APPLICABLE = "not_applicable"


class BandCheckStepKey(str, Enum):
    MUSIC_ENGINE = "music_engine"
    BAND_SERVER = "band_server"
    AUDIO_INPUT = "audio_input"
    HEADPHONES = "headphones"
    TEST_RECORDING = "test_recording"
    RECORDING_PATH = "recording_path"
    STUDIO = "studio"
    MUSIC_PATH = "music_path"
    WEBEX = "webex"


@dataclass(frozen=True)
class BandCheckStep:
    key: BandCheckStepKey
    title: str
    status: BandCheckStatus
    detail: str
    next_action: str = ""
    technical_details: tuple[str, ...] = ()
    required: bool = True

    @property
    def complete(self) -> bool:
        return self.status in {
            BandCheckStatus.PASS,
            BandCheckStatus.WARNING,
            BandCheckStatus.NOT_APPLICABLE,
        }


@dataclass(frozen=True)
class BandCheckObservations:
    """Read-only production observations supplied by the running controller.

    ``production_local_signal`` and ``production_remote_signal`` must come
    from the Jamulus control path (or an equivalent production-boundary
    diagnostic peer), never from WebJam's separate local sounddevice meter.
    None of the process, meter, signal, participant, or datagram fields is a
    claim that a human heard audio.  Only
    ``musician_confirmed_two_way_audibility`` carries that meaning.
    """

    music_engine_running: bool = False
    music_engine_responsive: bool = False
    band_server_running: bool | None = None
    recorder_ready: bool | None = None
    production_local_signal: bool = False
    production_remote_signal: bool = False
    peer_connected: bool = False
    local_meter_active: bool = False
    local_meter_rms: float = 0.0
    local_meter_peak: float = 0.0
    local_meter_clipped: bool = False
    transport_datagrams_flowed: bool = False
    remote_decoded_test_observed: bool = False
    musician_confirmed_two_way_audibility: bool | None = None
    connection_path: TransportPath | None = None
    connection_quality: ConnectionQuality = ConnectionQuality.UNKNOWN
    path_generation: int = 0

    def __post_init__(self) -> None:
        path = (
            None
            if self.connection_path is None
            else TransportPath(self.connection_path)
        )
        quality = ConnectionQuality(self.connection_quality)
        if (
            not isinstance(self.path_generation, int)
            or isinstance(self.path_generation, bool)
            or self.path_generation < 0
        ):
            raise ValueError("path_generation must be a non-negative integer")
        if path is not None and self.path_generation < 1:
            raise ValueError("a selected connection path requires a generation")
        if path is None and quality is not ConnectionQuality.UNKNOWN:
            raise ValueError("connection quality requires a selected path")
        if self.musician_confirmed_two_way_audibility is not None and not isinstance(
            self.musician_confirmed_two_way_audibility, bool
        ):
            raise ValueError("musician confirmation must be true, false, or absent")
        object.__setattr__(self, "connection_path", path)
        object.__setattr__(self, "connection_quality", quality)


@dataclass(frozen=True, slots=True)
class BandCheckEvidence:
    """Independent evidence facts for one Band Check session.

    The fields are deliberately not derivable from one another.  In
    particular, a running process, a responsive control channel, a participant,
    moving datagrams, and a decoded harness fixture remain weaker facts than a
    musician's explicit two-way hearing confirmation.
    """

    local_input_observed: bool = False
    local_output_confirmed: bool = False
    local_recording_heard: bool = False
    jamulus_process_started: bool = False
    jamulus_authenticated_responsive: bool = False
    remote_participant_appeared: bool = False
    transport_datagrams_flowed: bool = False
    production_local_signal_observed: bool = False
    production_remote_signal_observed: bool = False
    remote_decoded_test_observed: bool = False
    musician_confirmed_two_way_audibility: bool = False
    connection_path: TransportPath | None = None
    connection_quality: ConnectionQuality = ConnectionQuality.UNKNOWN
    path_generation: int = 0
    path_recheck_required: bool = False

    def __post_init__(self) -> None:
        path = (
            None
            if self.connection_path is None
            else TransportPath(self.connection_path)
        )
        quality = ConnectionQuality(self.connection_quality)
        if (
            not isinstance(self.path_generation, int)
            or isinstance(self.path_generation, bool)
            or self.path_generation < 0
        ):
            raise ValueError("path_generation must be a non-negative integer")
        if path is not None and self.path_generation < 1:
            raise ValueError("a selected connection path requires a generation")
        if path is None and quality is not ConnectionQuality.UNKNOWN:
            raise ValueError("connection quality requires a selected path")
        object.__setattr__(self, "connection_path", path)
        object.__setattr__(self, "connection_quality", quality)

    @property
    def path_label(self) -> str:
        if self.connection_path is None:
            return "The connection path is still being checked"
        return self.connection_path.musician_label

    @property
    def quality_label(self) -> str:
        return self.connection_quality.musician_label


@dataclass
class BandCheckSession:
    mode: BandCheckMode
    steps: list[BandCheckStep]
    input_rms: float = 0.0
    input_peak: float = 0.0
    input_clipped: bool = False
    manual_confirmations: set[str] = field(default_factory=set)
    evidence: BandCheckEvidence = field(default_factory=BandCheckEvidence)

    def step(self, key: BandCheckStepKey) -> BandCheckStep:
        return next(item for item in self.steps if item.key is key)

    def has_step(self, key: BandCheckStepKey) -> bool:
        return any(item.key is key for item in self.steps)

    def update_step(
        self,
        key: BandCheckStepKey,
        *,
        status: BandCheckStatus | None = None,
        detail: str | None = None,
        next_action: str | None = None,
        technical_details: Iterable[str] | None = None,
    ) -> None:
        for index, item in enumerate(self.steps):
            if item.key is not key:
                continue
            self.steps[index] = replace(
                item,
                status=item.status if status is None else status,
                detail=item.detail if detail is None else detail,
                next_action=item.next_action if next_action is None else next_action,
                technical_details=(
                    item.technical_details
                    if technical_details is None
                    else tuple(str(value) for value in technical_details)
                ),
            )
            return
        raise KeyError(key)

    def observe_input(self, *, rms: float, peak: float, clipped: bool) -> None:
        """Apply local-input meter evidence without claiming Jamulus routing."""

        self.input_rms = max(0.0, min(1.0, float(rms)))
        self.input_peak = max(0.0, min(1.0, float(peak)))
        self.input_clipped = bool(clipped or self.input_peak >= 0.99)
        if self.input_clipped:
            self.evidence = replace(self.evidence, local_input_observed=True)
            self.update_step(
                BandCheckStepKey.AUDIO_INPUT,
                status=BandCheckStatus.WARNING,
                detail="WebJam hears the input, but it is clipping. Turn the input gain down.",
                next_action="Turn the input gain down",
            )
        elif self.input_peak >= 0.02 or self.input_rms >= 0.005:
            self.evidence = replace(self.evidence, local_input_observed=True)
            self.update_step(
                BandCheckStepKey.AUDIO_INPUT,
                status=BandCheckStatus.PASS,
                detail=(
                    "WebJam can hear this input. The live music path is checked "
                    "separately when Jamulus reports it."
                ),
                next_action="",
            )
        else:
            self.update_step(
                BandCheckStepKey.AUDIO_INPUT,
                status=BandCheckStatus.RUNNING,
                detail="Play or sing now. No usable input level has appeared yet.",
                next_action="Play a note",
            )

    def confirm_headphones(
        self,
        heard: bool,
        *,
        stereo: bool = True,
    ) -> None:
        self.manual_confirmations.discard("headphones_left_right")
        self.manual_confirmations.discard("headphones_output")
        if heard:
            confirmation = "headphones_left_right" if stereo else "headphones_output"
            self.manual_confirmations.add(confirmation)
            self.evidence = replace(self.evidence, local_output_confirmed=True)
            self.update_step(
                BandCheckStepKey.HEADPHONES,
                status=BandCheckStatus.PASS,
                detail=(
                    "You confirmed the gentle left and right headphone test."
                    if stereo
                    else "You confirmed the gentle headphone output test."
                ),
                next_action="",
            )
        else:
            self.evidence = replace(self.evidence, local_output_confirmed=False)
            self.update_step(
                BandCheckStepKey.HEADPHONES,
                status=BandCheckStatus.ACTION_NEEDED,
                detail="The headphone test was not heard on both sides.",
                next_action="Check the output and try again",
            )

    def mark_scratch_recording(
        self,
        *,
        valid: bool,
        duration_s: float = 0.0,
        sample_rate: int = 0,
        channels: int = 0,
        has_signal: bool = False,
        detail: str = "",
    ) -> None:
        facts = (
            f"duration_s={duration_s:.3f}",
            f"sample_rate={sample_rate}",
            f"channels={channels}",
            f"has_signal={has_signal}",
        )
        if not valid:
            self.update_step(
                BandCheckStepKey.TEST_RECORDING,
                status=BandCheckStatus.ACTION_NEEDED,
                detail=detail
                or "The test recording could not be finalized and reopened.",
                next_action="Try the recording again",
                technical_details=facts,
            )
            self.update_step(
                BandCheckStepKey.RECORDING_PATH,
                status=BandCheckStatus.ACTION_NEEDED,
                detail="The recording path did not pass its write and reopen check.",
                next_action="Try the recording again",
                technical_details=facts,
            )
            return
        self.update_step(
            BandCheckStepKey.TEST_RECORDING,
            status=(
                BandCheckStatus.PENDING if has_signal else BandCheckStatus.ACTION_NEEDED
            ),
            detail=(
                "The five-second file was finalized and reopened. Play it, then "
                "confirm that it sounds right."
                if has_signal
                else "The file was created correctly, but it contains no usable input level."
            ),
            next_action=(
                "Play the recording"
                if has_signal
                else "Check the input and record again"
            ),
            technical_details=facts,
        )
        existing_recording_path = self.step(BandCheckStepKey.RECORDING_PATH)
        if existing_recording_path.status is BandCheckStatus.WARNING:
            # A successful five-second write proves this small file worked; it
            # cannot erase a preflight warning that the available reserve is
            # too small for a long rehearsal.
            self.update_step(
                BandCheckStepKey.RECORDING_PATH,
                status=BandCheckStatus.WARNING,
                detail=existing_recording_path.detail,
                next_action=existing_recording_path.next_action,
                technical_details=tuple(existing_recording_path.technical_details)
                + facts,
            )
        else:
            self.update_step(
                BandCheckStepKey.RECORDING_PATH,
                status=BandCheckStatus.PASS,
                detail="WebJam created, finalized, reopened, and read the test file.",
                next_action="",
                technical_details=facts,
            )
        self.update_step(
            BandCheckStepKey.STUDIO,
            status=BandCheckStatus.PASS,
            detail="The recording can be read and its waveform data can be generated.",
            next_action="",
            technical_details=facts,
        )

    def confirm_scratch_playback(self, sounds_right: bool) -> None:
        if sounds_right:
            self.manual_confirmations.add("scratch_sounds_right")
            self.evidence = replace(self.evidence, local_recording_heard=True)
            self.update_step(
                BandCheckStepKey.TEST_RECORDING,
                status=BandCheckStatus.PASS,
                detail="You confirmed that the isolated test recording sounds right.",
                next_action="",
            )
        else:
            self.manual_confirmations.discard("scratch_sounds_right")
            self.evidence = replace(self.evidence, local_recording_heard=False)
            self.update_step(
                BandCheckStepKey.TEST_RECORDING,
                status=BandCheckStatus.ACTION_NEEDED,
                detail="The test recording did not sound right.",
                next_action="Check the input and record again",
            )

    def mark_studio_check(self, *, valid: bool, detail: str = "") -> None:
        self.update_step(
            BandCheckStepKey.STUDIO,
            status=(BandCheckStatus.PASS if valid else BandCheckStatus.ACTION_NEEDED),
            detail=(
                "Studio opened the test media and exercised transport, mute, "
                "solo, gain, pan, seek, and resource release without changing it."
                if valid
                else detail or "Studio could not safely exercise the test recording."
            ),
            next_action="" if valid else "Try the recording again",
        )

    def apply_live_observations(self, observations: BandCheckObservations) -> None:
        """Refresh live truth without starting, stopping, or restarting anything."""

        if self.mode is not BandCheckMode.LIVE_OBSERVE:
            return
        if not isinstance(observations, BandCheckObservations):
            raise TypeError("live observations must be BandCheckObservations")
        self._merge_live_evidence(observations)
        if observations.local_meter_active:
            self.observe_input(
                rms=observations.local_meter_rms,
                peak=observations.local_meter_peak,
                clipped=observations.local_meter_clipped,
            )
        if (
            self.evidence.jamulus_process_started
            and self.evidence.jamulus_authenticated_responsive
        ):
            self.update_step(
                BandCheckStepKey.MUSIC_ENGINE,
                status=BandCheckStatus.PASS,
                detail=(
                    "The music engine started and its secure control check is "
                    "responding. The music path is checked separately."
                ),
                next_action="",
                technical_details=(
                    "process_started=true",
                    "authenticated_responsive=true",
                ),
            )
        elif self.evidence.jamulus_process_started:
            self.update_step(
                BandCheckStepKey.MUSIC_ENGINE,
                status=BandCheckStatus.ACTION_NEEDED,
                detail=(
                    "The music engine started, but its secure control check is not "
                    "responding. This does not prove that music is flowing."
                ),
                next_action="End the session, then start it again",
                technical_details=(
                    "process_started=true",
                    "authenticated_responsive=false",
                ),
            )
        else:
            self.update_step(
                BandCheckStepKey.MUSIC_ENGINE,
                status=BandCheckStatus.ACTION_NEEDED,
                detail="The music engine is not running.",
                next_action="Close Band Check and start the session",
                technical_details=(
                    "process_started=false",
                    "authenticated_responsive=false",
                ),
            )

        if self.has_step(BandCheckStepKey.MUSIC_PATH):
            self._refresh_music_path_step()

        if observations.band_server_running is not None and self.has_step(
            BandCheckStepKey.BAND_SERVER
        ):
            self.update_step(
                BandCheckStepKey.BAND_SERVER,
                status=(
                    BandCheckStatus.PASS
                    if observations.band_server_running
                    else BandCheckStatus.ACTION_NEEDED
                ),
                detail=(
                    "The hosted band server is running."
                    if observations.band_server_running
                    else "The hosted band server is not running."
                ),
                next_action=(
                    ""
                    if observations.band_server_running
                    else "End the session, then start it again"
                ),
            )
        if observations.recorder_ready is not None and self.has_step(
            BandCheckStepKey.RECORDING_PATH
        ):
            self.update_step(
                BandCheckStepKey.RECORDING_PATH,
                status=(
                    BandCheckStatus.PASS
                    if observations.recorder_ready
                    else BandCheckStatus.WARNING
                ),
                detail=(
                    "The live recording service is responding."
                    if observations.recorder_ready
                    else "The live recording service is not confirmed yet."
                ),
                next_action="",
            )

    def _merge_live_evidence(self, observations: BandCheckObservations) -> None:
        current = self.evidence
        authoritative_path = bool(
            observations.path_generation > 0 or observations.connection_path is not None
        )
        next_path = (
            observations.connection_path
            if authoritative_path
            else current.connection_path
        )
        next_quality = (
            observations.connection_quality
            if authoritative_path
            else current.connection_quality
        )
        next_generation = (
            observations.path_generation
            if authoritative_path
            else current.path_generation
        )
        generation_changed = bool(
            current.path_generation > 0
            and next_generation > 0
            and current.path_generation != next_generation
        )
        selected_path_changed = bool(
            authoritative_path
            and current.path_generation > 0
            and current.connection_path != next_path
        )
        material_path_changed = generation_changed or selected_path_changed

        datagrams = (
            False if material_path_changed else current.transport_datagrams_flowed
        )
        local_signal = (
            False if material_path_changed else current.production_local_signal_observed
        )
        remote_signal = (
            False
            if material_path_changed
            else current.production_remote_signal_observed
        )
        remote_decoded = (
            False if material_path_changed else current.remote_decoded_test_observed
        )
        musician_confirmed = (
            False
            if material_path_changed
            else current.musician_confirmed_two_way_audibility
        )
        recheck_required = current.path_recheck_required or material_path_changed
        if material_path_changed:
            self.manual_confirmations.discard("two_way_audibility")

        supplied_confirmation = observations.musician_confirmed_two_way_audibility
        if supplied_confirmation is not None:
            musician_confirmed = supplied_confirmation
            if supplied_confirmation:
                recheck_required = False
                self.manual_confirmations.add("two_way_audibility")
            else:
                self.manual_confirmations.discard("two_way_audibility")

        self.evidence = replace(
            current,
            jamulus_process_started=bool(observations.music_engine_running),
            jamulus_authenticated_responsive=bool(observations.music_engine_responsive),
            remote_participant_appeared=bool(observations.peer_connected),
            transport_datagrams_flowed=(
                datagrams or bool(observations.transport_datagrams_flowed)
            ),
            production_local_signal_observed=(
                local_signal or bool(observations.production_local_signal)
            ),
            production_remote_signal_observed=(
                remote_signal or bool(observations.production_remote_signal)
            ),
            remote_decoded_test_observed=(
                remote_decoded or bool(observations.remote_decoded_test_observed)
            ),
            musician_confirmed_two_way_audibility=musician_confirmed,
            connection_path=next_path,
            connection_quality=next_quality,
            path_generation=next_generation,
            path_recheck_required=recheck_required,
        )

    def confirm_two_way_audibility(self, heard: bool) -> None:
        """Record an explicit musician judgment for the current path generation."""

        if self.mode is not BandCheckMode.LIVE_OBSERVE or not self.has_step(
            BandCheckStepKey.MUSIC_PATH
        ):
            raise RuntimeError("two-way confirmation requires a live music path")
        if heard and not self.evidence.remote_participant_appeared:
            self.evidence = replace(
                self.evidence,
                musician_confirmed_two_way_audibility=False,
            )
            self.manual_confirmations.discard("two_way_audibility")
            self.update_step(
                BandCheckStepKey.MUSIC_PATH,
                status=BandCheckStatus.ACTION_NEEDED,
                detail=(
                    "No bandmate is connected to confirm with yet. Wait for them "
                    "to join, then play in both directions."
                ),
                next_action="Check Again",
                technical_details=self._music_path_facts(),
            )
            return
        self.evidence = replace(
            self.evidence,
            musician_confirmed_two_way_audibility=bool(heard),
            path_recheck_required=False
            if heard
            else self.evidence.path_recheck_required,
        )
        if heard:
            self.manual_confirmations.add("two_way_audibility")
            self._refresh_music_path_step()
        else:
            self.manual_confirmations.discard("two_way_audibility")
            self.update_step(
                BandCheckStepKey.MUSIC_PATH,
                status=BandCheckStatus.ACTION_NEEDED,
                detail=(
                    "You could not hear each other in both directions. Check the "
                    "music setup, then try the Band Check again."
                ),
                next_action="Check Again",
                technical_details=self._music_path_facts(),
            )

    def _refresh_music_path_step(self) -> None:
        evidence = self.evidence
        path_sentence = f"{evidence.path_label}."
        quality_sentence = f"{evidence.quality_label}."

        if evidence.connection_quality is ConnectionQuality.UNUSABLE:
            status = BandCheckStatus.ACTION_NEEDED
            detail = (
                f"{path_sentence} {quality_sentence} Wait for the connection to "
                "recover, then check both directions again."
            )
            action = "Check Again"
        elif not evidence.jamulus_authenticated_responsive:
            status = BandCheckStatus.WARNING
            detail = (
                "Waiting for the music engine's secure control check. No hearing "
                "claim can be made yet."
            )
            action = ""
        elif not evidence.remote_participant_appeared:
            status = BandCheckStatus.WARNING
            detail = (
                f"{path_sentence} Waiting for a bandmate to join. Music in both "
                "directions is not confirmed yet."
            )
            action = "Check Again"
        elif evidence.musician_confirmed_two_way_audibility:
            detail = (
                "You confirmed that you and your bandmate can hear each other in "
                f"both directions. {path_sentence} {quality_sentence}"
            )
            if evidence.connection_quality is ConnectionQuality.PLAYABLE:
                status = BandCheckStatus.PASS
                action = ""
            else:
                status = BandCheckStatus.WARNING
                action = "Check Again"
        elif evidence.path_recheck_required:
            status = BandCheckStatus.WARNING
            detail = (
                "The connection changed. Play in both directions again; moving "
                "music data does not carry over the earlier hearing confirmation. "
                f"{path_sentence}"
            )
            action = "We Can Still Hear Each Other"
        elif evidence.remote_decoded_test_observed:
            status = BandCheckStatus.WARNING
            detail = (
                f"{path_sentence} A remote test decoded at the far end. That proves "
                "the test reached the music path, not that a musician heard it. "
                "Both play a note, then confirm."
            )
            action = "We Can Hear Each Other"
        elif (
            evidence.production_local_signal_observed
            and evidence.production_remote_signal_observed
        ):
            status = BandCheckStatus.WARNING
            detail = (
                f"{path_sentence} Jamulus reports signal in both directions. That is "
                "path evidence, not proof that either musician heard it. Both play "
                "a note, then confirm."
            )
            action = "We Can Hear Each Other"
        elif evidence.transport_datagrams_flowed:
            status = BandCheckStatus.WARNING
            detail = (
                f"Music data crossed the connection. {path_sentence} That does not "
                "confirm that anyone heard it. Both play a note, then confirm."
            )
            action = "We Can Hear Each Other"
        elif evidence.production_local_signal_observed:
            status = BandCheckStatus.WARNING
            detail = (
                "Jamulus reports your signal, but no band signal has been observed. "
                "Ask your bandmate to play; this is not a hearing confirmation."
            )
            action = "We Can Hear Each Other"
        elif evidence.production_remote_signal_observed:
            status = BandCheckStatus.WARNING
            detail = (
                "Jamulus reports band signal, but your signal has not been observed. "
                "Play a note; this is not a hearing confirmation."
            )
            action = "We Can Hear Each Other"
        else:
            status = BandCheckStatus.WARNING
            detail = (
                f"A bandmate is connected. {path_sentence} Both play a note, then "
                "confirm that you can hear each other."
            )
            action = "We Can Hear Each Other"

        if (
            status is BandCheckStatus.PASS
            and evidence.connection_quality is ConnectionQuality.DIFFICULT
        ):
            status = BandCheckStatus.WARNING
        self.update_step(
            BandCheckStepKey.MUSIC_PATH,
            status=status,
            detail=detail,
            next_action=action,
            technical_details=self._music_path_facts(),
        )

    def _music_path_facts(self) -> tuple[str, ...]:
        evidence = self.evidence
        return (
            f"process_started={str(evidence.jamulus_process_started).lower()}",
            "authenticated_responsive="
            f"{str(evidence.jamulus_authenticated_responsive).lower()}",
            f"remote_participant={str(evidence.remote_participant_appeared).lower()}",
            f"transport_datagrams={str(evidence.transport_datagrams_flowed).lower()}",
            f"remote_decoded_test={str(evidence.remote_decoded_test_observed).lower()}",
            "musician_two_way_confirmation="
            f"{str(evidence.musician_confirmed_two_way_audibility).lower()}",
            f"connection_path={getattr(evidence.connection_path, 'value', 'unknown')}",
            f"connection_quality={evidence.connection_quality.value}",
            f"path_generation={evidence.path_generation}",
            f"path_recheck_required={str(evidence.path_recheck_required).lower()}",
        )

    @property
    def outcome(self) -> BandCheckOutcome:
        required = [item for item in self.steps if item.required]
        if any(
            item.status
            in {
                BandCheckStatus.PENDING,
                BandCheckStatus.RUNNING,
                BandCheckStatus.ACTION_NEEDED,
            }
            for item in required
        ):
            return BandCheckOutcome.ACTION_NEEDED
        if any(item.status is BandCheckStatus.WARNING for item in self.steps):
            return BandCheckOutcome.WARNING
        return BandCheckOutcome.READY

    @property
    def primary_action_step(self) -> BandCheckStep | None:
        priority = (
            BandCheckStatus.ACTION_NEEDED,
            BandCheckStatus.RUNNING,
            BandCheckStatus.PENDING,
        )
        for wanted in priority:
            for item in self.steps:
                if item.required and item.status is wanted and item.next_action:
                    return item
        for item in self.steps:
            if item.status is BandCheckStatus.WARNING and item.next_action:
                return item
        return None

    @property
    def primary_action(self) -> str:
        step = self.primary_action_step
        return step.next_action if step is not None else "Close Band Check"

    def summary_text(self) -> str:
        return f"{self.outcome.value}\nNext: {self.primary_action}."


def _preflight_item(report, name: str):
    return next((item for item in report.items if item.name == name), None)


def build_band_check_session(
    settings,
    *,
    mode: BandCheckMode = BandCheckMode.PRE_SESSION,
    observations: BandCheckObservations | None = None,
    host_server_certification: object | None = None,
) -> BandCheckSession:
    """Build initial typed state from the existing safe configuration probes."""

    from core.preflight import run_ready_check

    report = run_ready_check(settings)
    engine = _preflight_item(report, "Jamulus installed")
    server = _preflight_item(report, "Jamulus server set")
    hosted = _preflight_item(report, "Band server (hosted)")
    selected_input = _preflight_item(report, "Meter and local recording input")
    recorder = _preflight_item(report, "Host recorder")
    local_capture = _preflight_item(report, "Local stem recording")
    recording_storage = _preflight_item(report, "Recording storage")
    webex = _preflight_item(report, "Webex companion")
    is_host = bool(getattr(settings, "host_server_enabled", False))
    probed_engine_version = (
        music_engine_version(settings)
        if engine is not None and engine.ok
        else "unavailable"
    )

    def state(item, *, pending_on_success: bool = False):
        if item is None:
            return BandCheckStatus.NOT_APPLICABLE
        if item.ok:
            return (
                BandCheckStatus.PENDING if pending_on_success else BandCheckStatus.PASS
            )
        return (
            BandCheckStatus.ACTION_NEEDED if item.required else BandCheckStatus.WARNING
        )

    server_items = [item for item in (server, hosted) if item is not None]
    server_failure = next(
        (item for item in server_items if not item.ok and item.required), None
    )
    server_warning = next((item for item in server_items if not item.ok), None)
    if (
        is_host
        and mode is BandCheckMode.PRE_SESSION
        and host_server_certification is not None
    ):
        certification_ok = bool(getattr(host_server_certification, "ok", False))
        certification_warning = bool(
            getattr(host_server_certification, "warning", False)
        )
        server_status = (
            BandCheckStatus.WARNING
            if certification_ok and certification_warning
            else BandCheckStatus.PASS
            if certification_ok
            else BandCheckStatus.ACTION_NEEDED
        )
        server_detail = str(
            getattr(host_server_certification, "detail", "")
            or "The hosted band server could not be verified."
        )
        server_action = "" if certification_ok else "Close Band Check"
        server_technical = tuple(
            str(value)
            for value in getattr(
                host_server_certification,
                "technical_details",
                (),
            )
        )
    elif server_failure:
        server_status = BandCheckStatus.ACTION_NEEDED
        server_detail = (
            "This Mac could not verify the bundled band server. Close Band "
            "Check, restart WebJam, and reinstall the latest build if it repeats."
            if is_host
            else (
                "This Mac no longer has a complete band invite. Close WebJam, "
                "open it again, and paste a fresh invite from your host."
            )
        )
        server_action = "Close Band Check"
        server_technical = tuple(getattr(item, "detail", "") for item in server_items)
    elif server_warning:
        server_status = BandCheckStatus.WARNING
        server_detail = (
            "The band server cannot be fully checked until the session starts."
        )
        server_action = "Check again after the session starts"
        server_technical = tuple(getattr(item, "detail", "") for item in server_items)
    else:
        # A version/path probe is not reachability. PRE_SESSION deliberately
        # remains a warning until the production session reports the server.
        server_status = BandCheckStatus.WARNING
        server_detail = (
            "The hosted band server and its required version are available. "
            "Its running state is checked after the session starts."
            if is_host
            else "The band address is set. Reachability is checked when the session starts."
        )
        server_action = "Check again after the session starts"
        server_technical = tuple(getattr(item, "detail", "") for item in server_items)

    if mode is BandCheckMode.LIVE_OBSERVE:
        # A live Jamulus client owns the instrument device. Do not open a
        # second PortAudio stream or offer a competing system-input choice
        # while observing a real jam.
        input_status = BandCheckStatus.WARNING
        input_detail = (
            "Jamulus owns the live instrument input. Use Jamulus Audio "
            "Settings if your sound needs attention."
        )
        input_action = ""
        input_required = False
    else:
        input_status = state(selected_input, pending_on_success=True)
        input_detail = (
            "Press Check Input, then play or sing. Metering listens for level and saves nothing."
            if selected_input is not None and selected_input.ok
            else "The selected input cannot be opened with this setup."
        )
        input_action = (
            "Check Input"
            if input_status is BandCheckStatus.PENDING
            else "Open Jamulus Audio Settings"
        )
        input_required = True

    recording_problem = next(
        (
            item
            for item in (local_capture, recording_storage, recorder)
            if item is not None and not item.ok and item.required
        ),
        None,
    )
    recording_warning = next(
        (
            item
            for item in (local_capture, recording_storage, recorder)
            if item is not None and bool(getattr(item, "warning", False))
        ),
        None,
    )
    recording_status = (
        BandCheckStatus.ACTION_NEEDED
        if recording_problem is not None
        else BandCheckStatus.WARNING
        if recording_warning is not None
        else BandCheckStatus.PENDING
    )

    engine_compatible = probed_engine_version == "3.12.2"
    engine_status = (
        BandCheckStatus.PENDING
        if mode is BandCheckMode.LIVE_OBSERVE
        else BandCheckStatus.PASS
        if engine is not None and engine.ok and engine_compatible
        else BandCheckStatus.ACTION_NEEDED
    )
    engine_detail = (
        "Waiting for the running music engine and its secure control check."
        if mode is BandCheckMode.LIVE_OBSERVE
        else "The music engine opened for a version check and exited cleanly."
        if engine_compatible
        else (
            "The required music engine is missing or incompatible. Close Band "
            "Check and reinstall the latest WebJam build."
        )
    )

    steps = [
        BandCheckStep(
            BandCheckStepKey.MUSIC_ENGINE,
            "Music engine",
            engine_status,
            engine_detail,
            "Close Band Check"
            if engine_status is BandCheckStatus.ACTION_NEEDED
            else "",
            tuple(
                value
                for value in (
                    getattr(engine, "detail", "") if engine is not None else "",
                    f"version={probed_engine_version}",
                    "required_version=3.12.2",
                )
                if value
            ),
        ),
        BandCheckStep(
            BandCheckStepKey.BAND_SERVER,
            "Band server",
            server_status,
            server_detail,
            server_action,
            server_technical,
            required=is_host or bool(server),
        ),
        BandCheckStep(
            BandCheckStepKey.AUDIO_INPUT,
            "Your instrument input",
            input_status,
            input_detail,
            input_action,
            (getattr(selected_input, "detail", ""),) if selected_input else (),
            required=input_required,
        ),
        BandCheckStep(
            BandCheckStepKey.HEADPHONES,
            "Headphones",
            (
                BandCheckStatus.WARNING
                if mode is BandCheckMode.LIVE_OBSERVE
                else BandCheckStatus.PENDING
            ),
            (
                "Band Check will not play a test into an active session. Run the "
                "left/right check before the next session."
                if mode is BandCheckMode.LIVE_OBSERVE
                else "Use headphones. The gentle left/right test plays only when you press the button."
            ),
            "" if mode is BandCheckMode.LIVE_OBSERVE else "Play Headphone Test",
            required=mode is BandCheckMode.PRE_SESSION,
        ),
        BandCheckStep(
            BandCheckStepKey.TEST_RECORDING,
            "Five-second recording",
            (
                BandCheckStatus.WARNING
                if mode is BandCheckMode.LIVE_OBSERVE
                else BandCheckStatus.PENDING
            ),
            (
                "Band Check will not open a second recorder during a live session. "
                "Run this proof before the next session."
                if mode is BandCheckMode.LIVE_OBSERVE
                else "Record, replay, and confirm a short isolated-input test. It is deleted by default."
            ),
            "" if mode is BandCheckMode.LIVE_OBSERVE else "Record 5 Seconds",
            required=mode is BandCheckMode.PRE_SESSION,
        ),
        BandCheckStep(
            BandCheckStepKey.RECORDING_PATH,
            "Recording safety",
            (
                BandCheckStatus.WARNING
                if mode is BandCheckMode.LIVE_OBSERVE
                else recording_status
            ),
            (
                "The live recorder is observed without opening or restarting it. "
                "A write/reopen proof runs before the next session."
                if mode is BandCheckMode.LIVE_OBSERVE
                else "The configured recording path needs attention."
                if recording_problem is not None
                else getattr(recording_warning, "detail", "")
                if recording_warning is not None
                else "A real write, finalization, and reopen check runs with the five-second recording."
            ),
            (
                ""
                if mode is BandCheckMode.LIVE_OBSERVE
                else "Recording Setup"
                if recording_problem is not None or recording_warning is not None
                else "Record 5 Seconds"
            ),
            tuple(
                getattr(item, "detail", "")
                for item in (local_capture, recording_storage, recorder)
                if item is not None
            ),
            required=mode is BandCheckMode.PRE_SESSION,
        ),
        BandCheckStep(
            BandCheckStepKey.STUDIO,
            "Studio file check",
            (
                BandCheckStatus.WARNING
                if mode is BandCheckMode.LIVE_OBSERVE
                else BandCheckStatus.PENDING
            ),
            (
                "Band Check leaves Studio and the output device untouched while the session is live."
                if mode is BandCheckMode.LIVE_OBSERVE
                else "Waveform readability is checked from the finalized test recording."
            ),
            "" if mode is BandCheckMode.LIVE_OBSERVE else "Record 5 Seconds",
            required=mode is BandCheckMode.PRE_SESSION,
        ),
    ]

    if mode is BandCheckMode.LIVE_OBSERVE:
        steps.append(
            BandCheckStep(
                BandCheckStepKey.MUSIC_PATH,
                "Connection and music path",
                BandCheckStatus.WARNING,
                (
                    "Waiting for a bandmate and independent connection, music-data, "
                    "and hearing evidence."
                ),
                "Check Again",
                required=True,
            )
        )

    if str(getattr(settings, "webex_url", "") or "").strip():
        webex_ok = bool(webex and webex.ok)
        steps.append(
            BandCheckStep(
                BandCheckStepKey.WEBEX,
                "Webex companion",
                BandCheckStatus.WARNING if webex_ok else BandCheckStatus.WARNING,
                (
                    "Optional: Webex is for video and conversation. Keep its music monitoring off while playing."
                    if webex_ok
                    else "The optional Webex link needs attention. Jamulus still carries the music."
                ),
                "Review Webex audio" if webex_ok else "Open Settings",
                (getattr(webex, "detail", ""),) if webex else (),
                required=False,
            )
        )

    session = BandCheckSession(mode=mode, steps=steps)
    if observations is not None:
        session.apply_live_observations(observations)
    return session


@dataclass(frozen=True)
class VerificationSignature:
    app_version: str
    music_engine_version: str
    input_device_id: str
    sample_rate: int
    input_channels: tuple[int, ...]
    host_server_enabled: bool = False
    output_device_id: str = "system-default"
    audio_blocksize: int = 0
    recording_path_id: str = "unconfigured"
    # A hash of the native Jamulus route selection/snapshot when macOS owns
    # it. This stays separate from the PortAudio meter/capture identity above:
    # neither one may stand in for the other.
    jamulus_route_id: str = "system-controlled"

    def to_dict(self) -> dict:
        return {
            "app_version": self.app_version,
            "music_engine_version": self.music_engine_version,
            "input_device_id": self.input_device_id,
            "sample_rate": self.sample_rate,
            "input_channels": list(self.input_channels),
            "host_server_enabled": self.host_server_enabled,
            "output_device_id": self.output_device_id,
            "audio_blocksize": self.audio_blocksize,
            "recording_path_id": self.recording_path_id,
            "jamulus_route_id": self.jamulus_route_id,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "VerificationSignature":
        channels = value.get("input_channels", [])
        if not isinstance(channels, list):
            raise ValueError("input_channels must be a list")
        return cls(
            app_version=str(value.get("app_version", "")),
            music_engine_version=str(value.get("music_engine_version", "")),
            input_device_id=str(value.get("input_device_id", "")),
            sample_rate=int(value.get("sample_rate", 0)),
            input_channels=tuple(int(item) for item in channels),
            host_server_enabled=bool(value.get("host_server_enabled", False)),
            output_device_id=str(value.get("output_device_id", "system-default")),
            audio_blocksize=int(value.get("audio_blocksize", 0)),
            recording_path_id=str(value.get("recording_path_id", "unconfigured")),
            jamulus_route_id=str(
                value.get("jamulus_route_id", "legacy-system-route")
            ),
        )


@dataclass(frozen=True)
class BandCheckVerification:
    signature: VerificationSignature
    outcome: BandCheckOutcome
    manual_confirmations: tuple[str, ...]
    verified_at: str
    schema: int = 1

    @property
    def usable(self) -> bool:
        output_confirmed = bool(
            {"headphones_left_right", "headphones_output"}
            & set(self.manual_confirmations)
        )
        return (
            self.outcome in {BandCheckOutcome.READY, BandCheckOutcome.WARNING}
            and output_confirmed
            and "scratch_sounds_right" in self.manual_confirmations
        )

    def matches(self, signature: VerificationSignature) -> bool:
        return (
            self.usable
            and not signature.output_device_id.startswith("unavailable:")
            and self.signature == signature
        )

    def to_dict(self) -> dict:
        return {
            "schema": self.schema,
            "signature": self.signature.to_dict(),
            "outcome": self.outcome.value,
            "manual_confirmations": list(self.manual_confirmations),
            "verified_at": self.verified_at,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "BandCheckVerification":
        if int(value.get("schema", 0)) != 1:
            raise ValueError("unsupported Band Check verification schema")
        signature = value.get("signature")
        if not isinstance(signature, Mapping):
            raise ValueError("missing signature")
        confirmations = value.get("manual_confirmations", [])
        if not isinstance(confirmations, list):
            raise ValueError("manual_confirmations must be a list")
        return cls(
            signature=VerificationSignature.from_dict(signature),
            outcome=BandCheckOutcome(str(value.get("outcome", ""))),
            manual_confirmations=tuple(sorted({str(item) for item in confirmations})),
            verified_at=str(value.get("verified_at", "")),
        )


def verification_path(settings) -> Path:
    config = Path(str(getattr(settings, "config_file", "") or "~/.webjam_config.json"))
    return config.expanduser().with_name(".webjam_band_check.json")


def save_verification(
    path: str | Path,
    *,
    signature: VerificationSignature,
    session: BandCheckSession,
    now: datetime | None = None,
) -> BandCheckVerification:
    from core.file_io import atomic_write_text

    stamp = now or datetime.now(timezone.utc)
    verification = BandCheckVerification(
        signature=signature,
        outcome=session.outcome,
        manual_confirmations=tuple(sorted(session.manual_confirmations)),
        verified_at=stamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
    )
    atomic_write_text(
        Path(path),
        json.dumps(verification.to_dict(), indent=2, sort_keys=True),
        mode=0o600,
    )
    return verification


def load_verification(path: str | Path) -> BandCheckVerification | None:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return None
        return BandCheckVerification.from_dict(raw)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _music_engine_binary(settings) -> str:
    from services.bridge_service import _bundled_jamulus_candidate

    bundled = _bundled_jamulus_candidate()
    if bundled:
        return str(bundled)
    for candidate in list(getattr(settings, "jamulus_candidates", []) or []):
        path = Path(str(candidate)).expanduser()
        if path.is_file():
            return str(path)
    return ""


def music_engine_version(settings) -> str:
    """Read the engine version without starting a session or leaving a process."""

    binary = _music_engine_binary(settings)
    if not binary:
        return "unavailable"
    try:
        result = subprocess.run(
            [binary, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
        )
    except (OSError, subprocess.SubprocessError):
        return "unverified"
    text = f"{result.stdout}\n{result.stderr}"
    match = re.search(r"(?:Version\s+)?(\d+\.\d+(?:\.\d+)?)", text)
    return match.group(1) if match else "unverified"


def build_verification_signature(
    settings,
    *,
    app_version: str,
    engine_version: str | None = None,
) -> VerificationSignature:
    """Snapshot the exact setup values that invalidate a prior verification."""

    raw_device = getattr(settings, "audio_input_device_index", -1)
    try:
        device_index = int(raw_device)
    except (TypeError, ValueError):
        device_index = -1
    device_name = "system-default"
    try:
        import sounddevice as sd  # type: ignore

        raw = sd.query_devices(None if device_index < 0 else device_index, "input")
        if isinstance(raw, dict):
            device_name = str(raw.get("name") or device_name)
    except Exception:  # noqa: BLE001 - signature remains stable and honest
        pass
    device_id = f"portaudio:{device_index}:{device_name}"[:256]
    requested_output = str(
        getattr(settings, "take_playback_output_device", "") or ""
    ).strip()
    try:
        import sounddevice as sd  # type: ignore

        output = sd.query_devices(
            requested_output or None,
            "output",
        )
        if not isinstance(output, dict):
            raise ValueError("output device unavailable")
        output_name = str(output.get("name") or "unknown")
        output_channels = int(output.get("max_output_channels", 0) or 0)
        if output_channels < 1:
            raise ValueError("output device has no channels")
        output_device_id = (
            f"portaudio:{requested_output or 'default'}:{output_name}:{output_channels}"
        )[:256]
    except Exception:  # noqa: BLE001 - unavailable output must fail closed
        output_device_id = (f"unavailable:{requested_output or 'system-default'}")[:256]

    configured_root = str(getattr(settings, "takes_directory", "") or "").strip()
    if configured_root:
        recording_root = Path(configured_root).expanduser()
    else:
        config_file = Path(
            str(getattr(settings, "config_file", "") or "~/.webjam_config.json")
        ).expanduser()
        recording_root = config_file.parent
    try:
        normalized_root = str(recording_root.resolve(strict=False))
    except OSError:
        normalized_root = str(recording_root.absolute())
    recording_path_id = hashlib.sha256(
        normalized_root.encode("utf-8", errors="surrogatepass")
    ).hexdigest()
    channel_count = 2 if bool(getattr(settings, "local_capture_enabled", False)) else 1
    return VerificationSignature(
        app_version=str(app_version),
        music_engine_version=(
            str(engine_version)
            if engine_version is not None
            else music_engine_version(settings)
        ),
        input_device_id=device_id,
        sample_rate=int(getattr(settings, "audio_samplerate", 48000) or 48000),
        input_channels=tuple(range(channel_count)),
        host_server_enabled=bool(getattr(settings, "host_server_enabled", False)),
        output_device_id=output_device_id,
        audio_blocksize=int(getattr(settings, "audio_blocksize", 0) or 0),
        recording_path_id=recording_path_id,
        jamulus_route_id=_jamulus_route_signature(settings),
    )


def _jamulus_route_signature(settings) -> str:
    """Return a non-identifying value that invalidates stale band-route proof.

    On macOS the Jamulus selector is based on CoreAudio identities, not the
    PortAudio index used by WebJam's optional meter.  Hash the UIDs and current
    defaults before storing the verification so they never appear in a public
    diagnostic or in the verification file as raw hardware labels.
    """

    if sys.platform != "darwin":
        return "system-controlled"
    input_uid = str(getattr(settings, "jamulus_audio_input_uid", "") or "").strip()
    output_uid = str(getattr(settings, "jamulus_audio_output_uid", "") or "").strip()
    try:
        from core.coreaudio_devices import scan_coreaudio_devices

        scan = scan_coreaudio_devices()
        if scan.available:
            input_uid = input_uid or str(scan.default_input_uid or "")
            output_uid = output_uid or str(scan.default_output_uid or "")
    except Exception:  # noqa: BLE001 - verification remains conservative
        pass
    material = f"{input_uid}\x1f{output_uid}".encode("utf-8", "surrogatepass")
    return "coreaudio:" + hashlib.sha256(material).hexdigest()
