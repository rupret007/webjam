"""Independent sample-defined loopback fixtures; no physical timing claims."""

import hashlib
import json
import os
from pathlib import Path
import struct
import wave

import numpy as np
import pytest

from core.loopback_timing import LoopbackTimingError, analyze_loopback


def _write_pcm(path: Path, samples, *, rate=48000, bits=16):
    samples = np.asarray(samples, dtype=np.float64)
    integers = np.rint(np.clip(samples, -1, 1) * (2 ** (bits - 1) - 1)).astype(np.int32)
    if bits == 24:
        flat = integers.reshape(-1)
        payload = np.column_stack([(flat >> shift) & 255 for shift in (0, 8, 16)]).astype(np.uint8).tobytes()
    else:
        payload = integers.astype(f"<i{bits // 8}").tobytes()
    with wave.open(str(path), "wb") as output:
        output.setnchannels(samples.shape[1])
        output.setsampwidth(bits // 8)
        output.setframerate(rate)
        output.writeframes(payload)
    return path


def _capture(
    *, rate=48000, shifts=None, count=24, gain=.65, inverted=False,
    noise=0.0, missing=(), duplicate=(), channels=2, selected=(1, 2), burst_seconds=.015,
):
    """Known sample shifts, independent of detector thresholds or helpers."""

    if shifts is None:
        shifts = [1137] * count
    assert len(shifts) == count
    gaps = [.37, .53, .41, .61, .47]
    starts = [round(.35 * rate)]
    for index in range(count - 1):
        starts.append(starts[-1] + round(gaps[index % len(gaps)] * rate))
    length = round(burst_seconds * rate)
    # Broadband content avoids an accidental integer-cycle correlation match.
    rng = np.random.default_rng(82741)
    burst = rng.uniform(-1, 1, length) * np.hanning(length)
    burst = .6 * burst / np.max(np.abs(burst))
    frames = starts[-1] + max(shifts) + length + round(.4 * rate)
    samples = rng.normal(0, noise, (frames, channels))
    for index, (start, shift) in enumerate(zip(starts, shifts, strict=True)):
        samples[start:start + length, selected[0] - 1] += burst
        if index not in missing:
            position = start + shift
            samples[position:position + length, selected[1] - 1] += burst * gain * (-1 if inverted else 1)
            if index in duplicate:
                position += round(.06 * rate)
                samples[position:position + length, selected[1] - 1] += burst * gain
    return samples, starts


def _analyze(path, **changes):
    settings = dict(reference_channel=1, return_channel=2, max_delay_ms=200, same_clock_confirmed=True)
    settings.update(changes)
    return analyze_loopback(path, **settings)


def _assert_private(report, path):
    text = json.dumps(report)
    assert str(path) not in text
    assert path.name not in text
    assert "PRIVATE_CAPTURE" not in text
    assert report["provenance"]["same_clock_confirmed"] is True
    assert report["provenance"]["verified"] is False
    assert report["resolution"]["accuracy"] == "not_certified"


def _assert_indeterminate(report):
    assert report["status"] == "indeterminate"
    assert report["summary"] is None
    assert report["reasons"]


@pytest.mark.parametrize("rate,shift,bits", [(44100, 1079, 16), (48000, 1137, 24), (96000, 2315, 32)])
def test_known_noninteger_millisecond_delay_is_derived_from_sample_positions(tmp_path, rate, shift, bits):
    samples, _ = _capture(rate=rate, shifts=[shift] * 24)
    path = _write_pcm(tmp_path / "PRIVATE_CAPTURE known.wav", samples, rate=rate, bits=bits)
    original = path.read_bytes()
    report = _analyze(path)
    expected_ms = 1000 * shift / rate

    assert report["status"] == "measured"
    assert report["counts"]["reference_events"] == 24
    assert report["counts"]["return_events"] == 24
    assert report["counts"]["matched_pairs"] == 24
    assert report["counts"]["unmatched_reference"] == 0
    assert report["counts"]["unmatched_return"] == 0
    assert report["counts"]["ambiguous_pairs"] == 0
    tolerance = report["resolution"]["milliseconds"] + 1000 / rate
    for key in ("minimum_ms", "maximum_ms", "median_ms", "p95_ms"):
        assert report["summary"][key] == pytest.approx(expected_ms, abs=tolerance)
    for pair in report["pairs"]:
        assert pair["delay_ms"] == pytest.approx(expected_ms, abs=tolerance)
        assert pair["return_frame"] > pair["reference_frame"]
    assert report["input"]["sample_rate"] == rate
    assert report["input"]["frames"] == len(samples)
    assert report["input"]["channels"] == 2
    assert report["input"]["sha256"] == hashlib.sha256(original).hexdigest()
    assert path.read_bytes() == original
    _assert_private(report, path)


@pytest.mark.parametrize("gain,inverted,noise", [(.22, False, 0), (.8, True, 0), (.55, True, .002)])
def test_gain_polarity_and_quiet_noise_do_not_change_known_delay(tmp_path, gain, inverted, noise):
    samples, _ = _capture(gain=gain, inverted=inverted, noise=noise)
    report = _analyze(_write_pcm(tmp_path / "calibration.wav", samples))
    assert report["status"] == "measured"
    assert report["counts"]["matched_pairs"] == 24
    assert report["summary"]["median_ms"] == pytest.approx(
        1000 * 1137 / 48000, abs=report["resolution"]["milliseconds"] + 1000 / 48000,
    )


def test_changing_delay_reports_distribution_instead_of_one_false_constant(tmp_path):
    shifts = [480 + index * 192 for index in range(24)]
    samples, _ = _capture(shifts=shifts)
    report = _analyze(_write_pcm(tmp_path / "variable.wav", samples))
    expected = np.asarray(shifts) * 1000 / 48000
    assert report["status"] == "measured"
    summary = report["summary"]
    tolerance = report["resolution"]["milliseconds"] + 1000 / 48000
    for key, value in (
        ("minimum_ms", min(expected)), ("maximum_ms", max(expected)),
        ("median_ms", np.median(expected)), ("p95_ms", np.percentile(expected, 95)),
    ):
        assert summary[key] == pytest.approx(value, abs=tolerance)
    assert summary["maximum_ms"] - summary["minimum_ms"] > 80


@pytest.mark.parametrize("channels", [4, 8])
def test_explicit_channels_select_only_the_requested_capture_pair(tmp_path, channels):
    samples, _ = _capture(channels=channels, selected=(2, channels))
    samples[:, 0] = .2  # unrelated constant audio must not become reference timing
    path = _write_pcm(tmp_path / "four-channel.wav", samples)
    report = _analyze(path, reference_channel=2, return_channel=channels)
    assert report["status"] == "measured"
    assert report["input"]["channels"] == channels
    assert report["counts"]["matched_pairs"] == 24
    _assert_indeterminate(_analyze(path, reference_channel=1, return_channel=channels))


def test_missing_returns_remain_visible_and_prevent_success_summary(tmp_path):
    samples, _ = _capture(missing=(3, 11, 17))
    report = _analyze(_write_pcm(tmp_path / "missing.wav", samples))
    _assert_indeterminate(report)
    assert report["counts"]["reference_events"] == 24
    assert report["counts"]["return_events"] == 21
    assert report["counts"]["matched_pairs"] == 21
    assert report["counts"]["unmatched_reference"] == 3


def test_duplicate_returns_do_not_pick_a_plausible_first_arrival(tmp_path):
    samples, _ = _capture(duplicate=(2, 9, 15))
    report = _analyze(_write_pcm(tmp_path / "duplicates.wav", samples))
    _assert_indeterminate(report)
    assert report["counts"]["return_events"] == 27
    assert report["counts"]["ambiguous_pairs"] >= 3


@pytest.mark.parametrize("echo_ms", [5, 8])
def test_overlapping_equal_echoes_do_not_masquerade_as_one_clean_return(tmp_path, echo_ms):
    samples, _ = _capture(burst_seconds=.020)
    reference = samples[:, 0].copy()
    first, second = 1137, 1137 + round(echo_ms * 48000 / 1000)
    samples[:, 1] = 0
    samples[first:, 1] += reference[:-first] * .5
    samples[second:, 1] += reference[:-second] * .5
    report = _analyze(_write_pcm(tmp_path / "overlapping-echoes.wav", samples))
    _assert_indeterminate(report)


@pytest.mark.parametrize("content", ["silence", "noise", "continuous", "clipped", "equal", "swapped"])
def test_noncalibration_or_wrong_channel_direction_never_looks_measured(tmp_path, content):
    samples, _ = _capture()
    if content == "silence":
        samples[:] = 0
    elif content == "noise":
        samples = np.random.default_rng(559).uniform(-.3, .3, samples.shape)
    elif content == "continuous":
        tone = .25 * np.sin(2 * np.pi * 1403 * np.arange(len(samples)) / 48000)
        samples[:, 0], samples[:, 1] = tone, np.roll(tone, 1137)
    elif content == "clipped":
        samples = np.sign(samples)
    elif content == "equal":
        samples[:, 1] = samples[:, 0]
    else:
        samples = samples[:, ::-1]
    path = _write_pcm(tmp_path / f"PRIVATE_CAPTURE {content}.wav", samples)
    report = _analyze(path)
    _assert_indeterminate(report)
    _assert_private(report, path)


def test_independent_noise_on_return_cannot_supply_missing_pulse_evidence(tmp_path):
    samples, _ = _capture()
    samples[:, 1] = np.random.default_rng(811).normal(0, .003, len(samples))
    report = _analyze(_write_pcm(tmp_path / "noise-return.wav", samples))
    _assert_indeterminate(report)


def test_fewer_than_twenty_pairs_do_not_claim_a_useful_measurement(tmp_path):
    samples, _ = _capture(count=19)
    report = _analyze(_write_pcm(tmp_path / "short-evidence.wav", samples))
    _assert_indeterminate(report)


def test_delay_outside_selected_search_window_is_not_forced_to_boundary(tmp_path):
    samples, _ = _capture()
    report = _analyze(_write_pcm(tmp_path / "outside-window.wav", samples), max_delay_ms=10)
    _assert_indeterminate(report)


def test_standard_probe_full_capture_accepts_only_known_synthetic_round_trip(tmp_path):
    from tools.make_loopback_probe import write_probe

    probe_path = tmp_path / "standard-probe.wav"
    metadata = write_probe(probe_path)
    path = tmp_path / "PRIVATE_CAPTURE synthetic-round-trip.wav"
    delay = 1379
    carry = np.zeros(delay, dtype="<i2")
    # Build an exact sample-shifted return without using analyzer internals,
    # and stream the long probe rather than allocating the whole capture.
    with wave.open(str(probe_path), "rb") as source, wave.open(str(path), "wb") as output:
        assert source.getnchannels() == 1 and source.getsampwidth() == 2
        rate, frames = source.getframerate(), source.getnframes()
        output.setparams((2, 2, rate, frames + delay, "NONE", "not compressed"))
        while raw := source.readframes(32768):
            reference = np.frombuffer(raw, dtype="<i2")
            history = np.concatenate((carry, reference))
            returned, carry = history[:len(reference)], history[len(reference):]
            output.writeframesraw(np.column_stack((reference, returned)).astype("<i2").tobytes())
        output.writeframesraw(np.column_stack((np.zeros(delay, dtype="<i2"), carry)).astype("<i2").tobytes())
    report = _analyze(path, max_delay_ms=1000)
    assert metadata["burst_count"] == 100
    assert metadata["physical_validation"] == "not_run"
    assert metadata["audio_played"] is False and metadata["audio_recorded"] is False
    assert 140 < metadata["duration_seconds"] < 180
    assert report["status"] == "measured"
    assert report["counts"]["matched_pairs"] == 100
    assert report["input"]["frames"] == frames + delay
    assert report["summary"]["median_ms"] == pytest.approx(
        delay * 1000 / rate, abs=report["resolution"]["milliseconds"] + 1000 / rate,
    )
    _assert_private(report, path)


@pytest.mark.parametrize("count", [512, 513])
def test_event_bound_accepts_limit_and_rejects_excess_without_unbounded_pairing(tmp_path, count):
    rate = 44100
    gap_frames = [round(rate * seconds) for seconds in (.070, .093, .117)]
    starts = [round(.35 * rate)]
    for index in range(count - 1):
        starts.append(starts[-1] + gap_frames[index % len(gap_frames)])
    width, delay = round(.006 * rate), 97
    burst = np.random.default_rng(855).uniform(-1, 1, width) * np.hanning(width)
    burst *= .6 / np.max(np.abs(burst))
    samples = np.zeros((starts[-1] + width + delay + round(.4 * rate), 2))
    for start in starts:
        samples[start:start + width, 0] = burst
        samples[start + delay:start + delay + width, 1] = burst * .7
    path = _write_pcm(tmp_path / "bounded-events.wav", samples, rate=rate)
    if count > 512:
        with pytest.raises(LoopbackTimingError) as caught:
            _analyze(path, max_delay_ms=5)
        assert caught.value.code == "too_many_events"
    else:
        report = _analyze(path, max_delay_ms=5)
        assert report["status"] == "measured"
        assert report["counts"]["matched_pairs"] == 512


@pytest.mark.parametrize("changes,code", [
    ({"same_clock_confirmed": False}, "same_clock_required"),
    ({"same_clock_confirmed": "yes"}, "same_clock_required"),
    ({"reference_channel": 0}, "invalid_configuration"),
    ({"reference_channel": -1}, "invalid_configuration"),
    ({"reference_channel": True}, "invalid_configuration"),
    ({"return_channel": 3}, "invalid_configuration"),
    ({"return_channel": 1}, "invalid_configuration"),
    ({"max_delay_ms": 0}, "invalid_configuration"),
    ({"max_delay_ms": -1}, "invalid_configuration"),
    ({"max_delay_ms": 1001}, "invalid_configuration"),
    ({"max_delay_ms": float("nan")}, "invalid_configuration"),
    ({"max_delay_ms": float("inf")}, "invalid_configuration"),
])
def test_configuration_requires_explicit_bounded_distinct_channel_pair(tmp_path, changes, code):
    path = _write_pcm(tmp_path / "PRIVATE_CAPTURE inputs.wav", np.zeros((48000, 2)))
    with pytest.raises(LoopbackTimingError) as caught:
        _analyze(path, **changes)
    assert caught.value.code == code
    assert path.name not in str(caught.value)
    assert str(tmp_path) not in caught.value.message


def _sparse_wave(path, *, rate=48000, channels=2, bits=16, format_tag=1, frames=48000):
    block = channels * bits // 8
    size = frames * block
    header = (
        b"RIFF" + struct.pack("<I", 36 + size) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16,
        format_tag, channels, rate, rate * block, block, bits)
        + b"data" + struct.pack("<I", size)
    )
    with path.open("wb") as output:
        output.write(header)
        output.truncate(44 + size)
    return path


