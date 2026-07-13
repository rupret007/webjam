from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest
import soundfile as sf

from core.take_export import (
    TakeExportError,
    TrackMixSettings,
    export_logic_package,
)
from core.take_library import TakeInfo, TrackInfo


RATE = 8000


def _write(path: Path, data, *, rate: int = RATE) -> None:
    sf.write(path, np.asarray(data, dtype="float32"), rate, subtype="PCM_16")


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_logic_export_aligns_signed_offsets_and_preserves_originals(tmp_path):
    take_dir = tmp_path / "Take 01"
    take_dir.mkdir()
    early = take_dir / "host-guitar.wav"
    late = take_dir / "drums.wav"
    _write(
        early,
        np.concatenate(
            (np.full(RATE // 4, 0.1), np.full(RATE // 2, 0.4))
        ),
    )
    _write(late, np.full(RATE // 2, 0.2))
    before = {_digest(early), _digest(late)}
    take = TakeInfo(
        path=take_dir,
        name="Take 01",
        tracks=[
            TrackInfo(
                early,
                "Guitar / Lead",
                offset_s=-0.25,
                duration_s=0.75,
                samplerate=RATE,
                source="local_ssl",
            ),
            TrackInfo(
                late,
                "Drums",
                offset_s=0.25,
                duration_s=0.5,
                samplerate=RATE,
            ),
        ],
    )

    result = export_logic_package(take, destination_root=tmp_path / "exports")

    assert result.frames == int(0.75 * RATE)
    assert len(result.stems) == 2
    first, first_rate = sf.read(result.stems[0], dtype="float32")
    second, second_rate = sf.read(result.stems[1], dtype="float32")
    assert first_rate == second_rate == RATE
    assert len(first) == len(second) == result.frames
    assert first[0] == pytest.approx(0.4, abs=0.01)
    assert np.max(np.abs(second[: RATE // 4])) < 1e-5
    assert second[RATE // 4] == pytest.approx(0.2, abs=0.01)
    assert sf.info(result.stems[0]).subtype == "PCM_24"
    assert {_digest(early), _digest(late)} == before

    payload = json.loads(result.manifest.read_text(encoding="utf-8"))
    assert payload["all_stems_start_at_zero"] is True
    assert payload["original_files_modified"] is False
    assert payload["tracks"][0]["original_offset_s"] == -0.25
    assert payload["tracks"][0]["output_filename"].startswith(
        "01 Guitar - Lead"
    )
    assert "0:00" in result.instructions.read_text(encoding="utf-8")


def test_logic_export_rough_mix_honors_gain_pan_mute_and_solo(tmp_path):
    take_dir = tmp_path / "Take"
    take_dir.mkdir()
    left = take_dir / "left.wav"
    muted = take_dir / "muted.wav"
    _write(left, np.full(RATE // 4, 0.4))
    _write(muted, np.full(RATE // 4, 0.8))
    take = TakeInfo(
        path=take_dir,
        name="Take",
        tracks=[
            TrackInfo(left, "Guitar", duration_s=0.25, samplerate=RATE),
            TrackInfo(muted, "Drums", duration_s=0.25, samplerate=RATE),
        ],
    )
    result = export_logic_package(
        take,
        destination_root=tmp_path / "exports",
        mix_settings={
            0: TrackMixSettings(gain=0.5, pan=-1.0, solo=True),
            1: TrackMixSettings(gain=1.0, pan=1.0, muted=False, solo=False),
        },
    )
    mix, rate = sf.read(result.mixdown, dtype="float32", always_2d=True)
    assert rate == RATE
    assert mix.shape[1] == 2
    assert mix[100, 0] == pytest.approx(0.2, abs=0.01)
    assert abs(float(mix[100, 1])) < 1e-5
    payload = json.loads(result.manifest.read_text(encoding="utf-8"))
    assert payload["tracks"][0]["gain"] == 0.5
    assert payload["tracks"][0]["pan"] == -1.0
    assert payload["tracks"][0]["solo"] is True


def test_logic_export_never_overwrites_an_existing_package(tmp_path):
    take_dir = tmp_path / "Take"
    take_dir.mkdir()
    track = take_dir / "guitar.wav"
    _write(track, np.ones(2048) * 0.1)
    take = TakeInfo(
        take_dir,
        "Take",
        [TrackInfo(track, "Guitar", duration_s=2048 / RATE, samplerate=RATE)],
    )
    root = tmp_path / "exports"
    first = export_logic_package(take, destination_root=root)
    second = export_logic_package(take, destination_root=root)
    assert first.folder.name == "Logic Export"
    assert second.folder.name == "Logic Export 2"
    assert first.manifest.exists()


def test_logic_export_rejects_mixed_rates_without_partial_package(tmp_path):
    take_dir = tmp_path / "Take"
    take_dir.mkdir()
    first = take_dir / "a.wav"
    second = take_dir / "b.wav"
    _write(first, np.ones(2048) * 0.1, rate=8000)
    _write(second, np.ones(2048) * 0.1, rate=16000)
    take = TakeInfo(
        take_dir,
        "Take",
        [
            TrackInfo(first, "A", duration_s=0.25, samplerate=8000),
            TrackInfo(second, "B", duration_s=0.125, samplerate=16000),
        ],
    )
    root = tmp_path / "exports"
    with pytest.raises(TakeExportError, match="mixed sample rates"):
        export_logic_package(take, destination_root=root)
    assert not root.exists()


def test_logic_export_write_failure_leaves_no_visible_or_hidden_package(tmp_path):
    take_dir = tmp_path / "Take"
    take_dir.mkdir()
    track = take_dir / "guitar.wav"
    _write(track, np.ones(2048) * 0.1)
    take = TakeInfo(
        take_dir,
        "Take",
        [TrackInfo(track, "Guitar", duration_s=2048 / RATE, samplerate=RATE)],
    )
    root = tmp_path / "exports"
    with patch(
        "core.take_export._write_aligned_stem",
        side_effect=OSError("disk full"),
    ), pytest.raises(OSError, match="disk full"):
        export_logic_package(take, destination_root=root)
    assert root.is_dir()
    assert list(root.iterdir()) == []
