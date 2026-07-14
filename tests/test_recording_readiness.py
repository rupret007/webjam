"""Storage preflight contracts for a real-session recording start."""

from __future__ import annotations

from collections import namedtuple
from unittest.mock import patch

from core.recording_readiness import (
    RecordingStorageStatus,
    check_recording_storage,
    recording_storage_budget,
)


Usage = namedtuple("Usage", "total used free")
GIB = 1024**3


def test_storage_budget_scales_with_the_real_recording_inventory() -> None:
    minimum_small, warning_small = recording_storage_budget(
        expected_server_tracks=2,
        local_originals_enabled=False,
    )
    minimum_large, warning_large = recording_storage_budget(
        expected_server_tracks=5,
        local_originals_enabled=True,
    )

    assert minimum_large >= minimum_small
    assert warning_large >= warning_small
    assert minimum_small >= GIB
    assert warning_small >= 5 * GIB


def test_storage_preflight_blocks_missing_or_unavailable_folder(tmp_path) -> None:
    missing = tmp_path / "not-there"
    result = check_recording_storage(
        missing,
        expected_server_tracks=2,
        local_originals_enabled=False,
    )

    assert result.status is RecordingStorageStatus.ACTION_NEEDED
    assert not result.can_start
    assert str(missing) not in result.detail


def test_storage_preflight_blocks_dangerously_low_space_without_path(tmp_path) -> None:
    result = check_recording_storage(
        tmp_path,
        expected_server_tracks=2,
        local_originals_enabled=True,
        disk_usage=lambda _path: Usage(10 * GIB, 10 * GIB - 512 * 1024**2, 512 * 1024**2),
    )

    assert result.status is RecordingStorageStatus.ACTION_NEEDED
    assert not result.can_start
    assert "free up space" in result.detail.lower()
    assert str(tmp_path) not in result.detail


def test_storage_preflight_warns_before_a_long_rehearsal(tmp_path) -> None:
    result = check_recording_storage(
        tmp_path,
        expected_server_tracks=2,
        local_originals_enabled=False,
        disk_usage=lambda _path: Usage(10 * GIB, 6 * GIB, 4 * GIB),
    )

    assert result.status is RecordingStorageStatus.WARNING
    assert result.can_start
    assert "long rehearsal" in result.detail.lower()


def test_storage_preflight_treats_a_failed_disk_probe_as_actionable(tmp_path) -> None:
    result = check_recording_storage(
        tmp_path,
        expected_server_tracks=2,
        local_originals_enabled=False,
        disk_usage=lambda _path: (_ for _ in ()).throw(OSError("private path")),
    )

    assert result.status is RecordingStorageStatus.ACTION_NEEDED
    assert "private path" not in result.detail
    assert str(tmp_path) not in result.detail


def test_storage_preflight_blocks_a_folder_that_fails_a_real_write_probe(tmp_path) -> None:
    with patch(
        "core.recording_readiness.tempfile.TemporaryFile",
        side_effect=OSError("private write failure"),
    ):
        result = check_recording_storage(
            tmp_path,
            expected_server_tracks=2,
            local_originals_enabled=False,
        )

    assert result.status is RecordingStorageStatus.ACTION_NEEDED
    assert not result.can_start
    assert "private write failure" not in result.detail
    assert str(tmp_path) not in result.detail
