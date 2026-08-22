"""Separated stems as mute/solo targets sitting beside the live mix.

The point of running stem separation during a jam is not to look at four new
files. It is to mute the record's vocal and sing it yourself, or drop the
record's drums and let the drummer in the room play. So stems are modelled the
way the musicians beside them already are: named targets with mute, solo, and a
level, sharing the mixer semantics the participant grid already uses (any solo
anywhere silences everything not soloed).

The honest boundary is what happens next. WebJam does not stream these into the
jam directly; the one host-owned route into a live Jamulus session is the
Shared Track, and it plays one file. So the bench can hand the Shared Track a
single stem directly, and for any other combination it bounces the audible
stems to one WAV first and hands over that. Both paths end in the same
host-controlled route that already exists, and both are refused unless the
audio libraries needed to do it honestly are present.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, replace
from pathlib import Path

MAX_STEMS = 12
MAX_BOUNCE_SECONDS = 20 * 60
_BOUNCE_SUFFIX = ".wav"

# Names Music AI stem workflows return, mapped to what a musician calls them
# and to what dropping one leaves you free to do. The purpose is stored as the
# bare action so it reads correctly both as a tooltip and inline in the mix
# line -- a songwriter should not have to hover to find out why they would
# mute the vocal.
_STEM_ROLES: dict[str, tuple[str, str]] = {
    "vocals": ("Vocals", "sing it yourself"),
    "vocal": ("Vocals", "sing it yourself"),
    "lead": ("Lead vocal", "sing the lead"),
    "backing": ("Backing vocals", "sing the harmonies"),
    "accompaniment": ("Backing", "everything except the vocal"),
    "accompaniments": ("Backing", "everything except the vocal"),
    "instrumental": ("Backing", "everything except the vocal"),
    "drums": ("Drums", "play the kit"),
    "bass": ("Bass", "play the bass"),
    "guitar": ("Guitar", "play the guitar part"),
    "piano": ("Piano", "play the keys"),
    "keys": ("Keys", "play the keys"),
    "strings": ("Strings", ""),
    "brass": ("Brass", ""),
    "woodwinds": ("Woodwinds", ""),
    "percussion": ("Percussion", ""),
    "synth": ("Synth", ""),
    "other": ("Other", ""),
}
# Backing is a description of the file, not something you do instead of it.
_DESCRIPTIVE_PURPOSES = frozenset({"everything except the vocal"})


class StemBenchError(RuntimeError):
    """A stem could not be prepared for the jam, with a reason to show."""


@dataclass(frozen=True, slots=True)
class StemTarget:
    """One separated stem, mixed like a musician in the room."""

    name: str
    path: str
    muted: bool = False
    solo: bool = False
    gain: float = 1.0

    @property
    def label(self) -> str:
        return _STEM_ROLES.get(self.key, (self.name.title(), ""))[0]

    @property
    def key(self) -> str:
        return re.sub(r"[^a-z]+", "", self.name.lower())

    @property
    def purpose(self) -> str:
        """What dropping this stem leaves you free to do, bare and reusable."""

        return _STEM_ROLES.get(self.key, ("", ""))[1]

    @property
    def hint(self) -> str:
        purpose = self.purpose
        if not purpose:
            return ""
        if purpose in _DESCRIPTIVE_PURPOSES:
            return purpose.capitalize() + "."
        return f"Mute this to {purpose}."

    @property
    def exists(self) -> bool:
        try:
            return bool(self.path) and Path(self.path).is_file()
        except OSError:
            return False


@dataclass(frozen=True, slots=True)
class StemMix:
    """What the bench would sound like right now, and how to say it."""

    audible: tuple[StemTarget, ...] = ()
    silent: tuple[StemTarget, ...] = ()
    soloing: bool = False

    @property
    def is_empty(self) -> bool:
        return not self.audible

    def describe(self) -> str:
        if not self.audible and not self.silent:
            return "No stems yet. Run Split stems on a file you own."
        if not self.audible:
            return "Everything is muted."
        names = ", ".join(item.label for item in self.audible)
        if not self.silent:
            return f"All stems: {names}"
        dropped = ", ".join(item.label for item in self.silent)
        verb = "Soloing" if self.soloing else "Playing"
        line = f"{verb} {names} · without {dropped}"
        # Say what the gap is for, so the point of muting is on screen.
        purposes = [
            item.purpose
            for item in self.silent
            if item.purpose and item.purpose not in _DESCRIPTIVE_PURPOSES
        ]
        return f"{line} — {purposes[0]}" if purposes else line


class StemBench:
    """The stem set for this session, mixed the way the room's faders work."""

    def __init__(self) -> None:
        self._stems: list[StemTarget] = []
        self._source_name = ""

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------
    def load(self, entries: list[tuple[str, str]], *, source_name: str = "") -> None:
        """Adopt ``(name, path)`` pairs from a finished separation."""

        stems: list[StemTarget] = []
        for name, path in entries:
            clean_name = str(name or "").strip() or "stem"
            clean_path = str(path or "").strip()
            if not clean_path or len(stems) >= MAX_STEMS:
                continue
            stems.append(StemTarget(name=clean_name, path=clean_path))
        self._stems = stems
        self._source_name = str(source_name or "")

    def clear(self) -> None:
        self._stems = []
        self._source_name = ""

    @property
    def source_name(self) -> str:
        return self._source_name

    @property
    def stems(self) -> tuple[StemTarget, ...]:
        return tuple(self._stems)

    @property
    def loaded(self) -> bool:
        return bool(self._stems)

    # ------------------------------------------------------------------
    # Mixing
    # ------------------------------------------------------------------
    def set_muted(self, name: str, muted: bool) -> bool:
        return self._update(name, muted=bool(muted))

    def set_solo(self, name: str, solo: bool) -> bool:
        return self._update(name, solo=bool(solo))

    def toggle_mute(self, name: str) -> bool:
        stem = self.stem(name)
        return False if stem is None else self.set_muted(name, not stem.muted)

    def toggle_solo(self, name: str) -> bool:
        stem = self.stem(name)
        return False if stem is None else self.set_solo(name, not stem.solo)

    def reset(self) -> None:
        self._stems = [
            replace(stem, muted=False, solo=False) for stem in self._stems
        ]

    def stem(self, name: str) -> StemTarget | None:
        target = str(name or "").strip().lower()
        for stem in self._stems:
            if stem.name.lower() == target:
                return stem
        return None

    def mix(self) -> StemMix:
        """Return the audible set, using the participant grid's solo rule."""

        soloing = any(stem.solo for stem in self._stems)
        audible: list[StemTarget] = []
        silent: list[StemTarget] = []
        for stem in self._stems:
            heard = stem.solo if soloing else not stem.muted
            (audible if heard else silent).append(stem)
        return StemMix(
            audible=tuple(audible), silent=tuple(silent), soloing=soloing
        )

    def sing_this_one(self) -> bool:
        """Mute the vocal stems so the room sings the song themselves.

        This is the move people actually run stem separation for, so it is one
        call rather than several fader gestures mid-take.
        """

        vocal_keys = {"vocals", "vocal", "lead", "backing"}
        if not any(stem.key in vocal_keys for stem in self._stems):
            return False
        self._stems = [
            replace(stem, muted=stem.key in vocal_keys, solo=False)
            for stem in self._stems
        ]
        return True

    # ------------------------------------------------------------------
    # Into the jam
    # ------------------------------------------------------------------
    def shared_track_plan(self) -> tuple[str, str]:
        """Return ``(path, note)`` for the Shared Track, or ``("", reason)``.

        One audible stem needs no processing. Several need a bounce, which the
        caller performs, because the one live route plays a single file.
        """

        mix = self.mix()
        if not self._stems:
            return "", "Run Split stems on a file you own first."
        if mix.is_empty:
            return "", "Every stem is muted, so there is nothing to send."
        missing = [stem.label for stem in mix.audible if not stem.exists]
        if missing:
            return "", f"WebJam cannot read {missing[0]} on disk any more."
        if len(mix.audible) == 1:
            return mix.audible[0].path, f"Sending {mix.audible[0].label}."
        return "", f"Mix {len(mix.audible)} stems into one file first."

    def bounce_name(self) -> str:
        """Return a stable, content-derived name for the current mix."""

        mix = self.mix()
        digest = hashlib.sha256(
            "|".join(sorted(stem.path for stem in mix.audible)).encode("utf-8")
        ).hexdigest()[:10]
        stem_names = "-".join(item.key or "stem" for item in mix.audible[:3])
        base = f"{Path(self._source_name).stem or 'stems'}-{stem_names}-{digest}"
        return re.sub(r"[^A-Za-z0-9._-]+", "_", base)[:96] + _BOUNCE_SUFFIX

    def _update(self, name: str, **changes: object) -> bool:
        target = str(name or "").strip().lower()
        for index, stem in enumerate(self._stems):
            if stem.name.lower() == target:
                self._stems[index] = replace(stem, **changes)
                return True
        return False


