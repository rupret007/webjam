"""Real Jamulus/JACK boundary harness for Linux certification.

Nothing in this module replaces the production path.  A real JACK dummy
server clocks two official Jamulus clients and, for the Reference Track
companion, an optional third dedicated client.  This harness connects only to
their public JACK capture/playback ports.  Fixtures therefore enter before the
Jamulus client encoder and captures leave after its decoder.

The module imports :mod:`jack` lazily so ordinary developer and macOS/Windows
test runs remain dependency-free.  The opt-in integration job installs
``JACK-Client`` and provides the pinned official Jamulus 3.12.2 server/client
binaries.
"""
from __future__ import annotations

import base64
import json
import math
import os
import re
import shutil
import signal
import socket
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import numpy as np

SAMPLE_RATE = 48_000
JACK_BLOCK_SIZE = 128
FIXTURE_DURATION_S = 7.0
CAPTURE_TAIL_S = 2.0


class HarnessUnavailable(RuntimeError):
    """Raised when an opt-in host lacks a real required capability."""


class HarnessFailure(AssertionError):
    """Raised when the prepared real-audio path fails certification."""


@dataclass(frozen=True)
class ClientCapability:
    supported: bool
    detail: str
    output: str


@dataclass(frozen=True)
class SignalSpec:
    frequency_hz: float
    click_s: float
    tone_start_s: float
    tone_end_s: float
    amplitude: float = 0.24


@dataclass(frozen=True)
class SignalMetrics:
    sample_rate: int
    frames: int
    channels: int
    selected_channel: int
    click_frame: int
    dominant_hz: float
    tone_rms: float
    channel_tone_rms: tuple[float, ...]
    continuity_window_count: int
    dropout_window_count: int
    peak: float
    silence_rms: float
    expected_amplitude: float
    forbidden_amplitude: float
    cross_rejection_db: float


@dataclass(frozen=True)
class TransportResult:
    client_a_received: np.ndarray
    client_b_received: np.ndarray
    client_a_metrics: SignalMetrics
    client_b_metrics: SignalMetrics
    server_clients: tuple[dict[str, Any], ...]
    xrun_count: int


@dataclass(frozen=True)
class SilenceMetrics:
    """Measured silence at one JACK decoder boundary."""

    frames: int
    channels: int
    rms: float
    peak: float


@dataclass(frozen=True)
class ReferenceFaderProof:
    """Accepted local-mix commands for the three-client companion."""

    host_track_channel: int
    host_track_level: int
    bandmate_track_channel: int
    bandmate_track_level: int
    reference_zeroed_channels: tuple[int, ...]


@dataclass(frozen=True)
class ReferenceTrackTransportResult:
    """Synthetic evidence from a dedicated third Jamulus participant."""

    host_received: np.ndarray
    bandmate_received: np.ndarray
    reference_return_received: np.ndarray
    host_metrics: SignalMetrics | None
    bandmate_metrics: SignalMetrics | None
    host_silence: SilenceMetrics | None
    bandmate_silence: SilenceMetrics | None
    reference_return_silence: SilenceMetrics
    fader_proof: ReferenceFaderProof
    server_clients: tuple[dict[str, Any], ...]
    xrun_count: int


@dataclass(frozen=True)
class _ClientRpcEndpoint:
    name: str
    port: int
    secret: str


@dataclass(frozen=True)
class ProcessResourceSample:
    """One Linux /proc sample used by the opt-in longevity certification."""

    name: str
    pid: int
    rss_kib: int
    cpu_seconds: float
    fd_count: int


@dataclass(frozen=True)
class RecordedStemMetrics:
    """Measured evidence from one finalized Jamulus server WAV stem."""

    path: Path
    sample_rate: int
    frames: int
    channels: int
    duration_s: float
    click_frame: int
    dominant_hz: float
    tone_rms: float
    peak: float
    silence_rms: float
    expected_amplitude: float
    forbidden_amplitude: float
    cross_rejection_db: float


SPEC_A = SignalSpec(
    frequency_hz=440.0,
    click_s=1.0,
    tone_start_s=1.5,
    tone_end_s=4.0,
)
SPEC_B = SignalSpec(
    frequency_hz=660.0,
    click_s=1.25,
    tone_start_s=1.75,
    tone_end_s=4.25,
)
SPEC_REFERENCE_TRACK = SignalSpec(
    frequency_hz=880.0,
    click_s=0.85,
    tone_start_s=1.35,
    tone_end_s=4.35,
)


def make_fixture(
    spec: SignalSpec,
    *,
    sample_rate: int = SAMPLE_RATE,
    duration_s: float = FIXTURE_DURATION_S,
) -> np.ndarray:
    """Build a deterministic click, tone, and trailing-silence fixture."""
    if duration_s < spec.tone_end_s:
        raise ValueError(
            f"fixture duration {duration_s:.3f}s ends before the "
            f"{spec.tone_end_s:.3f}s tone"
        )
    frame_count = round(duration_s * sample_rate)
    samples = np.zeros(frame_count, dtype=np.float32)

    click_start = round(spec.click_s * sample_rate)
    click_frames = round(0.015 * sample_rate)
    click_t = np.arange(click_frames, dtype=np.float64) / sample_rate
    # A decaying high-frequency burst survives the codec more reliably than a
    # single-sample impulse while remaining unambiguous beside either tone.
    click = (
        0.42
        * np.exp(-click_t * 360.0)
        * np.sin(2.0 * math.pi * 3_100.0 * click_t)
    )
    samples[click_start : click_start + click_frames] = click.astype(np.float32)

    tone_start = round(spec.tone_start_s * sample_rate)
    tone_end = round(spec.tone_end_s * sample_rate)
    tone_t = np.arange(tone_end - tone_start, dtype=np.float64) / sample_rate
    tone = spec.amplitude * np.sin(
        2.0 * math.pi * spec.frequency_hz * tone_t
    )
    fade_frames = round(0.02 * sample_rate)
    fade = np.sin(np.linspace(0.0, math.pi / 2.0, fade_frames)) ** 2
    tone[:fade_frames] *= fade
    tone[-fade_frames:] *= fade[::-1]
    samples[tone_start:tone_end] = tone.astype(np.float32)
    return samples


def _rms(samples: np.ndarray) -> float:
    if not samples.size:
        return 0.0
    values = samples.astype(np.float64, copy=False)
    return float(np.sqrt(np.mean(values * values)))


def _spectrum(samples: np.ndarray, sample_rate: int) -> tuple[np.ndarray, np.ndarray]:
    values = samples.astype(np.float64, copy=False)
    window = np.hanning(len(values))
    spectrum = np.abs(np.fft.rfft(values * window))
    frequencies = np.fft.rfftfreq(len(values), 1.0 / sample_rate)
    return frequencies, spectrum


def _frequency_amplitude(
    frequencies: np.ndarray, spectrum: np.ndarray, frequency_hz: float
) -> float:
    index = int(np.argmin(np.abs(frequencies - frequency_hz)))
    return float(spectrum[index])


def _locate_click(
    samples: np.ndarray,
    *,
    search_start: int,
    search_end: int,
    sample_rate: int,
) -> int:
    """Locate the fixture's codec-safe 3.1 kHz timing burst."""
    if search_end <= search_start:
        raise HarnessFailure("capture is too short to locate the remote click")
    click_search = samples[search_start:search_end].astype(np.float64, copy=False)
    click_window = round(0.012 * sample_rate)
    carrier = np.exp(
        -2j
        * math.pi
        * 3_100.0
        * np.arange(len(click_search), dtype=np.float64)
        / sample_rate
    )
    demodulated = click_search * carrier
    integral = np.concatenate(
        (np.zeros(1, dtype=np.complex128), np.cumsum(demodulated))
    )
    click_energy = np.abs(integral[click_window:] - integral[:-click_window])
    if not click_energy.size:
        raise HarnessFailure("capture is too short for the click detector")
    return search_start + int(np.argmax(click_energy))


