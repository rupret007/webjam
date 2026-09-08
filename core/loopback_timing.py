"""Offline sample-delay evidence from an explicitly selected calibration capture.

This module never records, plays audio, opens a device, or contacts a network.
Both selected channels must come from one unedited ADC timebase. That wiring
is an operator assertion; neither a WAV header nor this analysis verifies it.
"""

from __future__ import annotations

import hashlib
import math
import os
from pathlib import Path
import stat
import struct
import time

import numpy as np
import soundfile as sf


_MAX_BYTES = 256 * 1024 * 1024
_MAX_SECONDS = 180
_BLOCK_FRAMES = 65_536
_MAX_EVENTS = 512
_MAX_ANALYSIS_SECONDS = 30
_ERRORS = {
    "invalid_configuration": "Choose two distinct 1-based channels and a positive maximum delay of at most 1000 ms.",
    "same_clock_required": "Confirm that both channels were captured together by one unedited recording timebase.",
    "invalid_file": "Choose one complete, regular PCM WAV file without a symbolic link.",
    "unsupported_audio": "Use a 2–8 channel PCM16, PCM24, or PCM32 WAV at 44100, 48000, or 96000 Hz.",
    "file_too_large": "The calibration capture must be at most 256 MiB.",
    "capture_too_long": "The calibration capture must be at most 180 seconds.",
    "too_many_events": "Use at most 512 isolated calibration bursts per channel.",
    "file_changed": "The selected capture changed during analysis. Choose a stable copy and try again.",
    "analysis_limit": "The calibration capture exceeded the bounded analysis budget.",
}


class LoopbackTimingError(Exception):
    """Fixed, path-free error safe to show or include in a local report."""

    def __init__(self, code: str) -> None:
        self.code = code if code in _ERRORS else "invalid_file"
        self.message = _ERRORS[self.code]
        super().__init__(self.message)


def _budget(deadline: float) -> None:
    if time.monotonic() > deadline:
        raise LoopbackTimingError("analysis_limit")


