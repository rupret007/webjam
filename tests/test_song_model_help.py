"""Asking a model about one section: what leaves, what comes back, what cannot.

Nothing here opens a socket. The transport is a fake in every test, which is
also the point: the client is a seam so a live jam can be proven to fail closed
without a key, without a network, and without a provider.
"""

from __future__ import annotations

import json

import pytest

from core.song_form import parse_song_form
from core.song_model_help import (
    MAX_CHORDS_PER_SUGGESTION,
    ask_for_section,
    build_prompt,
    consent_body,
    describe_what_is_sent,
    parse_suggestions,
)
from core.text_model_client import (
    ENDPOINTS,
    SHAPE_ANTHROPIC,
    SHAPE_OPENAI,
    TextModelAuthError,
    TextModelClient,
    TextModelConfigurationError,
    TextModelRequestError,
    TextModelResponse,
    TextModelTransportError,
    missing_model_key_message,
    resolve_model,
    validate_endpoint_url,
)

NOTES = """\
Key: G major
Tempo: 96
Time: 4/4

[Verse]
G D Em C
Waiting on the last train home
[Chorus]
C G D D
"""


class FakeTransport:
    """Records the one request the client makes and returns a canned answer."""

    def __init__(self, *, status: int = 200, payload=None, raises=None) -> None:
        self.status = status
        self.payload = payload if payload is not None else {}
        self.raises = raises
        self.calls: list[dict] = []

    def request(self, method, url, *, headers, body=None, timeout=30.0):
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": dict(headers),
                "body": json.loads(body.decode("utf-8")) if body else None,
                "timeout": timeout,
            }
        )
        if self.raises is not None:
            raise self.raises
        return TextModelResponse(
            status=self.status,
            body=json.dumps(self.payload).encode("utf-8"),
        )


def openai_answer(text: str) -> dict:
    return {"choices": [{"message": {"role": "assistant", "content": text}}]}


def anthropic_answer(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}]}


GOOD_ANSWER = (
    "CHORDS: Em C G D\n"
    "WHY: Starts on the relative minor so the last chorus lands brighter.\n"
    "CHORDS: C D Em Em\n"
    "WHY: Holds the minor for two bars.\n"
)


# ----------------------------------------------------------------------
# What leaves this computer
# ----------------------------------------------------------------------
def test_the_prompt_carries_the_shape_of_the_song_and_nothing_else():
    form = parse_song_form(NOTES, title="Jeff's demo at home")

    sent = describe_what_is_sent(form, section_label="Bridge")

    assert "Key: G major" in sent
    assert "Tempo: 96 BPM" in sent
    assert "Meter: 4/4" in sent
    assert "Verse: G D Em C" in sent
    assert "Write chords for: Bridge" in sent


@pytest.mark.parametrize(
    "forbidden",
    ["Waiting on the last train home", "Jeff's demo at home"],
)
def test_no_lyric_or_title_can_reach_a_provider(forbidden):
    form = parse_song_form(NOTES, title="Jeff's demo at home")

    system, user = build_prompt(form, section_label="Bridge")

    assert forbidden not in user
    assert forbidden not in system


def test_nothing_path_or_link_shaped_can_reach_a_provider():
    """A meter is "4/4"; a payload still may not contain a path or a URL."""

    import re

    notes = (
        "Key: G major\n"
        "Time: 4/4\n"
        "Reference: /Users/jeff/Music/demo.wav\n"
        "https://example.com/room\n"
        "[Verse]\nG D Em C\n"
    )
    form = parse_song_form(notes, title=r"C:\Users\jeff\song")

    system, user = build_prompt(form, section_label="Verse")

    payload = f"{system}\n{user}"
    assert "://" not in payload
    assert "\\" not in payload
    assert re.search(r"(?:^|\s)[~/]\S", payload) is None
    assert re.search(r"[A-Za-z]:[\\/]", payload) is None


def test_the_confirmation_shows_the_text_itself_not_a_summary_of_it():
    form = parse_song_form(NOTES)

    body = consent_body(form, section_label="Bridge", provider_label="OpenAI")

    assert describe_what_is_sent(form, section_label="Bridge") in body
    assert "does not send audio" in body
    assert "suggestion until you keep it" in body


def test_a_very_long_song_is_bounded_before_it_is_sent():
    notes = "Key: C major\n" + "".join(
        f"[Part {index}]\nC F G Am Em Dm Bdim C F G Am Em Dm\n" for index in range(40)
    )
    form = parse_song_form(notes)

    sent = describe_what_is_sent(form, section_label="Part 1")

    assert sent.count("Part ") <= 13
    assert len(sent) < 2000


