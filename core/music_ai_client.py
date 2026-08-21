"""A fail-closed client for the Music AI developer API (api.music.ai/v1).

Music AI is the developer platform behind Moises. The two are not the same
account system: the key this client needs is created at https://music.ai/dash
and sent verbatim in the ``Authorization`` header, with no ``Bearer`` prefix.
A consumer Moises app password is not a credential here and this module never
asks for one.

Everything the API does is asynchronous and file-shaped:

    GET  /upload      → a signed uploadUrl / downloadUrl pair
    PUT  uploadUrl    → the bytes of a file the user chose
    POST /job         → run a workflow slug against that downloadUrl
    GET  /job/:id     → poll until SUCCEEDED or FAILED
    GET  /workflow    → the slugs this particular key can actually run

The last one matters more than it looks. Workflow slugs are account-specific —
the API reference's own example lists a beat-and-BPM workflow under the slug
``untitled-workflow-e78c2e`` — so the set of things a key can run is discovered
at runtime, never assumed. The one slug this module is willing to name without
asking is ``music-ai/stems-vocals-accompaniment``, which the quick-start
documents as living in the shared ``music-ai`` namespace "accessible to all
users".

No call here happens implicitly. Uploads require a caller to have chosen a
specific local file, and :class:`MusicAIClient` refuses to construct itself
without a key rather than degrading to an anonymous mode.
"""

from __future__ import annotations

import json
import mimetypes
import ssl
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import urlsplit

API_BASE_URL = "https://api.music.ai/v1"
API_KEY_CONSOLE_URL = "https://music.ai/dash"
API_KEY_ENV_VAR = "MUSIC_AI_API_KEY"

# The one workflow slug the public docs name as available to every account.
DOCUMENTED_STEMS_WORKFLOW = "music-ai/stems-vocals-accompaniment"

# Hosts this client will talk to. The API itself is api.music.ai; signed upload
# targets are Google Cloud Storage and finished results are served from the
# Music AI CDN, both named in the published request/response examples.
_ALLOWED_HOSTS = frozenset(
    {
        "api.music.ai",
        "storage.googleapis.com",
        "cdn.music.ai",
    }
)

_TERMINAL_STATUSES = frozenset({"SUCCEEDED", "FAILED"})
_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_MAX_UPLOAD_BYTES = 512 * 1024 * 1024
_MAX_PAGES = 20
_PAGE_SIZE = 100


class MusicAIError(RuntimeError):
    """Base class for every failure this client reports."""


class MusicAIConfigurationError(MusicAIError):
    """No usable API key, so nothing was attempted."""


class MusicAIAuthError(MusicAIError):
    """The API rejected the key (HTTP 401)."""


class MusicAIRequestError(MusicAIError):
    """The API refused a request or returned something unusable."""


class MusicAITransportError(MusicAIError):
    """The request could not be completed over the network."""


