"""Deterministic local session summaries for the Conductor canvas.

The pulse extracts structure people deliberately record in their notes.  It
does not call a network service, persist derived data, or claim to be an AI
summary of a session.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Sequence

from core.creative_modes import (
    CreatorProfile,
    CreativeMode,
    get_creator_profile_by_key_or_default,
    get_mode_by_key_or_default,
)


_MAX_ITEMS = 5
_URL_RE = re.compile(r"https?://[^\s<>()\[\]{}]+")
_TIMESTAMP_RE = re.compile(r"^#{1,6}\s+\d{1,2}:\d{2}(?::\d{2})?\b")
_OWNER_RE = re.compile(r"(?<![\w.])@([A-Za-z][\w.-]{0,31})\b")


@dataclass(frozen=True)
class SessionAction:
    """An action item optionally assigned to a participant."""

    text: str
    owner: str = ""


@dataclass(frozen=True)
class ParticipantSignal:
    """A small, non-identifying snapshot of confirmed session participants."""

    count: int = 0
    local_present: bool = False
    muted_count: int = 0
    solo_count: int = 0


@dataclass(frozen=True)
class SessionPulse:
    """A local, repeatable view of a session's recorded structure."""

    mode_key: str
    mode_label: str
    title: str
    stage: str
    summary: str
    next_step: str
    decisions: tuple[str, ...]
    actions: tuple[SessionAction, ...]
    blockers: tuple[str, ...]
    questions: tuple[str, ...]
    references: tuple[str, ...]
    participant_signal: ParticipantSignal
    checkpoint: str

    @property
    def signal_line(self) -> str:
        return (
            f"{len(self.decisions)} decisions · "
            f"{len(self.actions)} actions · "
            f"{len(self.blockers)} blockers"
        )

    def to_markdown(self) -> str:
        """Render a portable Markdown handoff without the raw note body."""
        lines = [
            f"# {self.title or self.mode_label}",
            "",
            f"Mode: {self.mode_label}",
            f"Stage: {self.stage}",
        ]
        if self.participant_signal.count:
            lines.append(f"Participants: {self.participant_signal.count}")
        lines.extend(["", f"Summary: {self.summary}", f"Next: {self.next_step}"])
        _append_markdown_section(lines, "Decisions", self.decisions)
        if self.actions:
            lines.extend(["", "## Actions"])
            for action in self.actions:
                owner = f"@{action.owner} " if action.owner else ""
                lines.append(f"- {owner}{action.text}".rstrip())
        _append_markdown_section(lines, "Blockers", self.blockers)
        _append_markdown_section(lines, "Questions", self.questions)
        _append_markdown_section(lines, "References", self.references)
        return "\n".join(lines)


_CHECKPOINTS: dict[str, tuple[str, ...]] = {
    "music_jam": ("sound check", "first run", "feedback", "save mix"),
    "visual_studio": ("reference", "critique", "decision", "revision"),
    "writers_room": ("premise", "draft", "readback", "revision"),
    "design_critique": ("context", "critique", "decision", "owner"),
    "storyboard_film_room": ("scene goal", "shot list", "continuity", "handoff"),
}

_CREATOR_CHECKPOINT_TEMPLATES: dict[str, tuple[str, ...]] = {
    "music": ("sound check", "first run", "{section}", "save mix"),
    "podcast_voice": (
        "mic check",
        "first take",
        "{section} review",
        "pickup plan",
    ),
    "review_rehearsal": (
        "shared goal",
        "first pass",
        "{section} feedback",
        "handoff",
    ),
    "art": (
        "set up the table",
        "block in",
        "{section} check",
        "next session",
    ),
}

_PulseMode = CreativeMode | CreatorProfile


