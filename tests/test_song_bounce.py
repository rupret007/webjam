"""Transactional offline-bounce coverage for schema-3 song projects."""

from __future__ import annotations

import hashlib
import os
import shutil
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

import core.song_bounce as song_bounce
from core.song_bounce import (
    BounceArtifactKind,
    BounceFormat,
    Mp3EncoderCapability,
    MAX_PCM24_WAV_FRAMES,
    SongBounceCancelled,
    SongBounceEngine,
    SongBounceError,
    SongBounceRequest,
    SongBounceStale,
    mp3_bounce_capability,
)
from core.song_media_catalog import SongMediaCatalog
from core.song_project import MediaProvenance
from core.song_project_store import (
    create_project_bundle,
    import_project_media,
    save_project_bundle,
)
from core.studio_project import (
    StudioCycleRange,
    StudioRegion,
    default_song_studio_document,
)
from core.studio_renderer import StudioRenderer, studio_delivery_block


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(64 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _song(
    tmp_path: Path,
    *,
    frames: int = 4_096,
    backing_scale: float = 0.35,
    instrument_scale: float = 0.2,
) -> tuple[Path, object, object, StudioRenderer, tuple[Path, Path]]:
    phase = np.arange(frames, dtype=np.float32)
    backing = np.column_stack(
        (
            np.sin(phase * np.float32(0.017)) * np.float32(backing_scale),
            np.cos(phase * np.float32(0.013)) * np.float32(backing_scale * 0.7),
        )
    ).astype(np.float32)
    instrument = np.column_stack(
        (
            np.sin(phase * np.float32(0.031)) * np.float32(instrument_scale),
            np.sin(phase * np.float32(0.023)) * np.float32(instrument_scale * 0.8),
        )
    ).astype(np.float32)
    source_backing = tmp_path / "private backing source.wav"
    source_instrument = tmp_path / "private instrument source.wav"
    sf.write(source_backing, backing, 48_000, subtype="FLOAT")
    sf.write(source_instrument, instrument, 48_000, subtype="FLOAT")

    bundle = tmp_path / "Bounce Song.webjam"
    created = create_project_bundle(bundle, name="Bounce Song")
    first = import_project_media(
        bundle,
        created.project,
        source_backing,
        designate_backing=True,
        provenance=MediaProvenance.LOCAL_FILE,
    )
    with_track = first.project.add_track("Lead / Guitar")
    second = import_project_media(
        bundle,
        with_track,
        source_instrument,
        provenance=MediaProvenance.LOCAL_RECORDING,
    )
    saved = save_project_bundle(
        bundle,
        second.project,
        expected_token=created.token,
    )
    project = saved.project
    document = default_song_studio_document(project)
    audio_track = next(item for item in document.tracks if item.name == "Lead / Guitar")
    instrument_media = second.media
    instrument_region = StudioRegion(
        region_id="90000000-0000-0000-0000-000000000001",
        track_id=audio_track.track_id,
        source_media_id=instrument_media.media_id,
        source_start_frame=0,
        source_frame_count=frames,
        timeline_start_frame=0,
        timeline_frame_count=frames,
    )
    document = replace(document, regions=(*document.regions, instrument_region))
    catalog = SongMediaCatalog.load(project, bundle)
    renderer = StudioRenderer(
        project,
        document,
        bundle,
        block_frames=257,
        source_catalog=catalog,
    )
    return (
        bundle,
        project,
        document,
        renderer,
        (bundle / project.media[0].path, bundle / project.media[1].path),
    )


def _bounce(
    renderer: StudioRenderer,
    request: SongBounceRequest,
    *,
    engine: SongBounceEngine | None = None,
    cancel_event=None,
):
    worker = engine or SongBounceEngine()
    generation = worker.begin()
    return worker.bounce(
        renderer,
        request,
        generation=generation,
        cancel_event=cancel_event,
    )


def _decoded(path: Path) -> np.ndarray:
    values, sample_rate = sf.read(path, dtype="float32", always_2d=True)
    assert sample_rate == 48_000
    return values


def _pcm24_expected(values: np.ndarray) -> np.ndarray:
    delivered, _clipped = studio_delivery_block(values)
    # libsndfile's float-to-PCM24 path rounds/truncates within one 24-bit step.
    return delivered


def _assert_no_staging(parent: Path) -> None:
    assert not [
        item
        for item in parent.iterdir()
        if item.name.startswith((".webjam-bounce-", ".webjam-bounce-backup-"))
    ]


def test_wav_mix_matches_authoritative_renderer_and_reports_exact_evidence(
    tmp_path: Path,
) -> None:
    _bundle, _project, _document, renderer, media_paths = _song(tmp_path)
    source_receipts = tuple(
        (path.stat().st_mtime_ns, path.stat().st_size, _sha256(path))
        for path in media_paths
    )
    expected = np.concatenate(
        tuple(renderer.iter_blocks(start_frame=0, end_frame=4_096, block_frames=127))
    )
    destination = tmp_path / "Demo Mix.wav"

    result = _bounce(
        renderer,
        SongBounceRequest(destination=destination, block_frames=127),
    )

    actual = _decoded(destination)
    np.testing.assert_allclose(
        actual,
        _pcm24_expected(expected),
        rtol=0.0,
        atol=2.0 / (1 << 23),
    )
    info = sf.info(destination)
    assert info.format == "WAV"
    assert info.subtype == "PCM_24"
    assert info.channels == 2
    assert info.frames == 4_096
    assert result.mix.kind is BounceArtifactKind.MIX
    assert result.mix.path == destination
    assert result.mix.sha256 == _sha256(destination)
    assert result.mix.size_bytes == destination.stat().st_size
    assert result.mix.frame_count == 4_096
    assert result.mix.analysis.peak_amplitude == pytest.approx(
        float(np.max(np.abs(expected)))
    )
    assert result.mix.analysis.clipped_sample_count == 0
    assert result.mix.analysis.peak_dbfs is not None
    assert result.mix.analysis.loudness_dbfs is not None
    assert tuple(
        (path.stat().st_mtime_ns, path.stat().st_size, _sha256(path))
        for path in media_paths
    ) == source_receipts
    _assert_no_staging(tmp_path)


def test_flac_explicit_range_and_cycle_are_exact_pcm24(
    tmp_path: Path,
) -> None:
    _bundle, _project, document, renderer, _media_paths = _song(tmp_path)
    ranged = tmp_path / "Selected Range.flac"
    range_result = _bounce(
        renderer,
        SongBounceRequest(
            destination=ranged,
            audio_format=BounceFormat.FLAC,
            start_frame=111,
            end_frame=1_111,
            block_frames=73,
        ),
    )
    expected = renderer.render_block(111, 1_000)
    np.testing.assert_allclose(
        _decoded(ranged),
        expected,
        rtol=0.0,
        atol=2.0 / (1 << 23),
    )
    info = sf.info(ranged)
    assert info.format == "FLAC"
    assert info.subtype == "PCM_24"
    assert range_result.start_frame == 111
    assert range_result.end_frame == 1_111

    cycle_document = replace(
        document,
        cycle_range=StudioCycleRange(500, 900),
    )
    cycle_renderer = StudioRenderer(
        renderer.project,
        cycle_document,
        renderer.take_root,
        source_catalog=renderer.source_catalog,
    )
    cycled = tmp_path / "Cycle.wav"
    cycle_result = _bounce(
        cycle_renderer,
        SongBounceRequest(destination=cycled, use_cycle_range=True),
    )
    assert cycle_result.start_frame == 500
    assert cycle_result.end_frame == 900
    np.testing.assert_allclose(
        _decoded(cycled),
        cycle_renderer.render_block(500, 400),
        rtol=0.0,
        atol=2.0 / (1 << 23),
    )


def test_selected_mix_can_exclude_backing_and_publish_processed_stems(
    tmp_path: Path,
) -> None:
    _bundle, _project, document, renderer, _media_paths = _song(tmp_path)
    audio_track = next(item for item in document.tracks if item.name == "Lead / Guitar")
    destination = tmp_path / "Band Mix.wav"
    result = _bounce(
        renderer,
        SongBounceRequest(
            destination=destination,
            include_backing=False,
            create_stems=True,
        ),
    )

    expected = StudioRenderer(
        renderer.project,
        document,
        renderer.take_root,
        track_ids=(audio_track.track_id,),
        source_catalog=renderer.source_catalog,
    ).render_block(0, 4_096)
    np.testing.assert_allclose(
        _decoded(destination),
        expected,
        rtol=0.0,
        atol=2.0 / (1 << 23),
    )
    assert result.selected_track_ids == (audio_track.track_id,)
    assert not result.included_backing
    assert len(result.stems) == 1
    stem = result.stems[0]
    assert stem.track_id == audio_track.track_id
    assert stem.track_name == audio_track.name
    assert "/" not in stem.path.name
    assert stem.path.exists()
    np.testing.assert_allclose(
        _decoded(stem.path),
        expected,
        rtol=0.0,
        atol=2.0 / (1 << 23),
    )
    _assert_no_staging(tmp_path)


def test_default_range_follows_selected_content_and_replacement_is_atomic(
    tmp_path: Path,
) -> None:
    _bundle, _project, document, renderer, _media_paths = _song(tmp_path)
    backing_track, audio_track = document.tracks
    backing_region, audio_region = document.regions
    shortened = replace(
        document,
        regions=(
            backing_region,
            replace(
                audio_region,
                source_frame_count=2_000,
                timeline_frame_count=2_000,
                mapping_source_frame_count=2_000,
                mapping_timeline_frame_count=2_000,
            ),
        ),
    )
    selected_renderer = StudioRenderer(
        renderer.project,
        shortened,
        renderer.take_root,
        source_catalog=renderer.source_catalog,
    )
    destination = tmp_path / "Replace Me.wav"
    destination.write_bytes(b"previous output")

    result = _bounce(
        selected_renderer,
        SongBounceRequest(
            destination=destination,
            track_ids=(audio_track.track_id,),
            include_backing=False,
        ),
    )

    assert result.end_frame == 2_000
    assert result.mix.frame_count == 2_000
    assert destination.read_bytes() != b"previous output"
    assert result.mix.sha256 == _sha256(destination)
    assert backing_track.track_id not in result.selected_track_ids
    _assert_no_staging(tmp_path)


def test_peak_clipping_and_loudness_are_deterministic_and_silence_is_explicit(
    tmp_path: Path,
) -> None:
    _bundle, _project, document, renderer, _media_paths = _song(
        tmp_path,
        backing_scale=1.4,
        instrument_scale=1.2,
    )
    hot_document = replace(
        document,
        master=replace(document.master, limiter_enabled=False),
    )
    renderer = StudioRenderer(
        renderer.project,
        hot_document,
        renderer.take_root,
        source_catalog=renderer.source_catalog,
    )
    expected = renderer.render_block(0, 4_096)
    expected_clips = int(np.count_nonzero((expected < -1.0) | (expected > 1.0)))
    first = _bounce(
        renderer,
        SongBounceRequest(destination=tmp_path / "Hot.wav", block_frames=113),
    )
    second = _bounce(
        renderer,
        SongBounceRequest(
            destination=tmp_path / "Hot.flac",
            audio_format=BounceFormat.FLAC,
            block_frames=113,
        ),
    )
    assert expected_clips > 0
    assert first.mix.analysis.clipped_sample_count == expected_clips
    assert second.mix.analysis.clipped_sample_count == expected_clips
    assert first.mix.analysis.peak_amplitude > 1.0
    assert first.mix.analysis.peak_dbfs is not None
    assert first.mix.analysis.peak_dbfs > 0.0
    assert first.mix.analysis.loudness_dbfs == pytest.approx(
        second.mix.analysis.loudness_dbfs,
        abs=1e-12,
    )
    assert float(np.max(np.abs(_decoded(first.mix.path)))) <= 1.0

    silence_dir = tmp_path / "silence"
    silence_dir.mkdir()
    _bundle, _project, _document, silent_renderer, _media_paths = _song(
        silence_dir,
        backing_scale=0.0,
        instrument_scale=0.0,
    )
    silence = _bounce(
        silent_renderer,
        SongBounceRequest(destination=tmp_path / "Silence.wav"),
    )
    assert silence.mix.analysis.peak_amplitude == 0.0
    assert silence.mix.analysis.peak_dbfs is None
    assert silence.mix.analysis.loudness_dbfs is None
    assert silence.mix.analysis.clipped_sample_count == 0


def test_export_inclusion_and_explicit_track_selection_are_enforced(
    tmp_path: Path,
) -> None:
    _bundle, _project, document, renderer, _media_paths = _song(tmp_path)
    backing, instrument = document.tracks
    excluded_document = replace(
        document,
        tracks=(backing, replace(instrument, export_included=False)),
    )
    excluded_renderer = StudioRenderer(
        renderer.project,
        excluded_document,
        renderer.take_root,
        source_catalog=renderer.source_catalog,
    )
    output = tmp_path / "Included.wav"
    result = _bounce(
        excluded_renderer,
        SongBounceRequest(destination=output),
    )
    assert result.selected_track_ids == (backing.track_id,)

    with pytest.raises(SongBounceError, match="No enabled tracks"):
        _bounce(
            excluded_renderer,
            SongBounceRequest(
                destination=tmp_path / "Nothing.wav",
                track_ids=(instrument.track_id,),
            ),
        )
    override = _bounce(
        excluded_renderer,
        SongBounceRequest(
            destination=tmp_path / "Override.wav",
            track_ids=(instrument.track_id,),
            respect_export_included=False,
        ),
    )
    assert override.selected_track_ids == (instrument.track_id,)


class _CancelAfter:
    def __init__(self, calls: int) -> None:
        self.remaining = calls

    def is_set(self) -> bool:
        self.remaining -= 1
        return self.remaining <= 0


def test_cancel_and_stale_generation_remove_every_unpublished_file(
    tmp_path: Path,
) -> None:
    _bundle, _project, _document, renderer, _media_paths = _song(
        tmp_path,
        frames=20_000,
    )
    destination = tmp_path / "Cancelled.wav"
    engine = SongBounceEngine()
    generation = engine.begin()
    with pytest.raises(SongBounceCancelled, match="cancelled"):
        engine.bounce(
            renderer,
            SongBounceRequest(destination=destination, block_frames=64),
            generation=generation,
            cancel_event=_CancelAfter(12),
        )
    assert not destination.exists()
    _assert_no_staging(tmp_path)

    stale = engine.begin()
    current = engine.begin()
    assert current != stale
    with pytest.raises(SongBounceStale, match="newer bounce"):
        engine.bounce(
            renderer,
            SongBounceRequest(destination=destination),
            generation=stale,
        )
    assert not destination.exists()


def test_generation_change_during_streaming_rejects_old_worker(
    tmp_path: Path,
) -> None:
    _bundle, _project, _document, renderer, _media_paths = _song(
        tmp_path,
        frames=20_000,
    )
    destination = tmp_path / "Superseded.wav"
    engine = SongBounceEngine()
    generation = engine.begin()

    class Supersede:
        calls = 0

        def is_set(self) -> bool:
            self.calls += 1
            if self.calls == 15:
                engine.begin()
            return False

    with pytest.raises(SongBounceStale, match="superseded"):
        engine.bounce(
            renderer,
            SongBounceRequest(destination=destination, block_frames=64),
            generation=generation,
            cancel_event=Supersede(),
        )
    assert not destination.exists()
    _assert_no_staging(tmp_path)


def test_atomic_multi_output_failure_restores_all_existing_outputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bundle, _project, _document, renderer, _media_paths = _song(tmp_path)
    destination = tmp_path / "Atomic.wav"
    track_count = len(renderer.document.tracks)
    stem_paths = song_bounce._stem_paths(destination, renderer.document.tracks)
    destination.write_bytes(b"previous mix")
    for index, stem in enumerate(stem_paths):
        stem.write_bytes(f"previous stem {index}".encode())
    receipts = {
        path: path.read_bytes() for path in (destination, *stem_paths)
    }
    real_replace = os.replace
    publications = 0

    def fail_second_publication(source, target):
        nonlocal publications
        source_path = Path(source)
        if source_path.name.startswith(".webjam-bounce-") and not source_path.name.startswith(
            ".webjam-bounce-backup-"
        ):
            publications += 1
            if publications == 2:
                raise OSError("private destination failure")
        return real_replace(source, target)

    monkeypatch.setattr(song_bounce.os, "replace", fail_second_publication)
    with pytest.raises(SongBounceError, match="published atomically"):
        _bounce(
            renderer,
            SongBounceRequest(destination=destination, create_stems=True),
        )

    assert track_count == len(stem_paths)
    assert {path: path.read_bytes() for path in receipts} == receipts
    _assert_no_staging(tmp_path)


def test_corrupt_staging_output_is_never_published_or_leaked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bundle, _project, _document, renderer, _media_paths = _song(tmp_path)
    destination = tmp_path / "Corrupt.wav"

    original_verify = song_bounce._verify_pcm24

    def corrupt(stage, **kwargs):
        stage.path.write_bytes(b"truncated")
        original_verify(stage, **kwargs)

    monkeypatch.setattr(song_bounce, "_verify_pcm24", corrupt)
    with pytest.raises(SongBounceError, match="decoded|identity check"):
        _bounce(renderer, SongBounceRequest(destination=destination))
    assert not destination.exists()
    _assert_no_staging(tmp_path)


class _FakeMp3Encoder:
    def __init__(
        self,
        *,
        capability: Mp3EncoderCapability | None = None,
        corrupt: bool = False,
    ) -> None:
        self.capability = capability or Mp3EncoderCapability(
            available=True,
            self_tested=True,
            adapter_id="test.encoder",
            license_spdx="MIT",
            detail="Test-only encoder.",
        )
        self.corrupt = corrupt
        self.probes = 0
        self.encodes = 0
        self.verifies = 0

    def probe(self) -> Mp3EncoderCapability:
        self.probes += 1
        return self.capability

    def encode_pcm24_wav(
        self,
        source_wav: Path,
        destination: Path,
        *,
        sample_rate: int,
        channels: int,
        cancel_check,
    ) -> None:
        assert sf.info(source_wav).subtype == "PCM_24"
        assert sample_rate == 48_000
        assert channels == 2
        cancel_check()
        self.encodes += 1
        destination.write_bytes(b"not-mp3" if self.corrupt else b"ID3test-encoded")

    def verify_output(
        self,
        destination: Path,
        *,
        sample_rate: int,
        channels: int,
        frame_count: int,
    ) -> None:
        self.verifies += 1
        if self.corrupt or not destination.read_bytes().startswith(b"ID3"):
            raise ValueError("private decoder details")
        assert sample_rate == 48_000
        assert channels == 2
        assert frame_count == 4_096


def test_mp3_is_disabled_by_default_and_requires_tested_permissive_adapter(
    tmp_path: Path,
) -> None:
    _bundle, _project, _document, renderer, _media_paths = _song(tmp_path)
    capability = mp3_bounce_capability()
    assert not capability.available
    assert not capability.self_tested
    destination = tmp_path / "Mix.mp3"
    with pytest.raises(SongBounceError, match="No tested MP3 encoder"):
        _bounce(
            renderer,
            SongBounceRequest(
                destination=destination,
                audio_format=BounceFormat.MP3,
            ),
        )

    adapter = _FakeMp3Encoder()
    engine = SongBounceEngine(mp3_encoder=adapter)
    result = _bounce(
        renderer,
        SongBounceRequest(
            destination=destination,
            audio_format=BounceFormat.MP3,
        ),
        engine=engine,
    )
    assert result.mp3_encoder_id == "test.encoder"
    assert result.mix.sha256 == _sha256(destination)
    assert destination.read_bytes().startswith(b"ID3")
    assert adapter.probes == adapter.encodes == adapter.verifies == 1
    _assert_no_staging(tmp_path)

    denied = _FakeMp3Encoder(
        capability=Mp3EncoderCapability(
            available=True,
            self_tested=True,
            adapter_id="denied.encoder",
            license_spdx="GPL-3.0-only",
        )
    )
    with pytest.raises(SongBounceError, match="disallowed license"):
        _bounce(
            renderer,
            SongBounceRequest(
                destination=tmp_path / "Denied.mp3",
                audio_format=BounceFormat.MP3,
            ),
            engine=SongBounceEngine(mp3_encoder=denied),
        )


def test_failed_mp3_verification_cleans_intermediate_and_encoded_files(
    tmp_path: Path,
) -> None:
    _bundle, _project, _document, renderer, _media_paths = _song(tmp_path)
    destination = tmp_path / "Broken.mp3"
    with pytest.raises(SongBounceError, match="failed output verification"):
        _bounce(
            renderer,
            SongBounceRequest(
                destination=destination,
                audio_format=BounceFormat.MP3,
            ),
            engine=SongBounceEngine(mp3_encoder=_FakeMp3Encoder(corrupt=True)),
        )
    assert not destination.exists()
    _assert_no_staging(tmp_path)


def test_mp3_adapter_capability_contract_rejects_malformed_claims(
    tmp_path: Path,
) -> None:
    _bundle, _project, _document, renderer, _media_paths = _song(tmp_path)
    malformed = _FakeMp3Encoder(
        capability=Mp3EncoderCapability(
            available=True,
            self_tested=False,
            adapter_id="",
            license_spdx="",
        )
    )
    with pytest.raises(SongBounceError, match="fully identified tested"):
        _bounce(
            renderer,
            SongBounceRequest(
                destination=tmp_path / "Malformed.mp3",
                audio_format=BounceFormat.MP3,
            ),
            engine=SongBounceEngine(mp3_encoder=malformed),
        )


def test_long_bounce_never_requests_more_than_the_configured_block(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bundle, _project, _document, renderer, _media_paths = _song(
        tmp_path,
        frames=250_000,
    )
    observed: list[int] = []
    original = StudioRenderer.iter_blocks

    def bounded(self, **kwargs):
        for block in original(self, **kwargs):
            observed.append(len(block))
            yield block

    monkeypatch.setattr(StudioRenderer, "iter_blocks", bounded)
    destination = tmp_path / "Long.flac"
    result = _bounce(
        renderer,
        SongBounceRequest(
            destination=destination,
            audio_format=BounceFormat.FLAC,
            block_frames=257,
        ),
    )
    assert result.mix.frame_count == 250_000
    assert observed
    assert max(observed) <= 257
    assert len(observed) > 900
    assert sf.info(destination).frames == 250_000


def test_media_replacement_during_bounce_is_detected_before_publication(
    tmp_path: Path,
) -> None:
    _bundle, _project, _document, renderer, media_paths = _song(
        tmp_path,
        frames=12_000,
    )
    victim = media_paths[0]
    replacement = tmp_path / "replacement.wav"
    sf.write(
        replacement,
        np.zeros((12_000, 2), dtype=np.float32),
        48_000,
        subtype="FLOAT",
    )

    class ReplaceDuringRender:
        calls = 0

        def is_set(self) -> bool:
            self.calls += 1
            if self.calls == 12:
                replacement.replace(victim)
            return False

    destination = tmp_path / "Changed.wav"
    with pytest.raises(
        SongBounceError,
        match="media changed|rendered safely|failed bounce validation",
    ):
        _bounce(
            renderer,
            SongBounceRequest(destination=destination, block_frames=64),
            cancel_event=ReplaceDuringRender(),
        )
    assert not destination.exists()
    _assert_no_staging(tmp_path)


def test_path_safety_validation_and_errors_never_disclose_private_paths(
    tmp_path: Path,
) -> None:
    bundle, _project, _document, renderer, _media_paths = _song(tmp_path)
    private = bundle / "Media" / "private bounce.wav"
    with pytest.raises(SongBounceError) as captured:
        _bounce(renderer, SongBounceRequest(destination=private))
    assert str(bundle) not in str(captured.value)
    assert "private bounce" not in str(captured.value)

    outside = tmp_path / "link.wav"
    outside.symlink_to(tmp_path / "secret.wav")
    with pytest.raises(SongBounceError) as captured:
        _bounce(renderer, SongBounceRequest(destination=outside))
    assert str(tmp_path) not in str(captured.value)
    assert "secret" not in str(captured.value)


def test_disk_preflight_and_cancel_contract_fail_before_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bundle, _project, _document, renderer, _media_paths = _song(tmp_path)
    destination = tmp_path / "No Space.wav"
    usage = shutil.disk_usage(tmp_path)
    monkeypatch.setattr(
        song_bounce.shutil,
        "disk_usage",
        lambda _path: type(usage)(usage.total, usage.used, 0),
    )
    with pytest.raises(SongBounceError, match="Not enough free space"):
        _bounce(renderer, SongBounceRequest(destination=destination))
    assert not destination.exists()

    monkeypatch.undo()

    class InvalidCancel:
        def is_set(self):
            return 1

    with pytest.raises(SongBounceError, match="true or false"):
        _bounce(
            renderer,
            SongBounceRequest(destination=destination),
            cancel_event=InvalidCancel(),
        )
    assert not destination.exists()


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"destination": Path("mix.flac")}, "requires a .wav"),
        (
            {
                "destination": Path("mix.wav"),
                "start_frame": 1,
            },
            "both start_frame and end_frame",
        ),
        (
            {
                "destination": Path("mix.wav"),
                "start_frame": 10,
                "end_frame": 10,
            },
            "later than",
        ),
        (
            {
                "destination": Path("mix.wav"),
                "block_frames": 0,
            },
            "between",
        ),
    ],
)
def test_request_validation_is_strict(kwargs, message: str) -> None:
    with pytest.raises(SongBounceError, match=message):
        SongBounceRequest(**kwargs)


def test_pcm24_wav_refuses_ranges_beyond_the_riff_limit(tmp_path: Path) -> None:
    _bundle, _project, _document, renderer, _media_paths = _song(tmp_path)
    with pytest.raises(SongBounceError, match="RIFF size limit"):
        _bounce(
            renderer,
            SongBounceRequest(
                destination=tmp_path / "Too Long.wav",
                start_frame=0,
                end_frame=MAX_PCM24_WAV_FRAMES + 1,
            ),
        )
