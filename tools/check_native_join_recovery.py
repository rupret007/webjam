#!/usr/bin/env python3
"""Check one installed Mac launch/Join journey using real accessibility controls.

This deliberately has no host, valid-invitation, playback or permission-setting
operation. Run against an already open, idle application, not a user session.
"""
from __future__ import annotations

import argparse
import ctypes
import datetime
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import time


# Spaces are intentional. A DNS-shaped dummy is a VALID legacy server address.
MALFORMED_INVITATION = "this is not an invitation"
INVALID_STATUS = "That invite didn't work"
INVALID_HELP = (
    "That's not a WebJam invite. Ask your host to press Copy Invite "
    "and send you the whole message."
)


class CheckStopped(RuntimeError):
    """A bounded, non-sensitive reason to stop before the next action."""


def resource_guard() -> dict:
    raw = subprocess.check_output(["/usr/sbin/sysctl", "vm.swapusage"], text=True)
    values = dict((k, float(v)) for k, v in re.findall(
        r"(total|used|free) = ([0-9.]+)M", raw
    ))
    if not {"total", "used"} <= values.keys():
        raise CheckStopped("resource-inventory-unavailable")
    result = {
        "load": os.getloadavg()[0],
        "swapPercent": 100 * values["used"] / values["total"] if values["total"] else 0,
        "freeGiB": shutil.disk_usage("/").free / 2**30,
    }
    if result["load"] >= 10 or result["swapPercent"] >= 80 or result["freeGiB"] < 15:
        raise CheckStopped("resource-guard-closed")
    return result


def audio_processes() -> list[dict]:
    rows = subprocess.check_output(["/bin/ps", "-axo", "pid=,comm="], text=True)
    found = []
    for row in rows.splitlines():
        parts = row.split(None, 1)
        if len(parts) == 2:
            name = Path(parts[1]).name
            if name.lower() in {"webjam", "jamulus", "jamulusserver", "jamulusheadless"}:
                found.append({"pid": int(parts[0]), "name": name})
    return found


