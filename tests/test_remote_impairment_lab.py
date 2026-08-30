"""Adversarial checks for the rootless remote-network impairment lab."""

from __future__ import annotations

import errno
import json
import socket
import sys
import threading
import time

import pytest

from tests.support.remote_impairment_lab import (
    Direction,
    EventCategory,
    ImpairmentChannel,
    ImpairmentProfile,
    MAX_CHANNEL_ENDPOINTS,
    MAX_LAB_CHANNELS,
    MAX_LAB_ENDPOINTS,
    Metric,
    PathKind,
    PrivacySafeReport,
    REMOTE_IMPAIRMENT_REPORT_SCHEMA,
    ReasonCode,
    RemoteImpairmentLab,
    TrafficClass,
    VirtualClock,
    validate_report,
)


pytestmark = pytest.mark.requires_local_socket


def make_channel(
    profile: ImpairmentProfile,
    *,
    seed: int = 17,
) -> tuple[VirtualClock, PrivacySafeReport, ImpairmentChannel]:
    clock = VirtualClock()
    report = PrivacySafeReport(seed=seed, profile=profile, clock=clock)
    channel = ImpairmentChannel(
        profile=profile,
        seed=seed,
        clock=clock,
        report=report,
    )
    channel.register_endpoint("channel-001")
    return clock, report, channel


def delivery_signature(
    profile: ImpairmentProfile, seed: int
) -> tuple[tuple[object, ...], dict[str, object]]:
    clock, report, channel = make_channel(profile, seed=seed)
    submissions = []
    for index in range(80):
        result = channel.submit(
            index.to_bytes(2, "big"),
            traffic=TrafficClass.JAMULUS_DATAGRAM,
            path=PathKind.DIRECT,
            direction=Direction.TO_TARGET,
        )
        submissions.append((result.accepted, result.copies_scheduled, result.reason))
        clock.advance(0.0002)
    clock.advance(5)
    deliveries = channel.poll_due()
    signature = (
        tuple(submissions),
        tuple(
            (
                item.sequence,
                round(item.due_at, 8),
                item.duplicate,
                item.payload,
            )
            for item in deliveries
        ),
    )
    return signature, report.document()


def test_seeded_scheduler_and_report_are_exactly_reproducible() -> None:
    profile = ImpairmentProfile(
        latency_ms=12,
        jitter_ms=5,
        loss_rate=0.23,
        reorder_rate=0.19,
        duplicate_rate=0.17,
        reorder_hold_ms=8,
    )

    first_signature, first_report = delivery_signature(profile, 913)
    second_signature, second_report = delivery_signature(profile, 913)

    assert first_signature == second_signature
    assert first_report == second_report


def test_different_seeds_produce_different_loss_and_schedule_patterns() -> None:
    profile = ImpairmentProfile(
        latency_ms=7,
        jitter_ms=4,
        loss_rate=0.5,
        reorder_rate=0.4,
        duplicate_rate=0.3,
    )

    first, _ = delivery_signature(profile, 100)
    second, _ = delivery_signature(profile, 101)

    assert first != second


def test_queue_is_hard_bounded_and_pure_channel_starts_no_threads() -> None:
    profile = ImpairmentProfile(latency_ms=1_000, max_queue_packets=4)
    _, report, channel = make_channel(profile)
    before = {item.ident for item in threading.enumerate()}

    results = [
        channel.submit(
            bytes([index]),
            traffic=TrafficClass.CONTROL,
            path=PathKind.DIRECT,
        )
        for index in range(50)
    ]

    assert channel.pending_count == 4
    assert sum(result.accepted for result in results) == 4
    assert all(result.reason is ReasonCode.QUEUE_OVERFLOW for result in results[4:])
    assert {item.ident for item in threading.enumerate()} == before
    document = report.document()
    metrics = document["summary"]["metrics"]
    assert metrics[Metric.QUEUE_HIGH_WATER.value] == 4
    assert metrics[Metric.PACKETS_DROPPED.value] == 46
    categories = document["summary"]["category_counts"]
    assert categories[EventCategory.UNEXPECTED_TRANSPORT_LOSS.value] == 46
    assert categories[EventCategory.CONTROL_LOSS.value] == 46