class MusicAIJobError(MusicAIError):
    """A job reached FAILED, or stopped reporting progress."""

    def __init__(self, message: str, *, code: str = "", job_id: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.job_id = job_id


@dataclass(frozen=True, slots=True)
class MusicAIResponse:
    """One HTTP response, already bounded and decoded."""

    status: int
    body: bytes

    def json(self) -> Any:
        if not self.body:
            return None
        try:
            return json.loads(self.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MusicAIRequestError(
                "Music AI returned a response WebJam could not read."
            ) from exc


class MusicAITransport(Protocol):
    """The seam tests replace. No test in this repository opens a socket."""

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes | None = None,
        timeout: float = 30.0,
    ) -> MusicAIResponse: ...


@dataclass(frozen=True, slots=True)
class MusicAIApplication:
    """The application (API key) the current credential belongs to."""

    id: str
    name: str


@dataclass(frozen=True, slots=True)
class MusicAIWorkflow:
    """One workflow this key can run, exactly as the account defines it."""

    id: str
    name: str
    slug: str
    description: str = ""

    @property
    def search_text(self) -> str:
        return f"{self.name} {self.slug} {self.description}".lower()


@dataclass(frozen=True, slots=True)
class MusicAIUploadTarget:
    """A temporary signed upload/download URL pair from ``GET /upload``."""

    upload_url: str
    download_url: str


@dataclass(frozen=True, slots=True)
class MusicAIJob:
    """A job as the API reports it."""

    id: str
    status: str
    workflow: str = ""
    name: str = ""
    result: Mapping[str, Any] = field(default_factory=dict)
    error_code: str = ""
    error_title: str = ""
    error_message: str = ""

    @property
    def succeeded(self) -> bool:
        return self.status == "SUCCEEDED"

    @property
    def failed(self) -> bool:
        return self.status == "FAILED"

    @property
    def finished(self) -> bool:
        return self.status in _TERMINAL_STATUSES

    def failure_text(self) -> str:
        parts = [part for part in (self.error_title, self.error_message) if part]
        return " — ".join(parts) or "Music AI reported a failed job."


class UrllibMusicAITransport:
    """HTTPS transport over the standard library with a pinned CA bundle.

    This mirrors :class:`core.component_download.UrllibHttpsTransport`: frozen
    Python runtimes do not reliably find an operating-system CA bundle, and
    honouring ``SSL_CERT_FILE`` would let the launch environment redirect
    trust. Certifi is already a locked WebJam runtime dependency.
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
                if not context.check_hostname or context.verify_mode != ssl.CERT_REQUIRED:
                    raise ValueError("TLS context is not fail-closed")
                opener = urllib.request.build_opener(
                    urllib.request.HTTPSHandler(context=context)
                )
            except (ImportError, OSError, TypeError, ValueError, ssl.SSLError) as exc:
                raise MusicAITransportError(
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
    ) -> MusicAIResponse:
        validate_music_ai_url(url)
        request = urllib.request.Request(
            url,
            data=body,
            method=str(method).upper(),
            headers={"User-Agent": self.user_agent, **dict(headers)},
        )
        opener = self._secure_opener()
        try:
            with opener.open(request, timeout=float(timeout)) as response:
                return MusicAIResponse(
                    status=int(response.status),
                    body=response.read(_MAX_RESPONSE_BYTES),
                )
        except urllib.error.HTTPError as exc:
            try:
                payload = exc.read(_MAX_RESPONSE_BYTES)
            except OSError:
                payload = b""
            return MusicAIResponse(status=int(exc.code), body=payload)
        except (OSError, urllib.error.URLError, ssl.SSLError) as exc:
            # The URL can carry a signed token, so it never reaches the message.
            raise MusicAITransportError(
                "WebJam could not reach Music AI. Check the network and retry."
            ) from exc


def validate_music_ai_url(url: str) -> str:
    """Return ``url`` if it is an allowlisted HTTPS Music AI endpoint."""

    if not isinstance(url, str) or not url or len(url) > 4096:
        raise MusicAIRequestError("Music AI returned an unusable URL.")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise MusicAIRequestError("Music AI returned a malformed URL.") from exc
    host = (parts.hostname or "").strip(".").lower()
    if (
        parts.scheme != "https"
        or host not in _ALLOWED_HOSTS
        or parts.username is not None
        or parts.password is not None
        or port not in {None, 443}
    ):
        raise MusicAIRequestError(
            "Music AI returned a URL outside its published hosts."
        )
    return url


class MusicAIClient:
    """Typed access to the Music AI jobs API.

    The client is useless without a key and says so at construction time
    rather than producing an anonymous client that fails later at an
    unpredictable point.
    """

    def __init__(
        self,
        api_key: str,
        *,
        transport: MusicAITransport | None = None,
        base_url: str = API_BASE_URL,
        timeout: float = 30.0,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        key = str(api_key or "").strip()
        if not key:
            raise MusicAIConfigurationError(missing_key_message())
        self._api_key = key
        self._transport = transport or UrllibMusicAITransport()
        self._base_url = str(base_url).rstrip("/")
        self._timeout = float(timeout)
        self._sleep = sleep
        self._monotonic = monotonic

    @property
    def transport(self) -> MusicAITransport:
        """The transport this client uses, for downloading finished results."""

        return self._transport

    # ------------------------------------------------------------------
    # Applications and workflows
    # ------------------------------------------------------------------
    def application(self) -> MusicAIApplication:
        """Return the application behind this key. Used to validate it."""

        payload = self._json("GET", "/application")
        if not isinstance(payload, Mapping):
            raise MusicAIRequestError("Music AI returned an unexpected application.")
        return MusicAIApplication(
            id=str(payload.get("id") or ""),
            name=str(payload.get("name") or "Music AI application"),
        )

    def list_workflows(self) -> tuple[MusicAIWorkflow, ...]:
        """Return every workflow this key can run, following pagination."""

        workflows: list[MusicAIWorkflow] = []
        seen: set[str] = set()
        for page in range(_MAX_PAGES):
            payload = self._json(
                "GET", f"/workflow?page={page}&size={_PAGE_SIZE}"
            )
            entries = _workflow_entries(payload)
            if not entries:
                break
            for entry in entries:
                slug = str(entry.get("slug") or "").strip()
                if not slug or slug in seen:
                    continue
                seen.add(slug)
                workflows.append(
                    MusicAIWorkflow(
                        id=str(entry.get("id") or ""),
                        name=str(entry.get("name") or slug),
                        slug=slug,
                        description=str(entry.get("description") or ""),
                    )
                )
            if len(entries) < _PAGE_SIZE:
                break
        return tuple(workflows)

    # ------------------------------------------------------------------
    # Uploads
    # ------------------------------------------------------------------
    def prepare_upload(self) -> MusicAIUploadTarget:
        """Ask for a temporary signed upload/download URL pair."""

        payload = self._json("GET", "/upload")
        if not isinstance(payload, Mapping):
            raise MusicAIRequestError("Music AI did not return an upload target.")
        upload_url = str(payload.get("uploadUrl") or "")
        download_url = str(payload.get("downloadUrl") or "")
        if not upload_url or not download_url:
            raise MusicAIRequestError("Music AI did not return an upload target.")
        return MusicAIUploadTarget(
            upload_url=validate_music_ai_url(upload_url),
            download_url=validate_music_ai_url(download_url),
        )

    def upload_file(self, path: str | Path, target: MusicAIUploadTarget) -> str:
        """Upload one local file the user chose and return its download URL.

        The caller must already hold a concrete path. Nothing in this module
        discovers files, walks directories, or reaches the live Jamulus mix.
        """

        source = Path(path)
        try:
            size = source.stat().st_size
        except OSError as exc:
            raise MusicAIRequestError(
                "WebJam could not read the file you chose."
            ) from exc
        if size <= 0:
            raise MusicAIRequestError("The file you chose is empty.")
        if size > _MAX_UPLOAD_BYTES:
            raise MusicAIRequestError(
                "That file is larger than WebJam will upload (512 MB)."
            )
        try:
            data = source.read_bytes()
        except OSError as exc:
            raise MusicAIRequestError(
                "WebJam could not read the file you chose."
            ) from exc

        response = self._transport.request(
            "PUT",
            target.upload_url,
            # The signed URL carries its own authorization; sending the API key
            # to Google Cloud Storage would leak it to an unrelated host.
            headers={"Content-Type": guess_content_type(source)},
            body=data,
            timeout=max(self._timeout, 120.0),
        )
        if response.status >= 400:
            raise MusicAIRequestError(
                "Music AI rejected the upload. Try the file again."
            )
        return target.download_url

    # ------------------------------------------------------------------
    # Jobs
    # ------------------------------------------------------------------
    def create_job(
        self,
        *,
        name: str,
        workflow: str,
        params: Mapping[str, Any],
        metadata: Mapping[str, Any] | None = None,
    ) -> str:
        """Start one job and return its id."""

        slug = str(workflow or "").strip()
        if not slug:
            raise MusicAIRequestError("A workflow slug is required.")
        payload: dict[str, Any] = {
            "name": str(name or "WebJam job")[:120],
            "workflow": slug,
            "params": dict(params),
        }
        if metadata:
            payload["metadata"] = dict(metadata)
        body = self._json("POST", "/job", payload=payload)
        if not isinstance(body, Mapping) or not body.get("id"):
            raise MusicAIRequestError("Music AI did not return a job id.")
        return str(body["id"])

    def get_job(self, job_id: str) -> MusicAIJob:
        """Return the current state of one job."""

        payload = self._json("GET", f"/job/{_job_path(job_id)}")
        if not isinstance(payload, Mapping):
            raise MusicAIRequestError("Music AI returned an unexpected job.")
        return _job_from_payload(payload, fallback_id=job_id)

    def job_status(self, job_id: str) -> str:
        """Return only the status string for one job."""

        payload = self._json("GET", f"/job/{_job_path(job_id)}/status")
        if not isinstance(payload, Mapping):
            raise MusicAIRequestError("Music AI returned an unexpected status.")
        return str(payload.get("status") or "")

    def delete_job(self, job_id: str) -> None:
        """Delete one job. Used to clean up after results are downloaded."""

        self._request("DELETE", f"/job/{_job_path(job_id)}")

    def wait_for_job(
        self,
        job_id: str,
        *,
        poll_interval: float = 3.0,
        timeout: float = 900.0,
        should_cancel: Callable[[], bool] | None = None,
    ) -> MusicAIJob:
        """Poll until the job finishes, or raise rather than return a guess."""

        deadline = self._monotonic() + max(1.0, float(timeout))
        interval = max(0.1, float(poll_interval))
        while True:
            if should_cancel is not None and should_cancel():
                raise MusicAIJobError(
                    "The job was cancelled before it finished.",
                    code="CANCELLED",
                    job_id=job_id,
                )
            job = self.get_job(job_id)
            if job.succeeded:
                return job
            if job.failed:
                raise MusicAIJobError(
                    job.failure_text(), code=job.error_code, job_id=job_id
                )
            if self._monotonic() >= deadline:
                raise MusicAIJobError(
                    "Music AI is still working on this job. It was not "
                    "cancelled — check it later in the Music AI dashboard.",
                    code="TIMEOUT",
                    job_id=job_id,
                )
            self._sleep(interval)

    def run_file_workflow(
        self,
        path: str | Path,
        *,
        workflow: str,
        name: str = "WebJam",
        params: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
        poll_interval: float = 3.0,
        timeout: float = 900.0,
        should_cancel: Callable[[], bool] | None = None,
    ) -> MusicAIJob:
        """Upload one chosen file, run a workflow on it, and return the result."""

        target = self.prepare_upload()
        input_url = self.upload_file(path, target)
        job_id = self.create_job(
            name=name,
            workflow=workflow,
            params={"inputUrl": input_url, **dict(params or {})},
            metadata=metadata,
        )
        return self.wait_for_job(
            job_id,
            poll_interval=poll_interval,
            timeout=timeout,
            should_cancel=should_cancel,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _request(
        self,
        method: str,
        path: str,
        *,
        payload: Mapping[str, Any] | None = None,
    ) -> MusicAIResponse:
        # The docs are explicit that the key is sent verbatim. A "Bearer "
        # prefix here is the single most common reason a valid key 401s.
        headers = {"Authorization": self._api_key}
        body: bytes | None = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(payload).encode("utf-8")
        response = self._transport.request(
            method,
            f"{self._base_url}{path}",
            headers=headers,
            body=body,
            timeout=self._timeout,
        )
        if response.status == 401:
            raise MusicAIAuthError(
                "Music AI rejected this API key. Create a new key at "
                f"{API_KEY_CONSOLE_URL} and paste it into Settings."
            )
        if response.status == 404:
            raise MusicAIRequestError(
                "Music AI could not find that workflow or job. Your account's "
                "workflow list may have changed."
            )
        if response.status >= 400:
            raise MusicAIRequestError(
                f"Music AI refused the request (HTTP {response.status})."
            )
        return response

    def _json(
        self,
        method: str,
        path: str,
        *,
        payload: Mapping[str, Any] | None = None,
    ) -> Any:
        return self._request(method, path, payload=payload).json()


def missing_key_message() -> str:
    """Return the one line shown whenever no key is configured.

    One sentence, and it names the two places a key can live. This never
    appears in the HUD: an absent optional credential is not the session's
    next action.
    """

    return (
        f"Song tools need a Music AI key from {API_KEY_CONSOLE_URL}. "
        f"Put it in Settings or set {API_KEY_ENV_VAR}. "
        "A Moises app login is a different account."
    )


def guess_content_type(path: str | Path) -> str:
    """Return a content type for the signed upload PUT."""

    guessed, _encoding = mimetypes.guess_type(str(path))
    if guessed and guessed.startswith(("audio/", "video/")):
        return guessed
    return "application/octet-stream"


def _job_path(job_id: str) -> str:
    candidate = str(job_id or "").strip()
    if not candidate or not all(
        character.isalnum() or character in "-_" for character in candidate
    ):
        raise MusicAIRequestError("That job id is not valid.")
    return candidate


def _job_from_payload(payload: Mapping[str, Any], *, fallback_id: str) -> MusicAIJob:
    error = payload.get("error")
    error_map: Mapping[str, Any] = error if isinstance(error, Mapping) else {}
    result = payload.get("result")
    return MusicAIJob(
        id=str(payload.get("id") or fallback_id),
        status=str(payload.get("status") or ""),
        workflow=str(payload.get("workflow") or ""),
        name=str(payload.get("name") or ""),
        result=dict(result) if isinstance(result, Mapping) else {},
        error_code=str(error_map.get("code") or ""),
        error_title=str(error_map.get("title") or ""),
        error_message=str(error_map.get("message") or ""),
    )


def _workflow_entries(payload: Any) -> list[Mapping[str, Any]]:
    # The reference documents ``{"workflows": [...]}`` for this endpoint while
    # the job list returns a bare array, so accept either shape.
    if isinstance(payload, Mapping):
        candidate = payload.get("workflows")
    else:
        candidate = payload
    if not isinstance(candidate, list):
        return []
    return [entry for entry in candidate if isinstance(entry, Mapping)]


__all__ = [
    "API_BASE_URL",
    "API_KEY_CONSOLE_URL",
    "API_KEY_ENV_VAR",
    "DOCUMENTED_STEMS_WORKFLOW",
    "MusicAIApplication",
    "MusicAIAuthError",
    "MusicAIClient",
    "MusicAIConfigurationError",
    "MusicAIError",
    "MusicAIJob",
    "MusicAIJobError",
    "MusicAIRequestError",
    "MusicAIResponse",
    "MusicAITransport",
    "MusicAITransportError",
    "MusicAIUploadTarget",
    "MusicAIWorkflow",
    "UrllibMusicAITransport",
    "guess_content_type",
    "missing_key_message",
    "validate_music_ai_url",
]