def verify_owned_build(pid: int, expected_source: str) -> dict:
    if platform.system() != "Darwin" or pid <= 1:
        raise CheckStopped("requires-existing-macos-application")
    if not re.fullmatch(r"[0-9a-f]{40}", expected_source):
        raise CheckStopped("requires-full-source-sha")
    lib = ctypes.CDLL("/usr/lib/libproc.dylib")
    lib.proc_pidpath.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
    lib.proc_pidpath.restype = ctypes.c_int
    buffer = ctypes.create_string_buffer(4096)
    if lib.proc_pidpath(pid, buffer, len(buffer)) <= 0:
        raise CheckStopped("application-process-missing")
    executable = Path(os.fsdecode(buffer.value)).resolve()
    if executable.parts[-3:] != ("Contents", "MacOS", "WebJam"):
        raise CheckStopped("unexpected-application-executable")
    app = executable.parents[2]
    if (app / "Contents/Resources/webjam-build-id.txt").read_text().strip() != expected_source:
        raise CheckStopped("installed-source-mismatch")
    result = subprocess.run(
        ["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)],
        capture_output=True, timeout=40,
    )
    if result.returncode:
        raise CheckStopped("application-signature-invalid")
    if audio_processes() != [{"pid": pid, "name": "WebJam"}]:
        raise CheckStopped("another-audio-process-present")
    return {"pid": pid, "source": expected_source, "strictSignatureValid": True}


class LaunchControls:
    def __init__(self, pid: int):
        if not isinstance(pid, int) or pid <= 1:
            raise CheckStopped("invalid-process-id")
        self.pid = pid

    def _execute(self, body: str) -> str:
        script = (
            'tell application "System Events"\n'
            f'tell (first application process whose unix id is {self.pid})\n'
            f'{body}\nend tell\nend tell'
        )
        result = subprocess.run(
            ["/usr/bin/osascript", "-e", script],
            capture_output=True, text=True, timeout=20,
        )
        if result.returncode:
            # Do not export raw AX values, paths or OS diagnostics.
            raise CheckStopped("accessibility-operation-failed")
        return result.stdout.strip()

    def buttons(self) -> str:
        return self._execute("get name of every button of window 1")

    def press(self, name: str) -> None:
        if name not in {"Join", "Back"}:
            raise CheckStopped("media-or-unrelated-action-forbidden")
        self._execute(f'click button "{name}" of window 1')

    def fill_malformed_invitation(self) -> None:
        if self._execute("get count of text fields of window 1") != "1":
            raise CheckStopped("unexpected-join-surface")
        self._execute(
            'set value of text field 1 of window 1 to '
            f'"{MALFORMED_INVITATION}"'
        )

    def masked_value(self) -> str:
        # Only compare in memory. Never serialize even a masked invite value.
        return self._execute("get value of text field 1 of window 1")

    def instructions(self) -> str:
        # Buffer the AX element list before iterating; direct traversal and
        # window-level static-text queries can omit nested Qt labels.
        return self._execute('''
set outputText to ""
set es to entire contents of window 1
repeat with itemRef in es
 set elementItem to contents of itemRef
 try
  if role of elementItem is "AXStaticText" then
   set outputText to outputText & (name of elementItem as text) & linefeed
  end if
 end try
end repeat
return outputText''')


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise CheckStopped(reason)


def check_join_recovery(pid: int, expected_source: str) -> dict:
    result = {
        "state": "STOPPED",
        "at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "mediaRequested": False,
        "clipboardAccessed": False,
    }
    controls = LaunchControls(pid)
    entered_join = False
    try:
        result["guardBefore"] = resource_guard()
        result["build"] = verify_owned_build(pid, expected_source)
        require(controls._execute("get count of windows") == "1", "unexpected-window-count")
        require({"Host", "Join"} <= set(controls.buttons().split(", ")), "requires-idle-launch-screen")
        controls.press("Join")
        entered_join = True
        controls.fill_malformed_invitation()
        require(controls.masked_value() == "\u2022" * len(MALFORMED_INVITATION), "password-masking-not-observed")
        controls.press("Join")
        deadline = time.monotonic() + 5
        while True:
            labels = controls.instructions()
            if INVALID_STATUS in labels and INVALID_HELP in labels:
                break
            if time.monotonic() >= deadline:
                raise CheckStopped("invalid-invitation-guidance-not-observed")
            time.sleep(0.2)
        result["invalidInvitationHelpObserved"] = True
        require(controls.masked_value() == "\u2022" * len(MALFORMED_INVITATION), "masking-changed-after-refusal")
        controls.press("Back")
        entered_join = False
        controls.press("Join")
        entered_join = True
        require(controls.masked_value() == "", "previous-invitation-not-cleared")
        result["invitationClearedOnReturn"] = True
        controls.press("Back")
        entered_join = False
        require({"Host", "Join"} <= set(controls.buttons().split(", ")), "launch-screen-not-restored")
        require(audio_processes() == [{"pid": pid, "name": "WebJam"}], "unexpected-audio-process")
        result["guardAfter"] = resource_guard()
        result["state"] = "PASSED_NATIVE_JOIN_RECOVERY_ONLY"
    except CheckStopped as exc:
        result["reason"] = str(exc)
    except Exception as exc:
        result["reason"] = "unexpected-" + type(exc).__name__
    finally:
        if entered_join:
            # Never quit/restart an application or act on an unexpected workspace.
            try:
                if (
                    {"Join", "Back"} <= set(controls.buttons().split(", "))
                    and controls._execute("get count of text fields of window 1") == "1"
                ):
                    controls.press("Back")
                    result["failedCheckReturnedToLaunch"] = True
            except Exception:
                result["cleanupNeedsInspection"] = True
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--expected-source", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    # Exclusive, owner-only output; never overwrite an earlier attempt.
    with open(args.output, "x", opener=lambda p, f: os.open(p, f, 0o600)) as output:
        result = check_join_recovery(args.pid, args.expected_source)
        json.dump(result, output, indent=2)
        output.write("\n")
    print(json.dumps(result))
    return 0 if result["state"] == "PASSED_NATIVE_JOIN_RECOVERY_ONLY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
