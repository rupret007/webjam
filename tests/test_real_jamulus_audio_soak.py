"""Opt-in longevity certification for two real Jamulus/JACK clients.

This test is intentionally excluded from the normal CI command and refuses to
run for less than one hour. It repeats deterministic click/tone/silence phases,
cycles the real multitrack recorder, forces one client through a server-observed
disconnect/reconnect, samples Linux process resources, and writes a JSON report.

Prepared Linux host command (the two binary variables must name official 3.12.2
packages, and JACK-Client/numpy/jackd2/jack-tools must be installed)::

    WEBJAM_JAMULUS_BINARY=/usr/bin/jamulus-headless \
    WEBJAM_JAMULUS_CLIENT_BINARY=/usr/bin/jamulus \
    WEBJAM_RUN_JACK_AUDIO_SOAK=1 \
    WEBJAM_JACK_AUDIO_SOAK_SECONDS=3600 \
    WEBJAM_JACK_AUDIO_SOAK_REPORT=artifacts/jamulus-jack-soak.json \
      pytest tests/test_real_jamulus_audio_soak.py -v -s

A short rehearsal of the same recorder/reconnect/resource/cleanup machinery is
separately gated and does not count as longevity certification::

    WEBJAM_JAMULUS_BINARY=/usr/bin/jamulus-headless \
    WEBJAM_JAMULUS_CLIENT_BINARY=/usr/bin/jamulus \
    WEBJAM_RUN_JACK_AUDIO_SOAK_SMOKE=1 \
    WEBJAM_JACK_AUDIO_SMOKE_SECONDS=18 \
      pytest tests/test_real_jamulus_audio_soak.py \
        -k short_soak_rehearsal -v -s
"""
from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest

from tests.support.jamulus_jack_harness import (
    CAPTURE_TAIL_S,
    FIXTURE_DURATION_S,
    ProcessResourceSample,
    JamulusJackHarness,
)

MIN_SOAK_SECONDS = 60.0 * 60.0
DEFAULT_RESOURCE_INTERVAL_S = 60.0
DEFAULT_MAX_RSS_GROWTH_KIB = 64 * 1024
DEFAULT_MAX_FD_GROWTH = 8
# Ubuntu 24/amd64 under QEMU produced 431 raw JACK callbacks over a clean
# 65.677-second rehearsal (6.56/s) while decoded dropout windows stayed zero.
# Ten per wall-second gives that non-realtime scheduler 52% headroom while the
# independent decoded-signal gate remains unchanged and materially stricter.
DEFAULT_MAX_XRUNS_PER_SECOND = 10.0
RECORDING_CYCLE_FRACTIONS = (0.10, 0.40, 0.70)
RECONNECT_FRACTION = 0.33


def _positive_float(name: str, default: float) -> float:
    raw = os.environ.get(name, str(default))
    try:
        value = float(raw)
    except ValueError as exc:
        raise AssertionError(f"{name} must be numeric, got {raw!r}") from exc
    if value <= 0:
        raise AssertionError(f"{name} must be positive, got {value}")
    return value


def _positive_int(name: str, default: int) -> int:
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise AssertionError(f"{name} must be an integer, got {raw!r}") from exc
    if value <= 0:
        raise AssertionError(f"{name} must be positive, got {value}")
    return value


def _sample_payload(
    elapsed_s: float, samples: tuple[ProcessResourceSample, ...]
) -> dict[str, Any]:
    return {
        "elapsed_s": round(elapsed_s, 3),
        "processes": [asdict(sample) for sample in samples],
    }


def _index_samples(
    samples: tuple[ProcessResourceSample, ...],
) -> dict[str, ProcessResourceSample]:
    return {sample.name: sample for sample in samples}


