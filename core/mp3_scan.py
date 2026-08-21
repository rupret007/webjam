"""Descriptor-bound structural validation for MPEG Layer III project audio."""

from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass
from typing import Final

MP3_MAX_ID3_TAG_BYTES: Final = 4 * 1_024 * 1_024
MP3_MAX_ID3_TEXT_BYTES: Final = 4_096
MP3_MAX_TRAILING_REPORT_FRAMES: Final = 4 * 1_152

_MPEG1_BITRATES: Final[tuple[int, ...]] = (
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
)
_MPEG2_BITRATES: Final[tuple[int, ...]] = (
    0,
    8,
    16,
    24,
    32,
    40,
    48,
    56,
    64,
    80,
    96,
    112,
    128,
    144,
    160,
    0,
)
_BASE_SAMPLE_RATES: Final[tuple[int, ...]] = (44_100, 48_000, 32_000)
_ITUNSMPB_VALUE: Final[re.Pattern[str]] = re.compile(
    r"[0-9a-f]{8,16}(?:[ \t]+[0-9a-f]{8,16}){11}[ \t]*",
    re.IGNORECASE,
)


class Mp3ScanError(RuntimeError):
    """Path-free structural MP3 validation failure."""


@dataclass(frozen=True, slots=True)
class Mp3GaplessDeclaration:
    delay_frames: int
    content_frames: int
    padding_frames: int

    @property
    def raw_frames(self) -> int:
        return self.delay_frames + self.content_frames + self.padding_frames


@dataclass(frozen=True, slots=True)
class Mp3Scan:
    source_sample_rate: int
    channels: int
    samples_per_frame: int
    physical_frames: int
    audio_bytes: int
    raw_frames: int
    gapless: Mp3GaplessDeclaration | None
    xing_kind: str = ""


@dataclass(frozen=True, slots=True)
class _FrameHeader:
    version: int
    sample_rate: int
    channels: int
    samples_per_frame: int
    frame_bytes: int
    channel_mode: int


@dataclass(frozen=True, slots=True)
class _XingInfo:
    kind: str
    frames: int
    audio_bytes: int
    gapless: Mp3GaplessDeclaration | None


