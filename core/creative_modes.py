from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CreativeMode:
    key: str
    label: str
    default_template: str
    default_goal: str
    quick_help: str
    review_prompts: tuple[str, ...]


CREATIVE_MODES: tuple[CreativeMode, ...] = (
    CreativeMode(
        key="music_jam",
        label="Music Jam",
        default_template="Band Rehearsal",
        default_goal="Lock timing and balance for one full song run.",
        quick_help="Launch Jamulus first, then Webex. Keep latency and VU peaks in check.",
        review_prompts=(
            "What section needs a tighter groove?",
            "Which channel needs level or pan adjustment?",
            "What should we rehearse next session?",
        ),
    ),
    CreativeMode(
        key="visual_studio",
        label="Visual Studio",
        default_template="Critique Circle",
        default_goal="Share references and collect focused critique on one in-progress piece.",
        quick_help="Use the session canvas to pin references, drafts, and revision notes.",
        review_prompts=(
            "What visual focal point works best?",
            "What single change improves composition most?",
            "Which reference should guide the next draft?",
        ),
    ),
    CreativeMode(
        key="writers_room",
        label="Writer's Room",
        default_template="Draft Sprint",
        default_goal="Complete one draft checkpoint and agree on next edit pass.",
        quick_help="Capture prompt ideas, draft links, and revision decisions in notes.",
        review_prompts=(
            "What line or scene lands strongest?",
            "Where does pacing fall off?",
            "What rewrite target is highest priority?",
        ),
    ),
    CreativeMode(
        key="design_critique",
        label="Design Critique",
        default_template="Design Review",
        default_goal="Decide final direction using structured feedback and action owners.",
        quick_help="Pin mockups and track decisions with draft/review/final state updates.",
        review_prompts=(
            "What user problem is solved clearly?",
            "What decision is blocked and by whom?",
            "What must be tested before final sign-off?",
        ),
    ),
    CreativeMode(
        key="storyboard_film_room",
        label="Storyboard/Film Room",
        default_template="Shot Planning",
        default_goal="Finalize scene flow and capture the next shot list.",
        quick_help="Use pinned references for boards, cuts, and continuity callouts.",
        review_prompts=(
            "Which shot transition feels weakest?",
            "What reference should anchor scene pacing?",
            "What is the next production-ready action?",
        ),
    ),
)

_MODE_BY_KEY = {mode.key: mode for mode in CREATIVE_MODES}


def get_mode_keys() -> list[str]:
    return [mode.key for mode in CREATIVE_MODES]


def get_mode_labels() -> list[str]:
    return [mode.label for mode in CREATIVE_MODES]


def get_mode_by_key(mode_key: str) -> CreativeMode:
    return _MODE_BY_KEY.get(mode_key, CREATIVE_MODES[0])


def get_mode_by_label(label: str) -> CreativeMode:
    for mode in CREATIVE_MODES:
        if mode.label == label:
            return mode
    return CREATIVE_MODES[0]

