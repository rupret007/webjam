"""Platform approval and installed-result verification for Jamulus updates.

The generic component store owns signed catalogs and exact downloaded bytes.
Official Jamulus installers remain platform-approved artifacts: this module is
the only boundary allowed to accept the macOS disk-image SLA, invoke an
operating-system installer, or validate an installed app.  No function here
uses ``sudo``, a command shell, Gatekeeper bypasses, or quarantine removal.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import re
import shutil
import stat
import struct
import subprocess
import sys
import time
from typing import Callable, Mapping, Protocol
import uuid

from core.component_download import verify_downloaded_file
from core.component_lock import InterProcessComponentLock
from core.component_store import (
    BusyCheck,
    ComponentBusyStatus,
    default_component_store_root,
)
from core.file_io import atomic_write_text
from core.jamulus_compatibility import (
    ActivationMode,
    ArtifactKind,
    ComponentTarget,
    JamulusCompatibility,
    JamulusCompatibilityError,
    JamulusCompatibilityRegistry,
    JamulusRole,
    RuntimeFileIdentity,
    SourceProvenance,
    official_jamulus_compatibility_registry,
)
from core.jamulus_child_environment import (
    JamulusChildEnvironmentError,
    sanitized_jamulus_child_environment as _core_child_environment,
)
from core.jamulus_profile import default_jamulus_version_probe
from core.jamulus_component_resolver import (
    ExternalComponentCandidate,
    ValidatedExternalComponent,
)


MACOS_JAMULUS_TEAM_ID = "V9ZZ6B9WH8"
MACOS_CLIENT_BUNDLE_ID = "app.jamulussoftware.Jamulus"
MACOS_SERVER_BUNDLE_ID = "app.jamulussoftware.JamulusServer"
MACOS_INTEGRATED_RUNTIME_CAPABILITY = "webjam-integrated-runtime"
MACOS_INTEGRATED_RUNTIME_VARIANT = "webjam-integrated"
# A catalog shape is never enough to activate code. This single feature gate
# remains off until the separate integrated store/verifier described by ADR
# 0008 exists and has passed its physical release gates.
MACOS_INTEGRATED_RUNTIME_VERIFIER_ENABLED = False
PLATFORM_STATE_SCHEMA = 1
PLATFORM_DESCRIPTOR_SCHEMA = 1
PLATFORM_INSTALLED_STATE_SCHEMA = 1
_MAX_METADATA_BYTES = 1024 * 1024
_RUNTIME_HASH_CHUNK_BYTES = 1024 * 1024
_WINDOWS_PE_X86_64_MACHINE = 0x8664
_LINUX_ELF_X86_64_MACHINE = 62
_VERSION_RE = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
_WINDOWS_REPARSE_POINT = 0x400
_WINDOWS_LOADABLE_SUFFIXES = frozenset({".dll", ".ocx", ".ax"})
class JamulusPlatformError(RuntimeError):
    """A platform approval or installed-result proof failed closed."""


class JamulusPlatformInstallationNotFound(JamulusPlatformError):
    """No canonical platform installation exists; no integrity failure occurred."""


class JamulusLicenseApprovalRequired(JamulusPlatformError):
    """The user has not explicitly accepted the upstream disk-image SLA."""


class JamulusPlatformInstallDeferred(JamulusPlatformError):
    """A live audio owner prevented a safe installed-result change."""

    def __init__(self, status: ComponentBusyStatus) -> None:
        super().__init__("Jamulus update is deferred until the session is idle")
        self.status = status


class MacOSExecutionContractKind(str, Enum):
    """How one verified macOS bundle may participate in WebJam.

    An untouched upstream app is valuable download/publisher evidence, but it
    is not a WebJam-managed runtime.  Only a separately cataloged,
    CI-normalized asset may ever use ``WEBJAM_INTEGRATED``.
    """

    OFFICIAL_SOURCE = "official-source"
    WEBJAM_INTEGRATED = "webjam-integrated"


@dataclass(frozen=True, slots=True)
class MacOSExecutionContract:
    """Live, typed execution facts for one exact macOS Jamulus role."""

    kind: MacOSExecutionContractKind
    role: JamulusRole
    target: ComponentTarget
    source_app_sandbox_enabled: bool
    source_entitlements_sha256: str
    runtime_capabilities: frozenset[str]
    activation_allowed: bool
    reason_code: str

    def __post_init__(self) -> None:
        try:
            kind = MacOSExecutionContractKind(self.kind)
            role = JamulusRole(self.role)
            target = ComponentTarget(self.target)
        except (TypeError, ValueError) as exc:
            raise JamulusPlatformError(
                "the macOS Jamulus execution contract is invalid"
            ) from exc
        if role not in {JamulusRole.CLIENT, JamulusRole.SERVER}:
            raise JamulusPlatformError(
                "the macOS Jamulus execution role is unsupported"
            )
        if target not in {
            ComponentTarget.MACOS_ARM64,
            ComponentTarget.MACOS_X64,
        }:
            raise JamulusPlatformError(
                "the macOS Jamulus execution target is invalid"
            )
        digest = str(self.source_entitlements_sha256).lower()
        if len(digest) != 64 or any(
            character not in "0123456789abcdef" for character in digest
        ):
            raise JamulusPlatformError(
                "the macOS Jamulus entitlement proof is invalid"
            )
        if isinstance(self.runtime_capabilities, (str, bytes)):
            raise JamulusPlatformError(
                "the macOS Jamulus execution capabilities are invalid"
            )
        try:
            capabilities = frozenset(self.runtime_capabilities)
        except TypeError as exc:
            raise JamulusPlatformError(
                "the macOS Jamulus execution capabilities are invalid"
            ) from exc
        if not capabilities or not all(
            isinstance(value, str) and value
            for value in capabilities
        ):
            raise JamulusPlatformError(
                "the macOS Jamulus execution capabilities are invalid"
            )
        if not isinstance(self.source_app_sandbox_enabled, bool) or not isinstance(
            self.activation_allowed, bool
        ):
            raise JamulusPlatformError(
                "the macOS Jamulus execution decision is invalid"
            )
        if (
            kind is MacOSExecutionContractKind.OFFICIAL_SOURCE
            and self.activation_allowed
        ):
            raise JamulusPlatformError(
                "an upstream macOS source bundle cannot be activated directly"
            )
        base_capabilities = (
            frozenset({"audio-client", "json-rpc-client", "native-gui"})
            if role is JamulusRole.CLIENT
            else frozenset({"audio-server", "json-rpc-server"})
        )
        if (
            kind is MacOSExecutionContractKind.OFFICIAL_SOURCE
            and capabilities != base_capabilities
        ):
            raise JamulusPlatformError(
                "an upstream macOS source bundle claims runtime-file capabilities"
            )
        integrated_capabilities = (
            base_capabilities
            | (
                frozenset(
                    {"webjam-route-profile", "webjam-integrated-runtime"}
                )
                if role is JamulusRole.CLIENT
                else frozenset({"recording", "webjam-integrated-runtime"})
            )
        )
        if (
            kind is MacOSExecutionContractKind.WEBJAM_INTEGRATED
            and self.activation_allowed
            and not integrated_capabilities.issubset(capabilities)
        ):
            raise JamulusPlatformError(
                "the integrated macOS Jamulus execution contract is incomplete"
            )
        if (
            kind is MacOSExecutionContractKind.WEBJAM_INTEGRATED
            and self.activation_allowed
            and self.source_app_sandbox_enabled
        ):
            raise JamulusPlatformError(
                "the integrated macOS Jamulus execution contract is sandboxed"
            )
        allowed_reasons = {
            "official-source-app-sandboxed",
            "webjam-integrated-runtime-required",
            "verified-webjam-integrated-runtime",
        }
        if self.reason_code not in allowed_reasons:
            raise JamulusPlatformError(
                "the macOS Jamulus execution reason is invalid"
            )
        if (
            self.activation_allowed
            and self.reason_code != "verified-webjam-integrated-runtime"
        ):
            raise JamulusPlatformError(
                "the macOS Jamulus execution approval reason is invalid"
            )
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "role", role)
        object.__setattr__(self, "target", target)
        object.__setattr__(self, "source_entitlements_sha256", digest)
        object.__setattr__(self, "runtime_capabilities", capabilities)

    def to_public_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "role": self.role.value,
            "target": self.target.value,
            "source_app_sandbox_enabled": self.source_app_sandbox_enabled,
            "source_entitlements_sha256": self.source_entitlements_sha256,
            "runtime_capabilities": sorted(self.runtime_capabilities),
            "activation_allowed": self.activation_allowed,
            "reason_code": self.reason_code,
        }


def macos_integrated_runtime_entry_is_eligible(
    entry: JamulusCompatibility,
    *,
    verifier_enabled: bool | None = None,
) -> bool:
    """Recognize the one catalog shape a future Mac verifier may activate.

    The shared feature gate is part of the predicate so updater selection and
    Bridge execution cannot drift. Untouched ``official`` /
    ``PLATFORM_APPROVAL`` entries never satisfy this contract, regardless of
    capability strings supplied by a catalog.
    """

    enabled = (
        MACOS_INTEGRATED_RUNTIME_VERIFIER_ENABLED
        if verifier_enabled is None
        else verifier_enabled
    )
    if enabled is not True or not isinstance(entry, JamulusCompatibility):
        return False
    if entry.role is JamulusRole.CLIENT:
        required = frozenset(
            {
                "audio-client",
                "json-rpc-client",
                "native-gui",
                "webjam-route-profile",
                MACOS_INTEGRATED_RUNTIME_CAPABILITY,
            }
        )
    elif entry.role is JamulusRole.SERVER:
        required = frozenset(
            {
                "audio-server",
                "json-rpc-server",
                "recording",
                MACOS_INTEGRATED_RUNTIME_CAPABILITY,
            }
        )
    else:
        return False
    return bool(
        entry.component_id == "jamulus"
        and entry.target
        in {
            ComponentTarget.MACOS_ARM64,
            ComponentTarget.MACOS_X64,
        }
        and entry.variant == MACOS_INTEGRATED_RUNTIME_VARIANT
        and entry.activation_mode is ActivationMode.MANAGED
        and entry.source.provenance is SourceProvenance.OFFICIAL_RELEASE
        and entry.artifact.kind
        in {ArtifactKind.ARCHIVE, ArtifactKind.APP_BUNDLE}
        and entry.runtime_files
        and entry.executable_relative_path
        and entry.capabilities.includes(required)
    )


def macos_integrated_runtime_contract_allows(
    entry: JamulusCompatibility,
    contract: MacOSExecutionContract,
    *,
    verifier_enabled: bool | None = None,
) -> bool:
    """Require matching live entitlement facts in addition to catalog shape."""

    if not macos_integrated_runtime_entry_is_eligible(
        entry,
        verifier_enabled=verifier_enabled,
    ) or not isinstance(contract, MacOSExecutionContract):
        return False
    required = (
        frozenset(
            {
                "audio-client",
                "json-rpc-client",
                "native-gui",
                "webjam-route-profile",
                MACOS_INTEGRATED_RUNTIME_CAPABILITY,
            }
        )
        if entry.role is JamulusRole.CLIENT
        else frozenset(
            {
                "audio-server",
                "json-rpc-server",
                "recording",
                MACOS_INTEGRATED_RUNTIME_CAPABILITY,
            }
        )
    )
    return bool(
        contract.kind is MacOSExecutionContractKind.WEBJAM_INTEGRATED
        and contract.role is entry.role
        and contract.target is entry.target
        and contract.source_app_sandbox_enabled is False
        and contract.activation_allowed is True
        and contract.reason_code == "verified-webjam-integrated-runtime"
        and required.issubset(contract.runtime_capabilities)
    )


class CommandRunner(Protocol):
    def __call__(
        self,
        arguments: list[str],
        *,
        input: bytes | None = None,
        timeout: float,
    ) -> subprocess.CompletedProcess[bytes]: ...


def _run_command(
    arguments: list[str],
    *,
    input: bytes | None = None,
    timeout: float,
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        arguments,
        input=input,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
        shell=False,
    )


@dataclass(frozen=True, slots=True)
class VerifiedMacBundle:
    path: Path
    role: JamulusRole
    version: str
    architectures: tuple[str, ...]
    team_identifier: str
    bundle_identifier: str
    app_sandbox_enabled: bool
    entitlements_sha256: str


class MacOSBundleVerifier:
    """Prove one untouched official Jamulus app with Apple tooling."""

    def __init__(
        self,
        *,
        command_runner: CommandRunner = _run_command,
    ) -> None:
        self._run = command_runner

    def verify(
        self,
        bundle: str | Path,
        *,
        role: JamulusRole,
        version: str,
        target: ComponentTarget,
    ) -> VerifiedMacBundle:
        if sys.platform != "darwin":
            raise JamulusPlatformError(
                "macOS Jamulus bundle verification requires macOS"
            )
        role = JamulusRole(role)
        target = ComponentTarget(target)
        if role not in {JamulusRole.CLIENT, JamulusRole.SERVER}:
            raise JamulusPlatformError(
                "the official macOS updater supports client and server roles only"
            )
        if target not in {
            ComponentTarget.MACOS_ARM64,
            ComponentTarget.MACOS_X64,
        }:
            raise JamulusPlatformError("the Jamulus bundle target is not macOS")
        path = Path(bundle)
        if path.is_symlink() or not path.is_dir() or path.suffix != ".app":
            raise JamulusPlatformError("the Jamulus app bundle is unavailable")
        _verify_bundle_symlinks(path)

        expected_name = (
            "Jamulus.app" if role is JamulusRole.CLIENT else "JamulusServer.app"
        )
        expected_id = (
            MACOS_CLIENT_BUNDLE_ID
            if role is JamulusRole.CLIENT
            else MACOS_SERVER_BUNDLE_ID
        )
        executable_name = "Jamulus" if role is JamulusRole.CLIENT else "JamulusServer"
        if path.name != expected_name:
            raise JamulusPlatformError("the Jamulus app bundle has an unexpected name")
        executable = path / "Contents" / "MacOS" / executable_name
        try:
            details = executable.lstat()
        except OSError as exc:
            raise JamulusPlatformError("the Jamulus executable is unavailable") from exc
        if (
            stat.S_ISLNK(details.st_mode)
            or not stat.S_ISREG(details.st_mode)
            or not details.st_mode & 0o111
        ):
            raise JamulusPlatformError("the Jamulus executable is invalid")

        info_path = path / "Contents" / "Info.plist"
        try:
            if info_path.is_symlink() or info_path.stat().st_size > _MAX_METADATA_BYTES:
                raise JamulusPlatformError("the Jamulus bundle metadata is invalid")
            info = plistlib.loads(info_path.read_bytes())
        except (OSError, plistlib.InvalidFileException) as exc:
            raise JamulusPlatformError(
                "the Jamulus bundle metadata could not be verified"
            ) from exc
        if not isinstance(info, dict):
            raise JamulusPlatformError("the Jamulus bundle metadata is invalid")
        bundle_id = str(info.get("CFBundleIdentifier", "") or "")
        bundle_version = str(info.get("CFBundleVersion", "") or "")
        if bundle_id != expected_id or bundle_version != version:
            raise JamulusPlatformError(
                "the Jamulus bundle identity or version is not approved"
            )

        verified = self._run(
            [
                "/usr/bin/codesign",
                "--verify",
                "--deep",
                "--strict",
                "--verbose=2",
                str(path),
            ],
            timeout=60.0,
        )
        if verified.returncode != 0:
            raise JamulusPlatformError("the Jamulus Developer ID signature is invalid")
        details_result = self._run(
            ["/usr/bin/codesign", "-d", "--verbose=4", str(path)],
            timeout=30.0,
        )
        details_text = _bounded_output(details_result, maximum=64 * 1024)
        if (
            details_result.returncode != 0
            or f"Identifier={expected_id}" not in details_text
            or f"TeamIdentifier={MACOS_JAMULUS_TEAM_ID}" not in details_text
            or (
                "Authority=Developer ID Application: Jonathan Chung "
                f"({MACOS_JAMULUS_TEAM_ID})"
            )
            not in details_text
        ):
            raise JamulusPlatformError("the Jamulus publisher identity is not approved")
        entitlements_result = self._run(
            [
                "/usr/bin/codesign",
                "-d",
                "--xml",
                "--entitlements",
                "-",
                str(path),
            ],
            timeout=30.0,
        )
        if entitlements_result.returncode != 0:
            raise JamulusPlatformError(
                "the Jamulus signed entitlements could not be verified"
            )
        entitlements = _codesign_entitlements(entitlements_result)
        sandbox_value = entitlements.get("com.apple.security.app-sandbox", False)
        if not isinstance(sandbox_value, bool):
            raise JamulusPlatformError(
                "the Jamulus App Sandbox entitlement is invalid"
            )
        canonical_entitlements = plistlib.dumps(
            entitlements,
            fmt=plistlib.FMT_BINARY,
            sort_keys=True,
        )
        entitlements_sha256 = hashlib.sha256(canonical_entitlements).hexdigest()
        assessment = self._run(
            ["/usr/sbin/spctl", "-a", "-vv", "-t", "execute", str(path)],
            timeout=60.0,
        )
        assessment_text = _bounded_output(assessment, maximum=64 * 1024)
        if (
            assessment.returncode != 0
            or "source=Notarized Developer ID" not in assessment_text
        ):
            raise JamulusPlatformError(
                "Apple notarization could not be verified for Jamulus"
            )
        architectures_result = self._run(
            ["/usr/bin/lipo", "-archs", str(executable)],
            timeout=30.0,
        )
        if architectures_result.returncode != 0:
            raise JamulusPlatformError(
                "the Jamulus executable architecture could not be verified"
            )
        architecture_text = _bounded_output(architectures_result, maximum=4096).strip()
        architectures = tuple(sorted(set(architecture_text.split())))
        required = "arm64" if target is ComponentTarget.MACOS_ARM64 else "x86_64"
        if required not in architectures:
            raise JamulusPlatformError(
                "the Jamulus executable does not support this Mac"
            )
        return VerifiedMacBundle(
            path=path,
            role=role,
            version=version,
            architectures=architectures,
            team_identifier=MACOS_JAMULUS_TEAM_ID,
            bundle_identifier=bundle_id,
            app_sandbox_enabled=sandbox_value,
            entitlements_sha256=entitlements_sha256,
        )


def _official_source_execution_contract(
    verified: VerifiedMacBundle,
    *,
    target: ComponentTarget,
) -> MacOSExecutionContract:
    """Describe why an untouched official app is evidence, not a runtime."""

    if not isinstance(verified, VerifiedMacBundle):
        raise JamulusPlatformError(
            "the official macOS Jamulus verification result is invalid"
        )
    target = ComponentTarget(target)
    required_architecture = (
        "arm64"
        if target is ComponentTarget.MACOS_ARM64
        else "x86_64"
        if target is ComponentTarget.MACOS_X64
        else ""
    )
    if not required_architecture or required_architecture not in verified.architectures:
        raise JamulusPlatformError(
            "the official macOS Jamulus execution target is invalid"
        )
    if verified.role is JamulusRole.CLIENT:
        capabilities = frozenset(
            {"audio-client", "json-rpc-client", "native-gui"}
        )
    elif verified.role is JamulusRole.SERVER:
        capabilities = frozenset({"audio-server", "json-rpc-server"})
    else:
        raise JamulusPlatformError(
            "the official macOS Jamulus execution role is unsupported"
        )
    return MacOSExecutionContract(
        kind=MacOSExecutionContractKind.OFFICIAL_SOURCE,
        role=verified.role,
        target=target,
        source_app_sandbox_enabled=verified.app_sandbox_enabled,
        source_entitlements_sha256=verified.entitlements_sha256,
        runtime_capabilities=capabilities,
        activation_allowed=False,
        reason_code=(
            "official-source-app-sandboxed"
            if verified.app_sandbox_enabled
            else "webjam-integrated-runtime-required"
        ),
    )


@dataclass(frozen=True, slots=True)
class MacOSInstalledJamulus:
    version: str
    target: ComponentTarget
    artifact_sha256: str
    client_path: Path
    server_path: Path
    client_execution_contract: MacOSExecutionContract
    server_execution_contract: MacOSExecutionContract
    is_current: bool
    is_previous: bool

    def execution_contract_for(
        self, role: JamulusRole
    ) -> MacOSExecutionContract:
        selected = JamulusRole(role)
        if selected is JamulusRole.CLIENT:
            return self.client_execution_contract
        if selected is JamulusRole.SERVER:
            return self.server_execution_contract
        raise JamulusPlatformError(
            "the installed macOS Jamulus role is unsupported"
        )

    @property
    def activation_allowed(self) -> bool:
        return bool(
            self.client_execution_contract.activation_allowed
            and self.server_execution_contract.activation_allowed
        )

    def to_public_dict(self) -> dict[str, object]:
        return {
            "version": self.version,
            "target": self.target.value,
            "artifact_sha256": self.artifact_sha256,
            "activation_allowed": self.activation_allowed,
            "client_execution_contract": (
                self.client_execution_contract.to_public_dict()
            ),
            "server_execution_contract": (
                self.server_execution_contract.to_public_dict()
            ),
            "is_current": self.is_current,
            "is_previous": self.is_previous,
        }


@dataclass(frozen=True, slots=True)
class MacOSInstallResult:
    current: MacOSInstalledJamulus
    previous: MacOSInstalledJamulus | None


@dataclass(frozen=True, slots=True)
class _MacPointer:
    version: str
    target: str
    artifact_sha256: str

    def to_dict(self) -> dict[str, str]:
        return {
            "version": self.version,
            "target": self.target,
            "artifact_sha256": self.artifact_sha256,
        }

    @classmethod
    def from_dict(cls, value: object) -> "_MacPointer":
        if not isinstance(value, dict) or frozenset(value) != frozenset(
            {"version", "target", "artifact_sha256"}
        ):
            raise JamulusPlatformError("Jamulus platform pointer is invalid")
        if not all(isinstance(item, str) for item in value.values()):
            raise JamulusPlatformError("Jamulus platform pointer is invalid")
        try:
            target = ComponentTarget(value["target"])
        except ValueError as exc:
            raise JamulusPlatformError(
                "Jamulus platform pointer target is invalid"
            ) from exc
        if target not in {
            ComponentTarget.MACOS_ARM64,
            ComponentTarget.MACOS_X64,
        }:
            raise JamulusPlatformError("Jamulus platform pointer target is invalid")
        version = value["version"]
        if not _VERSION_RE.fullmatch(version):
            raise JamulusPlatformError("Jamulus platform pointer version is invalid")
        digest = value["artifact_sha256"]
        if len(digest) != 64 or any(
            character not in "0123456789abcdef" for character in digest
        ):
            raise JamulusPlatformError("Jamulus platform pointer digest is invalid")
        return cls(
            version=version,
            target=target.value,
            artifact_sha256=digest,
        )


class MacOSJamulusComponentStore:
    """Versioned official source bundles with current/previous evidence pointers.

    The untouched Developer-ID apps retained here are never an activatable
    WebJam runtime.  See :class:`MacOSExecutionContract`.
    """

    _VOLUME_INVENTORY = frozenset(
        {
            ".background",
            ".DS_Store",
            "Applications",
            "Jamulus.app",
            "JamulusServer.app",
        }
    )

    def __init__(
        self,
        registry: JamulusCompatibilityRegistry | None = None,
        *,
        webjam_version: str,
        root: str | Path | None = None,
        verifier: MacOSBundleVerifier | None = None,
        command_runner: CommandRunner = _run_command,
        lock_timeout: float = 5.0,
    ) -> None:
        if sys.platform != "darwin":
            raise JamulusPlatformError("the managed macOS Jamulus store requires macOS")
        if not isinstance(webjam_version, str) or not _VERSION_RE.fullmatch(
            webjam_version
        ):
            raise ValueError("webjam_version must be a semantic version")
        self.webjam_version = webjam_version
        self.registry = registry or official_jamulus_compatibility_registry()
        base = Path(root) if root is not None else default_component_store_root()
        self.root = base / "platform" / "macos"
        self.installed_root = self.root / "installed"
        self.staging_root = self.root / "staging"
        self.state_path = self.root / "state.json"
        self.lock_path = self.root / ".platform.lock"
        self._run = command_runner
        self.verifier = verifier or MacOSBundleVerifier(command_runner=command_runner)
        self.lock_timeout = float(lock_timeout)
        self._ensure_root()

    def install_from_dmg(
        self,
        *,
        client_entry: JamulusCompatibility,
        server_entry: JamulusCompatibility,
        dmg_path: str | Path,
        license_accepted: bool,
        busy_check: BusyCheck,
        authorization_check: PointOfUseAuthorization | None = None,
    ) -> MacOSInstallResult:
        """Install only after one explicit user SLA acceptance and idle proof."""

        if license_accepted is not True:
            raise JamulusLicenseApprovalRequired(
                "the Jamulus license must be accepted before installation"
            )
        if authorization_check is not None and not callable(authorization_check):
            raise TypeError("authorization_check must be callable")
        client, server = self._require_pair(client_entry, server_entry)
        busy = _check_busy(busy_check)
        if busy is not None:
            raise JamulusPlatformInstallDeferred(busy)
        verified_download = verify_downloaded_file(dmg_path, client.artifact)
        if server.artifact != client.artifact:
            raise JamulusPlatformError(
                "client and server are not from the same approved disk image"
            )

        with InterProcessComponentLock(self.lock_path, timeout=self.lock_timeout):
            # Re-check idle state while holding the cross-process install lock.
            busy = _check_busy(busy_check)
            if busy is not None:
                raise JamulusPlatformInstallDeferred(busy)
            destination = self._destination(client)
            if destination.exists():
                installed = self._verify_destination(destination, client, server)
            else:
                installed = self._install_new(
                    client=client,
                    server=server,
                    dmg_path=verified_download.path,
                    destination=destination,
                )
            current_pointer, previous_pointer = self._read_state()
            pointer = _MacPointer(
                version=client.version,
                target=client.target.value,
                artifact_sha256=client.artifact.sha256,
            )
            previous = None
            if current_pointer is not None and current_pointer != pointer:
                previous_pointer = current_pointer
                previous = self._snapshot_for_pointer(
                    previous_pointer, current=False, previous=True
                )
            elif previous_pointer is not None:
                previous = self._snapshot_for_pointer(
                    previous_pointer, current=False, previous=True
                )
            if authorization_check is not None:
                authorization_check(client, server)
            self._write_state(pointer, previous_pointer)
            current = MacOSInstalledJamulus(
                version=installed.version,
                target=installed.target,
                artifact_sha256=installed.artifact_sha256,
                client_path=installed.client_path,
                server_path=installed.server_path,
                client_execution_contract=(
                    installed.client_execution_contract
                ),
                server_execution_contract=(
                    installed.server_execution_contract
                ),
                is_current=True,
                is_previous=False,
            )
            self._prune_except(pointer, previous_pointer)
            return MacOSInstallResult(current=current, previous=previous)

    def current(self) -> MacOSInstalledJamulus | None:
        with InterProcessComponentLock(self.lock_path, timeout=self.lock_timeout):
            current, _previous = self._read_state()
            if current is None:
                return None
            return self._snapshot_for_pointer(
                current, current=True, previous=False
            )

    def previous(self) -> MacOSInstalledJamulus | None:
        with InterProcessComponentLock(self.lock_path, timeout=self.lock_timeout):
            _current, previous = self._read_state()
            if previous is None:
                return None
            return self._snapshot_for_pointer(previous, current=False, previous=True)

    def rollback(self, *, busy_check: BusyCheck) -> MacOSInstallResult:
        with InterProcessComponentLock(self.lock_path, timeout=self.lock_timeout):
            busy = _check_busy(busy_check)
            if busy is not None:
                raise JamulusPlatformInstallDeferred(busy)
            current_pointer, previous_pointer = self._read_state()
            if previous_pointer is None:
                raise JamulusPlatformError("no previous Jamulus component is available")
            previous = self._snapshot_for_pointer(
                previous_pointer, current=True, previous=False
            )
            old_current = None
            next_previous = current_pointer
            if current_pointer is not None:
                try:
                    old_current = self._snapshot_for_pointer(
                        current_pointer, current=False, previous=True
                    )
                except JamulusPlatformError:
                    # Explicit idle rollback is the recovery boundary for a
                    # damaged current install. Never preserve an invalid
                    # pointer as the new fallback.
                    next_previous = None
            self._write_state(previous_pointer, next_previous)
            return MacOSInstallResult(current=previous, previous=old_current)

    def external_validator(
        self,
        candidate: ExternalComponentCandidate,
        registry: JamulusCompatibilityRegistry,
        role: JamulusRole,
        target: ComponentTarget,
    ) -> ValidatedExternalComponent | None:
        """Expose only a separately proven WebJam-integrated runtime.

        The official DMG store deliberately retains verified upstream bundles
        as source evidence.  Those untouched PLATFORM_APPROVAL apps are never
        returned here, even when a stale signed catalog claimed broader
        capabilities.
        """

        role = JamulusRole(role)
        target = ComponentTarget(target)
        if role not in {JamulusRole.CLIENT, JamulusRole.SERVER}:
            return None
        try:
            with InterProcessComponentLock(
                self.lock_path, timeout=self.lock_timeout
            ):
                current_pointer, _previous_pointer = self._read_state()
                if current_pointer is None:
                    return None
                installed = self._snapshot_for_pointer(
                    current_pointer,
                    current=True,
                    previous=False,
                )
        except JamulusPlatformError:
            return None
        if installed.target is not target:
            return None
        contract = installed.execution_contract_for(role)
        # This store owns only untouched upstream source bundles. A future
        # integrated-runtime store/verifier must be a separate implementation;
        # neither a caller-supplied path nor a catalog object can upgrade this
        # source-only contract.
        _ = (candidate, registry, contract)
        return None

    def _install_new(
        self,
        *,
        client: JamulusCompatibility,
        server: JamulusCompatibility,
        dmg_path: Path,
        destination: Path,
    ) -> MacOSInstalledJamulus:
        token = uuid.uuid4().hex
        mountpoint = self.staging_root / f"mount-{token}"
        staging = self.staging_root / f"install-{token}"
        mountpoint.mkdir(mode=0o700)
        staging.mkdir(mode=0o700)
        mounted = False
        try:
            # The caller already showed the exact packaged license and received
            # an explicit Agree click. Feeding Y is the mechanical continuation
            # of that one approval; this code path is unreachable otherwise.
            attach = self._run(
                [
                    "/usr/bin/hdiutil",
                    "attach",
                    "-readonly",
                    "-nobrowse",
                    "-noautoopen",
                    "-mountpoint",
                    str(mountpoint),
                    str(dmg_path),
                ],
                input=b"Y\n",
                timeout=120.0,
            )
            if attach.returncode != 0:
                raise JamulusPlatformError(
                    "the verified Jamulus disk image could not be mounted"
                )
            mounted = True
            self._verify_volume_inventory(mountpoint)
            source_client = mountpoint / "Jamulus.app"
            source_server = mountpoint / "JamulusServer.app"
            self.verifier.verify(
                source_client,
                role=JamulusRole.CLIENT,
                version=client.version,
                target=client.target,
            )
            self.verifier.verify(
                source_server,
                role=JamulusRole.SERVER,
                version=server.version,
                target=server.target,
            )
            self._copy_bundle(source_client, staging / "Jamulus.app")
            self._copy_bundle(source_server, staging / "JamulusServer.app")
            self._set_quarantine(staging / "Jamulus.app")
            self._set_quarantine(staging / "JamulusServer.app")
            descriptor = {
                "schema": PLATFORM_DESCRIPTOR_SCHEMA,
                "client": client.to_dict(),
                "server": server.to_dict(),
            }
            atomic_write_text(
                staging / "descriptor.json",
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
            self._verify_destination(staging, client, server)
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if destination.exists() or destination.is_symlink():
                raise JamulusPlatformError(
                    "the Jamulus install destination changed unexpectedly"
                )
            os.replace(staging, destination)
            _fsync_directory(destination.parent)
            return self._verify_destination(destination, client, server)
        finally:
            detach_failed = False
            if mounted:
                detach = self._run(
                    ["/usr/bin/hdiutil", "detach", str(mountpoint)],
                    timeout=60.0,
                )
                detach_failed = detach.returncode != 0
            if mountpoint.exists() and not any(mountpoint.iterdir()):
                mountpoint.rmdir()
            if staging.exists():
                _remove_owned_tree(staging, root=self.staging_root)
            if detach_failed:
                raise JamulusPlatformError(
                    "the Jamulus installer volume could not be released safely"
                )

    def _copy_bundle(self, source: Path, destination: Path) -> None:
        result = self._run(
            [
                "/usr/bin/ditto",
                "--rsrc",
                "--extattr",
                str(source),
                str(destination),
            ],
            timeout=180.0,
        )
        if result.returncode != 0:
            raise JamulusPlatformError("the Jamulus app bundle could not be staged")

    def _set_quarantine(self, bundle: Path) -> None:
        # Programmatic downloads do not always receive a browser quarantine
        # xattr. Add (never remove) one so Gatekeeper assesses the official
        # notarized Developer ID app on first use.
        stamp = f"0083;{int(time.time()):x};WebJam;"
        result = self._run(
            [
                "/usr/bin/xattr",
                "-w",
                "com.apple.quarantine",
                stamp,
                str(bundle),
            ],
            timeout=30.0,
        )
        if result.returncode != 0:
            raise JamulusPlatformError(
                "the Jamulus quarantine marker could not be preserved"
            )

    def _verify_volume_inventory(self, mountpoint: Path) -> None:
        observed = frozenset(item.name for item in mountpoint.iterdir())
        if observed != self._VOLUME_INVENTORY:
            raise JamulusPlatformError(
                "the Jamulus disk image contains an unexpected inventory"
            )
        applications = mountpoint / "Applications"
        if not applications.is_symlink():
            raise JamulusPlatformError(
                "the Jamulus disk image Applications link is invalid"
            )
        try:
            target = os.readlink(applications)
        except OSError as exc:
            raise JamulusPlatformError(
                "the Jamulus disk image Applications link is unreadable"
            ) from exc
        if target != "/Applications":
            raise JamulusPlatformError(
                "the Jamulus disk image Applications link is unexpected"
            )
        background = mountpoint / ".background"
        if background.is_symlink() or not background.is_dir():
            raise JamulusPlatformError(
                "the Jamulus disk image background inventory is invalid"
            )
        if frozenset(item.name for item in background.iterdir()) != frozenset(
            {"installerbackground.png"}
        ):
            raise JamulusPlatformError(
                "the Jamulus disk image background inventory is unexpected"
            )

    def _verify_destination(
        self,
        destination: Path,
        client: JamulusCompatibility,
        server: JamulusCompatibility,
    ) -> MacOSInstalledJamulus:
        if destination.is_symlink() or not destination.is_dir():
            raise JamulusPlatformError("the installed Jamulus component is unavailable")
        observed = frozenset(item.name for item in destination.iterdir())
        if observed != frozenset(
            {"descriptor.json", "Jamulus.app", "JamulusServer.app"}
        ):
            raise JamulusPlatformError(
                "the installed Jamulus component contains unexpected files"
            )
        descriptor_path = destination / "descriptor.json"
        value = _read_json(descriptor_path)
        if not isinstance(value, dict) or frozenset(value) != frozenset(
            {"schema", "client", "server"}
        ):
            raise JamulusPlatformError(
                "the installed Jamulus component descriptor is invalid"
            )
        if value["schema"] != PLATFORM_DESCRIPTOR_SCHEMA:
            raise JamulusPlatformError(
                "the installed Jamulus component descriptor is unsupported"
            )
        described_client = JamulusCompatibility.from_dict(value["client"])
        described_server = JamulusCompatibility.from_dict(value["server"])
        if described_client != client or described_server != server:
            raise JamulusPlatformError(
                "the installed Jamulus component identity does not match"
            )
        self.registry.require_exact(described_client)
        self.registry.require_exact(described_server)
        client_bundle = destination / "Jamulus.app"
        server_bundle = destination / "JamulusServer.app"
        verified_client = self.verifier.verify(
            client_bundle,
            role=JamulusRole.CLIENT,
            version=client.version,
            target=client.target,
        )
        verified_server = self.verifier.verify(
            server_bundle,
            role=JamulusRole.SERVER,
            version=server.version,
            target=server.target,
        )
        client_contract = _official_source_execution_contract(
            verified_client,
            target=client.target,
        )
        server_contract = _official_source_execution_contract(
            verified_server,
            target=server.target,
        )
        return MacOSInstalledJamulus(
            version=client.version,
            target=client.target,
            artifact_sha256=client.artifact.sha256,
            client_path=(client_bundle / "Contents" / "MacOS" / "Jamulus"),
            server_path=(server_bundle / "Contents" / "MacOS" / "JamulusServer"),
            client_execution_contract=client_contract,
            server_execution_contract=server_contract,
            is_current=False,
            is_previous=False,
        )

    def _require_pair(
        self,
        client_entry: JamulusCompatibility,
        server_entry: JamulusCompatibility,
    ) -> tuple[JamulusCompatibility, JamulusCompatibility]:
        client = self.registry.require_exact(client_entry)
        server = self.registry.require_exact(server_entry)
        if (
            client.role is not JamulusRole.CLIENT
            or server.role is not JamulusRole.SERVER
            or client.target != server.target
            or client.version != server.version
            or client.artifact != server.artifact
            or client.activation_mode is not ActivationMode.PLATFORM_APPROVAL
            or server.activation_mode is not ActivationMode.PLATFORM_APPROVAL
            or client.target
            not in {ComponentTarget.MACOS_ARM64, ComponentTarget.MACOS_X64}
        ):
            raise JamulusPlatformError(
                "the Jamulus client/server update pair is not approved"
            )
        return client, server

    def _destination(self, entry: JamulusCompatibility) -> Path:
        return (
            self.installed_root
            / entry.version
            / entry.target.value
            / entry.artifact.sha256
        )

    def _snapshot_for_pointer(
        self,
        pointer: _MacPointer,
        *,
        current: bool,
        previous: bool,
    ) -> MacOSInstalledJamulus:
        target = ComponentTarget(pointer.target)
        client = self.registry.exact(
            component_id="jamulus",
            role=JamulusRole.CLIENT,
            target=target,
            version=pointer.version,
        )
        server = self.registry.exact(
            component_id="jamulus",
            role=JamulusRole.SERVER,
            target=target,
            version=pointer.version,
        )
        if (
            client.artifact.sha256 != pointer.artifact_sha256
            or server.artifact.sha256 != pointer.artifact_sha256
        ):
            raise JamulusPlatformError("the Jamulus platform pointer is not approved")
        snapshot = self._verify_destination(self._destination(client), client, server)
        return MacOSInstalledJamulus(
            version=snapshot.version,
            target=snapshot.target,
            artifact_sha256=snapshot.artifact_sha256,
            client_path=snapshot.client_path,
            server_path=snapshot.server_path,
            client_execution_contract=snapshot.client_execution_contract,
            server_execution_contract=snapshot.server_execution_contract,
            is_current=current,
            is_previous=previous,
        )

    def _read_state(self) -> tuple[_MacPointer | None, _MacPointer | None]:
        if not self.state_path.exists():
            return None, None
        value = _read_json(self.state_path)
        if not isinstance(value, dict) or frozenset(value) != frozenset(
            {"schema", "current", "previous"}
        ):
            raise JamulusPlatformError("Jamulus platform state is invalid")
        if value["schema"] != PLATFORM_STATE_SCHEMA:
            raise JamulusPlatformError("Jamulus platform state is unsupported")
        current = (
            _MacPointer.from_dict(value["current"])
            if value["current"] is not None
            else None
        )
        previous = (
            _MacPointer.from_dict(value["previous"])
            if value["previous"] is not None
            else None
        )
        return current, previous

    def _write_state(
        self,
        current: _MacPointer | None,
        previous: _MacPointer | None,
    ) -> None:
        atomic_write_text(
            self.state_path,
            json.dumps(
                {
                    "schema": PLATFORM_STATE_SCHEMA,
                    "current": current.to_dict() if current else None,
                    "previous": previous.to_dict() if previous else None,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            mode=0o600,
        )

    def _prune_except(
        self,
        current: _MacPointer | None,
        previous: _MacPointer | None,
    ) -> None:
        retained = {
            (
                pointer.version,
                pointer.target,
                pointer.artifact_sha256,
            )
            for pointer in (current, previous)
            if pointer is not None
        }
        if not self.installed_root.exists():
            return
        for version_dir in tuple(self.installed_root.iterdir()):
            if version_dir.is_symlink() or not version_dir.is_dir():
                continue
            for target_dir in tuple(version_dir.iterdir()):
                if target_dir.is_symlink() or not target_dir.is_dir():
                    continue
                for digest_dir in tuple(target_dir.iterdir()):
                    key = (version_dir.name, target_dir.name, digest_dir.name)
                    if key in retained:
                        continue
                    # Delete only a tree that still proves as one exact
                    # registry-approved install; ambiguous material is retained.
                    try:
                        pointer = _MacPointer(
                            version=version_dir.name,
                            target=target_dir.name,
                            artifact_sha256=digest_dir.name,
                        )
                        self._snapshot_for_pointer(
                            pointer, current=False, previous=False
                        )
                    except (ValueError, JamulusPlatformError):
                        continue
                    _remove_owned_tree(digest_dir, root=self.installed_root)

    def _ensure_root(self) -> None:
        for path in (self.root, self.installed_root, self.staging_root):
            if path.exists() and path.is_symlink():
                raise JamulusPlatformError(
                    "the Jamulus platform store cannot be a symlink"
                )
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
            if not path.is_dir():
                raise JamulusPlatformError("the Jamulus platform store is unavailable")
            os.chmod(path, 0o700)


InstalledVersionProbe = Callable[[str], str]
CanonicalPathProvider = Callable[[], tuple[Path, ...]]
PointOfUseAuthorization = Callable[
    [JamulusCompatibility, JamulusCompatibility], object
]
PlatformPathTrustVerifier = Callable[[Path, ComponentTarget], None]
LinuxPackageTrustVerifier = Callable[[Path, str], None]
WindowsRuntimeInventoryProvider = Callable[
    [JamulusCompatibility], tuple[RuntimeFileIdentity, ...]
]


@dataclass(frozen=True, slots=True)
class PlatformInstalledJamulus:
    """One freshly revalidated OS-installed Jamulus role.

    The exact catalog entry remains attached to the snapshot so runtime
    resolution never has to infer identity from a version string or path.
    Windows and Linux publisher identity is deliberately *not* claimed here:
    this boundary proves the exact catalog bytes, version, and architecture.
    """

    entry: JamulusCompatibility
    executable_path: Path
    trust_policy_verified: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.entry, JamulusCompatibility):
            raise TypeError("installed Jamulus entry is invalid")
        if self.entry.role not in {JamulusRole.CLIENT, JamulusRole.SERVER}:
            raise ValueError("installed Jamulus role is unsupported")
        if not isinstance(self.trust_policy_verified, bool):
            raise TypeError("installed Jamulus trust-policy result is invalid")
        object.__setattr__(self, "executable_path", Path(self.executable_path))

    @property
    def role(self) -> JamulusRole:
        return self.entry.role

    @property
    def version(self) -> str:
        return self.entry.version

    @property
    def target(self) -> ComponentTarget:
        return self.entry.target

    @property
    def artifact_sha256(self) -> str:
        return self.entry.artifact.sha256

    @property
    def content_verified(self) -> bool:
        return True

    @property
    def version_verified(self) -> bool:
        return True

    @property
    def architecture_verified(self) -> bool:
        return True

    @property
    def publisher_verified(self) -> bool:
        return False

    def to_public_dict(self) -> dict[str, object]:
        """Return receipt evidence without exposing a local installation path."""

        return {
            "component_id": self.entry.component_id,
            "role": self.role.value,
            "target": self.target.value,
            "version": self.version,
            "variant": self.entry.variant,
            "artifact_sha256": self.artifact_sha256,
            "runtime_sha256": _runtime_identity(self.entry).sha256,
            "content_verified": True,
            "version_verified": True,
            "architecture_verified": True,
            "publisher_verified": False,
            "trust_policy_verified": self.trust_policy_verified,
        }


@dataclass(frozen=True, slots=True)
class PlatformInstallResult:
    client: PlatformInstalledJamulus
    server: PlatformInstalledJamulus

    def __post_init__(self) -> None:
        if self.client.role is not JamulusRole.CLIENT:
            raise ValueError("platform install result client role is invalid")
        if self.server.role is not JamulusRole.SERVER:
            raise ValueError("platform install result server role is invalid")


class PlatformInstalledJamulusStore:
    """Receipt and exact post-install verification for Windows and Linux.

    Platform installers remain user/OS approved and install outside WebJam's
    component tree.  The private receipt stores only exact signed-catalog
    entries.  Every lookup rediscovers an allowed OS path and rechecks the
    executable bytes, version, and binary architecture; paths never become
    durable trust evidence.
    """

    def __init__(
        self,
        registry: JamulusCompatibilityRegistry | None = None,
        *,
        target: ComponentTarget | None = None,
        root: str | Path | None = None,
        platform_name: str | None = None,
        webjam_version: str | None = None,
        environ: Mapping[str, str] | None = None,
        version_probe: InstalledVersionProbe = default_jamulus_version_probe,
        canonical_path_provider: CanonicalPathProvider | None = None,
        path_trust_verifier: PlatformPathTrustVerifier | None = None,
        linux_package_verifier: LinuxPackageTrustVerifier | None = None,
        windows_inventory_provider: WindowsRuntimeInventoryProvider | None = None,
        lock_timeout: float = 5.0,
    ) -> None:
        platform_value = (platform_name or sys.platform).strip().lower()
        if platform_value in {"win32", "cygwin", "msys"}:
            normalized_platform = "win32"
            expected_target = ComponentTarget.WINDOWS_X64
        elif platform_value.startswith("linux"):
            normalized_platform = "linux"
            expected_target = ComponentTarget.LINUX_X64
        else:
            raise JamulusPlatformError(
                "the installed Jamulus receipt store supports Windows and Linux"
            )
        selected_target = ComponentTarget(target or expected_target)
        if selected_target is not expected_target:
            raise JamulusPlatformError(
                "the installed Jamulus receipt target does not match this platform"
            )
        if not callable(version_probe):
            raise TypeError("version_probe must be callable")
        if canonical_path_provider is not None and not callable(
            canonical_path_provider
        ):
            raise TypeError("canonical_path_provider must be callable")
        for callback, label in (
            (path_trust_verifier, "path_trust_verifier"),
            (linux_package_verifier, "linux_package_verifier"),
            (windows_inventory_provider, "windows_inventory_provider"),
        ):
            if callback is not None and not callable(callback):
                raise TypeError(f"{label} must be callable")
        if not isinstance(registry, (JamulusCompatibilityRegistry, type(None))):
            raise TypeError("registry must be a JamulusCompatibilityRegistry")
        if webjam_version is not None and not _VERSION_RE.fullmatch(
            str(webjam_version)
        ):
            raise ValueError("webjam_version must be a semantic version")
        if not 0 <= float(lock_timeout) <= 300:
            raise ValueError("lock_timeout must be between 0 and 300 seconds")

        self.registry = registry or official_jamulus_compatibility_registry()
        self.target = selected_target
        self.platform_name = normalized_platform
        self.webjam_version = (
            str(webjam_version) if webjam_version is not None else None
        )
        self._environ = dict(os.environ if environ is None else environ)
        self._version_probe = version_probe
        self._canonical_path_provider = canonical_path_provider
        self._path_trust_verifier = (
            path_trust_verifier or _verify_platform_install_path_trust
        )
        self._linux_package_verifier = (
            linux_package_verifier or _verify_linux_dpkg_install
        )
        self._windows_inventory_provider = (
            windows_inventory_provider or _approved_windows_runtime_inventory
        )
        base = (
            Path(root)
            if root is not None
            else default_component_store_root(
                platform_name=normalized_platform,
                environ=self._environ,
            )
        )
        self.root = base / "platform" / self.target.value
        self.state_path = self.root / "state.json"
        self.lock_path = self.root / ".installed.lock"
        self.lock_timeout = float(lock_timeout)
        self._ensure_root()

    def record_installed(
        self,
        client_entry: JamulusCompatibility,
        server_entry: JamulusCompatibility,
        busy_check: BusyCheck,
        *,
        authorization_check: PointOfUseAuthorization | None = None,
    ) -> PlatformInstallResult:
        """Record only a fully verified OS installation while audio is idle."""

        if not callable(busy_check):
            raise TypeError("busy_check must be callable")
        if authorization_check is not None and not callable(authorization_check):
            raise TypeError("authorization_check must be callable")
        client, server = self._require_pair(client_entry, server_entry)
        busy = _check_busy(busy_check)
        if busy is not None:
            raise JamulusPlatformInstallDeferred(busy)
        with InterProcessComponentLock(self.lock_path, timeout=self.lock_timeout):
            busy = _check_busy(busy_check)
            if busy is not None:
                raise JamulusPlatformInstallDeferred(busy)
            executable = self._discover_and_verify(client, server)
            if authorization_check is not None:
                authorization_check(client, server)
            self._write_receipt(client, server)
            return PlatformInstallResult(
                client=PlatformInstalledJamulus(
                    client, executable, trust_policy_verified=True
                ),
                server=PlatformInstalledJamulus(
                    server, executable, trust_policy_verified=True
                ),
            )

    def current(self, role: JamulusRole) -> PlatformInstalledJamulus | None:
        """Return one role only after a fresh receipt and executable proof."""

        try:
            selected_role = JamulusRole(role)
        except (TypeError, ValueError) as exc:
            raise JamulusPlatformError(
                "the installed Jamulus receipt role is invalid"
            ) from exc
        if selected_role not in {JamulusRole.CLIENT, JamulusRole.SERVER}:
            raise JamulusPlatformError(
                "the installed Jamulus receipt role is unsupported"
            )
        with InterProcessComponentLock(self.lock_path, timeout=self.lock_timeout):
            receipt = self._read_receipt()
            if receipt is None:
                return None
            client, server = receipt
            executable = self._discover_and_verify(client, server)
            entry = client if selected_role is JamulusRole.CLIENT else server
            return PlatformInstalledJamulus(
                entry, executable, trust_policy_verified=True
            )

    def _require_pair(
        self,
        client_entry: JamulusCompatibility,
        server_entry: JamulusCompatibility,
    ) -> tuple[JamulusCompatibility, JamulusCompatibility]:
        if not isinstance(client_entry, JamulusCompatibility) or not isinstance(
            server_entry, JamulusCompatibility
        ):
            raise JamulusPlatformError(
                "the installed Jamulus receipt entries are invalid"
            )
        try:
            client = self.registry.require_exact(client_entry)
            server = self.registry.require_exact(server_entry)
        except JamulusCompatibilityError as exc:
            raise JamulusPlatformError(
                "the installed Jamulus receipt is not in the current registry"
            ) from exc
        expected_kind = (
            ArtifactKind.INSTALLER
            if self.target is ComponentTarget.WINDOWS_X64
            else ArtifactKind.PACKAGE
        )
        if (
            client.component_id != "jamulus"
            or server.component_id != "jamulus"
            or client.role is not JamulusRole.CLIENT
            or server.role is not JamulusRole.SERVER
            or client.target is not self.target
            or server.target is not self.target
            or client.version != server.version
            or client.variant != server.variant
            or client.variant != "official"
            or client.artifact != server.artifact
            or client.artifact.kind is not expected_kind
            or server.artifact.kind is not expected_kind
            or client.activation_mode is not ActivationMode.PLATFORM_APPROVAL
            or server.activation_mode is not ActivationMode.PLATFORM_APPROVAL
            or client.source.provenance is not SourceProvenance.OFFICIAL_RELEASE
            or server.source.provenance is not SourceProvenance.OFFICIAL_RELEASE
            or not client.capabilities.includes(
                {"audio-client", "json-rpc-client"}
            )
            or not server.capabilities.includes(
                {"audio-server", "json-rpc-server"}
            )
            or (
                self.webjam_version is not None
                and (
                    not client.supports_webjam(self.webjam_version)
                    or not server.supports_webjam(self.webjam_version)
                )
            )
        ):
            raise JamulusPlatformError(
                "the installed Jamulus client/server pair is not approved"
            )
        client_runtime = _runtime_identity(client)
        server_runtime = _runtime_identity(server)
        expected_relative_path = (
            "Jamulus.exe"
            if self.target is ComponentTarget.WINDOWS_X64
            else "usr/bin/jamulus"
        )
        if (
            client.runtime_files != server.runtime_files
            or client_runtime != server_runtime
            or client_runtime.relative_path != expected_relative_path
            or client.executable_relative_path != expected_relative_path
            or server.executable_relative_path != expected_relative_path
        ):
            raise JamulusPlatformError(
                "the installed Jamulus runtime identity is not approved"
            )
        return client, server

    def _discover_and_verify(
        self,
        client: JamulusCompatibility,
        server: JamulusCompatibility,
    ) -> Path:
        # Revalidate the pair here as well so a caller replacing ``registry``
        # cannot make a stale in-memory entry authoritative.
        client, server = self._require_pair(client, server)
        candidates = self._canonical_candidates()
        for candidate in candidates:
            if not candidate.exists() and not candidate.is_symlink():
                continue
            try:
                self._verify_installation_trust(candidate, client)
                observed_version = self._probe_version(candidate)
                if observed_version != client.version:
                    raise JamulusPlatformError(
                        "the installed Jamulus version does not match the catalog"
                    )
                # The process probe may be slow. Re-prove every loadable byte,
                # package fact, and path boundary before publishing trust.
                self._verify_installation_trust(candidate, client)
            except JamulusPlatformError:
                # A discovered canonical installation that is malformed or
                # tampered is an integrity failure, not a benign absence.
                raise
            return candidate
        raise JamulusPlatformInstallationNotFound(
            "no exact catalog-approved Jamulus installation was found"
        )

    def _verify_installation_trust(
        self,
        candidate: Path,
        entry: JamulusCompatibility,
    ) -> None:
        try:
            self._path_trust_verifier(candidate, self.target)
        except JamulusPlatformError:
            raise
        except Exception as exc:
            raise JamulusPlatformError(
                "the installed Jamulus path trust could not be verified"
            ) from exc
        if self.target is ComponentTarget.WINDOWS_X64:
            try:
                inventory = tuple(self._windows_inventory_provider(entry))
            except JamulusPlatformError:
                raise
            except Exception as exc:
                raise JamulusPlatformError(
                    "the installed Jamulus module inventory is unavailable"
                ) from exc
            _verify_windows_runtime_inventory(
                candidate.parent,
                executable=candidate,
                inventory=inventory,
            )
            return
        runtime = _runtime_identity(entry)
        _verify_runtime_file(
            candidate,
            identity=runtime,
            target=ComponentTarget.LINUX_X64,
        )
        try:
            self._linux_package_verifier(candidate, entry.version)
        except JamulusPlatformError:
            raise
        except Exception as exc:
            raise JamulusPlatformError(
                "the installed Jamulus package trust could not be verified"
            ) from exc

    def _probe_version(self, candidate: Path) -> str:
        try:
            if self._version_probe is default_jamulus_version_probe:
                return _sanitized_installed_version_probe(
                    candidate,
                    platform_name=self.platform_name,
                )
            return str(self._version_probe(str(candidate)) or "").strip()
        except JamulusPlatformError:
            raise
        except Exception as exc:
            raise JamulusPlatformError(
                "the installed Jamulus version could not be verified"
            ) from exc

    def _canonical_candidates(self) -> tuple[Path, ...]:
        if self._canonical_path_provider is not None:
            try:
                supplied = tuple(self._canonical_path_provider())
            except Exception as exc:
                raise JamulusPlatformError(
                    "the Jamulus installation paths could not be discovered"
                ) from exc
            if len(supplied) > 4:
                raise JamulusPlatformError(
                    "too many Jamulus installation paths were supplied"
                )
            candidates = tuple(Path(item) for item in supplied)
        else:
            candidates = _canonical_installed_jamulus_paths(
                self.platform_name, self._environ
            )
        expected_name = (
            "Jamulus.exe" if self.target is ComponentTarget.WINDOWS_X64 else "jamulus"
        )
        observed: set[str] = set()
        result: list[Path] = []
        for candidate in candidates:
            if not candidate.is_absolute() or candidate.name != expected_name:
                raise JamulusPlatformError(
                    "a Jamulus installation path is not canonical"
                )
            key = str(candidate).casefold()
            if key in observed:
                continue
            observed.add(key)
            result.append(candidate)
        return tuple(result)

    def _read_receipt(
        self,
    ) -> tuple[JamulusCompatibility, JamulusCompatibility] | None:
        if not self.state_path.exists() and not self.state_path.is_symlink():
            return None
        if os.name == "posix":
            try:
                mode = stat.S_IMODE(self.state_path.lstat().st_mode)
            except OSError as exc:
                raise JamulusPlatformError(
                    "the installed Jamulus receipt is unavailable"
                ) from exc
            if mode & 0o077:
                raise JamulusPlatformError(
                    "the installed Jamulus receipt is not private"
                )
        value = _read_json(self.state_path)
        if not isinstance(value, dict) or frozenset(value) != frozenset(
            {"schema", "target", "client", "server"}
        ):
            raise JamulusPlatformError("the installed Jamulus receipt is invalid")
        if (
            value["schema"] != PLATFORM_INSTALLED_STATE_SCHEMA
            or value["target"] != self.target.value
        ):
            raise JamulusPlatformError("the installed Jamulus receipt is unsupported")
        try:
            client = JamulusCompatibility.from_dict(value["client"])
            server = JamulusCompatibility.from_dict(value["server"])
        except (JamulusCompatibilityError, TypeError, ValueError) as exc:
            raise JamulusPlatformError(
                "the installed Jamulus receipt entries are invalid"
            ) from exc
        return self._require_pair(client, server)

    def _write_receipt(
        self,
        client: JamulusCompatibility,
        server: JamulusCompatibility,
    ) -> None:
        payload = {
            "schema": PLATFORM_INSTALLED_STATE_SCHEMA,
            "target": self.target.value,
            "client": client.to_dict(),
            "server": server.to_dict(),
        }
        try:
            atomic_write_text(
                self.state_path,
                json.dumps(
                    payload,
                    ensure_ascii=True,
                    allow_nan=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n",
                mode=0o600,
            )
        except OSError as exc:
            raise JamulusPlatformError(
                "the installed Jamulus receipt could not be saved"
            ) from exc

    def _ensure_root(self) -> None:
        if self.root.exists() and self.root.is_symlink():
            raise JamulusPlatformError(
                "the installed Jamulus receipt root cannot be a symlink"
            )
        try:
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            if not self.root.is_dir():
                raise JamulusPlatformError(
                    "the installed Jamulus receipt root is unavailable"
                )
            if os.name == "posix":
                os.chmod(self.root, 0o700)
        except OSError as exc:
            raise JamulusPlatformError(
                "the installed Jamulus receipt root is unavailable"
            ) from exc


def default_macos_target() -> ComponentTarget:
    machine = platform.machine().strip().lower()
    if machine in {"arm64", "aarch64"}:
        return ComponentTarget.MACOS_ARM64
    if machine in {"x86_64", "amd64"}:
        return ComponentTarget.MACOS_X64
    raise JamulusPlatformError("this Mac architecture is not supported")


def platform_component_target(
    *,
    platform_name: str | None = None,
    machine: str | None = None,
) -> ComponentTarget:
    platform_value = (platform_name or sys.platform).strip().lower()
    machine_value = (machine or platform.machine()).strip().lower()
    if platform_value == "darwin":
        if machine_value in {"arm64", "aarch64"}:
            return ComponentTarget.MACOS_ARM64
        if machine_value in {"x86_64", "amd64"}:
            return ComponentTarget.MACOS_X64
    elif platform_value == "win32" and machine_value in {
        "amd64",
        "x86_64",
    }:
        return ComponentTarget.WINDOWS_X64
    elif platform_value.startswith("linux") and machine_value in {
        "amd64",
        "x86_64",
    }:
        return ComponentTarget.LINUX_X64
    raise JamulusPlatformError("this operating system or architecture is unsupported")


def open_platform_jamulus_installer(
    path: str | Path,
    entry: JamulusCompatibility,
    *,
    platform_name: str | None = None,
    startfile: object | None = None,
    command_runner: CommandRunner = _run_command,
) -> bool:
    """Hand one exact installer to the operating system after user approval.

    The update coordinator re-verifies the signed size and SHA-256 immediately
    before calling this boundary.  This function independently checks the
    target, artifact kind, filename, and regular-file identity.  It never uses
    a command shell, invokes ``sudo``, or performs a silent installation.
    """

    if not isinstance(entry, JamulusCompatibility):
        raise JamulusPlatformError("the Jamulus installer approval record is invalid")
    approved = entry
    if approved.role is not JamulusRole.CLIENT:
        raise JamulusPlatformError(
            "only the approved Jamulus client installer can be opened"
        )
    if (
        approved.activation_mode is not ActivationMode.PLATFORM_APPROVAL
        or approved.source.provenance is not SourceProvenance.OFFICIAL_RELEASE
    ):
        raise JamulusPlatformError(
            "the Jamulus installer is not an approved official release"
        )
    candidate = Path(path)
    try:
        details = candidate.lstat()
    except OSError as exc:
        raise JamulusPlatformError(
            "the verified Jamulus installer is unavailable"
        ) from exc
    if (
        stat.S_ISLNK(details.st_mode)
        or not stat.S_ISREG(details.st_mode)
        or not candidate.is_absolute()
        or candidate.name != approved.artifact.filename
        or details.st_size != approved.artifact.size
    ):
        raise JamulusPlatformError(
            "the Jamulus installer identity changed before approval"
        )
    verify_downloaded_file(candidate, approved.artifact)

    platform_value = (platform_name or sys.platform).strip().lower()
    if platform_value == "win32":
        if (
            approved.target is not ComponentTarget.WINDOWS_X64
            or approved.artifact.kind is not ArtifactKind.INSTALLER
        ):
            raise JamulusPlatformError(
                "the Jamulus installer is not approved for Windows"
            )
        launch = startfile if startfile is not None else getattr(os, "startfile", None)
        if not callable(launch):
            raise JamulusPlatformError(
                "the Windows installer approval service is unavailable"
            )
        try:
            launch(str(candidate))  # type: ignore[operator]
        except OSError as exc:
            raise JamulusPlatformError(
                "Windows could not open the Jamulus installer"
            ) from exc
        return True

    if platform_value.startswith("linux"):
        if (
            approved.target is not ComponentTarget.LINUX_X64
            or approved.artifact.kind is not ArtifactKind.PACKAGE
        ):
            raise JamulusPlatformError("the Jamulus package is not approved for Linux")
        result = command_runner(
            ["/usr/bin/xdg-open", str(candidate)],
            timeout=30.0,
        )
        if result.returncode != 0:
            raise JamulusPlatformError(
                "Linux could not open the Jamulus package approval"
            )
        return True

    raise JamulusPlatformError(
        "this platform does not use an external Jamulus installer handoff"
    )


def _runtime_identity(entry: JamulusCompatibility) -> RuntimeFileIdentity:
    matches = tuple(
        item
        for item in entry.runtime_files
        if item.executable
        and item.relative_path == entry.executable_relative_path
    )
    if len(matches) != 1:
        raise JamulusPlatformError(
            "the installed Jamulus runtime inventory is not exact"
        )
    return matches[0]


def _approved_windows_runtime_inventory(
    entry: JamulusCompatibility,
) -> tuple[RuntimeFileIdentity, ...]:
    if (
        entry.target is not ComponentTarget.WINDOWS_X64
        or entry.version != "3.12.3"
        or entry.variant != "official"
    ):
        raise JamulusPlatformError(
            "the Windows Jamulus loadable-module inventory is not approved"
        )
    approved = official_jamulus_compatibility_registry().exact(
        component_id=entry.component_id,
        role=entry.role,
        target=entry.target,
        version=entry.version,
        variant=entry.variant,
    )
    if approved != entry:
        raise JamulusPlatformError(
            "the Windows Jamulus loadable-module inventory is not approved"
        )
    inventory = tuple(entry.runtime_files)
    if len(inventory) != 27:
        raise JamulusPlatformError(
            "the Windows Jamulus loadable-module inventory is incomplete"
        )
    executable = _runtime_identity(entry)
    described = tuple(
        item
        for item in inventory
        if item.relative_path.casefold()
        == entry.executable_relative_path.casefold()
    )
    if len(described) != 1 or described[0] != executable:
        raise JamulusPlatformError(
            "the Windows Jamulus executable identity does not match its module inventory"
        )
    return inventory


def _verify_windows_runtime_inventory(
    install_root: Path,
    *,
    executable: Path,
    inventory: tuple[RuntimeFileIdentity, ...],
) -> None:
    try:
        root_details = install_root.lstat()
    except OSError as exc:
        raise JamulusPlatformError(
            "the installed Jamulus directory is unavailable"
        ) from exc
    if (
        not stat.S_ISDIR(root_details.st_mode)
        or stat.S_ISLNK(root_details.st_mode)
        or _is_windows_reparse(root_details)
    ):
        raise JamulusPlatformError(
            "the installed Jamulus directory is not trusted"
        )
    if not inventory or len(inventory) > 128:
        raise JamulusPlatformError(
            "the installed Jamulus module inventory is invalid"
        )
    expected: dict[str, RuntimeFileIdentity] = {}
    for identity in inventory:
        key = identity.relative_path.casefold()
        if key in expected:
            raise JamulusPlatformError(
                "the installed Jamulus module inventory is ambiguous"
            )
        expected[key] = identity
    executable_key = executable.name.casefold()
    if executable_key not in expected:
        raise JamulusPlatformError(
            "the installed Jamulus executable is missing from its module inventory"
        )

    observed_loadables: set[str] = set()
    visited = 0
    try:
        for directory, names, files in os.walk(
            install_root,
            followlinks=False,
            onerror=_raise_windows_inventory_walk_error,
        ):
            directory_path = Path(directory)
            for name in tuple(names):
                child = directory_path / name
                details = child.lstat()
                if stat.S_ISLNK(details.st_mode) or _is_windows_reparse(details):
                    raise JamulusPlatformError(
                        "the installed Jamulus directory contains a link or reparse point"
                    )
            for name in files:
                visited += 1
                if visited > 512:
                    raise JamulusPlatformError(
                        "the installed Jamulus directory inventory is too large"
                    )
                child = directory_path / name
                details = child.lstat()
                if stat.S_ISLNK(details.st_mode) or _is_windows_reparse(details):
                    raise JamulusPlatformError(
                        "the installed Jamulus directory contains a link or reparse point"
                    )
                relative = child.relative_to(install_root).as_posix()
                if (
                    child.suffix.casefold() in _WINDOWS_LOADABLE_SUFFIXES
                    or relative.casefold() == executable_key
                ):
                    observed_loadables.add(relative.casefold())
    except JamulusPlatformError:
        raise
    except OSError as exc:
        raise JamulusPlatformError(
            "the installed Jamulus directory inventory is unavailable"
        ) from exc
    if observed_loadables != frozenset(expected):
        raise JamulusPlatformError(
            "the installed Jamulus loadable-module inventory is unexpected"
        )
    for key, identity in expected.items():
        module = install_root.joinpath(*identity.relative_path.split("/"))
        _verify_runtime_file(
            module,
            identity=identity,
            target=ComponentTarget.WINDOWS_X64,
        )


def _raise_windows_inventory_walk_error(error: OSError) -> None:
    raise JamulusPlatformError(
        "the installed Jamulus directory inventory is unavailable"
    ) from error


def _verify_platform_install_path_trust(
    path: Path,
    target: ComponentTarget,
) -> None:
    if target is ComponentTarget.LINUX_X64:
        _verify_linux_root_owned_path(path)
        return
    if target is not ComponentTarget.WINDOWS_X64 or os.name != "nt":
        raise JamulusPlatformError(
            "the installed Jamulus platform path cannot be trusted on this host"
        )
    expected = _windows_program_files_x64() / "Jamulus" / "Jamulus.exe"
    if _normalized_windows_path(path) != _normalized_windows_path(expected):
        raise JamulusPlatformError(
            "the installed Jamulus path is outside the x64 Program Files location"
        )
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise JamulusPlatformError(
            "the installed Jamulus path could not be resolved"
        ) from exc
    if _normalized_windows_path(resolved) != _normalized_windows_path(path):
        raise JamulusPlatformError(
            "the installed Jamulus path contains a link or reparse point"
        )
    for candidate in (expected.parent.parent, expected.parent, expected):
        details = candidate.lstat()
        if stat.S_ISLNK(details.st_mode) or _is_windows_reparse(details):
            raise JamulusPlatformError(
                "the installed Jamulus path contains a link or reparse point"
            )
    if os.access(expected.parent, os.W_OK) or os.access(expected, os.W_OK):
        raise JamulusPlatformError(
            "the installed Jamulus path is writable by the current process"
        )


def _verify_linux_root_owned_path(path: Path) -> None:
    expected = Path("/usr/bin/jamulus")
    if path != expected:
        raise JamulusPlatformError(
            "the installed Jamulus path is not the canonical Linux package path"
        )
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise JamulusPlatformError(
            "the installed Jamulus path could not be resolved"
        ) from exc
    if resolved != expected:
        raise JamulusPlatformError(
            "the installed Jamulus path contains a link"
        )
    for candidate, kind in (
        (Path("/"), "directory"),
        (Path("/usr"), "directory"),
        (Path("/usr/bin"), "directory"),
        (expected, "file"),
    ):
        try:
            details = candidate.lstat()
        except OSError as exc:
            raise JamulusPlatformError(
                "the installed Jamulus ownership chain is unavailable"
            ) from exc
        if (
            details.st_uid != 0
            or details.st_mode & 0o022
            or stat.S_ISLNK(details.st_mode)
            or (
                kind == "directory"
                and not stat.S_ISDIR(details.st_mode)
            )
            or (kind == "file" and not stat.S_ISREG(details.st_mode))
        ):
            raise JamulusPlatformError(
                "the installed Jamulus ownership chain is not trusted"
            )


def _verify_linux_dpkg_install(path: Path, version: str) -> None:
    query = Path("/usr/bin/dpkg-query")
    _verify_linux_system_tool(query)
    environment = {
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": "/usr/bin:/bin",
    }
    try:
        owner = subprocess.run(
            [str(query), "-S", str(path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10.0,
            check=False,
            shell=False,
            env=environment,
        )
        status = subprocess.run(
            [
                str(query),
                "-W",
                "-f=${db:Status-Abbrev}\\t${Version}\\t${Architecture}\\n",
                "jamulus",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10.0,
            check=False,
            shell=False,
            env=environment,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise JamulusPlatformError(
            "the installed Jamulus Debian package could not be verified"
        ) from exc
    owner_text = _bounded_command_text(owner.stdout)
    status_text = _bounded_command_text(status.stdout)
    if (
        owner.returncode != 0
        or owner_text != f"jamulus: {path}"
        or status.returncode != 0
        or status_text != f"ii \t{version}\tamd64"
    ):
        raise JamulusPlatformError(
            "the installed Jamulus Debian package ownership is not approved"
        )


def _verify_linux_system_tool(path: Path) -> None:
    try:
        details = path.lstat()
    except OSError as exc:
        raise JamulusPlatformError(
            "the Debian package verification tool is unavailable"
        ) from exc
    if (
        stat.S_ISLNK(details.st_mode)
        or not stat.S_ISREG(details.st_mode)
        or details.st_uid != 0
        or details.st_mode & 0o022
        or details.st_mode & 0o111 == 0
    ):
        raise JamulusPlatformError(
            "the Debian package verification tool is not trusted"
        )


def _sanitized_installed_version_probe(
    path: Path,
    *,
    platform_name: str,
) -> str:
    environment = _sanitized_loader_environment(
        os.environ,
        platform_name=platform_name,
        executable=path,
    )
    try:
        completed = subprocess.run(
            [str(path), "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=8.0,
            check=False,
            shell=False,
            env=environment,
            cwd=str(path.parent if platform_name == "win32" else Path("/")),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise JamulusPlatformError(
            "the installed Jamulus version could not be verified"
        ) from exc
    output = (
        _bounded_command_text(completed.stdout)
        + "\n"
        + _bounded_command_text(completed.stderr)
    )
    matched = re.search(
        r"(?:version\s+)?(\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?)",
        output,
        flags=re.IGNORECASE,
    )
    return matched.group(1) if matched else "unverified"


def _sanitized_loader_environment(
    environ: Mapping[str, str],
    *,
    platform_name: str,
    executable: Path,
) -> dict[str, str]:
    try:
        return _core_child_environment(
            environ,
            platform_name=platform_name,
            executable=executable,
        )
    except JamulusChildEnvironmentError as exc:
        raise JamulusPlatformError(str(exc)) from None


def sanitized_jamulus_child_environment(
    environ: Mapping[str, str],
    *,
    platform_name: str,
    executable: str | Path,
) -> dict[str, str]:
    """Return a bounded native-child environment with injection paths removed.

    Every Jamulus role (client, server, practice, and Reference Track) must use
    this same boundary. Callers may add one reviewed literal logging rule
    afterwards; inherited DYLD/LD/QML/Qt loader and plugin controls never
    survive.
    """

    return _sanitized_loader_environment(
        environ,
        platform_name=platform_name,
        executable=Path(executable),
    )


def _windows_program_files_x64() -> Path:
    if os.name != "nt":
        raise JamulusPlatformError(
            "the x64 Program Files location is unavailable on this host"
        )
    try:
        import ctypes
        from ctypes import wintypes

        class GUID(ctypes.Structure):
            _fields_ = (
                ("Data1", wintypes.DWORD),
                ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8),
            )

        folder_id = GUID(
            0x6D809377,
            0x6AF0,
            0x444B,
            (ctypes.c_ubyte * 8)(
                0x89, 0x57, 0xA3, 0x77, 0x3F, 0x02, 0x20, 0x0E
            ),
        )
        pointer = ctypes.c_wchar_p()
        result = ctypes.windll.shell32.SHGetKnownFolderPath(
            ctypes.byref(folder_id),
            0,
            None,
            ctypes.byref(pointer),
        )
        if result != 0 or not pointer.value:
            raise OSError("SHGetKnownFolderPath failed")
        try:
            return Path(pointer.value)
        finally:
            ctypes.windll.ole32.CoTaskMemFree(pointer)
    except Exception as exc:
        raise JamulusPlatformError(
            "the x64 Program Files location could not be verified"
        ) from exc


def _normalized_windows_path(path: Path) -> str:
    return os.path.normcase(os.path.abspath(str(path))).rstrip("\\/")


def _is_windows_reparse(details: os.stat_result) -> bool:
    return bool(
        getattr(details, "st_file_attributes", 0)
        & _WINDOWS_REPARSE_POINT
    )


def _bounded_command_text(raw: bytes) -> str:
    if not isinstance(raw, bytes) or len(raw) > _MAX_METADATA_BYTES:
        raise JamulusPlatformError("platform verification output was invalid")
    return raw.decode("utf-8", errors="strict").strip()


def _canonical_installed_jamulus_paths(
    platform_name: str,
    environ: Mapping[str, str],
) -> tuple[Path, ...]:
    if platform_name == "linux":
        return (Path("/usr/bin/jamulus"),)
    if platform_name != "win32":
        raise JamulusPlatformError(
            "this platform has no approved Jamulus installation paths"
        )
    if os.name == "nt":
        return (
            _windows_program_files_x64()
            / "Jamulus"
            / "Jamulus.exe",
        )
    folded = {
        str(key).casefold(): str(value or "").strip() for key, value in environ.items()
    }
    # Non-Windows hosts use this branch only for deterministic contract tests.
    # The x86 Program Files tree is intentionally not a fallback for the x64
    # target; the official installer removes old x86 installations.
    raw = folded.get("programfiles", "")
    if not raw:
        return ()
    base = Path(raw)
    if not base.is_absolute():
        return ()
    return (base / "Jamulus" / "Jamulus.exe",)


def _verify_runtime_file(
    path: Path,
    *,
    identity: RuntimeFileIdentity,
    target: ComponentTarget,
) -> tuple[int, int, int, int]:
    try:
        path_details = path.lstat()
    except OSError as exc:
        raise JamulusPlatformError(
            "the installed Jamulus executable is unavailable"
        ) from exc
    if stat.S_ISLNK(path_details.st_mode) or not stat.S_ISREG(path_details.st_mode):
        raise JamulusPlatformError(
            "the installed Jamulus executable is not a regular file"
        )
    if target is ComponentTarget.LINUX_X64 and path_details.st_mode & 0o111 == 0:
        raise JamulusPlatformError("the installed Jamulus executable is not executable")
    if path_details.st_size != identity.size:
        raise JamulusPlatformError(
            "the installed Jamulus executable size does not match the catalog"
        )

    flags = os.O_RDONLY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_BINARY", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise JamulusPlatformError(
            "the installed Jamulus executable could not be opened safely"
        ) from exc
    try:
        with os.fdopen(descriptor, "rb", closefd=True) as handle:
            descriptor = -1
            opened = os.fstat(handle.fileno())
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_size != identity.size
                or (target is ComponentTarget.LINUX_X64 and opened.st_mode & 0o111 == 0)
            ):
                raise JamulusPlatformError(
                    "the installed Jamulus executable changed before verification"
                )
            if (
                path_details.st_dev,
                path_details.st_ino,
                path_details.st_size,
            ) != (opened.st_dev, opened.st_ino, opened.st_size):
                raise JamulusPlatformError(
                    "the installed Jamulus executable changed before verification"
                )
            _verify_runtime_architecture(handle, target=target, size=identity.size)
            handle.seek(0)
            digest = hashlib.sha256()
            total = 0
            while True:
                chunk = handle.read(_RUNTIME_HASH_CHUNK_BYTES)
                if not chunk:
                    break
                total += len(chunk)
                if total > identity.size:
                    raise JamulusPlatformError(
                        "the installed Jamulus executable grew during verification"
                    )
                digest.update(chunk)
            closed_details = os.fstat(handle.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if total != identity.size or digest.hexdigest() != identity.sha256:
        raise JamulusPlatformError(
            "the installed Jamulus executable does not match the catalog"
        )
    if (
        opened.st_dev,
        opened.st_ino,
        opened.st_size,
        getattr(opened, "st_mtime_ns", int(opened.st_mtime * 1_000_000_000)),
    ) != (
        closed_details.st_dev,
        closed_details.st_ino,
        closed_details.st_size,
        getattr(
            closed_details,
            "st_mtime_ns",
            int(closed_details.st_mtime * 1_000_000_000),
        ),
    ):
        raise JamulusPlatformError(
            "the installed Jamulus executable changed during verification"
        )
    try:
        final_details = path.lstat()
    except OSError as exc:
        raise JamulusPlatformError(
            "the installed Jamulus executable changed during verification"
        ) from exc
    if (
        stat.S_ISLNK(final_details.st_mode)
        or not stat.S_ISREG(final_details.st_mode)
        or (
            final_details.st_dev,
            final_details.st_ino,
            final_details.st_size,
        )
        != (opened.st_dev, opened.st_ino, opened.st_size)
    ):
        raise JamulusPlatformError(
            "the installed Jamulus executable changed during verification"
        )
    return (
        opened.st_dev,
        opened.st_ino,
        opened.st_size,
        getattr(opened, "st_mtime_ns", int(opened.st_mtime * 1_000_000_000)),
    )


def _verify_runtime_architecture(
    handle,
    *,
    target: ComponentTarget,
    size: int,
) -> None:
    handle.seek(0)
    if target is ComponentTarget.WINDOWS_X64:
        dos_header = handle.read(64)
        if len(dos_header) != 64 or dos_header[:2] != b"MZ":
            raise JamulusPlatformError(
                "the installed Jamulus executable is not a PE image"
            )
        pe_offset = struct.unpack_from("<I", dos_header, 0x3C)[0]
        if pe_offset < 64 or pe_offset > size - 6 or pe_offset > _MAX_METADATA_BYTES:
            raise JamulusPlatformError("the installed Jamulus PE header is invalid")
        handle.seek(pe_offset)
        if handle.read(4) != b"PE\0\0":
            raise JamulusPlatformError("the installed Jamulus PE signature is invalid")
        machine_raw = handle.read(2)
        if (
            len(machine_raw) != 2
            or struct.unpack("<H", machine_raw)[0]
            != _WINDOWS_PE_X86_64_MACHINE
        ):
            raise JamulusPlatformError(
                "the installed Jamulus PE architecture is not approved"
            )
        return
    if target is ComponentTarget.LINUX_X64:
        elf_header = handle.read(20)
        if (
            len(elf_header) != 20
            or elf_header[:4] != b"\x7fELF"
            or elf_header[4] != 2
            or elf_header[5] != 1
            or elf_header[6] != 1
            or struct.unpack_from("<H", elf_header, 18)[0] != _LINUX_ELF_X86_64_MACHINE
        ):
            raise JamulusPlatformError(
                "the installed Jamulus ELF architecture is not approved"
            )
        return
    raise JamulusPlatformError("the installed Jamulus binary target is unsupported")


def _check_busy(callback: BusyCheck) -> ComponentBusyStatus | None:
    try:
        result = callback()
    except Exception as exc:
        raise JamulusPlatformError(
            "Jamulus installation could not prove the session is idle"
        ) from exc
    if result is not None and not isinstance(result, ComponentBusyStatus):
        raise JamulusPlatformError(
            "Jamulus installation received an invalid busy-state result"
        )
    return result


def _verify_bundle_symlinks(bundle: Path) -> None:
    root = bundle.resolve(strict=True)
    count = 0
    for directory, child_directories, files in os.walk(
        bundle, topdown=True, followlinks=False
    ):
        for name in (*child_directories, *files):
            count += 1
            if count > 8192:
                raise JamulusPlatformError(
                    "the Jamulus app bundle contains too many entries"
                )
            path = Path(directory) / name
            if not path.is_symlink():
                continue
            try:
                target = path.resolve(strict=True)
                target.relative_to(root)
            except (OSError, ValueError) as exc:
                raise JamulusPlatformError(
                    "the Jamulus app bundle contains an escaping symlink"
                ) from exc


def _bounded_output(
    result: subprocess.CompletedProcess[bytes],
    *,
    maximum: int,
) -> str:
    raw = bytes(result.stdout or b"") + b"\n" + bytes(result.stderr or b"")
    if len(raw) > maximum:
        raise JamulusPlatformError("platform verification output was too large")
    return raw.decode("utf-8", errors="replace")


def _codesign_entitlements(
    result: subprocess.CompletedProcess[bytes],
) -> dict[str, object]:
    """Extract one bounded XML entitlement dictionary from ``codesign``."""

    raw = bytes(result.stdout or b"") + b"\n" + bytes(result.stderr or b"")
    if len(raw) > 64 * 1024:
        raise JamulusPlatformError(
            "the Jamulus signed entitlement output was too large"
        )
    starts = tuple(
        index
        for marker in (b"<?xml", b"<plist")
        if (index := raw.find(marker)) >= 0
    )
    end = raw.rfind(b"</plist>")
    if not starts or end < 0:
        raise JamulusPlatformError(
            "the Jamulus signed entitlements were unavailable"
        )
    payload = raw[min(starts) : end + len(b"</plist>")]
    try:
        value = plistlib.loads(payload)
    except plistlib.InvalidFileException as exc:
        raise JamulusPlatformError(
            "the Jamulus signed entitlements were malformed"
        ) from exc
    if not isinstance(value, dict) or not all(
        isinstance(key, str) for key in value
    ):
        raise JamulusPlatformError(
            "the Jamulus signed entitlements were invalid"
        )
    return value


def _read_json(path: Path) -> object:
    try:
        details = path.lstat()
        if (
            stat.S_ISLNK(details.st_mode)
            or not stat.S_ISREG(details.st_mode)
            or details.st_size <= 0
            or details.st_size > _MAX_METADATA_BYTES
        ):
            raise JamulusPlatformError("Jamulus platform metadata is invalid")
        raw = path.read_bytes()
    except OSError as exc:
        raise JamulusPlatformError("Jamulus platform metadata is unavailable") from exc

    def pairs(values: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in values:
            if key in result:
                raise JamulusPlatformError(
                    "Jamulus platform metadata has duplicate fields"
                )
            result[key] = value
        return result

    try:
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=pairs,
            parse_float=lambda _value: (_ for _ in ()).throw(
                JamulusPlatformError(
                    "Jamulus platform metadata cannot contain floating-point values"
                )
            ),
            parse_constant=lambda _value: (_ for _ in ()).throw(
                JamulusPlatformError(
                    "Jamulus platform metadata cannot contain non-finite values"
                )
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise JamulusPlatformError("Jamulus platform metadata is malformed") from exc


def _remove_owned_tree(path: Path, *, root: Path) -> None:
    if path.is_symlink():
        raise JamulusPlatformError(
            "refusing to remove a symlinked Jamulus component tree"
        )
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise JamulusPlatformError(
            "refusing to remove a Jamulus tree outside its component store"
        ) from exc
    shutil.rmtree(path)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


__all__ = [
    "JamulusLicenseApprovalRequired",
    "JamulusPlatformError",
    "JamulusPlatformInstallDeferred",
    "JamulusPlatformInstallationNotFound",
    "MACOS_JAMULUS_TEAM_ID",
    "MACOS_INTEGRATED_RUNTIME_CAPABILITY",
    "MACOS_INTEGRATED_RUNTIME_VARIANT",
    "MACOS_INTEGRATED_RUNTIME_VERIFIER_ENABLED",
    "MacOSBundleVerifier",
    "MacOSExecutionContract",
    "MacOSExecutionContractKind",
    "MacOSInstallResult",
    "MacOSInstalledJamulus",
    "MacOSJamulusComponentStore",
    "PlatformInstallResult",
    "PlatformInstalledJamulus",
    "PlatformInstalledJamulusStore",
    "VerifiedMacBundle",
    "default_macos_target",
    "open_platform_jamulus_installer",
    "macos_integrated_runtime_contract_allows",
    "macos_integrated_runtime_entry_is_eligible",
    "platform_component_target",
    "sanitized_jamulus_child_environment",
]
