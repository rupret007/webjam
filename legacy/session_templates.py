"""
One-click session templates.

Presets that set template name and session goal instantly.
Mode-agnostic by default; optional mode_key for mode-specific presets.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class SessionTemplate:
    """A preset that populates template name and session goal."""

    id: str
    label: str
    template_name: str
    session_goal: str
    mode_key: Optional[str] = None


SESSION_TEMPLATES: tuple[SessionTemplate, ...] = (
    SessionTemplate(
        id="band_rehearsal",
        label="Band Rehearsal",
        template_name="Band Rehearsal",
        session_goal="Lock timing and balance for one full song run.",
        mode_key="music_jam",
    ),
    SessionTemplate(
        id="feedback_on_track",
        label="Feedback on a Track",
        template_name="Track Review",
        session_goal="Get focused feedback on a mix or recording; decide next edits.",
        mode_key="music_jam",
    ),
    SessionTemplate(
        id="critique_circle",
        label="Critique Circle",
        template_name="Critique Circle",
        session_goal="Share references and collect focused critique on one in-progress piece.",
        mode_key="visual_studio",
    ),
    SessionTemplate(
        id="draft_sprint",
        label="Draft Sprint",
        template_name="Draft Sprint",
        session_goal="Complete one draft checkpoint and agree on next edit pass.",
        mode_key="writers_room",
    ),
    SessionTemplate(
        id="design_review",
        label="Design Review",
        template_name="Design Review",
        session_goal="Decide final direction using structured feedback and action owners.",
        mode_key="design_critique",
    ),
    SessionTemplate(
        id="shot_planning",
        label="Shot Planning",
        template_name="Shot Planning",
        session_goal="Finalize scene flow and capture the next shot list.",
        mode_key="storyboard_film_room",
    ),
)


def get_all_templates() -> list[SessionTemplate]:
    """Return all session templates."""
    return list(SESSION_TEMPLATES)


def get_templates_for_mode(mode_key: Optional[str]) -> list[SessionTemplate]:
    """Return templates for the given mode, or all if mode_key is None."""
    if not mode_key:
        return get_all_templates()
    return [t for t in SESSION_TEMPLATES if t.mode_key is None or t.mode_key == mode_key]


def get_template_by_id(template_id: str) -> Optional[SessionTemplate]:
    """Return the template with the given id, or None."""
    for t in SESSION_TEMPLATES:
        if t.id == template_id:
            return t
    return None
