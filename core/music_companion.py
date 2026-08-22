"""What a Music session may tell a companion surface, and what it will accept.

A companion — a Webex Embedded App panel, or anything else outside the desktop
window — needs to show the song and ask for work. This module is the whole
contract for that, kept in ``core`` so the desktop side can be built and proven
without a start page, a transport, or a running companion existing at all.

Two halves, and they are deliberately asymmetric.

**Outward** is :class:`MusicCompanionSnapshot`: where the song is, what it is
in, the chords and one lyric line, whether a Song tools job is running, and the
current suggestion. It is bounded, plain, and carries **no filesystem path, no
API key, no upload or result URL, and no participant identity** — asserted by
test rather than promised. It does carry musician-authored song text, because
showing the song *is* the feature; that is why it carries nothing else.

**Inward** is :class:`MusicCompanionCommand`, and the important property is what
a command cannot say. It cannot name a file. There is no path field to fill in,
so a companion cannot point Song tools at the live mix, at a meeting recording,
or at anything the host did not already choose on the desktop. A tool command
means "run this verb on the Shared Track the host already loaded", and if no
Shared Track is loaded the answer is no. A press is a request; the desktop
decides, and :func:`evaluate_command` is where it decides.

None of this gates Music. With no companion at all, the same state renders in
the native session strip and the Song panel, which is where it renders today.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping

CONTRACT_VERSION = 1

# Commands a companion may send. Anything else is refused by name.
COMMAND_RUN_TOOL = "run_song_tool"
COMMAND_WRITE_HELP = "write_help"
COMMAND_SUGGEST_CHORDS = "suggest_chords"

KNOWN_COMMANDS = frozenset(
    {COMMAND_RUN_TOOL, COMMAND_WRITE_HELP, COMMAND_SUGGEST_CHORDS}
)

# Verbs a companion may request. The desktop still checks the account can run
# the verb, that the caller is the host, and that a key exists.
REQUESTABLE_VERBS = ("stems", "chords", "lyrics", "sections", "key_tempo")

STATUS_ACCEPTED = "accepted"
STATUS_REJECTED = "rejected"
STATUS_UNSUPPORTED = "unsupported"

JOB_IDLE = "idle"
JOB_RUNNING = "running"

# Bounds. A companion panel is small and a projection is not a data channel.
MAX_CHORD_SYMBOLS = 8
MAX_OVERLAY_ROWS = 8
MAX_OVERLAY_CHARS = 80
MAX_LYRIC_CHARS = 120
MAX_SUGGESTION_CHORDS = 12
MAX_REASON_CHARS = 200
MAX_LABEL_CHARS = 40

# Anything shaped like a path, a URL, or a credential must never reach a
# companion, so the projection scrubs rather than trusting its callers.
_UNSAFE = re.compile(
    r"(?:[a-z][a-z0-9+.-]*://)"          # any URL scheme
    r"|(?:^|[\s(])[~/]\S*"               # absolute or home-relative path
    r"|(?:[A-Za-z]:[\\/])"               # windows drive
    r"|(?:\.\.[\\/])",                   # traversal
    re.IGNORECASE,
)


def _clean(value: Any, limit: int) -> str:
    """Return bounded single-line text with nothing path- or URL-shaped in it."""

    text = " ".join(str(value or "").split())
    if not text or _UNSAFE.search(text):
        return ""
    return text[:limit]


@dataclass(frozen=True, slots=True)
class MusicCompanionSuggestion:
    """One write-help result, always labelled as a suggestion."""

    kind: str = ""
    section: str = ""
    chords: tuple[str, ...] = ()
    reason: str = ""

    @property
    def is_suggestion(self) -> bool:
        return True

    def to_public_dict(self) -> dict:
        return {
            "kind": self.kind,
            "section": self.section,
            "chords": list(self.chords),
            "reason": self.reason,
            # Present in the wire form so a companion cannot render this as a
            # measurement even by accident.
            "label": "suggestion",
        }


@dataclass(frozen=True, slots=True)
class MusicCompanionJob:
    """Whether a Song tools job is running, and what to call it."""

    state: str = JOB_IDLE
    label: str = ""

    @property
    def running(self) -> bool:
        return self.state == JOB_RUNNING

    def to_public_dict(self) -> dict:
        return {"state": self.state, "label": self.label}


@dataclass(frozen=True, slots=True)
class MusicCompanionSnapshot:
    """Everything a companion may know about this Music session."""

    contract_version: int = CONTRACT_VERSION
    revision: int = 0
    is_music_session: bool = False

    # Position. ``position_known`` is false until a clock or Shared Track says.
    section: str = ""
    section_index: int = -1
    bar: int = 0
    bar_in_section: int = 0
    bars_total: int = 0
    beat: int = 0
    position_s: float = 0.0
    position_known: bool = False
    position_source: str = ""
    following_audio: bool = False
    section_lengths_assumed: bool = False

    # Song truth, each with where it came from.
    key: str = ""
    key_source: str = ""
    bpm: float = 0.0
    bpm_source: str = ""

    # Overlays, bounded.
    chords_now: tuple[str, ...] = ()
    chord_overlay: tuple[str, ...] = ()
    lyric_line: str = ""

    # Tools. Never a key, never a path, never a URL.
    shared_track_loaded: bool = False
    is_host: bool = False
    tools_available: tuple[str, ...] = ()
    tools_unavailable_reason: str = ""
    job: MusicCompanionJob = field(default_factory=MusicCompanionJob)
    suggestion: MusicCompanionSuggestion | None = None

    def to_public_dict(self) -> dict:
        """Return the wire form: plain JSON-ready values only."""

        return {
            "contract_version": self.contract_version,
            "revision": self.revision,
            "is_music_session": self.is_music_session,
            "section": self.section,
            "section_index": self.section_index,
            "bar": self.bar,
            "bar_in_section": self.bar_in_section,
            "bars_total": self.bars_total,
            "beat": self.beat,
            "position_s": round(self.position_s, 3),
            "position_known": self.position_known,
            "position_source": self.position_source,
            "following_audio": self.following_audio,
            "section_lengths_assumed": self.section_lengths_assumed,
            "key": self.key,
            "key_source": self.key_source,
            "bpm": round(self.bpm, 3),
            "bpm_source": self.bpm_source,
            "chords_now": list(self.chords_now),
            "chord_overlay": list(self.chord_overlay),
            "lyric_line": self.lyric_line,
            "shared_track_loaded": self.shared_track_loaded,
            "is_host": self.is_host,
            "tools_available": list(self.tools_available),
            "tools_unavailable_reason": self.tools_unavailable_reason,
            "job": self.job.to_public_dict(),
            "suggestion": (
                None if self.suggestion is None else self.suggestion.to_public_dict()
            ),
        }


@dataclass(frozen=True, slots=True)
class MusicCompanionCommand:
    """One request from a companion. It cannot name a file, by construction."""

    name: str
    verb: str = ""
    section: str = ""

    @property
    def is_known(self) -> bool:
        return self.name in KNOWN_COMMANDS


@dataclass(frozen=True, slots=True)
class MusicCompanionDecision:
    """The desktop's answer to a request."""

    status: str
    reason: str = ""
    command: MusicCompanionCommand | None = None

    @property
    def accepted(self) -> bool:
        return self.status == STATUS_ACCEPTED