def _identity(info: os.stat_result) -> tuple[int, ...]:
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _validate_riff(fd: int, size: int, deadline: float) -> tuple[int, int, int]:
    """Check declared chunk bounds; decoders may otherwise accept truncation."""

    os.lseek(fd, 0, os.SEEK_SET)
    header = os.read(fd, 12)
    if len(header) != 12 or header[:4] != b"RIFF" or header[8:] != b"WAVE":
        raise LoopbackTimingError("unsupported_audio")
    end = struct.unpack("<I", header[4:8])[0] + 8
    if end != size:
        raise LoopbackTimingError("invalid_file")
    position, chunks = 12, 0
    format_data = None
    format_length = 0
    data_size = None
    while position < end:
        _budget(deadline)
        chunks += 1
        if chunks > 4096:
            raise LoopbackTimingError("analysis_limit")
        if position + 8 > end:
            raise LoopbackTimingError("invalid_file")
        os.lseek(fd, position, os.SEEK_SET)
        chunk_header = os.read(fd, 8)
        if len(chunk_header) != 8:
            raise LoopbackTimingError("invalid_file")
        kind, length = chunk_header[:4], struct.unpack("<I", chunk_header[4:])[0]
        following = position + 8 + length + length % 2
        if following > end:
            raise LoopbackTimingError("invalid_file")
        if kind == b"fmt ":
            if format_data is not None or length < 16:
                raise LoopbackTimingError("invalid_file")
            format_length = length
            format_data = os.read(fd, min(length, 40))
        elif kind == b"data":
            if data_size is not None:
                raise LoopbackTimingError("unsupported_audio")
            data_size = length
        position = following
    if format_data is None or len(format_data) < 16 or not data_size:
        raise LoopbackTimingError("invalid_file")
    encoding, channels, rate, byte_rate, alignment, bits = struct.unpack("<HHIIHH", format_data[:16])
    if encoding == 0xFFFE:
        # WAVE_FORMAT_EXTENSIBLE may carry PCM, but never silently accept float.
        pcm_guid = bytes.fromhex("0100000000001000800000aa00389b71")
        if (len(format_data) < 40 or format_data[24:40] != pcm_guid
                or struct.unpack("<H", format_data[16:18])[0] < 22
                or 18 + struct.unpack("<H", format_data[16:18])[0] > format_length
                or struct.unpack("<H", format_data[18:20])[0] != bits):
            raise LoopbackTimingError("unsupported_audio")
    elif encoding != 1:
        raise LoopbackTimingError("unsupported_audio")
    if channels not in range(2, 9) or rate not in {44_100, 48_000, 96_000} or bits not in {16, 24, 32}:
        raise LoopbackTimingError("unsupported_audio")
    if alignment != channels * (bits // 8) or byte_rate != rate * alignment or data_size % alignment:
        raise LoopbackTimingError("invalid_file")
    return channels, rate, data_size // alignment


def _read_capture(path: object, channels: tuple[int, int], deadline: float):
    try:
        selected = Path(path)
        before = selected.lstat()
        if not stat.S_ISREG(before.st_mode):
            raise LoopbackTimingError("invalid_file")
        if before.st_size > _MAX_BYTES:
            raise LoopbackTimingError("file_too_large")
        flags = (os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                 | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
                 | getattr(os, "O_CLOEXEC", 0))
        fd = os.open(selected, flags)
    except LoopbackTimingError:
        raise
    except (TypeError, ValueError, OSError):
        raise LoopbackTimingError("invalid_file") from None
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode) or _identity(opened) != _identity(before):
            raise LoopbackTimingError("file_changed")
        count, rate, frames = _validate_riff(fd, opened.st_size, deadline)
        if max(channels) > count:
            raise LoopbackTimingError("invalid_configuration")
        if frames / rate > _MAX_SECONDS:
            raise LoopbackTimingError("capture_too_long")
        bin_frames = round(rate / 1000)
        envelopes = []
        remainder = np.empty((0, 2), dtype=np.float64)
        clipped = False
        os.lseek(fd, 0, os.SEEK_SET)
        with sf.SoundFile(fd, mode="r", closefd=False) as reader:
            if (reader.format not in {"WAV", "WAVEX"}
                    or reader.subtype not in {"PCM_16", "PCM_24", "PCM_32"}
                    or (reader.channels, reader.samplerate, reader.frames) != (count, rate, frames)):
                raise LoopbackTimingError("invalid_file")
            subtype = reader.subtype
            bits = int(subtype.split("_")[1])
            full_scale = 1.0 - 2.0 ** (1 - bits)
            remaining = frames
            while remaining:
                _budget(deadline)
                expected = min(remaining, _BLOCK_FRAMES)
                block = reader.read(expected, dtype="float64", always_2d=True)
                if len(block) != expected:
                    raise LoopbackTimingError("invalid_file")
                selected_samples = block[:, [channels[0] - 1, channels[1] - 1]]
                if not np.isfinite(selected_samples).all():
                    raise LoopbackTimingError("invalid_file")
                clipped = clipped or bool(np.any(np.abs(selected_samples) >= full_scale))
                samples = np.concatenate((remainder, selected_samples), axis=0)
                usable = len(samples) // bin_frames * bin_frames
                if usable:
                    grouped = samples[:usable].reshape(-1, bin_frames, 2)
                    envelopes.append(np.sqrt(np.mean(grouped * grouped, axis=1)))
                remainder = samples[usable:].copy()
                remaining -= expected
            if len(remainder):
                envelopes.append(np.sqrt(np.mean(remainder * remainder, axis=0))[None, :])
        digest = hashlib.sha256()
        os.lseek(fd, 0, os.SEEK_SET)
        remaining_bytes = opened.st_size
        while remaining_bytes:
            _budget(deadline)
            expected = min(remaining_bytes, 1024 * 1024)
            block = os.read(fd, expected)
            if len(block) != expected:
                raise LoopbackTimingError("file_changed")
            digest.update(block)
            remaining_bytes -= expected
        if os.read(fd, 1):
            raise LoopbackTimingError("file_changed")
        try:
            stable = _identity(os.fstat(fd)) == _identity(opened) == _identity(selected.lstat())
        except OSError:
            stable = False
        if not stable:
            raise LoopbackTimingError("file_changed")
        return np.concatenate(envelopes, axis=0), bin_frames, clipped, {
            "sha256": digest.hexdigest(), "sample_rate": rate,
            "frames": frames, "channels": count, "subtype": subtype,
        }
    except LoopbackTimingError:
        raise
    except (OSError, RuntimeError, ValueError, OverflowError):
        raise LoopbackTimingError("invalid_file") from None
    finally:
        os.close(fd)


def _events(envelope: np.ndarray, resolution_ms: float):
    """Find sparse bursts only when silence/noise contrast supports detection."""

    peak = float(np.max(envelope, initial=0.0))
    noise = float(np.median(envelope))
    if peak < 0.0001:
        return [], ["silence_or_low_signal"], np.zeros(len(envelope), dtype=bool)
    if peak < max(noise * 6, 0.0001):
        return [], ["low_signal_to_noise"], np.zeros(len(envelope), dtype=bool)
    active = envelope >= max(noise * 4, peak * 0.08, 0.00001)
    edges = np.diff(np.concatenate(([False], active, [False])).astype(np.int8))
    starts, ends = np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)
    events = []
    for start, end in zip(starts, ends):
        if events and (int(start) - events[-1][1]) * resolution_ms <= 2:
            events[-1] = (events[-1][0], int(end))
        else:
            events.append((int(start), int(end)))
        if len(events) > _MAX_EVENTS:
            raise LoopbackTimingError("too_many_events")
    reasons = []
    if np.count_nonzero(active) / max(1, len(active)) > 0.15:
        reasons.append("continuous_or_dense_signal")
    if any(not 2 <= (end - start) * resolution_ms <= 50 for start, end in events):
        reasons.append("burst_duration_outside_contract")
    quiet_bins = math.ceil(200 / resolution_ms)
    if np.any(active[:quiet_bins]):
        reasons.append("quiet_lead_missing")
    if np.any(active[-quiet_bins:]):
        reasons.append("quiet_tail_missing")
    return events, reasons, active


