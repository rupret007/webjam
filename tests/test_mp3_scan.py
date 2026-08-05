"""Authoritative descriptor and decoder contracts for MPEG Layer III."""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

import core.mp3_scan as mp3_scan
import core.project_audio as project_audio
from core.mp3_scan import (
    MP3_MAX_TRAILING_REPORT_FRAMES,
    Mp3GaplessDeclaration,
    Mp3Scan,
    Mp3ScanError,
    scan_mp3_descriptor,
)
from core.project_audio import ProjectAudioDecoder, ProjectAudioError


def _identity(info: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        int(info.st_dev),
        int(info.st_ino),
        int(info.st_size),
        int(getattr(info, "st_mtime_ns", round(info.st_mtime * 1_000_000_000))),
        int(getattr(info, "st_ctime_ns", round(info.st_ctime * 1_000_000_000))),
    )


def _write_mp3(
    path: Path,
    *,
    samplerate: int = 48_000,
    seconds: float = 1.0,
) -> Path:
    if not sf.check_format("MP3"):
        pytest.skip("the locked libsndfile build has no MP3 capability")
    frames = round(samplerate * seconds)
    timeline = np.arange(frames, dtype=np.float32) / samplerate
    samples = (0.3 * np.sin(2 * np.pi * 330.0 * timeline)).astype(np.float32)
    sf.write(
        path,
        samples,
        samplerate,
        format="MP3",
        subtype="MPEG_LAYER_III",
    )
    return path


def _scan(path: Path) -> Mp3Scan:
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_BINARY", 0),
    )
    try:
        info = os.fstat(descriptor)
        return scan_mp3_descriptor(
            descriptor,
            expected_identity=_identity(info),
            max_source_frames=project_audio.PROJECT_AUDIO_MAX_SOURCE_FRAMES,
            max_duration_seconds=project_audio.PROJECT_AUDIO_MAX_DURATION_SECONDS,
            max_file_bytes=project_audio.PROJECT_AUDIO_MAX_MP3_FILE_BYTES,
        )
    finally:
        os.close(descriptor)


def _synchsafe(value: int) -> bytes:
    return bytes(
        (
            (value >> 21) & 0x7F,
            (value >> 14) & 0x7F,
            (value >> 7) & 0x7F,
            value & 0x7F,
        )
    )


def _itunsmpb_value(declaration: Mp3GaplessDeclaration) -> str:
    values = (
        0,
        declaration.delay_frames,
        declaration.padding_frames,
        declaration.content_frames,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
    )
    return " ".join(
        f"{value:016X}" if index == 3 else f"{value:08X}"
        for index, value in enumerate(values)
    )


def _prepend_id3v22_comments(
    path: Path,
    declarations: tuple[tuple[str, str], ...],
) -> None:
    frames = bytearray()
    for description, value in declarations:
        payload = (
            b"\x00eng"
            + description.encode("latin-1")
            + b"\0"
            + value.encode("latin-1")
        )
        frames.extend(b"COM")
        frames.extend(len(payload).to_bytes(3, "big"))
        frames.extend(payload)
    frames.extend(b"\0" * 32)
    header = b"ID3" + bytes((2, 0, 0)) + _synchsafe(len(frames))
    path.write_bytes(header + frames + path.read_bytes())


def _prepend_id3v22_user_text(path: Path, value: str) -> None:
    payload = b"\x00iTunSMPB\0" + value.encode("latin-1")
    frame = b"TXX" + len(payload).to_bytes(3, "big") + payload
    body = frame + b"\0" * 32
    header = b"ID3" + bytes((2, 0, 0)) + _synchsafe(len(body))
    path.write_bytes(header + body + path.read_bytes())


def _mpeg1_layout(path: Path) -> tuple[list[tuple[int, int]], int, int]:
    data = path.read_bytes()
    offset = 0
    if data[:3] == b"ID3":
        offset = 10 + (
            (data[6] << 21)
            | (data[7] << 14)
            | (data[8] << 7)
            | data[9]
        )
    audio_start = offset
    frames: list[tuple[int, int]] = []
    while offset < len(data):
        header = int.from_bytes(data[offset : offset + 4], "big")
        assert header >> 21 == 0x7FF
        assert (header >> 19) & 0x3 == 3
        bitrate = (
            0,
            32,
            40,
            48,
            56,
            64,
            80,
            96,
            112,
            128,
            160,
            192,
            224,
            256,
            320,
            0,
        )[(header >> 12) & 0xF]
        samplerate = (44_100, 48_000, 32_000)[(header >> 10) & 0x3]
        frame_bytes = (
            (144_000 * bitrate) // samplerate + ((header >> 9) & 1)
        )
        frames.append((offset, frame_bytes))
        offset += frame_bytes
    first_header = int.from_bytes(data[audio_start : audio_start + 4], "big")
    mono = ((first_header >> 6) & 0x3) == 3
    xing_offset = audio_start + 4 + (17 if mono else 32)
    return frames, xing_offset, audio_start


