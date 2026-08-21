"""Version-scoped musician-name contract for Jamulus boundaries.

Jamulus stores the client name in a ``QString`` and its mixer presents the
name as two lines of eight user-visible characters.  Those are two distinct
constraints:

* the wire/profile value is limited to 16 UTF-16 code units;
* the musician-facing preview wraps after eight grapheme clusters.

Keeping the rules here prevents onboarding, saved settings, process arguments,
profiles, and JSON-RPC from silently applying different truncation rules.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

DEFAULT_JAMULUS_NAME = "WebJam Musician"
DEFAULT_JAMULUS_NAME_VERSION = "3.12.3"
JAMULUS_NAME_HELP = (
    "Jamulus displays up to 16 characters and wraps after 8; "
    "use a short stage name for one line."
)


class JamulusNameError(ValueError):
    """A musician name cannot be represented by the selected Jamulus build."""


@dataclass(frozen=True)
class JamulusNameContract:
    """Name limits verified for one compatible Jamulus release."""

    version: str
    max_utf16_units: int = 16
    mixer_wrap_graphemes: int = 8


@dataclass(frozen=True)
class ValidatedJamulusName:
    """A validated name plus its exact native-mixer presentation."""

    value: str
    version: str
    utf16_units: int
    graphemes: tuple[str, ...]
    first_line: str
    second_line: str

    @property
    def wraps(self) -> bool:
        return bool(self.second_line)

    @property
    def preview(self) -> str:
        return (
            f"{self.first_line}\n{self.second_line}"
            if self.second_line
            else self.first_line
        )


_CONTRACTS = {
    version: JamulusNameContract(version)
    for version in ("3.12.2", "3.12.3")
}


def _normalized_version(version: object) -> str:
    value = str(version or "").strip()
    if value.startswith("r") and "_" in value:
        value = value[1:].replace("_", ".")
    return value


def jamulus_name_contract(
    version: object = DEFAULT_JAMULUS_NAME_VERSION,
) -> JamulusNameContract:
    """Return the exact name contract for an approved Jamulus version."""

    normalized = _normalized_version(version)
    try:
        return _CONTRACTS[normalized]
    except KeyError as exc:
        raise JamulusNameError(
            "WebJam has not verified musician-name handling for this Jamulus version."
        ) from exc


def utf16_units(value: str) -> int:
    """Count the units a Qt ``QString`` uses without accepting lone surrogates."""

    try:
        return len(value.encode("utf-16-le")) // 2
    except UnicodeEncodeError as exc:
        raise JamulusNameError(
            "Musician name contains an unsupported Unicode character."
        ) from exc


def _is_regional_indicator(character: str) -> bool:
    point = ord(character)
    return 0x1F1E6 <= point <= 0x1F1FF


def _is_emoji_modifier(character: str) -> bool:
    point = ord(character)
    return 0x1F3FB <= point <= 0x1F3FF


def _is_variation_selector(character: str) -> bool:
    point = ord(character)
    return (
        0xFE00 <= point <= 0xFE0F
        or 0xE0100 <= point <= 0xE01EF
    )


def _hangul_type(character: str) -> str:
    """Return the UAX #29 Hangul syllable type needed for cluster boundaries."""

    point = ord(character)
    if 0x1100 <= point <= 0x115F or 0xA960 <= point <= 0xA97C:
        return "L"
    if 0x1160 <= point <= 0x11A7 or 0xD7B0 <= point <= 0xD7C6:
        return "V"
    if 0x11A8 <= point <= 0x11FF or 0xD7CB <= point <= 0xD7FB:
        return "T"
    if 0xAC00 <= point <= 0xD7A3:
        return "LV" if (point - 0xAC00) % 28 == 0 else "LVT"
    return ""


def _hangul_joins(previous: str, current: str) -> bool:
    prior_type = _hangul_type(previous)
    current_type = _hangul_type(current)
    return (
        (prior_type == "L" and current_type in {"L", "V", "LV", "LVT"})
        or (prior_type in {"LV", "V"} and current_type in {"V", "T"})
        or (prior_type in {"LVT", "T"} and current_type == "T")
    )


def grapheme_clusters(value: str) -> tuple[str, ...]:
    """Split without breaking combining, emoji-ZWJ, flag, or Hangul sequences.

    This is the subset of Unicode extended-grapheme boundaries relevant to a
    short display name.  It deliberately has no third-party dependency so the
    rule is identical in source checkouts and all four frozen applications.
    """

    clusters: list[str] = []
    regional_run = 0
    for character in value:
        if not clusters:
            clusters.append(character)
            regional_run = 1 if _is_regional_indicator(character) else 0
            continue

        category = unicodedata.category(character)
        previous = clusters[-1][-1]
        joins = (
            category in {"Mn", "Mc", "Me"}
            or _is_variation_selector(character)
            or _is_emoji_modifier(character)
            or character == "\u200d"
            or previous == "\u200d"
            or _hangul_joins(previous, character)
        )
        if _is_regional_indicator(character):
            joins = regional_run % 2 == 1

        if joins:
            clusters[-1] += character
        else:
            clusters.append(character)

        if _is_regional_indicator(character):
            regional_run += 1
        else:
            regional_run = 0
    return tuple(clusters)


def validate_jamulus_name(
    value: object,
    *,
    version: object = DEFAULT_JAMULUS_NAME_VERSION,
) -> ValidatedJamulusName:
    """Validate and return the exact value that may cross a Jamulus boundary.

    Surrounding whitespace is treated like ordinary form whitespace and
    removed.  No internal character is normalized, replaced, or abbreviated.
    """

    contract = jamulus_name_contract(version)
    name = str(value or "").strip()
    if not name:
        raise JamulusNameError("Enter a musician name.")

    for character in name:
        category = unicodedata.category(character)
        if (
            category in {"Cc", "Cs", "Zl", "Zp"}
            or (category == "Cf" and character not in {"\u200c", "\u200d"})
        ):
            raise JamulusNameError(
                "Musician name cannot contain control characters or line breaks."
            )

    unit_count = utf16_units(name)
    if unit_count > contract.max_utf16_units:
        raise JamulusNameError(
            "Musician name is too long for Jamulus. Use 16 characters or "
            "fewer; emoji can use two."
        )

    clusters = grapheme_clusters(name)
    split = contract.mixer_wrap_graphemes
    return ValidatedJamulusName(
        value=name,
        version=contract.version,
        utf16_units=unit_count,
        graphemes=clusters,
        first_line="".join(clusters[:split]),
        second_line="".join(clusters[split:]),
    )


def recover_jamulus_name(
    value: object,
    *,
    fallback: str = DEFAULT_JAMULUS_NAME,
    version: object = DEFAULT_JAMULUS_NAME_VERSION,
) -> str:
    """Recover an untrusted legacy/config/environment value without truncation."""

    try:
        return validate_jamulus_name(value, version=version).value
    except JamulusNameError:
        return validate_jamulus_name(fallback, version=version).value