@pytest.mark.parametrize("changes", [
    {"rate": 32000}, {"channels": 1}, {"channels": 9}, {"bits": 8}, {"format_tag": 3, "bits": 32},
])
def test_unsupported_wav_encodings_are_rejected_before_signal_analysis(tmp_path, changes):
    path = _sparse_wave(tmp_path / "PRIVATE_CAPTURE unsupported.wav", **changes)
    with pytest.raises(LoopbackTimingError) as caught:
        _analyze(path)
    assert caught.value.code == "unsupported_audio"
    assert path.name not in caught.value.message


@pytest.mark.parametrize("valid_bits,extension_size", [
    (32, 22), (16, 22), (32, 20), (32, 23), (32, 65535),
])
def test_wavex_precision_cannot_bypass_declared_container_clipping_policy(
    tmp_path, valid_bits, extension_size,
):
    path = tmp_path / "precision.wav"
    pcm_guid = bytes.fromhex("0100000000001000800000aa00389b71")
    format_data = struct.pack(
        "<HHIIHHHHI", 0xFFFE, 2, 48000, 48000 * 8, 8, 32,
        extension_size, valid_bits, 3,
    ) + pcm_guid
    payload = bytes(48000 * 8)
    path.write_bytes(
        b"RIFF" + struct.pack("<I", 4 + 8 + len(format_data) + 8 + len(payload))
        + b"WAVEfmt " + struct.pack("<I", len(format_data)) + format_data
        + b"data" + struct.pack("<I", len(payload)) + payload
    )
    if valid_bits == 32 and extension_size == 22:
        _assert_indeterminate(_analyze(path))
    else:
        with pytest.raises(LoopbackTimingError) as caught:
            _analyze(path)
        assert caught.value.code == "unsupported_audio"


