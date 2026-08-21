"""One small client for the text model a musician brought their own key for.

This is deliberately the least capable model client that can do the job: one
request, no streaming, no tools, no conversation state, a short answer, and a
hard host allowlist per provider. WebJam is not a chat app. The only question
it ever asks is "what could this section of the song do?", and the only thing it
does with the answer is show it, labelled, next to a Keep button.

Providers differ only in three ways — the URL, how the key is presented, and
where the text sits in the response — so those are data and everything else is
shared. Model ids move faster than releases do, so each provider's default is
overridable with an environment variable and an unknown-model refusal says so
by name instead of failing mysteriously.

The API key never appears in an exception message, a log record, or a returned
string. Failures are described by category.
"""

from __future__ import annotations

import json
import logging
import os
import ssl
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Mapping, Protocol
from urllib.parse import urlsplit

from core.provider_credentials import PROVIDERS, provider_spec

LOGGER = logging.getLogger("webjam.core.text_model")

SHAPE_OPENAI = "openai"
SHAPE_ANTHROPIC = "anthropic"

ANTHROPIC_VERSION = "2023-06-01"

_MAX_RESPONSE_BYTES = 512 * 1024
_MAX_OUTPUT_TOKENS = 400


@dataclass(frozen=True, slots=True)
class ProviderEndpoint:
    """Everything provider-specific about one completion request."""

    provider_id: str
    url: str
    host: str
    shape: str
    default_model: str
    model_env_var: str


ENDPOINTS: dict[str, ProviderEndpoint] = {
    "openai": ProviderEndpoint(
        provider_id="openai",
        url="https://api.openai.com/v1/chat/completions",
        host="api.openai.com",
        shape=SHAPE_OPENAI,
        default_model="gpt-5.6-luna",
        model_env_var="WEBJAM_OPENAI_MODEL",
    ),
    "anthropic": ProviderEndpoint(
        provider_id="anthropic",
        url="https://api.anthropic.com/v1/messages",
        host="api.anthropic.com",
        shape=SHAPE_ANTHROPIC,
        default_model="claude-haiku-4-5",
        model_env_var="WEBJAM_ANTHROPIC_MODEL",
    ),
    "xai": ProviderEndpoint(
        provider_id="xai",
        url="https://api.x.ai/v1/chat/completions",
        host="api.x.ai",
        shape=SHAPE_OPENAI,
        default_model="grok-4.6",
        model_env_var="WEBJAM_XAI_MODEL",
    ),
    "minimax": ProviderEndpoint(
        provider_id="minimax",
        url="https://api.minimax.io/v1/chat/completions",
        host="api.minimax.io",
        shape=SHAPE_OPENAI,
        default_model="MiniMax-M3",
        model_env_var="WEBJAM_MINIMAX_MODEL",
    ),
}


class TextModelError(RuntimeError):
    """Base class for every failure this client reports."""


class TextModelConfigurationError(TextModelError):
    """No key, or a provider WebJam does not know."""


class TextModelAuthError(TextModelError):
    """The provider rejected the key."""


class TextModelRequestError(TextModelError):
    """The provider refused the request or returned something unusable."""


class TextModelTransportError(TextModelError):
    """The request could not be completed over the network."""


@dataclass(frozen=True, slots=True)
class TextModelResponse:
    """One HTTP response, already bounded."""

    status: int
    body: bytes

    def json(self) -> Any:
        if not self.body:
            return None
        try:
            return json.loads(self.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TextModelRequestError(
                "That provider returned a response WebJam could not read."
            ) from exc


class TextModelTransport(Protocol):
    """The seam tests replace. No test in this repository opens a socket."""

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes | None = None,
        timeout: float = 30.0,
    ) -> TextModelResponse: ...