def bounce_stems(
    stems: list[StemTarget],
    destination: str | Path,
) -> str:
    """Mix the given stems down to one WAV so the Shared Track can play it.

    Refused rather than approximated when the audio libraries are unavailable,
    when the stems disagree about sample rate, or when the material is longer
    than a session would sensibly route. The sum is scaled by the number of
    stems so a four-stem mix cannot clip on its way into the room.
    """

    if not stems:
        raise StemBenchError("There are no audible stems to mix.")
    try:
        import numpy as np
        import soundfile as sf
    except ImportError as exc:  # pragma: no cover - exercised by a skip in tests
        raise StemBenchError(
            "WebJam cannot mix stems on this install. Send a single stem "
            "instead."
        ) from exc

    frames: list = []
    samplerate = 0
    channels = 0
    for stem in stems:
        try:
            data, rate = sf.read(stem.path, dtype="float32", always_2d=True)
        except (OSError, RuntimeError) as exc:
            raise StemBenchError(
                f"WebJam could not read {stem.label}."
            ) from exc
        if samplerate and rate != samplerate:
            raise StemBenchError(
                "These stems do not share a sample rate, so WebJam will not "
                "mix them."
            )
        if data.shape[0] > int(rate) * MAX_BOUNCE_SECONDS:
            raise StemBenchError("That material is too long to mix here.")
        samplerate = int(rate)
        channels = max(channels, int(data.shape[1]))
        frames.append(data * float(stem.gain))

    if not samplerate:
        raise StemBenchError("There are no audible stems to mix.")

    length = max(item.shape[0] for item in frames)
    mixed = np.zeros((length, channels), dtype="float32")
    for data in frames:
        padded = np.zeros((length, channels), dtype="float32")
        width = min(channels, data.shape[1])
        padded[: data.shape[0], :width] = data[:, :width]
        if data.shape[1] == 1 and channels > 1:
            padded[: data.shape[0], 1:] = data[:, :1]
        mixed += padded
    mixed /= float(len(frames))

    target = Path(destination)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(target), mixed, samplerate, subtype="PCM_24")
    except (OSError, RuntimeError) as exc:
        raise StemBenchError("WebJam could not save the mixed stems.") from exc
    return str(target)


__all__ = [
    "MAX_STEMS",
    "StemBench",
    "StemBenchError",
    "StemMix",
    "StemTarget",
    "bounce_stems",
]