def test_capture_duration_limit_is_checked_before_decoding_sparse_payload(tmp_path):
    path = _sparse_wave(tmp_path / "long.wav", frames=48000 * 181)
    with pytest.raises(LoopbackTimingError) as caught:
        _analyze(path)
    assert caught.value.code == "capture_too_long"


def test_file_size_limit_prevents_reading_oversized_sparse_input(tmp_path):
    path = tmp_path / "large.wav"
    with path.open("wb") as output:
        output.truncate(256 * 1024 * 1024 + 1)
    with pytest.raises(LoopbackTimingError) as caught:
        _analyze(path)
    assert caught.value.code == "file_too_large"


@pytest.mark.parametrize("malformation", ["empty", "not-wave", "truncated-header", "truncated-data"])
def test_malformed_or_truncated_wav_has_fixed_private_safe_failure(tmp_path, malformation):
    path = tmp_path / "PRIVATE_CAPTURE malformed.wav"
    if malformation == "empty":
        path.write_bytes(b"")
    elif malformation == "not-wave":
        path.write_bytes(b"PRIVATE_CAPTURE not audio")
    else:
        _sparse_wave(path)
        with path.open("r+b") as output:
            output.truncate(17 if malformation == "truncated-header" else 123)
    with pytest.raises(LoopbackTimingError) as caught:
        _analyze(path)
    assert caught.value.code == (
        "unsupported_audio" if malformation in {"empty", "not-wave"} else "invalid_file"
    )
    assert "PRIVATE_CAPTURE" not in str(caught.value)
    assert str(tmp_path) not in caught.value.message


