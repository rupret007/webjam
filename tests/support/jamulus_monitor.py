"""Process-bound Jamulus RPC monitor fixtures for controller tests."""

from __future__ import annotations

import math
import time
from unittest.mock import MagicMock

from core.jamulus_rpc_client import (
    JamulusRpcMonitorIdentity,
    JamulusRpcMonitorSnapshot,
)


def bind_primary_rpc_monitor(
    controller,
    *,
    process_id: int | None = None,
    process_generation: int | None = None,
    monitor_epoch: int = 1,
) -> JamulusRpcMonitorIdentity:
    """Bind dynamic fresh/stale RPC evidence to one fake Bridge process.

    Tests may replace ``controller.jamulus.rpc_client`` or change its
    ``available``/``last_activity_age`` values afterward. The installed
    provider reads those values at observation time, matching production's
    immutable process-identity contract.
    """

    process = getattr(controller.bridge, "jamulus_process", None)
    if process_id is None:
        process_id = int(getattr(process, "pid", 0) or 0)
    if process_id <= 0:
        raise ValueError("a positive primary process_id is required")

    current_generation = int(
        getattr(controller.bridge, "_jamulus_process_generation", 0)
    )
    if process_generation is None:
        process_generation = current_generation if current_generation > 0 else 1
    if process_generation <= 0:
        raise ValueError("a positive primary process_generation is required")

    controller.bridge._jamulus_process_generation_counter = max(
        int(
            getattr(
                controller.bridge,
                "_jamulus_process_generation_counter",
                0,
            )
        ),
        process_generation,
    )
    controller.bridge._jamulus_process_generation = process_generation
    controller.bridge._jamulus_process_started_at = 0.0
    identity = JamulusRpcMonitorIdentity(
        monitor_epoch=monitor_epoch,
        process_generation=process_generation,
        process_id=process_id,
    )

    def monitor_snapshot_for(
        *,
        process_generation: int,
        process_id: int,
    ) -> JamulusRpcMonitorSnapshot | None:
        if (
            process_generation != identity.process_generation
            or process_id != identity.process_id
        ):
            return None
        rpc = controller.jamulus.rpc_client
        available = getattr(rpc, "available", False) is True
        age_provider = getattr(rpc, "last_activity_age", None)
        try:
            observed_age = age_provider() if callable(age_provider) else None
        except Exception:
            observed_age = None
        usable_age = bool(
            isinstance(observed_age, (int, float))
            and not isinstance(observed_age, bool)
            and math.isfinite(float(observed_age))
            and float(observed_age) >= 0.0
        )
        return JamulusRpcMonitorSnapshot(
            identity=identity,
            running=True,
            available=available,
            authenticated=available,
            last_activity_at=(
                time.monotonic() - float(observed_age)
                if usable_age
                else None
            ),
            last_activity_age_seconds=(
                float(observed_age) if usable_age else None
            ),
        )

    controller.jamulus.rpc_monitor_snapshot_for = MagicMock(
        side_effect=monitor_snapshot_for
    )
    return identity
