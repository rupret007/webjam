from __future__ import annotations

import pytest
from dataclasses import replace
import time

from core.jamulus_roster_identity import (
    JamulusRosterIdentityError,
    client_common_profile,
    ordered_client_local_roster_fingerprint,
    ordered_common_roster_digest,
    server_common_profile,
)
from core.jamulus_rpc_client import (
    ORDERED_ROSTER_PROOF_MAX_AGE_S,
    JamulusRpcClient,
    JamulusRpcMonitorIdentity,
)


def _client_row(local_id: int, name: str, instrument: int) -> dict[str, object]:
    return {
        "id": local_id,
        "name": name,
        "instrumentId": instrument,
        "instrument": "",
        "city": "Chicago",
        "country": "United States",
        "skillLevel": "intermediate",
    }


def _server_row(server_id: int, name: str, instrument: int) -> dict[str, object]:
    return {
        "id": server_id,
        "name": name,
        "instrumentCode": instrument,
        "city": "Chicago",
        "skillLevelCode": 2,
    }


def _proof_ready_client() -> tuple[JamulusRpcClient, JamulusRpcMonitorIdentity]:
    client = JamulusRpcClient()
    identity = JamulusRpcMonitorIdentity(3, 17, 9001)
    client._monitor_epoch = identity.monitor_epoch
    client._monitor_identity = identity
    client._running = True
    client._available = True
    client._authed = True
    client._rpc_connection_generation = 4
    client._handle_response(
        "jamulusclient/getClientInfo",
        {"connected": True},
        epoch=identity.monitor_epoch,
    )
    return client, identity


def test_client_and_server_namespaces_share_only_ordered_common_digest() -> None:
    client_rows = [
        _client_row(1, "Host", 3),
        _client_row(0, "Guest", 5),
        _client_row(2, "Guest", 5),
    ]
    server_rows = [
        _server_row(0, "Host", 3),
        _server_row(4, "Guest", 5),
        _server_row(11, "Guest", 5),
    ]

    client_digest = ordered_common_roster_digest(
        tuple(client_common_profile(row) for row in client_rows)
    )
    server_digest = ordered_common_roster_digest(
        tuple(server_common_profile(row) for row in server_rows)
    )

    assert client_digest == server_digest
    assert [row["id"] for row in client_rows] != [row["id"] for row in server_rows]
    assert ordered_common_roster_digest(
        tuple(server_common_profile(row) for row in reversed(server_rows))
    ) != client_digest


def test_private_layout_detects_identical_profile_reorder_idempotently() -> None:
    profiles = tuple(
        client_common_profile(_client_row(local_id, "Alex", 5))
        for local_id in (0, 1, 2)
    )
    # The cross-endpoint digest intentionally cannot distinguish identical
    # full profiles; host-private mixer IDs make the transition visible.
    assert ordered_common_roster_digest(profiles) == ordered_common_roster_digest(
        tuple(reversed(profiles))
    )
    first = ordered_client_local_roster_fingerprint(
        (0, 1, 2), own_ordinal=0
    )
    assert first == ordered_client_local_roster_fingerprint(
        (0, 1, 2), own_ordinal=0
    )
    assert first != ordered_client_local_roster_fingerprint(
        (0, 2, 1), own_ordinal=0
    )


def test_rpc_proof_uses_unique_local_zero_ordinal_and_exact_generations() -> None:
    client, identity = _proof_ready_client()
    rows = [
        _client_row(1, "Host", 3),
        _client_row(0, "Alex", 5),
        _client_row(2, "Alex", 5),
    ]

    client._update_clients(rows, epoch=identity.monitor_epoch)
    proof = client.ordered_roster_proof_for(identity)

    assert proof is not None
    assert proof.own_ordinal == 1
    assert proof.roster_size == 3
    assert [row.client_local_channel_id for row in proof.rows] == [1, 0, 2]
    assert proof.ambiguous_ordinals == (1, 2)
    assert proof.rpc_connection_generation == 4
    assert proof.audio_connection_generation == 1
    rendered = " ".join((
        repr(identity),
        repr(proof),
        repr(proof.rows[0]),
        repr(proof.rows[0].profile),
    ))
    for private_value in (
        "9001",
        "Host",
        "Chicago",
        proof.host_roster_fingerprint,
    ):
        assert private_value not in rendered
    assert proof == replace(proof)
    assert hash(proof) == hash(replace(proof))

    # Duplicate notifications/getClientInfo replies for the same connection
    # cannot manufacture a new audio generation.
    client._handle_response(
        "jamulusclient/getClientInfo",
        {"connected": True},
        epoch=identity.monitor_epoch,
    )
    assert client._audio_connection_generation == 1


def test_identical_refresh_changes_freshness_not_topology_revision() -> None:
    client, identity = _proof_ready_client()
    rows = [_client_row(local_id, "Alex", 5) for local_id in (0, 1, 2)]
    client._update_clients(rows, epoch=identity.monitor_epoch)
    first = client.ordered_roster_proof_for(identity)
    assert first is not None

    client._update_clients(rows, epoch=identity.monitor_epoch)
    refreshed = client.ordered_roster_proof_for(identity)
    assert refreshed is not None
    assert refreshed.authority_key == first.authority_key
    assert refreshed.roster_revision == first.roster_revision
    assert refreshed.observed_at >= first.observed_at

    client._update_clients(
        [_client_row(local_id, "Alex", 5) for local_id in (0, 2, 1)],
        epoch=identity.monitor_epoch,
    )
    reordered = client.ordered_roster_proof_for(identity)
    assert reordered is not None
    assert reordered.common_digest == refreshed.common_digest
    assert reordered.host_roster_fingerprint != refreshed.host_roster_fingerprint
    assert reordered.roster_revision == refreshed.roster_revision + 1