def parse_command(payload: Mapping[str, Any] | None) -> MusicCompanionCommand | None:
    """Read a command off the wire, ignoring any field not in the contract.

    A payload carrying a path, a URL, or a key does not fail loudly — those
    fields simply do not exist here, so they are dropped on the way in.
    """

    if not isinstance(payload, Mapping):
        return None
    name = str(payload.get("command") or payload.get("name") or "").strip()
    if name not in KNOWN_COMMANDS:
        return None
    return MusicCompanionCommand(
        name=name,
        verb=_clean(payload.get("verb"), MAX_LABEL_CHARS),
        section=_clean(payload.get("section"), MAX_LABEL_CHARS),
    )


def evaluate_command(
    command: MusicCompanionCommand | None,
    snapshot: MusicCompanionSnapshot,
) -> MusicCompanionDecision:
    """Decide whether the desktop will act on a companion's request.

    Order matters: the cheapest refusal first, and every refusal says what a
    musician can do about it on the desktop.
    """

    if command is None or not command.is_known:
        return MusicCompanionDecision(
            STATUS_UNSUPPORTED, "WebJam does not know that request."
        )
    if not snapshot.is_music_session:
        return MusicCompanionDecision(
            STATUS_REJECTED, "Song tools are part of a Music session."
        )

    if command.name in {COMMAND_WRITE_HELP, COMMAND_SUGGEST_CHORDS}:
        # Local, read-only, and uploads nothing, so it needs no host role and
        # no key. It is still only a request.
        return MusicCompanionDecision(STATUS_ACCEPTED, command=command)

    if command.verb not in REQUESTABLE_VERBS:
        return MusicCompanionDecision(
            STATUS_UNSUPPORTED, "That Song tool cannot be requested from here."
        )
    if not snapshot.is_host:
        return MusicCompanionDecision(
            STATUS_REJECTED, "Only the host can send a file to Music AI."
        )
    if command.verb not in snapshot.tools_available:
        return MusicCompanionDecision(
            STATUS_UNSUPPORTED,
            snapshot.tools_unavailable_reason
            or "This Music AI account cannot run that tool.",
        )
    if not snapshot.shared_track_loaded:
        # The only file a companion may act on is the one the host already
        # chose. There is no way to name another, and none is offered.
        return MusicCompanionDecision(
            STATUS_REJECTED,
            "Load a Shared Track on the desktop first. WebJam never uploads "
            "the live jam.",
        )
    if snapshot.job.running:
        return MusicCompanionDecision(
            STATUS_REJECTED, "One Song tool is already running."
        )
    return MusicCompanionDecision(STATUS_ACCEPTED, command=command)


