"""Crash-safe per-user store for verified Jamulus runtime components."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import sys
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from core.component_lock import InterProcessComponentLock
from core.file_io import atomic_write_text
from core.jamulus_compatibility import (
    ActivationMode,
    ComponentTarget,
    JamulusCompatibility,
    JamulusCompatibilityError,
    JamulusCompatibilityRegistry,
    JamulusRole,
)

STORE_SCHEMA = 1
DESCRIPTOR_SCHEMA = 1
MAX_STATE_BYTES = 1_048_576
MAX_TREE_ENTRIES = 8192


class ComponentStoreError(RuntimeError):
    pass


class ComponentTreeIntegrityError(ComponentStoreError):
    pass


class ComponentBusyReason(str, Enum):
    CLIENT_ACTIVE = "client-active"
    SERVER_ACTIVE = "server-active"
    REFERENCE_TRACK_ACTIVE = "reference-track-active"
    RECORDING_ACTIVE = "recording-active"
    PRACTICE_ACTIVE = "practice-active"
    RECONNECT_PENDING = "reconnect-pending"
    LAUNCH_IN_PROGRESS = "launch-in-progress"
    ANOTHER_INSTANCE_ACTIVE = "another-instance-active"


@dataclass(frozen=True, slots=True)
class ComponentBusyStatus:
    reason: ComponentBusyReason
    message: str = ""

    def __post_init__(self) -> None:
        try:
            reason = ComponentBusyReason(self.reason)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid component busy reason") from exc
        if (
            not isinstance(self.message, str)
            or len(self.message) > 256
            or any(ord(character) < 32 for character in self.message)
        ):
            raise ValueError("component busy message is invalid")
        object.__setattr__(self, "reason", reason)


BusyCheck = Callable[[], ComponentBusyStatus | None]


@dataclass(frozen=True, slots=True)
class ComponentStagingArea:
    entry: JamulusCompatibility
    root: Path
    payload_root: Path


@dataclass(frozen=True, slots=True)
class InstalledComponentSnapshot:
    entry: JamulusCompatibility
    payload_root: Path
    executable_path: Path
    is_current: bool
    is_previous: bool
    is_ready: bool

    def to_dict(self) -> dict[str, object]:
        """Privacy-safe serializable state; paths intentionally stay private."""

        return {
            "component_id": self.entry.component_id,
            "role": self.entry.role.value,
            "target": self.entry.target.value,
            "version": self.entry.version,
            "variant": self.entry.variant,
            "runtime_digest": self.entry.runtime_digest,
            "is_current": self.is_current,
            "is_previous": self.is_previous,
            "is_ready": self.is_ready,
        }


@dataclass(frozen=True, slots=True)
class CachedArtifactSnapshot:
    """Exact opaque upstream bytes awaiting any required platform approval."""

    entry: JamulusCompatibility
    path: Path

    def to_dict(self) -> dict[str, object]:
        return {
            "component_id": self.entry.component_id,
            "role": self.entry.role.value,
            "target": self.entry.target.value,
            "version": self.entry.version,
            "variant": self.entry.variant,
            "artifact_sha256": self.entry.artifact.sha256,
            "artifact_size": self.entry.artifact.size,
        }


@dataclass(frozen=True, slots=True)
class ComponentActivationResult:
    activated: bool
    current: InstalledComponentSnapshot | None
    previous: InstalledComponentSnapshot | None
    deferred: ComponentBusyStatus | None = None


@dataclass(frozen=True, slots=True)
class _Pointer:
    component_id: str
    role: str
    target: str
    version: str
    variant: str
    runtime_digest: str

    @classmethod
    def for_entry(cls, entry: JamulusCompatibility) -> _Pointer:
        return cls(
            component_id=entry.component_id,
            role=entry.role.value,
            target=entry.target.value,
            version=entry.version,
            variant=entry.variant,
            runtime_digest=entry.runtime_digest,
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "component_id": self.component_id,
            "role": self.role,
            "target": self.target,
            "version": self.version,
            "variant": self.variant,
            "runtime_digest": self.runtime_digest,
        }

    @classmethod
    def from_dict(cls, value: object) -> _Pointer:
        if not isinstance(value, dict) or frozenset(value) != frozenset(
            {
                "component_id",
                "role",
                "target",
                "version",
                "variant",
                "runtime_digest",
            }
        ):
            raise ComponentStoreError("component pointer schema is invalid")
        if not all(isinstance(item, str) for item in value.values()):
            raise ComponentStoreError("component pointer values must be text")
        return cls(
            component_id=value["component_id"],
            role=value["role"],
            target=value["target"],
            version=value["version"],
            variant=value["variant"],
            runtime_digest=value["runtime_digest"],
        )


@dataclass(slots=True)
class _StoreState:
    current: dict[str, _Pointer]
    previous: dict[str, _Pointer]
    ready: dict[str, _Pointer]

    @classmethod
    def empty(cls) -> _StoreState:
        return cls(current={}, previous={}, ready={})

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": STORE_SCHEMA,
            "current": {
                slot: pointer.to_dict()
                for slot, pointer in sorted(self.current.items())
            },
            "previous": {
                slot: pointer.to_dict()
                for slot, pointer in sorted(self.previous.items())
            },
            "ready": {
                slot: pointer.to_dict()
                for slot, pointer in sorted(self.ready.items())
            },
        }


def default_component_store_root(
    *,
    platform_name: str | None = None,
    home: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> Path:
    """Return WebJam's operational per-user component root on each desktop."""

    platform_value = (platform_name or sys.platform).strip().lower()
    home_path = Path(home) if home is not None else Path.home()
    environment = os.environ if environ is None else environ
    if platform_value == "darwin":
        return home_path / "Library" / "Application Support" / "WebJam" / "components"
    if platform_value in {"win32", "cygwin", "msys"}:
        configured = str(environment.get("LOCALAPPDATA", "") or "").strip()
        base = Path(configured) if configured and Path(configured).is_absolute() else (
            home_path / "AppData" / "Local"
        )
        return base / "WebJam" / "components"
    if platform_value.startswith("linux"):
        configured = str(environment.get("XDG_DATA_HOME", "") or "").strip()
        base = Path(configured) if configured and Path(configured).is_absolute() else (
            home_path / ".local" / "share"
        )
        return base / "webjam" / "components"
    raise ComponentStoreError("unsupported component-store platform")