def analyze_received(
    capture: np.ndarray,
    *,
    expected: SignalSpec,
    forbidden_frequency_hz: float,
    sample_rate: int = SAMPLE_RATE,
) -> SignalMetrics:
    """Measure a decoded two-channel capture relative to its remote click."""
    if capture.ndim != 2:
        raise HarnessFailure(f"capture must be frames x channels, got {capture.shape}")
    if capture.shape[1] != 2:
        raise HarnessFailure(f"capture must expose two JACK outputs, got {capture.shape}")

    channel_energy = tuple(_rms(capture[:, index]) for index in range(2))
    selected_channel = int(np.argmax(channel_energy))
    selected = capture[:, selected_channel]

    search_start = max(0, round((expected.click_s - 0.2) * sample_rate))
    search_end = min(
        len(selected), round((expected.click_s + 1.2) * sample_rate)
    )
    # Locate the deliberately out-of-band 3.1 kHz click instead of the
    # loudest sample. Jamulus may apply enough monitor gain for the later
    # 440/660 Hz tone to exceed the click's peak, so a peak search can align
    # every downstream window to the tone by mistake. Quadrature demodulation
    # followed by a short moving sum rejects both fixture tones and survives
    # the codec's harmless phase/amplitude changes.
    click_frame = _locate_click(
        selected,
        search_start=search_start,
        search_end=search_end,
        sample_rate=sample_rate,
    )

    tone_offset_start = expected.tone_start_s - expected.click_s + 0.20
    tone_offset_end = expected.tone_end_s - expected.click_s - 0.20
    tone_start = click_frame + round(tone_offset_start * sample_rate)
    tone_end = click_frame + round(tone_offset_end * sample_rate)
    if tone_start < 0 or tone_end > len(selected) or tone_end <= tone_start:
        raise HarnessFailure(
            f"capture ended before the aligned tone window ({tone_start}:{tone_end})"
        )

    channel_tones = tuple(
        capture[tone_start:tone_end, index] for index in range(capture.shape[1])
    )
    channel_tone_rms = tuple(_rms(values) for values in channel_tones)
    selected_tone = channel_tones[selected_channel]
    continuity_window = round(0.020 * sample_rate)
    continuity_rms = tuple(
        _rms(selected_tone[index : index + continuity_window])
        for index in range(0, len(selected_tone) - continuity_window + 1, continuity_window)
    )
    tone_rms = _rms(selected_tone)
    dropout_window_count = sum(
        value < max(0.003, tone_rms * 0.30) for value in continuity_rms
    )
    frequencies, spectrum = _spectrum(selected_tone, sample_rate)
    audible = (frequencies >= 80.0) & (frequencies <= 4_000.0)
    dominant_hz = float(frequencies[audible][np.argmax(spectrum[audible])])
    expected_amplitude = _frequency_amplitude(
        frequencies, spectrum, expected.frequency_hz
    )
    forbidden_amplitude = _frequency_amplitude(
        frequencies, spectrum, forbidden_frequency_hz
    )
    cross_rejection_db = 20.0 * math.log10(
        max(expected_amplitude, 1e-15) / max(forbidden_amplitude, 1e-15)
    )

    silence_start = click_frame + round(
        (expected.tone_end_s - expected.click_s + 0.55) * sample_rate
    )
    silence_end = silence_start + round(0.75 * sample_rate)
    if silence_end > len(selected):
        raise HarnessFailure(
            f"capture ended before the aligned silence window ({silence_start}:{silence_end})"
        )

    return SignalMetrics(
        sample_rate=sample_rate,
        frames=len(capture),
        channels=capture.shape[1],
        selected_channel=selected_channel,
        click_frame=click_frame,
        dominant_hz=dominant_hz,
        tone_rms=tone_rms,
        channel_tone_rms=channel_tone_rms,
        continuity_window_count=len(continuity_rms),
        dropout_window_count=dropout_window_count,
        peak=float(np.max(np.abs(capture))),
        silence_rms=_rms(selected[silence_start:silence_end]),
        expected_amplitude=expected_amplitude,
        forbidden_amplitude=forbidden_amplitude,
        cross_rejection_db=cross_rejection_db,
    )


def assert_signal_metrics(
    metrics: SignalMetrics,
    *,
    expected: SignalSpec,
    expected_frames: int,
) -> None:
    """Apply the transport acceptance thresholds to measured PCM."""
    failures: list[str] = []
    if metrics.sample_rate != SAMPLE_RATE:
        failures.append(f"rate={metrics.sample_rate}, expected {SAMPLE_RATE}")
    if metrics.frames != expected_frames:
        failures.append(f"frames={metrics.frames}, expected {expected_frames}")
    if metrics.channels != 2:
        failures.append(f"channels={metrics.channels}, expected 2")
    if abs(metrics.dominant_hz - expected.frequency_hz) > 3.0:
        failures.append(
            f"dominant={metrics.dominant_hz:.2f} Hz, expected "
            f"{expected.frequency_hz:.2f} Hz"
        )
    if metrics.tone_rms < 0.008:
        failures.append(f"tone RMS too low: {metrics.tone_rms:.6f}")
    if min(metrics.channel_tone_rms) < 0.004:
        failures.append(
            f"one decoded output channel is silent: {metrics.channel_tone_rms}"
        )
    # A non-realtime JACK dummy graph on a shared CI VM can lose an isolated
    # 20 ms callback without invalidating the end-to-end codec result. Bound
    # that explicitly at 2% of the measured tone; larger loss is material.
    allowed_dropout_windows = max(
        1, math.ceil(metrics.continuity_window_count * 0.02)
    )
    if metrics.dropout_window_count > allowed_dropout_windows:
        failures.append(
            f"tone contains {metrics.dropout_window_count}/"
            f"{metrics.continuity_window_count} silent 20 ms windows; "
            f"limit is {allowed_dropout_windows}"
        )
    # JACK transports floating-point samples and permits a small overshoot;
    # the Jamulus decoder commonly emits 1.00003 at the click. Reject a
    # missing signal or material runaway gain, not that representation detail.
    if not 0.04 <= metrics.peak <= 1.01:
        failures.append(f"peak outside decoded PCM bounds: {metrics.peak:.6f}")
    silence_limit = max(0.003, metrics.tone_rms * 0.18)
    if metrics.silence_rms > silence_limit:
        failures.append(
            f"silence RMS {metrics.silence_rms:.6f} exceeds {silence_limit:.6f}"
        )
    if metrics.cross_rejection_db < 15.0:
        failures.append(
            f"cross rejection only {metrics.cross_rejection_db:.2f} dB"
        )
    if failures:
        raise HarnessFailure("; ".join(failures))


def analyze_silence(capture: np.ndarray) -> SilenceMetrics:
    """Measure a frames-by-stereo capture that is expected to be silent."""
    if capture.ndim != 2 or capture.shape[1] != 2:
        raise HarnessFailure(
            f"silence capture must be frames x 2 channels, got {capture.shape}"
        )
    return SilenceMetrics(
        frames=len(capture),
        channels=int(capture.shape[1]),
        rms=_rms(capture),
        peak=float(np.max(np.abs(capture))) if capture.size else 0.0,
    )


def assert_silence_metrics(metrics: SilenceMetrics) -> None:
    """Reject a material signal while tolerating bounded codec/JACK noise."""
    failures: list[str] = []
    if metrics.channels != 2:
        failures.append(f"channels={metrics.channels}, expected 2")
    if metrics.rms > 0.003:
        failures.append(f"silence RMS too high: {metrics.rms:.6f}")
    if metrics.peak > 0.04:
        failures.append(f"silence peak too high: {metrics.peak:.6f}")
    if failures:
        raise HarnessFailure("; ".join(failures))


