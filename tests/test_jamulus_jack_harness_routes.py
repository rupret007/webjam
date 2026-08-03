from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from core.jamulus_roster_identity import (
    JamulusCommonProfile,
    ordered_common_roster_digest,
)
from tests.support.jamulus_jack_harness import (
    EXPECTED_VERSION_ENV,
    HarnessFailure,
    HarnessUnavailable,
    JackBoundary,
    JamulusJackHarness,
    expected_jamulus_version_from_environment,
)


@dataclass(frozen=True)
class _Port:
    name: str


class _Process:
    name = "jamulus-test"

    def __init__(self) -> None:
        self.health_checks = 0

    def ensure_running(self) -> None:
        self.health_checks += 1

    @staticmethod
    def tail() -> str:
        return "process-tail"


class _JackClient:
    def __init__(self, *, converge: bool) -> None:
        self.converge = converge
        self.routes: dict[str, _Port] = {}
        self.snapshot = 0

    def connect(self, source: _Port, target: _Port) -> None:
        self.routes[source.name] = target

    def get_all_connections(self, source: _Port) -> list[_Port]:
        self.snapshot += 1
        if self.converge and self.snapshot > 4:
            return [self.routes[source.name]]
        return []


class _ServerRpc:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows

    def call(self, method: str, params: dict[str, object]) -> dict[str, object]:
        assert method == "jamulusserver/getClients"
        assert params == {}
        return {
            "result": {
                "connections": len(self.rows),
                "clients": self.rows,
            }
        }


def _boundary(*, converge: bool) -> JackBoundary:
    boundary = JackBoundary.__new__(JackBoundary)
    boundary.client = _JackClient(converge=converge)
    boundary._source_a = (_Port("a-left"), _Port("a-right"))
    boundary._source_b = (_Port("b-left"), _Port("b-right"))
    boundary._sink_a = (_Port("a-in-left"), _Port("a-in-right"))
    boundary._sink_b = (_Port("b-in-left"), _Port("b-in-right"))
    return boundary


def _jamulus_ports() -> dict[str, _Port]:
    return {
        "input left": _Port("jamulus-input-left"),
        "input right": _Port("jamulus-input-right"),
        "output left": _Port("jamulus-output-left"),
        "output right": _Port("jamulus-output-right"),
    }


