"""Asking a musician's own text model about one section of the song they have.

This is the only place in WebJam where anything a musician typed can leave the
computer for a language model, and it is narrow on purpose.

**What goes.** The key, the tempo, the meter, the ordered section names, and the
chords written under them. That is the whole payload, and
:func:`describe_what_is_sent` renders it verbatim so the confirmation shows the
actual text rather than a summary of it.

**What never goes.** Lyrics. The song or session title. Any filename or path.
Any participant name. The meeting link. Audio of any kind — there is no code
path from the Jamulus mix, the Shared Track, or a microphone to this module.

**What comes back.** Chord progressions for one named part, parsed strictly and
labelled as suggestions from that provider. A model is not a detector: nothing
here can set the song's key, its tempo, its lyrics, or its detected chords, and
a response WebJam cannot parse into real chord symbols becomes an honest "no
usable suggestion" instead of something invented. That boundary is ADR 0002's —
operational truth and creative suggestion are different fields — and it is the
reason a musician can trust the chords already on the form.

Write-help does not need any of this. :mod:`core.song_help` answers the same
question from music theory on this computer, with no key and no network, and
that is what a jam gets by default.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Mapping

from core.provider_credentials import provider_spec
from core.song_form import SongForm
from core.song_help import resolve_key, resolve_section_label
from core.text_model_client import (
    TextModelClient,
    TextModelError,
    TextModelTransport,
    resolve_model,
)

LOGGER = logging.getLogger("webjam.core.song_model_help")

# Bounds. A prompt is a description of one song's shape, not a data channel.
MAX_SECTIONS_SENT = 12
MAX_CHORDS_PER_SECTION = 12
MAX_SUGGESTIONS = 3
MAX_CHORDS_PER_SUGGESTION = 8
MIN_CHORDS_PER_SUGGESTION = 2
MAX_REASON_CHARS = 160

_CHORD_RE = re.compile(
    r"^[A-G][#b]?"                                   # root
    r"(?:maj|min|m|dim|aug|sus|add|\+)?"             # quality
    r"(?:[0-9]{1,2})?"                               # extension
    r"(?:(?:maj|add|sus|no|b|#|\+)[0-9]{1,2})*"      # alterations
    r"(?:/[A-G][#b]?)?$"                             # slash bass
)
_CHORDS_LINE_RE = re.compile(r"(?im)^\s*(?:[-*\d.\)\s]*)CHORDS\s*:\s*(.+?)\s*$")
_WHY_LINE_RE = re.compile(r"(?im)^\s*(?:[-*\d.\)\s]*)WHY\s*:\s*(.+?)\s*$")

_SYSTEM_PROMPT = (
    "You are helping a band finish a song they are already playing live. "
    "Suggest chord progressions for one named section of that song, in its "
    "key.\n"
    "Answer only in this format, repeated at most three times, and write "
    "nothing else:\n"
    "CHORDS: <two to eight chord symbols separated by spaces>\n"
    "WHY: <one short sentence>\n"
    "Use plain chord symbols such as G, Am, F#m7, Csus4, D/F#. Do not write "
    "lyrics, tablature, or commentary."
)


@dataclass(frozen=True, slots=True)
class ModelChordSuggestion:
    """One progression a model proposed. Always a suggestion, never a fact."""

    chords: tuple[str, ...]
    reason: str = ""
    provider_label: str = ""

    @property
    def chord_line(self) -> str:
        return " ".join(self.chords)

    def describe(self) -> str:
        source = f" — {self.provider_label}" if self.provider_label else ""
        return f"{self.chord_line}{source}"


@dataclass(frozen=True, slots=True)
class ModelHelpResult:
    """The answer to "ask a model about this part", including a refusal."""

    provider_id: str = ""
    provider_label: str = ""
    model: str = ""
    section_label: str = ""
    key: str = ""
    suggestions: tuple[ModelChordSuggestion, ...] = ()
    blocked_reason: str = ""

    @property
    def available(self) -> bool:
        return bool(self.suggestions)

    def headline(self) -> str:
        if not self.available:
            return self.blocked_reason or "That model returned nothing usable."
        where = f" for {self.section_label}" if self.section_label else ""
        in_key = f" in {self.key}" if self.key else ""
        return (
            f"{self.provider_label} suggestions{where}{in_key}. "
            "Suggestions, not what the song is."
        )


def build_prompt(form: SongForm, *, section_label: str) -> tuple[str, str]:
    """Return the exact ``(system, user)`` text a request would carry."""

    return _SYSTEM_PROMPT, describe_what_is_sent(
        form, section_label=section_label
    )


def describe_what_is_sent(form: SongForm, *, section_label: str) -> str:
    """Return the payload itself, so consent is about text a musician can read.

    Everything in here is the shape of the song. There is no branch that can
    add a lyric, a title, a filename, or a name to it.
    """

    key_name, key_basis = resolve_key(form)
    lines: list[str] = []
    if key_name:
        lines.append(f"Key: {key_name} ({key_basis})")
    else:
        lines.append("Key: not known")
    if form.tempo is not None and form.tempo.value:
        lines.append(f"Tempo: {form.tempo.value} BPM")
    if form.meter is not None and form.meter.value:
        lines.append(f"Meter: {form.meter.value}")

    shape: list[str] = []
    for section in form.sections[:MAX_SECTIONS_SENT]:
        chords = " ".join(section.chords[:MAX_CHORDS_PER_SECTION])
        shape.append(f"  {section.label}: {chords}" if chords else f"  {section.label}:")
    if shape:
        lines.append("Sections so far:")
        lines.extend(shape)
    else:
        lines.append("Sections so far: none written")

    target = " ".join(str(section_label or "").split()) or "the next part"
    lines.append(f"Write chords for: {target}")
    return "\n".join(lines)


def consent_body(
    form: SongForm,
    *,
    section_label: str,
    provider_label: str,
) -> str:
    """Return the confirmation text shown before the first request of a session."""

    return (
        f"This sends the lines below to {provider_label}, using your own key.\n\n"
        f"{describe_what_is_sent(form, section_label=section_label)}\n\n"
        "It does not send audio, lyrics, the live jam, the meeting, or any "
        "file. Anything it answers is a suggestion until you keep it."
    )


def parse_suggestions(
    text: str,
    *,
    provider_label: str = "",
) -> tuple[ModelChordSuggestion, ...]:
    """Read chord suggestions out of a model's answer, strictly.

    A token that is not a chord symbol is dropped, a suggestion with too few
    real chords is dropped, and a response with nothing left produces nothing.
    Guessing at a malformed answer would put a chord on a musician's screen
    that no one — not the band, not the model — actually proposed.
    """

    body = str(text or "")
    chord_matches = list(_CHORDS_LINE_RE.finditer(body))
    if not chord_matches:
        return ()
    why_matches = list(_WHY_LINE_RE.finditer(body))

    suggestions: list[ModelChordSuggestion] = []
    for index, match in enumerate(chord_matches[:MAX_SUGGESTIONS]):
        chords = _chord_symbols(match.group(1))
        if len(chords) < MIN_CHORDS_PER_SUGGESTION:
            continue
        reason = ""
        for why in why_matches:
            # The explanation for a progression is the first WHY after it.
            if why.start() > match.start():
                reason = _plain(why.group(1))
                break
        del index
        suggestions.append(
            ModelChordSuggestion(
                chords=chords,
                reason=reason,
                provider_label=str(provider_label or ""),
            )
        )
    return tuple(suggestions)


def ask_for_section(
    form: SongForm,
    *,
    section_label: str = "",
    provider_id: str,
    api_key: str,
    transport: TextModelTransport | None = None,
    model: str = "",
    environ: Mapping[str, str] | None = None,
    timeout: float = 30.0,
) -> ModelHelpResult:
    """Ask one provider for chords for one part. Never raises into the UI.

    Every failure comes back as a result with ``blocked_reason`` set, because
    an exception escaping here would land on a background thread during a live
    take. The band keeps playing either way.
    """

    spec = provider_spec(provider_id)
    label = spec.label if spec is not None else str(provider_id or "")
    target = " ".join(str(section_label or "").split()) or resolve_section_label(form)
    key_name, _basis = resolve_key(form)

    if not form.has_content:
        return ModelHelpResult(
            provider_id=str(provider_id or ""),
            provider_label=label,
            section_label=target,
            blocked_reason=(
                "There is no song written down yet, so there is nothing to ask "
                "about. Put a key and one section header in the notes first."
            ),
        )

    try:
        client = TextModelClient(
            provider_id,
            api_key,
            transport=transport,
            model=model,
            timeout=timeout,
            environ=environ,
        )
        system, user = build_prompt(form, section_label=target)
        answer = client.complete(system=system, user=user)
    except TextModelError as exc:
        return ModelHelpResult(
            provider_id=str(provider_id or ""),
            provider_label=label,
            model=model or resolve_model(provider_id, environ=environ),
            section_label=target,
            key=key_name,
            blocked_reason=str(exc),
        )
    except Exception:  # noqa: BLE001 - a live jam must survive any client bug
        LOGGER.warning("Model write-help failed", exc_info=True)
        return ModelHelpResult(
            provider_id=str(provider_id or ""),
            provider_label=label,
            section_label=target,
            key=key_name,
            blocked_reason=f"WebJam could not use {label}'s answer.",
        )

    suggestions = parse_suggestions(answer, provider_label=label)
    if not suggestions:
        return ModelHelpResult(
            provider_id=client.provider_id,
            provider_label=label,
            model=client.model,
            section_label=target,
            key=key_name,
            blocked_reason=(
                f"{label} answered, but not with chords WebJam could read. "
                "Nothing was changed."
            ),
        )
    return ModelHelpResult(
        provider_id=client.provider_id,
        provider_label=label,
        model=client.model,
        section_label=target,
        key=key_name,
        suggestions=suggestions,
    )


def _chord_symbols(text: str) -> tuple[str, ...]:
    """Return only the real chord symbols in one line, in order."""

    symbols: list[str] = []
    for raw in str(text or "").replace("|", " ").replace(",", " ").split():
        token = raw.strip("().;:").strip()
        if not token or len(token) > 12:
            continue
        if _CHORD_RE.match(token) is None:
            continue
        symbols.append(token)
        if len(symbols) >= MAX_CHORDS_PER_SUGGESTION:
            break
    return tuple(symbols)


def _plain(text: str) -> str:
    """Return one bounded, printable line with nothing link-shaped in it."""

    collapsed = " ".join(str(text or "").split())
    if not collapsed or "://" in collapsed:
        return ""
    safe = "".join(
        character
        for character in collapsed
        if character.isprintable() and character not in "\r\n"
    )
    return safe[:MAX_REASON_CHARS]


__all__ = [
    "MAX_CHORDS_PER_SUGGESTION",
    "MAX_SECTIONS_SENT",
    "MAX_SUGGESTIONS",
    "MIN_CHORDS_PER_SUGGESTION",
    "ModelChordSuggestion",
    "ModelHelpResult",
    "ask_for_section",
    "build_prompt",
    "consent_body",
    "describe_what_is_sent",
    "parse_suggestions",
]