def _assert_path_free(error: BaseException, source: Path) -> None:
    assert str(source) not in str(error)
    assert source.name not in str(error)
    assert error.__cause__ is None


def test_current_lame_mp3_and_matching_structured_itunsmpb_are_accepted(
    tmp_path: Path,
) -> None:
    source = _write_mp3(tmp_path / "current.mp3", seconds=2.0)
    initial = _scan(source)
    assert initial.xing_kind in {"Xing", "Info"}
    assert initial.gapless is not None
    assert initial.physical_frames >= 2
    _prepend_id3v22_comments(
        source,
        (("iTunSMPB", _itunsmpb_value(initial.gapless)),),
    )

    scanned = _scan(source)
    assert scanned.gapless == initial.gapless
    with ProjectAudioDecoder(source) as decoder:
        assert decoder.probe.source_frames == initial.gapless.content_frames
        assert decoder.output_frames == round(2.0 * 48_000)


@pytest.mark.parametrize("samplerate", (48_000, 24_000, 12_000))
def test_scanner_accepts_mpeg_versions_written_by_locked_decoder(
    tmp_path: Path,
    samplerate: int,
) -> None:
    source = _write_mp3(
        tmp_path / f"mpeg-{samplerate}.mp3",
        samplerate=samplerate,
        seconds=1.0,
    )
    scanned = _scan(source)
    assert scanned.source_sample_rate == samplerate
    assert scanned.gapless is not None
    assert scanned.gapless.content_frames == samplerate


def test_scanner_accepts_one_exact_id3v1_tail_without_counting_it(
    tmp_path: Path,
) -> None:
    source = _write_mp3(tmp_path / "id3v1.mp3")
    before = _scan(source)
    source.write_bytes(source.read_bytes() + b"TAG" + b"\0" * 125)

    after = _scan(source)
    assert after.physical_frames == before.physical_frames
    assert after.audio_bytes == before.audio_bytes
    assert after.raw_frames == before.raw_frames


