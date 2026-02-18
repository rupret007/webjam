from __future__ import annotations

import ctypes
import subprocess
from pathlib import Path
from typing import Iterable, Optional


def run(cmd, check: bool = False, shell: bool = False):
    return subprocess.run(cmd, check=check, shell=shell, capture_output=True, text=True)


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def find_jamulus(jamulus_candidates: Iterable[str]) -> Optional[str]:
    for candidate in jamulus_candidates:
        if Path(candidate).exists():
            return candidate
    return None


def vb_cable_present() -> bool:
    ps = r'''
    $reg = "HKLM:\SYSTEM\CurrentControlSet\Enum\SWD\MMDEVAPI"
    if (Test-Path $reg) {
      $all = Get-ChildItem $reg -Recurse -ErrorAction SilentlyContinue | Get-ItemProperty -ErrorAction SilentlyContinue
      if ($all.FriendlyName -match "VB-Audio Virtual Cable|CABLE Input|CABLE Output") { exit 0 } else { exit 1 }
    } else { exit 1 }
    '''
    result = run(["powershell", "-NoP", "-NonI", "-Command", ps])
    return result.returncode == 0
