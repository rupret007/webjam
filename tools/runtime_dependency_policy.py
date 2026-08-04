"""Validate and render WebJam's frozen Python runtime dependency inventory.

The native release lock files contain both libraries shipped in the frozen
application and packages used only while PyInstaller builds it.  This tool
keeps that boundary explicit, rejects unreviewed lock drift, and produces the
human- and machine-readable inventories included in every native package.

Only the Python standard library is used so the policy can run before the
application dependencies are installed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY = ROOT / "packaging" / "runtime-dependency-policy.json"
DEFAULT_LOCK_ROOT = ROOT / "requirements-lock"
DEFAULT_NOTICE = ROOT / "THIRD_PARTY_NOTICES_RUNTIME.md"
DEFAULT_SBOM = ROOT / "packaging" / "WebJam-runtime-sbom.cdx.json"
DEFAULT_VERSION_SOURCE = ROOT / "webjam_qt" / "__init__.py"

_LOCK_LINE = re.compile(
    r"^(?P<name>[A-Za-z0-9_.-]+)==(?P<version>[A-Za-z0-9_.+!-]+)\s*\\?$"
)
_SPDX_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9.+-]*")
_VALID_SCOPES = frozenset({"runtime", "build", "excluded"})
_VERSION_LINE = re.compile(
    r'^__version__ = "(?P<version>[0-9]+\.[0-9]+\.[0-9]+)"$',
    re.MULTILINE,
)


class PolicyError(ValueError):
    """Raised when reviewed dependency policy and release locks diverge."""


@dataclass(frozen=True)
class LockedPackage:
    """One normalized distribution and its target-specific locked versions."""

    name: str
    versions: tuple[tuple[str, str], ...]

    @property
    def targets(self) -> tuple[str, ...]:
        return tuple(target for target, _version in self.versions)


@dataclass(frozen=True)
class RuntimeInventory:
    """Validated immutable inputs used by both generated artifacts."""

    policy: dict[str, Any]
    packages: tuple[LockedPackage, ...]


def normalize_distribution_name(value: str) -> str:
    """Return the PEP 503 normalized form used as the policy key."""

    return re.sub(r"[-_.]+", "-", value).lower()


def _load_json(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PolicyError(f"cannot read dependency policy: {exc}") from exc
    if not isinstance(raw, dict):
        raise PolicyError("dependency policy root must be an object")
    return raw


def application_version(path: Path = DEFAULT_VERSION_SOURCE) -> str:
    """Read the single authoritative desktop version without importing Qt."""

    try:
        source = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PolicyError("cannot read the WebJam version source") from exc
    match = _VERSION_LINE.search(source)
    if match is None:
        raise PolicyError("WebJam version source has no strict semantic version")
    return match.group("version")


def parse_release_locks(
    lock_root: Path,
    targets: tuple[str, ...],
) -> tuple[LockedPackage, ...]:
    """Parse exact distribution versions from every reviewed release lock."""

    versions_by_name: dict[str, dict[str, str]] = {}
    for target in targets:
        path = lock_root / f"{target}.txt"
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise PolicyError(f"cannot read release lock for {target}: {exc}") from exc
        seen_in_target: set[str] = set()
        for line in lines:
            match = _LOCK_LINE.fullmatch(line)
            if match is None:
                continue
            name = normalize_distribution_name(match.group("name"))
            if name in seen_in_target:
                raise PolicyError(f"duplicate locked distribution {name!r} for {target}")
            seen_in_target.add(name)
            versions_by_name.setdefault(name, {})[target] = match.group("version")
        if not seen_in_target:
            raise PolicyError(f"release lock for {target} contains no exact pins")

    return tuple(
        LockedPackage(
            name=name,
            versions=tuple(
                (target, versions[target])
                for target in targets
                if target in versions
            ),
        )
        for name, versions in sorted(versions_by_name.items())
    )


def _license_ids(expression: str) -> frozenset[str]:
    tokens = {
        token
        for token in _SPDX_TOKEN.findall(expression)
        if token not in {"AND", "OR", "WITH"}
    }
    if not tokens:
        raise PolicyError("license expression contains no SPDX identifiers")
    return frozenset(tokens)


def _validate_runtime_license(
    *,
    component: str,
    expression: str,
    allowed_ids: frozenset[str],
) -> None:
    identifiers = _license_ids(expression)
    forbidden = sorted(
        item
        for item in identifiers
        if item.startswith("GPL-") or item.startswith("AGPL-")
    )
    if forbidden:
        raise PolicyError(
            f"runtime component {component!r} selects forbidden copyleft "
            f"license(s): {', '.join(forbidden)}"
        )
    unknown = sorted(identifiers - allowed_ids)
    if unknown:
        raise PolicyError(
            f"runtime component {component!r} uses unreviewed license "
            f"identifier(s): {', '.join(unknown)}"
        )


def validate_policy(
    policy_path: Path = DEFAULT_POLICY,
    lock_root: Path = DEFAULT_LOCK_ROOT,
) -> RuntimeInventory:
    """Fail closed unless policy exactly covers every target release lock."""

    policy = _load_json(policy_path)
    if policy.get("schema_version") != 1:
        raise PolicyError("dependency policy schema_version must be 1")

    targets_raw = policy.get("targets")
    if (
        not isinstance(targets_raw, list)
        or not targets_raw
        or any(not isinstance(item, str) or not item for item in targets_raw)
        or len(set(targets_raw)) != len(targets_raw)
    ):
        raise PolicyError("dependency policy targets must be unique non-empty strings")
    targets = tuple(targets_raw)
    packages = parse_release_locks(lock_root, targets)

    entries = policy.get("packages")
    if not isinstance(entries, dict):
        raise PolicyError("dependency policy packages must be an object")
    normalized_entries = {
        normalize_distribution_name(str(name)): value for name, value in entries.items()
    }
    if len(normalized_entries) != len(entries):
        raise PolicyError("dependency policy contains duplicate normalized package names")

    locked_names = {package.name for package in packages}
    policy_names = set(normalized_entries)
    missing = sorted(locked_names - policy_names)
    stale = sorted(policy_names - locked_names)
    if missing:
        raise PolicyError(
            "locked distribution(s) lack reviewed policy: " + ", ".join(missing)
        )
    if stale:
        raise PolicyError(
            "dependency policy contains stale distribution(s): " + ", ".join(stale)
        )

    allowed_raw = policy.get("allowed_runtime_license_ids")
    if (
        not isinstance(allowed_raw, list)
        or not allowed_raw
        or any(not isinstance(item, str) or not item for item in allowed_raw)
    ):
        raise PolicyError("allowed_runtime_license_ids must be non-empty strings")
    allowed_ids = frozenset(allowed_raw)

    for package in packages:
        entry = normalized_entries[package.name]
        if not isinstance(entry, dict):
            raise PolicyError(f"policy for {package.name!r} must be an object")
        scope = entry.get("scope")
        if scope not in _VALID_SCOPES:
            raise PolicyError(f"policy for {package.name!r} has invalid scope")
        expression = entry.get("license_expression")
        homepage = entry.get("homepage")
        if not isinstance(expression, str) or not expression.strip():
            raise PolicyError(f"policy for {package.name!r} lacks a license expression")
        if not isinstance(homepage, str) or not homepage.startswith("https://"):
            raise PolicyError(f"policy for {package.name!r} lacks an HTTPS attribution")
        if scope == "runtime":
            _validate_runtime_license(
                component=package.name,
                expression=expression,
                allowed_ids=allowed_ids,
            )

    native_components = policy.get("soundfile_bundled_native_components")
    if not isinstance(native_components, list) or not native_components:
        raise PolicyError("soundfile native component inventory must be non-empty")
    native_names: set[str] = set()
    for entry in native_components:
        if not isinstance(entry, dict):
            raise PolicyError("soundfile native component entries must be objects")
        name = entry.get("name")
        expression = entry.get("license_expression")
        homepage = entry.get("homepage")
        if not isinstance(name, str) or not name or name in native_names:
            raise PolicyError("soundfile native component names must be unique")
        native_names.add(name)
        if not isinstance(expression, str) or not expression:
            raise PolicyError(f"native component {name!r} lacks a license expression")
        if not isinstance(homepage, str) or not homepage.startswith("https://"):
            raise PolicyError(f"native component {name!r} lacks an HTTPS attribution")
        _validate_runtime_license(
            component=name,
            expression=expression,
            allowed_ids=allowed_ids,
        )

    platform_components = policy.get("platform_bundled_native_components")
    if not isinstance(platform_components, list) or not platform_components:
        raise PolicyError("platform native component inventory must be non-empty")
    platform_identities: set[tuple[str, str]] = set()
    for entry in platform_components:
        if not isinstance(entry, dict):
            raise PolicyError("platform native component entries must be objects")
        name = entry.get("name")
        version = entry.get("version")
        expression = entry.get("license_expression")
        homepage = entry.get("homepage")
        component_targets = entry.get("targets")
        embedded_by = entry.get("embedded_by")
        source_sha256 = entry.get("source_sha256")
        provenance = entry.get("provenance")
        if not isinstance(name, str) or not name:
            raise PolicyError("platform native component names must be non-empty")
        if not isinstance(version, str) or not version:
            raise PolicyError(f"platform native component {name!r} lacks a version")
        identity = (name, version)
        if identity in platform_identities:
            raise PolicyError("platform native component identities must be unique")
        platform_identities.add(identity)
        if not isinstance(expression, str) or not expression:
            raise PolicyError(
                f"platform native component {name!r} lacks a license expression"
            )
        if not isinstance(homepage, str) or not homepage.startswith("https://"):
            raise PolicyError(
                f"platform native component {name!r} lacks an HTTPS attribution"
            )
        if (
            not isinstance(component_targets, list)
            or not component_targets
            or any(item not in targets for item in component_targets)
            or len(set(component_targets)) != len(component_targets)
        ):
            raise PolicyError(
                f"platform native component {name!r} has invalid targets"
            )
        if not isinstance(embedded_by, str) or not embedded_by:
            raise PolicyError(
                f"platform native component {name!r} lacks its embedding owner"
            )
        if (
            not isinstance(source_sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", source_sha256) is None
        ):
            raise PolicyError(
                f"platform native component {name!r} lacks source SHA-256"
            )
        if (
            not isinstance(provenance, str)
            or not provenance
            or Path(provenance).name != provenance
        ):
            raise PolicyError(
                f"platform native component {name!r} has unsafe provenance"
            )
        _validate_runtime_license(
            component=name,
            expression=expression,
            allowed_ids=allowed_ids,
        )

    mp3 = policy.get("mp3_capability")
    if not isinstance(mp3, dict):
        raise PolicyError("mp3_capability policy must be an object")
    mp3_import = mp3.get("import")
    mp3_bounce = mp3.get("bounce")
    if not isinstance(mp3_import, dict) or not isinstance(mp3_bounce, dict):
        raise PolicyError("MP3 import and bounce policies must be objects")
    if mp3_import.get("availability") != "runtime-probed":
        raise PolicyError("MP3 import availability must remain runtime-probed")
    if mp3_import.get("probe") != "soundfile.check_format('MP3')":
        raise PolicyError("MP3 import must use the SoundFile runtime probe")
    if mp3_bounce.get("availability") != "disabled-by-default":
        raise PolicyError("MP3 bounce must remain disabled by default")
    if mp3_bounce.get("bundled_default_adapter") is not False:
        raise PolicyError("policy must not claim a default MP3 bounce adapter")
    if mp3.get("bundled_standalone_encoder") is not False:
        raise PolicyError("policy must not claim a standalone MP3 encoder is bundled")

    evidence = policy.get("packaged_license_evidence")
    if not isinstance(evidence, list) or not evidence:
        raise PolicyError("packaged_license_evidence must be non-empty")
    for item in evidence:
        if not isinstance(item, dict):
            raise PolicyError("packaged license evidence entries must be objects")
        suffix = item.get("path_suffix")
        digest = item.get("sha256")
        digests_by_target = item.get("sha256_by_target")
        if (
            not isinstance(suffix, str)
            or not suffix
            or Path(suffix).is_absolute()
            or ".." in Path(suffix).parts
        ):
            raise PolicyError("packaged license evidence suffix is unsafe")
        if (digest is None) == (digests_by_target is None):
            raise PolicyError(
                "packaged license evidence requires exactly one SHA-256 policy"
            )
        if digest is not None:
            if (
                not isinstance(digest, str)
                or re.fullmatch(r"[0-9a-f]{64}", digest) is None
            ):
                raise PolicyError(
                    "packaged license evidence requires lowercase SHA-256"
                )
            continue
        if (
            not isinstance(digests_by_target, dict)
            or set(digests_by_target) != set(targets)
            or any(
                not isinstance(value, str)
                or re.fullmatch(r"[0-9a-f]{64}", value) is None
                for value in digests_by_target.values()
            )
        ):
            raise PolicyError(
                "target-bound license evidence must cover every target with "
                "lowercase SHA-256"
            )

    return RuntimeInventory(policy=policy, packages=packages)


def _versions_text(package: LockedPackage) -> str:
    grouped: dict[str, list[str]] = {}
    for target, version in package.versions:
        grouped.setdefault(version, []).append(target)
    if len(grouped) == 1:
        return next(iter(grouped))
    return "; ".join(
        f"{version} ({', '.join(targets)})"
        for version, targets in sorted(grouped.items())
    )


def render_notice(inventory: RuntimeInventory) -> str:
    """Render the deterministic human-readable runtime attribution."""

    policy = inventory.policy
    entries: dict[str, dict[str, Any]] = policy["packages"]
    lines = [
        "# WebJam Frozen Python Runtime Notices",
        "",
        "<!-- Generated by tools/runtime_dependency_policy.py; do not edit. -->",
        "",
        "This inventory covers the Python distributions selected for WebJam's",
        "four native frozen-package targets. It is generated from the exact hashed",
        "release locks and a reviewed policy; an unreviewed lock entry fails the",
        "release gate. `Runtime` means code may ship in or support the frozen app.",
        "`Build` means freeze-time tooling, and `Excluded` means the PyInstaller",
        "spec deliberately omits that legacy dependency.",
        "",
        "The runtime policy rejects a selected GPL or AGPL license. LGPL libraries",
        "remain permitted and attributed. That rule is intentionally separate from",
        "the independently executed Jamulus distribution described in",
        "`THIRD_PARTY_NOTICES.md`, and from PyInstaller's build-only bootloader",
        "exception.",
        "",
        "## Frozen Python distributions",
        "",
        "| Distribution | Locked version(s) | Scope | Selected license | Upstream |",
        "|---|---|---|---|---|",
    ]
    for package in inventory.packages:
        entry = entries[package.name]
        lines.append(
            "| "
            f"`{package.name}` | `{_versions_text(package)}` | "
            f"{str(entry['scope']).title()} | "
            f"`{entry['license_expression']}` | "
            f"[project]({entry['homepage']}) |"
        )

    lines.extend(
        [
            "",
            "Dual-licensed packages use only the selected expression shown above.",
            "In particular, WebJam distributes Qt for Python under its",
            "LGPL-3.0-only option, and cryptography under its BSD-3-Clause option.",
            "",
            "## SoundFile wheel native payload",
            "",
            "SoundFile is BSD-3-Clause Python code. Its native wheels carry",
            "libsndfile and codec libraries under the following upstream terms.",
            "The exact upstream wheel license notes are packaged as",
            "`SOUNDFILE_WHEEL_LICENSE_NOTES.md`; libsndfile's full LGPL-2.1 text",
            "is retained from the wheel as `_soundfile_data/COPYING`.",
            "",
            "| Component | Version evidence | License | Used for MP3 | Upstream |",
            "|---|---|---|---|---|",
        ]
    )
    for entry in policy["soundfile_bundled_native_components"]:
        version = entry.get("version") or "not declared by wheel"
        mp3 = "Yes" if entry.get("mp3") else "No"
        lines.append(
            "| "
            f"`{entry['name']}` | {version} | "
            f"`{entry['license_expression']}` | {mp3} | "
            f"[project]({entry['homepage']}) |"
        )

    lines.extend(
        [
            "",
            "## Platform-specific native payload",
            "",
            "The cryptography wheels statically embed target-specific OpenSSL",
            "builds. Intel macOS uses WebJam's separately reviewed 3.5.7 LTS",
            "source build; the other targets use upstream's reviewed 4.0.1 wheels.",
            "",
            "| Component | Version | Target(s) | Embedded by | License | Provenance |",
            "|---|---|---|---|---|---|",
        ]
    )
    for entry in policy["platform_bundled_native_components"]:
        lines.append(
            "| "
            f"`{entry['name']}` | `{entry['version']}` | "
            f"`{','.join(entry['targets'])}` | `{entry['embedded_by']}` | "
            f"`{entry['license_expression']}` | `{entry['provenance']}` |"
        )

    lines.extend(
        [
            "",
            "## MP3 capability",
            "",
            "WebJam does not ship FFmpeg or a separate MP3 executable. MP3 import",
            "is optional and must be exposed only when the packaged SoundFile stack",
            "passes `soundfile.check_format('MP3')` at runtime. If that probe is",
            "false, the import path must report MP3 as unavailable;",
            "the presence of a SoundFile wheel is not itself an MP3 guarantee.",
            "",
            "Reference Studio MP3 bounce is a separate capability and is disabled",
            "by default. No encoder adapter is bundled. It may be offered only when",
            "a separately reviewed adapter passes its bounded self-test, identifies",
            "its implementation and license, and verifies the encoded output.",
            "SoundFile import capability alone must never enable MP3 bounce.",
            "",
            "The SoundFile wheel records LGPL libmpg123/libmp3lame components even",
            "though WebJam does not expose them as a default bounce adapter. WebJam",
            "does not claim that a successful probe removes patent, platform, or",
            "managed-device restrictions outside the packaged codec capability.",
            "",
            "## Machine-readable inventory",
            "",
            "The corresponding deterministic CycloneDX 1.5 inventory is packaged",
            "as `WebJam-runtime-sbom.cdx.json`.",
            "",
        ]
    )
    return "\n".join(lines)


def _component_properties(targets: tuple[str, ...]) -> list[dict[str, str]]:
    return [{"name": "webjam:targets", "value": ",".join(targets)}]


def render_sbom(inventory: RuntimeInventory) -> str:
    """Render a deterministic CycloneDX 1.5 runtime SBOM."""

    policy = inventory.policy
    webjam_version = application_version()
    entries: dict[str, dict[str, Any]] = policy["packages"]
    components: list[dict[str, Any]] = []
    for package in inventory.packages:
        entry = entries[package.name]
        if entry["scope"] != "runtime":
            continue
        targets_by_version: dict[str, list[str]] = {}
        for target, version in package.versions:
            targets_by_version.setdefault(version, []).append(target)
        for version, targets in sorted(targets_by_version.items()):
            purl = f"pkg:pypi/{package.name}@{version}"
            components.append(
                {
                    "type": "library",
                    "bom-ref": purl,
                    "name": package.name,
                    "version": version,
                    "purl": purl,
                    "licenses": [{"expression": entry["license_expression"]}],
                    "externalReferences": [
                        {"type": "website", "url": entry["homepage"]}
                    ],
                    "properties": _component_properties(tuple(targets)),
                }
            )

    for entry in policy["soundfile_bundled_native_components"]:
        component: dict[str, Any] = {
            "type": "library",
            "bom-ref": f"webjam:soundfile-native:{entry['name']}",
            "name": entry["name"],
            "scope": "required",
            "licenses": [{"expression": entry["license_expression"]}],
            "externalReferences": [{"type": "website", "url": entry["homepage"]}],
            "properties": [
                {
                    "name": "webjam:embedded-by",
                    "value": "pkg:pypi/soundfile@0.14.0",
                },
                {
                    "name": "webjam:mp3-component",
                    "value": str(bool(entry.get("mp3"))).lower(),
                },
            ],
        }
        if entry.get("version"):
            component["version"] = entry["version"]
        components.append(component)

    for entry in policy["platform_bundled_native_components"]:
        component = {
            "type": "library",
            "bom-ref": (
                f"webjam:native:{entry['name']}@{entry['version']}"
                f"?targets={','.join(entry['targets'])}"
            ),
            "name": entry["name"],
            "version": entry["version"],
            "scope": "required",
            "licenses": [{"expression": entry["license_expression"]}],
            "externalReferences": [
                {"type": "website", "url": entry["homepage"]}
            ],
            "properties": [
                {
                    "name": "webjam:targets",
                    "value": ",".join(entry["targets"]),
                },
                {
                    "name": "webjam:embedded-by",
                    "value": entry["embedded_by"],
                },
                {
                    "name": "webjam:source-sha256",
                    "value": entry["source_sha256"],
                },
                {
                    "name": "webjam:provenance",
                    "value": entry["provenance"],
                },
            ],
        }
        components.append(component)

    document = {
        "$schema": "http://cyclonedx.org/schema/bom-1.5.schema.json",
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "version": 1,
        "metadata": {
            "component": {
                "type": "application",
                "name": "WebJam",
                "version": webjam_version,
                "bom-ref": f"pkg:generic/webjam@{webjam_version}",
                "purl": f"pkg:generic/webjam@{webjam_version}",
            },
            "properties": [
                {
                    "name": "webjam:inventory-source",
                    "value": "requirements-lock/* plus reviewed runtime policy",
                },
                {
                    "name": "webjam:mp3-import-capability",
                    "value": "runtime-probed with soundfile.check_format('MP3')",
                },
                {
                    "name": "webjam:mp3-bounce-capability",
                    "value": "disabled by default; no bundled encoder adapter",
                },
            ],
        },
        "components": sorted(
            components,
            key=lambda item: (str(item["name"]).lower(), str(item.get("version", ""))),
        ),
    }
    return json.dumps(document, indent=2, sort_keys=True) + "\n"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_bundle(
    bundle_root: Path,
    inventory: RuntimeInventory,
    *,
    target: str | None = None,
) -> None:
    """Verify generated artifacts and reviewed license evidence in a package."""

    if not bundle_root.is_dir():
        raise PolicyError("packaged bundle root is missing")
    if target is not None and target not in inventory.policy["targets"]:
        raise PolicyError(f"unknown packaged bundle target: {target}")

    generated = {
        "THIRD_PARTY_NOTICES_RUNTIME.md": render_notice(inventory).encode("utf-8"),
        "WebJam-runtime-sbom.cdx.json": render_sbom(inventory).encode("utf-8"),
    }
    for name, expected in generated.items():
        matches = tuple(bundle_root.rglob(name))
        if not matches:
            raise PolicyError(f"packaged dependency artifact is missing: {name}")
        for match in matches:
            if match.read_bytes() != expected:
                raise PolicyError(f"packaged dependency artifact differs: {name}")

    for item in inventory.policy["packaged_license_evidence"]:
        suffix = item["path_suffix"]
        basename = Path(suffix).name
        matches = tuple(
            path
            for path in bundle_root.rglob(basename)
            if path.as_posix().endswith(suffix)
        )
        if not matches:
            raise PolicyError(
                "packaged license evidence is missing: " + str(item["component"])
            )
        if "sha256_by_target" in item:
            if target is None:
                raise PolicyError(
                    "packaged bundle target is required for target-bound "
                    f"license evidence: {item['component']}"
                )
            expected = item["sha256_by_target"][target]
        else:
            expected = item["sha256"]
        bad = [path for path in matches if _sha256(path) != expected]
        if bad:
            raise PolicyError(
                "packaged license evidence checksum failed: "
                + str(item["component"])
            )


def _check_output(path: Path, expected: str) -> None:
    try:
        actual = path.read_bytes()
    except OSError as exc:
        raise PolicyError(f"generated dependency artifact is missing: {path.name}") from exc
    if actual != expected.encode("utf-8"):
        raise PolicyError(
            f"{path.name} is stale; run tools/runtime_dependency_policy.py --write"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--lock-root", type=Path, default=DEFAULT_LOCK_ROOT)
    parser.add_argument("--notice", type=Path, default=DEFAULT_NOTICE)
    parser.add_argument("--sbom", type=Path, default=DEFAULT_SBOM)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--verify-bundle", type=Path)
    parser.add_argument("--target")
    args = parser.parse_args(argv)

    try:
        inventory = validate_policy(args.policy, args.lock_root)
        notice = render_notice(inventory)
        sbom = render_sbom(inventory)
        if args.write:
            args.notice.write_bytes(notice.encode("utf-8"))
            args.sbom.write_bytes(sbom.encode("utf-8"))
        if args.check or not args.write:
            _check_output(args.notice, notice)
            _check_output(args.sbom, sbom)
        if args.verify_bundle is not None:
            verify_bundle(args.verify_bundle, inventory, target=args.target)
        elif args.target is not None:
            raise PolicyError("--target requires --verify-bundle")
    except PolicyError as exc:
        print(f"runtime dependency policy failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
