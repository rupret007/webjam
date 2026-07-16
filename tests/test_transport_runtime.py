from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import stat
import textwrap
import threading
import time

import pytest

from core.remote_invitation import issue_remote_invitation
import services.transport_runtime as transport_runtime
from services.transport_runtime import (
    TransportLaunchError,
    TransportProcess,
    TransportProcessError,
    TransportProtocolError,
    TransportTimeoutError,
    _validate_binary_architecture,
    _validated_binary,
    parse_transport_event,
)


HOST_PIN = bytes(range(1, 33))
HOST_PIN_TEXT = base64.urlsafe_b64encode(HOST_PIN).rstrip(b"=").decode("ascii")


def _invitation(*, host_pin: bytes = HOST_PIN):
    return issue_remote_invitation(
        "reference-local",
        allowed_profiles=frozenset({"reference-local"}),
        host_spki_sha256=host_pin,
    ).invitation


def _sidecar(tmp_path: Path, *, behavior: str = "normal") -> Path:
    script = tmp_path / "webjam-fabric-test"
    body = f'''\
#!/usr/bin/python3
import json
import os
import sys
import time

BEHAVIOR = {behavior!r}
HOST_PIN = {HOST_PIN_TEXT!r}
COMMAND_LOG = {str(script.with_suffix(".commands"))!r}
if len(sys.argv) != 1 or "WEBJAM_ENROLLMENT_CAPABILITY" in os.environ:
    raise SystemExit(90)
if BEHAVIOR == "oversized":
    sys.stdout.buffer.write(b"x" * 5000 + b"\\n")
    sys.stdout.buffer.flush()
    time.sleep(5)
    raise SystemExit(0)
if BEHAVIOR == "duplicate":
    sys.stdout.write('{{"version":1,"id":0,"type":"ready","type":"ready","code":"ok","state":"idle","build":"test-build"}}\\n')
    sys.stdout.flush()
    time.sleep(5)
    raise SystemExit(0)
if BEHAVIOR == "delayed_ready":
    time.sleep(0.2)
if BEHAVIOR == "never_ready":
    for line in sys.stdin.buffer:
        with open(COMMAND_LOG, "ab") as command_log:
            command_log.write(line)
    raise SystemExit(0)
sys.stdout.write(json.dumps({{"version":1,"id":0,"type":"ready","code":"ok","state":"idle","build":"test-build"}}, separators=(",", ":")) + "\\n")
sys.stdout.flush()
last = None
for line in sys.stdin:
    command = json.loads(line)
    if BEHAVIOR == "hang":
        time.sleep(10)
        continue
    event = {{"version":1,"id":command["id"],"code":"ok"}}
    if command["type"] == "hello":
        event.update(type="hello", state="idle", build="test-build")
    elif command["type"] == "prepare_host":
        if set(command) != {{"version", "id", "type"}}:
            raise SystemExit(91)
        event.update(type="host_prepared", state="identity_ready", host_spki_sha256=HOST_PIN)
    elif command["type"] == "open_peer":
        required = {{"version", "id", "type", "mode", "generation", "profile_id", "session_reference", "invite_reference", "enrollment_capability", "expires_at_unix"}}
        required.add("target_port" if command["mode"] == "host" else "host_spki_sha256")
        if set(command) != required or command["profile_id"] != "reference-local":
            raise SystemExit(92)
        for key, size in (("session_reference", 22), ("invite_reference", 22), ("enrollment_capability", 43)):
            if len(command[key]) != size or "=" in command[key]:
                raise SystemExit(93)
        if command["mode"] == "guest" and command["host_spki_sha256"] != HOST_PIN:
            raise SystemExit(94)
        last = command["mode"], command["profile_id"], command["generation"]
        if last[0] == "host":
            event.update(type="host_registered", state="host_waiting", mode=last[0], profile_id=last[1], generation=last[2], loopback_port=43123)
        else:
            event.update(type="peer_connected", state="connected", mode=last[0], profile_id=last[1], generation=last[2], loopback_port=43123)
    elif command["type"] == "close_peer":
        if last is None:
            event.update(type="error", code="peer_not_open", state="idle")
        else:
            event.update(type="peer_closed", state="closed", mode=last[0], profile_id=last[1], generation=last[2])
    elif command["type"] == "shutdown":
        event.update(type="stopped", state="stopped")
    else:
        event.update(type="error", code="protocol_violation", state="failed")
    sys.stdout.write(json.dumps(event, separators=(",", ":")) + "\\n")
    sys.stdout.flush()
    if command["type"] == "shutdown":
        break
'''
    script.write_text(textwrap.dedent(body), encoding="utf-8")
    script.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    return script