# ----------------------------------------------------------------------
# What comes back
# ----------------------------------------------------------------------
def test_valid_chord_lines_become_labelled_suggestions():
    suggestions = parse_suggestions(GOOD_ANSWER, provider_label="OpenAI")

    assert [item.chord_line for item in suggestions] == ["Em C G D", "C D Em Em"]
    assert suggestions[0].reason.startswith("Starts on the relative minor")
    assert suggestions[0].describe().endswith("— OpenAI")


def test_words_that_are_not_chords_are_dropped_rather_than_shown():
    suggestions = parse_suggestions(
        "CHORDS: Em maybe C something G\nWHY: ok\n", provider_label="X"
    )

    assert suggestions[0].chords == ("Em", "C", "G")


def test_a_line_with_too_few_real_chords_produces_nothing():
    assert parse_suggestions("CHORDS: hmm G\nWHY: ok\n") == ()


def test_prose_that_never_answers_in_the_format_produces_nothing():
    assert parse_suggestions("Sure! How about G, D, Em and C?") == ()


def test_an_over_long_progression_is_cut_not_refused():
    suggestions = parse_suggestions(
        "CHORDS: " + " ".join(["C"] * 20) + "\nWHY: long\n"
    )

    assert len(suggestions[0].chords) == MAX_CHORDS_PER_SUGGESTION


def test_a_reason_carrying_a_link_is_dropped():
    suggestions = parse_suggestions(
        "CHORDS: C G Am F\nWHY: see https://example.com/buy\n"
    )

    assert suggestions[0].reason == ""


def test_only_three_suggestions_survive_however_many_arrive():
    answer = "".join("CHORDS: C G Am F\nWHY: fine\n" for _ in range(9))

    assert len(parse_suggestions(answer)) == 3


# ----------------------------------------------------------------------
# Request shaping, per provider
# ----------------------------------------------------------------------
def test_openai_shaped_providers_send_messages_and_a_bearer_key():
    transport = FakeTransport(payload=openai_answer(GOOD_ANSWER))
    client = TextModelClient("openai", "sk-secret", transport=transport)

    client.complete(system="s", user="u")

    call = transport.calls[0]
    assert call["url"] == "https://api.openai.com/v1/chat/completions"
    assert call["headers"]["Authorization"] == "Bearer sk-secret"
    assert call["body"]["messages"][0]["role"] == "system"
    assert call["body"]["model"] == "gpt-5.6-luna"


def test_anthropic_sends_its_own_header_and_a_system_field():
    transport = FakeTransport(payload=anthropic_answer(GOOD_ANSWER))
    client = TextModelClient("anthropic", "sk-ant-secret", transport=transport)

    client.complete(system="s", user="u")

    call = transport.calls[0]
    assert call["url"] == "https://api.anthropic.com/v1/messages"
    assert call["headers"]["x-api-key"] == "sk-ant-secret"
    assert "Authorization" not in call["headers"]
    assert call["body"]["system"] == "s"
    assert call["body"]["messages"] == [{"role": "user", "content": "u"}]


def test_every_provider_has_an_https_endpoint_on_its_own_host():
    for endpoint in ENDPOINTS.values():
        assert endpoint.url.startswith(f"https://{endpoint.host}/")
        assert endpoint.shape in {SHAPE_OPENAI, SHAPE_ANTHROPIC}
        assert validate_endpoint_url(endpoint.url, endpoint) == endpoint.url


@pytest.mark.parametrize(
    "url",
    [
        "http://api.openai.com/v1/chat/completions",
        "https://evil.example.com/v1/chat/completions",
        "https://user:pass@api.openai.com/v1/chat/completions",
        "https://api.openai.com:8443/v1/chat/completions",
    ],
)
def test_a_request_off_the_providers_own_host_is_refused(url):
    with pytest.raises(TextModelRequestError):
        validate_endpoint_url(url, ENDPOINTS["openai"])


def test_the_model_id_can_be_overridden_because_model_ids_move():
    assert resolve_model("xai", environ={}) == "grok-4.6"
    assert resolve_model("xai", environ={"WEBJAM_XAI_MODEL": "grok-mini"}) == (
        "grok-mini"
    )


# ----------------------------------------------------------------------
# Failing closed
# ----------------------------------------------------------------------
def test_no_key_means_no_request_at_all():
    transport = FakeTransport()

    with pytest.raises(TextModelConfigurationError):
        TextModelClient("openai", "  ", transport=transport)

    assert transport.calls == []


def test_an_unknown_provider_is_refused_before_any_request():
    with pytest.raises(TextModelConfigurationError):
        TextModelClient("hal9000", "key")