class UrllibTextModelTransport:
    """HTTPS over the standard library with WebJam's pinned CA bundle.

    Same reasoning as :class:`core.music_ai_client.UrllibMusicAITransport`:
    frozen Python runtimes do not reliably find an operating-system CA bundle,
    and honouring ``SSL_CERT_FILE`` would let the launch environment redirect
    trust for a request that carries a credential.
    """

    def __init__(self, *, user_agent: str = "WebJam") -> None:
        self.user_agent = str(user_agent).strip() or "WebJam"
        self._opener: urllib.request.OpenerDirector | None = None
        self._lock = threading.Lock()

    def _secure_opener(self) -> urllib.request.OpenerDirector:
        with self._lock:
            if self._opener is not None:
                return self._opener
            try:
                import certifi

                context = ssl.create_default_context(cadata=certifi.contents())
                context.minimum_version = ssl.TLSVersion.TLSv1_2
                if (
                    not context.check_hostname
                    or context.verify_mode != ssl.CERT_REQUIRED
                ):
                    raise ValueError("TLS context is not fail-closed")
                opener = urllib.request.build_opener(
                    urllib.request.HTTPSHandler(context=context)
                )
            except (
                ImportError,
                OSError,
                TypeError,
                ValueError,
                ssl.SSLError,
            ) as exc:
                raise TextModelTransportError(
                    "WebJam's packaged TLS trust data is unavailable."
                ) from exc
            self._opener = opener
            return opener

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes | None = None,
        timeout: float = 30.0,
    ) -> TextModelResponse:
        request = urllib.request.Request(
            url,
            data=body,
            method=str(method).upper(),
            headers={"User-Agent": self.user_agent, **dict(headers)},
        )
        opener = self._secure_opener()
        try:
            with opener.open(request, timeout=float(timeout)) as response:
                return TextModelResponse(
                    status=int(response.status),
                    body=response.read(_MAX_RESPONSE_BYTES),
                )
        except urllib.error.HTTPError as exc:
            try:
                payload = exc.read(_MAX_RESPONSE_BYTES)
            except OSError:
                payload = b""
            return TextModelResponse(status=int(exc.code), body=payload)
        except (OSError, urllib.error.URLError, ssl.SSLError) as exc:
            raise TextModelTransportError(
                "WebJam could not reach that provider. Check the network."
            ) from exc


def resolve_model(provider_id: str, *, environ: Mapping[str, str] | None = None) -> str:
    """Return the model id to ask for, honouring the environment override."""

    endpoint = ENDPOINTS.get(str(provider_id or "").strip().lower())
    if endpoint is None:
        return ""
    source = environ if environ is not None else os.environ
    override = str(source.get(endpoint.model_env_var, "") or "").strip()
    return override or endpoint.default_model


def validate_endpoint_url(url: str, endpoint: ProviderEndpoint) -> str:
    """Return ``url`` only if it is this provider's own HTTPS host."""

    try:
        parts = urlsplit(str(url or ""))
        port = parts.port
    except ValueError as exc:
        raise TextModelRequestError("That provider URL is malformed.") from exc
    host = (parts.hostname or "").strip(".").lower()
    if (
        parts.scheme != "https"
        or host != endpoint.host
        or parts.username is not None
        or parts.password is not None
        or port not in {None, 443}
    ):
        raise TextModelRequestError(
            "WebJam refused a model request outside that provider's own host."
        )
    return url


