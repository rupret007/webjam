"""Simple, truthful Band Check with an old-name compatibility alias."""

from __future__ import annotations

from copy import deepcopy
import logging
from pathlib import Path
import re
import shutil
import tempfile
import threading
from typing import Callable

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core.band_check import (
    BandCheckMode,
    BandCheckObservations,
    BandCheckOutcome,
    BandCheckSession,
    BandCheckStatus,
    BandCheckStepKey,
    build_band_check_session,
    build_verification_signature,
    save_verification,
    verification_path,
)
from core.band_check_audio import (
    BandCheckAudioError,
    HeadphoneTonePlayer,
    InputActivityProbe,
    ScratchRecorder,
    ScratchRecordingEvidence,
    StudioCheckEvidence,
    validate_studio_scratch,
)
from core.redaction import redact_text
from webjam_qt.platform_permissions import microphone_permission_status
from webjam_qt.widgets.accessible import set_labeled_action


LOGGER = logging.getLogger("webjam.qt.band_check")

_ABSOLUTE_PATH_RE = re.compile(
    r"(?<![:\\/\w])/(?:[^/\s]+/)*[^,;:\s]+|"
    r"(?i:(?<![\w])(?:[a-z]:\\|\\\\)[^\r\n,;]+)"
)


def _safe_report_text(value: object) -> str:
    """Keep Band Check useful without turning its report into a log viewer."""

    clean = redact_text(str(value or ""))
    return _ABSOLUTE_PATH_RE.sub("[private path]", clean)[:800]