def test_symlink_is_rejected_without_following_target_or_disclosing_path(tmp_path):
    target = _write_pcm(tmp_path / "PRIVATE_CAPTURE target.wav", np.zeros((48000, 2)))
    path = tmp_path / "PRIVATE_CAPTURE alias.wav"
    path.symlink_to(target)
    before = target.read_bytes()
    with pytest.raises(LoopbackTimingError) as caught:
        _analyze(path)
    assert caught.value.code == "invalid_file"
    assert "PRIVATE_CAPTURE" not in str(caught.value)
    assert target.read_bytes() == before


@pytest.mark.parametrize("mutation", ["in_place", "replacement"])
def test_capture_changed_during_decode_cannot_return_a_measurement(tmp_path, monkeypatch, mutation):
    from core import loopback_timing

    samples, _ = _capture()
    path = _write_pcm(tmp_path / "PRIVATE_CAPTURE changing.wav", samples)
    original = path.read_bytes()
    real_read = loopback_timing.sf.SoundFile.read
    changed = False

    def mutate_after_first_block(reader, *args, **kwargs):
        nonlocal changed
        block = real_read(reader, *args, **kwargs)
        if not changed:
            changed = True
            if mutation == "in_place":
                old_time = path.stat().st_mtime_ns
                with path.open("r+b") as writer:
                    writer.seek(44)
                    writer.write(bytes([original[44] ^ 1]))
                os.utime(path, ns=(old_time + 2_000_000_000, old_time + 2_000_000_000))
            else:
                replacement = tmp_path / "replacement.tmp"
                replacement.write_bytes(original)
                replacement.replace(path)
        return block

    monkeypatch.setattr(loopback_timing.sf.SoundFile, "read", mutate_after_first_block)
    with pytest.raises(LoopbackTimingError) as caught:
        _analyze(path)
    assert changed
    assert caught.value.code == "file_changed"
    assert "PRIVATE_CAPTURE" not in caught.value.message
    assert str(tmp_path) not in str(caught.value)


@pytest.mark.parametrize("kind", ["missing", "directory"])
def test_nonregular_path_fails_without_disclosing_input(tmp_path, kind):
    path = tmp_path / "PRIVATE_CAPTURE unavailable.wav"
    if kind == "directory":
        path.mkdir()
    with pytest.raises(LoopbackTimingError) as caught:
        _analyze(path)
    assert caught.value.code == "invalid_file"
    assert "PRIVATE_CAPTURE" not in str(caught.value)