def analyze_recorded_stem(
    path: str | Path,
    *,
    expected: SignalSpec,
    forbidden_frequency_hz: float,
) -> RecordedStemMetrics:
    """Measure a finalized real Jamulus server stem without changing it."""
    try:
        import soundfile as sf  # type: ignore
    except ImportError as exc:
        raise HarnessUnavailable(
            "soundfile is required to inspect real Jamulus recorder stems"
        ) from exc

    source_path = Path(path)
    try:
        audio, sample_rate = sf.read(
            str(source_path), dtype="float32", always_2d=True
        )
    except Exception as exc:  # noqa: BLE001
        raise HarnessFailure(f"cannot read server stem {source_path}: {exc}") from exc
    if audio.size == 0 or sample_rate <= 0:
        raise HarnessFailure(f"server stem is empty: {source_path}")

    channel_energy = tuple(_rms(audio[:, index]) for index in range(audio.shape[1]))
    selected = audio[:, int(np.argmax(channel_energy))]
    search_start = max(0, round((expected.click_s - 0.5) * sample_rate))
    search_end = min(
        len(selected), round((expected.click_s + 2.0) * sample_rate)
    )
    click_frame = _locate_click(
        selected,
        search_start=search_start,
        search_end=search_end,
        sample_rate=sample_rate,
    )
    tone_start = click_frame + round(
        (expected.tone_start_s - expected.click_s + 0.20) * sample_rate
    )
    tone_end = click_frame + round(
        (expected.tone_end_s - expected.click_s - 0.20) * sample_rate
    )
    silence_start = click_frame + round(
        (expected.tone_end_s - expected.click_s + 0.55) * sample_rate
    )
    silence_end = silence_start + round(0.60 * sample_rate)
    if (
        tone_start < 0
        or tone_end <= tone_start
        or silence_end > len(selected)
    ):
        raise HarnessFailure(
            f"server stem ended before its tone/silence evidence windows: "
            f"{source_path}"
        )

    tone = selected[tone_start:tone_end]
    frequencies, spectrum = _spectrum(tone, sample_rate)
    audible = (frequencies >= 80.0) & (frequencies <= 4_000.0)
    dominant_hz = float(frequencies[audible][np.argmax(spectrum[audible])])
    expected_amplitude = _frequency_amplitude(
        frequencies, spectrum, expected.frequency_hz
    )
    forbidden_amplitude = _frequency_amplitude(
        frequencies, spectrum, forbidden_frequency_hz
    )
    cross_rejection_db = 20.0 * math.log10(
        max(expected_amplitude, 1e-15) / max(forbidden_amplitude, 1e-15)
    )
    return RecordedStemMetrics(
        path=source_path,
        sample_rate=int(sample_rate),
        frames=len(audio),
        channels=int(audio.shape[1]),
        duration_s=len(audio) / sample_rate,
        click_frame=click_frame,
        dominant_hz=dominant_hz,
        tone_rms=_rms(tone),
        peak=float(np.max(np.abs(audio))),
        silence_rms=_rms(selected[silence_start:silence_end]),
        expected_amplitude=expected_amplitude,
        forbidden_amplitude=forbidden_amplitude,
        cross_rejection_db=cross_rejection_db,
    )


def assert_recorded_stem_metrics(
    metrics: RecordedStemMetrics,
    *,
    expected: SignalSpec,
    duration_bounds_s: tuple[float, float],
    expected_channels: int = 2,
) -> None:
    """Apply explicit format and signal gates to a server recorder stem."""
    failures: list[str] = []
    if metrics.sample_rate != SAMPLE_RATE:
        failures.append(f"rate={metrics.sample_rate}, expected {SAMPLE_RATE}")
    if metrics.channels != expected_channels:
        failures.append(
            f"channels={metrics.channels}, expected {expected_channels}"
        )
    minimum_s, maximum_s = duration_bounds_s
    if not minimum_s <= metrics.duration_s <= maximum_s:
        failures.append(
            f"duration={metrics.duration_s:.3f}s outside "
            f"{minimum_s:.3f}..{maximum_s:.3f}s"
        )
    if abs(metrics.dominant_hz - expected.frequency_hz) > 3.0:
        failures.append(
            f"dominant={metrics.dominant_hz:.2f} Hz, expected "
            f"{expected.frequency_hz:.2f} Hz"
        )
    if metrics.tone_rms < 0.008:
        failures.append(f"tone RMS too low: {metrics.tone_rms:.6f}")
    if not 0.04 <= metrics.peak <= 1.01:
        failures.append(f"peak outside decoded PCM bounds: {metrics.peak:.6f}")
    silence_limit = max(0.003, metrics.tone_rms * 0.18)
    if metrics.silence_rms > silence_limit:
        failures.append(
            f"silence RMS {metrics.silence_rms:.6f} exceeds {silence_limit:.6f}"
        )
    if metrics.cross_rejection_db < 15.0:
        failures.append(
            f"cross rejection only {metrics.cross_rejection_db:.2f} dB"
        )
    if failures:
        raise HarnessFailure(f"{metrics.path.name}: " + "; ".join(failures))


def _free_port(sock_type: int) -> int:
    with socket.socket(socket.AF_INET, sock_type) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _stop_process(proc: subprocess.Popen[bytes], timeout_s: float = 5.0) -> None:
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait(timeout=timeout_s)


def probe_client_capability(binary: str | Path) -> ClientCapability:
    """Execute client-only flags; do not infer support from misleading help.

    The official ``jamulus-headless`` 3.12.2 package is compiled with both
    ``headless`` and ``serveronly``.  Its help still prints client flags, but
    executing them exits with ``Client only option(s) ... used``.  The full
    official ``jamulus`` package accepts the same probe and may remain alive
    while waiting for JACK, which is sufficient evidence of client code.
    """
    binary = str(binary)
    if not binary or not Path(binary).is_file():
        return ClientCapability(False, f"client binary not found: {binary}", "")

    with tempfile.TemporaryDirectory(prefix="webjam-client-probe-") as temp:
        root = Path(temp)
        command = [
            binary,
            "--nogui",
            "--connect",
            "127.0.0.1:9",
            "--port",
            str(_free_port(socket.SOCK_DGRAM)),
            "--inifile",
            str(root / "probe.xml"),
            "--nojackconnect",
            "--mutemyown",
            "--clientname",
            "WebJamProbe",
        ]
        env = os.environ.copy()
        env.update(
            {
                "HOME": str(root),
                "XDG_CONFIG_HOME": str(root / "config"),
                "JACK_NO_START_SERVER": "1",
            }
        )
        proc = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
            start_new_session=True,
        )
        try:
            output_bytes, _ = proc.communicate(timeout=2.0)
        except subprocess.TimeoutExpired:
            _stop_process(proc)
            output_bytes = proc.stdout.read() if proc.stdout else b""
            return ClientCapability(
                True,
                "binary accepted client-only flags and remained in client startup",
                output_bytes.decode("utf-8", errors="replace"),
            )

    output = output_bytes.decode("utf-8", errors="replace")
    server_only_markers = (
        "Client only option(s)",
        "Only --server mode is supported in this build",
        "No initialization file support in headless server mode",
    )
    if any(marker in output for marker in server_only_markers):
        return ClientCapability(
            False,
            "official binary is a SERVER_ONLY build despite listing client flags",
            output,
        )
    # A client-capable binary can exit here because this probe intentionally
    # supplies no JACK server.  Reject only an explicit server-only path.
    return ClientCapability(
        True,
        f"binary executed client startup (exit {proc.returncode})",
        output,
    )