def build_snapshot(
    *,
    revision: int = 0,
    is_music_session: bool = False,
    clock: Any = None,
    form_rows: Any = (),
    lyric_line: str = "",
    shared_track_loaded: bool = False,
    is_host: bool = False,
    tools_available: Any = (),
    tools_unavailable_reason: str = "",
    job_verb: str = "",
    job_label: str = "",
    suggestion: Any = None,
    suggestion_section: str = "",
    suggestion_kind: str = "",
) -> MusicCompanionSnapshot:
    """Build the projection, scrubbing and bounding every field on the way out.

    Callers pass whatever they already have; nothing is trusted to be safe or
    short. That keeps the safety property in one place instead of at every
    call site.
    """

    position_known = False
    section = ""
    section_index = -1
    bar = bar_in_section = bars_total = beat = 0
    position_s = 0.0
    position_source = ""
    following_audio = False
    lengths_assumed = False
    key = key_source = bpm_source = ""
    bpm = 0.0
    chords_now: tuple[str, ...] = ()

    if clock is not None:
        section = _clean(getattr(clock, "section_label", ""), MAX_LABEL_CHARS)
        # Index 0 is the first part, not "unknown", so this cannot use ``or``.
        raw_index = getattr(clock, "section_index", -1)
        section_index = int(raw_index) if isinstance(raw_index, int) else -1
        bar = max(0, int(getattr(clock, "bar", 0) or 0))
        bar_in_section = max(0, int(getattr(clock, "bar_in_section", 0) or 0))
        bars_total = max(0, int(getattr(clock, "bars_total", 0) or 0))
        beat = max(0, int(getattr(clock, "beat", 0) or 0))
        position_s = max(0.0, float(getattr(clock, "position_s", 0.0) or 0.0))
        position_source = _clean(
            getattr(clock, "position_source", ""), MAX_LABEL_CHARS
        )
        following_audio = bool(getattr(clock, "following_audio", False))
        lengths_assumed = bool(getattr(clock, "section_lengths_assumed", False))
        key = _clean(getattr(clock, "key", ""), MAX_LABEL_CHARS)
        key_source = _clean(getattr(clock, "key_source", ""), MAX_LABEL_CHARS)
        bpm = max(0.0, float(getattr(clock, "tempo_bpm", 0.0) or 0.0))
        bpm_source = _clean(getattr(clock, "tempo_source", ""), MAX_LABEL_CHARS)
        chords_now = tuple(
            _clean(chord, 12)
            for chord in tuple(getattr(clock, "chords_now", ()))[:MAX_CHORD_SYMBOLS]
        )
        chords_now = tuple(chord for chord in chords_now if chord)
        position_known = bool(section and bar)

    overlay: list[str] = []
    for row in tuple(form_rows)[:MAX_OVERLAY_ROWS]:
        label = _clean(getattr(row, "label", ""), MAX_LABEL_CHARS)
        chords = _clean(getattr(row, "chords", ""), MAX_OVERLAY_CHARS)
        if not label:
            continue
        overlay.append(f"{label}: {chords}"[:MAX_OVERLAY_CHARS] if chords else label)

    projected: MusicCompanionSuggestion | None = None
    if suggestion is not None:
        chords = tuple(
            _clean(chord, 12)
            for chord in tuple(getattr(suggestion, "chords", ()))[
                :MAX_SUGGESTION_CHORDS
            ]
        )
        chords = tuple(chord for chord in chords if chord)
        reason = _clean(
            f"{getattr(suggestion, 'reason', '')} "
            f"{getattr(suggestion, 'context', '')}",
            MAX_REASON_CHARS,
        )
        if chords or reason:
            projected = MusicCompanionSuggestion(
                kind=_clean(suggestion_kind or "chords", MAX_LABEL_CHARS),
                section=_clean(suggestion_section, MAX_LABEL_CHARS),
                chords=chords,
                reason=reason,
            )

    verbs = tuple(
        verb
        for verb in (
            _clean(item, MAX_LABEL_CHARS) for item in tuple(tools_available)
        )
        if verb in REQUESTABLE_VERBS
    )

    running = bool(job_verb)
    return MusicCompanionSnapshot(
        revision=max(0, int(revision)),
        is_music_session=bool(is_music_session),
        section=section,
        section_index=section_index,
        bar=bar,
        bar_in_section=bar_in_section,
        bars_total=bars_total,
        beat=beat,
        position_s=position_s,
        position_known=position_known,
        position_source=position_source,
        following_audio=following_audio,
        section_lengths_assumed=lengths_assumed,
        key=key,
        key_source=key_source,
        bpm=bpm,
        bpm_source=bpm_source,
        chords_now=chords_now,
        chord_overlay=tuple(overlay),
        lyric_line=_clean(lyric_line, MAX_LYRIC_CHARS),
        shared_track_loaded=bool(shared_track_loaded),
        is_host=bool(is_host),
        tools_available=verbs,
        tools_unavailable_reason=_clean(tools_unavailable_reason, MAX_REASON_CHARS),
        job=MusicCompanionJob(
            state=JOB_RUNNING if running else JOB_IDLE,
            label=_clean(job_label, MAX_LABEL_CHARS) if running else "",
        ),
        suggestion=projected,
    )


