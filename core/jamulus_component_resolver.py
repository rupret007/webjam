"""Fail-closed Jamulus runtime resolution with explicit precedence."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import os
from pathlib import Path
import stat
from typing import Callable, Iterable

from core.component_store import (
    ComponentStoreError,
    ManagedComponentStore,
)
from core.jamulus_compatibility import (
    ComponentTarget,
    JamulusCompatibility,
    JamulusCompatibilityError,
    JamulusCompatibilityRegistry,
    JamulusRole,
)


class ComponentResolutionError(RuntimeError):
    pass


class ComponentOrigin(str, Enum):
    MANAGED = "managed"
    EMBEDDED = "embedded"
    EXPLICIT = "explicit"
    SYSTEM = "system"


@dataclass(frozen=True, slots=True)
class ExternalComponentCandidate:
    path: Path
    origin: ComponentOrigin

    def __post_init__(self) -> None:
        path = Path(self.path)
        try:
            origin = ComponentOrigin(self.origin)
        except (TypeError, ValueError) as exc:
            raise ValueError("external component origin is invalid") from exc
        object.__setattr__(self, "path", path)
        object.__setattr__(self, "origin", origin)


@dataclass(frozen=True, slots=True)
class ValidatedExternalComponent:
    entry: JamulusCompatibility
    executable_path: Path
    content_verified: bool
    version_verified: bool
    architecture_verified: bool
    publisher_verified: bool
    trust_policy_verified: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.entry, JamulusCompatibility):
            raise TypeError("validated component entry is invalid")
        object.__setattr__(self, "executable_path", Path(self.executable_path))
        for name in (
            "content_verified",
            "version_verified",
            "architecture_verified",
            "publisher_verified",
            "trust_policy_verified",
        ):
            if not isinstance(getattr(self, name), bool):
                raise TypeError(f"{name} must be boolean")

    @property
    def fully_verified(self) -> bool:
        """Whether the component satisfies one complete platform trust policy.

        Apple Developer ID is a publisher-identity policy. The intentionally
        unsigned Windows release and Linux package instead require WebJam's
        stricter catalog bytes, complete runtime inventory/package ownership,
        canonical-path, version, and architecture policy. The two claims stay
        separate so diagnostics never imply an unsigned binary has a publisher
        signature.
        """

        return (
            self.content_verified
            and self.version_verified
            and self.architecture_verified
            and (self.publisher_verified or self.trust_policy_verified)
        )


ExternalComponentValidator = Callable[
    [
        ExternalComponentCandidate,
        JamulusCompatibilityRegistry,
        JamulusRole,
        ComponentTarget,
    ],
    ValidatedExternalComponent | None,
]


@dataclass(frozen=True, slots=True)
class ComponentResolution:
    origin: ComponentOrigin
    entry: JamulusCompatibility
    executable_path: Path
    used_fallback: bool
    reason_codes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        """Privacy-safe serializable resolution; the local path is omitted."""

        return {
            "origin": self.origin.value,
            "component_id": self.entry.component_id,
            "role": self.entry.role.value,
            "target": self.entry.target.value,
            "version": self.entry.version,
            "variant": self.entry.variant,
            "used_fallback": self.used_fallback,
            "reason_codes": list(self.reason_codes),
        }


class JamulusComponentResolver:
    """Resolve managed, then embedded, then explicit, then system components."""

    def __init__(
        self,
        registry: JamulusCompatibilityRegistry,
        *,
        managed_store: ManagedComponentStore | None = None,
        external_validator: ExternalComponentValidator | None = None,
    ) -> None:
        if not isinstance(registry, JamulusCompatibilityRegistry):
            raise TypeError("registry must be a JamulusCompatibilityRegistry")
        self.registry = registry
        self.managed_store = managed_store
        self.external_validator = external_validator

    def resolve(
        self,
        *,
        component_id: str,
        role: JamulusRole,
        target: ComponentTarget,
        webjam_version: str,
        variant: str = "official",
        required_capabilities: Iterable[str] = (),
        managed_paths: Iterable[str | Path] = (),
        embedded_paths: Iterable[str | Path] = (),
        explicit_paths: Iterable[str | Path] = (),
        system_paths: Iterable[str | Path] = (),
    ) -> ComponentResolution:
        role = JamulusRole(role)
        target = ComponentTarget(target)
        capabilities = frozenset(required_capabilities)
        reasons: list[str] = []
        if self.managed_store is not None:
            try:
                managed = self.managed_store.current(
                    component_id=component_id,
                    role=role,
                    target=target,
                    variant=variant,
                )
            except ComponentStoreError:
                managed = None
                reasons.append("managed-invalid")
            if managed is not None:
                if self._entry_allowed(
                    managed.entry,
                    component_id=component_id,
                    role=role,
                    target=target,
                    webjam_version=webjam_version,
                    variant=variant,
                    required_capabilities=capabilities,
                ) and _is_regular_executable(managed.executable_path):
                    return ComponentResolution(
                        origin=ComponentOrigin.MANAGED,
                        entry=managed.entry,
                        executable_path=managed.executable_path,
                        used_fallback=False,
                        reason_codes=tuple(reasons),
                    )
                reasons.append("managed-incompatible")
            else:
                reasons.append("managed-unavailable")

        groups = (
            (ComponentOrigin.MANAGED, managed_paths),
            (ComponentOrigin.EMBEDDED, embedded_paths),
            (ComponentOrigin.EXPLICIT, explicit_paths),
            (ComponentOrigin.SYSTEM, system_paths),
        )
        for origin, paths in groups:
            for raw_path in paths:
                candidate = ExternalComponentCandidate(
                    path=Path(raw_path), origin=origin
                )
                validated = self._validate_external(candidate, role=role, target=target)
                if validated is None:
                    continue
                if not self._entry_allowed(
                    validated.entry,
                    component_id=component_id,
                    role=role,
                    target=target,
                    webjam_version=webjam_version,
                    variant=variant,
                    required_capabilities=capabilities,
                ):
                    continue
                if not validated.fully_verified:
                    continue
                if not _is_regular_executable(validated.executable_path):
                    continue
                return ComponentResolution(
                    origin=origin,
                    entry=validated.entry,
                    executable_path=validated.executable_path,
                    used_fallback=origin is not ComponentOrigin.MANAGED,
                    reason_codes=tuple(reasons),
                )
            reasons.append(f"{origin.value}-unavailable")
        raise ComponentResolutionError(
            "no fully verified compatible Jamulus component is available"
        )

    def resolve_optional(self, **kwargs) -> ComponentResolution | None:
        try:
            return self.resolve(**kwargs)
        except ComponentResolutionError:
            return None

    def _validate_external(
        self,
        candidate: ExternalComponentCandidate,
        *,
        role: JamulusRole,
        target: ComponentTarget,
    ) -> ValidatedExternalComponent | None:
        if self.external_validator is None:
            return None
        try:
            result = self.external_validator(candidate, self.registry, role, target)
        except Exception:
            return None
        if result is None or not isinstance(result, ValidatedExternalComponent):
            return None
        try:
            self.registry.require_exact(result.entry)
        except JamulusCompatibilityError:
            return None
        try:
            candidate_path = candidate.path.resolve(strict=True)
            executable = result.executable_path.resolve(strict=True)
        except OSError:
            return None
        if candidate_path.is_dir():
            try:
                executable.relative_to(candidate_path)
            except ValueError:
                return None
        elif executable != candidate_path:
            return None
        return ValidatedExternalComponent(
            entry=result.entry,
            executable_path=executable,
            content_verified=result.content_verified,
            version_verified=result.version_verified,
            architecture_verified=result.architecture_verified,
            publisher_verified=result.publisher_verified,
            trust_policy_verified=result.trust_policy_verified,
        )

    def _entry_allowed(
        self,
        entry: JamulusCompatibility,
        *,
        component_id: str,
        role: JamulusRole,
        target: ComponentTarget,
        webjam_version: str,
        variant: str,
        required_capabilities: frozenset[str],
    ) -> bool:
        try:
            self.registry.require_exact(entry)
        except JamulusCompatibilityError:
            return False
        return (
            entry.component_id == component_id
            and entry.role is role
            and entry.target is target
            and entry.variant == variant
            and entry.supports_webjam(webjam_version)
            and entry.capabilities.includes(required_capabilities)
        )


def _is_regular_executable(path: Path) -> bool:
    try:
        details = path.lstat()
    except OSError:
        return False
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
        return False
    if os.name == "posix" and not details.st_mode & 0o111:
        return False
    return True


__all__ = [
    "ComponentOrigin",
    "ComponentResolution",
    "ComponentResolutionError",
    "ExternalComponentCandidate",
    "ExternalComponentValidator",
    "JamulusComponentResolver",
    "ValidatedExternalComponent",
]