def _refine_pair(reference: np.ndarray, returned: np.ndarray, source, target,
                 resolution_ms: float, max_delay_ms: float):
    source_start, source_end = source
    return_start, return_end = target
    source_length = source_end - source_start
    return_length = return_end - return_start
    if not 0.7 <= return_length / source_length <= 1.4:
        return None, "distorted_or_ambiguous_burst"
    padding = max(2, math.ceil(3 / resolution_ms))
    first, last = max(0, source_start - padding), min(len(reference), source_end + padding)
    template = reference[first:last]
    source_norm = float(np.linalg.norm(template))
    approximate = return_start - source_start
    radius = max(2, math.ceil(2 / resolution_ms))
    candidates = []
    for delay in range(approximate - radius, approximate + radius + 1):
        if first + delay < 0 or last + delay > len(returned):
            continue
        segment = returned[first + delay:last + delay]
        denominator = source_norm * float(np.linalg.norm(segment))
        score = float(np.dot(template, segment)) / denominator if denominator > 0 else 0.0
        candidates.append((score, delay))
    if not candidates:
        return None, "delay_outside_bounds"
    score, delay = max(candidates)
    # Inspect the local peak before enforcing the declared lag bounds. Cutting
    # the search at zero/the upper bound could manufacture a plausible boundary
    # value when the stronger match actually falls outside the permitted range.
    if score < 0.90:
        return None, "distorted_or_ambiguous_burst"
    if delay <= 0 or delay * resolution_ms > max_delay_ms:
        return None, "delay_outside_bounds"
    # Two overlapping returns can have a strong single correlation peak yet
    # broaden the energy envelope, shifting the reported delay between paths.
    # Compare normalized second moments, independently of level and polarity.
    # This conservative policy also refuses genuine but distorted bursts; it
    # does not pretend every unresolved echo can be inferred from a waveform.
    def variance(profile: np.ndarray) -> float:
        energy = profile * profile
        total = float(np.sum(energy))
        if total <= 0:
            return 0.0
        positions = np.arange(len(profile), dtype=np.float64)
        center = float(np.dot(positions, energy)) / total
        return float(np.dot((positions - center) ** 2, energy)) / total

    reference_variance = variance(template)
    return_variance = variance(returned[first + delay:last + delay])
    if reference_variance <= 0 or not 0.85 <= return_variance / reference_variance <= 1.15:
        return None, "distorted_or_ambiguous_burst"
    return delay, None


