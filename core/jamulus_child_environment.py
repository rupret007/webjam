"""Shared environment boundary for every native Jamulus subprocess.

WebJam must never let inherited loader, Qt, or QML controls alter which code a
verified Jamulus executable loads.  This module lives below both the profile
and platform layers so launch probes and long-lived child roles use one policy
without a core-to-services dependency.
"""

from __future__ import annotations

from collections.abc import Mapping
import os
from pathlib import Path


_INJECTION_PREFIXES = ("DYLD_", "LD_", "QML", "QT")
_INJECTION_NAMES = frozenset(
    {
        "GCONV_PATH",
        "PATH",
        "DYLD_FRAMEWORK_PATH",
        "DYLD_INSERT_LIBRARIES",
        "DYLD_LIBRARY_PATH",
        "QML2_IMPORT_PATH",
        "QML_IMPORT_PATH",
        "QT_PLUGIN_PATH",
        "QT_QPA_PLATFORM_PLUGIN_PATH",
    }
)


class JamulusChildEnvironmentError(ValueError):
    """A native-child environment could not be bounded safely."""


def sanitized_jamulus_child_environment(
    environ: Mapping[str, str],
    *,
    platform_name: str,
    executable: str | Path,
) -> dict[str, str]:
    """Return a bounded environment for one exact native executable."""

    if not isinstance(environ, Mapping):
        raise TypeError("Jamulus child environment must be a mapping")
    path = Path(executable)
    if not path.is_absolute():
        raise JamulusChildEnvironmentError(
            "the Jamulus child executable path is invalid"
        )
    normalized_platform = str(platform_name or "").strip().lower()
    if normalized_platform not in {"darwin", "win32", "linux"}:
        raise JamulusChildEnvironmentError(
            "the Jamulus child environment platform is unsupported"
        )

    result: dict[str, str] = {}
    for key, value in environ.items():
        normalized = str(key).upper()
        if normalized in _INJECTION_NAMES or any(
            normalized.startswith(prefix)
            for prefix in _INJECTION_PREFIXES
        ):
            continue
        result[str(key)] = str(value)
    if normalized_platform == "win32":
        windows_root = str(
            next(
                (
                    value
                    for key, value in result.items()
                    if key.casefold() == "systemroot"
                ),
                r"C:\Windows",
            )
        )
        result["PATH"] = os.pathsep.join(
            (
                str(path.parent),
                str(Path(windows_root) / "System32"),
                windows_root,
            )
        )
    else:
        result["PATH"] = "/usr/bin:/bin"
    return result


__all__ = [
    "JamulusChildEnvironmentError",
    "sanitized_jamulus_child_environment",
]
