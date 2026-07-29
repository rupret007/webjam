"""Bounded HTTPS download and exact-byte verification for components."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from http.client import HTTPMessage
import os
from pathlib import Path
import stat
import tempfile
import threading
from typing import Callable, Mapping, Protocol
import urllib.error
import urllib.request

from core.component_hosts import (
    ComponentUrlError,
    HttpsHostPolicy,
    JAMULUS_RELEASE_HOST_POLICY,
)
from core.jamulus_compatibility import ArtifactIdentity


class ComponentDownloadError(RuntimeError):
    pass


class ComponentDownloadCancelled(ComponentDownloadError):
    pass


class ComponentDownloadIntegrityError(ComponentDownloadError):
    pass


@dataclass(frozen=True, slots=True)
class DownloadProgress:
    received: int
    expected: int

    @property
    def fraction(self) -> float:
        if self.expected <= 0:
            return 0.0
        return min(1.0, self.received / self.expected)


@dataclass(frozen=True, slots=True)
class VerifiedDownload:
    path: Path
    size: int
    sha256: str
    redirect_count: int


class DownloadCancellation:
    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise ComponentDownloadCancelled("component download was cancelled")


class DownloadBody(Protocol):
    status: int
    headers: Mapping[str, str] | HTTPMessage

    def read(self, amount: int = -1) -> bytes: ...

    def close(self) -> None: ...


@dataclass(slots=True)
class OpenedDownload:
    body: DownloadBody
    redirect_count: int


class DownloadTransport(Protocol):
    def open(
        self,
        url: str,
        *,
        policy: HttpsHostPolicy,
        cancellation: DownloadCancellation,
    ) -> OpenedDownload: ...


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class UrllibHttpsTransport:
    """System-trust HTTPS transport with explicitly inspected redirects."""

    _REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})

    def __init__(self, *, timeout: float = 30.0, user_agent: str = "WebJam") -> None:
        if timeout <= 0 or timeout > 300:
            raise ValueError("download timeout must be between 0 and 300 seconds")
        self.timeout = float(timeout)
        self.user_agent = str(user_agent).strip() or "WebJam"
        self._opener = urllib.request.build_opener(_NoRedirect())

    def open(
        self,
        url: str,
        *,
        policy: HttpsHostPolicy,
        cancellation: DownloadCancellation,
    ) -> OpenedDownload:
        current = policy.validate_source(url)
        redirects = 0
        while True:
            cancellation.raise_if_cancelled()
            request = urllib.request.Request(
                current,
                method="GET",
                headers={
                    "Accept": "application/octet-stream",
                    "Accept-Encoding": "identity",
                    "User-Agent": self.user_agent,
                },
            )
            try:
                response = self._opener.open(request, timeout=self.timeout)
            except urllib.error.HTTPError as exc:
                if exc.code not in self._REDIRECT_CODES:
                    exc.close()
                    raise ComponentDownloadError(
                        "component server returned an HTTP error"
                    ) from exc
                location = exc.headers.get("Location", "") if exc.headers else ""
                exc.close()
                if redirects >= policy.maximum_redirects:
                    raise ComponentDownloadError(
                        "component download exceeded the redirect limit"
                    )
                try:
                    current = policy.validate_redirect(current, location)
                except ComponentUrlError as redirect_exc:
                    raise ComponentDownloadError(
                        "component download redirect was rejected"
                    ) from redirect_exc
                redirects += 1
                continue
            except (OSError, urllib.error.URLError) as exc:
                raise ComponentDownloadError(
                    "component download could not connect securely"
                ) from exc
            try:
                policy.validate_final(response.geturl())
            except ComponentUrlError as exc:
                response.close()
                raise ComponentDownloadError(
                    "component response origin was rejected"
                ) from exc
            return OpenedDownload(body=response, redirect_count=redirects)


class SecureComponentDownloader:
    def __init__(
        self,
        *,
        transport: DownloadTransport | None = None,
        host_policy: HttpsHostPolicy = JAMULUS_RELEASE_HOST_POLICY,
        chunk_size: int = 256 * 1024,
        maximum_size: int = 2 * 1024 * 1024 * 1024,
    ) -> None:
        if not 4096 <= chunk_size <= 4 * 1024 * 1024:
            raise ValueError("download chunk_size is outside the safe range")
        if maximum_size <= 0:
            raise ValueError("maximum component size must be positive")
        self.transport = transport or UrllibHttpsTransport()
        self.host_policy = host_policy
        self.chunk_size = int(chunk_size)
        self.maximum_size = int(maximum_size)

    def download(
        self,
        artifact: ArtifactIdentity,
        *,
        destination_directory: str | Path,
        cancellation: DownloadCancellation | None = None,
        progress: Callable[[DownloadProgress], None] | None = None,
    ) -> VerifiedDownload:
        if artifact.size > self.maximum_size:
            raise ComponentDownloadIntegrityError(
                "component exceeds the configured download limit"
            )
        token = cancellation or DownloadCancellation()
        token.raise_if_cancelled()
        destination = Path(destination_directory)
        if destination.exists() and destination.is_symlink():
            raise ComponentDownloadError(
                "component download directory cannot be a symlink"
            )
        destination.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not destination.is_dir():
            raise ComponentDownloadError(
                "component download destination is not a directory"
            )
        target = destination / artifact.filename
        if target.exists() or target.is_symlink():
            try:
                details = target.lstat()
            except OSError as exc:
                raise ComponentDownloadError(
                    "component destination could not be inspected"
                ) from exc
            if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
                raise ComponentDownloadError(
                    "component destination is not a regular file"
                )
        opened = self.transport.open(
            artifact.url,
            policy=self.host_policy,
            cancellation=token,
        )
        body = opened.body
        descriptor: int | None = None
        temporary_path: Path | None = None
        try:
            if body.status != 200:
                raise ComponentDownloadError(
                    "component server did not return a complete response"
                )
            _validate_response_headers(body.headers, expected_size=artifact.size)
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{artifact.filename}.",
                suffix=".part",
                dir=str(destination),
            )
            temporary_path = Path(temporary_name)
            if hasattr(os, "fchmod"):
                os.fchmod(descriptor, 0o600)
            digest = hashlib.sha256()
            total = 0
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = None
                while True:
                    token.raise_if_cancelled()
                    chunk = body.read(self.chunk_size)
                    if not isinstance(chunk, bytes):
                        raise ComponentDownloadIntegrityError(
                            "component response returned invalid bytes"
                        )
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > artifact.size or total > self.maximum_size:
                        raise ComponentDownloadIntegrityError(
                            "component response exceeded its signed size"
                        )
                    stream.write(chunk)
                    digest.update(chunk)
                    if progress is not None:
                        progress(
                            DownloadProgress(received=total, expected=artifact.size)
                        )
                if total != artifact.size:
                    raise ComponentDownloadIntegrityError(
                        "component response size did not match its signed identity"
                    )
                actual_digest = digest.hexdigest()
                if actual_digest != artifact.sha256:
                    raise ComponentDownloadIntegrityError(
                        "component response hash did not match its signed identity"
                    )
                stream.flush()
                os.fsync(stream.fileno())
            token.raise_if_cancelled()
            if target.is_symlink():
                raise ComponentDownloadError(
                    "component destination changed during download"
                )
            os.replace(temporary_path, target)
            temporary_path = None
            _fsync_directory(destination)
            return VerifiedDownload(
                path=target,
                size=total,
                sha256=actual_digest,
                redirect_count=opened.redirect_count,
            )
        except OSError as exc:
            raise ComponentDownloadError(
                "component download could not be stored safely"
            ) from exc
        finally:
            try:
                body.close()
            except Exception:
                pass
            if descriptor is not None:
                os.close(descriptor)
            if temporary_path is not None:
                try:
                    temporary_path.unlink()
                except FileNotFoundError:
                    pass


def verify_downloaded_file(
    path: str | Path,
    artifact: ArtifactIdentity,
) -> VerifiedDownload:
    """Verify an already-downloaded regular file without trusting its name."""

    candidate = Path(path)
    try:
        details = candidate.lstat()
    except OSError as exc:
        raise ComponentDownloadIntegrityError(
            "downloaded component is unavailable"
        ) from exc
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
        raise ComponentDownloadIntegrityError(
            "downloaded component is not a regular file"
        )
    if details.st_size != artifact.size:
        raise ComponentDownloadIntegrityError(
            "downloaded component size does not match its signed identity"
        )
    try:
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(candidate, flags)
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_dev != details.st_dev
            or opened.st_ino != details.st_ino
        ):
            os.close(descriptor)
            raise ComponentDownloadIntegrityError(
                "downloaded component changed during verification"
            )
        digest = hashlib.sha256()
        with os.fdopen(descriptor, "rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise ComponentDownloadIntegrityError(
            "downloaded component could not be verified"
        ) from exc
    actual = digest.hexdigest()
    if actual != artifact.sha256:
        raise ComponentDownloadIntegrityError(
            "downloaded component hash does not match its signed identity"
        )
    return VerifiedDownload(
        path=candidate,
        size=details.st_size,
        sha256=actual,
        redirect_count=0,
    )


def _validate_response_headers(
    headers: Mapping[str, str] | HTTPMessage,
    *,
    expected_size: int,
) -> None:
    encoding = str(headers.get("Content-Encoding", "") or "").strip().lower()
    if encoding not in {"", "identity"}:
        raise ComponentDownloadIntegrityError(
            "component response used an unexpected content encoding"
        )
    length = str(headers.get("Content-Length", "") or "").strip()
    if length:
        if not length.isascii() or not length.isdigit():
            raise ComponentDownloadIntegrityError(
                "component response content length is invalid"
            )
        if int(length) != expected_size:
            raise ComponentDownloadIntegrityError(
                "component response content length does not match its signed size"
            )


def _fsync_directory(path: Path) -> None:
    if os.name != "posix":
        return
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


__all__ = [
    "ComponentDownloadCancelled",
    "ComponentDownloadError",
    "ComponentDownloadIntegrityError",
    "DownloadCancellation",
    "DownloadProgress",
    "DownloadTransport",
    "OpenedDownload",
    "SecureComponentDownloader",
    "UrllibHttpsTransport",
    "VerifiedDownload",
    "verify_downloaded_file",
]
