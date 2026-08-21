"""Explicit, short-lived audio helpers used by Band Check.

Nothing in this module starts on construction.  The UI must call ``start`` or
``play`` in response to a clear user action.  Meter samples are never retained.
Scratch audio is written through a bounded callback queue, finalized, reopened,
validated, and deleted by default when its owner closes.
"""
from __future__ import annotations

import hashlib
import queue
import shutil
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path

import numpy as np


class BandCheckAudioError(RuntimeError):
    pass


@dataclass(frozen=True)
class InputSnapshot:
    rms: float = 0.0
    peak: float = 0.0
    clipped: bool = False
    active: bool = False
    error: str = ""


class InputActivityProbe:
    """Non-recording local level probe; retains aggregate values only."""

    def __init__(
        self,
        *,
        device: int = -1,
        sample_rate: int = 48_000,
        blocksize: int = 0,
    ) -> None:
        self.device = None if int(device) < 0 else int(device)
        self.sample_rate = int(sample_rate)
        self.blocksize = max(0, int(blocksize))
        self._stream = None
        self._lock = threading.Lock()
        self._snapshot = InputSnapshot()

    def start(self) -> None:
        if self._stream is not None:
            return
        try:
            import sounddevice as sd  # type: ignore

            sd.check_input_settings(
                device=self.device,
                channels=1,
                samplerate=self.sample_rate,
                dtype="float32",
            )

            def callback(indata, _frames, _time_info, status) -> None:
                values = np.asarray(indata[:, 0], dtype=np.float32)
                rms = (
                    float(np.sqrt(np.mean(np.square(values))))
                    if len(values)
                    else 0.0
                )
                peak = float(np.max(np.abs(values))) if len(values) else 0.0
                error = str(status) if status else ""
                with self._lock:
                    previous = self._snapshot
                    self._snapshot = InputSnapshot(
                        rms=rms,
                        peak=max(peak, previous.peak * 0.92),
                        clipped=previous.clipped or peak >= 0.99,
                        active=True,
                        error=error,
                    )

            self._stream = sd.InputStream(
                device=self.device,
                channels=1,
                samplerate=self.sample_rate,
                blocksize=self.blocksize,
                dtype="float32",
                callback=callback,
            )
            with self._lock:
                self._snapshot = InputSnapshot(active=True)
            self._stream.start()
        except Exception as exc:
            self.stop()
            with self._lock:
                self._snapshot = InputSnapshot(error=str(exc))
            raise BandCheckAudioError(f"The selected input could not be opened: {exc}") from exc

    def snapshot(self) -> InputSnapshot:
        with self._lock:
            return self._snapshot

    def stop(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
            finally:
                stream.close()
        with self._lock:
            current = self._snapshot
            self._snapshot = InputSnapshot(
                rms=current.rms,
                peak=current.peak,
                clipped=current.clipped,
                active=False,
                error=current.error,
            )


@dataclass(frozen=True)
class ScratchRecordingEvidence:
    path: Path | None
    valid: bool
    duration_s: float
    sample_rate: int
    channels: int
    frame_count: int
    subtype: str
    peak: float
    has_signal: bool
    waveform_peaks: tuple[float, ...]
    error: str = ""


@dataclass(frozen=True)
class StudioCheckEvidence:
    valid: bool
    rendered_frames: int = 0
    source_unchanged: bool = False
    error: str = ""


def validate_studio_scratch(path: str | Path) -> StudioCheckEvidence:
    """Exercise the production Studio transport/mixer without an output device."""

    source = Path(path)
    try:
        import soundfile as sf  # type: ignore

        from core.take_library import TakeInfo, TrackInfo
        from core.take_player import TakePlayer

        before = hashlib.sha256(source.read_bytes()).hexdigest()
        info = sf.info(str(source))

        samples, _ = sf.read(str(source), dtype="float32", always_2d=True)
        peak_frame = (
            int(np.argmax(np.abs(samples[:, 0]))) if len(samples) else 0
        )

        class InspectionSink:
            def __init__(self) -> None:
                self.pull = None
                self.rendered_frames = 0
                self.stopped = False
                self.blocks: list[np.ndarray] = []

            def start(self, _sample_rate, blocksize, pull) -> None:
                self.pull = pull
                block = pull(blocksize)
                self.blocks.append(np.asarray(block))
                self.rendered_frames += len(block)

            def stop(self) -> None:
                self.stopped = True

        sink = InspectionSink()
        player = TakePlayer(
            samplerate=int(info.samplerate),
            blocksize=256,
            sink=sink,
        )
        take = TakeInfo(
            path=source.parent,
            name="Band Check",
            project_samplerate=int(info.samplerate),
            tracks=[
                TrackInfo(
                    path=source,
                    name="Input test",
                    duration_s=(
                        int(info.frames) / int(info.samplerate)
                        if info.samplerate
                        else 0.0
                    ),
                    samplerate=int(info.samplerate),
                    source="local_isolated",
                ),
                TrackInfo(
                    path=source,
                    name="Input test copy",
                    duration_s=(
                        int(info.frames) / int(info.samplerate)
                        if info.samplerate
                        else 0.0
                    ),
                    samplerate=int(info.samplerate),
                    source="local_isolated",
                ),
            ],
        )
        player.load(take)
        player.set_gain(0, 0.75)
        player.set_pan(0, -1.0)
        player.set_gain(1, 0.75)
        player.set_pan(1, 1.0)
        player.set_solo(0, True)
        player.seek(max(0.0, peak_frame / int(info.samplerate) - 0.002))
        player.play()
        if sink.pull is None:
            raise RuntimeError("Studio playback did not expose its transport")
        solo = sink.blocks[-1]
        if not len(solo) or float(np.max(np.abs(solo[:, 0]))) <= 1e-5:
            raise RuntimeError("Studio playback produced no test signal")
        if float(np.max(np.abs(solo[:, 1]))) > 1e-5:
            raise RuntimeError("Studio solo or pan did not isolate the left track")
        player.set_solo(0, False)
        player.set_muted(0, True)
        player.seek(max(0.0, peak_frame / int(info.samplerate) - 0.002))
        right = sink.pull(256)
        sink.rendered_frames += len(right)
        if float(np.max(np.abs(right[:, 0]))) > 1e-5:
            raise RuntimeError("Studio mute did not silence the left track")
        if float(np.max(np.abs(right[:, 1]))) <= 1e-5:
            raise RuntimeError("Studio pan did not preserve the right track")
        player.set_muted(0, False)
        player.set_gain(1, 0.0)
        player.set_muted(0, True)
        player.seek(max(0.0, peak_frame / int(info.samplerate) - 0.002))
        silent = sink.pull(256)
        sink.rendered_frames += len(silent)
        if np.any(np.abs(silent) > 1e-7):
            raise RuntimeError("Studio gain did not silence the selected track")
        player.stop()
        if not sink.stopped:
            raise RuntimeError("Studio did not release its output sink")
        after = hashlib.sha256(source.read_bytes()).hexdigest()
        unchanged = before == after
        if not unchanged:
            raise RuntimeError("Studio changed the source recording")
        return StudioCheckEvidence(
            valid=True,
            rendered_frames=sink.rendered_frames,
            source_unchanged=True,
        )
    except Exception as exc:  # noqa: BLE001
        return StudioCheckEvidence(valid=False, error=str(exc))


class ScratchRecorder:
    """Five-second faithful scratch writer at the selected hardware boundary."""

    QUEUE_BLOCKS = 256
    JOIN_TIMEOUT_S = 8.0
    DELETE_JOIN_TIMEOUT_S = 0.5

    def __init__(
        self,
        root: str | Path,
        *,
        device: int = -1,
        sample_rate: int = 48_000,
        blocksize: int = 0,
        target_duration_s: float = 5.0,
    ) -> None:
        self.root = Path(root).expanduser()
        self.device = None if int(device) < 0 else int(device)
        self.sample_rate = int(sample_rate)
        self.blocksize = max(0, int(blocksize))
        self.target_duration_s = float(target_duration_s)
        self._directory: Path | None = None
        self._part: Path | None = None
        self._final: Path | None = None
        self._stream = None
        self._writer = None
        self._queue: queue.Queue = queue.Queue(maxsize=self.QUEUE_BLOCKS)
        self._thread: threading.Thread | None = None
        self._stop_requested = False
        self._dropped_blocks = 0
        self._writer_error = ""

    @property
    def path(self) -> Path | None:
        return self._final

    def start(self) -> None:
        if self._stream is not None or self._thread is not None:
            raise BandCheckAudioError("The test recording is already running.")
        try:
            import sounddevice as sd  # type: ignore
            import soundfile as sf  # type: ignore

            self.root.mkdir(parents=True, exist_ok=True)
            self._directory = self.root / f".webjam-band-check-{uuid.uuid4().hex}"
            self._directory.mkdir(mode=0o700)
            self._part = self._directory / "input-test.wav.part"
            self._final = self._directory / "input-test.wav"
            self._writer = sf.SoundFile(
                str(self._part),
                mode="w",
                samplerate=self.sample_rate,
                channels=1,
                format="WAV",
                subtype="PCM_24",
            )
            sd.check_input_settings(
                device=self.device,
                channels=1,
                samplerate=self.sample_rate,
                dtype="float32",
            )

            def callback(indata, _frames, _time_info, status) -> None:
                if status and not self._writer_error:
                    self._writer_error = f"Audio device reported: {status}"
                try:
                    self._queue.put_nowait(
                        np.asarray(indata[:, 0], dtype=np.float32).copy()
                    )
                except queue.Full:
                    self._dropped_blocks += 1

            self._stream = sd.InputStream(
                device=self.device,
                channels=1,
                samplerate=self.sample_rate,
                blocksize=self.blocksize,
                dtype="float32",
                callback=callback,
            )
            self._thread = threading.Thread(
                target=self._write_loop,
                daemon=True,
                name="band-check-scratch-writer",
            )
            self._thread.start()
            self._stream.start()
        except Exception as exc:
            self.delete()
            raise BandCheckAudioError(f"The test recording could not start: {exc}") from exc

    def _write_loop(self) -> None:
        while True:
            try:
                block = self._queue.get(timeout=0.2)
            except queue.Empty:
                if self._stop_requested:
                    break
                continue
            if block is None:
                break
            try:
                self._writer.write(block)
            except Exception as exc:  # noqa: BLE001
                if not self._writer_error:
                    self._writer_error = f"Audio file write failed: {exc}"

    def stop_and_validate(self) -> ScratchRecordingEvidence:
        if self._stream is None:
            return self._failed("The test recording was not running.")
        try:
            self._stream.stop()
            self._stream.close()
        except Exception as exc:  # noqa: BLE001
            if not self._writer_error:
                self._writer_error = f"The input did not close cleanly: {exc}"
        finally:
            self._stream = None
        self._stop_requested = True
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        if self._thread is not None:
            self._thread.join(timeout=self.JOIN_TIMEOUT_S)
            if self._thread.is_alive():
                # The writer still owns its file handle. Keep the private file
                # and return; close/delete would race the writer.
                return self._failed("The test recording writer did not finish in time.")
            self._thread = None
        try:
            self._writer.flush()
            self._writer.close()
            self._writer = None
            if self._part is None or self._final is None:
                return self._failed("The test recording path was lost.")
            self._part.replace(self._final)
        except Exception as exc:  # noqa: BLE001
            return self._failed(f"The test recording could not be finalized: {exc}")
        return self._validate_final()

    def _validate_final(self) -> ScratchRecordingEvidence:
        try:
            import soundfile as sf  # type: ignore

            if self._final is None:
                return self._failed("The finalized test file is missing.")
            info = sf.info(str(self._final))
            frame_count = int(info.frames)
            duration_s = frame_count / int(info.samplerate) if info.samplerate else 0.0
            peaks: list[float] = []
            peak = 0.0
            # Exact bounded streaming scan. This also proves the file can be
            # reopened/read and produces useful waveform evidence.
            bucket_frames = max(1, frame_count // 96)
            carry = np.empty(0, dtype=np.float32)
            with sf.SoundFile(str(self._final), mode="r") as source:
                while True:
                    data = source.read(32_768, dtype="float32", always_2d=True)
                    if not len(data):
                        break
                    mono = np.asarray(data[:, 0], dtype=np.float32)
                    if carry.size:
                        mono = np.concatenate((carry, mono))
                        carry = np.empty(0, dtype=np.float32)
                    while mono.size >= bucket_frames:
                        bucket = mono[:bucket_frames]
                        value = float(np.max(np.abs(bucket))) if bucket.size else 0.0
                        peaks.append(value)
                        peak = max(peak, value)
                        mono = mono[bucket_frames:]
                    carry = mono
                if carry.size:
                    value = float(np.max(np.abs(carry)))
                    peaks.append(value)
                    peak = max(peak, value)
            expected_min = max(0.5, self.target_duration_s * 0.70)
            expected_max = self.target_duration_s * 1.60
            valid = (
                int(info.samplerate) == self.sample_rate
                and int(info.channels) == 1
                and expected_min <= duration_s <= expected_max
                and frame_count > 0
                and not self._writer_error
                and self._dropped_blocks == 0
            )
            error = self._writer_error
            if self._dropped_blocks:
                error = f"{self._dropped_blocks} audio blocks were dropped."
            if not valid and not error:
                error = "The test file's duration or format did not match the selected setup."
            return ScratchRecordingEvidence(
                path=self._final,
                valid=valid,
                duration_s=duration_s,
                sample_rate=int(info.samplerate),
                channels=int(info.channels),
                frame_count=frame_count,
                subtype=str(info.subtype),
                peak=peak,
                has_signal=peak >= 0.005,
                waveform_peaks=tuple(peaks[:128]),
                error=error,
            )
        except Exception as exc:  # noqa: BLE001
            return self._failed(f"The test recording could not be reopened: {exc}")

    def play(self, *, output_device_name: str = "") -> float:
        """Play the scratch recording quietly; returns its duration in seconds."""

        if self._final is None or not self._final.is_file():
            raise BandCheckAudioError("There is no test recording to play.")
        try:
            import sounddevice as sd  # type: ignore
            import soundfile as sf  # type: ignore

            data, sample_rate = sf.read(str(self._final), dtype="float32", always_2d=True)
            peak = float(np.max(np.abs(data))) if data.size else 0.0
            gain = min(0.5, 0.20 / peak) if peak > 0 else 0.5
            device = _output_device_index(sd, output_device_name)
            sd.play(data * gain, int(sample_rate), device=device, blocking=False)
            return len(data) / int(sample_rate) if sample_rate else 0.0
        except Exception as exc:
            raise BandCheckAudioError(f"The test recording could not be played: {exc}") from exc

    def delete(self, *, wait_timeout: float | None = DELETE_JOIN_TIMEOUT_S) -> bool:
        """Stop playback/capture and delete only after the writer releases it.

        Returns ``False`` when a writer still owns the private scratch files;
        callers may retry from a background cleanup thread. ``None`` waits
        without a timeout and is intended only for that background cleanup.
        """
        try:
            import sounddevice as sd  # type: ignore

            sd.stop()
        except Exception:  # noqa: BLE001
            pass
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:  # noqa: BLE001
                pass
            self._stream = None
        self._stop_requested = True
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        if self._thread is not None:
            self._thread.join(timeout=wait_timeout)
            if self._thread.is_alive():
                return False
            self._thread = None
        if self._writer is not None:
            try:
                self._writer.close()
            except Exception:  # noqa: BLE001
                pass
            self._writer = None
        if self._directory is not None:
            shutil.rmtree(self._directory, ignore_errors=True)
            self._directory = None
        self._part = None
        self._final = None
        return True

    def _failed(self, message: str) -> ScratchRecordingEvidence:
        return ScratchRecordingEvidence(
            path=self._final,
            valid=False,
            duration_s=0.0,
            sample_rate=self.sample_rate,
            channels=1,
            frame_count=0,
            subtype="PCM_24",
            peak=0.0,
            has_signal=False,
            waveform_peaks=(),
            error=message,
        )


class HeadphoneTonePlayer:
    """Conservative stereo-or-mono tone played only after an explicit action."""

    SAMPLE_RATE = 48_000
    LEVEL = 0.03  # roughly -30.5 dBFS

    def play(self, *, output_device_name: str = "") -> HeadphoneTestEvidence:
        try:
            import sounddevice as sd  # type: ignore

            side_s = 0.65
            gap_s = 0.20
            side_frames = int(side_s * self.SAMPLE_RATE)
            gap_frames = int(gap_s * self.SAMPLE_RATE)
            times = np.arange(side_frames, dtype=np.float32) / self.SAMPLE_RATE
            left = np.sin(2 * np.pi * 440.0 * times) * self.LEVEL
            right = np.sin(2 * np.pi * 660.0 * times) * self.LEVEL
            ramp_frames = int(0.03 * self.SAMPLE_RATE)
            ramp = np.linspace(0.0, 1.0, ramp_frames, dtype=np.float32)
            envelope = np.ones(side_frames, dtype=np.float32)
            envelope[:ramp_frames] = ramp
            envelope[-ramp_frames:] = ramp[::-1]
            left *= envelope
            right *= envelope
            device = _output_device_index(sd, output_device_name)
            if str(output_device_name or "").strip() and device is None:
                raise BandCheckAudioError(
                    "The selected Studio output is not connected."
                )
            channels = 2
            try:
                raw = sd.query_devices(device, "output")
                if isinstance(raw, dict):
                    channels = (
                        2
                        if int(raw.get("max_output_channels", 0) or 0) >= 2
                        else 1
                    )
            except Exception:  # noqa: BLE001
                pass
            output = np.zeros(
                (side_frames * 2 + gap_frames, channels),
                dtype=np.float32,
            )
            output[:side_frames, 0] = left
            output[side_frames + gap_frames :, channels - 1] = right
            sd.check_output_settings(
                device=device,
                channels=channels,
                samplerate=self.SAMPLE_RATE,
                dtype="float32",
            )
            sd.play(output, self.SAMPLE_RATE, device=device, blocking=False)
            return HeadphoneTestEvidence(
                duration_s=len(output) / self.SAMPLE_RATE,
                channels=channels,
            )
        except Exception as exc:
            raise BandCheckAudioError(f"The headphone test could not play: {exc}") from exc

    @staticmethod
    def stop() -> None:
        try:
            import sounddevice as sd  # type: ignore

            sd.stop()
        except Exception:  # noqa: BLE001
            pass


@dataclass(frozen=True)
class HeadphoneTestEvidence:
    duration_s: float
    channels: int


def _output_device_index(sounddevice, name: str):
    target = str(name or "").strip().casefold()
    if not target:
        return None
    try:
        devices = sounddevice.query_devices()
    except Exception:  # noqa: BLE001
        return None
    for index, device in enumerate(devices):
        if not isinstance(device, dict):
            continue
        if int(device.get("max_output_channels", 0) or 0) < 1:
            continue
        if str(device.get("name", "")).strip().casefold() == target:
            return int(device.get("index", index))
    return None