def build_session_pulse(
    *,
    mode_key: str = "music_jam",
    creator_profile_key: object | None = None,
    title: str = "",
    notes: str = "",
    participants: Iterable[object] = (),
) -> SessionPulse:
    """Build a local session pulse from the current session state.

    ``creator_profile_key`` is the current product contract.  ``mode_key``
    remains available for older callers and retains its exact legacy
    behavior when no creator profile is supplied.
    """
    profile = (
        get_creator_profile_by_key_or_default(creator_profile_key)
        if creator_profile_key is not None
        else None
    )
    mode: _PulseMode = profile or get_mode_by_key_or_default(mode_key)
    parsed = _parse_notes(notes)
    participant_signal = _participant_signal(participants)
    checkpoint = _next_checkpoint(mode, notes)
    clean_title = " ".join(title.split())

    return SessionPulse(
        mode_key=mode.key,
        mode_label=mode.label,
        title=clean_title or mode.default_goal,
        stage=_stage_for(mode, parsed, notes, participant_signal, checkpoint),
        summary=_summary(mode, parsed, participant_signal, notes),
        next_step=_next_step(mode, parsed, checkpoint, notes),
        decisions=tuple(parsed["decisions"]),
        actions=tuple(parsed["actions"]),
        blockers=tuple(parsed["blockers"]),
        questions=tuple(parsed["questions"]),
        references=tuple(parsed["references"]),
        participant_signal=participant_signal,
        checkpoint=checkpoint,
    )


def _parse_notes(notes: str) -> dict[str, list]:
    decisions: list[str] = []
    actions: list[SessionAction] = []
    blockers: list[str] = []
    questions: list[str] = []
    references: list[str] = []

    for raw_line in notes.splitlines():
        line = raw_line.strip()
        if not line or _TIMESTAMP_RE.match(line):
            continue
        references.extend(_clean_url(url) for url in _URL_RE.findall(line))
        lowered = line.lower()
        cleaned = _strip_marker(line)

        if _is_decision(lowered):
            _append_unique(decisions, cleaned)
        if _is_action(lowered):
            _append_unique_action(actions, _action_from_line(cleaned))
        if _is_blocker(lowered):
            _append_unique(blockers, cleaned)
        if _is_question(lowered):
            _append_unique(questions, cleaned.rstrip("?").strip())

    return {
        "decisions": decisions[:_MAX_ITEMS],
        "actions": actions[:_MAX_ITEMS],
        "blockers": blockers[:_MAX_ITEMS],
        "questions": questions[:_MAX_ITEMS],
        "references": _unique(references)[:_MAX_ITEMS],
    }