class TextModelClient:
    """Ask one provider for one short completion. Nothing else."""

    def __init__(
        self,
        provider_id: str,
        api_key: str,
        *,
        transport: TextModelTransport | None = None,
        model: str = "",
        timeout: float = 30.0,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        endpoint = ENDPOINTS.get(str(provider_id or "").strip().lower())
        if endpoint is None:
            raise TextModelConfigurationError("WebJam does not know that provider.")
        key = str(api_key or "").strip()
        if not key:
            raise TextModelConfigurationError(missing_model_key_message(provider_id))
        self.endpoint = endpoint
        self.model = str(model or "").strip() or resolve_model(
            endpoint.provider_id, environ=environ
        )
        self._api_key = key
        self._transport = transport or UrllibTextModelTransport()
        self._timeout = float(timeout)

    @property
    def provider_id(self) -> str:
        return self.endpoint.provider_id

    @property
    def label(self) -> str:
        spec = provider_spec(self.endpoint.provider_id)
        return spec.label if spec is not None else self.endpoint.provider_id

    def complete(
        self,
        *,
        system: str,
        user: str,
        max_output_tokens: int = _MAX_OUTPUT_TOKENS,
    ) -> str:
        """Return the model's text, or raise. Never returns a partial guess."""

        url = validate_endpoint_url(self.endpoint.url, self.endpoint)
        headers, payload = self._shape(
            system=system, user=user, max_output_tokens=max_output_tokens
        )
        response = self._transport.request(
            "POST",
            url,
            headers=headers,
            body=json.dumps(payload).encode("utf-8"),
            timeout=self._timeout,
        )
        self._raise_for_status(response)
        return _extract_text(response.json(), self.endpoint.shape)

    def _shape(
        self,
        *,
        system: str,
        user: str,
        max_output_tokens: int,
    ) -> tuple[dict[str, str], dict[str, Any]]:
        limit = max(32, min(int(max_output_tokens), 2000))
        if self.endpoint.shape == SHAPE_ANTHROPIC:
            return (
                {
                    "x-api-key": self._api_key,
                    "anthropic-version": ANTHROPIC_VERSION,
                    "Content-Type": "application/json",
                },
                {
                    "model": self.model,
                    "max_tokens": limit,
                    "system": system,
                    "messages": [{"role": "user", "content": user}],
                },
            )
        return (
            {
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            {
                "model": self.model,
                "max_tokens": limit,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
        )

    def _raise_for_status(self, response: TextModelResponse) -> None:
        status = int(response.status)
        if status < 400:
            return
        if status in {401, 403}:
            raise TextModelAuthError(
                f"{self.label} rejected this key. Check it in Settings."
            )
        if status == 404:
            # Model ids move faster than WebJam releases do, so name the
            # override rather than leaving a musician guessing.
            raise TextModelRequestError(
                f"{self.label} does not have the model \"{self.model}\". Set "
                f"{self.endpoint.model_env_var} to one your account can use."
            )
        if status == 429:
            raise TextModelRequestError(
                f"{self.label} is rate limiting this key right now. Try again "
                "in a minute."
            )
        if status >= 500:
            raise TextModelRequestError(f"{self.label} had a server error.")
        raise TextModelRequestError(
            f"{self.label} refused the request (HTTP {status})."
        )


def _extract_text(payload: Any, shape: str) -> str:
    """Pull the completion text out, or raise rather than return ``""``."""

    if not isinstance(payload, Mapping):
        raise TextModelRequestError("That provider returned nothing usable.")
    if shape == SHAPE_ANTHROPIC:
        blocks = payload.get("content")
        if isinstance(blocks, list):
            parts = [
                str(block.get("text") or "")
                for block in blocks
                if isinstance(block, Mapping) and block.get("type") in {None, "text"}
            ]
            text = "\n".join(part for part in parts if part).strip()
            if text:
                return text
        raise TextModelRequestError("That provider returned nothing usable.")

    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, Mapping):
            message = first.get("message")
            if isinstance(message, Mapping):
                text = str(message.get("content") or "").strip()
                if text:
                    return text
    raise TextModelRequestError("That provider returned nothing usable.")


def missing_model_key_message(provider_id: str = "") -> str:
    """Return the one line shown when write-help has no model key at all."""

    spec = provider_spec(provider_id)
    if spec is not None:
        return (
            f"Add a {spec.label} key in Settings to ask a model. "
            "WebJam's own suggestions work without one."
        )
    return (
        "Add a model key in Settings to ask a model. WebJam's own suggestions "
        "work without one."
    )


def known_provider_ids() -> tuple[str, ...]:
    """Return the text providers this client can actually call."""

    return tuple(
        provider_id for provider_id in PROVIDERS if provider_id in ENDPOINTS
    )


__all__ = [
    "ANTHROPIC_VERSION",
    "ENDPOINTS",
    "SHAPE_ANTHROPIC",
    "SHAPE_OPENAI",
    "ProviderEndpoint",
    "TextModelAuthError",
    "TextModelClient",
    "TextModelConfigurationError",
    "TextModelError",
    "TextModelRequestError",
    "TextModelResponse",
    "TextModelTransport",
    "TextModelTransportError",
    "UrllibTextModelTransport",
    "known_provider_ids",
    "missing_model_key_message",
    "resolve_model",
    "validate_endpoint_url",
]
