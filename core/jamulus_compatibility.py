"""Authoritative Jamulus component compatibility policy.

The updater must never infer compatibility from a release being "latest".
Every downloadable artifact is represented by an immutable record containing
its source identity, exact bytes, target, role, WebJam range, runtime
capabilities, and legal inventory.  A signed component catalog may transport
these records, but it cannot weaken any of the validation performed here.

This module intentionally has no UI, networking, or process-lifecycle
dependencies.  It is safe to import from packaging verification, the desktop
runtime, and focused security tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
from pathlib import PurePosixPath
import re
from types import MappingProxyType
from typing import Iterable
from urllib.parse import urlsplit


_COMPONENT_ID_RE = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9])?$")
_VARIANT_RE = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,30}[a-z0-9])?$")
_VERSION_RE = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$"
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_REPOSITORY_RE = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,99})/"
    r"[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,99})$"
)
_TAG_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,126}[A-Za-z0-9])?$")
_CAPABILITY_RE = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9])?$")
_WINDOWS_FORBIDDEN_NAME_CHARACTERS = frozenset('<>:"|?*')
_WINDOWS_RESERVED_BASENAMES = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{index}" for index in range(1, 10)),
        *(f"LPT{index}" for index in range(1, 10)),
    }
)


class JamulusCompatibilityError(ValueError):
    """A compatibility record or lookup failed closed."""


class JamulusRole(str, Enum):
    CLIENT = "client"
    SERVER = "server"
    HEADLESS = "headless"


class ComponentTarget(str, Enum):
    WINDOWS_X64 = "windows-x64"
    LINUX_X64 = "linux-x64"
    MACOS_ARM64 = "macos-arm64"
    MACOS_X64 = "macos-x64"


class ArtifactKind(str, Enum):
    INSTALLER = "installer"
    PACKAGE = "package"
    DISK_IMAGE = "disk-image"
    ARCHIVE = "archive"
    EXECUTABLE = "executable"
    APP_BUNDLE = "app-bundle"


class ActivationMode(str, Enum):
    """How an approved artifact may become active.

    ``MANAGED`` means WebJam can stage an exact runtime tree in its per-user
    component store.  Platform installers always require a separate,
    user-visible approval path and can never be silently activated by the
    store.
    """

    MANAGED = "managed"
    PLATFORM_APPROVAL = "platform-approval"
    EMBEDDED_ONLY = "embedded-only"


class SourceProvenance(str, Enum):
    OFFICIAL_RELEASE = "official-release"
    WEBJAM_PATCHED_BUILD = "webjam-patched-build"


def _strict_dict(value: object, *, keys: frozenset[str], label: str) -> dict:
    if not isinstance(value, dict):
        raise JamulusCompatibilityError(f"{label} must be an object")
    actual = frozenset(value)
    if actual != keys:
        missing = sorted(keys - actual)
        extra = sorted(actual - keys)
        raise JamulusCompatibilityError(
            f"{label} has an invalid schema (missing={missing}, extra={extra})"
        )
    if not all(isinstance(key, str) for key in value):
        raise JamulusCompatibilityError(f"{label} keys must be strings")
    return value


def _strict_int(
    value: object,
    *,
    label: str,
    minimum: int = 0,
    maximum: int = 2**63 - 1,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise JamulusCompatibilityError(f"{label} must be an integer")
    if not minimum <= value <= maximum:
        raise JamulusCompatibilityError(f"{label} is outside the allowed range")
    return value


def _validate_relative_path(value: str, *, label: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 512:
        raise JamulusCompatibilityError(f"{label} must be a non-empty relative path")
    if (
        "\\" in value
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
        or any(character in _WINDOWS_FORBIDDEN_NAME_CHARACTERS for character in value)
    ):
        raise JamulusCompatibilityError(
            f"{label} is not a portable canonical path"
        )
    parsed = PurePosixPath(value)
    if parsed.is_absolute() or any(part in {"", ".", ".."} for part in parsed.parts):
        raise JamulusCompatibilityError(f"{label} is not a safe relative path")
    if parsed.as_posix() != value:
        raise JamulusCompatibilityError(f"{label} is not canonical")
    for part in parsed.parts:
        if part.endswith((".", " ")):
            raise JamulusCompatibilityError(
                f"{label} has a Windows-unsafe trailing character"
            )
        basename = part.split(".", 1)[0].upper()
        if basename in _WINDOWS_RESERVED_BASENAMES:
            raise JamulusCompatibilityError(
                f"{label} uses a Windows-reserved device name"
            )
    return value


def _version_tuple(value: str, *, label: str) -> tuple[int, int, int]:
    if not isinstance(value, str):
        raise JamulusCompatibilityError(f"{label} must be a semantic version")
    matched = _VERSION_RE.fullmatch(value)
    if not matched:
        raise JamulusCompatibilityError(
            f"{label} must use canonical major.minor.patch form"
        )
    return tuple(int(part) for part in matched.groups())  # type: ignore[return-value]


@dataclass(frozen=True, slots=True)
class WebJamVersionRange:
    minimum: str
    maximum: str

    def __post_init__(self) -> None:
        low = _version_tuple(self.minimum, label="minimum WebJam version")
        high = _version_tuple(self.maximum, label="maximum WebJam version")
        if low > high:
            raise JamulusCompatibilityError("WebJam version range is reversed")

    def contains(self, version: str) -> bool:
        candidate = _version_tuple(version, label="WebJam version")
        return (
            _version_tuple(self.minimum, label="minimum WebJam version")
            <= candidate
            <= _version_tuple(self.maximum, label="maximum WebJam version")
        )

    def to_dict(self) -> dict[str, str]:
        return {"minimum": self.minimum, "maximum": self.maximum}

    @classmethod
    def from_dict(cls, value: object) -> "WebJamVersionRange":
        data = _strict_dict(
            value,
            keys=frozenset({"minimum", "maximum"}),
            label="webjam_range",
        )
        return cls(minimum=data["minimum"], maximum=data["maximum"])


@dataclass(frozen=True, slots=True)
class JamulusSourceIdentity:
    repository: str
    tag: str
    commit: str
    provenance: SourceProvenance
    patch_sha256: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.repository, str) or not _REPOSITORY_RE.fullmatch(
            self.repository
        ):
            raise JamulusCompatibilityError("source repository is invalid")
        if not isinstance(self.tag, str) or not _TAG_RE.fullmatch(self.tag):
            raise JamulusCompatibilityError("source tag is invalid")
        commit = str(self.commit).lower()
        if not _COMMIT_RE.fullmatch(commit):
            raise JamulusCompatibilityError("source commit must be a full SHA-1")
        object.__setattr__(self, "commit", commit)
        try:
            provenance = SourceProvenance(self.provenance)
        except (TypeError, ValueError) as exc:
            raise JamulusCompatibilityError("source provenance is invalid") from exc
        object.__setattr__(self, "provenance", provenance)
        patch = str(self.patch_sha256).lower()
        if provenance is SourceProvenance.WEBJAM_PATCHED_BUILD:
            if not _SHA256_RE.fullmatch(patch):
                raise JamulusCompatibilityError(
                    "patched Jamulus source requires an exact patch SHA-256"
                )
        elif patch:
            raise JamulusCompatibilityError(
                "official Jamulus source cannot declare a WebJam patch"
            )
        object.__setattr__(self, "patch_sha256", patch)

    def to_dict(self) -> dict[str, object]:
        return {
            "repository": self.repository,
            "tag": self.tag,
            "commit": self.commit,
            "provenance": self.provenance.value,
            "patch_sha256": self.patch_sha256,
        }

    @classmethod
    def from_dict(cls, value: object) -> "JamulusSourceIdentity":
        data = _strict_dict(
            value,
            keys=frozenset(
                {"repository", "tag", "commit", "provenance", "patch_sha256"}
            ),
            label="source",
        )
        return cls(
            repository=data["repository"],
            tag=data["tag"],
            commit=data["commit"],
            provenance=data["provenance"],
            patch_sha256=data["patch_sha256"],
        )


@dataclass(frozen=True, slots=True)
class LegalInventory:
    license_files: tuple[str, ...]
    notice_files: tuple[str, ...]
    source_offer: str
    corresponding_source_sha256: str = ""

    def __post_init__(self) -> None:
        if isinstance(self.license_files, (str, bytes)) or isinstance(
            self.notice_files, (str, bytes)
        ):
            raise JamulusCompatibilityError(
                "legal inventory paths must be collections"
            )
        try:
            licenses = tuple(self.license_files)
            notices = tuple(self.notice_files)
        except TypeError as exc:
            raise JamulusCompatibilityError(
                "legal inventory paths must be collections"
            ) from exc
        if not licenses:
            raise JamulusCompatibilityError("legal inventory needs a license file")
        if not all(
            isinstance(path, str) for path in (*licenses, *notices)
        ):
            raise JamulusCompatibilityError("legal inventory paths must be text")
        for index, path in enumerate((*licenses, *notices)):
            _validate_relative_path(path, label=f"legal file {index}")
        if len(set((*licenses, *notices))) != len((*licenses, *notices)):
            raise JamulusCompatibilityError("legal inventory contains duplicate paths")
        _validate_relative_path(self.source_offer, label="source offer")
        source_hash = str(self.corresponding_source_sha256).lower()
        if source_hash and not _SHA256_RE.fullmatch(source_hash):
            raise JamulusCompatibilityError(
                "corresponding source SHA-256 is invalid"
            )
        object.__setattr__(self, "license_files", licenses)
        object.__setattr__(self, "notice_files", notices)
        object.__setattr__(self, "corresponding_source_sha256", source_hash)

    def to_dict(self) -> dict[str, object]:
        return {
            "license_files": list(self.license_files),
            "notice_files": list(self.notice_files),
            "source_offer": self.source_offer,
            "corresponding_source_sha256": self.corresponding_source_sha256,
        }

    @classmethod
    def from_dict(cls, value: object) -> "LegalInventory":
        data = _strict_dict(
            value,
            keys=frozenset(
                {
                    "license_files",
                    "notice_files",
                    "source_offer",
                    "corresponding_source_sha256",
                }
            ),
            label="legal",
        )
        if not isinstance(data["license_files"], list) or not all(
            isinstance(item, str) for item in data["license_files"]
        ):
            raise JamulusCompatibilityError("legal license_files must be strings")
        if not isinstance(data["notice_files"], list) or not all(
            isinstance(item, str) for item in data["notice_files"]
        ):
            raise JamulusCompatibilityError("legal notice_files must be strings")
        return cls(
            license_files=tuple(data["license_files"]),
            notice_files=tuple(data["notice_files"]),
            source_offer=data["source_offer"],
            corresponding_source_sha256=data["corresponding_source_sha256"],
        )


@dataclass(frozen=True, slots=True)
class ArtifactIdentity:
    url: str
    filename: str
    size: int
    sha256: str
    kind: ArtifactKind

    def __post_init__(self) -> None:
        if not isinstance(self.url, str):
            raise JamulusCompatibilityError("artifact URL is invalid")
        parts = urlsplit(self.url)
        try:
            port = parts.port
        except ValueError as exc:
            raise JamulusCompatibilityError("artifact URL is malformed") from exc
        if (
            parts.scheme != "https"
            or not parts.hostname
            or parts.username is not None
            or parts.password is not None
            or parts.fragment
        ):
            raise JamulusCompatibilityError(
                "artifact URL must be credential-free HTTPS"
            )
        if port not in {None, 443}:
            raise JamulusCompatibilityError("artifact URL uses a forbidden port")
        filename = _validate_relative_path(self.filename, label="artifact filename")
        if "/" in filename:
            raise JamulusCompatibilityError("artifact filename cannot contain folders")
        _strict_int(self.size, label="artifact size", minimum=1)
        digest = str(self.sha256).lower()
        if not _SHA256_RE.fullmatch(digest):
            raise JamulusCompatibilityError("artifact SHA-256 is invalid")
        try:
            kind = ArtifactKind(self.kind)
        except (TypeError, ValueError) as exc:
            raise JamulusCompatibilityError("artifact kind is invalid") from exc
        object.__setattr__(self, "sha256", digest)
        object.__setattr__(self, "kind", kind)

    def to_dict(self) -> dict[str, object]:
        return {
            "url": self.url,
            "filename": self.filename,
            "size": self.size,
            "sha256": self.sha256,
            "kind": self.kind.value,
        }

    @classmethod
    def from_dict(cls, value: object) -> "ArtifactIdentity":
        data = _strict_dict(
            value,
            keys=frozenset({"url", "filename", "size", "sha256", "kind"}),
            label="artifact",
        )
        return cls(
            url=data["url"],
            filename=data["filename"],
            size=_strict_int(data["size"], label="artifact size", minimum=1),
            sha256=data["sha256"],
            kind=data["kind"],
        )


@dataclass(frozen=True, slots=True)
class RuntimeFileIdentity:
    relative_path: str
    size: int
    sha256: str
    executable: bool = False

    def __post_init__(self) -> None:
        path = _validate_relative_path(
            self.relative_path, label="runtime file relative_path"
        )
        size = _strict_int(self.size, label=f"runtime file {path} size", minimum=0)
        digest = str(self.sha256).lower()
        if not _SHA256_RE.fullmatch(digest):
            raise JamulusCompatibilityError(
                f"runtime file {path} SHA-256 is invalid"
            )
        if not isinstance(self.executable, bool):
            raise JamulusCompatibilityError(
                f"runtime file {path} executable must be boolean"
            )
        object.__setattr__(self, "relative_path", path)
        object.__setattr__(self, "size", size)
        object.__setattr__(self, "sha256", digest)

    def to_dict(self) -> dict[str, object]:
        return {
            "relative_path": self.relative_path,
            "size": self.size,
            "sha256": self.sha256,
            "executable": self.executable,
        }

    @classmethod
    def from_dict(cls, value: object) -> "RuntimeFileIdentity":
        data = _strict_dict(
            value,
            keys=frozenset({"relative_path", "size", "sha256", "executable"}),
            label="runtime file",
        )
        return cls(
            relative_path=data["relative_path"],
            size=_strict_int(
                data["size"], label="runtime file size", minimum=0
            ),
            sha256=data["sha256"],
            executable=data["executable"],
        )


@dataclass(frozen=True, slots=True)
class JamulusCapabilities:
    values: frozenset[str]

    def __post_init__(self) -> None:
        if isinstance(self.values, (str, bytes)):
            raise JamulusCompatibilityError(
                "capabilities must be a collection of names"
            )
        try:
            values = frozenset(self.values)
        except TypeError as exc:
            raise JamulusCompatibilityError(
                "capabilities must be a collection of names"
            ) from exc
        if not values:
            raise JamulusCompatibilityError("capabilities cannot be empty")
        if len(values) > 64:
            raise JamulusCompatibilityError("too many capabilities")
        for value in values:
            if not isinstance(value, str) or not _CAPABILITY_RE.fullmatch(value):
                raise JamulusCompatibilityError(
                    f"invalid Jamulus capability: {value!r}"
                )
        object.__setattr__(self, "values", values)

    def includes(self, required: Iterable[str]) -> bool:
        return frozenset(required).issubset(self.values)

    def to_list(self) -> list[str]:
        return sorted(self.values)

    @classmethod
    def from_list(cls, value: object) -> "JamulusCapabilities":
        if not isinstance(value, list) or not all(
            isinstance(item, str) for item in value
        ):
            raise JamulusCompatibilityError("capabilities must be a list of strings")
        if len(value) != len(set(value)):
            raise JamulusCompatibilityError("capabilities contain duplicates")
        return cls(frozenset(value))


@dataclass(frozen=True, slots=True)
class JamulusCompatibility:
    component_id: str
    role: JamulusRole
    target: ComponentTarget
    version: str
    variant: str
    source: JamulusSourceIdentity
    artifact: ArtifactIdentity
    runtime_files: tuple[RuntimeFileIdentity, ...]
    executable_relative_path: str
    capabilities: JamulusCapabilities
    webjam_range: WebJamVersionRange
    legal: LegalInventory
    activation_mode: ActivationMode
    publisher: str

    def __post_init__(self) -> None:
        if not isinstance(self.component_id, str) or not _COMPONENT_ID_RE.fullmatch(
            self.component_id
        ):
            raise JamulusCompatibilityError("component_id is invalid")
        if not isinstance(self.variant, str) or not _VARIANT_RE.fullmatch(self.variant):
            raise JamulusCompatibilityError("variant is invalid")
        try:
            role = JamulusRole(self.role)
            target = ComponentTarget(self.target)
            activation = ActivationMode(self.activation_mode)
        except (TypeError, ValueError) as exc:
            raise JamulusCompatibilityError(
                "component role, target, or activation mode is invalid"
            ) from exc
        _version_tuple(self.version, label="Jamulus version")
        if isinstance(self.runtime_files, (str, bytes)):
            raise JamulusCompatibilityError(
                "runtime_files must be a collection"
            )
        try:
            files = tuple(self.runtime_files)
        except TypeError as exc:
            raise JamulusCompatibilityError(
                "runtime_files must be a collection"
            ) from exc
        if not all(isinstance(item, RuntimeFileIdentity) for item in files):
            raise JamulusCompatibilityError(
                "runtime_files must contain exact file identities"
            )
        if len(files) > 4096:
            raise JamulusCompatibilityError("runtime file inventory is too large")
        paths = tuple(item.relative_path for item in files)
        if len(paths) != len(set(paths)):
            raise JamulusCompatibilityError(
                "runtime file inventory contains duplicate paths"
            )
        if len(paths) != len({path.casefold() for path in paths}):
            raise JamulusCompatibilityError(
                "runtime file inventory collides on case-insensitive filesystems"
            )
        executable = self.executable_relative_path
        if executable:
            executable = _validate_relative_path(
                executable, label="executable_relative_path"
            )
            matches = [item for item in files if item.relative_path == executable]
            if files and (len(matches) != 1 or not matches[0].executable):
                raise JamulusCompatibilityError(
                    "runtime executable is not an executable inventory item"
                )
        elif files:
            raise JamulusCompatibilityError(
                "runtime file inventory requires an executable path"
            )
        if activation is ActivationMode.MANAGED and not files:
            raise JamulusCompatibilityError(
                "managed components require an exact runtime file inventory"
            )
        if (
            self.source.provenance is SourceProvenance.WEBJAM_PATCHED_BUILD
            and role is not JamulusRole.HEADLESS
        ):
            raise JamulusCompatibilityError(
                "only the isolated headless role may use a WebJam patch"
            )
        if (
            self.source.provenance is SourceProvenance.WEBJAM_PATCHED_BUILD
            and not self.legal.corresponding_source_sha256
        ):
            raise JamulusCompatibilityError(
                "patched headless Jamulus requires exact corresponding source"
            )
        if role is JamulusRole.HEADLESS and not self.capabilities.includes(
            {"headless"}
        ):
            raise JamulusCompatibilityError(
                "headless components must declare the headless capability"
            )
        if not isinstance(self.publisher, str) or not self.publisher.strip():
            raise JamulusCompatibilityError("publisher identity is required")
        if len(self.publisher) > 256 or any(
            ord(character) < 32 or ord(character) == 127
            for character in self.publisher
        ):
            raise JamulusCompatibilityError("publisher identity is invalid")
        object.__setattr__(self, "role", role)
        object.__setattr__(self, "target", target)
        object.__setattr__(self, "activation_mode", activation)
        object.__setattr__(self, "runtime_files", files)
        object.__setattr__(self, "executable_relative_path", executable)
        object.__setattr__(self, "publisher", self.publisher.strip())

    @property
    def key(self) -> tuple[str, JamulusRole, ComponentTarget, str, str]:
        return (
            self.component_id,
            self.role,
            self.target,
            self.version,
            self.variant,
        )

    @property
    def slot(self) -> str:
        return f"{self.component_id}:{self.role.value}:{self.target.value}:{self.variant}"

    @property
    def runtime_digest(self) -> str:
        canonical = json.dumps(
            [item.to_dict() for item in self.runtime_files],
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    def supports_webjam(self, version: str) -> bool:
        return self.webjam_range.contains(version)

    def to_dict(self) -> dict[str, object]:
        return {
            "component_id": self.component_id,
            "role": self.role.value,
            "target": self.target.value,
            "version": self.version,
            "variant": self.variant,
            "source": self.source.to_dict(),
            "artifact": self.artifact.to_dict(),
            "runtime_files": [item.to_dict() for item in self.runtime_files],
            "executable_relative_path": self.executable_relative_path,
            "capabilities": self.capabilities.to_list(),
            "webjam_range": self.webjam_range.to_dict(),
            "legal": self.legal.to_dict(),
            "activation_mode": self.activation_mode.value,
            "publisher": self.publisher,
        }

    @classmethod
    def from_dict(cls, value: object) -> "JamulusCompatibility":
        data = _strict_dict(
            value,
            keys=frozenset(
                {
                    "component_id",
                    "role",
                    "target",
                    "version",
                    "variant",
                    "source",
                    "artifact",
                    "runtime_files",
                    "executable_relative_path",
                    "capabilities",
                    "webjam_range",
                    "legal",
                    "activation_mode",
                    "publisher",
                }
            ),
            label="component",
        )
        if not isinstance(data["runtime_files"], list):
            raise JamulusCompatibilityError("runtime_files must be a list")
        return cls(
            component_id=data["component_id"],
            role=data["role"],
            target=data["target"],
            version=data["version"],
            variant=data["variant"],
            source=JamulusSourceIdentity.from_dict(data["source"]),
            artifact=ArtifactIdentity.from_dict(data["artifact"]),
            runtime_files=tuple(
                RuntimeFileIdentity.from_dict(item)
                for item in data["runtime_files"]
            ),
            executable_relative_path=data["executable_relative_path"],
            capabilities=JamulusCapabilities.from_list(data["capabilities"]),
            webjam_range=WebJamVersionRange.from_dict(data["webjam_range"]),
            legal=LegalInventory.from_dict(data["legal"]),
            activation_mode=data["activation_mode"],
            publisher=data["publisher"],
        )


class JamulusCompatibilityRegistry:
    """Immutable exact-match registry for approved Jamulus components."""

    def __init__(self, entries: Iterable[JamulusCompatibility]) -> None:
        by_key: dict[
            tuple[str, JamulusRole, ComponentTarget, str, str],
            JamulusCompatibility,
        ] = {}
        by_artifact: dict[str, JamulusCompatibility] = {}
        for entry in tuple(entries):
            if not isinstance(entry, JamulusCompatibility):
                raise JamulusCompatibilityError(
                    "registry entries must be JamulusCompatibility records"
                )
            if entry.key in by_key:
                raise JamulusCompatibilityError(
                    f"duplicate compatibility key: {entry.key}"
                )
            existing = by_artifact.get(entry.artifact.sha256)
            if existing is not None and existing.artifact != entry.artifact:
                raise JamulusCompatibilityError(
                    "one artifact digest maps to conflicting identities"
                )
            by_key[entry.key] = entry
            by_artifact[entry.artifact.sha256] = entry
        if not by_key:
            raise JamulusCompatibilityError("compatibility registry cannot be empty")
        self._by_key = MappingProxyType(by_key)
        self._entries = tuple(
            sorted(
                by_key.values(),
                key=lambda item: (
                    item.component_id,
                    item.role.value,
                    item.target.value,
                    _version_tuple(item.version, label="Jamulus version"),
                    item.variant,
                ),
            )
        )

    @property
    def entries(self) -> tuple[JamulusCompatibility, ...]:
        return self._entries

    @classmethod
    def combine(
        cls, *registries: "JamulusCompatibilityRegistry"
    ) -> "JamulusCompatibilityRegistry":
        """Combine baseline and signed registries without permitting conflicts."""

        combined: dict[
            tuple[str, JamulusRole, ComponentTarget, str, str],
            JamulusCompatibility,
        ] = {}
        for registry in registries:
            if not isinstance(registry, JamulusCompatibilityRegistry):
                raise TypeError(
                    "combined registries must be JamulusCompatibilityRegistry values"
                )
            for entry in registry.entries:
                existing = combined.get(entry.key)
                if existing is not None and existing != entry:
                    raise JamulusCompatibilityError(
                        "combined registries contain a conflicting exact identity"
                    )
                combined[entry.key] = entry
        return cls(combined.values())

    def exact(
        self,
        *,
        component_id: str,
        role: JamulusRole,
        target: ComponentTarget,
        version: str,
        variant: str = "official",
    ) -> JamulusCompatibility:
        try:
            key = (
                component_id,
                JamulusRole(role),
                ComponentTarget(target),
                version,
                variant,
            )
        except (TypeError, ValueError) as exc:
            raise JamulusCompatibilityError(
                "Jamulus component lookup role or target is invalid"
            ) from exc
        try:
            return self._by_key[key]
        except KeyError as exc:
            raise JamulusCompatibilityError(
                "Jamulus component is not in the approved compatibility registry"
            ) from exc

    def require_exact(self, entry: JamulusCompatibility) -> JamulusCompatibility:
        approved = self.exact(
            component_id=entry.component_id,
            role=entry.role,
            target=entry.target,
            version=entry.version,
            variant=entry.variant,
        )
        if approved != entry:
            raise JamulusCompatibilityError(
                "Jamulus component differs from its approved exact identity"
            )
        return approved

    def compatible(
        self,
        *,
        role: JamulusRole,
        target: ComponentTarget,
        webjam_version: str,
        required_capabilities: Iterable[str] = (),
        activation_mode: ActivationMode | None = None,
    ) -> tuple[JamulusCompatibility, ...]:
        candidates = [
            entry
            for entry in self._entries
            if entry.role is JamulusRole(role)
            and entry.target is ComponentTarget(target)
            and entry.supports_webjam(webjam_version)
            and entry.capabilities.includes(required_capabilities)
            and (
                activation_mode is None
                or entry.activation_mode is ActivationMode(activation_mode)
            )
        ]
        return tuple(
            sorted(
                candidates,
                key=lambda item: _version_tuple(
                    item.version, label="Jamulus version"
                ),
                reverse=True,
            )
        )


_OFFICIAL_RELEASES = {
    "3.12.2": {
        "tag": "r3_12_2",
        "commit": "ffca974ed4e47b8f4621f3b583c00db2f87974fa",
        "webjam_range": ("0.21.0", "0.25.0"),
        "artifacts": {
            ComponentTarget.WINDOWS_X64: (
                "jamulus_3.12.2_win.exe",
                84_381_073,
                "4e7cef6a70fe4525f0e7ea1f1c3301d7298047d9456283b7e12035f3ab5ba7b9",
                ArtifactKind.INSTALLER,
            ),
            ComponentTarget.LINUX_X64: (
                "jamulus_3.12.2_ubuntu_amd64.deb",
                1_505_188,
                "029f8858f21a5fb36da5144046473575caa2a26f2c7d8db162953b89d8c8ccc9",
                ArtifactKind.PACKAGE,
            ),
            ComponentTarget.MACOS_ARM64: (
                "jamulus_3.12.2_mac.dmg",
                88_808_313,
                "adf185aaf78e27d9f603daa6895e7698b4bdffee18fe29ad789cd7c1021d6bd0",
                ArtifactKind.DISK_IMAGE,
            ),
            ComponentTarget.MACOS_X64: (
                "jamulus_3.12.2_mac.dmg",
                88_808_313,
                "adf185aaf78e27d9f603daa6895e7698b4bdffee18fe29ad789cd7c1021d6bd0",
                ArtifactKind.DISK_IMAGE,
            ),
        },
        "headless_linux": (
            "jamulus-headless_3.12.2_ubuntu_amd64.deb",
            1_248_620,
            "aee63a55d0637d38718f4940b77733b1fb37446f7f7a0493fb2d46e096addbab",
            ArtifactKind.PACKAGE,
        ),
    },
    "3.12.3": {
        "tag": "r3_12_3",
        "commit": "74dc422116983a2173eb917cb4d6a403886b31e5",
        "webjam_range": ("0.22.0", "0.25.0"),
        "artifacts": {
            ComponentTarget.WINDOWS_X64: (
                "jamulus_3.12.3_win.exe",
                84_406_464,
                "008918b1564b2a46f1a371d7e3df661a0d710689383dab5c61b80be3c4aaf5a1",
                ArtifactKind.INSTALLER,
            ),
            ComponentTarget.LINUX_X64: (
                "jamulus_3.12.3_ubuntu_amd64.deb",
                1_505_696,
                "100af7bcf6edb5729df03ac38bbbdbb4f02014d50b32e0a0e11e55bffba783d3",
                ArtifactKind.PACKAGE,
            ),
            ComponentTarget.MACOS_ARM64: (
                "jamulus_3.12.3_mac.dmg",
                88_923_220,
                "9502b78c3b13d1e58a6ae417ecb1b5c6ebdf9a3c18e7ec4e23e23230890900cb",
                ArtifactKind.DISK_IMAGE,
            ),
            ComponentTarget.MACOS_X64: (
                "jamulus_3.12.3_mac.dmg",
                88_923_220,
                "9502b78c3b13d1e58a6ae417ecb1b5c6ebdf9a3c18e7ec4e23e23230890900cb",
                ArtifactKind.DISK_IMAGE,
            ),
        },
        # Exact installed executables independently extracted from the
        # catalog-pinned upstream packages. The Windows installer contains
        # both architectures under the same internal filename; these values
        # identify the x64 section selected on supported x64 Windows.
        "runtimes": {
            ComponentTarget.WINDOWS_X64: (
                (
                    "Jamulus.exe",
                    3_111_424,
                    "25c3dacaece705a233d9d2a1b7ddb00bb5dfcd10fb3af7ed98f024c56b473295",
                    True,
                ),
                (
                    "avcodec-61.dll",
                    13_921_592,
                    "e419ac581dd7317dc49b54349827db26e479bc71a71fe7af7529086aa6fa7f60",
                    False,
                ),
                (
                    "avformat-61.dll",
                    2_642_232,
                    "570fe624961878fa08611335fe7f92b7ab898013e16ddb000e63dc7be1d6cd1b",
                    False,
                ),
                (
                    "avutil-59.dll",
                    1_198_392,
                    "c53a43cc7f15cf36e2143240302f2dcf0c10c9236e41ef6aba488d3986e90c96",
                    False,
                ),
                (
                    "d3dcompiler_47.dll",
                    4_741_488,
                    "a05f99734f7c4822fefc12b367af21fd0976ed6608752fb1e1e80b6ece7ecbbb",
                    False,
                ),
                (
                    "dxcompiler.dll",
                    12_602_944,
                    "6ec587d1952778a41a5a90647b39d0c86958e7dabf87fa1cf2b14408bfe1e8de",
                    False,
                ),
                (
                    "dxil.dll",
                    1_508_432,
                    "e69b838018a1e0025201edbe9f166d061807cab2f02221c18c53ea6f5e96f0b5",
                    False,
                ),
                (
                    "icuuc.dll",
                    36_864,
                    "6febd789ff616dd6398ae2453adc1e7ccd7bbb6aee8c623a2638c1c00d51e003",
                    False,
                ),
                (
                    "Qt6Core.dll",
                    10_242_360,
                    "b98eea394a95563879b614dbd4432ce074b0fceeb154c3bae252f4e7a9376a40",
                    False,
                ),
                (
                    "Qt6Gui.dll",
                    9_494_840,
                    "28ca8b9e7d3d1c4e3cfafd6fe63877a05cf10202a240134a11f98384f01c9e37",
                    False,
                ),
                (
                    "Qt6Multimedia.dll",
                    1_241_400,
                    "97dbc2efc2dcc84ac8d6af1e7e6c4fcb23f8280af888a59d8667668c6b163416",
                    False,
                ),
                (
                    "Qt6Network.dll",
                    1_766_712,
                    "905e1ce3947fadaad566b6deffe345daf7f6efbc57207ea7fcdbfa77669e3de6",
                    False,
                ),
                (
                    "Qt6Widgets.dll",
                    6_576_952,
                    "f383fb6b3f6996efe23327ff607e7298fc47ee76e8b8ef8a65f8d2e0a91707e4",
                    False,
                ),
                (
                    "Qt6Xml.dll",
                    160_056,
                    "c9d4c5c5dbf8d9ef3264f27c67bbaa0cd1aac98bee2f258b4a0a19664e9f18e5",
                    False,
                ),
                (
                    "swresample-5.dll",
                    247_096,
                    "b516d8e9428e5afc8304e7087a673ed0289e87aba5e00f78e52af2d6b54c655e",
                    False,
                ),
                (
                    "swscale-8.dll",
                    751_928,
                    "e9a7f5342fa2b157c20ba2483397f6fecca0d46f6453d651c8f76dc7cd72fe7a",
                    False,
                ),
                (
                    "generic/qtuiotouchplugin.dll",
                    102_712,
                    "bf4efd69f22ad7b3f3bb79979e9fdef4182ef782e7ab2c4b80e6656bbe33e363",
                    False,
                ),
                (
                    "imageformats/qgif.dll",
                    47_928,
                    "4fe24916a961587173c846055a34735b904900fb75716de4fa458c203da3e7ef",
                    False,
                ),
                (
                    "imageformats/qico.dll",
                    45_880,
                    "0d79934047e06591b74201d826af72f6ab595f5df4705ad7f0c234c0e2cf3a32",
                    False,
                ),
                (
                    "imageformats/qjpeg.dll",
                    577_848,
                    "004f456d513a6bfc58320652c326a023fa8fea905d26b642a561f7081442aff0",
                    False,
                ),
                (
                    "multimedia/ffmpegmediaplugin.dll",
                    635_704,
                    "f9b77081dbe6557a89057735a0c7d8ed7268b6d60d9794b2890db55703a98fe1",
                    False,
                ),
                (
                    "multimedia/windowsmediaplugin.dll",
                    291_640,
                    "049dbe8d45cdc3dcff82a6705da61fccf1022f162bd982224bcb81144bf02b13",
                    False,
                ),
                (
                    "networkinformation/qnetworklistmanager.dll",
                    70_968,
                    "2f76ab1699bf9a1ef89404d054dc7325c4e18d4d27f52cd3ca3b5088d944eb18",
                    False,
                ),
                (
                    "platforms/qwindows.dll",
                    997_688,
                    "540211565db496024246cb8a44437212e6b967f3ee7b899e888f41cc556bf041",
                    False,
                ),
                (
                    "styles/qmodernwindowsstyle.dll",
                    220_984,
                    "f9ddcd6f6319be76ec8ea1b95e6829cdc0f9600242b6bdd07babb0185e9762ab",
                    False,
                ),
                (
                    "tls/qcertonlybackend.dll",
                    103_224,
                    "584392e25d46b6edc255e4c5849920da3e52c01172199def876284525587bb05",
                    False,
                ),
                (
                    "tls/qschannelbackend.dll",
                    262_968,
                    "4632404cb87f860e7bb811f525685509127da8d05f398aada5b2b88318e4a621",
                    False,
                ),
            ),
            ComponentTarget.LINUX_X64: (
                (
                    "usr/bin/jamulus",
                    3_430_688,
                    "f576bb7139b4f48ae8331cff46641dc5a0350e6afbd11cd93411fbf36834c983",
                    True,
                ),
            ),
        },
        "headless_linux": (
            "jamulus-headless_3.12.3_ubuntu_amd64.deb",
            1_249_168,
            "995e06c9e53c36f8ea41853b26200ec101ed7170d97112312d6c45b4fb7e237f",
            ArtifactKind.PACKAGE,
        ),
    },
}


def official_jamulus_compatibility_registry() -> JamulusCompatibilityRegistry:
    """Return the audited 3.12.2 fallback and 3.12.3 candidate identities.

    These records describe upstream installers/packages, which always use the
    explicit platform-approval path.  Build-specific embedded and patched
    headless runtime trees must be separate exact records in the signed
    catalog because their post-build file hashes differ by target.
    """

    entries: list[JamulusCompatibility] = []
    # The upstream macOS DMGs are exact, Developer-ID-notarized source
    # artifacts, but their app bundles currently carry App Sandbox.  That
    # sandbox cannot consume WebJam-owned profile/secret/recording paths.
    # Consequently the catalog must not advertise those execution
    # capabilities for the untouched upstream Mac apps.  A future
    # WebJam-integrated asset needs its own independently verified execution
    # contract before it may add them.
    base_role_capabilities = {
        JamulusRole.CLIENT: frozenset(
            {"audio-client", "json-rpc-client", "native-gui"}
        ),
        JamulusRole.SERVER: frozenset({"audio-server", "json-rpc-server"}),
    }
    for version, release in _OFFICIAL_RELEASES.items():
        license_path = (
            "licenses/JAMULUS_COPYING-r3_12_3.txt"
            if version == "3.12.3"
            else "licenses/JAMULUS_COPYING.txt"
        )
        legal = LegalInventory(
            license_files=(license_path,),
            notice_files=(
                "THIRD_PARTY_NOTICES.md",
                "THIRD_PARTY_NOTICES_RUNTIME.md",
                "packaging/Jamulus-component-sbom.cdx.json",
            ),
            source_offer="THIRD_PARTY_NOTICES.md",
        )
        source = JamulusSourceIdentity(
            repository="jamulussoftware/jamulus",
            tag=release["tag"],
            commit=release["commit"],
            provenance=SourceProvenance.OFFICIAL_RELEASE,
        )
        webjam_range = WebJamVersionRange(*release["webjam_range"])
        for target, artifact_values in release["artifacts"].items():
            filename, size, digest, kind = artifact_values
            runtime_values = release.get("runtimes", {}).get(target)
            runtime_files: tuple[RuntimeFileIdentity, ...] = ()
            executable_relative_path = ""
            if runtime_values is not None:
                runtime_files = tuple(
                    RuntimeFileIdentity(
                        relative_path=runtime_path,
                        size=runtime_size,
                        sha256=runtime_digest,
                        executable=runtime_executable,
                    )
                    for (
                        runtime_path,
                        runtime_size,
                        runtime_digest,
                        runtime_executable,
                    ) in runtime_values
                )
                executable_paths = tuple(
                    item.relative_path for item in runtime_files if item.executable
                )
                if len(executable_paths) != 1:
                    raise JamulusCompatibilityError(
                        "official runtime inventory needs one executable"
                    )
                executable_relative_path = executable_paths[0]
            artifact = ArtifactIdentity(
                url=(
                    "https://github.com/jamulussoftware/jamulus/releases/"
                    f"download/{release['tag']}/{filename}"
                ),
                filename=filename,
                size=size,
                sha256=digest,
                kind=kind,
            )
            for role, base_capabilities in base_role_capabilities.items():
                capabilities = set(base_capabilities)
                if target not in {
                    ComponentTarget.MACOS_ARM64,
                    ComponentTarget.MACOS_X64,
                }:
                    capabilities.add(
                        "webjam-route-profile"
                        if role is JamulusRole.CLIENT
                        else "recording"
                    )
                entries.append(
                    JamulusCompatibility(
                        component_id="jamulus",
                        role=role,
                        target=target,
                        version=version,
                        variant="official",
                        source=source,
                        artifact=artifact,
                        runtime_files=runtime_files,
                        executable_relative_path=executable_relative_path,
                        capabilities=JamulusCapabilities(
                            frozenset(capabilities)
                        ),
                        webjam_range=webjam_range,
                        legal=legal,
                        activation_mode=ActivationMode.PLATFORM_APPROVAL,
                        publisher=(
                            "Developer ID Application: Jonathan Chung "
                            "(V9ZZ6B9WH8)"
                            if target
                            in {
                                ComponentTarget.MACOS_ARM64,
                                ComponentTarget.MACOS_X64,
                            }
                            else (
                                "Unsigned upstream installer; exact "
                                "WebJam-approved SHA-256"
                                if target is ComponentTarget.WINDOWS_X64
                                else "Debian package jamulus"
                            )
                        ),
                    )
                )
        headless_name, headless_size, headless_digest, headless_kind = release[
            "headless_linux"
        ]
        entries.append(
            JamulusCompatibility(
                component_id="jamulus",
                role=JamulusRole.HEADLESS,
                target=ComponentTarget.LINUX_X64,
                version=version,
                variant="official-headless",
                source=source,
                artifact=ArtifactIdentity(
                    url=(
                        "https://github.com/jamulussoftware/jamulus/releases/"
                        f"download/{release['tag']}/{headless_name}"
                    ),
                    filename=headless_name,
                    size=headless_size,
                    sha256=headless_digest,
                    kind=headless_kind,
                ),
                runtime_files=(),
                executable_relative_path="",
                capabilities=JamulusCapabilities(
                    frozenset(
                        {
                            "audio-server",
                            "headless",
                            "json-rpc-server",
                        }
                    )
                ),
                webjam_range=webjam_range,
                legal=legal,
                activation_mode=ActivationMode.PLATFORM_APPROVAL,
                publisher="Debian package jamulus",
            )
        )
    return JamulusCompatibilityRegistry(entries)


__all__ = [
    "ActivationMode",
    "ArtifactIdentity",
    "ArtifactKind",
    "ComponentTarget",
    "JamulusCapabilities",
    "JamulusCompatibility",
    "JamulusCompatibilityError",
    "JamulusCompatibilityRegistry",
    "JamulusRole",
    "JamulusSourceIdentity",
    "LegalInventory",
    "RuntimeFileIdentity",
    "SourceProvenance",
    "WebJamVersionRange",
    "official_jamulus_compatibility_registry",
]