def _write_report(
    report: dict[str, Any],
    *,
    environment_name: str = "WEBJAM_JACK_AUDIO_SOAK_REPORT",
    default_path: str = "artifacts/jamulus-jack-soak.json",
) -> Path:
    target = Path(
        os.environ.get(environment_name, default_path)
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(report, allow_nan=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return target


def _update_signal_summary(summary: dict[str, Any], result: Any) -> None:
    summary["transport_cycles"] += 1
    summary["jack_xruns"] += result.xrun_count
    for label, metrics in (
        ("client_a", result.client_a_metrics),
        ("client_b", result.client_b_metrics),
    ):
        values = summary[label]
        values["min_tone_rms"] = (
            metrics.tone_rms
            if values["min_tone_rms"] is None
            else min(values["min_tone_rms"], metrics.tone_rms)
        )
        values["max_silence_rms"] = max(
            values["max_silence_rms"], metrics.silence_rms
        )
        values["max_peak"] = max(values["max_peak"], metrics.peak)
        values["min_cross_rejection_db"] = (
            metrics.cross_rejection_db
            if values["min_cross_rejection_db"] is None
            else min(values["min_cross_rejection_db"], metrics.cross_rejection_db)
        )
        values["max_dropout_windows"] = max(
            values["max_dropout_windows"], metrics.dropout_window_count
        )


def _new_signal_summary() -> dict[str, Any]:
    process = {
        "min_tone_rms": None,
        "max_silence_rms": 0.0,
        "max_peak": 0.0,
        "min_cross_rejection_db": None,
        "max_dropout_windows": 0,
    }
    return {
        "transport_cycles": 0,
        "jack_xruns": 0,
        "client_a": process.copy(),
        "client_b": process.copy(),
    }


@pytest.mark.skipif(
    os.environ.get("WEBJAM_RUN_JACK_AUDIO_SOAK_SMOKE") != "1",
    reason="real JACK/Jamulus longevity rehearsal is explicitly opt-in",
)
@pytest.mark.skipif(sys.platform != "linux", reason="JACK dummy smoke is Linux-only")
def test_short_soak_rehearsal_exercises_recovery_and_resources() -> None:
    requested_s = _positive_float("WEBJAM_JACK_AUDIO_SMOKE_SECONDS", 18.0)
    max_rss_growth_kib = _positive_int(
        "WEBJAM_JACK_MAX_RSS_GROWTH_KIB", DEFAULT_MAX_RSS_GROWTH_KIB
    )
    max_fd_growth = _positive_int(
        "WEBJAM_JACK_MAX_FD_GROWTH", DEFAULT_MAX_FD_GROWTH
    )
    max_xruns_per_second = _positive_float(
        "WEBJAM_JACK_MAX_XRUNS_PER_SECOND", DEFAULT_MAX_XRUNS_PER_SECOND
    )
    harness = JamulusJackHarness.from_environment()
    signal_summary = _new_signal_summary()
    report: dict[str, Any] = {
        "schema": 1,
        "kind": "short-rehearsal-not-longevity-certification",
        "requested_signal_duration_s": requested_s,
        "success": False,
    }
    started = time.monotonic()
    baseline: tuple[ProcessResourceSample, ...] = ()
    final_samples: tuple[ProcessResourceSample, ...] = ()

    try:
        with harness:
            baseline = harness.resource_snapshot()
            harness.set_recording(True)
            signal_started = time.monotonic()
            transport_cycles = 0
            # Two phases guarantee meaningful audio on both sides of the real
            # recorder restart even when a caller asks for only a few seconds.
            while (
                transport_cycles < 2
                or time.monotonic() - signal_started < requested_s
            ):
                result = harness.run_transport()
                _update_signal_summary(signal_summary, result)
                transport_cycles += 1
                del result
                if transport_cycles == 1:
                    harness.restart_recording()
            harness.set_recording(False)

            artifacts = harness.recording_artifacts()
            wav_artifacts = [path for path in artifacts if path.suffix.lower() == ".wav"]
            reconnect_started = time.monotonic()
            recovered = harness.exercise_disconnect_reconnect(client_index=1)
            recovery_s = time.monotonic() - reconnect_started
            result = harness.run_transport()
            _update_signal_summary(signal_summary, result)
            del result

            final_samples = harness.resource_snapshot()
            baseline_by_name = _index_samples(baseline)
            final_by_name = _index_samples(final_samples)
            observed_s = time.monotonic() - started
            xrun_rate = signal_summary["jack_xruns"] / observed_s
            growth: dict[str, Any] = {}
            for name, before in baseline_by_name.items():
                after = final_by_name[name]
                rss_growth = after.rss_kib - before.rss_kib
                fd_growth = after.fd_count - before.fd_count
                cpu_delta = after.cpu_seconds - before.cpu_seconds
                growth[name] = {
                    "rss_growth_kib": rss_growth,
                    "fd_growth": fd_growth,
                    "cpu_seconds": round(cpu_delta, 3),
                    "cpu_fraction_of_wall": round(cpu_delta / observed_s, 5),
                }
                assert rss_growth <= max_rss_growth_kib
                assert fd_growth <= max_fd_growth
            assert xrun_rate <= max_xruns_per_second, (
                f"raw JACK xrun rate {xrun_rate:.3f}/s exceeds the "
                f"{max_xruns_per_second:.3f}/s rehearsal ceiling"
            )

            assert {entry["name"] for entry in recovered} == {
                harness.CLIENT_A_NAME,
                harness.CLIENT_B_NAME,
            }
            assert len(wav_artifacts) >= 4
            assert all(path.stat().st_size > 44 for path in wav_artifacts)
            report.update(
                {
                    "resource_growth": growth,
                    "jack_xrun_rate_per_wall_second": round(xrun_rate, 6),
                    "max_jack_xruns_per_wall_second": max_xruns_per_second,
                    "recovery_s": round(recovery_s, 3),
                    "recording_artifacts": {
                        "file_count": len(artifacts),
                        "wav_file_count": len(wav_artifacts),
                        "total_bytes": sum(
                            path.stat().st_size for path in artifacts
                        ),
                    },
                }
            )

        assert not harness.cleanup_errors
        assert all(process.proc.poll() is not None for process in harness.processes)
        report["success"] = True
    except BaseException as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        report["wall_duration_s"] = round(time.monotonic() - started, 3)
        report["signal_summary"] = signal_summary
        report["resource_samples"] = [
            _sample_payload(0.0, baseline),
            _sample_payload(time.monotonic() - started, final_samples),
        ]
        report["cleanup"] = {
            "errors": list(harness.cleanup_errors),
            "process_exit_codes": {
                process.name: process.proc.poll() for process in harness.processes
            },
        }
        _write_report(
            report,
            environment_name="WEBJAM_JACK_AUDIO_SMOKE_REPORT",
            default_path="artifacts/jamulus-jack-smoke.json",
        )


@pytest.mark.skipif(
    os.environ.get("WEBJAM_RUN_JACK_AUDIO_SOAK") != "1",
    reason="one-hour real JACK/Jamulus soak is explicitly opt-in",
)
@pytest.mark.skipif(sys.platform != "linux", reason="JACK dummy soak is Linux-only")
def test_two_real_clients_survive_one_hour_transport_and_recovery() -> None:
    requested_s = _positive_float(
        "WEBJAM_JACK_AUDIO_SOAK_SECONDS", MIN_SOAK_SECONDS
    )
    if requested_s < MIN_SOAK_SECONDS:
        pytest.fail(
            "longevity certification requires at least 3600 seconds; "
            f"got {requested_s}"
        )
    segment_s = _positive_float(
        "WEBJAM_JACK_AUDIO_SEGMENT_SECONDS", FIXTURE_DURATION_S
    )
    tail_s = _positive_float(
        "WEBJAM_JACK_AUDIO_TAIL_SECONDS", CAPTURE_TAIL_S
    )
    sample_interval_s = _positive_float(
        "WEBJAM_JACK_RESOURCE_INTERVAL_SECONDS", DEFAULT_RESOURCE_INTERVAL_S
    )
    max_rss_growth_kib = _positive_int(
        "WEBJAM_JACK_MAX_RSS_GROWTH_KIB", DEFAULT_MAX_RSS_GROWTH_KIB
    )
    max_fd_growth = _positive_int(
        "WEBJAM_JACK_MAX_FD_GROWTH", DEFAULT_MAX_FD_GROWTH
    )
    max_xruns_per_second = _positive_float(
        "WEBJAM_JACK_MAX_XRUNS_PER_SECOND", DEFAULT_MAX_XRUNS_PER_SECOND
    )

    harness = JamulusJackHarness.from_environment()
    signal_summary = _new_signal_summary()
    resource_payloads: list[dict[str, Any]] = []
    recording_events: list[dict[str, Any]] = []
    reconnect_event: dict[str, Any] | None = None
    completed_recording_cycles = 0
    recording_restarts = 0
    report: dict[str, Any] = {
        "schema": 1,
        "requested_duration_s": requested_s,
        "segment_duration_s": segment_s,
        "capture_tail_s": tail_s,
        "max_rss_growth_kib": max_rss_growth_kib,
        "max_fd_growth": max_fd_growth,
        "max_jack_xruns_per_wall_second": max_xruns_per_second,
        "success": False,
    }
    test_started = time.monotonic()

    try:
        with harness:
            # Prime codec/JACK allocations before the resource-leak baseline.
            warmup = harness.run_transport(duration_s=segment_s, tail_s=tail_s)
            _update_signal_summary(signal_summary, warmup)
            del warmup

            baseline = harness.resource_snapshot()
            resource_payloads.append(_sample_payload(0.0, baseline))
            baseline_by_name = _index_samples(baseline)
            soak_started = time.monotonic()
            deadline = soak_started + requested_s
            next_sample_at = soak_started + sample_interval_s
            recording_thresholds = iter(
                requested_s * fraction for fraction in RECORDING_CYCLE_FRACTIONS
            )
            next_recording_at = next(recording_thresholds, None)
            recording_active = False
            segments_in_recording = 0
            reconnect_done = False

            while time.monotonic() < deadline:
                elapsed_s = time.monotonic() - soak_started

                if not reconnect_done and elapsed_s >= requested_s * RECONNECT_FRACTION:
                    reconnect_started = time.monotonic()
                    recovered = harness.exercise_disconnect_reconnect(client_index=1)
                    reconnect_event = {
                        "at_s": round(elapsed_s, 3),
                        "recovery_s": round(time.monotonic() - reconnect_started, 3),
                        "names": sorted(client["name"] for client in recovered),
                    }
                    reconnect_done = True

                if (
                    not recording_active
                    and next_recording_at is not None
                    and elapsed_s >= next_recording_at
                ):
                    harness.set_recording(True)
                    recording_active = True
                    segments_in_recording = 0
                    recording_events.append(
                        {"action": "start", "at_s": round(elapsed_s, 3)}
                    )

                result = harness.run_transport(duration_s=segment_s, tail_s=tail_s)
                _update_signal_summary(signal_summary, result)
                del result

                if recording_active and segments_in_recording == 0:
                    harness.restart_recording()
                    recording_restarts += 1
                    recording_events.append(
                        {
                            "action": "restart",
                            "at_s": round(time.monotonic() - soak_started, 3),
                        }
                    )
                    segments_in_recording = 1
                elif recording_active:
                    harness.set_recording(False)
                    recording_active = False
                    completed_recording_cycles += 1
                    recording_events.append(
                        {
                            "action": "stop",
                            "at_s": round(time.monotonic() - soak_started, 3),
                        }
                    )
                    next_recording_at = next(recording_thresholds, None)

                now = time.monotonic()
                if now >= next_sample_at:
                    resource_payloads.append(
                        _sample_payload(now - soak_started, harness.resource_snapshot())
                    )
                    next_sample_at = now + sample_interval_s

            if recording_active:
                harness.set_recording(False)
                completed_recording_cycles += 1
                recording_events.append(
                    {
                        "action": "stop-finally",
                        "at_s": round(time.monotonic() - soak_started, 3),
                    }
                )

            actual_soak_s = time.monotonic() - soak_started
            xrun_rate = signal_summary["jack_xruns"] / actual_soak_s
            final_samples = harness.resource_snapshot()
            resource_payloads.append(_sample_payload(actual_soak_s, final_samples))
            final_by_name = _index_samples(final_samples)
            growth: dict[str, Any] = {}
            for name, before in baseline_by_name.items():
                after = final_by_name[name]
                rss_growth = after.rss_kib - before.rss_kib
                fd_growth = after.fd_count - before.fd_count
                cpu_delta = after.cpu_seconds - before.cpu_seconds
                growth[name] = {
                    "rss_growth_kib": rss_growth,
                    "fd_growth": fd_growth,
                    "cpu_seconds": round(cpu_delta, 3),
                    "cpu_fraction_of_wall": round(cpu_delta / actual_soak_s, 5),
                }
                assert rss_growth <= max_rss_growth_kib, (
                    f"{name} RSS grew {rss_growth} KiB; limit is "
                    f"{max_rss_growth_kib} KiB"
                )
                assert fd_growth <= max_fd_growth, (
                    f"{name} gained {fd_growth} file descriptors; limit is "
                    f"{max_fd_growth}"
                )

            artifacts = harness.recording_artifacts()
            wav_artifacts = [path for path in artifacts if path.suffix.lower() == ".wav"]
            report["recording_artifacts"] = {
                "file_count": len(artifacts),
                "wav_file_count": len(wav_artifacts),
                "total_bytes": sum(path.stat().st_size for path in artifacts),
                "paths": [
                    str(path.relative_to(harness.recordings_path)) for path in artifacts
                ],
            }
            report["actual_soak_duration_s"] = actual_soak_s
            report["jack_xrun_rate_per_wall_second"] = round(xrun_rate, 6)
            report["resource_growth"] = growth

            assert actual_soak_s >= MIN_SOAK_SECONDS
            assert xrun_rate <= max_xruns_per_second, (
                f"raw JACK xrun rate {xrun_rate:.3f}/s exceeds the "
                f"{max_xruns_per_second:.3f}/s rehearsal ceiling"
            )
            assert reconnect_done and reconnect_event is not None
            assert completed_recording_cycles >= len(RECORDING_CYCLE_FRACTIONS)
            assert recording_restarts >= len(RECORDING_CYCLE_FRACTIONS)
            assert len(wav_artifacts) >= completed_recording_cycles * 2, (
                "real recorder did not produce at least one stem per client and cycle"
            )
            assert all(path.stat().st_size > 44 for path in wav_artifacts), (
                "real recorder produced an empty WAV stem"
            )

        # close() enforces zero live owned processes, zero occupied test ports,
        # and an unreachable private JACK server. Keep those exact thresholds
        # visible in the machine-readable report as well.
        assert not harness.cleanup_errors
        assert all(process.proc.poll() is not None for process in harness.processes)
        report["success"] = True
    except BaseException as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        report["wall_duration_s"] = round(time.monotonic() - test_started, 3)
        report["signal_summary"] = signal_summary
        report["resource_samples"] = resource_payloads
        report["recording_events"] = recording_events
        report["completed_recording_cycles"] = completed_recording_cycles
        report["recording_restarts"] = recording_restarts
        report["reconnect_event"] = reconnect_event
        report["cleanup"] = {
            "allowed_live_processes": 0,
            "allowed_occupied_ports": 0,
            "allowed_private_jack_servers": 0,
            "errors": list(harness.cleanup_errors),
            "process_exit_codes": {
                process.name: process.proc.poll() for process in harness.processes
            },
        }
        _write_report(report)
