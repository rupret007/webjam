"""Real recorder -> project -> Studio core -> track-export certification."""
from __future__ import annotations

import hashlib
import json
import os
import sys
import uuid
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from typing import Callable
from unittest.mock import patch

import numpy as np
import pytest

from core.take_export import export_track_package
from core.local_capture import LocalInputCapture
from core.session_transfer import (
    EnrollmentRegistry,
    SessionControlState,
    SessionCredentials,
    SessionPeerClient,
    SessionPeerServer,
    TransferDescriptor,
    TransferStore,
)
from core.take_library import (
    RecorderClientReceipt,
    discover_takes,
    parse_jamulus_recording_filename,
    recorder_client_observations,
    write_take_manifest,
)
from core.take_player import TakePlayer
from core.take_project import (
    MediaStatus,
    ProjectStatus,
    SourceQuality,
    SourceType,
    load_take_project,
)
from tests.support.jamulus_jack_harness import (
    SAMPLE_RATE,
    SPEC_A,
    SPEC_B,
    JamulusJackHarness,
    analyze_recorded_stem,
    assert_recorded_stem_metrics,
    make_fixture,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


class _PullSink:
    """Headless output boundary for the same TakePlayer Studio uses."""

    def __init__(self) -> None:
        self.pull: Callable[[int], np.ndarray] | None = None
        self.stopped = True

    def start(
        self,
        _samplerate: int,
        _blocksize: int,
        pull: Callable[[int], np.ndarray],
    ) -> None:
        self.pull = pull
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


class _DeterministicHardwareInputStream:
    """Fake only the sounddevice hardware callback, never Jamulus routing."""

    def __init__(self, *, callback: Callable, **_kwargs: object) -> None:
        self.callback = callback

    def start(self) -> None:
        fixture_a = make_fixture(SPEC_A, duration_s=6.0)
        # Model two physical inputs owned by the same authenticated musician.
        # The server-side SPEC_B source belongs to the other Jamulus client and
        # must never be selected merely because it correlates with an input.
        inputs = np.column_stack((fixture_a, fixture_a * 0.75))
        for start in range(0, len(inputs), 4096):
            block = inputs[start : start + 4096]
            self.callback(block, len(block), None, "")

    def stop(self) -> None:
        return None

    def abort(self) -> None:
        return None

    def close(self) -> None:
        return None


def _deterministic_sounddevice_boundary() -> SimpleNamespace:
    return SimpleNamespace(
        check_input_settings=lambda **_kwargs: None,
        InputStream=lambda **kwargs: _DeterministicHardwareInputStream(**kwargs),
        query_devices=lambda _device, _kind: {
            "name": "Deterministic Hardware-Boundary Fixture",
            "hostapi": 0,
        },
        query_hostapis=lambda _index: {"name": "Test Boundary (not physical)"},
    )


def _exercise_resumable_http_transfer(
    root: Path,
    source: Path,
    *,
    device_id: str,
) -> dict[str, object]:
    """Upload one real local WAV over HTTP, restarting after a partial PUT."""
    import soundfile as sf  # type: ignore

    credentials = SessionCredentials.create()
    host_root = root / "transfer-host"
    registry = EnrollmentRegistry(host_root, credentials)
    control = SessionControlState(host_root, credentials.session_id)
    transfers = TransferStore(host_root, credentials.session_id)
    first_server = SessionPeerServer(
        "127.0.0.1",
        0,
        registry=registry,
        control=control,
        transfers=transfers,
    )
    first_server.start()
    original = source.read_bytes()
    try:
        first_client = SessionPeerClient(
            "127.0.0.1", first_server.address[1], credentials=credentials
        )
        enrollment = first_client.enroll(str(uuid.uuid4()), "Boundary Musician")
        take_id = str(uuid.uuid4())
        control.begin(take_id, started_utc="2026-07-13T00:00:00Z")
        info = sf.info(source)
        descriptor = TransferDescriptor(
            session_id=credentials.session_id,
            take_id=take_id,
            participant_id=enrollment.participant_id,
            segment_id=str(uuid.uuid4()),
            sha256=_sha256(source),
            size_bytes=len(original),
            sample_rate=int(info.samplerate),
            channels=int(info.channels),
            frame_count=int(info.frames),
            subtype=str(info.subtype),
            started_utc="2026-07-13T00:00:00Z",
            device_id=device_id,
            source_channel=0,
        )
        partial = original[:4096]
        response = first_client._request(
            "PUT",
            "/v1/segment",
            token=enrollment.participant_token,
            participant_id=enrollment.participant_id,
            body=partial,
            headers={
                "Content-Type": "application/octet-stream",
                "X-WebJam-Offset": "0",
                "X-WebJam-Descriptor": json.dumps(
                    asdict(descriptor), separators=(",", ":")
                ),
            },
        )
        assert response["received_bytes"] == len(partial)
        assert response["complete"] is False
        assert first_client.transfer_status(
            enrollment, descriptor
        ).received_bytes == len(partial)
    finally:
        first_server.stop()

    reopened_registry = EnrollmentRegistry(host_root, credentials)
    reopened_control = SessionControlState(host_root, credentials.session_id)
    reopened_transfers = TransferStore(host_root, credentials.session_id)
    second_server = SessionPeerServer(
        "127.0.0.1",
        0,
        registry=reopened_registry,
        control=reopened_control,
        transfers=reopened_transfers,
    )
    second_server.start()
    try:
        second_client = SessionPeerClient(
            "127.0.0.1", second_server.address[1], credentials=credentials
        )
        receipt = second_client.upload_file(
            enrollment, descriptor, source, chunk_bytes=32_768
        )
        assert receipt.complete
        published = reopened_transfers.status(descriptor)
        assert published.complete and published.path is not None
        assert published.path.read_bytes() == original
        assert source.read_bytes() == original
        return {
            "partial_bytes_before_restart": len(partial),
            "final_bytes": published.received_bytes,
            "sha256": _sha256(published.path),
            "device_id_preserved": descriptor.device_id == device_id,
            "source_channel": descriptor.source_channel,
        }
    finally:
        second_server.stop()


@pytest.mark.skipif(
    os.environ.get("WEBJAM_RUN_JACK_AUDIO_INTEGRATION") != "1",
    reason="real JACK/Jamulus recording pipeline is opt-in",
)
@pytest.mark.skipif(sys.platform != "linux", reason="real Jamulus pipeline is Linux-only")
def test_real_server_stems_reach_studio_and_track_export_without_relabeling() -> None:
    import soundfile as sf  # type: ignore

    harness = JamulusJackHarness.from_environment()
    pipeline_evidence: dict[str, object] = {}
    with harness:
        harness.set_recording(True)
        transport = harness.run_transport(duration_s=5.0, tail_s=1.5)
        harness.set_recording(False)

        source_wavs = tuple(
            path
            for path in harness.recording_artifacts()
            if path.suffix.lower() == ".wav"
        )
        assert len(source_wavs) == 2, source_wavs
        take_directories = {path.parent for path in source_wavs}
        assert len(take_directories) == 1
        take_dir = take_directories.pop()
        native_recorder_names = tuple(path.name for path in source_wavs)
        authenticated_source_addresses = tuple(
            str(client.get("address", ""))
            for client in transport.server_clients
        )

        # Bind the authenticated recorder roster to the two exact client
        # processes the harness owns.  Display names and filename prefixes are
        # labels only: neither is allowed to decide which musician owns PCM.
        observations = recorder_client_observations({
            "connections": len(transport.server_clients),
            "clients": list(transport.server_clients),
        })
        participant_ids = {
            harness.CLIENT_A_NAME: str(uuid.uuid4()),
            harness.CLIENT_B_NAME: str(uuid.uuid4()),
        }
        # The Jamulus --port arguments are allocation bases, not bound ports.
        # Bind each authenticated roster address to the exact PID-owned UDP
        # socket and require a bijection across the two private processes.
        actual_owned_udp_ports = harness.exact_owned_client_udp_ports()
        assert len(actual_owned_udp_ports) == 2
        owned_fixtures_by_udp_port = {
            actual_owned_udp_ports[0]: (
                harness.CLIENT_A_NAME,
                participant_ids[harness.CLIENT_A_NAME],
                SPEC_A,
            ),
            actual_owned_udp_ports[1]: (
                harness.CLIENT_B_NAME,
                participant_ids[harness.CLIENT_B_NAME],
                SPEC_B,
            ),
        }
        recording_receipts: list[RecorderClientReceipt] = []
        expected_by_participant_id = {}
        observed_owned_udp_ports: set[int] = set()
        for raw_client, observation in zip(
            transport.server_clients, observations, strict=True
        ):
            address = str(raw_client.get("address", ""))
            _host, separator, port_text = address.rpartition(":")
            assert separator and port_text.isdigit(), raw_client
            owned = owned_fixtures_by_udp_port.get(int(port_text))
            assert owned is not None, raw_client
            assert int(port_text) not in observed_owned_udp_ports, raw_client
            observed_owned_udp_ports.add(int(port_text))
            expected_name, participant_id, expected_spec = owned
            assert observation.display_name == expected_name
            expected_by_participant_id[participant_id] = (
                expected_name,
                expected_spec,
            )
            recording_receipts.append(RecorderClientReceipt(
                server_channel_id=observation.server_channel_id,
                display_name=observation.display_name,
                participant_id=participant_id,
                recorder_key_sha256=observation.recorder_key_sha256,
                channels=observation.channels,
            ))
        assert len(recording_receipts) == 2
        assert observed_owned_udp_ports == set(owned_fixtures_by_udp_port)

        # Jamulus's numeric suffix is startFrame + channel count.  Match each
        # WAV through the address-erased recorder-key digest from the receipt;
        # never reinterpret startFrame as a server channel ID or infer an
        # owner from a musician-looking filename.
        receipts_by_media_key = {
            (receipt.recorder_key_sha256, receipt.channels): receipt
            for receipt in recording_receipts
        }
        source_by_participant_id: dict[str, Path] = {}
        for path in source_wavs:
            parsed = parse_jamulus_recording_filename(path.name)
            assert parsed is not None, path.name
            receipt = receipts_by_media_key.get(
                (parsed.recorder_key_sha256, parsed.channels)
            )
            assert receipt is not None, path.name
            assert receipt.participant_id not in source_by_participant_id
            source_by_participant_id[receipt.participant_id] = path
        assert set(source_by_participant_id) == set(expected_by_participant_id)

        source_metrics = {}
        for participant_id, (name, expected) in expected_by_participant_id.items():
            forbidden = (
                SPEC_B.frequency_hz
                if expected is SPEC_A
                else SPEC_A.frequency_hz
            )
            source_metrics[name] = analyze_recorded_stem(
                source_by_participant_id[participant_id],
                expected=expected,
                forbidden_frequency_hz=forbidden,
            )
            assert_recorded_stem_metrics(
                source_metrics[name],
                expected=expected,
                duration_bounds_s=(6.0, 8.5),
            )

        # This is deliberately a deterministic fake at the sounddevice
        # InputStream boundary only. It exercises WebJam's real callback,
        # bounded queue, writer, PCM finalization, and take attachment; it does
        # not claim physical equivalence to the independent Jamulus/JACK path.
        with patch.dict(
            sys.modules,
            {"sounddevice": _deterministic_sounddevice_boundary()},
        ):
            local_capture = LocalInputCapture(
                harness.root / "local-working", samplerate=SAMPLE_RATE
            )
            local_capture.start()
            local_result = local_capture.stop_into(take_dir)
        assert not local_result.errors
        assert not local_result.gaps
        assert local_result.total_frames == 6 * SAMPLE_RATE
        assert len(local_result.files) == 2
        assert local_result.capture_device.display_name == (
            "Deterministic Hardware-Boundary Fixture"
        )
        assert local_result.capture_device.backend == "Test Boundary (not physical)"
        assert local_result.capture_device.channel_indices == (0, 1)
        assert local_result.capture_device.channel_labels == ("Input 1", "Input 2")

        local_by_name = {path.name: path for path in local_result.files}
        assert set(local_by_name) == {"host-guitar.wav", "host-vocal.wav"}
        local_metrics = {
            "host-guitar.wav": analyze_recorded_stem(
                local_by_name["host-guitar.wav"],
                expected=SPEC_A,
                forbidden_frequency_hz=SPEC_B.frequency_hz,
            ),
            "host-vocal.wav": analyze_recorded_stem(
                local_by_name["host-vocal.wav"],
                expected=SPEC_A,
                forbidden_frequency_hz=SPEC_B.frequency_hz,
            ),
        }
        for filename, expected in (
            ("host-guitar.wav", SPEC_A),
            ("host-vocal.wav", SPEC_A),
        ):
            assert_recorded_stem_metrics(
                local_metrics[filename],
                expected=expected,
                duration_bounds_s=(5.99, 6.01),
                expected_channels=1,
            )

        server_hashes_by_participant_id = {
            participant_id: _sha256(path)
            for participant_id, path in source_by_participant_id.items()
        }
        local_source_hashes = {
            path.name: _sha256(path) for path in local_result.files
        }
        transfer_evidence = _exercise_resumable_http_transfer(
            harness.root,
            local_by_name["host-guitar.wav"],
            device_id=local_result.capture_device.device_id,
        )
        assert transfer_evidence["final_bytes"] == local_by_name[
            "host-guitar.wav"
        ].stat().st_size
        assert transfer_evidence["sha256"] == local_source_hashes["host-guitar.wav"]
        assert transfer_evidence["device_id_preserved"] is True

        validation = write_take_manifest(
            take_dir,
            expected_tracks=2,
            required_local_stems=2,
            local_started_utc=local_result.started_utc,
            local_duration_s=local_result.total_frames / SAMPLE_RATE,
            capture_errors=local_result.errors,
            session_title="Real Jamulus Boundary Certification",
            app_version="integration",
            local_participant_id=participant_ids[harness.CLIENT_A_NAME],
            local_participant_name=harness.CLIENT_A_NAME,
            capture_device=local_result.capture_device,
            capture_gaps=local_result.gaps,
            local_total_frames=local_result.total_frames,
            recording_receipts=tuple(recording_receipts),
        )
        assert validation.ok, validation.errors
        assert validation.take is not None
        assert validation.take.validation_status == "complete"
        assert not validation.take.manifest_errors

        project = load_take_project(take_dir)
        assert project.effective_status is ProjectStatus.COMPLETE
        assert project.project_sample_rate == SAMPLE_RATE
        assert len(project.tracks) == 4
        assert project.devices == (local_result.capture_device,)
        assert {
            participant.display_name for participant in project.participants
        } == {
            harness.CLIENT_A_NAME,
            harness.CLIENT_B_NAME,
        }
        server_tracks = tuple(
            track
            for track in project.tracks
            if track.source_type is SourceType.JAMULUS_SERVER
        )
        local_tracks = tuple(
            track
            for track in project.tracks
            if track.source_type is SourceType.LOCAL_ISOLATED
        )
        assert len(server_tracks) == len(local_tracks) == 2
        for track in server_tracks:
            assert track.source_type is SourceType.JAMULUS_SERVER
            assert track.quality is SourceQuality.NETWORK_TRACK
            assert track.media_status is MediaStatus.AVAILABLE
            assert len(track.segments) == 1
            segment = track.segments[0]
            assert segment.sample_rate == SAMPLE_RATE
            assert segment.channels == 2
            source_path = take_dir / segment.path
            assert source_path.name.startswith("server-media-")
            assert not any(
                name in source_path.name
                for name in (harness.CLIENT_A_NAME, harness.CLIENT_B_NAME)
            )
            assert segment.frame_count == sf.info(source_path).frames
            assert track.participant_id in server_hashes_by_participant_id
            assert (
                segment.sha256
                == server_hashes_by_participant_id[track.participant_id]
            )
        for track in local_tracks:
            assert track.quality is SourceQuality.UNVERIFIED
            assert track.media_status is MediaStatus.AVAILABLE
            assert len(track.segments) == 1
            segment = track.segments[0]
            assert segment.sample_rate == SAMPLE_RATE
            assert segment.channels == 1
            assert segment.device_id == local_result.capture_device.device_id
            source_path = take_dir / segment.path
            assert segment.frame_count == local_result.total_frames
            assert segment.sha256 == local_source_hashes[source_path.name]

        discovered = [
            take
            for take in discover_takes(harness.recordings_path)
            if take.path == take_dir
        ]
        assert len(discovered) == 1
        take = discovered[0]
        assert take.validation_status == "complete"
        assert {track.source for track in take.tracks} == {
            "jamulus_server",
            "local_isolated",
        }
        assert {track.name for track in take.tracks} == {
            harness.CLIENT_A_NAME,
            harness.CLIENT_B_NAME,
            f"{harness.CLIENT_A_NAME} Input 1",
            f"{harness.CLIENT_A_NAME} Input 2",
        }

        sink = _PullSink()
        player = TakePlayer(samplerate=SAMPLE_RATE, sink=sink)
        player.load(take)
        assert len(player.tracks) == 4
        assert {track.source for track in player.tracks} == {
            "jamulus_server",
            "local_isolated",
        }
        player.seek(2.5)
        player.play()
        assert sink.pull is not None
        studio_block = sink.pull(4096)
        player.stop()
        assert sink.stopped
        assert studio_block.shape == (4096, 2)
        assert np.isfinite(studio_block).all()
        assert float(np.max(np.abs(studio_block))) > 0.01

        export = export_track_package(
            take,
            destination_root=harness.root / "track-exports",
            chunk_frames=4096,
        )
        assert len(export.stems) == 4
        assert export.reference_mix is not None and export.reference_mix.is_file()
        assert all(sf.info(path).samplerate == SAMPLE_RATE for path in export.stems)
        assert all(sf.info(path).frames == export.frames for path in export.stems)
        assert all(sf.info(path).subtype == "PCM_24" for path in export.stems)

        handoff = json.loads(export.manifest.read_text(encoding="utf-8"))
        assert handoff["schema_version"] == 2
        assert handoff["source_take_id"] == project.take_id
        assert handoff["original_files_modified"] is False
        assert handoff["external_editor_physically_verified"] is False
        assert {track["source_type"] for track in handoff["tracks"]} == {
            "jamulus_server",
            "local_isolated",
        }
        assert {track["source_quality"] for track in handoff["tracks"]} == {
            "network_track",
            "unverified",
        }
        assert {track["musician"] for track in handoff["tracks"]} == {
            harness.CLIENT_A_NAME,
            harness.CLIENT_B_NAME,
        }
        recording_report = export.recording_report.read_text(encoding="utf-8")
        assert "jamulus_server; network_track" in recording_report
        assert "local_isolated; unverified" in recording_report

        for evidence, exported_stem in zip(handoff["tracks"], export.stems):
            source_path = evidence["segments"][0]["path"]
            assert evidence["output_filename"] == exported_stem.name
            assert evidence["output_sha256"] == _sha256(exported_stem)
            if evidence["source_type"] == "jamulus_server":
                participant_id = evidence["participant_id"]
                assert participant_id in expected_by_participant_id
                assert source_path.startswith("server-media-")
                assert not any(
                    name in source_path
                    for name in (harness.CLIENT_A_NAME, harness.CLIENT_B_NAME)
                )
                _name, expected = expected_by_participant_id[participant_id]
                expected_source_hash = server_hashes_by_participant_id[
                    participant_id
                ]
            else:
                expected = {
                    "host-guitar.wav": SPEC_A,
                    "host-vocal.wav": SPEC_A,
                }[source_path]
                expected_source_hash = local_source_hashes[source_path]
            assert evidence["segments"][0]["declared_sha256"] == expected_source_hash
            assert evidence["segments"][0]["observed_sha256"] == expected_source_hash
            forbidden = (
                SPEC_B.frequency_hz
                if expected is SPEC_A
                else SPEC_A.frequency_hz
            )
            exported_metrics = analyze_recorded_stem(
                exported_stem,
                expected=expected,
                forbidden_frequency_hz=forbidden,
            )
            assert_recorded_stem_metrics(
                exported_metrics,
                expected=expected,
                duration_bounds_s=(6.0, 8.5),
                expected_channels=(
                    1 if evidence["source_type"] == "local_isolated" else 2
                ),
            )

        # Privacy staging intentionally renamed native recorder files. Their
        # immutable content remains provably identical under opaque paths, and
        # the explicitly named local originals remain in place unchanged.
        assert all(not path.exists() for path in source_wavs)
        assert {
            track.participant_id: _sha256(take_dir / track.segments[0].path)
            for track in server_tracks
        } == server_hashes_by_participant_id
        assert {
            path.name: _sha256(path) for path in local_result.files
        } == local_source_hashes
        persisted_recording_evidence = "\n".join(
            path.read_text(encoding="utf-8")
            for path in take_dir.iterdir()
            if path.is_file() and path.suffix.lower() in {".json", ".lof", ".rpp"}
        )
        assert all(
            native_name not in persisted_recording_evidence
            for native_name in native_recorder_names
        )
        assert all(
            address and address not in persisted_recording_evidence
            for address in authenticated_source_addresses
        )
        pipeline_evidence = {
            "sources": {
                name: {
                    "sample_rate": metrics.sample_rate,
                    "channels": metrics.channels,
                    "frames": metrics.frames,
                    "duration_s": round(metrics.duration_s, 6),
                    "dominant_hz": round(metrics.dominant_hz, 6),
                    "tone_rms": round(metrics.tone_rms, 6),
                    "silence_rms": round(metrics.silence_rms, 6),
                    "peak": round(metrics.peak, 6),
                    "cross_rejection_db": round(metrics.cross_rejection_db, 6),
                }
                for name, metrics in source_metrics.items()
            },
            "local_originals": {
                name: {
                    "sample_rate": metrics.sample_rate,
                    "channels": metrics.channels,
                    "frames": metrics.frames,
                    "dominant_hz": round(metrics.dominant_hz, 6),
                    "cross_rejection_db": round(metrics.cross_rejection_db, 6),
                }
                for name, metrics in local_metrics.items()
            },
            "track_export_frames": export.frames,
            "studio_peak": float(np.max(np.abs(studio_block))),
            "transport_xruns": transport.xrun_count,
            "source_hashes_preserved": True,
            "project_source_types": sorted(
                {track.source_type.value for track in project.tracks}
            ),
            "local_capture_seam": "fake sounddevice InputStream boundary only",
            "resumable_http_transfer": transfer_evidence,
        }

    assert pipeline_evidence["track_export_frames"]
    assert not harness.cleanup_errors
    assert all(process.proc.poll() is not None for process in harness.processes)
    print("JAMULUS_PIPELINE_EVIDENCE=" + json.dumps(pipeline_evidence, sort_keys=True))
