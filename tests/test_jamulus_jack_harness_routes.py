from __future__ import annotations

from dataclasses import dataclass

import pytest

from tests.support.jamulus_jack_harness import (
    EXPECTED_VERSION_ENV,
    HarnessUnavailable,
    HarnessFailure,
    JackBoundary,
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
