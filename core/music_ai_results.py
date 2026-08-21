"""Turn a finished Music AI job into something the live session can show.

A job's ``result`` is a flat map of output name to URL. What those outputs
*mean* depends on the workflow, and the workflow belongs to the account, so
this module reads what actually came back instead of asserting a schema.

That shapes the honesty rule here: a value is reported as a detected key, tempo,
chord list, or section list only when it is genuinely present in the payload.
Anything else stays an artifact with the name Music AI gave it. WebJam would
rather show "beat_map.json" than invent a tempo.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import unquote, urlsplit

from core.music_ai_catalog import (
    RESULT_AUDIO_FILE,
    RESULT_AUDIO_SET,
    SongToolCapability,
)
from core.music_ai_client import (
    MusicAIJob,
    MusicAIRequestError,
    MusicAITransport,
    validate_music_ai_url,
)

AUDIO_SUFFIXES = frozenset(
    {".wav", ".mp3", ".flac", ".m4a", ".ogg", ".oga", ".aac", ".aif", ".aiff"}
)
TEXT_SUFFIXES = frozenset({".json", ".txt", ".lrc", ".srt", ".vtt", ".csv"})

ARTIFACT_AUDIO = "audio"
ARTIFACT_TEXT = "text"
ARTIFACT_DATA = "data"

_MAX_ARTIFACTS = 24
_MAX_DOWNLOAD_BYTES = 256 * 1024 * 1024
_MAX_TEXT_PREVIEW = 8000

_KEY_FIELDS = ("key", "musicalkey", "detectedkey", "tonality", "keysignature")
_TEMPO_FIELDS = ("bpm", "tempo", "detectedbpm", "estimatedbpm")
_CHORD_FIELDS = ("chords", "chordmap", "chordsequence", "harmony")
_LYRIC_FIELDS = ("lyrics", "text", "transcript", "transcription")
_SECTION_FIELDS = ("sections", "segments", "structure", "arrangement")

_KEY_TEXT_RE = re.compile(
    r"^([A-G][#b]?)\s*(maj(?:or)?|min(?:or)?|m)?$", re.IGNORECASE
)


@dataclass(frozen=True, slots=True)
class SongArtifact:
    """One output file from a job, before or after it is downloaded."""

    name: str
    kind: str
    url: str = ""
    local_path: str = ""
    text: str = ""

    @property
    def filename(self) -> str:
        if self.local_path:
            return Path(self.local_path).name
        return _filename_from_url(self.url) or f"{self.name}"

    @property
    def downloaded(self) -> bool:
        return bool(self.local_path)


@dataclass(frozen=True, slots=True)
class SongToolRun:
    """One completed Song tools run, ready to attach to the session."""

    verb_key: str
    label: str
    workflow_slug: str
    job_id: str
    source_name: str
    artifacts: tuple[SongArtifact, ...] = ()
    detected_key: str = ""
    detected_tempo: str = ""
    detected_sections: tuple[str, ...] = ()
    chord_symbols: tuple[str, ...] = ()
    lyrics_text: str = ""
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def audio_artifacts(self) -> tuple[SongArtifact, ...]:
        return tuple(item for item in self.artifacts if item.kind == ARTIFACT_AUDIO)

    @property
    def has_detected_facts(self) -> bool:
        return bool(
            self.detected_key
            or self.detected_tempo
            or self.detected_sections
            or self.chord_symbols
            or self.lyrics_text
        )

    def summary_line(self) -> str:
        """Return a short line that claims only what the job returned."""

        parts: list[str] = []
        if self.detected_key:
            parts.append(f"key {self.detected_key}")
        if self.detected_tempo:
            parts.append(f"{self.detected_tempo} BPM")
        if self.chord_symbols:
            parts.append(f"{len(self.chord_symbols)} chords")
        if self.detected_sections:
            parts.append(f"{len(self.detected_sections)} sections")
        audio = self.audio_artifacts
        if audio:
            parts.append(f"{len(audio)} audio files")
        if not parts:
            parts.append(f"{len(self.artifacts)} files")
        return f"{self.label}: {', '.join(parts)}"


def interpret_job(
    job: MusicAIJob,
    capability: SongToolCapability,
    *,
    source_name: str = "",
) -> SongToolRun:
    """Read a finished job into the shape the session surface renders."""

    artifacts: list[SongArtifact] = []
    inline: dict[str, Any] = {}
    for name, value in dict(job.result or {}).items():
        if len(artifacts) >= _MAX_ARTIFACTS:
            break
        label = str(name)
        if isinstance(value, str) and value.lower().startswith("https://"):
            try:
                url = validate_music_ai_url(value)
            except MusicAIRequestError:
                continue
            artifacts.append(
                SongArtifact(name=label, kind=_artifact_kind(url, capability), url=url)
            )
        elif isinstance(value, str):
            inline[label] = value
            artifacts.append(
                SongArtifact(
                    name=label,
                    kind=ARTIFACT_DATA,
                    text=value[:_MAX_TEXT_PREVIEW],
                )
            )
        elif isinstance(value, (Mapping, list, int, float)):
            inline[label] = value

    facts = extract_facts(inline)
    return SongToolRun(
        verb_key=capability.key,
        label=capability.label,
        workflow_slug=capability.workflow_slug,
        job_id=job.id,
        source_name=str(source_name or ""),
        artifacts=tuple(artifacts),
        **facts,
    )


def download_artifacts(
    run: SongToolRun,
    *,
    transport: MusicAITransport,
    directory: str | Path,
    timeout: float = 120.0,
) -> SongToolRun:
    """Fetch a run's outputs next to the session and re-read what they contain.

    Results are written locally so the jam surface can show stems, chords, and
    lyrics in place rather than behind a link that expires.
    """

    target = Path(directory)
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise MusicAIRequestError(
            "WebJam could not create a folder for the results."
        ) from exc

    updated: list[SongArtifact] = []
    inline: dict[str, Any] = {}
    for artifact in run.artifacts:
        if not artifact.url:
            updated.append(artifact)
            continue
        response = transport.request(
            "GET", artifact.url, headers={}, timeout=float(timeout)
        )
        if response.status >= 400 or not response.body:
            updated.append(artifact)
            continue
        body = response.body[:_MAX_DOWNLOAD_BYTES]
        path = _unique_path(target, artifact.filename or f"{artifact.name}.bin")
        try:
            path.write_bytes(body)
        except OSError as exc:
            raise MusicAIRequestError(
                "WebJam could not save the results to disk."
            ) from exc
        text = ""
        if artifact.kind != ARTIFACT_AUDIO:
            text = _decode_text(body)
            parsed = _maybe_json(text)
            if parsed is not None:
                inline[artifact.name] = parsed
            elif text:
                inline[artifact.name] = text
        updated.append(
            replace(
                artifact,
                local_path=str(path),
                text=text[:_MAX_TEXT_PREVIEW],
            )
        )

    enriched = replace(run, artifacts=tuple(updated))
    if not inline:
        return enriched
    facts = extract_facts(inline)
    return replace(
        enriched,
        detected_key=enriched.detected_key or facts["detected_key"],
        detected_tempo=enriched.detected_tempo or facts["detected_tempo"],
        detected_sections=enriched.detected_sections or facts["detected_sections"],
        chord_symbols=enriched.chord_symbols or facts["chord_symbols"],
        lyrics_text=enriched.lyrics_text or facts["lyrics_text"],
    )


def extract_facts(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Pull only the musical facts that are genuinely present in ``payload``."""

    flat = _flatten(payload)
    return {
        "detected_key": _first_key(flat),
        "detected_tempo": _first_tempo(flat),
        "detected_sections": _first_sections(payload, flat),
        "chord_symbols": _first_chords(payload, flat),
        "lyrics_text": _first_lyrics(payload, flat),
    }


