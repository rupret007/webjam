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
import os
from pathlib import Path
import re
import subprocess
from typing import Iterable, Mapping


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


@dataclass
class BandCheckSession:
    mode: BandCheckMode
    steps: list[BandCheckStep]
    input_rms: float = 0.0
    input_peak: float = 0.0
    input_clipped: bool = False
    manual_confirmations: set[str] = field(default_factory=set)

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
            self.update_step(
                BandCheckStepKey.AUDIO_INPUT,
                status=BandCheckStatus.WARNING,
                detail="WebJam hears the input, but it is clipping. Turn the input gain down.",
                next_action="Turn the input gain down",
            )
        elif self.input_peak >= 0.02 or self.input_rms >= 0.005:
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
                detail=detail or "The test recording could not be finalized and reopened.",
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
                BandCheckStatus.PENDING
                if has_signal
                else BandCheckStatus.ACTION_NEEDED
            ),
            detail=(
                "The five-second file was finalized and reopened. Play it, then "
                "confirm that it sounds right."
                if has_signal
                else "The file was created correctly, but it contains no usable input level."
            ),
            next_action=(
                "Play the recording" if has_signal else "Check the input and record again"
            ),
            technical_details=facts,
        )
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
            self.update_step(
                BandCheckStepKey.TEST_RECORDING,
                status=BandCheckStatus.PASS,
                detail="You confirmed that the isolated test recording sounds right.",
                next_action="",
            )
        else:
            self.manual_confirmations.discard("scratch_sounds_right")
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
        if observations.local_meter_active:
            self.observe_input(
                rms=observations.local_meter_rms,
                peak=observations.local_meter_peak,
                clipped=observations.local_meter_clipped,
            )
        if observations.music_engine_running and observations.music_engine_responsive:
            self.update_step(
                BandCheckStepKey.MUSIC_ENGINE,
                status=BandCheckStatus.PASS,
                detail="The music engine is running and responding.",
                next_action="",
            )
        elif observations.music_engine_running:
            self.update_step(
                BandCheckStepKey.MUSIC_ENGINE,
                status=BandCheckStatus.ACTION_NEEDED,
                detail="The music engine is running but is not responding.",
                next_action="End the session, then start it again",
            )
        else:
            self.update_step(
                BandCheckStepKey.MUSIC_ENGINE,
                status=BandCheckStatus.ACTION_NEEDED,
                detail="The music engine is not running.",
                next_action="Close Band Check and start the session",
            )

        if observations.production_local_signal and observations.production_remote_signal:
            status = BandCheckStatus.PASS
            detail = "Jamulus reports both your music and band audio on the live path."
            action = ""
        elif observations.production_local_signal:
            status = BandCheckStatus.WARNING
            detail = "Jamulus reports your music. No band audio has been observed yet."
            action = "Ask a bandmate to play"
        elif observations.production_remote_signal:
            status = BandCheckStatus.WARNING
            detail = "Jamulus reports band audio, but your music has not appeared yet."
            action = "Play a note"
        elif observations.peer_connected:
            status = BandCheckStatus.WARNING
            detail = "A bandmate is connected, but no live-path audio has been observed yet."
            action = "Both play a note"
        else:
            status = BandCheckStatus.WARNING
            detail = "No diagnostic peer is available, so send and receive audio are unverified."
            action = "Check again when a bandmate joins"
        if self.has_step(BandCheckStepKey.MUSIC_PATH):
            self.update_step(
                BandCheckStepKey.MUSIC_PATH,
                status=status,
                detail=detail,
                next_action=action,
            )

        if (
            observations.band_server_running is not None
            and self.has_step(BandCheckStepKey.BAND_SERVER)
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
                    "" if observations.band_server_running
                    else "End the session, then start it again"
                ),
            )
        if (
            observations.recorder_ready is not None
            and self.has_step(BandCheckStepKey.RECORDING_PATH)
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

    @property
    def outcome(self) -> BandCheckOutcome:
        required = [item for item in self.steps if item.required]
        if any(
            item.status in {
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
    def primary_action(self) -> str:
        priority = (
            BandCheckStatus.ACTION_NEEDED,
            BandCheckStatus.RUNNING,
            BandCheckStatus.PENDING,
        )
        for wanted in priority:
            for item in self.steps:
                if item.required and item.status is wanted and item.next_action:
                    return item.next_action
        for item in self.steps:
            if item.status is BandCheckStatus.WARNING and item.next_action:
                return item.next_action
        return "Close Band Check"

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
            return BandCheckStatus.PENDING if pending_on_success else BandCheckStatus.PASS
        return (
            BandCheckStatus.ACTION_NEEDED
            if item.required
            else BandCheckStatus.WARNING
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
        certification_ok = bool(
            getattr(host_server_certification, "ok", False)
        )
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
        server_action = "" if certification_ok else "Open Settings"
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
        server_detail = "The band server settings need attention."
        server_action = "Open Settings"
        server_technical = tuple(getattr(item, "detail", "") for item in server_items)
    elif server_warning:
        server_status = BandCheckStatus.WARNING
        server_detail = "The band server cannot be fully checked until the session starts."
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

    input_status = state(selected_input, pending_on_success=True)
    input_detail = (
        "Press Check Input, then play or sing. Metering listens for level and saves nothing."
        if selected_input is not None and selected_input.ok
        else "The selected input cannot be opened with this setup."
    )
    input_action = "Check Input" if input_status is BandCheckStatus.PENDING else "Open Settings"

    recording_problem = next(
        (
            item
            for item in (local_capture, recorder)
            if item is not None and not item.ok and item.required
        ),
        None,
    )
    recording_status = (
        BandCheckStatus.ACTION_NEEDED
        if recording_problem is not None
        else BandCheckStatus.PENDING
    )

    engine_compatible = probed_engine_version == "3.12.2"
    engine_status = (
        BandCheckStatus.PASS
        if engine is not None and engine.ok and engine_compatible
        else BandCheckStatus.ACTION_NEEDED
    )
    engine_detail = (
        "The music engine opened for a version check and exited cleanly."
        if engine_compatible
        else "The required music engine version could not be launched and verified."
    )

    steps = [
        BandCheckStep(
            BandCheckStepKey.MUSIC_ENGINE,
            "Music engine",
            engine_status,
            engine_detail,
            "Open Settings" if engine_status is BandCheckStatus.ACTION_NEEDED else "",
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
                else "A real write, finalization, and reopen check runs with the five-second recording."
            ),
            (
                ""
                if mode is BandCheckMode.LIVE_OBSERVE
                else "Open Settings"
                if recording_problem is not None
                else "Record 5 Seconds"
            ),
            tuple(
                getattr(item, "detail", "")
                for item in (local_capture, recorder)
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
                "Live music path",
                BandCheckStatus.WARNING,
                "Waiting for production send and receive observations.",
                "Play a note",
                required=False,
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

    def to_dict(self) -> dict:
        return {
            "app_version": self.app_version,
            "music_engine_version": self.music_engine_version,
            "input_device_id": self.input_device_id,
            "sample_rate": self.sample_rate,
            "input_channels": list(self.input_channels),
            "host_server_enabled": self.host_server_enabled,
            "output_device_id": self.output_device_id,
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
            output_device_id=str(
                value.get("output_device_id", "system-default")
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
        return self.usable and self.signature == signature

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
        output_device_id=(
            str(getattr(settings, "take_playback_output_device", "") or "").strip()
            or "system-default"
        )[:256],
    )