def test_transport_process_has_constant_argv_empty_env_and_clean_lifecycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WEBJAM_ENROLLMENT_CAPABILITY", "must-not-reach-child")
    process = TransportProcess(_sidecar(tmp_path), expected_build="test-build")

    ready = process.start()
    assert ready.build == "test-build"
    assert process.running
    assert process.process_id is not None
    assert process.hello().event_type == "hello"
    invitation = _invitation()
    opened = process.open_guest(invitation, generation=7)
    assert opened.mode == "guest"
    assert opened.profile_id == "reference-local"
    assert opened.loopback_port == 43123
    assert process.close_peer().event_type == "peer_closed"
    process.stop()

    assert not process.running
    assert process.process_id is None
    assert tuple(event.event_type for event in process.timeline) == (
        "ready",
        "hello",
        "peer_connected",
        "peer_closed",
        "stopped",
    )


def test_host_command_is_bounded_and_requires_valid_port_and_generation(
    tmp_path: Path,
) -> None:
    with TransportProcess(_sidecar(tmp_path), expected_build="test-build") as process:
        pin = process.prepare_host()
        assert pin == HOST_PIN
        invitation = _invitation(host_pin=pin)
        opened = process.open_host(
            invitation,
            target_port=22124,
            generation=2**32 - 1,
        )
        assert opened.mode == "host"
        assert opened.event_type == "host_registered"
        assert opened.generation == 2**32 - 1
        with pytest.raises(ValueError):
            process.open_host(invitation, target_port=0, generation=1)
        with pytest.raises(ValueError):
            process.open_guest(invitation, generation=True)


def test_build_mismatch_fails_closed_and_reaps_child(tmp_path: Path) -> None:
    process = TransportProcess(_sidecar(tmp_path), expected_build="other-build")
    with pytest.raises(TransportLaunchError, match="does not match"):
        process.start()
    assert not process.running
    assert process.process_id is None


def test_delayed_valid_ready_event_within_start_budget_is_accepted(
    tmp_path: Path,
) -> None:
    process = TransportProcess(
        _sidecar(tmp_path, behavior="delayed_ready"),
        expected_build="test-build",
        start_timeout=0.75,
        stop_timeout=0.25,
    )

    ready = process.start()

    assert ready.event_type == "ready"
    assert ready.build == "test-build"
    assert process.running
    process.stop()
    assert not process.running


def test_ready_budget_expiry_remains_fail_closed_and_reaps_delayed_child(
    tmp_path: Path,
) -> None:
    process = TransportProcess(
        _sidecar(tmp_path, behavior="delayed_ready"),
        expected_build="test-build",
        start_timeout=0.05,
        stop_timeout=0.25,
    )

    with pytest.raises(TransportTimeoutError, match="did not become ready"):
        process.start()

    assert not process.running
    assert process.process_id is None