@pytest.mark.parametrize("damage", ("one_byte", "partial_frame", "full_frame"))
def test_physical_truncation_is_rejected_path_free(
    tmp_path: Path,
    damage: str,
) -> None:
    source = _write_mp3(tmp_path / f"{damage}.mp3", seconds=2.0)
    frames, _xing, _audio_start = _mpeg1_layout(source)
    remove = {
        "one_byte": 1,
        "partial_frame": max(2, frames[-1][1] // 2),
        "full_frame": frames[-1][1],
    }[damage]
    source.write_bytes(source.read_bytes()[:-remove])

    with pytest.raises(ProjectAudioError) as caught:
        ProjectAudioDecoder(source)
    _assert_path_free(caught.value, source)


@pytest.mark.parametrize(
    ("field", "delta"),
    (("frames", -1), ("frames", 1), ("bytes", -1), ("bytes", 1)),
)
def test_xing_inventory_under_and_overcounts_are_rejected(
    tmp_path: Path,
    field: str,
    delta: int,
) -> None:
    source = _write_mp3(tmp_path / f"{field}-{delta}.mp3", seconds=2.0)
    data = bytearray(source.read_bytes())
    _frames, xing_offset, _audio_start = _mpeg1_layout(source)
    assert data[xing_offset : xing_offset + 4] in (b"Xing", b"Info")
    flags = int.from_bytes(data[xing_offset + 4 : xing_offset + 8], "big")
    assert flags & 0x3 == 0x3
    field_offset = xing_offset + (8 if field == "frames" else 12)
    value = int.from_bytes(data[field_offset : field_offset + 4], "big")
    data[field_offset : field_offset + 4] = (value + delta).to_bytes(4, "big")
    source.write_bytes(data)

    with pytest.raises(ProjectAudioError) as caught:
        ProjectAudioDecoder(source)
    _assert_path_free(caught.value, source)


@pytest.mark.parametrize(
    "declarations",
    (
        (("iTunSMPB", "00000000"),),
        (
            ("iTunSMPB", "00000000"),
            ("iTunSMPB", "00000000"),
        ),
        (("iTunSMPB", "00000000\n00000000"),),
    ),
)
def test_structural_itunsmpb_malformed_duplicate_and_injected_text_fail(
    tmp_path: Path,
    declarations: tuple[tuple[str, str], ...],
) -> None:
    source = _write_mp3(tmp_path / "bad-itun.mp3")
    _prepend_id3v22_comments(source, declarations)

    with pytest.raises(ProjectAudioError) as caught:
        ProjectAudioDecoder(source)
    _assert_path_free(caught.value, source)


def test_structural_itunsmpb_conflict_with_lame_fails(
    tmp_path: Path,
) -> None:
    source = _write_mp3(tmp_path / "conflict.mp3")
    scanned = _scan(source)
    assert scanned.gapless is not None
    conflicting = Mp3GaplessDeclaration(
        delay_frames=scanned.gapless.delay_frames + 1,
        content_frames=scanned.gapless.content_frames - 1,
        padding_frames=scanned.gapless.padding_frames,
    )
    _prepend_id3v22_comments(
        source,
        (("iTunSMPB", _itunsmpb_value(conflicting)),),
    )

    with pytest.raises(ProjectAudioError) as caught:
        ProjectAudioDecoder(source)
    _assert_path_free(caught.value, source)


@pytest.mark.parametrize("conflicting", (False, True))
def test_id3v22_txx_itunsmpb_is_structural_and_conflict_checked(
    tmp_path: Path,
    conflicting: bool,
) -> None:
    source = _write_mp3(tmp_path / f"txx-{conflicting}.mp3")
    scanned = _scan(source)
    assert scanned.gapless is not None
    declaration = scanned.gapless
    if conflicting:
        declaration = Mp3GaplessDeclaration(
            delay_frames=declaration.delay_frames + 1,
            content_frames=declaration.content_frames - 1,
            padding_frames=declaration.padding_frames,
        )
    _prepend_id3v22_user_text(source, _itunsmpb_value(declaration))

    if conflicting:
        with pytest.raises(ProjectAudioError) as caught:
            ProjectAudioDecoder(source)
        _assert_path_free(caught.value, source)
    else:
        with ProjectAudioDecoder(source) as decoder:
            assert decoder.probe.source_frames == declaration.content_frames


class _VirtualReader:
    def __init__(
        self,
        boundary: int,
        *,
        nonexact_at: int | None = None,
        nonexact_reset: bool = False,
    ) -> None:
        self.boundary = boundary
        self.position = 0
        self.nonexact_at = nonexact_at
        self.nonexact_reset = nonexact_reset

    def seek(self, position: int) -> int:
        self.position = int(position)
        if self.nonexact_reset and position == 0:
            return 1
        if self.nonexact_at == position:
            return position + 1
        return position

    def read(self, *, out: np.ndarray) -> np.ndarray:
        amount = min(len(out), max(0, self.boundary - self.position))
        out[:amount].fill(0.25)
        self.position += amount
        return out[:amount]


def _synthetic_scan(
    *,
    raw_frames: int,
    gapless: Mp3GaplessDeclaration | None,
) -> Mp3Scan:
    return Mp3Scan(
        source_sample_rate=48_000,
        channels=2,
        samples_per_frame=1_152,
        physical_frames=raw_frames // 1_152,
        audio_bytes=1_000,
        raw_frames=raw_frames,
        gapless=gapless,
    )


def test_reconciliation_accepts_trimmed_and_bounded_raw_decoder_models() -> None:
    gapless = Mp3GaplessDeclaration(528, 55_296, 1_776)
    scan = _synthetic_scan(raw_frames=57_600, gapless=gapless)

    assert project_audio._reconcile_mp3_source_frames(
        _VirtualReader(gapless.content_frames),
        gapless.content_frames,
        2,
        scan,
    ) == (0, gapless.content_frames)
    assert project_audio._reconcile_mp3_source_frames(
        _VirtualReader(scan.raw_frames),
        scan.raw_frames + MP3_MAX_TRAILING_REPORT_FRAMES,
        2,
        scan,
    ) == (gapless.delay_frames, gapless.content_frames)


@pytest.mark.parametrize("failure", ("underreport", "overbound", "seek", "reset"))
def test_reconciliation_fails_closed_on_duration_or_seek_disagreement(
    failure: str,
) -> None:
    scan = _synthetic_scan(raw_frames=57_600, gapless=None)
    report = scan.raw_frames
    reader = _VirtualReader(scan.raw_frames)
    if failure == "underreport":
        report -= 1
    elif failure == "overbound":
        report += MP3_MAX_TRAILING_REPORT_FRAMES + 1
    elif failure == "seek":
        reader.nonexact_at = scan.raw_frames - 576
    else:
        reader.nonexact_reset = True

    with pytest.raises(Mp3ScanError):
        project_audio._reconcile_mp3_source_frames(
            reader,
            report,
            2,
            scan,
        )


def test_scan_rejects_descriptor_identity_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _write_mp3(tmp_path / "identity.mp3")
    descriptor = os.open(
        source,
        os.O_RDONLY | getattr(os, "O_BINARY", 0),
    )
    real_fstat = os.fstat
    first = real_fstat(descriptor)
    calls = 0

    def changing_fstat(fd: int) -> os.stat_result:
        nonlocal calls
        info = real_fstat(fd)
        calls += 1
        if calls < 2:
            return info
        values = list(info)
        values[8] = info.st_mtime + 1
        return os.stat_result(values)

    monkeypatch.setattr(mp3_scan.os, "fstat", changing_fstat)
    try:
        with pytest.raises(Mp3ScanError):
            scan_mp3_descriptor(
                descriptor,
                expected_identity=_identity(first),
                max_source_frames=project_audio.PROJECT_AUDIO_MAX_SOURCE_FRAMES,
                max_duration_seconds=(
                    project_audio.PROJECT_AUDIO_MAX_DURATION_SECONDS
                ),
                max_file_bytes=project_audio.PROJECT_AUDIO_MAX_MP3_FILE_BYTES,
            )
    finally:
        os.close(descriptor)


def test_scanner_fallback_restores_binary_descriptor_cursor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _write_mp3(tmp_path / "fallback-cursor.mp3")
    descriptor = os.open(
        source,
        os.O_RDONLY | getattr(os, "O_BINARY", 0),
    )
    monkeypatch.setattr(mp3_scan.os, "pread", None, raising=False)
    try:
        before = os.fstat(descriptor)
        scanned = scan_mp3_descriptor(
            descriptor,
            expected_identity=_identity(before),
            max_source_frames=project_audio.PROJECT_AUDIO_MAX_SOURCE_FRAMES,
            max_duration_seconds=(
                project_audio.PROJECT_AUDIO_MAX_DURATION_SECONDS
            ),
            max_file_bytes=project_audio.PROJECT_AUDIO_MAX_MP3_FILE_BYTES,
        )
        assert scanned.raw_frames > 0
        assert os.lseek(descriptor, 0, os.SEEK_CUR) == 0
    finally:
        os.close(descriptor)


def test_ordinary_decode_rejects_nonexact_seek_and_zeroes_output(
    tmp_path: Path,
) -> None:
    source = _write_mp3(tmp_path / "nonexact-read.mp3")
    decoder = ProjectAudioDecoder(source)
    real_seek = decoder._reader.seek  # type: ignore[attr-defined]
    output = np.full((32, 2), 1.0, dtype=np.float32)

    def nonexact(position: int) -> int:
        real_seek(position)
        return position + 1

    decoder._reader.seek = nonexact  # type: ignore[attr-defined,method-assign]
    try:
        with pytest.raises(ProjectAudioError) as caught:
            decoder.read_into(0, output)
        _assert_path_free(caught.value, source)
        assert np.count_nonzero(output) == 0
    finally:
        decoder.close()


def _patch_first_tag(path: Path, replacement: bytes) -> None:
    data = path.read_bytes()
    index = data.find(b"LAME")
    assert index > 0, "expected a LAME extension in the written file"
    path.write_bytes(data[:index] + replacement + data[index + 4 :])


@pytest.mark.parametrize("writer_tag", (b"Lavc", b"Lavf"))
def test_lavc_and_lavf_tagged_gapless_extensions_match_lame(
    tmp_path: Path,
    writer_tag: bytes,
) -> None:
    source = _write_mp3(tmp_path / "writer-tag.mp3")
    lame = _scan(source)
    assert lame.gapless is not None

    _patch_first_tag(source, writer_tag)
    patched = _scan(source)

    assert patched.gapless == lame.gapless
    assert patched.raw_frames == lame.raw_frames
    assert patched.physical_frames == lame.physical_frames

    # End to end: the runtime decoder honors the same writers, so the
    # decoder-side reconciliation must agree with the scan.
    decoder = ProjectAudioDecoder(source)
    try:
        assert decoder.output_frames > 0
    finally:
        decoder.close()


def test_unknown_writer_tagged_extension_is_not_parsed_as_gapless(
    tmp_path: Path,
) -> None:
    source = _write_mp3(tmp_path / "unknown-tag.mp3")
    _patch_first_tag(source, b"XXXX")
    assert _scan(source).gapless is None


def _ape_tag(*, version: int = 2000, with_header: bool = True) -> bytes:
    key = b"MP3GAIN_MINMAX"
    value = b"128,128"
    item = (
        len(value).to_bytes(4, "little")
        + (0).to_bytes(4, "little")
        + key
        + b"\0"
        + value
    )
    tag_size = 32 + len(item)

    def block(flags: int) -> bytes:
        return (
            b"APETAGEX"
            + version.to_bytes(4, "little")
            + tag_size.to_bytes(4, "little")
            + (1).to_bytes(4, "little")
            + flags.to_bytes(4, "little")
            + b"\0" * 8
        )

    if with_header:
        return block(0xA000_0000) + item + block(0x8000_0000)
    return item + block(0)


@pytest.mark.parametrize(
    ("tag", "trailer"),
    (
        ("apev2", b""),
        ("apev2", b"TAG" + b"\0" * 125),
        ("apev1", b""),
    ),
)
def test_trailing_ape_tags_are_excluded_from_the_frame_walk(
    tmp_path: Path,
    tag: str,
    trailer: bytes,
) -> None:
    source = _write_mp3(tmp_path / f"{tag}.mp3")
    before = _scan(source)
    ape = _ape_tag(
        version=2000 if tag == "apev2" else 1000,
        with_header=tag == "apev2",
    )
    source.write_bytes(source.read_bytes() + ape + trailer)

    after = _scan(source)
    assert after.physical_frames == before.physical_frames
    assert after.audio_bytes == before.audio_bytes
    assert after.raw_frames == before.raw_frames
    assert after.gapless == before.gapless

    decoder = ProjectAudioDecoder(source)
    try:
        assert decoder.output_frames > 0
    finally:
        decoder.close()


@pytest.mark.parametrize("damage", ("version", "undersize", "overrun", "header"))
def test_malformed_trailing_ape_tags_are_rejected_path_free(
    tmp_path: Path,
    damage: str,
) -> None:
    source = _write_mp3(tmp_path / f"ape-{damage}.mp3")
    ape = bytearray(_ape_tag())
    if damage == "version":
        ape[-24:-20] = (3000).to_bytes(4, "little")
    elif damage == "undersize":
        ape[-20:-16] = (16).to_bytes(4, "little")
    elif damage == "overrun":
        ape[-20:-16] = (2**24).to_bytes(4, "little")
    elif damage == "header":
        ape[0:8] = b"APETAGEY"
    source.write_bytes(source.read_bytes() + bytes(ape))

    with pytest.raises(ProjectAudioError) as caught:
        ProjectAudioDecoder(source)
    _assert_path_free(caught.value, source)


def test_trailing_garbage_is_still_rejected(tmp_path: Path) -> None:
    source = _write_mp3(tmp_path / "garbage.mp3")
    source.write_bytes(source.read_bytes() + b"\0" * 16)

    with pytest.raises(ProjectAudioError) as caught:
        ProjectAudioDecoder(source)
    _assert_path_free(caught.value, source)


def test_mp3_rejections_carry_the_bounded_scan_reason(tmp_path: Path) -> None:
    source = _write_mp3(tmp_path / "truncated.mp3", seconds=2.0)
    source.write_bytes(source.read_bytes()[:-1])

    with pytest.raises(ProjectAudioError) as caught:
        ProjectAudioDecoder(source)
    message = str(caught.value)
    assert "couldn't validate that MP3" in message
    assert "(" in message and ")" in message
    _assert_path_free(caught.value, source)