def test_oversized_datagram_is_rejected_before_it_enters_the_queue() -> None:
    profile = ImpairmentProfile(max_datagram_bytes=8)
    _, report, channel = make_channel(profile)
    sentinel = b"payload-must-never-appear"

    result = channel.submit(
        sentinel,
        traffic=TrafficClass.JAMULUS_DATAGRAM,
        path=PathKind.DIRECT,
    )

    assert result.accepted is False
    assert result.reason is ReasonCode.OVERSIZED_DATAGRAM
    assert channel.pending_count == 0
    rendered = report.to_json()
    assert sentinel.decode() not in rendered
    assert EventCategory.JAMULUS_DATAGRAM_LOSS.value in rendered
    assert EventCategory.UNEXPECTED_TRANSPORT_LOSS.value in rendered


def test_temporary_blackhole_recovers_only_after_virtual_deadline() -> None:
    profile = ImpairmentProfile(latency_ms=0)
    clock, report, channel = make_channel(profile)
    channel.begin_blackhole(PathKind.DIRECT, duration_s=0.010)

    blocked = channel.submit(
        b"blocked",
        traffic=TrafficClass.JAMULUS_DATAGRAM,
        path=PathKind.DIRECT,
    )
    clock.advance(0.009)
    still_blocked = channel.submit(
        b"still-blocked",
        traffic=TrafficClass.JAMULUS_DATAGRAM,
        path=PathKind.DIRECT,
    )
    clock.advance(0.002)
    recovered = channel.submit(
        b"recovered",
        traffic=TrafficClass.JAMULUS_DATAGRAM,
        path=PathKind.DIRECT,
    )
    deliveries = channel.poll_due()

    assert blocked.reason is ReasonCode.BLACKHOLE
    assert still_blocked.reason is ReasonCode.BLACKHOLE
    assert recovered.accepted is True
    assert [item.payload for item in deliveries] == [b"recovered"]
    categories = report.document()["summary"]["category_counts"]
    assert categories[EventCategory.INJECTED_OUTAGE.value] >= 1
    assert categories[EventCategory.JAMULUS_DATAGRAM_LOSS.value] == 2
    assert categories[EventCategory.MEDIA_RECOVERY.value] == 1


def test_reorder_and_duplication_are_observable_without_wall_clock_sleep() -> None:
    profile = ImpairmentProfile(
        duplicate_rate=1.0,
        reorder_rate=0.0,
        reorder_hold_ms=10,
        duplicate_gap_ms=0.1,
    )
    clock, report, channel = make_channel(profile)
    channel.force_reorder_next()

    first = channel.submit(
        b"first",
        traffic=TrafficClass.JAMULUS_DATAGRAM,
        path=PathKind.DIRECT,
    )
    second = channel.submit(
        b"second",
        traffic=TrafficClass.JAMULUS_DATAGRAM,
        path=PathKind.DIRECT,
    )
    clock.advance(0.020)
    delivered = channel.poll_due()

    assert first.copies_scheduled == second.copies_scheduled == 2
    assert [item.payload for item in delivered] == [
        b"second",
        b"second",
        b"first",
        b"first",
    ]
    assert [item.duplicate for item in delivered] == [False, True, False, True]
    metrics = report.document()["summary"]["metrics"]
    assert metrics[Metric.PACKETS_REORDERED.value] == 1
    assert metrics[Metric.PACKETS_DUPLICATED.value] == 2


