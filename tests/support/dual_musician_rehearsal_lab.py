"""Repeatable source-level rehearsal proof for two WebJam musicians.

This lab deliberately exercises the real private peer runtime on loopback:
``HostPeerSession`` starts its HTTP service, ``GuestPeerSession`` enrolls and
uploads a real PCM WAV, and the host reconciles that transfer into a project.
Only the audio-device capture boundary is synthetic.  The durable report is
therefore explicit about what it proves and, just as importantly, what it
does not prove (real Jamulus/JACK/CoreAudio, LAN traversal, or audibility).

It belongs under ``tests.support`` rather than production code so a source
checkout can run it with no audio hardware, private network, or Jamulus
binary.  The caller supplies a temporary root and is responsible for a
test-scoped RFC1918 admission patch around :meth:`run`; production policy is
never relaxed here.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import threading
import time
import uuid
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Callable

import numpy as np
import soundfile as sf

from core.file_io import atomic_write_text
from core.local_capture import LocalCaptureGap, LocalCaptureResult
from core.network_invite import BandInvite, parse_invite_link
from core.session_transfer import (
    RecordingSignal,
    SessionCredentials,
    SessionPeerClient,
    SessionPeerServer,
    SessionTransferError,
    TransferAuthenticationError,
)
from core.session_transfer_runtime import GuestPeerSession, HostPeerSession
from core.studio_state import load_studio_state, save_studio_state
from core.take_export import TakeExportError, TrackMixSettings, export_track_package
from core.take_library import load_take
from core.take_player import TakePlayer
from core.take_project import (
    AlignmentState,
    MediaSegment,
    MediaStatus,
    Participant,
    ProjectStatus,
    ProjectTrack,
    RecoveryStatus,
    SourceQuality,
    SourceType,
    TakeProject,
    load_take_project,
    new_project_id,
    write_take_project,
)


LAB_REPORT_SCHEMA = "webjam.dual-musician-rehearsal-lab.v1"
CLEANUP_SCHEMA = "webjam.dual-musician-rehearsal-cleanup.v1"
_RATE = 48_000
_FRAMES = 9_600
_GAP_START = 2_400
_GAP_FRAMES = 480
_SAFE_REPORT_ROOT = "dual-musician-rehearsal-lab"


@dataclass(frozen=True)
class DualMusicianLabResult:
    """Artifacts made by a completed source-level lab run.

    Paths are returned to the test process for assertions, but the serialized
    reports intentionally contain only opaque run identity and aggregate facts.
    """

    run_id: str
    artifact_root: Path
    report_path: Path
    cleanup_path: Path
    gapped_take_dir: Path
    clean_take_dir: Path
    export_folder: Path
    report: dict[str, object]
    cleanup: dict[str, object]


@dataclass(frozen=True)
class _SyntheticCapturePlan:
    label: str
    frequency: float
    gap: LocalCaptureGap | None = None


class _SyntheticCapture:
    """Small deterministic stand-in for only the local audio-device boundary."""

    def __init__(
        self,
        root: Path,
        *,
        plan: _SyntheticCapturePlan,
        samplerate: int,
        take_id: str,
    ) -> None:
        self.root = Path(root)
        self.plan = plan
        self.samplerate = int(samplerate)
        self.take_id = take_id
        self.started = False
        self.stop_calls = 0

    def start(self) -> None:
        self.started = True

    def stop_into(self, destination: Path) -> LocalCaptureResult:
        if not self.started:
            raise AssertionError("Synthetic capture was finalized before it started.")
        self.stop_calls += 1
        source = Path(destination) / "input-1.wav"
        _write_pcm24_sine(
            source,
            frequency=self.plan.frequency,
            gap=self.plan.gap,
        )
        return LocalCaptureResult(
            files=(source,),
            started_utc="2026-07-15T00:00:00Z",
            started_monotonic=0.0,
            duration_s=_FRAMES / _RATE,
            errors=(),
            gaps=(self.plan.gap,) if self.plan.gap is not None else (),
            total_frames=_FRAMES,
            capture_device=SimpleNamespace(device_id=""),
            durable_frames=_FRAMES,
        )


class _SyntheticCaptureFactory:
    """Consumes a known, finite capture plan so takes cannot run unbounded."""

    def __init__(self, plans: tuple[_SyntheticCapturePlan, ...]) -> None:
        self._plans = list(plans)
        self.instances: list[_SyntheticCapture] = []

    def __call__(
        self,
        root: Path,
        *,
        device: int,
        samplerate: int,
        blocksize: int,
        take_id: str,
        session_id: str,
    ) -> _SyntheticCapture:
        del device, blocksize, session_id
        if not self._plans:
            raise AssertionError("The lab requested an unexpected extra capture.")
        capture = _SyntheticCapture(
            Path(root),
            plan=self._plans.pop(0),
            samplerate=samplerate,
            take_id=take_id,
        )
        self.instances.append(capture)
        return capture


class _NoopSink:
    """Avoid a sound-device dependency while exercising Studio transport seek."""

    def start(self, _samplerate: int, _blocksize: int, _pull: Callable) -> None:
        return

    def stop(self) -> None:
        return


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_pcm24_sine(
    path: Path,
    *,
    frequency: float,
    gap: LocalCaptureGap | None = None,
) -> None:
    """Write bounded deterministic source media with an optional declared gap."""

    path.parent.mkdir(parents=True, exist_ok=True)
    timeline = np.arange(_FRAMES, dtype=np.float64) / _RATE
    samples = (0.25 * np.sin(2.0 * np.pi * frequency * timeline)).astype(np.float32)
    if gap is not None:
        samples[gap.start_frame : gap.end_frame] = 0.0
    sf.write(path, samples, _RATE, subtype="PCM_24")
    os.chmod(path, 0o600)


def _unused_loopback_port() -> int:
    """Reserve then release an unused port for one real connection-refused probe."""

    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])
    finally:
        probe.close()


def _port_is_released(port: int) -> bool:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.settimeout(0.25)
        return probe.connect_ex(("127.0.0.1", int(port))) != 0
    finally:
        probe.close()


class DualMusicianRehearsalLab:
    """One deterministic source-level pass through the dual-musician workflow."""

    def __init__(self, root: str | Path, *, run_id: str | None = None) -> None:
        self._outer_root = Path(root).expanduser().resolve()
        self.run_id = run_id or f"run-{uuid.uuid4().hex[:16]}"
        self.artifact_root = self._outer_root / _SAFE_REPORT_ROOT / self.run_id
        self.report_path = self.artifact_root / "lab-report.json"
        self.cleanup_path = self.artifact_root / "cleanup-manifest.json"
        self._steps: list[dict[str, object]] = []
        self._evidence: list[dict[str, object]] = []
        self._host: HostPeerSession | None = None
        self._guest: GuestPeerSession | None = None
        self._reconnected_guest: GuestPeerSession | None = None
        self._factory: _SyntheticCaptureFactory | None = None
        self._invite: BandInvite | None = None
        self._forbidden: set[str] = {str(self._outer_root), str(self.artifact_root)}
        self._artifact_secrets: set[str] = set()
        self._primary_peer_port = 0
        self._primary_session_id = ""
        self._host_start_started_at = 0.0
        self._invite_ready_ms = 0
        self._guest_connect_ms = 0
        self._returning_join_ms = 0
        self._returning_host_ms = 0
        self._gapped_take_dir: Path | None = None
        self._clean_take_dir: Path | None = None
        self._export_folder: Path | None = None
        self._failure = ""
        self._thread_ids_before = {
            thread.ident for thread in threading.enumerate() if thread.ident is not None
        }

    def run(self) -> DualMusicianLabResult:
        """Run, verify, clean up, and serialize the entire source-level lab."""

        self.artifact_root.mkdir(parents=True, exist_ok=False, mode=0o700)
        os.chmod(self.artifact_root, 0o700)
        cleanup: dict[str, object] = {}
        try:
            self._measure("host_start", "real_loopback_component", 5_000, self._start_host)
            self._measure("invite_privacy", "source_observed", 2_000, self._exercise_invite)
            self._measure(
                "enrollment_identity",
                "real_loopback_component",
                5_000,
                self._enroll_guest,
            )
            self._measure(
                "gapped_take_control_recovery",
                "synthetic_capture_plus_real_loopback_control",
                8_000,
                self._exercise_gapped_take,
            )
            self._measure(
                "clean_take_transfer_recovery",
                "synthetic_capture_plus_real_loopback_transfer",
                8_000,
                self._exercise_clean_take,
            )
            self._measure(
                "identity_reconnect_idempotence",
                "real_loopback_component",
                5_000,
                self._exercise_identity_reconnect,
            )
            self._measure(
                "studio_seek_state_export",
                "source_artifact_exercise",
                12_000,
                self._exercise_studio_and_export,
            )
            self._measure(
                "privacy_artifact_scan",
                "source_observed",
                3_000,
                self._assert_private_artifacts,
            )
        except BaseException as exc:
            # Keep failure text generic: test reports must never serialize an
            # endpoint, local path, credential, or arbitrary exception detail.
            self._failure = type(exc).__name__
            raise
        finally:
            cleanup = self._cleanup_and_relaunch()
            report = self._report_document(cleanup)
            self._assert_report_is_sanitized(cleanup)
            self._write_json(self.report_path, report)
            self._write_json(self.cleanup_path, cleanup)

        if self._gapped_take_dir is None or self._clean_take_dir is None:
            raise AssertionError("The lab did not produce both bounded takes.")
        if self._export_folder is None:
            raise AssertionError("The lab did not produce its PCM24 export.")
        return DualMusicianLabResult(
            run_id=self.run_id,
            artifact_root=self.artifact_root,
            report_path=self.report_path,
            cleanup_path=self.cleanup_path,
            gapped_take_dir=self._gapped_take_dir,
            clean_take_dir=self._clean_take_dir,
            export_folder=self._export_folder,
            report=report,
            cleanup=cleanup,
        )

    def _measure(
        self,
        step_id: str,
        classification: str,
        target_ms: int,
        operation: Callable[[], None],
    ) -> None:
        started = time.perf_counter()
        try:
            operation()
        except BaseException:
            elapsed = max(0, round((time.perf_counter() - started) * 1000))
            self._steps.append(
                {
                    "id": step_id,
                    "classification": classification,
                    "status": "failed",
                    "elapsed_ms": elapsed,
                    "target_ms": target_ms,
                    "within_target": elapsed <= target_ms,
                }
            )
            raise
        elapsed = max(0, round((time.perf_counter() - started) * 1000))
        self._steps.append(
            {
                "id": step_id,
                "classification": classification,
                "status": "passed",
                "elapsed_ms": elapsed,
                "target_ms": target_ms,
                "within_target": elapsed <= target_ms,
            }
        )

    def _start_host(self) -> None:
        self._host_start_started_at = time.perf_counter()
        host = HostPeerSession()
        host.start(
            "127.0.0.1",
            takes_root=self.artifact_root / "host-takes",
            installation_path=self.artifact_root / "host-installation.json",
            display_name="Host musician",
        )
        if not host.active or not isinstance(host.server, SessionPeerServer):
            raise AssertionError("The real host peer service did not become active.")
        if host.credentials is None or host.host_enrollment is None or host.peer_port <= 0:
            raise AssertionError("The host did not issue a usable private session.")
        self._host = host
        self._primary_peer_port = host.peer_port
        self._primary_session_id = host.session_id
        self._forbidden.update(
            {
                host.credentials.invite_token,
                host.credentials.session_id,
                host.host_enrollment.participant_token,
            }
        )
        self._artifact_secrets.update(
            {host.credentials.invite_token, host.host_enrollment.participant_token}
        )
        self._evidence.append(
            {
                "id": "host_peer_service",
                "classification": "real_loopback_component",
                "outcome": "passed",
                "facts": {"http_service_started": True, "credential_rotated": True},
            }
        )

    def _exercise_invite(self) -> None:
        host = self._require_host()
        if host.credentials is None:
            raise AssertionError("The host credentials disappeared before invite creation.")
        # A real v2 invite must use an RFC1918 address. The parsed invite is
        # subsequently copied to a loopback-only endpoint for this isolated lab.
        invite_url = host.invite_link(
            host="192.168.50.24",
            jamulus_port=22_124,
            session_name="Dual Musician Lab",
        )
        parsed = parse_invite_link(invite_url)
        if not isinstance(parsed, BandInvite) or not parsed.peer_enabled:
            raise AssertionError("The private invitation did not round-trip.")
        if parsed.invite_token != host.credentials.invite_token:
            raise AssertionError("The parsed invite did not retain its enrollment bearer.")
        if host.credentials.invite_token in repr(parsed) or host.credentials.invite_token in repr(host.credentials):
            raise AssertionError("Private invite credentials appeared in a runtime repr.")
        self._invite = BandInvite(
            host="127.0.0.1",
            port=parsed.port,
            session_name=parsed.session_name,
            session_id=parsed.session_id,
            peer_port=parsed.peer_port,
            invite_token=parsed.invite_token,
        )
        self._invite_ready_ms = max(
            0,
            round((time.perf_counter() - self._host_start_started_at) * 1000),
        )
        self._forbidden.update({invite_url, parsed.invite_token, parsed.session_id})
        self._artifact_secrets.update({invite_url, parsed.invite_token})
        self._evidence.append(
            {
                "id": "private_invite",
                "classification": "source_observed",
                "outcome": "passed",
                "facts": {
                    "v2_round_trip": True,
                    "runtime_repr_redacted": True,
                    "lab_artifacts_checked_for_bearer": True,
                },
                "limitation": "The v2 URL itself carries an enrollment bearer; this lab does not exercise argv or FileOpen ingress.",
            }
        )

    def _enroll_guest(self) -> None:
        invite = self._require_invite()
        guest_connect_started = time.perf_counter()
        self._factory = _SyntheticCaptureFactory(
            (
                _SyntheticCapturePlan(
                    "gapped",
                    330.0,
                    LocalCaptureGap(
                        _GAP_START,
                        _GAP_FRAMES,
                        (0,),
                        "synthetic_lab_gap",
                    ),
                ),
                _SyntheticCapturePlan("clean", 440.0),
            )
        )
        guest = self._new_guest(invite, self._factory)
        guest.observe_presence(7, "Guest musician")
        first = guest.poll_once()
        second = guest.poll_once()
        if first.signal is not RecordingSignal.IDLE or second.signal is not RecordingSignal.IDLE:
            raise AssertionError("The newly enrolled guest did not observe idle recording state.")
        if not guest.participant_id or guest.enrollment is None:
            raise AssertionError("The guest did not receive a durable enrollment.")
        host = self._require_host()
        if host.registry is None:
            raise AssertionError("The host registry is unavailable after enrollment.")
        if len(host.registry.participants()) != 2:
            raise AssertionError("Idempotent polling created a duplicate participant.")
        if not isinstance(guest.client, SessionPeerClient):
            raise AssertionError("The guest did not use the real peer HTTP client.")
        self._guest_connect_ms = max(
            0,
            round((time.perf_counter() - guest_connect_started) * 1000),
        )
        self._guest = guest
        self._forbidden.add(guest.enrollment.participant_token)
        self._artifact_secrets.add(guest.enrollment.participant_token)
        self._evidence.append(
            {
                "id": "guest_enrollment",
                "classification": "real_loopback_component",
                "outcome": "passed",
                "facts": {
                    "one_guest_identity": True,
                    "duplicate_enrollment_prevented": True,
                    "presence_bound": True,
                },
            }
        )

    def _exercise_gapped_take(self) -> None:
        guest = self._require_guest()
        host = self._require_host()
        take_id = str(uuid.uuid4())
        snapshot = host.begin_take(take_id, started_utc="2026-07-15T00:00:00Z")
        if snapshot is None or snapshot.signal is not RecordingSignal.RECORDING:
            raise AssertionError("The host did not begin the bounded gapped take.")
        guest.poll_once()
        if guest.active_take_id != take_id:
            raise AssertionError("The guest did not retain an active local capture.")

        # This is a real TCP connection-refused control-plane interruption. It
        # intentionally does not claim a Jamulus or physical-LAN outage.
        original_port = guest.client.port
        guest.client.port = _unused_loopback_port()
        try:
            try:
                guest.poll_once()
            except SessionTransferError:
                pass
            else:
                raise AssertionError("The lab control-plane outage unexpectedly succeeded.")
        finally:
            guest.client.port = original_port
        if guest.active_take_id != take_id:
            raise AssertionError("A control-plane outage interrupted local capture.")
        recovered_state = guest.poll_once()
        if recovered_state.signal is not RecordingSignal.RECORDING:
            raise AssertionError("The guest did not recover peer state after outage.")

        host.finish_take(take_id, stopped_utc="2026-07-15T00:00:01Z")
        take_dir = self.artifact_root / "takes" / "gapped-take"
        self._write_base_project(take_dir, take_id)
        host.register_take(take_id, take_dir)
        self._reconcile_until(
            take_id,
            take_dir,
            lambda project: project.status is ProjectStatus.NEEDS_ATTENTION,
        )
        initial = load_take_project(take_dir)
        if initial.status is not ProjectStatus.NEEDS_ATTENTION or not initial.errors:
            raise AssertionError("Missing guest media was not made visible before recovery.")
        guest.poll_once()
        self._reconcile_until(take_id, take_dir, lambda item: bool(item.tracks))
        project = load_take_project(take_dir)
        local_tracks = [
            track
            for track in project.tracks
            if track.source_type is SourceType.LOCAL_ISOLATED
        ]
        if len(local_tracks) != 1:
            raise AssertionError("The gapped guest transfer attached more or fewer than one track.")
        segment = local_tracks[0].primary_segment
        if (
            project.status is not ProjectStatus.NEEDS_ATTENTION
            or local_tracks[0].media_status is not MediaStatus.PARTIAL
            or len(segment.gaps) != 1
            or segment.gaps[0].start_frame != _GAP_START
            or segment.gaps[0].frame_count != _GAP_FRAMES
        ):
            raise AssertionError("The synthetic source gap was not disclosed through transfer reconciliation.")
        if self._factory is None or len(self._factory.instances) != 1:
            raise AssertionError("The first bounded capture was not created exactly once.")
        if self._factory.instances[0].stop_calls != 1:
            raise AssertionError("The first bounded capture was not finalized exactly once.")
        gapped_take = load_take(take_dir)
        if gapped_take is None:
            raise AssertionError("The gapped project could not be opened for export safety.")
        blocked_export_root = self.artifact_root / "gapped-export-attempt"
        try:
            export_track_package(gapped_take, destination_root=blocked_export_root)
        except TakeExportError:
            pass
        else:
            raise AssertionError("A disclosed partial source was exported as if it were ready.")
        if blocked_export_root.exists() and any(blocked_export_root.iterdir()):
            raise AssertionError("The blocked gapped export left a visible package behind.")
        self._gapped_take_dir = take_dir
        self._evidence.append(
            {
                "id": "gapped_capture_transfer",
                "classification": "synthetic_capture_plus_real_loopback_transfer",
                "outcome": "passed",
                "facts": {
                    "bounded_capture_frames": _FRAMES,
                    "declared_gap_frames": _GAP_FRAMES,
                    "control_outage_recovered_without_capture_stop": True,
                    "host_reconciliation_marks_attention": True,
                    "track_export_safely_blocked": True,
                },
                "limitation": "The gap is injected at the synthetic capture boundary; it is not evidence of Jamulus media loss.",
            }
        )

    def _exercise_clean_take(self) -> None:
        guest = self._require_guest()
        host = self._require_host()
        take_id = str(uuid.uuid4())
        snapshot = host.begin_take(take_id, started_utc="2026-07-15T00:01:00Z")
        if snapshot is None or snapshot.signal is not RecordingSignal.RECORDING:
            raise AssertionError("The host did not begin the clean bounded take.")
        guest.poll_once()
        if guest.active_take_id != take_id:
            raise AssertionError("The clean take did not start local capture.")
        host.finish_take(take_id, stopped_utc="2026-07-15T00:01:01Z")
        take_dir = self.artifact_root / "takes" / "clean-take"
        self._write_base_project(take_dir, take_id)
        host.register_take(take_id, take_dir)
        self._reconcile_until(
            take_id,
            take_dir,
            lambda project: project.status is ProjectStatus.NEEDS_ATTENTION,
        )
        missing = load_take_project(take_dir)
        if missing.status is not ProjectStatus.NEEDS_ATTENTION:
            raise AssertionError("The host did not disclose the pending clean transfer.")
        # Finalize through the real guest runtime, then deliberately publish a
        # first real HTTP chunk. The queued guest upload must inspect that host
        # checkpoint and resume from it, rather than attach a duplicate segment.
        guest._finalize_capture()
        pending = next(
            item
            for item in reversed(guest.pending_segments)
            if item.status != "verified"
        )
        if guest.enrollment is None:
            raise AssertionError("The guest lost enrollment before partial transfer.")
        source_bytes = pending.source.read_bytes()
        partial_size = min(1_024, len(source_bytes) - 1)
        if partial_size <= 0:
            raise AssertionError("The bounded source was too small for a partial upload.")
        partial = guest.client._request(
            "PUT",
            "/v1/segment",
            token=guest.enrollment.participant_token,
            participant_id=guest.enrollment.participant_id,
            body=source_bytes[:partial_size],
            headers={
                "Content-Type": "application/octet-stream",
                "X-WebJam-Offset": "0",
                "X-WebJam-Descriptor": json.dumps(
                    asdict(pending.descriptor), separators=(",", ":")
                ),
            },
        )
        if int(partial["received_bytes"]) != partial_size or bool(partial["complete"]):
            raise AssertionError("The host did not retain the expected resumable partial.")
        checkpoint = guest.client.transfer_status(guest.enrollment, pending.descriptor)
        if checkpoint.complete or checkpoint.received_bytes != partial_size:
            raise AssertionError("The guest could not observe the host partial-upload checkpoint.")
        guest._upload_pending()
        resumed = next(
            item for item in guest.pending_segments if item.descriptor == pending.descriptor
        )
        if resumed.status != "verified":
            raise AssertionError("The guest queue did not resume and verify its partial upload.")
        self._reconcile_until(
            take_id,
            take_dir,
            lambda item: item.status is ProjectStatus.COMPLETE and not item.errors,
        )
        project = load_take_project(take_dir)
        local_tracks = [
            track
            for track in project.tracks
            if track.source_type is SourceType.LOCAL_ISOLATED
        ]
        if len(local_tracks) != 1 or local_tracks[0].media_status is not MediaStatus.AVAILABLE:
            raise AssertionError("The clean guest transfer did not attach as verified media.")
        if project.session_evidence.recovery_status is not RecoveryStatus.RECOVERED:
            raise AssertionError("The delayed clean transfer did not leave recovery evidence.")
        if not any(
            event.event == "peer_original_recovered"
            for event in project.session_evidence.timeline
        ):
            raise AssertionError("The clean transfer recovery timeline event is missing.")
        # Repeated host reconciliation is a real idempotence check, not a mock.
        if host.reconcile_take(take_id, take_dir):
            raise AssertionError("A stable clean transfer was attached more than once.")
        if self._factory is None or len(self._factory.instances) != 2:
            raise AssertionError("The clean bounded capture was not created exactly once.")
        if self._factory.instances[1].stop_calls != 1:
            raise AssertionError("The clean bounded capture was not finalized exactly once.")
        self._clean_take_dir = take_dir
        self._evidence.append(
            {
                "id": "clean_transfer_recovery",
                "classification": "synthetic_capture_plus_real_loopback_transfer",
                "outcome": "passed",
                "facts": {
                    "initial_missing_transfer_disclosed": True,
                    "checksum_verified_upload_attached": True,
                    "real_partial_upload_resumed_from_checkpoint": True,
                    "recovery_timeline_recorded": True,
                    "reconcile_idempotent": True,
                },
            }
        )

    def _exercise_identity_reconnect(self) -> None:
        guest = self._require_guest()
        original_id = guest.participant_id
        guest.stop()
        invite = self._require_invite()
        # Reuse exactly the same guest installation identity file, but a new
        # runtime object and real HTTP client.
        returning_join_started = time.perf_counter()
        reconnected = self._new_guest(invite, _SyntheticCaptureFactory(()))
        reconnected.observe_presence(9, "Guest musician returning")
        reconnected.poll_once()
        self._returning_join_ms = max(
            0,
            round((time.perf_counter() - returning_join_started) * 1000),
        )
        if reconnected.participant_id != original_id:
            raise AssertionError("Guest relaunch changed the durable participant identity.")
        host = self._require_host()
        if host.registry is None or len(host.registry.participants()) != 2:
            raise AssertionError("Guest relaunch created a duplicate enrollment record.")
        rebound = host.registry.presence_for_participant(original_id)
        if rebound is None or rebound.display_name != "Guest musician returning":
            raise AssertionError("Returning guest presence did not retain its renamed identity.")
        clean_dir = self._require_clean_take_dir()
        clean_project = load_take_project(clean_dir)
        before_tracks = len(
            [
                track
                for track in clean_project.tracks
                if track.source_type is SourceType.LOCAL_ISOLATED
            ]
        )
        host.reconcile_take(clean_project.take_id, clean_dir)
        after_tracks = len(
            [
                track
                for track in load_take_project(clean_dir).tracks
                if track.source_type is SourceType.LOCAL_ISOLATED
            ]
        )
        if before_tracks != 1 or after_tracks != 1:
            raise AssertionError("Guest reconnect duplicated or removed clean project media.")
        self._reconnected_guest = reconnected
        self._evidence.append(
            {
                "id": "guest_reconnect_identity",
                "classification": "real_loopback_component",
                "outcome": "passed",
                "facts": {
                    "same_installation_same_participant": True,
                    "participant_count_stable": True,
                    "renamed_presence_rebound": True,
                    "reconnect_did_not_duplicate_media": True,
                },
            }
        )

    def _exercise_studio_and_export(self) -> None:
        take_dir = self._require_clean_take_dir()
        project = load_take_project(take_dir)
        local_tracks = [
            track
            for track in project.tracks
            if track.source_type is SourceType.LOCAL_ISOLATED
        ]
        if len(local_tracks) != 1:
            raise AssertionError("The clean project lost its guest source before Studio.")
        # The synthetic host and guest WAVs are generated at the same known
        # zero origin. This fixture alignment permits export mechanics to be
        # exercised; it is explicitly not production alignment evidence.
        aligned_tracks = tuple(
            replace(
                track,
                alignment=AlignmentState(
                    automatic_offset_s=0.0,
                    confidence=1.0,
                    method="synthetic-lab-zero-origin",
                    residual_ms=0.0,
                ),
            )
            if track.source_type is SourceType.LOCAL_ISOLATED
            else track
            for track in project.tracks
        )
        project = replace(
            project,
            tracks=aligned_tracks,
            revision=project.revision + 1,
        )
        write_take_project(take_dir, project)
        host = self._require_host()
        host.reconcile_take(project.take_id, take_dir)
        project = load_take_project(take_dir)
        manifest_before_studio = (take_dir / "webjam-take.json").read_bytes()
        source_bytes = {
            segment.path: (take_dir / segment.path).read_bytes()
            for track in project.tracks
            for segment in track.segments
        }

        take = load_take(take_dir)
        if take is None or len(take.tracks) != 2:
            raise AssertionError("Studio could not load both clean project tracks.")
        player = TakePlayer(sink=_NoopSink())
        player.load(take)
        seek_s = min(0.1, player.duration_s / 2.0)
        player.seek(seek_s)
        if abs(player.position_s - seek_s) > 1 / _RATE:
            raise AssertionError("Studio transport did not seek to the requested project time.")
        player.stop()

        state = load_studio_state(take_dir)
        guest_track = next(
            track
            for track in project.tracks
            if track.source_type is SourceType.LOCAL_ISOLATED
        )
        changed = state.update_track(
            guest_track.track_id,
            gain=0.75,
            pan=-0.2,
            muted=False,
            solo=False,
            export_included=True,
        )
        sidecar = save_studio_state(take_dir, changed)
        restored = load_studio_state(take_dir)
        saved_guest = restored.state_for(guest_track.track_id)
        if (
            saved_guest.gain != 0.75
            or saved_guest.pan != -0.2
            or not saved_guest.export_included
            or not sidecar.is_file()
        ):
            raise AssertionError("Studio state did not persist by durable track identity.")
        if (take_dir / "webjam-take.json").read_bytes() != manifest_before_studio:
            raise AssertionError("Studio state changed immutable project evidence.")

        mix_settings = {
            item.track_id: TrackMixSettings(
                gain=item.gain,
                pan=item.pan,
                muted=item.muted,
                solo=item.solo,
            )
            for item in restored.tracks
        }
        export = export_track_package(
            take,
            destination_root=self.artifact_root / "exports",
            mix_settings=mix_settings,
            include_processed_stems=True,
        )
        if len(export.stems) != 2 or not export.manifest.is_file():
            raise AssertionError("The dual-track export did not produce its expected package.")
        if export.checksums is None or export.analysis is None:
            raise AssertionError("The dual-track export omitted checksum or analysis evidence.")
        for stem in export.stems:
            info = sf.info(str(stem))
            if (
                info.subtype != "PCM_24"
                or int(info.samplerate) != _RATE
                or int(info.frames) != export.frames
            ):
                raise AssertionError("The exported stem is not a zero-aligned 48 kHz PCM24 WAV.")
        manifest = json.loads(export.manifest.read_text(encoding="utf-8"))
        if (
            manifest.get("bit_depth") != 24
            or not manifest.get("all_stems_start_at_zero")
            or not manifest.get("all_stems_same_length")
            or manifest.get("external_editor_physically_verified") is not False
        ):
            raise AssertionError("Export evidence made an invalid external-editor claim.")
        for track in manifest.get("tracks", []):
            output = export.folder / str(track["output_filename"])
            if _sha256(output) != track["output_sha256"]:
                raise AssertionError("The export manifest checksum disagrees with its rendered stem.")
        for line in export.checksums.read_text(encoding="utf-8").splitlines():
            digest, filename = line.split("  ", 1)
            if _sha256(export.folder / filename) != digest:
                raise AssertionError("The export checksum inventory is not self-consistent.")
        analysis = json.loads(export.analysis.read_text(encoding="utf-8"))
        analysis_by_name = {item["filename"]: item for item in analysis.get("files", [])}
        analyzed_files = [*export.stems, *export.processed_stems, export.mixdown]
        for audio in analyzed_files:
            observed = analysis_by_name.get(audio.name)
            if (
                observed is None
                or observed["sha256"] != _sha256(audio)
                or observed["sample_rate"] != _RATE
                or observed["frames"] != export.frames
            ):
                raise AssertionError("The export audio analysis is incomplete or inconsistent.")
        for relative, before in source_bytes.items():
            if (take_dir / relative).read_bytes() != before:
                raise AssertionError("Studio/export mutated an original source WAV.")
        self._export_folder = export.folder
        self._evidence.append(
            {
                "id": "studio_and_pcm24_export",
                "classification": "source_artifact_exercise",
                "outcome": "passed",
                "facts": {
                    "seek_verified": True,
                    "durable_track_keyed_state": True,
                    "source_media_unchanged": True,
                    "pcm24_stems": 2,
                    "export_checksums_verified": True,
                    "export_analysis_verified": True,
                    "external_editor_physical_import_claim": False,
                },
                "limitation": "The export uses a known synthetic zero-origin alignment fixture, not a production alignment or human review claim.",
            }
        )

    def _assert_private_artifacts(self) -> None:
        # The invitation bearer is necessarily present in memory while joining,
        # but must not appear in persisted lab artifacts or reports.
        forbidden = {item.encode("utf-8") for item in self._artifact_secrets if item}
        for path in self.artifact_root.rglob("*"):
            if not path.is_file():
                continue
            payload = path.read_bytes()
            if any(item in payload for item in forbidden):
                raise AssertionError("A private runtime value appeared in a lab artifact.")

    def _reconcile_until(
        self,
        take_id: str,
        take_dir: Path,
        predicate: Callable[[TakeProject], bool],
    ) -> None:
        host = self._require_host()
        for _attempt in range(3):
            host.reconcile_take(take_id, take_dir)
            project = load_take_project(take_dir)
            if predicate(project):
                return
        raise AssertionError("Host reconciliation did not reach its expected durable state.")

    def _write_base_project(self, take_dir: Path, take_id: str) -> None:
        host = self._require_host()
        if host.credentials is None or host.host_enrollment is None:
            raise AssertionError("Host identity is unavailable for the test take.")
        take_dir.mkdir(parents=True, exist_ok=False, mode=0o700)
        host_source = take_dir / "host-reference.wav"
        _write_pcm24_sine(host_source, frequency=220.0)
        info = sf.info(str(host_source))
        segment = MediaSegment(
            segment_id=new_project_id(),
            path=host_source.name,
            project_start_frame=0,
            frame_count=int(info.frames),
            sample_rate=int(info.samplerate),
            channels=int(info.channels),
            sample_format=str(info.subtype),
            media_status=MediaStatus.AVAILABLE,
            sha256=_sha256(host_source),
            size_bytes=host_source.stat().st_size,
            has_signal=None,
        )
        project = TakeProject(
            session_id=host.credentials.session_id,
            take_id=take_id,
            session_title="Dual musician source-level lab",
            take_name=take_dir.name,
            status=ProjectStatus.COMPLETE,
            project_sample_rate=_RATE,
            participants=(
                Participant(host.host_enrollment.participant_id, "Host musician"),
            ),
            tracks=(
                ProjectTrack(
                    track_id=new_project_id(),
                    source_id=new_project_id(),
                    participant_id=host.host_enrollment.participant_id,
                    name="Host synthetic reference",
                    instrument="",
                    source_type=SourceType.LIVE_REFERENCE,
                    quality=SourceQuality.REFERENCE,
                    media_status=MediaStatus.AVAILABLE,
                    order=0,
                    segments=(segment,),
                    alignment=AlignmentState(
                        confidence=1.0,
                        method="synthetic-lab-zero-origin",
                    ),
                ),
            ),
        )
        write_take_project(take_dir, project)

    def _new_guest(
        self,
        invite: BandInvite,
        capture_factory: _SyntheticCaptureFactory,
    ) -> GuestPeerSession:
        return GuestPeerSession(
            invite,
            display_name="Guest musician",
            takes_root=self.artifact_root / "guest-takes",
            installation_path=self.artifact_root / "guest-installation.json",
            capture_enabled=lambda: True,
            capture_config=lambda: (0, _RATE, 128),
            capture_factory=capture_factory,
        )

    def _cleanup_and_relaunch(self) -> dict[str, object]:
        """Stop all runtime-owned resources, prove port release, then relaunch."""

        reconnected = self._reconnected_guest
        if reconnected is not None:
            reconnected.stop()
            self._reconnected_guest = None
        guest = self._guest
        if guest is not None:
            guest.stop()
            self._guest = None
        host = self._host
        primary_released = False
        if host is not None:
            host.stop()
            primary_released = _port_is_released(self._primary_peer_port)
            self._host = None

        relaunch = HostPeerSession()
        relaunch_port = 0
        relaunch_rotated = False
        stale_invite_rejected = False
        try:
            returning_host_started = time.perf_counter()
            relaunch.start(
                "127.0.0.1",
                takes_root=self.artifact_root / "relaunch-takes",
                installation_path=self.artifact_root / "host-installation.json",
                display_name="Host musician",
            )
            relaunch_port = relaunch.peer_port
            relaunch_rotated = bool(
                relaunch.active and relaunch.session_id != self._primary_session_id
            )
            self._returning_host_ms = max(
                0,
                round((time.perf_counter() - returning_host_started) * 1000),
            )
            invite = self._invite
            if invite is not None:
                stale_client = SessionPeerClient(
                    "127.0.0.1",
                    relaunch_port,
                    credentials=SessionCredentials(
                        invite.session_id,
                        invite.invite_token,
                    ),
                )
                try:
                    stale_client.enroll(str(uuid.uuid4()), "Stale guest")
                except TransferAuthenticationError:
                    stale_invite_rejected = True
        finally:
            relaunch.stop()
        relaunch_released = _port_is_released(relaunch_port) if relaunch_port else False
        leaked_threads = sorted(
            thread.name
            for thread in threading.enumerate()
            if thread.ident not in self._thread_ids_before
            and thread.name
            in {
                "webjam-host-transfer-maintenance",
                "webjam-session-peer",
                "webjam-guest-recording-transfer",
            }
        )
        originals_preserved = bool(
            self._gapped_take_dir is not None
            and self._clean_take_dir is not None
            and (self.artifact_root / "guest-takes" / "WebJam Local Originals").is_dir()
        )
        cleanup: dict[str, object] = {
            "schema_version": CLEANUP_SCHEMA,
            "run_id": self.run_id,
            "classification": "source_level_cleanup",
            "primary_peer_port_released": primary_released,
            "relaunch_started_with_rotated_session": relaunch_rotated,
            "relaunch_peer_port_released": relaunch_released,
            "stale_previous_invite_rejected": stale_invite_rejected,
            "new_webjam_runtime_threads": leaked_threads,
            "preserved_local_originals_remain": originals_preserved,
            "limitations": [
                "This checks lab-owned loopback resources only.",
                "It does not prove cleanup of a real Jamulus, JACK, or CoreAudio process.",
            ],
        }
        if not (
            primary_released
            and relaunch_rotated
            and relaunch_released
            and stale_invite_rejected
            and not leaked_threads
            and originals_preserved
        ):
            raise AssertionError("The source-level cleanup/relaunch checks did not pass.")
        return cleanup

    def _report_document(self, cleanup: dict[str, object]) -> dict[str, object]:
        report: dict[str, object] = {
            "schema_version": LAB_REPORT_SCHEMA,
            "run_id": self.run_id,
            "overall_status": "passed" if not self._failure else "failed",
            "failure_classification": self._failure or None,
            "execution": {
                "classification": "source_level_deterministic_lab",
                "real_components": [
                    "HostPeerSession",
                    "GuestPeerSession",
                    "SessionPeerServer",
                    "SessionPeerClient",
                    "loopback_http_transfer",
                ],
                "synthetic_boundary": "local_capture_wav_generation",
                "jamulus": "not_exercised",
            },
            "evidence": self._evidence,
            "ux_step_measures": self._steps,
            "cleanup_summary": {
                "all_lab_resources_released": bool(
                    cleanup.get("primary_peer_port_released")
                    and cleanup.get("relaunch_peer_port_released")
                    and not cleanup.get("new_webjam_runtime_threads")
                ),
                "relaunch_verified": bool(
                    cleanup.get("relaunch_started_with_rotated_session")
                ),
                "stale_invite_rejected": bool(
                    cleanup.get("stale_previous_invite_rejected")
                ),
            },
            "source_level_ux": {
                "classification": "source_level_workflow_measure",
                "first_host": {
                    "step_count": 3,
                    "repeated_setup_question_count": 0,
                },
                "first_join": {
                    "step_count": 3,
                    "repeated_setup_question_count": 0,
                },
                "returning_host": {
                    "step_count": 1,
                    "repeated_setup_question_count": 0,
                },
                "returning_join": {
                    "step_count": 1,
                    "repeated_setup_question_count": 0,
                },
                "invite_ready_ms": self._invite_ready_ms,
                "guest_connect_ms": self._guest_connect_ms,
                "returning_host_connect_ms": self._returning_host_ms,
                "returning_join_connect_ms": self._returning_join_ms,
                "limitation": "These are source-flow counts and timings, not a rendered UI usability study.",
            },
            "limitations": [
                "No real Jamulus executable, JACK graph, CoreAudio device, or human audibility check ran.",
                "Loopback HTTP proves this source-level control and transfer path, not physical LAN traversal or Internet behavior.",
                "The control outage is a real local connection refusal; it is not evidence of Jamulus transport/media recovery.",
                "The reported source gap and zero-origin alignment are deterministic synthetic-capture fixtures.",
                "The v2 invitation URL contains a bearer in memory; this lab verifies redaction of its own artifacts, not argv/FileOpen ingress.",
                "PCM24 export verifies files and declared package facts, not a physical import into an external editor.",
            ],
        }
        self._assert_report_is_sanitized(report)
        return report

    def _assert_report_is_sanitized(self, payload: dict[str, object]) -> None:
        rendered = json.dumps(payload, sort_keys=True)
        forbidden = {item for item in self._forbidden if item}
        if any(item in rendered for item in forbidden):
            raise AssertionError("The durable lab report contains a private runtime value.")
        for key in ("invite_token", "participant_token", "peer_port", "session_id"):
            if f'"{key}"' in rendered:
                raise AssertionError("The durable lab report contains an unsafe field.")

    def _write_json(self, path: Path, payload: dict[str, object]) -> None:
        atomic_write_text(
            path,
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            mode=0o600,
        )

    def _require_host(self) -> HostPeerSession:
        if self._host is None:
            raise AssertionError("The host runtime is unavailable.")
        return self._host

    def _require_guest(self) -> GuestPeerSession:
        if self._guest is None:
            raise AssertionError("The guest runtime is unavailable.")
        return self._guest

    def _require_invite(self) -> BandInvite:
        if self._invite is None:
            raise AssertionError("The private invite is unavailable.")
        return self._invite

    def _require_clean_take_dir(self) -> Path:
        if self._clean_take_dir is None:
            raise AssertionError("The clean take is unavailable.")
        return self._clean_take_dir


__all__ = [
    "CLEANUP_SCHEMA",
    "LAB_REPORT_SCHEMA",
    "DualMusicianLabResult",
    "DualMusicianRehearsalLab",
]