@pytest.mark.parametrize(
    ("status", "expected", "fragment"),
    [
        (401, TextModelAuthError, "rejected this key"),
        (403, TextModelAuthError, "rejected this key"),
        (404, TextModelRequestError, "WEBJAM_OPENAI_MODEL"),
        (429, TextModelRequestError, "rate limiting"),
        (500, TextModelRequestError, "server error"),
        (418, TextModelRequestError, "HTTP 418"),
    ],
)
def test_every_refusal_is_readable_and_never_contains_the_key(
    status, expected, fragment
):
    transport = FakeTransport(status=status, payload={"error": "nope"})
    client = TextModelClient("openai", "sk-super-secret", transport=transport)

    with pytest.raises(expected) as excinfo:
        client.complete(system="s", user="u")

    assert fragment in str(excinfo.value)
    assert "sk-super-secret" not in str(excinfo.value)


def test_an_empty_completion_is_an_error_not_an_empty_suggestion():
    transport = FakeTransport(payload={"choices": [{"message": {"content": ""}}]})
    client = TextModelClient("openai", "sk", transport=transport)

    with pytest.raises(TextModelRequestError):
        client.complete(system="s", user="u")


# ----------------------------------------------------------------------
# The whole path
# ----------------------------------------------------------------------
def test_a_good_answer_becomes_suggestions_for_the_named_part():
    form = parse_song_form(NOTES)
    transport = FakeTransport(payload=openai_answer(GOOD_ANSWER))

    result = ask_for_section(
        form,
        section_label="Bridge",
        provider_id="openai",
        api_key="sk-secret",
        transport=transport,
    )

    assert result.available
    assert result.section_label == "Bridge"
    assert result.suggestions[0].chord_line == "Em C G D"
    assert "Suggestions, not what the song is." in result.headline()


def test_with_no_selection_the_next_missing_part_is_answered():
    form = parse_song_form(NOTES)
    transport = FakeTransport(payload=openai_answer(GOOD_ANSWER))

    result = ask_for_section(
        form, provider_id="openai", api_key="sk", transport=transport
    )

    assert result.section_label == "Bridge"


def test_a_transport_failure_comes_back_as_a_result_not_an_exception():
    form = parse_song_form(NOTES)
    transport = FakeTransport(raises=TextModelTransportError("no network"))

    result = ask_for_section(
        form, provider_id="openai", api_key="sk", transport=transport
    )

    assert not result.available
    assert result.blocked_reason == "no network"


def test_an_unreadable_answer_changes_nothing_and_says_so():
    form = parse_song_form(NOTES)
    transport = FakeTransport(payload=openai_answer("I'd love to help!"))

    result = ask_for_section(
        form, provider_id="openai", api_key="sk", transport=transport
    )

    assert not result.available
    assert "Nothing was changed." in result.blocked_reason


def test_an_empty_song_is_refused_before_anything_is_sent():
    transport = FakeTransport(payload=openai_answer(GOOD_ANSWER))

    result = ask_for_section(
        parse_song_form(""),
        provider_id="openai",
        api_key="sk",
        transport=transport,
    )

    assert not result.available
    assert transport.calls == []
    assert "nothing to ask about" in result.blocked_reason


def test_a_missing_key_never_becomes_a_request():
    transport = FakeTransport(payload=openai_answer(GOOD_ANSWER))

    result = ask_for_section(
        parse_song_form(NOTES),
        provider_id="openai",
        api_key="",
        transport=transport,
    )

    assert not result.available
    assert transport.calls == []
    assert "Settings" in result.blocked_reason


def test_the_missing_key_line_points_at_settings_and_promises_nothing_breaks():
    message = missing_model_key_message("anthropic")

    assert "Anthropic" in message
    assert "Settings" in message
    assert "work without one" in message
    assert "sign up" not in message.lower()


# ----------------------------------------------------------------------
# A model is not a detector (ADR 0002)
# ----------------------------------------------------------------------
def test_a_model_result_carries_no_detected_fact_at_all():
    """It can propose chords. It cannot say what the song's key or lyrics are."""

    from dataclasses import fields

    from core.song_model_help import ModelHelpResult

    names = {field.name for field in fields(ModelHelpResult)}
    assert "lyrics" not in names
    assert "detected_key" not in names
    assert "tempo" not in names
    # ``key`` is echoed back for display only, and comes from the room's form.
    form = parse_song_form(NOTES)
    transport = FakeTransport(payload=openai_answer(GOOD_ANSWER))
    result = ask_for_section(
        form, section_label="Bridge", provider_id="openai", api_key="sk",
        transport=transport,
    )
    assert result.key == "G major"


def test_a_model_answer_never_reaches_the_song_form():
    """The form is what the room wrote plus what Music AI measured. Not this."""

    import ast
    from pathlib import Path

    source = (
        Path(__file__).resolve().parent.parent / "core" / "song_model_help.py"
    ).read_text()
    tree = ast.parse(source)
    imported = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "core.song_workbench" not in imported
    for forbidden in ("with_detected", "merge_sections", "detected_sections"):
        assert forbidden not in source, forbidden