def analyze_loopback(path, *, reference_channel: int, return_channel: int,
                     max_delay_ms: float, same_clock_confirmed: bool) -> dict:
    """Measure conservative round-trip sample delay, never physical acceptance.

    Contract: at least 20 isolated 2–50 ms bursts with nonuniform spacing,
    200 ms quiet lead/tail, and reference quiet gaps exceeding the maximum
    delay plus 50 ms. Full-scale, missing, ambiguous or distorted bursts
    withhold aggregate timing. Returned frame positions are RMS-bin alignment
    landmarks, not independently certified physical launch/arrival instants.
    """

    if same_clock_confirmed is not True:
        raise LoopbackTimingError("same_clock_required")
    if (type(reference_channel) is not int or type(return_channel) is not int
            or not 1 <= reference_channel <= 8 or not 1 <= return_channel <= 8
            or reference_channel == return_channel
            or isinstance(max_delay_ms, bool) or not isinstance(max_delay_ms, (int, float))):
        raise LoopbackTimingError("invalid_configuration")
    try:
        delay_limit = float(max_delay_ms)
    except (ValueError, OverflowError):
        raise LoopbackTimingError("invalid_configuration") from None
    if not math.isfinite(delay_limit) or not 0 < delay_limit <= 1000:
        raise LoopbackTimingError("invalid_configuration")
    max_delay_ms = delay_limit
    deadline = time.monotonic() + _MAX_ANALYSIS_SECONDS
    envelope, bin_frames, clipped, facts = _read_capture(
        path, (reference_channel, return_channel), deadline
    )
    resolution_ms = 1000 * bin_frames / facts["sample_rate"]
    reference, reference_reasons, _ = _events(envelope[:, 0], resolution_ms)
    returned, return_reasons, _ = _events(envelope[:, 1], resolution_ms)
    reasons = list(dict.fromkeys(reference_reasons + return_reasons))
    if clipped:
        reasons.append("clipped_or_full_scale")
    if np.array_equal(envelope[:, 0], envelope[:, 1]):
        reasons.append("identical_channels")
    if len(reference) < 20 or len(returned) < 20:
        reasons.append("too_few_events")
    if len(reference) > 1:
        gaps = [(reference[index][0] - reference[index - 1][1]) * resolution_ms
                for index in range(1, len(reference))]
        if min(gaps) <= max_delay_ms + 50:
            reasons.append("reference_windows_overlap")
        intervals = np.diff([start for start, _end in reference]) * resolution_ms
        if float(np.ptp(intervals)) < 10:
            reasons.append("periodic_reference")
    if reference and (reference[-1][1] * resolution_ms + max_delay_ms + 50
                      > 1000 * facts["frames"] / facts["sample_rate"]):
        reasons.append("incomplete_final_search_window")
    pairs, used, ambiguous = [], set(), 0
    # Even invalid captures retain useful counts; do not manufacture timing
    # from dense/noisy signals or overlapping correspondence windows.
    if not reference_reasons and not return_reasons and "reference_windows_overlap" not in reasons:
        for source in reference:
            _budget(deadline)
            eligible = [(index, event) for index, event in enumerate(returned)
                        if index not in used
                        and 0 < (event[0] - source[0]) * resolution_ms <= max_delay_ms]
            if len(eligible) > 1:
                ambiguous += 1
                continue
            if not eligible:
                continue
            index, target = eligible[0]
            delay, failure = _refine_pair(envelope[:, 0], envelope[:, 1], source, target,
                                         resolution_ms, max_delay_ms)
            if delay is None:
                reasons.append(failure)
                continue
            used.add(index)
            source_frame = source[0] * bin_frames
            returned_frame = source_frame + delay * bin_frames
            pairs.append({"reference_frame": source_frame, "return_frame": returned_frame,
                          "delay_ms": 1000 * (returned_frame - source_frame) / facts["sample_rate"]})
    unmatched_reference = len(reference) - len(pairs)
    unmatched_return = len(returned) - len(used)
    if ambiguous:
        reasons.append("ambiguous_returns")
    if unmatched_reference or unmatched_return:
        reasons.append("unmatched_or_unreliable_events")
    if len(pairs) < 20:
        reasons.append("too_few_valid_pairs")
    reasons = list(dict.fromkeys(reasons))
    summary = None
    if not reasons:
        delays = np.array([pair["delay_ms"] for pair in pairs], dtype=np.float64)
        summary = {"minimum_ms": float(np.min(delays)), "maximum_ms": float(np.max(delays)),
                   "median_ms": float(np.median(delays)), "p95_ms": float(np.percentile(delays, 95))}
    _budget(deadline)
    return {
        "schema_version": 1,
        "measurement": "audio_round_trip_delay",
        "physical_validation": "not_run",
        "status": "indeterminate" if reasons else "measured",
        "config": {"reference_channel": reference_channel, "return_channel": return_channel,
                   "max_delay_ms": float(max_delay_ms)},
        "provenance": {"same_clock_confirmed": True, "verified": False},
        "input": facts,
        "resolution": {"frames": bin_frames, "milliseconds": resolution_ms,
                       "accuracy": "not_certified"},
        "detector": {"name": "isolated_burst_rms", "version": 1,
                     "minimum_pairs": 20, "burst_duration_ms": [2, 50],
                     "quiet_lead_tail_ms": 200, "reference_gap_margin_ms": 50,
                     "minimum_interval_variation_ms": 10,
                     "minimum_correlation": 0.90, "energy_variance_ratio": [0.85, 1.15]},
        "counts": {"reference_events": len(reference), "return_events": len(returned),
                   "matched_pairs": len(pairs), "unmatched_reference": unmatched_reference,
                   "unmatched_return": unmatched_return, "ambiguous_pairs": ambiguous},
        "pairs": pairs,
        "summary": summary,
        "reasons": reasons,
        "limitations": [
            "Wiring and the single recording timebase are operator assertions, not verified provenance.",
            "RMS-bin resolution is not certified physical measurement accuracy.",
            "Round-trip delay includes the complete declared path; it is not one-way network delay.",
            "This does not certify clock drift, audibility, route isolation, or playable latency.",
            "Some overlapping paths are indistinguishable from one burst; declared wiring remains unverified.",
        ],
    }


__all__ = ["LoopbackTimingError", "analyze_loopback"]
