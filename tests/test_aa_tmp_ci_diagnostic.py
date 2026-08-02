"""TEMPORARY diagnostic - delete once the Linux-only failure is understood.

tests/test_bridge_recovery_contract.py has two cases that pass on macOS and
fail on Linux CI, reproducibly. Three local hypotheses were disproven by
experiment: the Jamulus component target (pinned to LINUX_X64 and to None),
and sys.platform forced to "linux". All still passed locally.

Rather than guess a fourth time, this reports the runtime facts from the
machine where it actually fails. It asserts False on purpose so pytest
prints the captured values.
"""

from __future__ import annotations

import logging
import platform
import sys
from unittest.mock import MagicMock, patch

from tests.test_bridge_recovery_contract import (
    _ImmediateThread,
    _bridge,
    _prime_recovery,
    _process,
    _publish_recovery_process,
)


def test_tmp_report_launch_path_facts() -> None:
    records: list[str] = []

    class _Capture(logging.Handler):
        def emit(self, record):
            records.append(f"{record.name}:{record.getMessage()[:90]}")

    handler = _Capture()
    root = logging.getLogger()
    root.addHandler(handler)
    previous_level = root.level
    root.setLevel(logging.DEBUG)

    facts = {
        "python": sys.version.split()[0],
        "sys.platform": sys.platform,
        "machine": platform.machine(),
    }

    with (
        patch(
            "services.bridge_service.subprocess.Popen",
            side_effect=OSError("spawn failed"),
        ) as popen,
        patch("services.bridge_service.threading.Thread", _ImmediateThread),
        patch("services.bridge_service.time.sleep"),
    ):
        bridge = _bridge()
        facts["component_target"] = repr(
            getattr(bridge, "_jamulus_component_target", "missing")
        )
        old_process = _process(114)
        _prime_recovery(bridge, attempts=1)
        _publish_recovery_process(
            bridge,
            old_process,
            process_generation=7,
            recovery_generation=0,
        )
        bridge.find_jamulus = MagicMock(return_value="/usr/bin/jamulus")
        bridge._is_rpc_port_in_use = MagicMock(return_value=False)

        facts["launch_returned"] = repr(
            bridge.launch_jamulus(
                manual=False, reconnect=True, force_restart=True
            )
        )
        facts["popen_calls"] = popen.call_count
        facts["terminate_calls"] = old_process.terminate.call_count
        facts["jamulus_process"] = repr(bridge.jamulus_process)
        facts["reconnect_attempts"] = getattr(
            bridge, "jamulus_reconnect_attempts", "missing"
        )
        facts["launch_intended"] = getattr(
            bridge, "jamulus_launch_intended", "missing"
        )
        facts["shutdown_requested"] = bridge.shutdown_requested()

    root.removeHandler(handler)
    root.setLevel(previous_level)

    assert False, (
        "CI DIAGNOSTIC: "
        + "; ".join(f"{k}={v}" for k, v in facts.items())
        + " || LOGS: "
        + " | ".join(records[-14:])
    )
