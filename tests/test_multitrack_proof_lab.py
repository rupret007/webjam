from __future__ import annotations

import json
import socket
from pathlib import Path
import stat
import uuid

import numpy as np
import pytest
import soundfile as sf

from core.recording_readiness import RecordingStorageCheck, RecordingStorageStatus
from core.session_recording_plan import SessionRecordingPlan
from core.take_library import (
    RecorderClientReceipt,
    parse_jamulus_recording_filename,
    write_take_manifest,
)
from tests.support.multitrack_proof_lab import (
    EXPECTED_SOURCE_COUNT,
    FRAMES,
    MAX_ARTIFACT_BYTES,
    MultitrackProofLab,
    PcmProofError,
    assert_pcm,
    signal_for,
    validate_completed_take,
    write_signal,
)


pytestmark = pytest.mark.requires_local_socket


def _loopback_bind_permitted() -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
    except PermissionError:
        return False
    finally:
        sock.close()
    return True


if not _loopback_bind_permitted():
    pytest.skip(
        "Skipping multitrack proof lab: loopback bind unavailable in this environment.",
        allow_module_level=True,
    )


def test_exact_multitrack_plan_reaches_repeat_lanes_and_studio_export(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import core.session_transfer_runtime as runtime

    monkeypatch.setattr(runtime, "is_private_lan_host", lambda _host: True)
    monkeypatch.setattr(runtime, "_POLL_SECONDS", 3_600.0)

    result = MultitrackProofLab(tmp_path).run()

    assert result.primary.status.value == "complete"
    assert result.repeated.status.value == "complete"
    assert len(result.primary.tracks) == EXPECTED_SOURCE_COUNT
    assert len(result.repeated.tracks) == EXPECTED_SOURCE_COUNT
    validate_completed_take(
        result.primary_take_root, expected_tracks=EXPECTED_SOURCE_COUNT
    )
    validate_completed_take(
        result.repeated_take_root, expected_tracks=EXPECTED_SOURCE_COUNT
    )
    assert result.export_root.is_dir()
    assert result.report["automatic_lane_count"] == EXPECTED_SOURCE_COUNT
    assert result.report["physical_status"] == "not_run"
    assert result.report_path.stat().st_size < 100 * 1024
    assert stat.S_IMODE(result.report_path.stat().st_mode) == 0o600
    assert (
        sum(
            path.stat().st_size
            for path in result.artifact_root.rglob("*")
            if path.is_file()
        )
        < MAX_ARTIFACT_BYTES
    )
    serialized = result.report_path.read_text(encoding="utf-8")
    assert str(tmp_path) not in serialized
    assert result.primary.session_id not in serialized
    assert result.primary.take_id not in serialized
    assert json.loads(serialized)["overall_status"] == "passed"


def test_pcm_and_planned_topology_mutations_fail_closed(tmp_path: Path) -> None:
    original = tmp_path / "original.wav"
    expected = write_signal(
        original,
        "mutation-source",
        take_index=1,
        channels=2,
    )
    assert_pcm(original, expected)

    silence = tmp_path / "silence.wav"
    sf.write(silence, np.zeros((FRAMES, 2), dtype=np.float32), 48_000, subtype="PCM_24")
    swapped = tmp_path / "swapped.wav"
    write_signal(swapped, "other-source", take_index=1, channels=2)
    truncated = tmp_path / "truncated.wav"
    sf.write(
        truncated,
        signal_for("mutation-source", take_index=1, channels=2)[:-128],
        48_000,
        subtype="PCM_24",
    )
    collapsed = tmp_path / "collapsed.wav"
    source = signal_for("mutation-source", take_index=1, channels=2)
    sf.write(
        collapsed,
        np.column_stack((source[:, 0], source[:, 0])),
        48_000,
        subtype="PCM_24",
    )
    mono = tmp_path / "mono.wav"
    sf.write(mono, source[:, 0], 48_000, subtype="PCM_24")

    for candidate in (silence, swapped, truncated, collapsed, mono):
        with pytest.raises(PcmProofError):
            assert_pcm(candidate, expected)

    take_root = tmp_path / "wrong-width"
    take_root.mkdir()
    session_id = str(uuid.uuid4())
    take_id = str(uuid.uuid4())
    participant_id = str(uuid.uuid4())
    key = "Guest-127_0_0_1_42001"
    filename = f"{key}-0-2.wav"
    write_signal(take_root / filename, "wrong-width", take_index=1, channels=2)
    parsed = parse_jamulus_recording_filename(filename)
    assert parsed is not None
    receipt = RecorderClientReceipt(
        0,
        "Guest",
        participant_id,
        parsed.recorder_key_sha256,
        2,
    )
    plan = SessionRecordingPlan(
        session_id=session_id,
        take_id=take_id,
        plan_generation=1,
        roster=((participant_id, "Guest"),),
        expected_server_stems=(participant_id,),
        count_in_frames=0,
        pre_roll_frames=0,
        storage=RecordingStorageCheck(
            RecordingStorageStatus.READY,
            "Mutation storage is ready.",
            free_bytes=1024**3,
            required_bytes=1024,
        ),
        expected_source_count=1,
        created_at_utc="2026-08-17T02:30:00Z",
        server_channel_counts=(1,),
    )
    result = write_take_manifest(
        take_root,
        expected_tracks=1,
        required_local_stems=0,
        session_id=session_id,
        take_id=take_id,
        recording_receipts=(receipt,),
        recording_plan=plan,
    )
    assert not result.ok
    assert any("mono/stereo" in error for error in result.errors)
