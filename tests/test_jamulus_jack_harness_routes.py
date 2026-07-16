from __future__ import annotations

from dataclasses import dataclass

import pytest

from tests.support.jamulus_jack_harness import (
    HarnessFailure,
    JackBoundary,
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
