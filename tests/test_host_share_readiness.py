from __future__ import annotations

import pytest

from core.host_share_readiness import (
    HostShareReadinessStatus,
    evaluate_host_share_readiness,
)


@pytest.mark.parametrize(
    ("server_alive", "port_bound", "address", "expected"),
    [
        (False, None, "", HostShareReadinessStatus.SERVER_STARTING),
        (True, False, "192.168.1.8", HostShareReadinessStatus.AUDIO_PORT_UNAVAILABLE),
        (True, None, "192.168.1.8", HostShareReadinessStatus.PORT_INSPECTION_FAILED),
        (True, True, "", HostShareReadinessStatus.NETWORK_UNAVAILABLE),
    ],
)
def test_host_share_readiness_refuses_to_share_when_a_local_fact_is_missing(
    server_alive: bool,
    port_bound: bool | None,
    address: str,
    expected: HostShareReadinessStatus,
):
    result = evaluate_host_share_readiness(
        server_alive=server_alive,
        audio_port_bound=port_bound,
        private_lan_address=address,
    )

    assert result.status is expected
    assert not result.shareable
    assert result.action


def test_host_share_readiness_is_explicitly_private_lan_only():
    result = evaluate_host_share_readiness(
        server_alive=True,
        audio_port_bound=True,
        private_lan_address="192.168.1.8",
    )

    assert result.shareable
    assert result.status is HostShareReadinessStatus.READY_PRIVATE_LAN
    assert result.address == "192.168.1.8"
    assert "same network" in result.detail
