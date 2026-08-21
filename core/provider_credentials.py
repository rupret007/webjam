"""The optional keys a musician brings, and the one place WebJam reads them.

Nothing in a WebJam jam requires a key. Jamulus carries the band, the Shared
Track carries the song, the conductor and Studio sections carry the shape, a
meeting sits beside all of it, and muting the live mix is a fader. Every one of
those works on a computer that has never seen an API key, and a test in
``tests/test_zero_key_music.py`` holds that line.

Two separate optional things sit on top:

* **Music AI** (the developer platform behind Moises) answers questions *about
  an audio file* — stems, chords, lyrics, sections, BPM. It is an audio-facts
  key. Song tools use it; the jam does not.
* **A text model** (OpenAI, Anthropic, xAI, MiniMax) answers "what could the
  bridge do?" as a *suggestion*. Write-help uses it; nothing else does.

They are different credentials for different jobs and neither implies the
other. This module is the shared registry for both, so the Art profile — or
anything else that later wants a musician's own key — inherits the same
provider ids, the same storage rule, and the same redaction behaviour instead
of inventing a second settings schema.

The storage rule is the whole point: a key goes in the operating system's
credential store (see :mod:`core.secret_store`) or it comes from the
environment. It is never written into ``~/.webjam_config.json``, never logged,
and never committed. The one exception is documented below and is a migration,
not a design.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Mapping

from core.secret_store import (
    SecretStore,
    SecretStoreError,
    StoreOutcome,
    default_secret_store,
)

KIND_TEXT = "text"
KIND_AUDIO = "audio"

SOURCE_NONE = ""
SOURCE_ENVIRONMENT = "environment"
SOURCE_STORE = "store"
SOURCE_LEGACY_SETTINGS = "settings-file"

# Published API keys are ASCII tokens. Anything with a space or a newline in it
# is a pasted paragraph, not a key, and saying so beats a 401 an hour later.
_MAX_KEY_CHARS = 512


@dataclass(frozen=True, slots=True)
class ProviderSpec:
    """One provider a musician may bring their own key for."""

    id: str
    label: str
    kind: str
    purpose: str
    env_vars: tuple[str, ...]
    console_url: str
    placeholder: str = ""
    # Music AI shipped before this module existed and its key is already in the
    # private 0600 settings file for anyone who typed it there. That value is
    # still honoured so an upgrade does not silently lose a working key, but
    # nothing writes a key back to that field.
    legacy_settings_field: str = ""

    @property
    def is_text(self) -> bool:
        return self.kind == KIND_TEXT


TEXT_PROVIDER_IDS: tuple[str, ...] = ("openai", "anthropic", "xai", "minimax")
AUDIO_PROVIDER_IDS: tuple[str, ...] = ("music_ai",)

PROVIDERS: dict[str, ProviderSpec] = {
    "openai": ProviderSpec(
        id="openai",
        label="OpenAI",
        kind=KIND_TEXT,
        purpose="Writing help suggestions",
        env_vars=("WEBJAM_OPENAI_API_KEY", "OPENAI_API_KEY"),
        console_url="https://platform.openai.com/api-keys",
        placeholder="sk-…",
    ),
    "anthropic": ProviderSpec(
        id="anthropic",
        label="Anthropic",
        kind=KIND_TEXT,
        purpose="Writing help suggestions",
        env_vars=("WEBJAM_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY"),
        console_url="https://console.anthropic.com/settings/keys",
        placeholder="sk-ant-…",
    ),
    "xai": ProviderSpec(
        id="xai",
        label="xAI (Grok)",
        kind=KIND_TEXT,
        purpose="Writing help suggestions",
        env_vars=("WEBJAM_XAI_API_KEY", "XAI_API_KEY"),
        console_url="https://console.x.ai",
        placeholder="xai-…",
    ),
    "minimax": ProviderSpec(
        id="minimax",
        label="MiniMax",
        kind=KIND_TEXT,
        purpose="Writing help suggestions",
        env_vars=("WEBJAM_MINIMAX_API_KEY", "MINIMAX_API_KEY"),
        console_url="https://www.minimax.io/platform",
    ),
    "music_ai": ProviderSpec(
        id="music_ai",
        label="Music AI",
        kind=KIND_AUDIO,
        purpose="Song tools: stems, chords, lyrics, sections",
        env_vars=("WEBJAM_MUSIC_AI_API_KEY", "MUSIC_AI_API_KEY"),
        console_url="https://music.ai/dash",
        legacy_settings_field="music_ai_api_key",
    ),
}


@dataclass(frozen=True, slots=True)
class ResolvedKey:
    """A resolved credential and where it came from.

    ``value`` is kept out of ``repr`` so a stray debug print, an exception
    chain, or a Sentry frame local cannot spill it.
    """

    provider_id: str
    value: str = field(default="", repr=False)
    source: str = SOURCE_NONE

    @property
    def present(self) -> bool:
        return bool(self.value)

    def describe_source(self) -> str:
        if not self.present:
            return "not set"
        if self.source == SOURCE_ENVIRONMENT:
            return "from the environment"
        if self.source == SOURCE_STORE:
            return "saved in this computer's credential store"
        if self.source == SOURCE_LEGACY_SETTINGS:
            return "from the older settings file"
        return "set"


@dataclass(frozen=True, slots=True)
class SaveResult:
    """What happened when a musician pressed Save on a key."""

    saved: bool
    reason: str = ""

    @property
    def failed(self) -> bool:
        return not self.saved


def provider_spec(provider_id: str) -> ProviderSpec | None:
    return PROVIDERS.get(str(provider_id or "").strip().lower())


def text_providers() -> tuple[ProviderSpec, ...]:
    return tuple(PROVIDERS[key] for key in TEXT_PROVIDER_IDS)


def audio_providers() -> tuple[ProviderSpec, ...]:
    return tuple(PROVIDERS[key] for key in AUDIO_PROVIDER_IDS)


def validate_key(value: str) -> str:
    """Return a storable key, or raise with a line worth showing.

    Deliberately shape-only. WebJam does not know what a valid OpenAI key looks
    like this month, and rejecting a working key because its prefix changed
    would be worse than letting the provider answer.
    """

    candidate = str(value or "").strip()
    if not candidate:
        raise SecretStoreError("Paste a key first.")
    if len(candidate) > _MAX_KEY_CHARS:
        raise SecretStoreError("That is longer than any API key WebJam accepts.")
    if any(character.isspace() for character in candidate):
        raise SecretStoreError(
            "That has spaces or line breaks in it, so it is not a key. "
            "Copy just the key."
        )
    if not all(0x21 <= ord(character) <= 0x7E for character in candidate):
        raise SecretStoreError(
            "That contains characters an API key cannot have. Copy just the key."
        )
    return candidate


class ProviderCredentials:
    """Reads and writes the musician's own keys. Never persists one in plain text."""

    def __init__(
        self,
        *,
        store: SecretStore | None = None,
        environ: Mapping[str, str] | None = None,
        settings: Any = None,
    ) -> None:
        self._store = store
        self._environ = environ
        self._settings = settings

    @property
    def store(self) -> SecretStore:
        return self._store if self._store is not None else default_secret_store()

    @property
    def environ(self) -> Mapping[str, str]:
        return self._environ if self._environ is not None else os.environ

    def resolve(self, provider_id: str) -> ResolvedKey:
        """Return the key for one provider, and where WebJam found it.

        Order is environment, then the OS store, then — for Music AI only — the
        older private settings file. The environment wins so a musician can
        override a saved key for one launch without editing anything, which is
        the same precedence every other WebJam setting uses.
        """

        spec = provider_spec(provider_id)
        if spec is None:
            return ResolvedKey(provider_id=str(provider_id or ""))

        for name in spec.env_vars:
            raw = str(self.environ.get(name, "") or "").strip()
            if raw:
                return ResolvedKey(spec.id, raw, SOURCE_ENVIRONMENT)

        stored = ""
        store = self.store
        if store.usable():
            try:
                stored = str(store.get(spec.id) or "").strip()
            except SecretStoreError:
                stored = ""
        if stored:
            return ResolvedKey(spec.id, stored, SOURCE_STORE)

        if spec.legacy_settings_field and self._settings is not None:
            legacy = str(
                getattr(self._settings, spec.legacy_settings_field, "") or ""
            ).strip()
            if legacy:
                return ResolvedKey(spec.id, legacy, SOURCE_LEGACY_SETTINGS)

        return ResolvedKey(spec.id)

    def api_key(self, provider_id: str) -> str:
        return self.resolve(provider_id).value

    def has_key(self, provider_id: str) -> bool:
        return bool(self.api_key(provider_id))

    def configured_ids(self, kind: str = "") -> tuple[str, ...]:
        """Return the providers that actually have a key right now."""

        wanted = str(kind or "").strip()
        return tuple(
            spec.id
            for spec in PROVIDERS.values()
            if (not wanted or spec.kind == wanted) and self.has_key(spec.id)
        )

    def configured_text_ids(self) -> tuple[str, ...]:
        return tuple(
            provider_id
            for provider_id in TEXT_PROVIDER_IDS
            if self.has_key(provider_id)
        )

    def save(self, provider_id: str, value: str) -> SaveResult:
        """Put one key in the OS credential store, or explain why not."""

        spec = provider_spec(provider_id)
        if spec is None:
            return SaveResult(saved=False, reason="WebJam does not know that provider.")
        try:
            key = validate_key(value)
        except SecretStoreError as exc:
            return SaveResult(saved=False, reason=str(exc))

        store = self.store
        if not store.usable():
            return SaveResult(
                saved=False,
                reason=(
                    f"{no_store_reason(store)} Set {spec.env_vars[-1]} in your "
                    "environment instead — WebJam will not write a key to disk "
                    "in plain text."
                ),
            )
        try:
            outcome: StoreOutcome = store.put(spec.id, key)
        except SecretStoreError as exc:
            return SaveResult(saved=False, reason=str(exc))
        if outcome.failed:
            return SaveResult(saved=False, reason=outcome.reason)
        return SaveResult(saved=True)

    def clear(self, provider_id: str) -> bool:
        """Remove a stored key. An environment key is the shell's to remove."""

        spec = provider_spec(provider_id)
        if spec is None:
            return False
        store = self.store
        if not store.usable():
            return False
        try:
            return bool(store.delete(spec.id))
        except SecretStoreError:
            return False


def no_store_reason(store: SecretStore) -> str:
    """Return the honest one-liner for a computer with no credential store.

    Read off the store rather than probed: writing a dummy secret to find out
    whether writing works would be a real keychain entry nobody asked for.
    """

    reason = str(getattr(store, "reason", "") or "").strip()
    return reason or "This computer has no credential store WebJam can use."


def storage_note() -> str:
    """One line for the Settings dialog about where a key goes."""

    return (
        "Keys are saved in this computer's credential store, not in WebJam's "
        "settings file. WebJam never writes a key to disk in plain text and "
        "never sends one anywhere except that provider."
    )


__all__ = [
    "AUDIO_PROVIDER_IDS",
    "KIND_AUDIO",
    "KIND_TEXT",
    "PROVIDERS",
    "SOURCE_ENVIRONMENT",
    "SOURCE_LEGACY_SETTINGS",
    "SOURCE_NONE",
    "SOURCE_STORE",
    "TEXT_PROVIDER_IDS",
    "ProviderCredentials",
    "ProviderSpec",
    "ResolvedKey",
    "SaveResult",
    "audio_providers",
    "no_store_reason",
    "provider_spec",
    "storage_note",
    "text_providers",
    "validate_key",
]