class RawRpc:
    """Small authenticated NDJSON JSON-RPC client for independent checks."""

    def __init__(self, port: int, secret: str) -> None:
        self._sock = socket.create_connection(("127.0.0.1", port), timeout=3.0)
        self._reader = self._sock.makefile("r", encoding="utf-8", newline="\n")
        self._next_id = 0
        response = self.call("jamulus/apiAuth", {"secret": secret})
        if response.get("result") != "ok":
            self.close()
            raise HarnessFailure(f"RPC authentication failed on {port}: {response}")

    def call(
        self, method: str, params: dict[str, Any], *, timeout_s: float = 4.0
    ) -> dict[str, Any]:
        self._next_id += 1
        request_id = self._next_id
        request = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params,
            }
        )
        self._sock.sendall((request + "\n").encode("utf-8"))
        self._sock.settimeout(0.5)
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            try:
                line = self._reader.readline()
            except (OSError, TimeoutError):
                continue
            if not line:
                break
            try:
                response = json.loads(line)
            except json.JSONDecodeError:
                continue
            if response.get("id") == request_id:
                return response
        raise HarnessFailure(f"RPC {method} timed out")

    def close(self) -> None:
        try:
            self._reader.close()
        except OSError:
            pass
        try:
            self._sock.close()
        except OSError:
            pass

    def __enter__(self) -> RawRpc:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


class ManagedProcess:
    """A process group with file-backed diagnostics and bounded cleanup."""

    def __init__(
        self,
        name: str,
        command: list[str],
        *,
        log_path: Path,
        env: dict[str, str],
    ) -> None:
        self.name = name
        self.command = tuple(command)
        self.log_path = log_path
        self._log = log_path.open("wb")
        self.proc = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=self._log,
            stderr=subprocess.STDOUT,
            env=env,
            start_new_session=True,
        )

    def tail(self, limit: int = 8_000) -> str:
        self._log.flush()
        try:
            data = self.log_path.read_bytes()[-limit:]
        except OSError:
            return "<log unavailable>"
        return data.decode("utf-8", errors="replace")

    def ensure_running(self) -> None:
        if self.proc.poll() is not None:
            raise HarnessFailure(
                f"{self.name} exited {self.proc.returncode}\n"
                f"command={self.command!r}\n{self.tail()}"
            )

    def resource_sample(self) -> ProcessResourceSample:
        """Read RSS, cumulative CPU, and descriptor count without psutil."""
        self.ensure_running()
        proc_root = Path("/proc") / str(self.proc.pid)
        try:
            status = (proc_root / "status").read_text(encoding="utf-8")
            stat = (proc_root / "stat").read_text(encoding="utf-8")
            fd_count = sum(1 for _ in (proc_root / "fd").iterdir())
        except OSError as exc:
            raise HarnessFailure(
                f"cannot sample {self.name} process resources: {exc}"
            ) from exc

        rss_match = re.search(r"^VmRSS:\s+(\d+)\s+kB$", status, re.MULTILINE)
        if rss_match is None:
            raise HarnessFailure(f"{self.name} /proc status has no VmRSS")
        # Field 3 begins after the final ')' around comm. utime/stime are
        # fields 14/15, therefore indexes 11/12 in this suffix.
        suffix = stat.rsplit(")", 1)[1].split()
        clock_ticks = float(os.sysconf("SC_CLK_TCK"))
        cpu_seconds = (int(suffix[11]) + int(suffix[12])) / clock_ticks
        return ProcessResourceSample(
            name=self.name,
            pid=self.proc.pid,
            rss_kib=int(rss_match.group(1)),
            cpu_seconds=cpu_seconds,
            fd_count=fd_count,
        )

    def stop(self) -> None:
        _stop_process(self.proc)
        self._log.close()


@dataclass
class _BoundaryRun:
    fixture_a: np.ndarray
    fixture_b: np.ndarray
    fixture_reference: np.ndarray
    capture_a: np.ndarray
    capture_b: np.ndarray
    capture_reference: np.ndarray
    cursor: int = 0
    done: threading.Event = field(default_factory=threading.Event)


