"""Deterministic source-level proof for WebJam's exact multitrack contract.

The lab uses the real private peer protocol, immutable recording plan, ARM/ACK
handshake, manifest writer, guest transfer reconciliation, Studio comping, and
Studio exporter. Only the audio-device and Jamulus-process boundaries are
synthetic. Every synthetic source carries unique PCM plus a shared transient
code so swaps, duplication, truncation, topology drift, and stereo collapse
remain observable without claiming hardware or audibility evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import socket
from types import SimpleNamespace

import numpy as np
import soundfile as sf

from core.file_io import atomic_write_text
from core.local_capture import (
    LocalCaptureResult,
    LocalCaptureTrack,
    local_capture_track_map_fingerprint,
)
from core.network_invite import BandInvite
from core.recording_readiness import RecordingStorageCheck, RecordingStorageStatus
from core.session_recording_plan import (
    GuestLocalOriginalBinding,
    InputMapBinding,
    SessionRecordingPlan,
    SharedTrackBinding,
)
from core.session_transfer import RecordingSignal, SharedTrackPlaybackState
from core.session_transfer_runtime import GuestPeerSession, HostPeerSession
from core.studio_comping import automatic_take_lane_matches, stack_automatic_take_lanes
from core.studio_export import export_studio_arrangement, studio_export_supported
from core.studio_project import default_studio_document
from core.studio_source_catalog import StudioSourceCatalog
from core.studio_store import load_studio_document, save_studio_document
from core.take_library import (
    RecorderClientReceipt,
    parse_jamulus_recording_filename,
    validate_take,
    write_take_manifest,
)
from core.take_project import (
    CaptureDevice,
    ProjectStatus,
    SessionEvidence,
    SourceQuality,
    SourceType,
    TakeProject,
    load_take_project,
    new_project_id,
)


RATE = 48_000
FRAMES = 153_600
SUBTYPE = "PCM_24"
EXPECTED_SOURCE_COUNT = 7
MAX_ARTIFACT_BYTES = 64 * 1024 * 1024
REPORT_SCHEMA = "webjam.multitrack-proof-lab.v1"
_CLICK_FRAMES = (9_600, 38_400, 72_000, 105_600, 139_200)
_THREAD_NAMES = {
    "webjam-host-transfer-maintenance",
    "webjam-session-peer",
    "webjam-guest-recording-transfer",
}


class PcmProofError(AssertionError):
    """Raised when one deterministic source no longer matches its contract."""


@dataclass(frozen=True)
class PcmExpectation:
    label: str
    frames: int
    channels: int
    digest: str


@dataclass(frozen=True)
class MultitrackProofResult:
    artifact_root: Path
    primary_take_root: Path
    repeated_take_root: Path
    export_root: Path
    report_path: Path
    primary: TakeProject
    repeated: TakeProject
    report: dict[str, object]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _pcm_signature(path: Path) -> tuple[int, int, int, str]:
    audio, rate = sf.read(path, dtype="int32", always_2d=True)
    info = sf.info(path)
    payload = np.asarray(audio, dtype="<i4").tobytes(order="C")
    digest = hashlib.sha256(
        f"{rate}:{info.frames}:{info.channels}:".encode("ascii") + payload
    ).hexdigest()
    return int(rate), int(info.frames), int(info.channels), digest


def assert_pcm(path: Path, expected: PcmExpectation) -> None:
    """Require exact format, content identity, signal, and stereo separation."""

    info = sf.info(path)
    rate, frames, channels, digest = _pcm_signature(path)
    if (
        rate != RATE
        or frames != expected.frames
        or channels != expected.channels
        or str(info.subtype) != SUBTYPE
    ):
        raise PcmProofError(f"{expected.label} PCM topology changed.")
    audio = sf.read(path, dtype="float32", always_2d=True)[0]
    if audio.size == 0 or float(np.max(np.abs(audio))) < 0.05:
        raise PcmProofError(f"{expected.label} became silent.")
    if channels == 2 and np.array_equal(audio[:, 0], audio[:, 1]):
        raise PcmProofError(f"{expected.label} stereo collapsed.")
    if digest != expected.digest:
        raise PcmProofError(f"{expected.label} source identity changed.")


def _source_number(label: str) -> int:
    return int.from_bytes(hashlib.sha256(label.encode("utf-8")).digest()[:2], "big")


def signal_for(
    label: str,
    *,
    take_index: int,
    channels: int,
    delay_frames: int = 0,
) -> np.ndarray:
    """Return one bounded unique carrier plus a common coded click pattern."""

    timeline = np.arange(FRAMES, dtype=np.float64) / RATE
    number = _source_number(label)
    columns: list[np.ndarray] = []
    for channel in range(channels):
        frequency = 137.0 + (number % 29) * 11.0 + channel * 47.0
        phase = (take_index * 0.31) + channel * 0.67 + (number % 7) * 0.09
        amplitude = 0.025 + 0.004 * ((number + channel) % 5)
        samples = amplitude * np.sin(2.0 * np.pi * frequency * timeline + phase)
        for ordinal, click_frame in enumerate(_CLICK_FRAMES):
            start = click_frame + delay_frames
            if start < 0 or start + 96 >= FRAMES:
                continue
            width = 24 + ordinal * 8
            strength = 0.62 - ordinal * 0.045 + channel * 0.025
            samples[start : start + width] += strength
            samples[start + width : start + 2 * width] -= strength * 0.55
        columns.append(samples.astype(np.float32))
    return np.column_stack(columns)


def write_signal(
    path: Path,
    label: str,
    *,
    take_index: int,
    channels: int,
    samples: np.ndarray | None = None,
) -> PcmExpectation:
    path.parent.mkdir(parents=True, exist_ok=True)
    source = signal_for(label, take_index=take_index, channels=channels)
    if samples is not None:
        source = np.asarray(samples, dtype=np.float32)
        if source.ndim == 1:
            source = source[:, None]
    sf.write(path, source, RATE, subtype=SUBTYPE)
    os.chmod(path, 0o600)
    _rate, frames, observed_channels, digest = _pcm_signature(path)
    return PcmExpectation(label, frames, observed_channels, digest)


class _GuestCapture:
    def __init__(
        self,
        root: Path,
        *,
        take_index: int,
        take_id: str,
        tracks: tuple[LocalCaptureTrack, ...],
        expectations: dict[str, PcmExpectation],
    ) -> None:
        self.root = Path(root)
        self.take_index = take_index
        self.take_id = take_id
        self.tracks = tuple(tracks)
        self.expectations = expectations
        self.started = False
        self.stop_calls = 0
        self.abort_calls = 0

    def start(self) -> None:
        self.started = True

    def abort(self) -> None:
        self.abort_calls += 1

    def stop_into(self, destination: Path) -> LocalCaptureResult:
        if not self.started:
            raise AssertionError("Guest capture was finalized before start.")
        self.stop_calls += 1
        if len(self.tracks) != 1 or self.tracks[0].channel_count != 2:
            raise AssertionError("Guest proof capture lost its true-stereo map.")
        track = self.tracks[0]
        output = Path(destination) / f"{track.stem}.wav"
        expectation = write_signal(
            output,
            "guest-original",
            take_index=self.take_index,
            channels=2,
        )
        self.expectations[track.logical_source_id] = expectation
        return LocalCaptureResult(
            files=(output,),
            started_utc=f"2026-08-17T02:0{self.take_index}:00Z",
            started_monotonic=0.0,
            duration_s=FRAMES / RATE,
            errors=(),
            gaps=(),
            total_frames=FRAMES,
            capture_device=SimpleNamespace(device_id="test-boundary:guest-stereo"),
            durable_frames=FRAMES,
            tracks=self.tracks,
        )


class _GuestCaptureFactory:
    def __init__(self) -> None:
        self.instances: list[_GuestCapture] = []
        self.expectations_by_take: dict[str, dict[str, PcmExpectation]] = {}

    def __call__(self, root: Path, **kwargs) -> _GuestCapture:
        take_index = len(self.instances) + 1
        take_id = str(kwargs["take_id"])
        expectations: dict[str, PcmExpectation] = {}
        capture = _GuestCapture(
            root,
            take_index=take_index,
            take_id=take_id,
            tracks=tuple(kwargs.get("tracks", ())),
            expectations=expectations,
        )
        self.instances.append(capture)
        self.expectations_by_take[take_id] = expectations
        return capture


def _port_released(port: int) -> bool:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.settimeout(0.25)
        return probe.connect_ex(("127.0.0.1", int(port))) != 0
    finally:
        probe.close()


class MultitrackProofLab:
    """Run two exact takes and carry their durable truth through Studio export."""

    def __init__(self, root: str | Path) -> None:
        self.artifact_root = Path(root).resolve() / "multitrack-proof"
        self.report_path = self.artifact_root / "proof-report.json"
        self._host = HostPeerSession()
        self._guest: GuestPeerSession | None = None
        self._factory = _GuestCaptureFactory()
        self._thread_ids_before = {
            thread.ident
            for thread in __import__("threading").enumerate()
            if thread.ident is not None
        }
        self._host_tracks = (
            InputMapBinding("Host Mic", 1, True, True),
            InputMapBinding("Host DI", 1, True, True),
            InputMapBinding("Room Bus", 2, True, True),
        )
        self._roster_digest = hashlib.sha256(b"proof-roster-v1").hexdigest()
        self._roster_fingerprint = hashlib.sha256(
            b"proof-roster-process-bound-v1"
        ).hexdigest()
        self._shared_source = self.artifact_root / "shared-track-source.wav"
        self._shared_fingerprint = ""
        self._reference_participant_id = new_project_id()

    def run(self) -> MultitrackProofResult:
        self.artifact_root.mkdir(parents=True, exist_ok=False, mode=0o700)
        port = 0
        cleanup_ok = False
        try:
            self._host.start(
                "127.0.0.1",
                takes_root=self.artifact_root / "host",
                installation_path=self.artifact_root / "host-installation.json",
                display_name="Host",
                creator_profile_key="music",
            )
            port = self._host.peer_port
            self._enroll_guest()
            shared_expectation = write_signal(
                self._shared_source,
                "shared-track",
                take_index=1,
                channels=2,
            )
            self._shared_fingerprint = _sha256(self._shared_source)

            first, first_expectations = self._record_take(1)
            second, second_expectations = self._record_take(2)
            self._assert_project(first, first_expectations)
            self._assert_project(second, second_expectations)
            self._assert_repeat_identity(first, second)
            export_root, lane_count = self._studio_and_export(first, second)

            secrets = {
                self._host.credentials.invite_token if self._host.credentials else "",
                self._guest.enrollment.participant_token
                if self._guest and self._guest.enrollment
                else "",
            }
            self._assert_no_bearer_in_artifacts(secrets)
            artifact_bytes = sum(
                path.stat().st_size
                for path in self.artifact_root.rglob("*")
                if path.is_file()
            )
            if artifact_bytes >= MAX_ARTIFACT_BYTES:
                raise AssertionError("The deterministic proof exceeded its size cap.")
            report: dict[str, object] = {
                "schema_version": REPORT_SCHEMA,
                "classification": "automated_source_evidence",
                "overall_status": "passed",
                "source_count_per_take": EXPECTED_SOURCE_COUNT,
                "take_count": 2,
                "automatic_lane_count": lane_count,
                "pcm24_48khz_verified": True,
                "unique_source_signatures_verified": True,
                "guest_arm_ack_before_recording": True,
                "guest_transfer_reconciled": True,
                "studio_export_verified": True,
                "artifact_bytes": artifact_bytes,
                "shared_source_sha256": shared_expectation.digest,
                "physical_status": "not_run",
                "limitations": [
                    "synthetic_audio_device_boundary",
                    "jamulus_process_not_run",
                    "physical_audibility_latency_accessibility_not_run",
                    "package_trust_not_run",
                ],
            }
            serialized = json.dumps(report, sort_keys=True, separators=(",", ":"))
            atomic_write_text(self.report_path, serialized + "\n", mode=0o600)
        finally:
            if self._guest is not None:
                self._guest.stop()
            self._host.stop()
            cleanup_ok = bool(port and _port_released(port))

        leaked = tuple(
            thread.name
            for thread in __import__("threading").enumerate()
            if thread.ident not in self._thread_ids_before
            and thread.name in _THREAD_NAMES
        )
        if not cleanup_ok or leaked:
            raise AssertionError("The deterministic proof leaked a runtime owner.")
        return MultitrackProofResult(
            artifact_root=self.artifact_root,
            primary_take_root=self.artifact_root / "takes" / "take-1",
            repeated_take_root=self.artifact_root / "takes" / "take-2",
            export_root=export_root,
            report_path=self.report_path,
            primary=first,
            repeated=second,
            report=report,
        )

    def _enroll_guest(self) -> None:
        credentials = self._host.credentials
        if credentials is None:
            raise AssertionError("Host credentials are unavailable.")
        invite = BandInvite(
            "127.0.0.1",
            22_124,
            "Multitrack Proof",
            credentials.session_id,
            self._host.peer_port,
            credentials.invite_token,
        )
        guest = GuestPeerSession(
            invite,
            display_name="Guest",
            takes_root=self.artifact_root / "guest",
            installation_path=self.artifact_root / "guest-installation.json",
            capture_enabled=lambda: True,
            capture_config=lambda: (0, RATE, 128),
            capture_tracks=lambda: (LocalCaptureTrack("local-Guest Stereo", (0, 1)),),
            capture_factory=self._factory,
        )
        state = guest.poll_once()
        if state.signal is not RecordingSignal.IDLE or not guest.participant_id:
            raise AssertionError("Guest enrollment did not reach exact idle truth.")
        self._guest = guest
        self._refresh_presence(force_rotate=False)

    def _refresh_presence(self, *, force_rotate: bool) -> None:
        guest = self._require_guest()
        host_enrollment = self._host.host_enrollment
        if host_enrollment is None:
            raise AssertionError("Host enrollment is unavailable.")
        challenge = self._host.install_recording_presence_roster(
            self._roster_digest,
            2,
            self_ordinal=0,
            host_roster_fingerprint=self._roster_fingerprint,
            ambiguous_ordinals=(),
            process_generation=1,
            rpc_connection_generation=2,
            audio_connection_generation=3,
            force_rotate=force_rotate,
        )
        if challenge is None:
            raise AssertionError("Exact roster challenge was not created.")
        provisional_plan = self._plan_skeleton(new_project_id(), 1, ())
        host_tracks = provisional_plan.resolved_capture_tracks()
        host_proof = self._host.bind_host_recording_presence(
            "Host",
            ordered_roster_digest=self._roster_digest,
            roster_count=2,
            self_ordinal=0,
            host_roster_fingerprint=self._roster_fingerprint,
            ambiguous_ordinals=(),
            process_generation=1,
            rpc_connection_generation=2,
            audio_connection_generation=3,
            challenge=challenge.challenge,
            challenge_epoch=challenge.challenge_epoch,
            topology_epoch=challenge.topology_epoch,
            presence_generation=1,
            capture_enabled=True,
            local_original_track_count=len(host_tracks),
            local_original_map_fingerprint=local_capture_track_map_fingerprint(
                host_tracks
            ),
            local_original_channel_counts=tuple(
                track.channel_count for track in host_tracks
            ),
            local_original_source_ids=tuple(
                track.logical_source_id for track in host_tracks
            ),
        )
        if host_proof is None:
            raise AssertionError("Host exact presence was not bound.")
        guest.observe_presence_v2(
            "Guest",
            ordered_roster_digest=self._roster_digest,
            roster_count=2,
            self_ordinal=1,
            process_generation=1,
            rpc_connection_generation=2,
            audio_connection_generation=3,
        )
        guest.poll_once()

    def _plan_skeleton(
        self,
        take_id: str,
        take_index: int,
        guest_bindings: tuple[GuestLocalOriginalBinding, ...],
    ) -> SessionRecordingPlan:
        host_id = self._host.host_enrollment.participant_id
        guest_id = self._require_guest().participant_id
        return SessionRecordingPlan(
            session_id=self._host.session_id,
            take_id=take_id,
            plan_generation=take_index,
            roster=((host_id, "Host"), (guest_id, "Guest")),
            expected_server_stems=(
                host_id,
                guest_id,
                self._reference_participant_id,
            ),
            count_in_frames=4_800,
            pre_roll_frames=2_400,
            storage=RecordingStorageCheck(
                RecordingStorageStatus.READY,
                "Automated proof storage is ready.",
                free_bytes=10 * 1024**3,
                required_bytes=16 * 1024**2,
            ),
            expected_source_count=(
                3
                + len(self._host_tracks)
                + sum(item.track_count for item in guest_bindings)
            ),
            created_at_utc=f"2026-08-17T02:0{take_index}:00Z",
            shared_track=SharedTrackBinding(self._shared_fingerprint or "ab" * 32, 11),
            shared_track_planned=True,
            input_maps=self._host_tracks,
            creator_profile_key="music",
            guest_local_originals=guest_bindings,
            server_channel_counts=(1, 2, 2),
        )

    def _record_take(
        self,
        take_index: int,
    ) -> tuple[TakeProject, dict[str, PcmExpectation]]:
        if take_index > 1:
            self._refresh_presence(force_rotate=True)
        guest = self._require_guest()
        take_id = new_project_id()
        obligations, issues = self._host.prepare_local_original_obligations(take_id)
        if issues or len(obligations) != 1 or not obligations[0].exact_topology:
            raise AssertionError(f"Guest obligation was not exact: {issues!r}")
        obligation = obligations[0]
        binding = GuestLocalOriginalBinding(
            participant_id=obligation.participant_id,
            track_count=obligation.track_count,
            map_fingerprint_sha256=obligation.map_fingerprint,
            presence_generation=obligation.presence_generation,
            channel_counts=obligation.channel_counts,
            logical_source_ids=obligation.logical_source_ids,
        )
        plan = self._plan_skeleton(take_id, take_index, (binding,))
        if (
            not plan.server_topology_exact
            or plan.expected_source_count != EXPECTED_SOURCE_COUNT
        ):
            raise AssertionError("The proof plan did not bind seven exact sources.")
        restored = SessionRecordingPlan.from_private_dict(plan.to_private_dict())
        if restored != plan or restored.plan_fingerprint() != plan.plan_fingerprint():
            raise AssertionError("The immutable plan did not round-trip exactly.")
        arm = self._host.publish_capture_arm(
            take_id,
            recording_plan_fingerprint=plan.plan_fingerprint(),
        )
        if (
            self._host.publish_capture_arm(
                take_id,
                recording_plan_fingerprint=plan.plan_fingerprint(),
            )
            != arm
        ):
            raise AssertionError("Duplicate ARM changed the take contract.")
        armed_state = guest.poll_once()
        capture = self._factory.instances[-1]
        if (
            not capture.started
            or guest.active_take_id != take_id
            or self._host.capture_arm_pending_participant_ids(
                take_id, arm_generation=arm.arm_generation
            )
        ):
            raise AssertionError("Guest capture did not start and ACK exact ARM.")
        if not self._host.wait_for_capture_arm_acknowledgements(
            take_id,
            arm_generation=arm.arm_generation,
            timeout_s=0.0,
        ):
            raise AssertionError("Host did not observe all exact capture ACKs.")
        if armed_state.capture_arm != arm:
            raise AssertionError("Guest observed a different capture ARM.")

        self._host.publish_shared_track_state(
            state=SharedTrackPlaybackState.READY,
            loaded=True,
            source_display_name="Checksum-bound proof track",
            duration_s=FRAMES / RATE,
            count_in_active=False,
            playback_generation=11,
        )
        self._host.publish_shared_track_state(
            state=SharedTrackPlaybackState.ROUTING,
            loaded=True,
            source_display_name="Checksum-bound proof track",
            duration_s=FRAMES / RATE,
            count_in_active=True,
            playback_generation=11,
        )
        self._host.publish_shared_track_state(
            state=SharedTrackPlaybackState.PLAYING,
            loaded=True,
            source_display_name="Checksum-bound proof track",
            duration_s=FRAMES / RATE,
            count_in_active=False,
            playback_generation=11,
        )
        started_utc = f"2026-08-17T02:0{take_index}:00Z"
        first_start = self._host.begin_take(take_id, started_utc=started_utc)
        second_start = self._host.begin_take(take_id, started_utc=started_utc)
        if first_start != second_start or first_start is None:
            raise AssertionError("Record start was not idempotent.")
        guest.poll_once()
        stopped_utc = f"2026-08-17T02:0{take_index}:01Z"
        first_stop = self._host.begin_take_finalization(
            take_id,
            stopped_utc=stopped_utc,
        )
        second_stop = self._host.begin_take_finalization(
            take_id,
            stopped_utc=stopped_utc,
        )
        if first_stop != second_stop or first_stop is None:
            raise AssertionError("Finalizing was not idempotent.")
        guest.poll_once()
        if capture.stop_calls != 1 or guest.active_take_id:
            raise AssertionError("Guest capture did not finalize exactly once.")
        if not self._host.wait_for_initial_take_inventory(take_id, timeout_s=1.0):
            raise AssertionError("Exact guest inventory did not settle.")

        take_root = self.artifact_root / "takes" / f"take-{take_index}"
        take_root.mkdir(parents=True, exist_ok=False, mode=0o700)
        expectations = dict(self._factory.expectations_by_take[take_id])
        receipts = self._write_server_and_host_media(
            take_root,
            plan,
            take_index,
            expectations,
        )
        host_tracks = plan.resolved_capture_tracks()
        capture_device = CaptureDevice(
            device_id="test-boundary:host-four-channel",
            display_name="Deterministic test boundary",
            backend="Synthetic source fixture",
            sample_rate=RATE,
            channel_indices=(0, 1, 2, 3),
            channel_labels=("Host Mic", "Host DI", "Room L", "Room R"),
        )
        validation = write_take_manifest(
            take_root,
            expected_tracks=3,
            required_local_stems=3,
            local_started_utc=started_utc,
            local_duration_s=FRAMES / RATE,
            session_title="Deterministic multitrack proof",
            app_version="proof-lab",
            session_id=plan.session_id,
            take_id=plan.take_id,
            local_participant_id=self._host.host_enrollment.participant_id,
            local_participant_name="Host",
            capture_device=capture_device,
            local_capture_tracks=host_tracks,
            local_total_frames=FRAMES,
            local_durable_frames=FRAMES,
            session_evidence=SessionEvidence(
                protocol_version="proof-lab-v1",
                started_utc=started_utc,
                ended_utc=stopped_utc,
                recording_plan_fingerprint=plan.plan_fingerprint(),
                creator_profile_key="music",
            ),
            recording_receipts=receipts,
            required_reference_track=True,
            recording_plan=plan,
        )
        if not validation.ok:
            raise AssertionError(f"Take validation failed: {validation.errors!r}")
        self._host.register_take(take_id, take_root)
        for _attempt in range(4):
            self._host.reconcile_take(take_id, take_root)
            project = load_take_project(take_root)
            if len(project.tracks) == EXPECTED_SOURCE_COUNT:
                break
        else:
            raise AssertionError("Guest media did not reconcile into the take.")
        if self._host.reconcile_take(take_id, take_root):
            raise AssertionError("Idempotent reconcile unexpectedly changed the take.")
        completed = self._host.finish_take(take_id, stopped_utc=stopped_utc)
        duplicate_completed = self._host.finish_take(take_id, stopped_utc=stopped_utc)
        if completed != duplicate_completed or completed is None:
            raise AssertionError("Completion was not idempotent.")
        return load_take_project(take_root), expectations

    def _write_server_and_host_media(
        self,
        take_root: Path,
        plan: SessionRecordingPlan,
        take_index: int,
        expectations: dict[str, PcmExpectation],
    ) -> tuple[RecorderClientReceipt, ...]:
        host_tracks = plan.resolved_capture_tracks()
        host_arrays: list[np.ndarray] = []
        for ordinal, track in enumerate(host_tracks):
            label = ("host-mic", "host-di", "room-bus")[ordinal]
            samples = signal_for(
                label,
                take_index=take_index,
                channels=track.channel_count,
            )
            expectation = write_signal(
                take_root / f"{track.stem}.wav",
                label,
                take_index=take_index,
                channels=track.channel_count,
                samples=samples,
            )
            expectations[track.logical_source_id] = expectation
            host_arrays.append(samples.mean(axis=1))

        # The manifest writer aligns host originals from the first two local
        # stems. Preserve that exact production contract at the synthetic
        # boundary so confidence measures timing, not fixture mismatch.
        host_server = (0.5 * (host_arrays[0] + host_arrays[1]))[:, None]
        guest_server = signal_for(
            "guest-original",
            take_index=take_index,
            channels=2,
            delay_frames=3_840,
        )
        shared_audio = sf.read(self._shared_source, dtype="float32", always_2d=True)[0]
        sources = (
            (
                "Host-127_0_0_1_41001",
                "Host",
                plan.expected_server_stems[0],
                1,
                "musician",
                host_server,
            ),
            (
                "Guest-127_0_0_1_41002",
                "Guest",
                plan.expected_server_stems[1],
                2,
                "musician",
                guest_server,
            ),
            (
                "WebJamTrack-127_0_0_1_41003",
                "WebJam Track",
                plan.expected_server_stems[2],
                2,
                "reference_track",
                shared_audio,
            ),
        )
        receipts: list[RecorderClientReceipt] = []
        lof_lines: list[str] = []
        for channel_id, (
            key,
            name,
            participant_id,
            channels,
            kind,
            samples,
        ) in enumerate(sources):
            filename = f"{key}-0-{channels}.wav"
            expectation = write_signal(
                take_root / filename,
                f"server-{kind}-{channel_id}",
                take_index=take_index,
                channels=channels,
                samples=samples,
            )
            parsed = parse_jamulus_recording_filename(filename)
            if parsed is None:
                raise AssertionError("Synthetic Jamulus filename did not parse.")
            lof_lines.append(f'file "{filename}" offset 0')
            expectations[plan.server_logical_source_ids[channel_id]] = expectation
            receipts.append(
                RecorderClientReceipt(
                    server_channel_id=channel_id,
                    display_name=name,
                    participant_id=participant_id,
                    recorder_key_sha256=parsed.recorder_key_sha256,
                    channels=channels,
                    source_kind=kind,
                    source_fingerprint_sha256=(
                        self._shared_fingerprint if kind == "reference_track" else ""
                    ),
                    playback_generation=11 if kind == "reference_track" else 0,
                )
            )
        atomic_write_text(
            take_root / "take.lof",
            "\n".join(lof_lines) + "\n",
            mode=0o600,
        )
        return tuple(receipts)

    def _assert_project(
        self,
        project: TakeProject,
        expectations: dict[str, PcmExpectation],
    ) -> None:
        if project.status is not ProjectStatus.COMPLETE or project.errors:
            raise AssertionError(f"Take did not become complete: {project.errors!r}")
        if len(project.tracks) != EXPECTED_SOURCE_COUNT:
            raise AssertionError("Take did not contain exactly seven sources.")
        logical_ids = tuple(track.logical_source_id for track in project.tracks)
        if not all(logical_ids) or len(set(logical_ids)) != EXPECTED_SOURCE_COUNT:
            raise AssertionError("Take logical source identities were not exact.")
        if set(logical_ids) != set(expectations):
            raise AssertionError("Take sources did not match the immutable plan.")
        signatures: set[str] = set()
        for track in project.tracks:
            if len(track.segments) != 1:
                raise AssertionError("Proof sources must each contain one segment.")
            path = self._take_root(project) / track.primary_segment.path
            expectation = expectations[track.logical_source_id]
            assert_pcm(path, expectation)
            signatures.add(expectation.digest)
        if len(signatures) != EXPECTED_SOURCE_COUNT:
            raise AssertionError("Two planned sources contained duplicate PCM.")
        guest_track = next(
            track
            for track in project.tracks
            if track.participant_id == self._require_guest().participant_id
            and track.source_type is SourceType.LOCAL_ISOLATED
        )
        if (
            guest_track.quality is not SourceQuality.VERIFIED_ISOLATED
            or not guest_track.alignment.method.startswith(
                "peer-local-original-verified-alignment/"
            )
            or guest_track.alignment.confidence < 0.85
            or guest_track.alignment.residual_ms > 2.0
            or len(guest_track.alignment.anchors) < 3
        ):
            raise AssertionError("Guest timing evidence was not strongly verified.")

    def _take_root(self, project: TakeProject) -> Path:
        for root in (
            self.artifact_root / "takes" / "take-1",
            self.artifact_root / "takes" / "take-2",
        ):
            if (root / "webjam-take.json").is_file():
                try:
                    if load_take_project(root).take_id == project.take_id:
                        return root
                except Exception:
                    continue
        raise AssertionError("Take root could not be resolved.")

    @staticmethod
    def _assert_repeat_identity(first: TakeProject, second: TakeProject) -> None:
        first_by_id = {track.logical_source_id: track for track in first.tracks}
        second_by_id = {track.logical_source_id: track for track in second.tracks}
        if set(first_by_id) != set(second_by_id):
            raise AssertionError("Repeated take logical identities drifted.")
        changed = sum(
            first_by_id[source_id].primary_segment.sha256
            != second_by_id[source_id].primary_segment.sha256
            for source_id in first_by_id
        )
        if changed < EXPECTED_SOURCE_COUNT - 1:
            raise AssertionError("Repeated take PCM did not prove new performances.")

    def _studio_and_export(
        self,
        primary: TakeProject,
        repeated: TakeProject,
    ) -> tuple[Path, int]:
        if not studio_export_supported():
            raise AssertionError("Secure Studio export is unavailable on this host.")
        document = default_studio_document(primary)
        matches = automatic_take_lane_matches(document, primary, repeated)
        if len(matches) != EXPECTED_SOURCE_COUNT:
            raise AssertionError("Automatic repeat-take matching was incomplete.")
        stacked = stack_automatic_take_lanes(document, primary, repeated)
        if stack_automatic_take_lanes(stacked, primary, repeated) != stacked:
            raise AssertionError("Automatic lane stacking was not idempotent.")
        primary_root = self._take_root(primary)
        repeated_root = self._take_root(repeated)
        loaded = load_studio_document(primary_root)
        saved = save_studio_document(
            primary_root,
            stacked,
            expected_token=loaded.token,
        )
        reopened = load_studio_document(primary_root)
        if reopened.token != saved.token or reopened.document != saved.document:
            raise AssertionError("Studio lane state did not survive reopen.")
        catalog = StudioSourceCatalog.load(
            primary,
            primary_root,
            additional_take_roots=(repeated_root,),
        )
        manifest_hashes = {
            root: _sha256(root / "webjam-take.json")
            for root in (primary_root, repeated_root)
        }
        source_hashes = {
            root / segment.path: _sha256(root / segment.path)
            for root, project in ((primary_root, primary), (repeated_root, repeated))
            for track in project.tracks
            for segment in track.segments
        }
        result = export_studio_arrangement(
            primary,
            reopened.document,
            primary_root,
            destination_root=self.artifact_root / "exports",
            source_catalog=catalog,
            disk_reserve_bytes=0,
            block_frames=4_096,
        )
        if (
            len(result.edited_stems) != EXPECTED_SOURCE_COUNT
            or len(result.original_stems) != EXPECTED_SOURCE_COUNT * 2
            or len(result.source_manifests) != 2
        ):
            raise AssertionError("Studio export did not preserve both exact takes.")
        for path in (*result.edited_stems, *result.original_stems, result.rough_mix):
            info = sf.info(path)
            if info.samplerate != RATE or info.subtype != SUBTYPE:
                raise AssertionError("Studio export changed the PCM contract.")
        for line in result.checksums.read_text(encoding="utf-8").splitlines():
            digest, relative = line.split("  ", 1)
            if _sha256(result.folder / relative) != digest:
                raise AssertionError("Studio export checksum inventory changed.")
        if any(
            _sha256(root / "webjam-take.json") != digest
            for root, digest in manifest_hashes.items()
        ) or any(_sha256(path) != digest for path, digest in source_hashes.items()):
            raise AssertionError("Studio/export mutated immutable take evidence.")
        return result.folder, len(matches)

    def _assert_no_bearer_in_artifacts(self, secrets: set[str]) -> None:
        forbidden = tuple(item.encode("utf-8") for item in secrets if item)
        for path in self.artifact_root.rglob("*"):
            if path.is_file() and any(item in path.read_bytes() for item in forbidden):
                raise AssertionError(
                    "A private session bearer entered proof artifacts."
                )

    def _require_guest(self) -> GuestPeerSession:
        if self._guest is None:
            raise AssertionError("Guest runtime is unavailable.")
        return self._guest


def validate_completed_take(root: Path, *, expected_tracks: int) -> None:
    """Expose one bounded validation assertion for the public test module."""

    result = validate_take(
        root, expected_tracks=expected_tracks, required_local_stems=3
    )
    if not result.ok:
        raise AssertionError(result.errors)