def test_bandwidth_limit_serializes_packets_at_a_deterministic_rate() -> None:
    profile = ImpairmentProfile(bandwidth_kbps=8.0)
    clock, _, channel = make_channel(profile)

    for _ in range(3):
        channel.submit(
            b"x" * 1_000,
            traffic=TrafficClass.CONTROL,
            path=PathKind.RELAY,
        )

    clock.advance(0.999)
    assert channel.poll_due() == ()
    clock.advance(0.001)
    assert len(channel.poll_due()) == 1
    clock.advance(1.0)
    assert len(channel.poll_due()) == 1
    clock.advance(1.0)
    assert len(channel.poll_due()) == 1


def test_direct_rejection_relay_failure_restart_and_address_change_are_injectable() -> (
    None
):
    profile = ImpairmentProfile(latency_ms=100)
    clock, report, channel = make_channel(profile)
    channel.reject_direct_path()
    direct = channel.submit(
        b"direct",
        traffic=TrafficClass.CONTROL,
        path=PathKind.DIRECT,
    )
    channel.allow_direct_path()
    relay_queued = channel.submit(
        b"old-relay",
        traffic=TrafficClass.CONTROL,
        path=PathKind.RELAY,
    )
    channel.fail_relay()
    relay_down = channel.submit(
        b"down",
        traffic=TrafficClass.CONTROL,
        path=PathKind.RELAY,
    )
    relay_generation = channel.restart_relay()
    relay_live = channel.submit(
        b"new-relay",
        traffic=TrafficClass.CONTROL,
        path=PathKind.RELAY,
    )
    address_generation = channel.change_address("channel-001")
    after_change = channel.submit(
        b"after-change",
        traffic=TrafficClass.CONTROL,
        path=PathKind.RELAY,
        generation=address_generation,
    )
    clock.advance(1)
    delivered = channel.poll_due()

    assert direct.reason is ReasonCode.DIRECT_REJECTED
    assert relay_queued.accepted is True
    assert relay_down.reason is ReasonCode.RELAY_FAILED
    assert relay_generation == 2
    assert relay_live.accepted is True
    assert address_generation == 2
    assert after_change.accepted is True
    assert [item.payload for item in delivered] == [b"after-change"]
    categories = report.document()["summary"]["category_counts"]
    assert categories[EventCategory.INJECTED_OUTAGE.value] >= 3
    assert categories[EventCategory.RECONNECT.value] == 2


def test_live_proxy_forwards_both_directions_on_isolated_loopback() -> None:
    with RemoteImpairmentLab(profile=ImpairmentProfile(), seed=77) as lab:
        target = lab.bind_udp_endpoint()
        proxy = lab.start_udp_proxy(
            target=target.local_address,
            traffic=TrafficClass.JAMULUS_DATAGRAM,
            path=PathKind.DIRECT,
        )
        client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        client.bind(("127.0.0.1", 0))
        client.settimeout(0.003)
        try:
            client.sendto(b"ping", proxy.local_address)
            payload, proxy_source = recv_with_short_polls(target.socket)
            assert payload == b"ping"

            target.socket.sendto(b"pong", proxy_source)
            response, _ = recv_with_short_polls(client)
            assert response == b"pong"
        finally:
            client.close()

    assert lab.active_thread_count == 0


def test_live_proxy_refuses_non_loopback_or_hostname_targets() -> None:
    with RemoteImpairmentLab() as lab:
        with pytest.raises(ValueError, match="remain on loopback"):
            lab.start_udp_proxy(target=("203.0.113.10", 22124))
        with pytest.raises(ValueError, match="numeric loopback"):
            lab.start_udp_proxy(target=("relay.example.invalid", 22124))

    rendered = lab.report_json()
    assert "203.0.113.10" not in rendered
    assert "relay.example.invalid" not in rendered