def describe_contract() -> dict:
    """Return the published shape, for the companion track to assert against."""

    return {
        "version": CONTRACT_VERSION,
        "snapshot_fields": tuple(MusicCompanionSnapshot().to_public_dict().keys()),
        "commands": tuple(sorted(KNOWN_COMMANDS)),
        "requestable_verbs": REQUESTABLE_VERBS,
        "guarantees": (
            "no filesystem path, API key, or upload URL is ever published",
            "a command cannot name a file; tools act on the host's Shared Track",
            "the desktop decides; a companion press is only a request",
            "position is a host reference, never audio-followed",
            "write-help results are labelled suggestions",
        ),
    }


__all__ = [
    "COMMAND_RUN_TOOL",
    "COMMAND_SUGGEST_CHORDS",
    "COMMAND_WRITE_HELP",
    "CONTRACT_VERSION",
    "JOB_IDLE",
    "JOB_RUNNING",
    "KNOWN_COMMANDS",
    "MAX_LYRIC_CHARS",
    "MAX_OVERLAY_ROWS",
    "REQUESTABLE_VERBS",
    "STATUS_ACCEPTED",
    "STATUS_REJECTED",
    "STATUS_UNSUPPORTED",
    "MusicCompanionCommand",
    "MusicCompanionDecision",
    "MusicCompanionJob",
    "MusicCompanionSnapshot",
    "MusicCompanionSuggestion",
    "build_snapshot",
    "describe_contract",
    "evaluate_command",
    "parse_command",
]