class ManagedComponentStore:
    """Verified staging, activation, fallback, and rollback.

    Installation and activation are deliberately separate.  A download may be
    staged and marked ``ready`` while a session is active, but the caller's
    busy callback can defer the atomic active-pointer change until the next
    clean stop or process launch.
    """

    def __init__(
        self,
        registry: JamulusCompatibilityRegistry,
        *,
        root: str | Path | None = None,
        lock_timeout: float = 5.0,
        forbidden_roots: tuple[str | Path, ...] | None = None,
    ) -> None:
        if not isinstance(registry, JamulusCompatibilityRegistry):
            raise TypeError("registry must be a JamulusCompatibilityRegistry")
        self.registry = registry
        self.root = Path(root) if root is not None else default_component_store_root()
        self.lock_timeout = float(lock_timeout)
        configured_forbidden = forbidden_roots
        if configured_forbidden is None:
            configured_forbidden = _runtime_forbidden_roots()
        self._forbidden_roots = tuple(Path(item) for item in configured_forbidden)
        self.state_path = self.root / "state.json"
        self.lock_path = self.root / ".store.lock"
        self.staging_root = self.root / "staging"
        self.components_root = self.root / "installed"
        self.artifacts_root = self.root / "artifacts"
        self._ensure_root()

    def artifact_cache_directory(self, entry: JamulusCompatibility) -> Path:
        """Return a private cache directory for exact opaque upstream bytes.

        A cached DMG/installer/package is not considered activated.  Platform
        code must still present any SLA/UAC/package-manager approval and
        independently verify the installed result.
        """

        try:
            approved = self.registry.require_exact(entry)
        except JamulusCompatibilityError as exc:
            raise ComponentStoreError("component is not exactly approved") from exc
        with self._lock():
            directory = self.artifacts_root / approved.artifact.sha256
            if directory.exists() and directory.is_symlink():
                raise ComponentStoreError(
                    "component artifact cache cannot be a symlink"
                )
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            if os.name == "posix":
                os.chmod(directory, 0o700)
            return directory

    def cached_artifact(
        self, entry: JamulusCompatibility
    ) -> CachedArtifactSnapshot | None:
        """Return a freshly hash-verified cache hit, or ``None`` if absent."""

        try:
            approved = self.registry.require_exact(entry)
        except JamulusCompatibilityError as exc:
            raise ComponentStoreError("component is not exactly approved") from exc
        with self._lock():
            path = (
                self.artifacts_root
                / approved.artifact.sha256
                / approved.artifact.filename
            )
            if not path.exists() and not path.is_symlink():
                return None
            from core.component_download import (
                ComponentDownloadIntegrityError,
                verify_downloaded_file,
            )

            try:
                verify_downloaded_file(path, approved.artifact)
            except ComponentDownloadIntegrityError as exc:
                raise ComponentTreeIntegrityError(
                    "cached component artifact failed exact-byte verification"
                ) from exc
            return CachedArtifactSnapshot(entry=approved, path=path)

    def discard_cached_artifact(self, entry: JamulusCompatibility) -> None:
        try:
            approved = self.registry.require_exact(entry)
        except JamulusCompatibilityError as exc:
            raise ComponentStoreError("component is not exactly approved") from exc
        with self._lock():
            directory = self.artifacts_root / approved.artifact.sha256
            if directory.exists() or directory.is_symlink():
                _remove_tree(directory)

    def create_staging(
        self, entry: JamulusCompatibility
    ) -> ComponentStagingArea:
        approved = self._require_managed(entry)
        with self._lock():
            self._ensure_root()
            staging = self.staging_root / uuid.uuid4().hex
            staging.mkdir(mode=0o700)
            payload = staging / "payload"
            payload.mkdir(mode=0o700)
            marker = {
                "schema": 1,
                "slot": approved.slot,
                "runtime_digest": approved.runtime_digest,
            }
            atomic_write_text(
                staging / "staging.json",
                json.dumps(marker, sort_keys=True, separators=(",", ":")) + "\n",
                mode=0o600,
            )
            return ComponentStagingArea(
                entry=approved, root=staging, payload_root=payload
            )

    def discard_staging(self, staging: ComponentStagingArea) -> None:
        with self._lock():
            path = self._require_staging_path(staging)
            _remove_tree(path)

    def commit_staging(
        self,
        staging: ComponentStagingArea,
    ) -> InstalledComponentSnapshot:
        entry = self._require_managed(staging.entry)
        with self._lock():
            staging_path = self._require_staging_path(staging)
            self._verify_staging_marker(staging_path, entry)
            self._verify_runtime_tree(staging.payload_root, entry)
            destination = self._installed_path(entry)
            if destination.exists():
                self._verify_installed(destination, entry)
                _remove_tree(staging_path)
            else:
                destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                self._reject_symlink_chain(destination.parent)
                pending = destination.parent / f".pending-{uuid.uuid4().hex}"
                pending.mkdir(mode=0o700)
                try:
                    os.replace(staging.payload_root, pending / "payload")
                    descriptor = {
                        "schema": DESCRIPTOR_SCHEMA,
                        "entry": entry.to_dict(),
                    }
                    atomic_write_text(
                        pending / "descriptor.json",
                        json.dumps(
                            descriptor,
                            ensure_ascii=False,
                            allow_nan=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                        + "\n",
                        mode=0o600,
                    )
                    _fsync_directory(pending)
                    os.replace(pending, destination)
                    _fsync_directory(destination.parent)
                except Exception:
                    if pending.exists():
                        _remove_tree(pending)
                    raise
                finally:
                    if staging_path.exists():
                        _remove_tree(staging_path)
                self._verify_installed(destination, entry)
            state = self._read_state()
            state.ready[entry.slot] = _Pointer.for_entry(entry)
            self._write_state(state)
            return self._snapshot(entry, state)

    def activate(
        self,
        entry: JamulusCompatibility,
        *,
        busy_check: BusyCheck | None = None,
    ) -> ComponentActivationResult:
        approved = self._require_managed(entry)
        with self._lock():
            state = self._read_state()
            destination = self._installed_path(approved)
            self._verify_installed(destination, approved)
            busy = self._check_busy(busy_check)
            if busy is not None:
                return ComponentActivationResult(
                    activated=False,
                    current=self._snapshot_for_pointer(
                        state.current.get(approved.slot), state
                    ),
                    previous=self._snapshot_for_pointer(
                        state.previous.get(approved.slot), state
                    ),
                    deferred=busy,
                )
            pointer = _Pointer.for_entry(approved)
            prior = state.current.get(approved.slot)
            if prior is not None and prior != pointer:
                state.previous[approved.slot] = prior
            state.current[approved.slot] = pointer
            state.ready.pop(approved.slot, None)
            self._write_state(state)
            return ComponentActivationResult(
                activated=True,
                current=self._snapshot(approved, state),
                previous=self._snapshot_for_pointer(
                    state.previous.get(approved.slot), state
                ),
            )

    def activate_ready(
        self,
        *,
        component_id: str,
        role: JamulusRole,
        target: ComponentTarget,
        variant: str = "official",
        busy_check: BusyCheck | None = None,
    ) -> ComponentActivationResult:
        slot = (
            f"{component_id}:{JamulusRole(role).value}:"
            f"{ComponentTarget(target).value}:{variant}"
        )
        with self._lock():
            state = self._read_state()
            pointer = state.ready.get(slot)
            if pointer is None:
                raise ComponentStoreError("no verified component is ready to activate")
            entry = self._entry_for_pointer(pointer)
            self._verify_installed(self._installed_path(entry), entry)
            busy = self._check_busy(busy_check)
            if busy is not None:
                return ComponentActivationResult(
                    activated=False,
                    current=self._snapshot_for_pointer(
                        state.current.get(slot), state
                    ),
                    previous=self._snapshot_for_pointer(
                        state.previous.get(slot), state
                    ),
                    deferred=busy,
                )
            current_pointer = state.current.get(slot)
            if current_pointer is not None and current_pointer != pointer:
                state.previous[slot] = current_pointer
            state.current[slot] = pointer
            state.ready.pop(slot, None)
            self._write_state(state)
            return ComponentActivationResult(
                activated=True,
                current=self._snapshot(entry, state),
                previous=self._snapshot_for_pointer(
                    state.previous.get(slot), state
                ),
            )

    def rollback(
        self,
        *,
        component_id: str,
        role: JamulusRole,
        target: ComponentTarget,
        variant: str = "official",
        busy_check: BusyCheck | None = None,
    ) -> ComponentActivationResult:
        slot = (
            f"{component_id}:{JamulusRole(role).value}:"
            f"{ComponentTarget(target).value}:{variant}"
        )
        with self._lock():
            state = self._read_state()
            prior_pointer = state.previous.get(slot)
            if prior_pointer is None:
                raise ComponentStoreError("no previous component is available")
            prior_entry = self._entry_for_pointer(prior_pointer)
            self._verify_installed(self._installed_path(prior_entry), prior_entry)
            busy = self._check_busy(busy_check)
            if busy is not None:
                return ComponentActivationResult(
                    activated=False,
                    current=self._snapshot_for_pointer(state.current.get(slot), state),
                    previous=self._snapshot(prior_entry, state),
                    deferred=busy,
                )
            current_pointer = state.current.get(slot)
            state.current[slot] = prior_pointer
            if current_pointer is None:
                state.previous.pop(slot, None)
            else:
                state.previous[slot] = current_pointer
            state.ready.pop(slot, None)
            self._write_state(state)
            return ComponentActivationResult(
                activated=True,
                current=self._snapshot(prior_entry, state),
                previous=self._snapshot_for_pointer(
                    state.previous.get(slot), state
                ),
            )

    def current(
        self,
        *,
        component_id: str,
        role: JamulusRole,
        target: ComponentTarget,
        variant: str = "official",
    ) -> InstalledComponentSnapshot | None:
        slot = (
            f"{component_id}:{JamulusRole(role).value}:"
            f"{ComponentTarget(target).value}:{variant}"
        )
        with self._lock():
            state = self._read_state()
            return self._snapshot_for_pointer(state.current.get(slot), state)

    def ready(
        self,
        *,
        component_id: str,
        role: JamulusRole,
        target: ComponentTarget,
        variant: str = "official",
    ) -> InstalledComponentSnapshot | None:
        slot = (
            f"{component_id}:{JamulusRole(role).value}:"
            f"{ComponentTarget(target).value}:{variant}"
        )
        with self._lock():
            state = self._read_state()
            return self._snapshot_for_pointer(state.ready.get(slot), state)

    def installed(self, entry: JamulusCompatibility) -> InstalledComponentSnapshot:
        approved = self._require_managed(entry)
        with self._lock():
            state = self._read_state()
            self._verify_installed(self._installed_path(approved), approved)
            return self._snapshot(approved, state)

    def prune(self) -> tuple[str, ...]:
        """Remove only verified unreferenced component-version directories."""

        removed: list[str] = []
        with self._lock():
            state = self._read_state()
            retained = {
                pointer
                for mapping in (state.current, state.previous, state.ready)
                for pointer in mapping.values()
            }
            if not self.components_root.exists():
                return ()
            for entry in self.registry.entries:
                if entry.activation_mode is not ActivationMode.MANAGED:
                    continue
                path = self._installed_path(entry)
                if path.exists() and _Pointer.for_entry(entry) not in retained:
                    self._verify_installed(path, entry)
                    _remove_tree(path)
                    removed.append(entry.runtime_digest)
            return tuple(sorted(removed))

    def _snapshot(
        self,
        entry: JamulusCompatibility,
        state: _StoreState,
    ) -> InstalledComponentSnapshot:
        pointer = _Pointer.for_entry(entry)
        destination = self._installed_path(entry)
        return InstalledComponentSnapshot(
            entry=entry,
            payload_root=destination / "payload",
            executable_path=destination / "payload" / entry.executable_relative_path,
            is_current=state.current.get(entry.slot) == pointer,
            is_previous=state.previous.get(entry.slot) == pointer,
            is_ready=state.ready.get(entry.slot) == pointer,
        )

    def _snapshot_for_pointer(
        self,
        pointer: _Pointer | None,
        state: _StoreState,
    ) -> InstalledComponentSnapshot | None:
        if pointer is None:
            return None
        entry = self._entry_for_pointer(pointer)
        self._verify_installed(self._installed_path(entry), entry)
        return self._snapshot(entry, state)

    def _entry_for_pointer(self, pointer: _Pointer) -> JamulusCompatibility:
        try:
            entry = self.registry.exact(
                component_id=pointer.component_id,
                role=JamulusRole(pointer.role),
                target=ComponentTarget(pointer.target),
                version=pointer.version,
                variant=pointer.variant,
            )
        except (ValueError, JamulusCompatibilityError) as exc:
            raise ComponentStoreError(
                "component state references an unapproved component"
            ) from exc
        if entry.runtime_digest != pointer.runtime_digest:
            raise ComponentStoreError(
                "component state runtime identity does not match the registry"
            )
        return entry

    def _require_managed(
        self, entry: JamulusCompatibility
    ) -> JamulusCompatibility:
        try:
            approved = self.registry.require_exact(entry)
        except JamulusCompatibilityError as exc:
            raise ComponentStoreError("component is not exactly approved") from exc
        if approved.activation_mode is not ActivationMode.MANAGED:
            raise ComponentStoreError(
                "platform-approved components cannot use the managed store"
            )
        return approved

    def _installed_path(self, entry: JamulusCompatibility) -> Path:
        return (
            self.components_root
            / entry.component_id
            / entry.role.value
            / entry.target.value
            / entry.variant
            / entry.version
            / entry.runtime_digest
        )

    def _require_staging_path(self, staging: ComponentStagingArea) -> Path:
        if not isinstance(staging, ComponentStagingArea):
            raise ComponentStoreError("invalid component staging area")
        try:
            relative = staging.root.relative_to(self.staging_root)
        except ValueError as exc:
            raise ComponentStoreError(
                "component staging area is outside the managed store"
            ) from exc
        if len(relative.parts) != 1 or not relative.name:
            raise ComponentStoreError("component staging path is invalid")
        if staging.payload_root != staging.root / "payload":
            raise ComponentStoreError("component staging payload path is invalid")
        self._reject_symlink_chain(staging.root)
        return staging.root

    def _verify_staging_marker(
        self, staging: Path, entry: JamulusCompatibility
    ) -> None:
        marker = staging / "staging.json"
        try:
            details = marker.lstat()
            if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
                raise ComponentStoreError("component staging marker is invalid")
            value = _load_json(marker, maximum=4096)
        except OSError as exc:
            raise ComponentStoreError("component staging marker is unavailable") from exc
        if not isinstance(value, dict) or frozenset(value) != frozenset(
            {"schema", "slot", "runtime_digest"}
        ):
            raise ComponentStoreError("component staging marker schema is invalid")
        if (
            value["schema"] != 1
            or value["slot"] != entry.slot
            or value["runtime_digest"] != entry.runtime_digest
        ):
            raise ComponentStoreError("component staging marker does not match")

    def _verify_installed(
        self, destination: Path, entry: JamulusCompatibility
    ) -> None:
        if destination.is_symlink() or not destination.is_dir():
            raise ComponentTreeIntegrityError(
                "installed component directory is unavailable"
            )
        descriptor_path = destination / "descriptor.json"
        try:
            details = descriptor_path.lstat()
        except OSError as exc:
            raise ComponentTreeIntegrityError(
                "installed component descriptor is unavailable"
            ) from exc
        if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
            raise ComponentTreeIntegrityError(
                "installed component descriptor is invalid"
            )
        descriptor = _load_json(descriptor_path, maximum=MAX_STATE_BYTES)
        if not isinstance(descriptor, dict) or frozenset(descriptor) != frozenset(
            {"schema", "entry"}
        ):
            raise ComponentTreeIntegrityError(
                "installed component descriptor schema is invalid"
            )
        if descriptor["schema"] != DESCRIPTOR_SCHEMA:
            raise ComponentTreeIntegrityError(
                "installed component descriptor schema is unsupported"
            )
        try:
            described = JamulusCompatibility.from_dict(descriptor["entry"])
            self.registry.require_exact(described)
        except JamulusCompatibilityError as exc:
            raise ComponentTreeIntegrityError(
                "installed component descriptor is not approved"
            ) from exc
        if described != entry:
            raise ComponentTreeIntegrityError(
                "installed component descriptor identity does not match"
            )
        children = frozenset(item.name for item in destination.iterdir())
        if children != frozenset({"descriptor.json", "payload"}):
            raise ComponentTreeIntegrityError(
                "installed component contains unexpected top-level entries"
            )
        self._verify_runtime_tree(destination / "payload", entry)

    def _verify_runtime_tree(
        self, payload: Path, entry: JamulusCompatibility
    ) -> None:
        if payload.is_symlink() or not payload.is_dir():
            raise ComponentTreeIntegrityError(
                "component payload is not a regular directory"
            )
        expected = {item.relative_path: item for item in entry.runtime_files}
        expected_directories = {
            parent.as_posix()
            for item in entry.runtime_files
            for parent in Path(item.relative_path).parents
            if parent.as_posix() != "."
        }
        observed_files: dict[str, os.stat_result] = {}
        observed_directories: set[str] = set()
        stack = [(payload, "")]
        count = 0
        while stack:
            directory, prefix = stack.pop()
            try:
                entries = list(os.scandir(directory))
            except OSError as exc:
                raise ComponentTreeIntegrityError(
                    "component payload could not be enumerated"
                ) from exc
            for child in entries:
                count += 1
                if count > MAX_TREE_ENTRIES:
                    raise ComponentTreeIntegrityError(
                        "component payload contains too many entries"
                    )
                relative = f"{prefix}/{child.name}".lstrip("/")
                if (
                    not child.name
                    or child.name in {".", ".."}
                    or "\\" in child.name
                    or "\x00" in child.name
                ):
                    raise ComponentTreeIntegrityError(
                        "component payload contains an unsafe path"
                    )
                try:
                    details = child.stat(follow_symlinks=False)
                except OSError as exc:
                    raise ComponentTreeIntegrityError(
                        "component payload entry could not be inspected"
                    ) from exc
                if stat.S_ISLNK(details.st_mode):
                    raise ComponentTreeIntegrityError(
                        "component payload cannot contain symlinks"
                    )
                if stat.S_ISDIR(details.st_mode):
                    observed_directories.add(relative)
                    stack.append((Path(child.path), relative))
                elif stat.S_ISREG(details.st_mode):
                    if details.st_nlink != 1:
                        raise ComponentTreeIntegrityError(
                            "component payload cannot contain hard-linked files"
                        )
                    observed_files[relative] = details
                else:
                    raise ComponentTreeIntegrityError(
                        "component payload contains a special file"
                    )
        if frozenset(observed_files) != frozenset(expected):
            raise ComponentTreeIntegrityError(
                "component payload file inventory does not match"
            )
        if frozenset(observed_directories) != frozenset(expected_directories):
            raise ComponentTreeIntegrityError(
                "component payload directory inventory does not match"
            )
        for relative, identity in expected.items():
            details = observed_files[relative]
            if details.st_size != identity.size:
                raise ComponentTreeIntegrityError(
                    "component payload file size does not match"
                )
            path = payload / relative
            try:
                actual_digest = _sha256_regular_file(
                    path, expected_details=details
                )
            except OSError as exc:
                raise ComponentTreeIntegrityError(
                    "component payload file could not be verified"
                ) from exc
            if actual_digest != identity.sha256:
                raise ComponentTreeIntegrityError(
                    "component payload file hash does not match"
                )
            if os.name == "posix":
                executable = bool(details.st_mode & 0o111)
                if executable != identity.executable:
                    raise ComponentTreeIntegrityError(
                        "component payload executable mode does not match"
                    )

    def _read_state(self) -> _StoreState:
        if not self.state_path.exists():
            return _StoreState.empty()
        if self.state_path.is_symlink():
            raise ComponentStoreError("component store state cannot be a symlink")
        value = _load_json(self.state_path, maximum=MAX_STATE_BYTES)
        if not isinstance(value, dict) or frozenset(value) != frozenset(
            {"schema", "current", "previous", "ready"}
        ):
            raise ComponentStoreError("component store state schema is invalid")
        if value["schema"] != STORE_SCHEMA:
            raise ComponentStoreError("component store state schema is unsupported")
        mappings: dict[str, dict[str, _Pointer]] = {}
        for name in ("current", "previous", "ready"):
            raw = value[name]
            if not isinstance(raw, dict) or len(raw) > 256:
                raise ComponentStoreError(
                    f"component store {name} map is invalid"
                )
            parsed: dict[str, _Pointer] = {}
            for slot, pointer_value in raw.items():
                if not isinstance(slot, str) or len(slot) > 256:
                    raise ComponentStoreError("component store slot is invalid")
                pointer = _Pointer.from_dict(pointer_value)
                entry = self._entry_for_pointer(pointer)
                if slot != entry.slot:
                    raise ComponentStoreError(
                        "component store pointer is in the wrong slot"
                    )
                parsed[slot] = pointer
            mappings[name] = parsed
        return _StoreState(
            current=mappings["current"],
            previous=mappings["previous"],
            ready=mappings["ready"],
        )

    def _write_state(self, state: _StoreState) -> None:
        atomic_write_text(
            self.state_path,
            json.dumps(
                state.to_dict(),
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            mode=0o600,
        )

    def _check_busy(self, callback: BusyCheck | None) -> ComponentBusyStatus | None:
        if callback is None:
            return None
        try:
            result = callback()
        except Exception as exc:
            raise ComponentStoreError(
                "component activation could not prove the runtime is idle"
            ) from exc
        if result is None:
            return None
        if not isinstance(result, ComponentBusyStatus):
            raise ComponentStoreError(
                "component busy callback returned an invalid result"
            )
        return result

    def _lock(self) -> InterProcessComponentLock:
        return InterProcessComponentLock(
            self.lock_path, timeout=self.lock_timeout
        )

    def _ensure_root(self) -> None:
        if self.root.exists() and self.root.is_symlink():
            raise ComponentStoreError("component store root cannot be a symlink")
        resolved = self.root.resolve(strict=False)
        if any(part.lower().endswith(".app") for part in resolved.parts):
            raise ComponentStoreError(
                "component store must be outside every application bundle"
            )
        for forbidden in self._forbidden_roots:
            blocked = forbidden.resolve(strict=False)
            if resolved == blocked or _is_relative_to(resolved, blocked):
                raise ComponentStoreError(
                    "component store must be outside the packaged application"
                )
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not self.root.is_dir():
            raise ComponentStoreError("component store root is not a directory")
        if os.name == "posix":
            os.chmod(self.root, 0o700)
        for directory in (
            self.staging_root,
            self.components_root,
            self.artifacts_root,
        ):
            if directory.exists() and directory.is_symlink():
                raise ComponentStoreError(
                    "component store directory cannot be a symlink"
                )
            directory.mkdir(exist_ok=True, mode=0o700)
            if os.name == "posix":
                os.chmod(directory, 0o700)

    def _reject_symlink_chain(self, path: Path) -> None:
        try:
            relative = path.relative_to(self.root)
        except ValueError as exc:
            raise ComponentStoreError("component path escaped its store") from exc
        current = self.root
        if current.is_symlink():
            raise ComponentStoreError("component store root cannot be a symlink")
        for part in relative.parts:
            current /= part
            if current.is_symlink():
                raise ComponentStoreError("component path contains a symlink")


def _runtime_forbidden_roots() -> tuple[Path, ...]:
    if not getattr(sys, "frozen", False):
        return ()
    values = [Path(sys.executable).resolve().parent]
    bundle = getattr(sys, "_MEIPASS", "")
    if bundle:
        values.append(Path(bundle).resolve())
    return tuple(values)


def _load_json(path: Path, *, maximum: int) -> object:
    try:
        details = path.lstat()
        if (
            stat.S_ISLNK(details.st_mode)
            or not stat.S_ISREG(details.st_mode)
            or details.st_size <= 0
            or details.st_size > maximum
        ):
            raise ComponentStoreError("component metadata file is invalid")
        raw = path.read_bytes()
    except OSError as exc:
        raise ComponentStoreError("component metadata file is unreadable") from exc

    def object_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ComponentStoreError("component metadata has duplicate keys")
            result[key] = value
        return result

    def reject(value: str) -> object:
        raise ComponentStoreError("component metadata contains invalid numbers")

    try:
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=object_pairs,
            parse_float=reject,
            parse_constant=reject,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ComponentStoreError("component metadata is malformed") from exc


def _remove_tree(path: Path) -> None:
    if path.is_symlink():
        raise ComponentStoreError("refusing to remove a symlinked component tree")
    shutil.rmtree(path)


def _sha256_regular_file(
    path: Path,
    *,
    expected_details: os.stat_result,
) -> str:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        actual = os.fstat(descriptor)
        if (
            not stat.S_ISREG(actual.st_mode)
            or actual.st_dev != expected_details.st_dev
            or actual.st_ino != expected_details.st_ino
        ):
            raise OSError("component file changed during verification")
        digest = hashlib.sha256()
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    finally:
        os.close(descriptor)


def _fsync_directory(path: Path) -> None:
    if os.name != "posix":
        return
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


__all__ = [
    "BusyCheck",
    "CachedArtifactSnapshot",
    "ComponentActivationResult",
    "ComponentBusyReason",
    "ComponentBusyStatus",
    "ComponentStagingArea",
    "ComponentStoreError",
    "ComponentTreeIntegrityError",
    "InstalledComponentSnapshot",
    "ManagedComponentStore",
    "default_component_store_root",
]