def _artifact_kind(url: str, capability: SongToolCapability) -> str:
    suffix = Path(_filename_from_url(url)).suffix.lower()
    if suffix in AUDIO_SUFFIXES:
        return ARTIFACT_AUDIO
    if suffix in TEXT_SUFFIXES:
        return ARTIFACT_TEXT
    if not suffix and capability.verb.result_kind in {
        RESULT_AUDIO_SET,
        RESULT_AUDIO_FILE,
    }:
        return ARTIFACT_AUDIO
    return ARTIFACT_TEXT


def _filename_from_url(url: str) -> str:
    if not url:
        return ""
    try:
        path = urlsplit(url).path
    except ValueError:
        return ""
    name = unquote(path.rsplit("/", 1)[-1])
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    return cleaned[:96]


def _unique_path(directory: Path, filename: str) -> Path:
    stem = Path(filename).stem or "result"
    suffix = Path(filename).suffix
    candidate = directory / f"{stem}{suffix}"
    counter = 2
    while candidate.exists():
        candidate = directory / f"{stem}-{counter}{suffix}"
        counter += 1
    return candidate


def _decode_text(body: bytes) -> str:
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        return ""


def _maybe_json(text: str) -> Any:
    stripped = text.strip()
    if not stripped or stripped[0] not in "[{":
        return None
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return None