def test_expected_jamulus_version_defaults_to_3_12_2(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(EXPECTED_VERSION_ENV, raising=False)

    assert expected_jamulus_version_from_environment() == "3.12.2"


@pytest.mark.parametrize("version", ("3.12.2", "3.12.3"))
def test_expected_jamulus_version_accepts_supported_canonical_values(
    monkeypatch: pytest.MonkeyPatch,
    version: str,
) -> None:
    monkeypatch.setenv(EXPECTED_VERSION_ENV, version)

    assert expected_jamulus_version_from_environment() == version


@pytest.mark.parametrize("version", ("", "3.12.3 ", "3.13.0"))
def test_expected_jamulus_version_rejects_invalid_values(
    monkeypatch: pytest.MonkeyPatch,
    version: str,
) -> None:
    monkeypatch.setenv(EXPECTED_VERSION_ENV, version)

    with pytest.raises(HarnessUnavailable, match=EXPECTED_VERSION_ENV):
        expected_jamulus_version_from_environment()


def test_route_client_waits_for_jack_graph_convergence() -> None:
    process = _Process()
    _boundary(converge=True).route_client(
        0,
        _jamulus_ports(),
        process=process,
        timeout_s=0.2,
        poll_interval_s=0.001,
    )
    assert process.health_checks >= 2


def test_route_client_reports_persistent_missing_routes() -> None:
    process = _Process()
    with pytest.raises(HarnessFailure, match="routes did not converge") as error:
        _boundary(converge=False).route_client(
            0,
            _jamulus_ports(),
            process=process,
            timeout_s=0.005,
            poll_interval_s=0.001,
        )
    assert "process-tail" in str(error.value)
    assert "a-left -> jamulus-input-left" in str(error.value)


def _server_identity_row(
    *, address: str = "127.0.0.1:41000", channels: int = 1
) -> dict[str, object]:
    return {
        "id": 7,
        "name": "presentation only",
        "instrumentCode": 3,
        "city": "Chicago",
        "skillLevelCode": 2,
        "channels": channels,
        "address": address,
    }


def test_server_identity_transaction_digest_privately_covers_endpoint() -> None:
    harness = JamulusJackHarness.__new__(JamulusJackHarness)
    harness.client_rpc_endpoints = [object()]
    rpc = _ServerRpc([_server_identity_row()])
    harness.server_rpc = rpc

    first = harness._server_identity_roster()
    rpc.rows = [_server_identity_row(address="127.0.0.1:41001")]
    second = harness._server_identity_roster()
    rpc.rows = [_server_identity_row(channels=2)]
    third = harness._server_identity_roster()

    assert first.profiles == second.profiles == third.profiles
    assert first.channel_ids == second.channel_ids == third.channel_ids == (7,)
    assert first.transaction_digest != second.transaction_digest
    assert first.transaction_digest != third.transaction_digest
    assert "127.0.0.1" not in repr(first)
    assert "41000" not in repr(first)


@pytest.mark.parametrize(
    ("address", "channels"),
    (("127.0.0.1:not-a-port", 1), ("203.0.113.9:41000", 1), ("127.0.0.1:41000", 3)),
)
def test_server_identity_roster_rejects_unsafe_private_metadata_without_echo(
    address: str,
    channels: int,
) -> None:
    harness = JamulusJackHarness.__new__(JamulusJackHarness)
    harness.client_rpc_endpoints = [object()]
    harness.server_rpc = _ServerRpc(
        [_server_identity_row(address=address, channels=channels)]
    )

    with pytest.raises(HarnessFailure) as error:
        harness._server_identity_roster()

    assert address not in str(error.value)


def test_owned_roster_certification_maps_sparse_server_ids_only_by_ordinal() -> None:
    harness = JamulusJackHarness.__new__(JamulusJackHarness)
    harness.client_rpc_endpoints = [object(), object()]
    harness.client_processes = [object(), object()]
    profiles = (
        JamulusCommonProfile("first", 1, "", 0),
        JamulusCommonProfile("second", 2, "", 0),
    )
    snapshot = SimpleNamespace(
        profiles=profiles,
        channel_ids=(3, 11),
        transaction_digest="private-transaction-digest",
    )
    harness._server_identity_roster = lambda: snapshot
    self_ordinals = (1, 0)
    harness._owned_client_identity_roster = (
        lambda owned_index, _endpoint, _process: (
            profiles,
            self_ordinals[owned_index],
        )
    )

    certification = harness.certify_owned_client_roster_identity()

    assert certification.common_roster_digest == ordered_common_roster_digest(
        profiles
    )
    assert certification.server_channel_ids_by_ordinal == (3, 11)
    assert [
        (item.owned_client_index, item.self_ordinal, item.server_channel_id)
        for item in certification.owned_clients
    ] == [(0, 1, 11), (1, 0, 3)]


def test_owned_roster_certification_rejects_private_endpoint_race() -> None:
    harness = JamulusJackHarness.__new__(JamulusJackHarness)
    harness.client_rpc_endpoints = [object()]
    harness.client_processes = [object()]
    profiles = (JamulusCommonProfile("musician", 1, "", 0),)
    snapshots = iter(
        (
            SimpleNamespace(
                profiles=profiles,
                channel_ids=(5,),
                transaction_digest="before-private-endpoint",
            ),
            SimpleNamespace(
                profiles=profiles,
                channel_ids=(5,),
                transaction_digest="after-private-endpoint",
            ),
        )
    )
    harness._server_identity_roster = lambda: next(snapshots)
    harness._owned_client_identity_roster = (
        lambda _owned_index, _endpoint, _process: (profiles, 0)
    )

    with pytest.raises(HarnessFailure, match="changed during challenge"):
        harness.certify_owned_client_roster_identity()
