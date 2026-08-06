from __future__ import annotations

import pytest

from core.audio_feedback_guard import (
    AudioFeedbackRisk,
    assess_audio_feedback_risk,
)


@pytest.mark.parametrize(
    ("input_name", "output_name"),
    (
        ("Built-in Microphone", "Built-in Output"),
        ("Internal Microphone", "Internal Speakers"),
        ("MacBook Pro Microphone", "MacBook Pro Speakers"),
        ("Mac mini Microphone", "Mac mini Speakers"),
        ("Studio Display Microphone", "Studio Display Speakers"),
    ),
)
def test_clear_builtin_microphone_and_speaker_pairs_warn(
    input_name: str,
    output_name: str,
) -> None:
    assessment = assess_audio_feedback_risk(input_name, output_name)

    assert assessment.risk is AudioFeedbackRisk.BUILTIN_MIC_AND_SPEAKERS
    assert assessment.should_warn is True


@pytest.mark.parametrize(
    ("input_name", "output_name"),
    (
        ("Built-in Microphone", "External Headphones"),
        ("Built-in Microphone", "Scarlett USB Audio Interface"),
        ("Built-in Microphone", "HDMI Output"),
        ("Built-in Microphone", "AirPods Pro Bluetooth"),
        ("Scarlett USB Audio Interface", "MacBook Pro Speakers"),
        ("BlackHole 16ch", "Built-in Output"),
    ),
)
def test_isolated_or_external_side_never_warns(
    input_name: str,
    output_name: str,
) -> None:
    assessment = assess_audio_feedback_risk(input_name, output_name)

    assert assessment.risk is AudioFeedbackRisk.NOT_DETECTED
    assert assessment.should_warn is False


@pytest.mark.parametrize(
    ("input_name", "output_name"),
    (
        ("", ""),
        ("Unknown input", "Unknown output"),
        ("Built-in Microphone", "Unknown output"),
        ("Unknown input", "Built-in Output"),
    ),
)
def test_missing_or_ambiguous_names_remain_unknown(
    input_name: str,
    output_name: str,
) -> None:
    assessment = assess_audio_feedback_risk(input_name, output_name)

    assert assessment.risk is AudioFeedbackRisk.UNKNOWN
    assert assessment.should_warn is False