def test_stop_cancels_pre_ready_start_without_sending_protocol_requests(
    tmp_path: Path,
) -> None:
    binary = _sidecar(tmp_path, behavior="never_ready")
    command_log = binary.with_suffix(".commands")
    process = TransportProcess(
        binary,
        expected_build="test-build",
        start_timeout=5,
        stop_timeout=0.25,
    )
    failures = []
    request_types = []
    request = process._request

    def record_request(command_type: str, **kwargs):
        request_types.append(command_type)
        return request(command_type, **kwargs)

    process._request = record_request

    def start_process() -> None:
        try:
            process.start()
        except Exception as exc:  # noqa: BLE001 - assertion captures type below
            failures.append(exc)

    worker = threading.Thread(target=start_process)
    worker.start()
    deadline = time.monotonic() + 1
    while process.process_id is None and time.monotonic() < deadline:
        time.sleep(0.005)
    assert process.process_id is not None
    child = process._process
    assert child is not None

    started = time.monotonic()
    process.stop()
    worker.join(1)

    assert time.monotonic() - started < 1
    assert not worker.is_alive()
    assert failures and isinstance(failures[0], TransportProcessError)
    assert child.poll() is not None
    assert process.process_id is None
    assert request_types == []
    assert not command_log.exists()


def test_stop_waits_for_a_cancelled_spawn_to_be_reaped(
    tmp_path: Path,
    monkeypatch,
) -> None:
    binary = _sidecar(tmp_path, behavior="never_ready")
    command_log = binary.with_suffix(".commands")
    process = TransportProcess(
        binary,
        expected_build="test-build",
        start_timeout=5,
        stop_timeout=0.5,
    )
    real_popen = transport_runtime.subprocess.Popen
    popen_entered = threading.Event()
    release_popen = threading.Event()
    children = []
    failures = []

    def delayed_popen(*args, **kwargs):
        popen_entered.set()
        if not release_popen.wait(2):
            raise RuntimeError("test did not release Popen")
        child = real_popen(*args, **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(transport_runtime.subprocess, "Popen", delayed_popen)

    def start_process() -> None:
        try:
            process.start()
        except Exception as exc:  # noqa: BLE001 - assertion captures type below
            failures.append(exc)

    worker = threading.Thread(target=start_process)
    worker.start()
    assert popen_entered.wait(1)

    stop_done = threading.Event()
    stopper = threading.Thread(target=lambda: (process.stop(), stop_done.set()))
    stopper.start()
    assert not stop_done.wait(0.05)
    release_popen.set()
    worker.join(1)
    stopper.join(1)

    assert stop_done.is_set()
    assert not worker.is_alive()
    assert not stopper.is_alive()
    assert failures and isinstance(failures[0], TransportProcessError)
    assert len(children) == 1
    assert children[0].poll() is not None
    assert process.process_id is None
    assert not command_log.exists()


@pytest.mark.parametrize("behavior", ["oversized", "duplicate"])
def test_malformed_or_oversized_child_output_fails_closed(
    tmp_path: Path, behavior: str
) -> None:
    process = TransportProcess(
        _sidecar(tmp_path, behavior=behavior),
        expected_build="test-build",
        start_timeout=0.5,
        stop_timeout=0.5,
    )
    with pytest.raises(TransportProtocolError):
        process.start()
    assert not process.running


def test_command_timeout_is_bounded_and_process_can_be_reaped(tmp_path: Path) -> None:
    process = TransportProcess(
        _sidecar(tmp_path, behavior="hang"),
        expected_build="test-build",
        command_timeout=0.15,
        stop_timeout=0.15,
    )
    process.start()
    started = time.monotonic()
    with pytest.raises(TransportTimeoutError):
        process.hello()
    assert time.monotonic() - started < 1.0
    process.stop()
    assert not process.running


def test_stop_interrupts_an_inflight_enrollment_without_waiting_for_its_timeout(
    tmp_path: Path,
) -> None:
    process = TransportProcess(
        _sidecar(tmp_path, behavior="hang"),
        expected_build="test-build",
        command_timeout=5,
        stop_timeout=0.15,
    )
    process.start()
    failures = []
    def enroll() -> None:
        try:
            process.open_guest(_invitation(), generation=1)
        except Exception as exc:  # noqa: BLE001 - assertion captures type below
            failures.append(exc)

    worker = threading.Thread(target=enroll)
    worker.start()
    time.sleep(0.05)
    started = time.monotonic()
    process.stop()
    worker.join(1)

    assert time.monotonic() - started < 1
    assert not worker.is_alive()
    assert failures and isinstance(failures[0], TransportProcessError)


def test_binary_must_be_absolute_non_symlink_and_not_group_writable(
    tmp_path: Path,
) -> None:
    relative = Path("webjam-fabric")
    with pytest.raises(TransportLaunchError):
        TransportProcess(relative, expected_build="test-build").start()

    target = _sidecar(tmp_path)
    link = tmp_path / "linked-fabric"
    link.symlink_to(target)
    with pytest.raises(TransportLaunchError):
        TransportProcess(link, expected_build="test-build").start()

    target.chmod(0o770)
    with pytest.raises(TransportLaunchError):
        TransportProcess(target, expected_build="test-build").start()


def test_binary_manifest_hash_is_verified_before_process_launch(tmp_path: Path) -> None:
    target = _sidecar(tmp_path)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()

    assert _validated_binary(target, expected_sha256=digest) == target.resolve()
    with pytest.raises(TransportLaunchError, match="not installed safely"):
        _validated_binary(target, expected_sha256="0" * 64)


def test_linux_elf_machine_is_verified_before_launch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binary = tmp_path / "webjam-fabric"
    header = bytearray(64)
    header[:4] = b"\x7fELF"
    header[4] = 2
    header[5] = 1
    header[18:20] = (0x3E).to_bytes(2, "little")
    binary.write_bytes(header)
    monkeypatch.setattr("services.transport_runtime.sys.platform", "linux")

    _validate_binary_architecture(binary, "x86_64")
    with pytest.raises(TransportLaunchError, match="not installed safely"):
        _validate_binary_architecture(binary, "arm64")

    header[18:20] = (0xB7).to_bytes(2, "little")
    binary.write_bytes(header)
    _validate_binary_architecture(binary, "arm64")


def test_event_parser_rejects_unknown_duplicate_noncanonical_and_bad_versions() -> None:
    valid = {
        "version": 1,
        "id": 0,
        "type": "ready",
        "code": "ok",
        "state": "idle",
        "build": "abc123",
    }
    assert parse_transport_event(json.dumps(valid).encode() + b"\n").build == "abc123"
    with pytest.raises(TransportProtocolError):
        parse_transport_event(b'{"version":1,"version":1,"id":0,"type":"ready"}\n')
    with pytest.raises(TransportProtocolError):
        parse_transport_event(json.dumps({**valid, "secret": "leak"}).encode() + b"\n")
    with pytest.raises(TransportProtocolError, match="not compatible"):
        parse_transport_event(json.dumps({**valid, "version": 2}).encode() + b"\n")
    with pytest.raises(TransportProtocolError):
        parse_transport_event(json.dumps(valid).encode())
    with pytest.raises(TransportProtocolError):
        parse_transport_event(
            json.dumps({**valid, "type": ["ready"]}).encode() + b"\n"
        )
    with pytest.raises(TransportProtocolError):
        parse_transport_event(
            json.dumps({**valid, "generation": 1}).encode() + b"\n"
        )


def test_peer_connected_schema_distinguishes_guest_response_and_host_update() -> None:
    base = {
        "version": 1,
        "type": "peer_connected",
        "code": "ok",
        "state": "connected",
        "profile_id": "reference-local",
        "generation": 2,
        "loopback_port": 43123,
    }
    guest = parse_transport_event(
        json.dumps({**base, "id": 7, "mode": "guest"}).encode() + b"\n"
    )
    host = parse_transport_event(
        json.dumps({**base, "id": 0, "mode": "host"}).encode() + b"\n"
    )
    assert guest.event_id == 7
    assert host.event_id == 0
    for invalid in (
        {**base, "id": 0, "mode": "guest"},
        {**base, "id": 7, "mode": "host"},
    ):
        with pytest.raises(TransportProtocolError):
            parse_transport_event(json.dumps(invalid).encode() + b"\n")