class _DescriptorView:
    """Small descriptor-relative reads without sharing a mutable file cursor."""

    def __init__(self, descriptor: int, size: int, initial_offset: int) -> None:
        self._descriptor = descriptor
        self._size = size
        self._initial_offset = initial_offset
        self._pread = getattr(os, "pread", None)

    def __getitem__(self, key: int | slice) -> int | bytes:
        if isinstance(key, int):
            if key < 0:
                key += self._size
            return self._read_exact(key, 1)[0]
        if key.step not in (None, 1):
            raise ValueError("descriptor slices must be contiguous")
        start = 0 if key.start is None else int(key.start)
        stop = self._size if key.stop is None else int(key.stop)
        if start < 0:
            start += self._size
        if stop < 0:
            stop += self._size
        if not 0 <= start <= stop <= self._size:
            raise Mp3ScanError("MP3 descriptor read is out of range.")
        return self._read_exact(start, stop - start)

    def _read_exact(self, offset: int, count: int) -> bytes:
        if not 0 <= offset <= self._size or count < 0 or offset + count > self._size:
            raise Mp3ScanError("MP3 descriptor read is out of range.")
        chunks: list[bytes] = []
        cursor = offset
        remaining = count
        while remaining:
            try:
                if self._pread is not None:
                    chunk = self._pread(self._descriptor, remaining, cursor)
                else:
                    landed = os.lseek(self._descriptor, cursor, os.SEEK_SET)
                    if landed != cursor:
                        raise OSError("descriptor seek was not exact")
                    chunk = os.read(self._descriptor, remaining)
            except OSError:
                raise Mp3ScanError("MP3 descriptor read failed.") from None
            if not chunk:
                raise Mp3ScanError("MP3 descriptor changed during validation.")
            chunks.append(chunk)
            cursor += len(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def close(self) -> None:
        if self._pread is not None:
            return
        try:
            landed = os.lseek(
                self._descriptor,
                self._initial_offset,
                os.SEEK_SET,
            )
        except OSError:
            raise Mp3ScanError("MP3 descriptor could not reset.") from None
        if landed != self._initial_offset:
            raise Mp3ScanError("MP3 descriptor reset was not exact.")


def _identity(info: os.stat_result) -> tuple[int, int, int, int, int]:
    mtime_ns = getattr(info, "st_mtime_ns", None)
    ctime_ns = getattr(info, "st_ctime_ns", None)
    return (
        int(info.st_dev),
        int(info.st_ino),
        int(info.st_size),
        int(
            round(info.st_mtime * 1_000_000_000)
            if mtime_ns is None
            else mtime_ns
        ),
        int(
            round(info.st_ctime * 1_000_000_000)
            if ctime_ns is None
            else ctime_ns
        ),
    )


def _synchsafe_u32(value: bytes) -> int:
    if len(value) != 4 or any(byte & 0x80 for byte in value):
        raise Mp3ScanError("MP3 metadata is malformed.")
    return (
        (value[0] << 21)
        | (value[1] << 14)
        | (value[2] << 7)
        | value[3]
    )


def _split_id3_text(
    payload: bytes,
    *,
    comment: bool,
) -> tuple[str, str]:
    minimum = 4 if comment else 1
    if len(payload) < minimum:
        raise Mp3ScanError("MP3 metadata is malformed.")
    encoding = payload[0]
    encoded = payload[4:] if comment else payload[1:]
    if encoding in (0, 3):
        terminator = encoded.find(b"\0")
        if terminator < 0:
            raise Mp3ScanError("MP3 metadata is malformed.")
        description_bytes = encoded[:terminator]
        value_bytes = encoded[terminator + 1 :]
        description_codec = "latin-1" if encoding == 0 else "utf-8"
        value_codec = description_codec
    elif encoding in (1, 2):
        if len(encoded) % 2:
            raise Mp3ScanError("MP3 metadata is malformed.")
        terminator = -1
        for offset in range(0, len(encoded) - 1, 2):
            if encoded[offset : offset + 2] == b"\0\0":
                terminator = offset
                break
        if terminator < 0:
            raise Mp3ScanError("MP3 metadata is malformed.")
        description_bytes = encoded[:terminator]
        value_bytes = encoded[terminator + 2 :]
        if encoding == 1:
            if description_bytes.startswith(b"\xff\xfe"):
                inherited_codec = "utf-16-le"
            elif description_bytes.startswith(b"\xfe\xff"):
                inherited_codec = "utf-16-be"
            else:
                raise Mp3ScanError("MP3 metadata is malformed.")
            description_codec = "utf-16"
            value_codec = (
                "utf-16"
                if value_bytes.startswith((b"\xff\xfe", b"\xfe\xff"))
                else inherited_codec
            )
        else:
            description_codec = "utf-16-be"
            value_codec = "utf-16-be"
    else:
        raise Mp3ScanError("MP3 metadata is malformed.")
    try:
        description = description_bytes.decode(
            description_codec,
            errors="strict",
        )
        value = value_bytes.decode(value_codec, errors="strict")
    except UnicodeError:
        raise Mp3ScanError("MP3 metadata is malformed.") from None
    description = description.strip(" \t")
    value = value.rstrip("\0").strip(" \t")
    if (
        len(description) > 128
        or len(value) > MP3_MAX_ID3_TEXT_BYTES
    ):
        raise Mp3ScanError("MP3 metadata is malformed.")
    return description, value


def _parse_itunsmpb(value: str) -> Mp3GaplessDeclaration:
    if _ITUNSMPB_VALUE.fullmatch(value) is None:
        raise Mp3ScanError("MP3 gapless metadata is malformed.")
    fields = tuple(int(token, 16) for token in value.split())
    if len(fields) != 12:
        raise Mp3ScanError("MP3 gapless metadata is malformed.")
    return Mp3GaplessDeclaration(
        delay_frames=fields[1],
        padding_frames=fields[2],
        content_frames=fields[3],
    )


def _id3v2_prefix(
    data: _DescriptorView,
    file_size: int,
) -> tuple[int, Mp3GaplessDeclaration | None]:
    if file_size < 10 or data[:3] != b"ID3":
        return 0, None
    header = data[:10]
    major = int(header[3])
    revision = int(header[4])
    flags = int(header[5])
    if major not in (2, 3, 4) or revision == 0xFF:
        raise Mp3ScanError("MP3 metadata version is unsupported.")
    footer = major == 4 and bool(flags & 0x10)
    allowed_flags = 0x10 if major == 4 else 0
    if flags & ~allowed_flags:
        raise Mp3ScanError("MP3 transformed metadata is unsupported.")
    tag_size = _synchsafe_u32(header[6:10])
    if tag_size < 1 or tag_size > MP3_MAX_ID3_TAG_BYTES:
        raise Mp3ScanError("MP3 metadata size is out of range.")
    body_start = 10
    body_end = body_start + tag_size
    prefix_end = body_end + (10 if footer else 0)
    if prefix_end > file_size:
        raise Mp3ScanError("MP3 metadata ended unexpectedly.")
    if footer:
        footer_data = data[body_end:prefix_end]
        if (
            footer_data[:3] != b"3DI"
            or footer_data[3] != major
            or footer_data[4] != revision
            or footer_data[5] != flags
            or footer_data[6:10] != header[6:10]
        ):
            raise Mp3ScanError("MP3 metadata footer is malformed.")

    declarations: list[Mp3GaplessDeclaration] = []
    offset = body_start
    frame_header_bytes = 6 if major == 2 else 10
    while offset < body_end:
        if data[offset] == 0:
            if any(data[offset:body_end]):
                raise Mp3ScanError("MP3 metadata padding is malformed.")
            break
        if body_end - offset < frame_header_bytes:
            raise Mp3ScanError("MP3 metadata frame ended unexpectedly.")
        if major == 2:
            frame_id = data[offset : offset + 3]
            frame_size = int.from_bytes(data[offset + 3 : offset + 6], "big")
            frame_flags = b""
        else:
            frame_id = data[offset : offset + 4]
            size_bytes = data[offset + 4 : offset + 8]
            frame_size = (
                _synchsafe_u32(size_bytes)
                if major == 4
                else int.from_bytes(size_bytes, "big")
            )
            frame_flags = data[offset + 8 : offset + 10]
        if (
            not all(
                48 <= byte <= 57 or 65 <= byte <= 90 for byte in frame_id
            )
            or frame_size < 1
            or frame_size > MP3_MAX_ID3_TAG_BYTES
            or any(frame_flags)
        ):
            raise Mp3ScanError("MP3 metadata frame is malformed.")
        payload_start = offset + frame_header_bytes
        payload_end = payload_start + frame_size
        if payload_end > body_end:
            raise Mp3ScanError("MP3 metadata frame ended unexpectedly.")
        is_comment = frame_id in (b"COM", b"COMM")
        is_user_text = frame_id in (b"TXX", b"TXXX")
        if is_comment or is_user_text:
            if frame_size > MP3_MAX_ID3_TEXT_BYTES:
                raise Mp3ScanError("MP3 text metadata is too large.")
            payload = data[payload_start:payload_end]
            description, value = _split_id3_text(
                payload,
                comment=is_comment,
            )
            if description.casefold() == "itunsmpb":
                declarations.append(_parse_itunsmpb(value))
        offset = payload_end
    if len(declarations) > 1:
        raise Mp3ScanError("MP3 gapless metadata is duplicated.")
    return prefix_end, declarations[0] if declarations else None


def _frame_header(value: int) -> _FrameHeader:
    if value >> 21 != 0x7FF:
        raise Mp3ScanError("MP3 frame sync is invalid.")
    version_bits = (value >> 19) & 0x3
    layer_bits = (value >> 17) & 0x3
    bitrate_index = (value >> 12) & 0xF
    sample_rate_index = (value >> 10) & 0x3
    padding = (value >> 9) & 0x1
    channel_mode = (value >> 6) & 0x3
    emphasis = value & 0x3
    if (
        version_bits == 1
        or layer_bits != 1
        or bitrate_index in (0, 15)
        or sample_rate_index == 3
        or emphasis == 2
    ):
        raise Mp3ScanError("MP3 frame header is unsupported.")
    if version_bits == 3:
        version = 1
        sample_rate = _BASE_SAMPLE_RATES[sample_rate_index]
        bitrate = _MPEG1_BITRATES[bitrate_index]
        samples_per_frame = 1_152
        frame_bytes = (144_000 * bitrate) // sample_rate + padding
    else:
        version = 2 if version_bits == 2 else 25
        divisor = 2 if version_bits == 2 else 4
        sample_rate = _BASE_SAMPLE_RATES[sample_rate_index] // divisor
        bitrate = _MPEG2_BITRATES[bitrate_index]
        samples_per_frame = 576
        frame_bytes = (72_000 * bitrate) // sample_rate + padding
    if frame_bytes < 4:
        raise Mp3ScanError("MP3 frame length is invalid.")
    return _FrameHeader(
        version=version,
        sample_rate=sample_rate,
        channels=1 if channel_mode == 3 else 2,
        samples_per_frame=samples_per_frame,
        frame_bytes=frame_bytes,
        channel_mode=channel_mode,
    )


def _first_frame_xing(
    data: _DescriptorView,
    frame_start: int,
    frame_end: int,
    header: _FrameHeader,
) -> _XingInfo | None:
    if header.version == 1:
        side_info = 17 if header.channel_mode == 3 else 32
    else:
        side_info = 9 if header.channel_mode == 3 else 17
    # LAME places Xing/Info at this fixed side-info boundary even when the
    # MPEG frame carries CRC protection.
    offset = frame_start + 4 + side_info
    if offset + 4 > frame_end:
        return None
    marker = data[offset : offset + 4]
    if marker not in (b"Xing", b"Info"):
        return None
    cursor = offset + 4
    if cursor + 4 > frame_end:
        raise Mp3ScanError("MP3 Xing metadata ended unexpectedly.")
    flags = int.from_bytes(data[cursor : cursor + 4], "big")
    cursor += 4
    if flags & ~0xF or not flags & 0x1 or not flags & 0x2:
        raise Mp3ScanError("MP3 Xing metadata is incomplete.")

    frame_count = -1
    audio_bytes = -1
    for flag, size in ((0x1, 4), (0x2, 4), (0x4, 100), (0x8, 4)):
        if not flags & flag:
            continue
        if cursor + size > frame_end:
            raise Mp3ScanError("MP3 Xing metadata ended unexpectedly.")
        if flag == 0x1:
            frame_count = int.from_bytes(data[cursor : cursor + 4], "big")
        elif flag == 0x2:
            audio_bytes = int.from_bytes(data[cursor : cursor + 4], "big")
        cursor += size
    if frame_count < 1 or audio_bytes < 1:
        raise Mp3ScanError("MP3 Xing metadata is invalid.")

    gapless = None
    # mpg123 (the runtime decoder inside the locked libsndfile build) honors
    # the encoder-delay/padding fields of this extension for both LAME- and
    # Lavc/Lavf-tagged writers (ffmpeg). Parsing the same set keeps this scan
    # and the runtime decoder in agreement about the trimmed content length;
    # the decoder-side reconciliation still cross-checks either outcome.
    if cursor + 4 <= frame_end and data[cursor : cursor + 4] in (
        b"LAME",
        b"Lavc",
        b"Lavf",
    ):
        if cursor + 36 > frame_end:
            raise Mp3ScanError("MP3 LAME metadata ended unexpectedly.")
        delay = (data[cursor + 21] << 4) | (data[cursor + 22] >> 4)
        padding = ((data[cursor + 22] & 0xF) << 8) | data[cursor + 23]
        if delay > 3_000 or padding > 3_000:
            raise Mp3ScanError("MP3 LAME gapless metadata is invalid.")
        raw_frames = frame_count * header.samples_per_frame
        content_frames = raw_frames - delay - padding
        if content_frames < 1:
            raise Mp3ScanError("MP3 LAME gapless metadata is invalid.")
        gapless = Mp3GaplessDeclaration(
            delay_frames=delay,
            padding_frames=padding,
            content_frames=content_frames,
        )
    return _XingInfo(
        kind=marker.decode("ascii"),
        frames=frame_count,
        audio_bytes=audio_bytes,
        gapless=gapless,
    )


_APE_PREAMBLE: Final = b"APETAGEX"
_APE_HAS_HEADER_FLAG: Final = 0x8000_0000


def _without_trailing_ape_tag(
    data: _DescriptorView,
    audio_start: int,
    audio_end: int,
) -> int:
    """Return ``audio_end`` with one trailing APE tag excluded, if present.

    mp3gain and common tag editors append an APEv2 (or legacy APEv1) tag
    directly after the last audio frame, before the optional ID3v1 trailer.
    The runtime decoder ignores that tag, so the physical frame walk must
    exclude it too. The tag is parsed strictly and bounded; a malformed tag
    rejects the file rather than being skipped over.
    """

    if audio_end - audio_start < 32:
        return audio_end
    footer = data[audio_end - 32 : audio_end]
    if footer[:8] != _APE_PREAMBLE:
        return audio_end
    version = int.from_bytes(footer[8:12], "little")
    tag_size = int.from_bytes(footer[12:16], "little")
    flags = int.from_bytes(footer[20:24], "little")
    if version not in (1000, 2000) or tag_size < 32:
        raise Mp3ScanError("MP3 APE tag is invalid.")
    total = tag_size + (32 if flags & _APE_HAS_HEADER_FLAG else 0)
    if total > audio_end - audio_start:
        raise Mp3ScanError("MP3 APE tag is invalid.")
    if flags & _APE_HAS_HEADER_FLAG:
        header = data[audio_end - total : audio_end - total + 8]
        if header != _APE_PREAMBLE:
            raise Mp3ScanError("MP3 APE tag is invalid.")
    return audio_end - total


def scan_mp3_descriptor(
    descriptor: int,
    *,
    expected_identity: tuple[int, int, int, int, int],
    max_source_frames: int,
    max_duration_seconds: int,
    max_file_bytes: int,
) -> Mp3Scan:
    """Scan one immutable descriptor through every physical Layer III frame.

    The caller must exclusively own the descriptor cursor for the duration of
    this call. Platforms without ``pread`` use exact ``lseek``/``read`` pairs
    and restore the original cursor before returning.
    """

    try:
        before = os.fstat(descriptor)
        initial_offset = os.lseek(descriptor, 0, os.SEEK_CUR)
    except OSError:
        raise Mp3ScanError("MP3 descriptor is unavailable.") from None
    if (
        not stat.S_ISREG(before.st_mode)
        or _identity(before) != expected_identity
        or initial_offset != 0
        or not 4 <= before.st_size <= max_file_bytes
    ):
        raise Mp3ScanError("MP3 descriptor identity or size is invalid.")

    view: _DescriptorView | None = None
    result: Mp3Scan | None = None
    try:
        view = _DescriptorView(
            descriptor,
            int(before.st_size),
            initial_offset,
        )
        file_size = int(before.st_size)
        audio_start, id3_gapless = _id3v2_prefix(view, file_size)
        audio_end = file_size
        if (
            audio_end - audio_start >= 128
            and view[audio_end - 128 : audio_end - 125] == b"TAG"
        ):
            audio_end -= 128
        audio_end = _without_trailing_ape_tag(view, audio_start, audio_end)
        if audio_end - audio_start < 4:
            raise Mp3ScanError("MP3 contains no complete audio frame.")

        offset = audio_start
        physical_frames = 0
        first: _FrameHeader | None = None
        xing: _XingInfo | None = None
        while offset < audio_end:
            if audio_end - offset < 4:
                raise Mp3ScanError("MP3 final frame is incomplete.")
            value = int.from_bytes(view[offset : offset + 4], "big")
            header = _frame_header(value)
            frame_end = offset + header.frame_bytes
            if frame_end > audio_end:
                raise Mp3ScanError("MP3 final frame is incomplete.")
            if first is None:
                first = header
                xing = _first_frame_xing(view, offset, frame_end, header)
            elif (
                header.version != first.version
                or header.sample_rate != first.sample_rate
                or header.channels != first.channels
            ):
                raise Mp3ScanError("MP3 frame format changes mid-stream.")
            physical_frames += 1
            audio_frame_count = physical_frames - (1 if xing is not None else 0)
            if (
                max_duration_seconds < 1
                or audio_frame_count * header.samples_per_frame
                > header.sample_rate * max_duration_seconds
            ):
                raise Mp3ScanError("MP3 duration exceeds the safe bound.")
            offset = frame_end
        if offset != audio_end or physical_frames < 1 or first is None:
            raise Mp3ScanError("MP3 frame inventory is incomplete.")

        audio_bytes = audio_end - audio_start
        if xing is not None:
            if (
                xing.frames + 1 != physical_frames
                or xing.audio_bytes != audio_bytes
            ):
                raise Mp3ScanError("MP3 Xing inventory does not match the file.")
            raw_frames = xing.frames * first.samples_per_frame
        else:
            raw_frames = physical_frames * first.samples_per_frame
        if not 1 <= raw_frames <= max_source_frames:
            raise Mp3ScanError("MP3 decoded duration is out of range.")
        if raw_frames > first.sample_rate * max_duration_seconds:
            raise Mp3ScanError("MP3 decoded duration is out of range.")

        gapless = xing.gapless if xing is not None else None
        if id3_gapless is not None:
            if (
                id3_gapless.content_frames < 1
                or id3_gapless.delay_frames > 3_000
                or id3_gapless.padding_frames > 3_000
            ):
                raise Mp3ScanError("MP3 gapless metadata is out of range.")
            if id3_gapless.raw_frames != raw_frames:
                raise Mp3ScanError(
                    "MP3 gapless metadata does not match the frame inventory."
                )
            if gapless is not None and gapless != id3_gapless:
                raise Mp3ScanError("MP3 gapless metadata is conflicting.")
            gapless = id3_gapless
        if gapless is not None and gapless.raw_frames != raw_frames:
            raise Mp3ScanError(
                "MP3 gapless metadata does not match the frame inventory."
            )
        result = Mp3Scan(
            source_sample_rate=first.sample_rate,
            channels=first.channels,
            samples_per_frame=first.samples_per_frame,
            physical_frames=physical_frames,
            audio_bytes=audio_bytes,
            raw_frames=raw_frames,
            gapless=gapless,
            xing_kind=xing.kind if xing is not None else "",
        )
    except (OSError, ValueError):
        raise Mp3ScanError("MP3 structural validation failed.") from None
    finally:
        if view is not None:
            view.close()
        try:
            after = os.fstat(descriptor)
            final_offset = os.lseek(descriptor, 0, os.SEEK_CUR)
        except OSError:
            raise Mp3ScanError(
                "MP3 descriptor changed during validation."
            ) from None
        if (
            _identity(after) != expected_identity
            or final_offset != initial_offset
        ):
            raise Mp3ScanError("MP3 descriptor changed during validation.")
    if result is None:
        raise Mp3ScanError("MP3 structural validation failed.")
    return result


__all__ = [
    "MP3_MAX_TRAILING_REPORT_FRAMES",
    "Mp3GaplessDeclaration",
    "Mp3Scan",
    "Mp3ScanError",
    "scan_mp3_descriptor",
]