def test_live_proxy_blackhole_and_recovery_use_the_same_fault_channel() -> None:
    with RemoteImpairmentLab() as lab:
        target = lab.bind_udp_endpoint()
        proxy = lab.start_udp_proxy(target=target.local_address)
        client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        client.bind(("127.0.0.1", 0))
        try:
            proxy.channel.begin_blackhole(PathKind.DIRECT)
            client.sendto(b"blocked-live", proxy.local_address)
            assert_no_datagram_with_short_polls(target.socket)

            proxy.channel.end_blackhole(PathKind.DIRECT)
            client.sendto(b"recovered-live", proxy.local_address)
            payload, _ = recv_with_short_polls(target.socket)
            assert payload == b"recovered-live"
        finally:
            client.close()

    categories = json.loads(lab.report_json())["summary"]["category_counts"]
    assert categories[EventCategory.INJECTED_OUTAGE.value] >= 1
    assert categories[EventCategory.MEDIA_RECOVERY.value] == 1


def recv_with_short_polls(
    sock: socket.socket, *, timeout_s: float = 0.3
) -> tuple[bytes, tuple[object, ...]]:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            return sock.recvfrom(65_507)
        except socket.timeout:
            continue
    raise AssertionError("loopback datagram did not arrive within the test bound")


def assert_no_datagram_with_short_polls(
    sock: socket.socket, *, timeout_s: float = 0.030
) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            payload, _ = sock.recvfrom(65_507)
        except socket.timeout:
            continue
        raise AssertionError(
            f"blackholed datagram unexpectedly arrived ({len(payload)} B)"
        )


def assert_udp_address_released(
    address: tuple[object, ...], *, timeout_s: float = 0.5
) -> None:
    """Allow an in-flight Linux recvfrom poll to release its closed socket."""
    deadline = time.monotonic() + timeout_s
    last_error: OSError | None = None
    while True:
        rebound = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            rebound.bind(address)
            return
        except OSError as exc:
            if exc.errno != errno.EADDRINUSE:
                raise
            last_error = exc
        finally:
            rebound.close()
        if time.monotonic() >= deadline:
            raise AssertionError(
                "rotated UDP address was not released within the test bound"
            ) from last_error
        time.sleep(0.003)


def test_proxy_and_process_counts_are_bounded_before_allocation() -> None:
    lab = RemoteImpairmentLab(max_proxies=1, max_processes=1)
    endpoint = lab.bind_udp_endpoint()
    lab.start_udp_proxy(target=endpoint.local_address)
    with pytest.raises(RuntimeError, match="proxy bound"):
        lab.start_udp_proxy(target=endpoint.local_address)
    process = lab.start_process(
        [sys.executable, "-c", "import threading; threading.Event().wait()"]
    )
    with pytest.raises(RuntimeError, match="process bound"):
        lab.start_process([sys.executable, "-c", "pass"])

    assert lab.active_thread_count == 1
    assert lab.running_process_count == 1
    lab.close()
    assert process.process.poll() is not None
    assert lab.active_thread_count == 0


def test_cleanup_releases_udp_ports_and_owned_process_without_leaks() -> None:
    lab = RemoteImpairmentLab(seed=88)
    endpoint = lab.bind_udp_endpoint()
    proxy = lab.start_udp_proxy(target=endpoint.local_address)
    endpoint_address = endpoint.local_address
    proxy_address = proxy.local_address
    tracked = lab.start_process(
        [sys.executable, "-c", "import threading; threading.Event().wait()"]
    )

    lab.close()

    assert tracked.process.poll() is not None
    assert lab.active_thread_count == 0
    for address in (endpoint_address, proxy_address):
        rebound = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            rebound.bind(address)
        finally:
            rebound.close()
    categories = json.loads(lab.report_json())["summary"]["category_counts"]
    assert categories[EventCategory.CLEANUP.value] == 1


def test_proxy_address_rotation_changes_generation_and_releases_old_port() -> None:
    with RemoteImpairmentLab() as lab:
        target = lab.bind_udp_endpoint()
        proxy = lab.start_udp_proxy(target=target.local_address)
        old_address = proxy.local_address
        new_address = proxy.rotate_address()

        assert new_address != old_address
        assert proxy.generation == 2
        assert_udp_address_released(old_address)