@pytest.mark.parametrize(
    "rows",
    [
        [_client_row(1, "No self", 3)],
        [_client_row(0, "A", 3), _client_row(0, "B", 4)],
        [_client_row(0, "A", 3), _client_row(1, "B", 4) | {"city": 7}],
        [_client_row(0, "A", 3), _client_row(1, "B", 4) | {"id": True}],
    ],
)
def test_malformed_or_ambiguous_client_roster_is_ui_only(
    rows: list[dict[str, object]],
) -> None:
    client, identity = _proof_ready_client()
    client._update_clients(rows, epoch=identity.monitor_epoch)
    assert client.ordered_roster_proof_for(identity) is None


def test_disconnect_and_rpc_reconnect_invalidate_prior_proof() -> None:
    client, identity = _proof_ready_client()
    client._update_clients([_client_row(0, "Host", 3)], epoch=identity.monitor_epoch)
    assert client.ordered_roster_proof_for(identity) is not None

    client._handle_notification(
        "jamulusclient/disconnected",
        {},
        epoch=identity.monitor_epoch,
    )
    assert client.ordered_roster_proof_for(identity) is None
    assert client._audio_connection_generation == 2

    client._handle_response(
        "jamulusclient/getClientInfo",
        {"connected": "yes"},
        epoch=identity.monitor_epoch,
    )
    client._update_clients([_client_row(0, "Host", 3)], epoch=identity.monitor_epoch)
    assert client.ordered_roster_proof_for(identity) is None


@pytest.mark.parametrize(
    "malformed_observation",
    (
        lambda client, epoch: client._update_clients(None, epoch=epoch),
        lambda client, epoch: client._handle_response(
            "jamulusclient/getClientList", None, epoch=epoch
        ),
        lambda client, epoch: client._handle_response(
            "jamulusclient/getClientList", {}, epoch=epoch
        ),
        lambda client, epoch: client._handle_notification(
            "jamulusclient/clientListReceived", [], epoch=epoch
        ),
        lambda client, epoch: client._handle_response(
            "jamulusclient/getClientInfo", None, epoch=epoch
        ),
        lambda client, epoch: client._handle_response(
            "jamulusclient/getClientInfo", {"connected": 1}, epoch=epoch
        ),
        lambda client, epoch: client._handle_notification(
            "jamulusclient/connected", [], epoch=epoch
        ),
    ),
)
def test_every_malformed_authoritative_shape_retires_prior_proof(
    malformed_observation,
) -> None:
    client, identity = _proof_ready_client()
    client._update_clients(
        [_client_row(0, "Host", 3)],
        epoch=identity.monitor_epoch,
    )
    assert client.ordered_roster_proof_for(identity) is not None

    malformed_observation(client, identity.monitor_epoch)

    assert client.ordered_roster_proof_for(identity) is None


@pytest.mark.parametrize(
    "method",
    ("jamulusclient/getClientList", "jamulusclient/getClientInfo"),
)
def test_authoritative_rpc_error_retires_prior_proof(method: str) -> None:
    client, identity = _proof_ready_client()
    client._update_clients(
        [_client_row(0, "Host", 3)],
        epoch=identity.monitor_epoch,
    )
    assert client.ordered_roster_proof_for(identity) is not None
    client._inflight[71] = (identity.monitor_epoch, method)

    client._dispatch_obj(
        {"jsonrpc": "2.0", "id": 71, "error": {"code": 1}},
        epoch=identity.monitor_epoch,
    )

    assert client.ordered_roster_proof_for(identity) is None


def test_old_roster_proof_expires_despite_healthy_unrelated_rpc_traffic() -> None:
    client, identity = _proof_ready_client()
    client._update_clients(
        [_client_row(0, "Host", 3)],
        epoch=identity.monitor_epoch,
    )
    proof = client.ordered_roster_proof_for(identity)
    assert proof is not None
    client._ordered_roster_proof = replace(
        proof,
        observed_at=time.monotonic() - ORDERED_ROSTER_PROOF_MAX_AGE_S - 1.0,
    )

    # Level/chat traffic keeps the general monitor heartbeat fresh, but is
    # not evidence that the ordered identity roster is unchanged.
    client._dispatch_obj(
        {
            "jsonrpc": "2.0",
            "method": "jamulusclient/channelLevelListReceived",
            "params": {"channelLevelList": [9]},
        },
        epoch=identity.monitor_epoch,
    )

    assert client.last_activity_age() < 1.0
    assert client.ordered_roster_proof_for(identity) is None


def test_common_profile_decoder_rejects_partial_server_shape() -> None:
    with pytest.raises(JamulusRosterIdentityError):
        server_common_profile({"id": 4, "name": "Alex"})
