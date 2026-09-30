"""Pure Video status-chip policy (informative profiles, fail-closed)."""

from __future__ import annotations

import pytest

from webex_integration import WebexLaunchState
from webjam_qt.status_chips import (
    ACTIVE_VIDEO_STATES,
    INFORMATIVE_VIDEO_PROFILES,
    video_chip_label,
)


@pytest.mark.parametrize("profile_key", sorted(INFORMATIVE_VIDEO_PROFILES))
def test_informative_hides_not_opened_without_link(profile_key: str) -> None:
    assert (
        video_chip_label(
            profile_key,
            WebexLaunchState.NOT_OPENED.value,
            has_link=False,
        )
        == ""
    )


@pytest.mark.parametrize("profile_key", sorted(INFORMATIVE_VIDEO_PROFILES))
def test_informative_shows_not_opened_with_link(profile_key: str) -> None:
    assert (
        video_chip_label(
            profile_key,
            WebexLaunchState.NOT_OPENED.value,
            has_link=True,
        )
        == WebexLaunchState.NOT_OPENED.value
    )


@pytest.mark.parametrize("profile_key", sorted(INFORMATIVE_VIDEO_PROFILES))
@pytest.mark.parametrize("active_state", sorted(ACTIVE_VIDEO_STATES))
def test_informative_shows_active_states_without_link(
    profile_key: str, active_state: str
) -> None:
    assert video_chip_label(profile_key, active_state, has_link=False) == active_state


@pytest.mark.parametrize("profile_key", sorted(INFORMATIVE_VIDEO_PROFILES))
@pytest.mark.parametrize(
    "state",
    ["", "  ", "ACTIVE", "Joined", "unknown"],
)
def test_informative_hides_empty_and_unknown_without_link(
    profile_key: str, state: str
) -> None:
    assert video_chip_label(profile_key, state, has_link=False) == ""


@pytest.mark.parametrize("profile_key", sorted(INFORMATIVE_VIDEO_PROFILES))
def test_informative_passes_unknown_through_with_link(profile_key: str) -> None:
    assert video_chip_label(profile_key, "ACTIVE", has_link=True) == "ACTIVE"


def test_non_informative_profile_passes_label_unchanged() -> None:
    assert (
        video_chip_label(
            "art",
            WebexLaunchState.NOT_OPENED.value,
            has_link=False,
        )
        == WebexLaunchState.NOT_OPENED.value
    )


def test_whitespace_stripped_before_policy() -> None:
    assert (
        video_chip_label("music", f"  {WebexLaunchState.OPENING.value}  ", has_link=False)
        == WebexLaunchState.OPENING.value
    )