def test_channel_and_endpoint_ownership_have_aggregate_hard_bounds() -> None:
    lab = RemoteImpairmentLab()
    try:
        for _ in range(MAX_LAB_CHANNELS):
            lab.new_channel()
        with pytest.raises(RuntimeError, match="channel hard bound"):
            lab.new_channel()

        for _ in range(MAX_LAB_ENDPOINTS):
            lab.bind_udp_endpoint()
        with pytest.raises(RuntimeError, match="endpoint hard bound"):
            lab.bind_udp_endpoint()
    finally:
        lab.close()


def test_each_channel_has_a_bounded_default_address_identity() -> None:
    with RemoteImpairmentLab(clock=VirtualClock()) as lab:
        first = lab.new_channel()
        second = lab.new_channel()

        assert first.default_endpoint == "channel-001"
        assert second.default_endpoint == "channel-002"
        assert second.submit(
            b"uses-second-default",
            traffic=TrafficClass.CONTROL,
            path=PathKind.DIRECT,
        ).accepted
        assert second.generation("channel-002") == 1
        for index in range(2, MAX_CHANNEL_ENDPOINTS + 1):
            second.register_endpoint(f"peer-{index:03d}")
        with pytest.raises(RuntimeError, match="endpoint hard bound"):
            second.register_endpoint("peer-overflow")


def test_report_is_bounded_schema_valid_and_cannot_contain_payload_or_raw_ip() -> None:
    profile = ImpairmentProfile(max_report_events=5)
    _, report, channel = make_channel(profile, seed=1234)
    channel.reject_direct_path()
    sentinel = b"secret-audio-at-203.0.113.77"
    for _ in range(20):
        channel.submit(
            sentinel,
            traffic=TrafficClass.JAMULUS_DATAGRAM,
            path=PathKind.DIRECT,
        )

    document = report.document()
    rendered = report.to_json()

    validate_report(document, max_events=profile.max_report_events)
    assert len(document["events"]) == 5
    assert document["events_truncated"] > 0
    assert "secret-audio" not in rendered
    assert "203.0.113.77" not in rendered
    assert "127.0.0.1" not in rendered
    assert all("payload" not in event for event in document["events"])
    assert REMOTE_IMPAIRMENT_REPORT_SCHEMA["additionalProperties"] is False
    endpoint_schema = REMOTE_IMPAIRMENT_REPORT_SCHEMA["properties"]["events"]["items"][
        "properties"
    ]["endpoint"]
    assert endpoint_schema["pattern"].startswith("^")
    assert "\\Z" not in endpoint_schema["pattern"]
    poisoned = json.loads(rendered)
    poisoned["profile"]["latency_ms"] = "203.0.113.77"
    with pytest.raises(ValueError, match="profile value"):
        validate_report(poisoned, max_events=profile.max_report_events)
    with pytest.raises(ValueError, match="opaque token"):
        report.event(
            EventCategory.RECONNECT,
            ReasonCode.ADDRESS_CHANGED,
            endpoint="203.0.113.77",
        )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"loss_rate": -0.1},
        {"duplicate_rate": 1.1},
        {"bandwidth_kbps": 0},
        {"max_datagram_bytes": 65_508},
        {"max_queue_packets": 8_193},
        {"max_report_events": 4_097},
    ],
)
def test_profile_rejects_unbounded_or_impossible_values(
    kwargs: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        ImpairmentProfile(**kwargs)


def test_scheduled_datagram_repr_does_not_expose_payload_or_route() -> None:
    profile = ImpairmentProfile(latency_ms=1)
    _, _, channel = make_channel(profile)
    channel.submit(
        b"private-payload",
        traffic=TrafficClass.CONTROL,
        path=PathKind.DIRECT,
        route=("203.0.113.90", 3478),
    )
    item = channel._pending[0][2]  # adversarial inspection of the lab internals

    assert "private-payload" not in repr(item)
    assert "203.0.113.90" not in repr(item)
