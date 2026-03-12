from __future__ import annotations

import platform
import subprocess
import os
from pathlib import Path
from typing import Iterable, Optional


def run(cmd, check: bool = False, shell: bool = False):
    return subprocess.run(cmd, check=check, shell=shell, capture_output=True, text=True)


def is_admin() -> bool:
    if platform.system() != "Windows":
        return False
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def find_jamulus(jamulus_candidates: Iterable[object]) -> Optional[str]:
    for candidate in jamulus_candidates:
        if not isinstance(candidate, (str, bytes, os.PathLike)):
            continue
        try:
            candidate_path = Path(candidate)
        except (TypeError, ValueError):
            continue
        try:
            if candidate_path.exists():
                return str(candidate_path)
        except OSError:
            continue
    return None


def vb_cable_present() -> bool:
    if platform.system() != "Windows":
        return False
    ps = r'''
    $reg = "HKLM:\SYSTEM\CurrentControlSet\Enum\SWD\MMDEVAPI"
    if (Test-Path $reg) {
      $all = Get-ChildItem $reg -Recurse -ErrorAction SilentlyContinue | Get-ItemProperty -ErrorAction SilentlyContinue
      if ($all.FriendlyName -match "VB-Audio Virtual Cable|CABLE Input|CABLE Output") { exit 0 } else { exit 1 }
    } else { exit 1 }
    '''
    result = run(["powershell", "-NoP", "-NonI", "-Command", ps])
    return result.returncode == 0