class JackBoundary:
    """JACK ports immediately outside two musicians and an optional track."""

    def __init__(self, server_name: str) -> None:
        try:
            import jack  # type: ignore
        except ImportError as exc:
            raise HarnessUnavailable(
                "python JACK-Client is not installed in the prepared job"
            ) from exc

        self._jack = jack
        self.client = jack.Client(
            "webjam_cert_boundary",
            use_exact_name=True,
            no_start_server=True,
            servername=server_name,
        )
        if self.client.samplerate != SAMPLE_RATE:
            self.client.close()
            raise HarnessFailure(
                f"JACK rate is {self.client.samplerate}, expected {SAMPLE_RATE}"
            )
        if self.client.blocksize != JACK_BLOCK_SIZE:
            self.client.close()
            raise HarnessFailure(
                f"JACK block is {self.client.blocksize}, expected {JACK_BLOCK_SIZE}"
            )

        self._source_a = (
            self.client.outports.register("a_tx_left"),
            self.client.outports.register("a_tx_right"),
        )
        self._source_b = (
            self.client.outports.register("b_tx_left"),
            self.client.outports.register("b_tx_right"),
        )
        self._source_reference = (
            self.client.outports.register("reference_tx_left"),
            self.client.outports.register("reference_tx_right"),
        )
        self._sink_a = (
            self.client.inports.register("a_rx_left"),
            self.client.inports.register("a_rx_right"),
        )
        self._sink_b = (
            self.client.inports.register("b_rx_left"),
            self.client.inports.register("b_rx_right"),
        )
        self._sink_reference = (
            self.client.inports.register("reference_rx_left"),
            self.client.inports.register("reference_rx_right"),
        )
        self._run: _BoundaryRun | None = None
        self.xrun_count = 0
        self.last_run_xruns = 0

        @self.client.set_xrun_callback
        def on_xrun(_delayed_usecs: float) -> None:
            self.xrun_count += 1

        @self.client.set_process_callback
        def process(frames: int) -> None:
            for port in (
                *self._source_a,
                *self._source_b,
                *self._source_reference,
            ):
                port.get_array().fill(0.0)
            run = self._run
            if run is None or run.cursor >= len(run.capture_a):
                return
            count = min(frames, len(run.capture_a) - run.cursor)
            start = run.cursor
            end = start + count
            if start < len(run.fixture_a):
                source_end = min(end, len(run.fixture_a))
                source_count = source_end - start
                for port in self._source_a:
                    port.get_array()[:source_count] = run.fixture_a[start:source_end]
                for port in self._source_b:
                    port.get_array()[:source_count] = run.fixture_b[start:source_end]
                for port in self._source_reference:
                    port.get_array()[:source_count] = run.fixture_reference[
                        start:source_end
                    ]
            for channel, port in enumerate(self._sink_a):
                run.capture_a[start:end, channel] = port.get_array()[:count]
            for channel, port in enumerate(self._sink_b):
                run.capture_b[start:end, channel] = port.get_array()[:count]
            for channel, port in enumerate(self._sink_reference):
                run.capture_reference[start:end, channel] = port.get_array()[:count]
            run.cursor = end
            if end == len(run.capture_a):
                run.done.set()

        self.client.activate()

    def wait_for_jamulus_ports(
        self,
        jack_client_name: str,
        *,
        timeout_s: float,
        process: ManagedProcess,
    ) -> dict[str, Any]:
        expected_client = f"Jamulus {jack_client_name}"
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            process.ensure_running()
            ports = self.client.get_ports(
                name_pattern=f"^{re.escape(expected_client)}:", is_audio=True
            )
            by_shortname = {port.shortname: port for port in ports}
            expected = {"input left", "input right", "output left", "output right"}
            if expected.issubset(by_shortname):
                return by_shortname
            time.sleep(0.1)
        raise HarnessFailure(
            f"{process.name} did not expose {expected_client}'s four JACK ports\n"
            f"available={[port.name for port in self.client.get_ports(is_audio=True)]}\n"
            f"{process.tail()}"
        )

    def route_client(
        self,
        client_index: int,
        jamulus_ports: dict[str, Any],
        *,
        process: ManagedProcess,
        timeout_s: float = 2.0,
        poll_interval_s: float = 0.05,
    ) -> None:
        if client_index == 0:
            sources, sinks = self._source_a, self._sink_a
        elif client_index == 1:
            sources, sinks = self._source_b, self._sink_b
        elif client_index == 2:
            sources, sinks = self._source_reference, self._sink_reference
        else:
            raise ValueError(f"invalid JACK client index: {client_index}")
        routes = (
            (sources[0], jamulus_ports["input left"]),
            (sources[1], jamulus_ports["input right"]),
            (jamulus_ports["output left"], sinks[0]),
            (jamulus_ports["output right"], sinks[1]),
        )
        for source, target in routes:
            self.client.connect(source, target)

        # JACK applies graph mutations asynchronously. A successful connect()
        # can precede the graph snapshot that exposes the new route, so poll a
        # short bounded convergence window while still failing real defects.
        deadline = time.monotonic() + timeout_s
        missing = routes
        while True:
            process.ensure_running()
            missing = tuple(
                (source, target)
                for source, target in routes
                if target.name
                not in {
                    port.name
                    for port in self.client.get_all_connections(source)
                }
            )
            if not missing:
                return
            if time.monotonic() >= deadline:
                break
            time.sleep(poll_interval_s)
        missing_text = ", ".join(
            f"{source.name} -> {target.name}" for source, target in missing
        )
        raise HarnessFailure(
            f"JACK routes did not converge; missing={missing_text}\n{process.tail()}"
        )

    def run(
        self,
        fixture_a: np.ndarray,
        fixture_b: np.ndarray,
        *,
        tail_s: float = CAPTURE_TAIL_S,
    ) -> tuple[np.ndarray, np.ndarray]:
        silence = np.zeros_like(fixture_a)
        capture_a, capture_b, _capture_reference = self._run_three(
            fixture_a,
            fixture_b,
            silence,
            tail_s=tail_s,
        )
        return capture_a, capture_b

    def run_reference_track(
        self,
        fixture_reference: np.ndarray,
        *,
        tail_s: float = CAPTURE_TAIL_S,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Clock one track fixture while both musician sources stay silent."""
        silence = np.zeros_like(fixture_reference)
        return self._run_three(
            silence,
            silence.copy(),
            fixture_reference,
            tail_s=tail_s,
        )

    def _run_three(
        self,
        fixture_a: np.ndarray,
        fixture_b: np.ndarray,
        fixture_reference: np.ndarray,
        *,
        tail_s: float,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        shapes = {fixture_a.shape, fixture_b.shape, fixture_reference.shape}
        if len(shapes) != 1:
            raise HarnessFailure(
                "fixture shapes differ: "
                f"{fixture_a.shape}, {fixture_b.shape}, {fixture_reference.shape}"
            )
        total_frames = len(fixture_a) + round(tail_s * SAMPLE_RATE)
        run = _BoundaryRun(
            fixture_a=fixture_a,
            fixture_b=fixture_b,
            fixture_reference=fixture_reference,
            capture_a=np.zeros((total_frames, 2), dtype=np.float32),
            capture_b=np.zeros((total_frames, 2), dtype=np.float32),
            capture_reference=np.zeros((total_frames, 2), dtype=np.float32),
        )
        xrun_start = self.xrun_count
        self._run = run
        timeout_s = total_frames / SAMPLE_RATE + 5.0
        if not run.done.wait(timeout=timeout_s):
            raise HarnessFailure(
                f"JACK boundary captured {run.cursor}/{total_frames} frames in "
                f"{timeout_s:.1f}s"
            )
        self._run = None
        self.last_run_xruns = self.xrun_count - xrun_start
        return run.capture_a, run.capture_b, run.capture_reference

    def close(self) -> None:
        self._run = None
        try:
            self.client.deactivate()
        finally:
            self.client.close()


def _wait_for(
    predicate: Callable[[], Any],
    *,
    timeout_s: float,
    description: str,
    processes: tuple[ManagedProcess, ...] = (),
) -> Any:
    deadline = time.monotonic() + timeout_s
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        for process in processes:
            process.ensure_running()
        try:
            value = predicate()
            if value:
                return value
        except (OSError, HarnessFailure) as exc:
            last_error = exc
        time.sleep(0.15)
    diagnostics = "\n".join(
        f"--- {process.name} ---\n{process.tail()}" for process in processes
    )
    raise HarnessFailure(
        f"timed out waiting for {description}; last_error={last_error}\n{diagnostics}"
    )


def _rpc_result(port: int, secret: str, method: str) -> dict[str, Any] | None:
    try:
        with RawRpc(port, secret) as rpc:
            response = rpc.call(method, {})
    except (OSError, HarnessFailure):
        return None
    result = response.get("result")
    return result if isinstance(result, dict) else None


def _write_secret(path: Path, secret: str) -> None:
    path.write_text(secret + "\n", encoding="utf-8")
    path.chmod(0o600)


def _write_client_settings(path: Path, name: str) -> None:
    encoded = base64.b64encode(name.encode("utf-8")).decode("ascii")
    path.write_text(
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
        "<client>\n"
        f"  <name_base64>{encoded}</name_base64>\n"
        "  <audiochannels>1</audiochannels>\n"
        "  <newclientlevel>100</newclientlevel>\n"
        "  <enableaudioalerts>0</enableaudioalerts>\n"
        "</client>\n",
        encoding="utf-8",
    )


def _binary_version(binary: str) -> str:
    result = subprocess.run(
        [binary, "--version"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        timeout=5,
        text=True,
    )
    return result.stdout


class JamulusJackHarness:
    """Own a dummy JACK graph, server, musicians, and optional track client."""

    CLIENT_A_NAME = "WebJamCertA"
    CLIENT_B_NAME = "WebJamCertB"
    REFERENCE_TRACK_NAME = "WebJam Track"

    def __init__(
        self,
        server_binary: str,
        client_binary: str,
        *,
        include_reference_track: bool = False,
    ) -> None:
        self.server_binary = str(Path(server_binary).resolve())
        self.client_binary = str(Path(client_binary).resolve())
        self.include_reference_track = bool(include_reference_track)
        self._temp = tempfile.TemporaryDirectory(prefix="webjam-jack-cert-")
        self.root = Path(self._temp.name)
        self.server_name = f"webjam-cert-{os.getpid()}-{time.time_ns()}"
        self.processes: list[ManagedProcess] = []
        self.boundary: JackBoundary | None = None
        self.server_rpc: RawRpc | None = None
        self.server_process: ManagedProcess | None = None
        self.client_processes: list[ManagedProcess] = []
        self.client_rpc_endpoints: list[_ClientRpcEndpoint] = []
        self.recordings_path = self.root / "recordings"
        self.cleanup_errors: tuple[str, ...] = ()
        self.ports = {
            "server_udp": _free_port(socket.SOCK_DGRAM),
            "server_rpc": _free_port(socket.SOCK_STREAM),
            "client_a_udp": _free_port(socket.SOCK_DGRAM),
            "client_a_rpc": _free_port(socket.SOCK_STREAM),
            "client_b_udp": _free_port(socket.SOCK_DGRAM),
            "client_b_rpc": _free_port(socket.SOCK_STREAM),
        }
        if self.include_reference_track:
            self.ports.update(
                {
                    "reference_udp": _free_port(socket.SOCK_DGRAM),
                    "reference_rpc": _free_port(socket.SOCK_STREAM),
                }
            )
        if len(set(self.ports.values())) != len(self.ports):
            raise HarnessFailure(f"ephemeral port collision: {self.ports}")
        self._closed = False

    @classmethod
    def from_environment(
        cls, *, include_reference_track: bool = False
    ) -> JamulusJackHarness:
        server_binary = os.environ.get("WEBJAM_JAMULUS_BINARY", "")
        client_binary = os.environ.get("WEBJAM_JAMULUS_CLIENT_BINARY", "")
        if not server_binary:
            raise HarnessUnavailable("WEBJAM_JAMULUS_BINARY is not set")
        if not client_binary:
            capability = probe_client_capability(server_binary)
            if not capability.supported:
                raise HarnessUnavailable(
                    f"{capability.detail}. Install the checksum-pinned official "
                    "jamulus_3.12.2_ubuntu_amd64.deb and set "
                    "WEBJAM_JAMULUS_CLIENT_BINARY; probe output:\n"
                    f"{capability.output[-2_000:]}"
                )
            client_binary = server_binary
        capability = probe_client_capability(client_binary)
        if not capability.supported:
            raise HarnessUnavailable(
                f"configured client cannot run the production path: "
                f"{capability.detail}\n{capability.output[-2_000:]}"
            )
        return cls(
            server_binary,
            client_binary,
            include_reference_track=include_reference_track,
        )

    def _base_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env.update(
            {
                "JACK_DEFAULT_SERVER": self.server_name,
                "JACK_NO_START_SERVER": "1",
                "JACK_NO_AUDIO_RESERVATION": "1",
                "QT_QPA_PLATFORM": "offscreen",
            }
        )
        return env

    def _start_process(
        self, name: str, command: list[str], env: dict[str, str]
    ) -> ManagedProcess:
        process = ManagedProcess(
            name,
            command,
            log_path=self.root / f"{name}.log",
            env=env,
        )
        self.processes.append(process)
        return process

    @property
    def expected_client_names(self) -> tuple[str, ...]:
        names = [self.CLIENT_A_NAME, self.CLIENT_B_NAME]
        if self.include_reference_track:
            names.append(self.REFERENCE_TRACK_NAME)
        return tuple(names)

    def _client_specs(self) -> tuple[tuple[str, str, str, str], ...]:
        specs = [
            ("client-a", self.CLIENT_A_NAME, "client_a_udp", "client_a_rpc"),
            ("client-b", self.CLIENT_B_NAME, "client_b_udp", "client_b_rpc"),
        ]
        if self.include_reference_track:
            specs.append(
                (
                    "reference-track",
                    self.REFERENCE_TRACK_NAME,
                    "reference_udp",
                    "reference_rpc",
                )
            )
        return tuple(specs)

    def __enter__(self) -> JamulusJackHarness:
        try:
            self.start()
        except BaseException:
            self.close()
            raise
        return self

    def start(self) -> None:
        for label, binary in (
            ("server", self.server_binary),
            ("client", self.client_binary),
        ):
            if not Path(binary).is_file():
                raise HarnessUnavailable(f"{label} binary does not exist: {binary}")
            version = _binary_version(binary)
            if "Version 3.12.2" not in version:
                raise HarnessUnavailable(
                    f"{label} binary is not pinned Jamulus 3.12.2: {version.strip()}"
                )

        jackd = shutil.which("jackd")
        jack_lsp = shutil.which("jack_lsp")
        if not jackd or not jack_lsp:
            raise HarnessUnavailable("jackd2 and jack-tools are required")

        env = self._base_env()
        jackd_process = self._start_process(
            "jackd",
            [
                jackd,
                "--no-realtime",
                "--name",
                self.server_name,
                "-d",
                "dummy",
                "-r",
                str(SAMPLE_RATE),
                "-p",
                str(JACK_BLOCK_SIZE),
                "-C",
                "2",
                "-P",
                "2",
            ],
            env,
        )

        def jack_ready() -> bool:
            completed = subprocess.run(
                [jack_lsp],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
                timeout=2,
                check=False,
            )
            return completed.returncode == 0

        _wait_for(
            jack_ready,
            timeout_s=12.0,
            description="JACK dummy server",
            processes=(jackd_process,),
        )
        self.boundary = JackBoundary(self.server_name)

        server_secret = "webjam-cert-server-secret-0123456789"
        server_secret_path = self.root / "server.secret"
        _write_secret(server_secret_path, server_secret)
        self.recordings_path.mkdir()
        server_process = self._start_process(
            "jamulus-server",
            [
                self.server_binary,
                "--server",
                "--nogui",
                "--serverbindip",
                "127.0.0.1",
                "--port",
                str(self.ports["server_udp"]),
                "--jsonrpcport",
                str(self.ports["server_rpc"]),
                "--jsonrpcsecretfile",
                str(server_secret_path),
                "--recording",
                str(self.recordings_path),
                "--norecord",
            ],
            env,
        )
        server_mode = _wait_for(
            lambda: _rpc_result(
                self.ports["server_rpc"], server_secret, "jamulus/getMode"
            ),
            timeout_s=12.0,
            description="authenticated server RPC",
            processes=(server_process,),
        )
        if server_mode.get("mode") != "server":
            raise HarnessFailure(f"unexpected server RPC mode: {server_mode}")
        self.server_process = server_process
        self.server_rpc = RawRpc(self.ports["server_rpc"], server_secret)

        client_processes: list[ManagedProcess] = []
        client_rpc_endpoints: list[_ClientRpcEndpoint] = []
        for label, name, udp_key, rpc_key in self._client_specs():
            home = self.root / label
            home.mkdir()
            settings = home / "client.xml"
            _write_client_settings(settings, name)
            secret = f"webjam-cert-{label}-secret-0123456789"
            secret_path = home / "rpc.secret"
            _write_secret(secret_path, secret)
            client_env = env.copy()
            client_env.update(
                {
                    "HOME": str(home),
                    "XDG_CONFIG_HOME": str(home / "config"),
                }
            )
            process = self._start_process(
                label,
                [
                    self.client_binary,
                    # Pinned GUI builds acknowledge setFaderLevel but only
                    # apply it through CClientDlg. The three-client companion
                    # therefore runs Qt's real mixer handlers on the isolated
                    # offscreen platform. This is CI evidence, not a claim
                    # that the macOS GUI binary is a safe hidden backend.
                    *(() if self.include_reference_track else ("--nogui",)),
                    "--connect",
                    f"127.0.0.1:{self.ports['server_udp']}",
                    "--port",
                    str(self.ports[udp_key]),
                    "--inifile",
                    str(settings),
                    "--jsonrpcport",
                    str(self.ports[rpc_key]),
                    "--jsonrpcsecretfile",
                    str(secret_path),
                    "--nojackconnect",
                    *(() if self.include_reference_track else ("--mutemyown",)),
                    "--clientname",
                    name,
                ],
                client_env,
            )
            client_processes.append(process)
            mode = _wait_for(
                lambda port=self.ports[rpc_key], rpc_secret=secret: _rpc_result(
                    port, rpc_secret, "jamulus/getMode"
                ),
                timeout_s=15.0,
                description=f"authenticated {label} RPC",
                processes=(jackd_process, process),
            )
            if mode.get("mode") != "client":
                raise HarnessFailure(f"unexpected {label} RPC mode: {mode}")
            def connected_info(
                port: int = self.ports[rpc_key], rpc_secret: str = secret
            ) -> dict[str, Any] | None:
                info = _rpc_result(
                    port, rpc_secret, "jamulusclient/getClientInfo"
                )
                return info if info and info.get("connected") else None

            connected = _wait_for(
                connected_info,
                timeout_s=20.0,
                description=f"{label} network connection",
                processes=(server_process, process),
            )
            if not connected.get("connected"):
                raise HarnessFailure(f"{label} did not connect: {connected}")
            # Set and prove the profile through the authenticated client API.
            # --clientname intentionally controls only the process/JACK name.
            with RawRpc(self.ports[rpc_key], secret) as client_rpc:
                response = client_rpc.call(
                    "jamulusclient/setName", {"name": name}
                )
                if response.get("result") != "ok":
                    raise HarnessFailure(
                        f"{label} profile name was rejected: {response}"
                    )
            client_rpc_endpoints.append(
                _ClientRpcEndpoint(
                    name=name,
                    port=self.ports[rpc_key],
                    secret=secret,
                )
            )

        self.client_processes = client_processes
        self.client_rpc_endpoints = client_rpc_endpoints

        assert self.boundary is not None
        for index, (name, process) in enumerate(
            zip(self.expected_client_names, client_processes, strict=True)
        ):
            jamulus_ports = self.boundary.wait_for_jamulus_ports(
                name,
                timeout_s=12.0,
                process=process,
            )
            self.boundary.route_client(
                index,
                jamulus_ports,
                process=process,
            )

        expected_names = set(self.expected_client_names)

        def named_clients() -> tuple[dict[str, Any], ...] | None:
            current = self._connected_clients()
            if current is None:
                return None
            names = {client.get("name") for client in current}
            return current if names == expected_names else None

        clients = _wait_for(
            named_clients,
            timeout_s=20.0,
            description=f"{len(expected_names)} named Jamulus clients",
            processes=(server_process, *client_processes),
        )
        names = {client.get("name") for client in clients}
        if names != expected_names:
            raise HarnessFailure(f"server reported wrong client identities: {clients}")
        # Let every client jitter buffer reach steady state before the
        # deterministic source clock begins.
        time.sleep(1.0)

    def _server_roster(self) -> dict[str, Any] | None:
        if self.server_rpc is None:
            return None
        try:
            response = self.server_rpc.call("jamulusserver/getClients", {})
        except HarnessFailure:
            return None
        result = response.get("result", {})
        return result if isinstance(result, dict) else None

    def _connected_clients(self) -> tuple[dict[str, Any], ...] | None:
        result = self._server_roster()
        if result is None:
            return None
        clients = result.get("clients", [])
        expected_count = len(self.expected_client_names)
        if (
            result.get("connections") != expected_count
            or len(clients) != expected_count
        ):
            return None
        return tuple(clients)

    def run_transport(
        self,
        *,
        duration_s: float = FIXTURE_DURATION_S,
        tail_s: float = CAPTURE_TAIL_S,
    ) -> TransportResult:
        """Run one bounded signal/silence phase through both real codecs."""
        if self.boundary is None:
            raise HarnessFailure("harness was not started")
        if duration_s < max(SPEC_A.tone_end_s, SPEC_B.tone_end_s):
            raise ValueError("transport duration ends before the fixture tones")
        if tail_s < 1.5:
            raise ValueError("transport tail must include at least 1.5s of silence")
        fixture_a = make_fixture(SPEC_A, duration_s=duration_s)
        fixture_b = make_fixture(SPEC_B, duration_s=duration_s)
        capture_a, capture_b = self.boundary.run(
            fixture_a, fixture_b, tail_s=tail_s
        )
        # --mutemyown means A must decode B and B must decode A.  Any strong
        # local frequency is therefore cross-contamination.
        metrics_a = analyze_received(
            capture_a,
            expected=SPEC_B,
            forbidden_frequency_hz=SPEC_A.frequency_hz,
        )
        metrics_b = analyze_received(
            capture_b,
            expected=SPEC_A,
            forbidden_frequency_hz=SPEC_B.frequency_hz,
        )
        assert_signal_metrics(
            metrics_a,
            expected=SPEC_B,
            expected_frames=len(capture_a),
        )
        assert_signal_metrics(
            metrics_b,
            expected=SPEC_A,
            expected_frames=len(capture_b),
        )
        clients = self._connected_clients()
        if clients is None:
            raise HarnessFailure("server lost a client during deterministic capture")
        return TransportResult(
            client_a_received=capture_a,
            client_b_received=capture_b,
            client_a_metrics=metrics_a,
            client_b_metrics=metrics_b,
            server_clients=clients,
            xrun_count=self.boundary.last_run_xruns,
        )

    @staticmethod
    def _server_channel_id(row: dict[str, Any]) -> int:
        value = row.get("id")
        if isinstance(value, bool):
            raise HarnessFailure(f"invalid Jamulus channel id: {value!r}")
        try:
            channel_id = int(value)
        except (TypeError, ValueError) as exc:
            raise HarnessFailure(f"invalid Jamulus channel id: {value!r}") from exc
        if channel_id < 0:
            raise HarnessFailure(f"invalid Jamulus channel id: {channel_id}")
        return channel_id

    @staticmethod
    def _accepted_fader(
        rpc: RawRpc,
        *,
        channel_index: int,
        level: int,
        owner: str,
    ) -> None:
        response = rpc.call(
            "jamulusclient/setFaderLevel",
            {"channelIndex": channel_index, "level": level},
        )
        if response.get("result") != "ok":
            raise HarnessFailure(
                f"{owner} fader index {channel_index}={level} was not accepted: "
                f"{response}"
            )

    def _configure_listener_faders(
        self,
        endpoint: _ClientRpcEndpoint,
        *,
        track_level: int | None,
    ) -> tuple[int | None, tuple[int, ...]]:
        with RawRpc(endpoint.port, endpoint.secret) as rpc:
            response = rpc.call("jamulusclient/getClientList", {})
            result = response.get("result")
            if not isinstance(result, dict) or not isinstance(
                result.get("clients"), list
            ):
                raise HarnessFailure(
                    f"{endpoint.name} returned an invalid client list: {response}"
                )
            rows = result["clients"]
            if not rows:
                raise HarnessFailure(f"{endpoint.name} returned an empty client list")

            zeroed: list[int] = []
            seen_server_ids: set[int] = set()
            track_channel: int | None = None
            for channel_index, value in enumerate(rows):
                if not isinstance(value, dict):
                    raise HarnessFailure(
                        f"{endpoint.name} returned a malformed client row"
                    )
                server_id = self._server_channel_id(value)
                if server_id in seen_server_ids:
                    raise HarnessFailure(
                        f"{endpoint.name} returned a duplicate client id"
                    )
                seen_server_ids.add(server_id)
                self._accepted_fader(
                    rpc,
                    channel_index=channel_index,
                    level=0,
                    owner=endpoint.name,
                )
                zeroed.append(channel_index)
                if value.get("name") == self.REFERENCE_TRACK_NAME:
                    track_channel = channel_index

            if track_level is not None:
                if track_channel is None:
                    raise HarnessFailure(
                        f"{endpoint.name} cannot see {self.REFERENCE_TRACK_NAME}"
                    )
                self._accepted_fader(
                    rpc,
                    channel_index=track_channel,
                    level=track_level,
                    owner=endpoint.name,
                )
            return track_channel, tuple(sorted(set(zeroed)))

    def configure_reference_track_faders(
        self,
        *,
        host_level: int,
        bandmate_level: int,
    ) -> ReferenceFaderProof:
        """Set independent listener levels and zero the track return."""
        if not self.include_reference_track or len(self.client_rpc_endpoints) != 3:
            raise HarnessFailure("Reference Track client is not running")
        for label, level in (
            ("host", host_level),
            ("bandmate", bandmate_level),
        ):
            if isinstance(level, bool) or not 0 <= level <= 100:
                raise ValueError(f"{label} track level must be 0..100")

        def configure() -> ReferenceFaderProof:
            host_channel, _ = self._configure_listener_faders(
                self.client_rpc_endpoints[0],
                track_level=host_level,
            )
            bandmate_channel, _ = self._configure_listener_faders(
                self.client_rpc_endpoints[1],
                track_level=bandmate_level,
            )
            _unused, reference_zeroed = self._configure_listener_faders(
                self.client_rpc_endpoints[2],
                track_level=None,
            )
            if host_channel is None or bandmate_channel is None:
                raise HarnessFailure("Reference Track channel identity was not found")
            return ReferenceFaderProof(
                host_track_channel=host_channel,
                host_track_level=host_level,
                bandmate_track_channel=bandmate_channel,
                bandmate_track_level=bandmate_level,
                reference_zeroed_channels=reference_zeroed,
            )

        return _wait_for(
            configure,
            timeout_s=10.0,
            description="independent Reference Track faders and zero return mix",
            processes=tuple(self.processes),
        )

    def run_reference_track_transport(
        self,
        *,
        host_level: int = 100,
        bandmate_level: int = 100,
        duration_s: float = FIXTURE_DURATION_S,
        tail_s: float = CAPTURE_TAIL_S,
    ) -> ReferenceTrackTransportResult:
        """Route deterministic PCM through the dedicated third real client."""
        if self.boundary is None:
            raise HarnessFailure("harness was not started")
        if not self.include_reference_track:
            raise HarnessFailure("Reference Track client was not requested")
        if duration_s < SPEC_REFERENCE_TRACK.tone_end_s:
            raise ValueError("transport duration ends before the track fixture tone")
        if tail_s < 1.5:
            raise ValueError("transport tail must include at least 1.5s of silence")

        proof = self.configure_reference_track_faders(
            host_level=host_level,
            bandmate_level=bandmate_level,
        )
        fixture = make_fixture(SPEC_REFERENCE_TRACK, duration_s=duration_s)
        host_capture, bandmate_capture, reference_capture = (
            self.boundary.run_reference_track(fixture, tail_s=tail_s)
        )

        def listener_evidence(
            capture: np.ndarray,
            level: int,
        ) -> tuple[SignalMetrics | None, SilenceMetrics | None]:
            if level == 0:
                silence = analyze_silence(capture)
                assert_silence_metrics(silence)
                return None, silence
            metrics = analyze_received(
                capture,
                expected=SPEC_REFERENCE_TRACK,
                forbidden_frequency_hz=SPEC_A.frequency_hz,
            )
            assert_signal_metrics(
                metrics,
                expected=SPEC_REFERENCE_TRACK,
                expected_frames=len(capture),
            )
            return metrics, None

        host_metrics, host_silence = listener_evidence(
            host_capture,
            host_level,
        )
        bandmate_metrics, bandmate_silence = listener_evidence(
            bandmate_capture,
            bandmate_level,
        )
        reference_silence = analyze_silence(reference_capture)
        assert_silence_metrics(reference_silence)

        clients = self._connected_clients()
        if clients is None:
            raise HarnessFailure(
                "server lost a client during Reference Track capture"
            )
        return ReferenceTrackTransportResult(
            host_received=host_capture,
            bandmate_received=bandmate_capture,
            reference_return_received=reference_capture,
            host_metrics=host_metrics,
            bandmate_metrics=bandmate_metrics,
            host_silence=host_silence,
            bandmate_silence=bandmate_silence,
            reference_return_silence=reference_silence,
            fader_proof=proof,
            server_clients=clients,
            xrun_count=self.boundary.last_run_xruns,
        )

    def resource_snapshot(self) -> tuple[ProcessResourceSample, ...]:
        """Capture bounded Linux resource evidence for every owned process."""
        return tuple(process.resource_sample() for process in self.processes)

    def recorder_status(self) -> dict[str, Any]:
        if self.server_rpc is None:
            raise HarnessFailure("server RPC is unavailable")
        response = self.server_rpc.call("jamulusserver/getRecorderStatus", {})
        result = response.get("result")
        if not isinstance(result, dict):
            raise HarnessFailure(f"unexpected recorder status: {response}")
        return result

    def set_recording(self, enabled: bool) -> dict[str, Any]:
        """Arm/disarm the real multitrack recorder and prove its state."""
        if self.server_rpc is None:
            raise HarnessFailure("server RPC is unavailable")
        method = (
            "jamulusserver/startRecording"
            if enabled
            else "jamulusserver/stopRecording"
        )
        response = self.server_rpc.call(method, {})
        if response.get("result") != "acknowledged":
            raise HarnessFailure(f"{method} was not acknowledged: {response}")
        return _wait_for(
            lambda: (
                status
                if (status := self.recorder_status()).get("enabled") is enabled
                else None
            ),
            timeout_s=8.0,
            description=f"recorder enabled={enabled}",
            processes=tuple(self.processes),
        )

    def restart_recording(self) -> dict[str, Any]:
        """Start a new real Jamulus take while recording remains armed."""
        if self.server_rpc is None:
            raise HarnessFailure("server RPC is unavailable")
        response = self.server_rpc.call("jamulusserver/restartRecording", {})
        if response.get("result") != "acknowledged":
            raise HarnessFailure(f"recorder restart was not acknowledged: {response}")
        status = self.recorder_status()
        if not status.get("enabled"):
            raise HarnessFailure(f"recorder disarmed after restart: {status}")
        return status

    def recording_artifacts(self) -> tuple[Path, ...]:
        return tuple(
            sorted(path for path in self.recordings_path.rglob("*") if path.is_file())
        )

    def exercise_disconnect_reconnect(
        self,
        *,
        client_index: int = 1,
        timeout_s: float = 45.0,
    ) -> tuple[dict[str, Any], ...]:
        """Force a real server timeout, resume the client, and prove recovery."""
        expected_count = len(self.expected_client_names)
        if (
            self.server_process is None
            or len(self.client_processes) != expected_count
        ):
            raise HarnessFailure("clients are not running")
        try:
            client = self.client_processes[client_index]
        except IndexError as exc:
            raise ValueError(f"invalid client index: {client_index}") from exc

        paused = False
        try:
            os.killpg(client.proc.pid, signal.SIGSTOP)
            paused = True

            def one_client_absent() -> dict[str, Any] | None:
                roster = self._server_roster()
                if roster is None:
                    return None
                clients = roster.get("clients", [])
                remaining_count = expected_count - 1
                return (
                    roster
                    if (
                        roster.get("connections") == remaining_count
                        and len(clients) == remaining_count
                    )
                    else None
                )

            _wait_for(
                one_client_absent,
                timeout_s=timeout_s,
                description=f"server to time out {client.name}",
                processes=(self.server_process,),
            )
        finally:
            if paused:
                os.killpg(client.proc.pid, signal.SIGCONT)

        expected_names = set(self.expected_client_names)

        def all_named_clients() -> tuple[dict[str, Any], ...] | None:
            clients = self._connected_clients()
            if clients is None:
                return None
            return (
                clients
                if {entry.get("name") for entry in clients} == expected_names
                else None
            )

        recovered = _wait_for(
            all_named_clients,
            timeout_s=timeout_s,
            description=f"{client.name} to reconnect with its identity",
            processes=(self.server_process, client),
        )
        # The roster reports network recovery before both audio jitter/mix
        # paths have necessarily drained. Keep the real JACK graph clocking
        # zeroes long enough to exclude stale pre-timeout audio from the next
        # certified cross-contamination window.
        time.sleep(4.0)
        return recovered

    def _port_is_bindable(self, port: int, sock_type: int) -> bool:
        try:
            with socket.socket(socket.AF_INET, sock_type) as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self.server_rpc is not None:
            self.server_rpc.close()
            self.server_rpc = None
        if self.boundary is not None:
            self.boundary.close()
            self.boundary = None
        for process in reversed(self.processes):
            process.stop()

        cleanup_errors: list[str] = []
        for process in self.processes:
            if process.proc.poll() is None:
                cleanup_errors.append(f"process still alive: {process.name}")
        for name, port in self.ports.items():
            sock_type = socket.SOCK_STREAM if name.endswith("rpc") else socket.SOCK_DGRAM
            if not self._port_is_bindable(port, sock_type):
                cleanup_errors.append(f"port still occupied: {name}={port}")

        jack_lsp = shutil.which("jack_lsp")
        if jack_lsp:
            completed = subprocess.run(
                [jack_lsp],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=self._base_env(),
                timeout=3,
                check=False,
            )
            if completed.returncode == 0:
                cleanup_errors.append("private JACK server still accepts clients")
        self.cleanup_errors = tuple(cleanup_errors)
        self._temp.cleanup()
        if cleanup_errors:
            raise HarnessFailure("cleanup failed: " + "; ".join(cleanup_errors))

    def __exit__(self, exc_type: object, _exc: object, _tb: object) -> bool:
        try:
            self.close()
        except HarnessFailure:
            if exc_type is None:
                raise
        return False