def _strip_marker(line: str) -> str:
    cleaned = re.sub(r"^[-*]\s+", "", line.strip())
    cleaned = re.sub(r"^\[[ xX]\]\s+", "", cleaned)
    cleaned = re.sub(
        r"^(decision|decided|action|todo|next|blocker|blocked|risk|question|q)\s*[:\-]\s*",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    return cleaned.strip()


def _is_decision(lowered: str) -> bool:
    return (
        lowered.startswith(("decision:", "decided:", "decided "))
        or " approved" in lowered
        or lowered.startswith("approved ")
    )


def _is_action(lowered: str) -> bool:
    return lowered.startswith(("action:", "todo:", "next:", "- [ ]", "* [ ]"))


def _is_blocker(lowered: str) -> bool:
    return lowered.startswith(("blocker:", "blocked:", "risk:")) or any(
        token in lowered for token in (" blocked by ", " stuck on ", " at risk")
    )


def _is_question(lowered: str) -> bool:
    return lowered.endswith("?") or lowered.startswith(("question:", "q:"))


def _action_from_line(text: str) -> SessionAction:
    owner_match = _OWNER_RE.search(text)
    if owner_match is None:
        return SessionAction(text=text)
    owner = owner_match.group(1)
    action_text = " ".join(
        (text[: owner_match.start()] + text[owner_match.end() :]).split()
    )
    return SessionAction(text=action_text or text, owner=owner)


def _participant_signal(participants: Iterable[object]) -> ParticipantSignal:
    count = 0
    local_present = False
    muted_count = 0
    solo_count = 0
    for participant in participants:
        count += 1
        local_present = local_present or _truthy_field(participant, "is_local")
        muted_count += int(_truthy_field(participant, "muted"))
        solo_count += int(_truthy_field(participant, "solo"))
    return ParticipantSignal(
        count=count,
        local_present=local_present,
        muted_count=muted_count,
        solo_count=solo_count,
    )


def _truthy_field(obj: object, key: str) -> bool:
    if isinstance(obj, dict):
        return bool(obj.get(key))
    return bool(getattr(obj, key, False))


def _next_checkpoint(mode: _PulseMode, notes: str) -> str:
    checkpoints = (
        tuple(
            item.format(section=mode.vocabulary.section_noun)
            for item in _CREATOR_CHECKPOINT_TEMPLATES.get(mode.key, ())
        )
        if isinstance(mode, CreatorProfile)
        else _CHECKPOINTS.get(mode.key, ())
    )
    if not checkpoints:
        return mode.review_prompts[0] if mode.review_prompts else "the next checkpoint"
    lowered = notes.lower()
    for checkpoint in checkpoints:
        if checkpoint not in lowered:
            return checkpoint
    return checkpoints[-1]


def _stage_for(
    mode: _PulseMode,
    parsed: dict[str, Sequence],
    notes: str,
    participant_signal: ParticipantSignal,
    checkpoint: str,
) -> str:
    if parsed["blockers"]:
        return "Unblock"
    if parsed["decisions"] and parsed["actions"]:
        return "Handoff"
    if parsed["decisions"]:
        return "Decision"
    if parsed["actions"]:
        return "Execution"
    if notes.strip():
        return "Exploration"
    if participant_signal.count > 1:
        if isinstance(mode, CreatorProfile):
            return {
                "podcast_voice": "Mic Check",
                "review_rehearsal": "Session Check",
            }.get(mode.key, "Sound Check")
        return "Sound Check"
    return checkpoint.title()


def _summary(
    mode: _PulseMode,
    parsed: dict[str, Sequence],
    participant_signal: ParticipantSignal,
    notes: str,
) -> str:
    if parsed["blockers"]:
        return f"{mode.label} is blocked by: {parsed['blockers'][0]}"
    if parsed["decisions"]:
        return f"Latest decision: {parsed['decisions'][0]}"
    if parsed["actions"]:
        return f"Working from: {parsed['actions'][0].text}"
    if notes.strip():
        return f"{mode.label} has notes but no captured decision yet."
    if participant_signal.count > 1:
        participant_plural = (
            mode.vocabulary.participant_plural
            if isinstance(mode, CreatorProfile)
            else "people"
        )
        return (
            f"{participant_signal.count} {participant_plural} are in the room; "
            "capture the first checkpoint."
        )
    return "No creative notes yet; capture the shared goal when you’re ready."


def _next_step(
    mode: _PulseMode,
    parsed: dict[str, Sequence],
    checkpoint: str,
    notes: str,
) -> str:
    if parsed["blockers"]:
        return f"Clear blocker: {parsed['blockers'][0]}"
    if parsed["decisions"] and not parsed["actions"]:
        return f"Assign an owner for: {parsed['decisions'][0]}"
    if parsed["actions"]:
        return f"Work the next action: {parsed['actions'][0].text}"
    if not notes.strip():
        return f"Start with {checkpoint}."
    if mode.review_prompts:
        return mode.review_prompts[0]
    return f"Capture {checkpoint}."


def _append_markdown_section(lines: list[str], heading: str, items: Sequence[str]) -> None:
    if items:
        lines.extend(["", f"## {heading}", *[f"- {item}" for item in items]])


def _append_unique(items: list[str], value: str) -> None:
    if value and value not in items:
        items.append(value)


def _append_unique_action(items: list[SessionAction], value: SessionAction) -> None:
    if value.text and all(existing.text != value.text for existing in items):
        items.append(value)


def _unique(values: Iterable[str]) -> list[str]:
    unique: list[str] = []
    for value in values:
        if value and value not in unique:
            unique.append(value)
    return unique


def _clean_url(url: str) -> str:
    return url.rstrip(".,;:!?")