def _flatten(payload: Any, *, depth: int = 0) -> dict[str, Any]:
    """Return a lowercase field map, looking a couple of levels into results."""

    flat: dict[str, Any] = {}
    if depth > 3:
        return flat
    if isinstance(payload, Mapping):
        for name, value in payload.items():
            token = re.sub(r"[^a-z0-9]+", "", str(name).lower())
            if token and token not in flat:
                flat[token] = value
            if isinstance(value, (Mapping, list)):
                for nested_name, nested in _flatten(value, depth=depth + 1).items():
                    flat.setdefault(nested_name, nested)
    elif isinstance(payload, list):
        for item in payload[:8]:
            for nested_name, nested in _flatten(item, depth=depth + 1).items():
                flat.setdefault(nested_name, nested)
    return flat


def _first_key(flat: Mapping[str, Any]) -> str:
    for field_name in _KEY_FIELDS:
        value = flat.get(field_name)
        if isinstance(value, str):
            match = _KEY_TEXT_RE.match(value.strip())
            if match is not None:
                tonic, quality = match.groups()
                mode = (quality or "").lower()
                suffix = "minor" if mode in {"m", "min", "minor"} else "major"
                return f"{tonic[0].upper()}{tonic[1:]} {suffix}"
    return ""


def _first_tempo(flat: Mapping[str, Any]) -> str:
    for field_name in _TEMPO_FIELDS:
        value = flat.get(field_name)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)) and 20 <= float(value) <= 300:
            return str(int(round(float(value))))
        if isinstance(value, str):
            match = re.match(r"^\s*(\d{2,3})(?:\.\d+)?\s*$", value)
            if match is not None and 20 <= int(match.group(1)) <= 300:
                return match.group(1)
    return ""


def _first_sections(payload: Any, flat: Mapping[str, Any]) -> tuple[str, ...]:
    for field_name in _SECTION_FIELDS:
        value = flat.get(field_name)
        labels = _labels_from(value, ("label", "name", "section", "type"))
        if labels:
            return labels
    return _labels_from(payload, ("label", "name", "section", "type"))[:0]


def _first_chords(payload: Any, flat: Mapping[str, Any]) -> tuple[str, ...]:
    from core.song_form import is_chord_symbol

    for field_name in _CHORD_FIELDS:
        value = flat.get(field_name)
        candidates = _labels_from(value, ("chord", "label", "name", "value"))
        chords = tuple(item for item in candidates if is_chord_symbol(item))
        if chords:
            return chords[:64]
        if isinstance(value, str):
            tokens = tuple(
                token for token in value.split() if is_chord_symbol(token)
            )
            if tokens:
                return tokens[:64]
    return ()


def _first_lyrics(payload: Any, flat: Mapping[str, Any]) -> str:
    del payload
    # Word-timed transcripts are lists of segments, and flattening lifts a
    # segment's own "text" to the top level. Take the structured transcript
    # first so a whole lyric is never replaced by its first word.
    for field_name in _LYRIC_FIELDS:
        lines = _labels_from(
            flat.get(field_name), ("text", "word", "line", "value")
        )
        if lines:
            return " ".join(lines)[:_MAX_TEXT_PREVIEW]
    for field_name in _LYRIC_FIELDS:
        value = flat.get(field_name)
        if isinstance(value, str) and value.strip():
            return value.strip()[:_MAX_TEXT_PREVIEW]
    return ""


def _labels_from(value: Any, fields: Sequence[str]) -> tuple[str, ...]:
    if isinstance(value, str):
        return ()
    labels: list[str] = []
    if isinstance(value, list):
        for item in value[:128]:
            if isinstance(item, str) and item.strip():
                labels.append(item.strip()[:64])
            elif isinstance(item, Mapping):
                for name in fields:
                    candidate = item.get(name)
                    if isinstance(candidate, str) and candidate.strip():
                        labels.append(candidate.strip()[:64])
                        break
    return tuple(labels)


__all__ = [
    "ARTIFACT_AUDIO",
    "ARTIFACT_DATA",
    "ARTIFACT_TEXT",
    "AUDIO_SUFFIXES",
    "SongArtifact",
    "SongToolRun",
    "TEXT_SUFFIXES",
    "download_artifacts",
    "extract_facts",
    "interpret_job",
]