class BandCheckDialog(QDialog):
    """Guided check whose audio actions always require an explicit click."""

    settings_requested = Signal()
    recording_settings_requested = Signal()
    system_input_requested = Signal()
    microphone_settings_requested = Signal()
    practice_requested = Signal()
    support_requested = Signal()
    session_start_requested = Signal()
    _report_ready = Signal(object)
    _scratch_ready = Signal(object)

    def __init__(
        self,
        settings_provider: Callable[[], object],
        parent: QWidget | None = None,
        *,
        mode: BandCheckMode = BandCheckMode.PRE_SESSION,
        observations_provider: Callable[[], BandCheckObservations] | None = None,
        host_server_service: object | None = None,
        start_session_when_ready: bool = False,
        settings_generation_provider: Callable[[], int] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("BandCheckDialog")
        self.setWindowTitle("WebJam — Band Check")
        # Keep the client area short enough to leave room for the native
        # title bar on a 600-pixel-tall display. Longer reports already scroll
        # inside ``_report``, so they must not make the dialog taller.
        self.resize(680, 540)
        self.setMinimumSize(560, 520)
        self.setModal(False)
        self._settings_provider = settings_provider
        self._mode = mode
        self._observations_provider = observations_provider
        self._host_server_service = host_server_service
        self._start_session_when_ready = bool(start_session_when_ready)
        self._settings_generation_provider = settings_generation_provider
        self._scan_id = 0
        self._scan_failed = False
        self._items: list[object] = []  # old Ready Check test/extension surface
        self._session: BandCheckSession | None = None
        self._input_probe: InputActivityProbe | None = None
        self._scratch: ScratchRecorder | None = None
        self._scratch_evidence: ScratchRecordingEvidence | None = None
        self._tone = HeadphoneTonePlayer()
        self._tone_played = False
        self._headphone_channels = 2
        self._scratch_played = False
        self._scratch_root: Path | None = None
        self._action_step: BandCheckStepKey | None = None
        self._verification_save_started = False
        self._microphone_permission_explained = False

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 16)
        root.setSpacing(12)

        eyebrow = QLabel("BAND CHECK")
        eyebrow.setObjectName("BandCheckEyebrow")
        root.addWidget(eyebrow)
        title = QLabel(
            "Check this setup"
            if mode is BandCheckMode.PRE_SESSION
            else "Check the live session"
        )
        title.setObjectName("BandCheckTitle")
        root.addWidget(title)
        intro = QLabel(
            "Three quick proofs: your input, your headphones, and a five-second "
            "recording. Nothing plays or records until you press its button."
            if mode is BandCheckMode.PRE_SESSION
            else "Band Check keeps connection, music-data, and hearing evidence "
            "separate. It will not restart the music engine or band server."
        )
        intro.setObjectName("BandCheckIntro")
        intro.setWordWrap(True)
        root.addWidget(intro)

        self._summary = QLabel("Checking your setup…")
        self._summary.setObjectName("ReadySummary")
        self._summary.setWordWrap(True)
        self._summary.setAccessibleName("Band Check result")
        self._summary.setAccessibleDescription("Band Check is running")
        root.addWidget(self._summary)

        self._next = QLabel("Next: checking the setup.")
        self._next.setObjectName("BandCheckNext")
        self._next.setWordWrap(True)
        root.addWidget(self._next)

        self._meter = QProgressBar()
        self._meter.setObjectName("BandCheckMeter")
        self._meter.setRange(0, 100)
        self._meter.setValue(0)
        self._meter.setTextVisible(True)
        self._meter.setFormat("Input level — waiting")
        self._meter.setAccessibleName("Selected input level")
        self._meter.setAccessibleDescription("Input level: waiting for a check")
        root.addWidget(self._meter)

        action_row = QHBoxLayout()
        self._primary = QPushButton("Checking…")
        self._primary.setObjectName("PrimaryButton")
        self._primary.setEnabled(False)
        self._primary.clicked.connect(self._run_primary_action)
        action_row.addWidget(self._primary)
        self._secondary = QPushButton("Try Again")
        self._secondary.setObjectName("GhostButton")
        self._secondary.setVisible(False)
        self._secondary.clicked.connect(self._run_secondary_action)
        action_row.addWidget(self._secondary)
        action_row.addStretch(1)
        root.addLayout(action_row)

        self._report = QScrollArea()
        self._report.setWidgetResizable(True)
        self._report.setFrameShape(QScrollArea.Shape.NoFrame)
        self._report.setAccessibleName("Band Check details")
        self._report_content = QWidget()
        self._report_layout = QVBoxLayout(self._report_content)
        self._report_layout.setContentsMargins(0, 0, 0, 0)
        self._report_layout.setSpacing(8)
        self._report_layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        self._report.setWidget(self._report_content)
        root.addWidget(self._report, stretch=1)

        footer = QHBoxLayout()
        settings = QPushButton("Audio Settings")
        settings.setObjectName("GhostButton")
        settings.setAccessibleName("Jamulus audio settings")
        settings.setAccessibleDescription(
            "Bring Jamulus forward to change your interface, headphones, and buffer."
        )
        settings.clicked.connect(self.settings_requested.emit)
        footer.addWidget(settings)
        practice = QPushButton("Practice Solo")
        practice.setObjectName("GhostButton")
        practice.clicked.connect(self.practice_requested.emit)
        footer.addWidget(practice)
        footer.addStretch(1)
        support = QPushButton("Save Support Bundle")
        support.setObjectName("QuietButton")
        support.setAccessibleDescription(
            "Save a private diagnostic file only when support asks for it"
        )
        support.clicked.connect(self.support_requested.emit)
        footer.addWidget(support)
        close = QPushButton("Close")
        close.setObjectName("GhostButton")
        close.clicked.connect(self.close)
        footer.addWidget(close)
        root.addLayout(footer)

        self._meter_timer = QTimer(self)
        self._meter_timer.setInterval(100)
        self._meter_timer.timeout.connect(self._poll_input)
        self._live_timer = QTimer(self)
        self._live_timer.setInterval(500)
        self._live_timer.timeout.connect(self._refresh_live_observations)
        if mode is BandCheckMode.LIVE_OBSERVE and observations_provider is not None:
            self._live_timer.start()

        self._report_ready.connect(self._apply_report)
        self._scratch_ready.connect(self._apply_scratch_evidence)
        self.run_checks()

    def run_checks(self) -> None:
        """Refresh evidence without opening a musician audio device.

        A pre-session host scan may briefly start the production band server
        in this worker, authenticate it, then stop only the process it owns.
        """

        for item in self._items:
            if getattr(item, "manual_verification", False):
                item.ok = False
        self._stop_input_probe()
        self._delete_scratch()
        if self._tone_played:
            self._tone.stop()
        self._tone_played = False
        self._headphone_channels = 2
        self._scratch_played = False
        self._scan_id += 1
        scan_id = self._scan_id
        self._summary.setText("Checking your setup…")
        self._summary.setProperty("result", "checking")
        self._next.setText("Next: checking the setup.")
        self._primary.setEnabled(False)
        set_labeled_action(self._primary, "Checking…")
        self._secondary.setVisible(False)
        self._clear_rows()
        self._items = []
        self._session = None
        self._scan_failed = False
        self._verification_save_started = False

        def worker() -> None:
            try:
                settings = self._settings_provider()
                observations = (
                    self._observations_provider()
                    if self._observations_provider is not None
                    else None
                )
                host_server_certification = None
                if (
                    self._mode is BandCheckMode.PRE_SESSION
                    and bool(getattr(settings, "host_server_enabled", False))
                    and self._host_server_service is not None
                ):
                    certify = getattr(
                        self._host_server_service,
                        "certify_hosted_server_lifecycle",
                        None,
                    )
                    if callable(certify):
                        host_server_certification = certify()
                session = build_band_check_session(
                    settings,
                    mode=self._mode,
                    observations=observations,
                    host_server_certification=host_server_certification,
                )
                self._report_ready.emit((scan_id, session))
            except Exception as exc:  # noqa: BLE001
                LOGGER.exception("Band Check configuration scan failed")
                self._report_ready.emit((scan_id, exc))

        threading.Thread(target=worker, daemon=True, name="band-check").start()

    def _apply_report(self, payload: object) -> None:
        """Render typed state; legacy reports remain accepted for one release."""

        scan_id, report = payload
        if scan_id != self._scan_id:
            return
        if isinstance(report, BandCheckSession):
            self._session = report
            self._render_session()
            return
        if isinstance(report, Exception):
            self._show_scan_failure(report)
            return
        self._render_legacy_report(report)

    def _render_session(self) -> None:
        session = self._session
        if session is None:
            return
        self._clear_rows()
        for step in session.steps:
            self._add_step_row(step)
        self._report_layout.addStretch(1)
        outcome = session.outcome
        result = {
            BandCheckOutcome.READY: "pass",
            BandCheckOutcome.WARNING: "warn",
            BandCheckOutcome.ACTION_NEEDED: "fail",
        }[outcome]
        self._summary.setText(outcome.value)
        self._summary.setProperty("result", result)
        self._summary.setAccessibleDescription(
            f"Band Check result: {outcome.value}. Next: {session.primary_action}."
        )
        self._next.setText(f"Next: {session.primary_action}.")
        self._repolish(self._summary)
        self._refresh_action_button()

    def _add_step_row(self, step) -> QFrame:
        row = QFrame()
        row.setObjectName("ReadyCheckRow")
        row.setProperty("result", step.status.value)
        row.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        row.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        words = {
            BandCheckStatus.PASS: "READY",
            BandCheckStatus.WARNING: "WARNING",
            BandCheckStatus.ACTION_NEEDED: "ACTION",
            BandCheckStatus.RUNNING: "LISTENING",
            BandCheckStatus.PENDING: "CHECK",
            BandCheckStatus.NOT_APPLICABLE: "OPTIONAL",
        }
        mark_text = words[step.status]
        safe_detail = _safe_report_text(step.detail)
        row.setAccessibleName(f"{mark_text}: {step.title}. {safe_detail}")
        mark = QLabel(mark_text)
        mark.setObjectName("ReadyCheckMark")
        mark.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        title = QLabel(step.title)
        title.setObjectName("ReadyCheckName")
        title.setTextFormat(Qt.TextFormat.PlainText)
        detail = QLabel(safe_detail)
        detail.setObjectName("ReadyCheckDetail")
        detail.setTextFormat(Qt.TextFormat.PlainText)
        detail.setWordWrap(True)
        text = QVBoxLayout()
        text.setSpacing(2)
        text.addWidget(title)
        text.addWidget(detail)
        layout = QHBoxLayout(row)
        layout.addWidget(mark)
        layout.addLayout(text, stretch=1)
        self._report_layout.addWidget(row)
        return row

    def _refresh_action_button(self) -> None:
        session = self._session
        if session is None:
            return
        self._secondary.setVisible(False)
        self._action_step = self._find_action_step()
        key = self._action_step
        label = session.primary_action
        if (
            key is None
            and self._start_session_when_ready
            and session.outcome is not BandCheckOutcome.ACTION_NEEDED
        ):
            label = "Start Session"
        elif key is None:
            label = "Close Band Check"
        if key is BandCheckStepKey.HEADPHONES and self._tone_played:
            label = (
                "I Heard Both Sides"
                if self._headphone_channels >= 2
                else "I Heard the Test"
            )
            self._secondary.setText("Play Again")
            self._secondary.setVisible(True)
        elif key is BandCheckStepKey.TEST_RECORDING and self._scratch_evidence:
            if self._scratch_played:
                label = "That Sounds Right"
            elif self._scratch_evidence.valid and self._scratch_evidence.has_signal:
                label = "Play the Recording"
            else:
                label = "Record 5 Seconds"
            self._secondary.setText("Record Again")
            self._secondary.setVisible(True)
        set_labeled_action(self._primary, label)
        self._primary.setEnabled(True)

    def _find_action_step(self) -> BandCheckStepKey | None:
        session = self._session
        if session is None:
            return None
        step = session.primary_action_step
        return step.key if step is not None else None

    def _run_primary_action(self) -> None:
        session = self._session
        if session is None:
            if self._scan_failed:
                self.run_checks()
            return
        key = self._action_step
        if key is not None:
            step = session.step(key)
            if step.next_action == "Recording Setup":
                self.recording_settings_requested.emit()
                return
            if step.next_action in {
                "Use System Input",  # one-release extension compatibility
                "Open Jamulus Audio Settings",
            }:
                # Keep the legacy signal surface, but never use it to change
                # a live device. Jamulus is the one place musicians configure
                # interface input, channels, headphones, and buffer.
                self.settings_requested.emit()
                return
            if step.next_action == "Open Settings":
                self.settings_requested.emit()
                return
            if step.next_action == "Close Band Check":
                self.close()
                return
        if self._mode is BandCheckMode.LIVE_OBSERVE and key in {
            BandCheckStepKey.MUSIC_ENGINE,
            BandCheckStepKey.BAND_SERVER,
        }:
            # Main-session controls own End/Start. Closing this observational
            # report is the only truthful action the dialog can take itself.
            self.close()
            return
        if key in {BandCheckStepKey.MUSIC_ENGINE, BandCheckStepKey.BAND_SERVER}:
            self.settings_requested.emit()
        elif key is BandCheckStepKey.AUDIO_INPUT:
            step = session.step(BandCheckStepKey.AUDIO_INPUT)
            if step.next_action == "Open System Settings":
                self.microphone_settings_requested.emit()
                session.update_step(
                    BandCheckStepKey.AUDIO_INPUT,
                    status=BandCheckStatus.ACTION_NEEDED,
                    detail=(
                        "Allow WebJam in System Settings → Privacy & Security → "
                        "Microphone. Then return here and choose Try Again."
                    ),
                    next_action="Try Again",
                )
                self._render_session()
            else:
                self._start_input_check()
        elif key is BandCheckStepKey.HEADPHONES:
            if self._tone_played:
                session.confirm_headphones(
                    True,
                    stereo=self._headphone_channels >= 2,
                )
                self._tone.stop()
                self._render_session()
            else:
                self._play_headphone_test()
        elif key in {
            BandCheckStepKey.TEST_RECORDING,
            BandCheckStepKey.RECORDING_PATH,
            BandCheckStepKey.STUDIO,
        }:
            self._advance_scratch_check()
        elif key is BandCheckStepKey.MUSIC_PATH:
            step = session.step(BandCheckStepKey.MUSIC_PATH)
            if step.next_action in {
                "We Can Hear Each Other",
                "We Can Still Hear Each Other",
            }:
                session.confirm_two_way_audibility(True)
                self._render_session()
            else:
                self._refresh_live_observations()
        elif key is None:
            if (
                self._start_session_when_ready
                and session.outcome is not BandCheckOutcome.ACTION_NEEDED
            ):
                self._persist_verification_if_ready()
                self.session_start_requested.emit()
            self.close()
        else:
            self.settings_requested.emit()

    def _run_secondary_action(self) -> None:
        if self._action_step is BandCheckStepKey.HEADPHONES:
            self._tone_played = False
            self._play_headphone_test()
        elif self._action_step in {
            BandCheckStepKey.TEST_RECORDING,
            BandCheckStepKey.RECORDING_PATH,
            BandCheckStepKey.STUDIO,
        }:
            self._reset_scratch_check()
            self._start_scratch_recording()

    def _start_input_check(self) -> None:
        session = self._session
        if session is None:
            return
        if self._mode is BandCheckMode.LIVE_OBSERVE:
            self._refresh_live_observations()
            if (
                session.step(BandCheckStepKey.AUDIO_INPUT).status
                is not BandCheckStatus.PASS
            ):
                session.update_step(
                    BandCheckStepKey.AUDIO_INPUT,
                    status=BandCheckStatus.RUNNING,
                    detail=(
                        "Play or sing now. Band Check is reading the meter already "
                        "owned by the live session and will not open another device."
                    ),
                    next_action="Play a note",
                )
                self._render_session()
            return
        if self._input_probe is not None:
            return
        permission = microphone_permission_status()
        if permission == "not_determined" and not self._microphone_permission_explained:
            self._microphone_permission_explained = True
            session.update_step(
                BandCheckStepKey.AUDIO_INPUT,
                status=BandCheckStatus.RUNNING,
                detail=(
                    "WebJam needs microphone access so your band can hear your "
                    "instrument. Choose Continue, then allow access in the macOS prompt."
                ),
                next_action="Continue",
            )
            self._render_session()
            return
        if permission in {"denied", "restricted"}:
            session.update_step(
                BandCheckStepKey.AUDIO_INPUT,
                status=BandCheckStatus.ACTION_NEEDED,
                detail=(
                    "Microphone access is off. Open System Settings, allow WebJam "
                    "to use the microphone, then return to Band Check."
                ),
                next_action="Open System Settings",
            )
            self._render_session()
            return
        settings = deepcopy(self._settings_provider())
        self._input_probe = InputActivityProbe(
            device=_input_device_index(settings),
            sample_rate=int(getattr(settings, "audio_samplerate", 48_000) or 48_000),
            blocksize=int(getattr(settings, "audio_blocksize", 0) or 0),
        )
        try:
            self._input_probe.start()
        except BandCheckAudioError:
            self._input_probe = None
            permission = microphone_permission_status()
            if permission in {"denied", "restricted"}:
                session.update_step(
                    BandCheckStepKey.AUDIO_INPUT,
                    status=BandCheckStatus.ACTION_NEEDED,
                    detail=(
                        "Microphone access is off. Open System Settings, allow "
                        "WebJam to use the microphone, then return to Band Check."
                    ),
                    next_action="Open System Settings",
                )
            else:
                session.update_step(
                    BandCheckStepKey.AUDIO_INPUT,
                    status=BandCheckStatus.ACTION_NEEDED,
                    detail=(
                        "Jamulus needs an available instrument input. Open "
                        "Jamulus Audio Settings, check your interface, then "
                        "return here."
                    ),
                    next_action="Open Jamulus Audio Settings",
                )
            self._render_session()
            return
        session.update_step(
            BandCheckStepKey.AUDIO_INPUT,
            status=BandCheckStatus.RUNNING,
            detail="Play or sing now. WebJam is measuring level and saving nothing.",
            next_action="Play a note",
        )
        self._meter_timer.start()
        self._render_session()

    def _poll_input(self) -> None:
        if self._input_probe is None or self._session is None:
            return
        snapshot = self._input_probe.snapshot()
        self._show_meter(snapshot.rms, snapshot.peak, snapshot.clipped)
        previous = self._session.step(BandCheckStepKey.AUDIO_INPUT).status
        self._session.observe_input(
            rms=snapshot.rms,
            peak=snapshot.peak,
            clipped=snapshot.clipped,
        )
        current = self._session.step(BandCheckStepKey.AUDIO_INPUT).status
        if current in {BandCheckStatus.PASS, BandCheckStatus.WARNING}:
            self._stop_input_probe()
        if current is not previous:
            self._render_session()

    def _show_meter(self, rms: float, peak: float, clipped: bool) -> None:
        value = max(0, min(100, int(max(float(rms) * 300, float(peak) * 100))))
        self._meter.setValue(value)
        band = "too loud" if clipped else "signal" if value >= 2 else "quiet"
        self._meter.setFormat(f"Input level — {band}")
        self._meter.setAccessibleDescription(f"Input level: {band}, {value} percent")

    def _stop_input_probe(self) -> None:
        self._meter_timer.stop()
        probe, self._input_probe = self._input_probe, None
        if probe is not None:
            try:
                probe.stop()
            except Exception:  # noqa: BLE001
                LOGGER.debug("Input probe cleanup failed", exc_info=True)

    def _play_headphone_test(self) -> None:
        settings = deepcopy(self._settings_provider())
        self._primary.setEnabled(False)
        set_labeled_action(self._primary, "Playing Left, then Right…")
        try:
            evidence = self._tone.play(
                output_device_name=str(
                    getattr(settings, "take_playback_output_device", "") or ""
                )
            )
        except BandCheckAudioError:
            if self._session is not None:
                self._session.update_step(
                    BandCheckStepKey.HEADPHONES,
                    status=BandCheckStatus.ACTION_NEEDED,
                    detail=(
                        "WebJam couldn't play through the selected Studio output. "
                        "Open Settings, choose another output, then try again."
                    ),
                    next_action="Recording Setup",
                )
                self._render_session()
            return
        self._tone_played = True
        self._headphone_channels = evidence.channels
        QTimer.singleShot(
            max(250, int(evidence.duration_s * 1_000)),
            self._render_session,
        )

    def _advance_scratch_check(self) -> None:
        if self._scratch_evidence is None:
            self._start_scratch_recording()
        elif not self._scratch_evidence.valid or not self._scratch_evidence.has_signal:
            self._reset_scratch_check()
            self._start_scratch_recording()
        elif not self._scratch_played:
            self._play_scratch()
        else:
            if self._session is not None:
                self._session.confirm_scratch_playback(True)
                self._persist_verification_if_ready()
                self._delete_scratch()
                self._render_session()

    def _start_scratch_recording(self) -> None:
        session = self._session
        if session is None:
            return
        self._stop_input_probe()
        self._delete_scratch()
        settings = self._settings_provider()
        configured_root = str(getattr(settings, "takes_directory", "") or "").strip()
        scratch_parent = (
            Path(configured_root).expanduser()
            if configured_root
            else Path(str(getattr(settings, "config_file", "~/.webjam_config.json")))
            .expanduser()
            .parent
        )
        try:
            scratch_parent.mkdir(parents=True, exist_ok=True)
            self._scratch_root = Path(
                tempfile.mkdtemp(
                    prefix="webjam-band-check-",
                    dir=scratch_parent,
                )
            )
        except OSError:
            session.mark_scratch_recording(
                valid=False,
                detail=(
                    "WebJam can't write the recording folder. Choose another "
                    "Takes folder in Recording Setup, then try again."
                ),
            )
            session.update_step(
                BandCheckStepKey.TEST_RECORDING,
                status=BandCheckStatus.ACTION_NEEDED,
                detail=(
                    "WebJam can't write the recording folder. Choose another "
                    "Takes folder in Recording Setup, then try again."
                ),
                next_action="Recording Setup",
            )
            session.update_step(
                BandCheckStepKey.RECORDING_PATH,
                status=BandCheckStatus.ACTION_NEEDED,
                detail="The selected Takes folder is not writable.",
                next_action="Recording Setup",
            )
            self._render_session()
            return
        self._scratch = ScratchRecorder(
            self._scratch_root,
            device=_input_device_index(settings),
            sample_rate=int(getattr(settings, "audio_samplerate", 48_000) or 48_000),
            blocksize=int(getattr(settings, "audio_blocksize", 0) or 0),
            target_duration_s=5.0,
        )
        try:
            self._scratch.start()
        except BandCheckAudioError:
            self._delete_scratch()
            session.mark_scratch_recording(
                valid=False,
                detail=(
                    "WebJam couldn't start the five-second input recording. "
                    "Check the selected input and try again."
                ),
            )
            self._render_session()
            return
        session.update_step(
            BandCheckStepKey.TEST_RECORDING,
            status=BandCheckStatus.RUNNING,
            detail="Recording now — play or sing for five seconds.",
            next_action="Keep playing",
        )
        set_labeled_action(self._primary, "Recording 5 Seconds…")
        self._primary.setEnabled(False)
        self._secondary.setVisible(False)
        QTimer.singleShot(5_000, self._finish_scratch_recording)
        self._render_session()
        self._primary.setEnabled(False)

    def _finish_scratch_recording(self) -> None:
        recorder = self._scratch
        if recorder is None:
            return
        set_labeled_action(self._primary, "Checking the Recording…")
        self._primary.setEnabled(False)

        def worker() -> None:
            evidence = recorder.stop_and_validate()
            studio = (
                validate_studio_scratch(evidence.path)
                if evidence.valid and evidence.has_signal and evidence.path is not None
                else StudioCheckEvidence(
                    valid=False,
                    error=(
                        "Studio needs a test recording with usable input signal."
                        if evidence.valid
                        else evidence.error
                    ),
                )
            )
            self._scratch_ready.emit((evidence, studio))

        threading.Thread(
            target=worker,
            daemon=True,
            name="band-check-scratch-finalize",
        ).start()

    def _apply_scratch_evidence(self, evidence: object) -> None:
        studio = None
        if (
            isinstance(evidence, tuple)
            and len(evidence) == 2
            and isinstance(evidence[0], ScratchRecordingEvidence)
            and isinstance(evidence[1], StudioCheckEvidence)
        ):
            evidence, studio = evidence
        if not isinstance(evidence, ScratchRecordingEvidence) or self._session is None:
            return
        self._scratch_evidence = evidence
        self._scratch_played = False
        recording_detail = (
            "WebJam couldn't validate the five-second recording. Check the "
            "selected input and Takes folder, then try again."
            if not evidence.valid
            else ""
        )
        self._session.mark_scratch_recording(
            valid=evidence.valid,
            duration_s=evidence.duration_s,
            sample_rate=evidence.sample_rate,
            channels=evidence.channels,
            has_signal=evidence.has_signal,
            detail=recording_detail,
        )
        if studio is not None:
            self._session.mark_studio_check(
                valid=studio.valid,
                detail=(
                    "Studio couldn't safely open the five-second test recording. "
                    "Record it again."
                    if not studio.valid
                    else ""
                ),
            )
        self._render_session()

    def _play_scratch(self) -> None:
        if self._scratch is None or self._session is None:
            return
        settings = self._settings_provider()
        self._primary.setEnabled(False)
        set_labeled_action(self._primary, "Playing Quietly…")
        try:
            duration = self._scratch.play(
                output_device_name=str(
                    getattr(settings, "take_playback_output_device", "") or ""
                )
            )
        except BandCheckAudioError:
            self._session.update_step(
                BandCheckStepKey.TEST_RECORDING,
                status=BandCheckStatus.ACTION_NEEDED,
                detail=(
                    "WebJam couldn't play the five-second test through the "
                    "selected output. Choose another output and try again."
                ),
                next_action="Check the output and try again",
            )
            self._render_session()
            return

        def enable_confirmation() -> None:
            self._scratch_played = True
            self._render_session()

        QTimer.singleShot(max(250, int(duration * 1_000)), enable_confirmation)

    def _reset_scratch_check(self) -> None:
        self._delete_scratch()
        self._scratch_evidence = None
        self._scratch_played = False
        if self._session is None:
            return
        self._session.update_step(
            BandCheckStepKey.TEST_RECORDING,
            status=BandCheckStatus.PENDING,
            detail="Record, replay, and confirm a five-second isolated-input test.",
            next_action="Record 5 Seconds",
        )
        for key, detail in (
            (
                BandCheckStepKey.RECORDING_PATH,
                "A real write, finalization, and reopen check runs with the recording.",
            ),
            (
                BandCheckStepKey.STUDIO,
                "Waveform readability is checked from the finalized recording.",
            ),
        ):
            self._session.update_step(
                key,
                status=BandCheckStatus.PENDING,
                detail=detail,
                next_action="Record 5 Seconds",
            )

    def _delete_scratch(self) -> None:
        recorder, self._scratch = self._scratch, None
        temporary, self._scratch_root = self._scratch_root, None
        released = True
        if recorder is not None:
            try:
                released = recorder.delete()
            except Exception:  # noqa: BLE001
                LOGGER.debug("Scratch cleanup failed", exc_info=True)
                released = False
        if temporary is not None and released:
            try:
                shutil.rmtree(temporary, ignore_errors=True)
            except Exception:  # noqa: BLE001
                LOGGER.debug("Scratch directory cleanup failed", exc_info=True)
        elif temporary is not None and recorder is not None:
            # Never remove a libsndfile-owned path from the UI thread. Finish
            # deletion after the writer releases it, even if the dialog closes.
            def deferred_cleanup() -> None:
                try:
                    if recorder.delete(wait_timeout=None):
                        shutil.rmtree(temporary, ignore_errors=True)
                except Exception:  # noqa: BLE001
                    LOGGER.debug("Deferred scratch cleanup failed", exc_info=True)

            threading.Thread(
                target=deferred_cleanup,
                daemon=True,
                name="band-check-scratch-cleanup",
            ).start()

    def _refresh_live_observations(self) -> None:
        if (
            self._mode is not BandCheckMode.LIVE_OBSERVE
            or self._observations_provider is None
            or self._session is None
        ):
            return
        try:
            observations = self._observations_provider()
        except Exception:  # noqa: BLE001
            LOGGER.debug("Live Band Check observation failed", exc_info=True)
            return
        before = list(self._session.steps)
        before_evidence = self._session.evidence
        self._session.apply_live_observations(observations)
        if observations.local_meter_active:
            self._show_meter(
                observations.local_meter_rms,
                observations.local_meter_peak,
                observations.local_meter_clipped,
            )
        if before != self._session.steps or before_evidence != self._session.evidence:
            self._render_session()

    def _persist_verification_if_ready(self) -> None:
        session = self._session
        if (
            session is None
            or session.outcome is BandCheckOutcome.ACTION_NEEDED
            or self._verification_save_started
        ):
            return
        self._verification_save_started = True
        settings = deepcopy(self._settings_provider())
        expected_generation = getattr(self, "_settings_generation", None)

        def generation_is_current() -> bool:
            provider = self._settings_generation_provider
            if provider is None or expected_generation is None:
                return True
            try:
                return int(provider()) == int(expected_generation)
            except Exception:  # noqa: BLE001
                return False

        def worker() -> None:
            try:
                from webjam_qt import __version__

                if not generation_is_current():
                    return
                signature = build_verification_signature(
                    settings,
                    app_version=__version__,
                )
                if not generation_is_current():
                    return
                save_verification(
                    verification_path(settings),
                    signature=signature,
                    session=session,
                )
            except Exception:  # noqa: BLE001
                LOGGER.exception("Band Check verification could not be saved")

        threading.Thread(
            target=worker,
            daemon=True,
            name="band-check-verification",
        ).start()

    def _show_scan_failure(self, error: Exception) -> None:
        # The worker already records the failure through WebJam's redacted log
        # pipeline. Never echo ``str(error)`` here: device libraries can put
        # home paths, invite material, or backend command lines in it.
        self._clear_rows()
        self._session = None
        self._scan_failed = True
        self._summary.setText(BandCheckOutcome.ACTION_NEEDED.value)
        self._summary.setProperty("result", "fail")
        self._next.setText("Next: close Band Check and try again.")
        detail = QLabel("Band Check could not inspect this setup.")
        detail.setTextFormat(Qt.TextFormat.PlainText)
        detail.setWordWrap(True)
        self._report_layout.addWidget(detail)
        technical = QLabel(
            "The automatic scan stopped safely. Choose Try Again. If it repeats, "
            "save a Support Bundle."
        )
        technical.setObjectName("TechnicalDetailsText")
        technical.setTextFormat(Qt.TextFormat.PlainText)
        technical.setWordWrap(True)
        self._report_layout.addWidget(technical)
        set_labeled_action(self._primary, "Try Again")
        self._primary.setEnabled(True)
        self._action_step = None

    # ------------------------------------------------------------------
    # Compatibility rendering for old injected ReadyCheckReport instances.
    # Production runs always use BandCheckSession above.
    # ------------------------------------------------------------------
    def _render_legacy_report(self, report: object) -> None:
        self._clear_rows()
        raw_items = getattr(report, "items", [])
        items = list(raw_items) if isinstance(raw_items, (list, tuple)) else []
        self._items = items
        self._update_legacy_summary()
        for item in items:
            self._add_legacy_row(item)
        if not items:
            fallback = QLabel(_safe_report_text(report.to_text()))
            fallback.setTextFormat(Qt.TextFormat.PlainText)
            fallback.setWordWrap(True)
            self._report_layout.addWidget(fallback)
        self._report_layout.addStretch(1)
        set_labeled_action(self._primary, "Run Again")
        self._primary.setEnabled(True)

    def _update_legacy_summary(self) -> None:
        automatic = [
            item
            for item in self._items
            if item.required
            and not item.ok
            and not getattr(item, "manual_verification", False)
        ]
        manual = [
            item
            for item in self._items
            if item.required
            and not item.ok
            and getattr(item, "manual_verification", False)
        ]
        warnings = [item for item in self._items if not item.required and not item.ok]
        if automatic:
            count = len(automatic)
            text = (
                f"Fix {count} required item{'s' if count != 1 else ''} before the jam."
            )
        elif manual:
            count = len(manual)
            text = f"Automated checks passed; confirm {count} Webex setting{'s' if count != 1 else ''}."
        else:
            text = "Ready to play — all required checks passed."
        if warnings:
            text += (
                f" {len(warnings)} optional warning{'s' if len(warnings) != 1 else ''}."
            )
        self._summary.setText(text)

    def _add_legacy_row(self, item) -> QFrame:
        row = QFrame()
        manual = bool(getattr(item, "manual_verification", False))
        result = (
            "pass"
            if item.ok
            else "verify"
            if manual
            else "fail"
            if item.required
            else "warn"
        )
        row.setObjectName("ReadyCheckRow")
        row.setProperty("result", result)
        row.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        row.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        row.setAccessibleName(
            f"{'Passed' if item.ok else 'Manual verification' if manual else 'Required failure' if item.required else 'Optional warning'}: "
            f"{_safe_report_text(item.name)}"
        )
        if manual:
            mark = QCheckBox("VERIFY")
            mark.setChecked(bool(item.ok))

            def verified(checked: bool) -> None:
                item.ok = checked
                self._update_legacy_summary()

            mark.toggled.connect(verified)
        else:
            mark = QLabel("PASS" if item.ok else "FIX" if item.required else "OPTIONAL")
        mark.setObjectName("ReadyCheckMark")
        mark.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        name = QLabel(_safe_report_text(item.name))
        name.setObjectName("ReadyCheckName")
        name.setTextFormat(Qt.TextFormat.PlainText)
        detail = QLabel(_safe_report_text(item.detail or "No additional details"))
        detail.setObjectName("ReadyCheckDetail")
        detail.setTextFormat(Qt.TextFormat.PlainText)
        detail.setWordWrap(True)
        text = QVBoxLayout()
        text.addWidget(name)
        text.addWidget(detail)
        layout = QHBoxLayout(row)
        layout.addWidget(mark)
        layout.addLayout(text, stretch=1)
        self._report_layout.addWidget(row)
        return row

    def _clear_rows(self) -> None:
        while self._report_layout.count():
            item = self._report_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

    def closeEvent(self, event) -> None:  # noqa: N802
        self._scan_id += 1
        self._live_timer.stop()
        self._stop_input_probe()
        if self._tone_played:
            self._tone.stop()
        self._delete_scratch()
        self._items = []
        super().closeEvent(event)

    @staticmethod
    def _repolish(widget: QWidget) -> None:
        style = widget.style()
        style.unpolish(widget)
        style.polish(widget)


# One-release compatibility for imports/extensions/tests using the old name.
ReadyCheckDialog = BandCheckDialog


def _input_device_index(settings: object) -> int:
    try:
        return int(getattr(settings, "audio_input_device_index", -1))
    except (TypeError, ValueError):
        return -1
