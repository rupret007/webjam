from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import stat
import subprocess
import sys
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


def test_transport_runtime_imports_without_typing_extensions() -> None:
    root = Path(__file__).resolve().parents[1]
    program = f"""
import importlib.abc
import sys

sys.path.insert(0, {str(root)!r})

class DenyTypingExtensions(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "typing_extensions":
            raise ModuleNotFoundError("typing_extensions is intentionally unavailable")
        return None

sys.meta_path.insert(0, DenyTypingExtensions())
import services.transport_runtime
"""
    completed = subprocess.run(
        [sys.executable, "-I", "-c", program],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


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
    elif command["type"] == "send_help":
        if last is None or command.get("generation") != last[2]:
            event.update(type="error", code="help_not_ready", state="connected")
        elif set(command) != {{"version", "id", "type", "generation", "text"}}:
            raise SystemExit(95)
        else:
            event.update(type="help_accepted", state="connected", mode=last[0], profile_id=last[1], generation=last[2], request_id=command["id"])
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
    if command["type"] == "send_help" and event["type"] == "help_accepted":
        received = {{"version":1,"id":0,"type":"help_received","code":"ok","state":"connected","mode":last[0],"profile_id":last[1],"generation":last[2],"request_id":command["id"] + 100,"text":command["text"]}}
        delivered = {{"version":1,"id":0,"type":"help_delivered","code":"ok","state":"connected","mode":last[0],"profile_id":last[1],"generation":last[2],"request_id":command["id"]}}
        sys.stdout.write(json.dumps(received, separators=(",", ":"), ensure_ascii=False) + "\\n")
        sys.stdout.write(json.dumps(delivered, separators=(",", ":")) + "\\n")
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


def test_help_is_nfc_bounded_ephemeral_and_excluded_from_diagnostics(
    tmp_path: Path,
) -> None:
    observed = []
    with TransportProcess(
        _sidecar(tmp_path), expected_build="test-build", on_event=observed.append
    ) as process:
        process.open_guest(_invitation(), generation=7)
        accepted = process.send_help('Try cafe\u0301 "mix"', generation=7)
        assert accepted.event_type == "help_accepted"
        assert accepted.request_id == accepted.event_id

        deadline = time.monotonic() + 1
        while not any(event.event_type == "help_delivered" for event in observed):
            assert time.monotonic() < deadline
            time.sleep(0.01)

        received = next(
            event for event in observed if event.event_type == "help_received"
        )
        assert received.help_text == 'Try café "mix"'
        assert 'Try café "mix"' not in repr(received)
        assert not any(
            event.event_type.startswith("help_") for event in process.timeline
        )

        for invalid in ("", "   ", "<b>help</b>", "line\nbreak", "é" * 251):
            with pytest.raises(ValueError, match="bounded plain text"):
                process.send_help(invalid, generation=7)
        with pytest.raises(ValueError):
            process.send_help("help", generation=0)


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
        # The full GUI/native suite can briefly delay scheduling the reader
        # thread on shared CI runners. Keep the assertion specific to the
        # malformed protocol output rather than racing a synthetic 0.5 s
        # startup budget.
        start_timeout=2.0,
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


def test_help_event_schema_is_exact_canonical_and_redacted() -> None:
    common = {
        "version": 1,
        "id": 0,
        "code": "ok",
        "state": "connected",
        "mode": "guest",
        "profile_id": "reference-local",
        "generation": 7,
        "request_id": 23,
    }
    received = parse_transport_event(
        json.dumps(
            {**common, "type": "help_received", "text": "Try headphones — café"},
            ensure_ascii=False,
        ).encode("utf-8")
        + b"\n"
    )
    assert received.request_id == 23
    assert received.help_text == "Try headphones — café"
    assert "headphones" not in repr(received)

    accepted = parse_transport_event(
        json.dumps(
            {**common, "id": 23, "type": "help_accepted", "text": ""}
        ).encode()
        + b"\n"
    )
    assert accepted.request_id == accepted.event_id == 23

    delivered = parse_transport_event(
        json.dumps({**common, "type": "help_delivered"}).encode() + b"\n"
    )
    assert delivered.request_id == 23

    for invalid in (
        {**common, "type": "help_received", "text": "cafe\u0301"},
        {**common, "type": "help_received", "text": "<b>help</b>"},
        {**common, "type": "help_received", "text": "help", "id": 1},
        {**common, "type": "help_accepted", "id": 22},
        {**common, "type": "help_delivered", "text": "leak"},
    ):
        with pytest.raises(TransportProtocolError):
            parse_transport_event(
                json.dumps(invalid, ensure_ascii=False).encode("utf-8") + b"\n"
            )


def _room_event(state=None):
    from core.room_state import RoomState
    return {
        "version": 1, "id": 0, "type": "room_state_received", "code": "ok",
        "state": "connected", "mode": "guest", "profile_id": "reference-local",
        "generation": 3,
        "room_state": (state or RoomState(1, "art", "paint_along")).to_mapping(),
    }


def test_room_event_strict_nested_schema_direction_and_redaction() -> None:
    from core.room_state import RoomState
    from core.session_transfer import SharedCanvasSessionSnapshot
    state = RoomState(1, "art", "paint_along", shared_canvas=SharedCanvasSessionSnapshot(
        generation=1, shared=True, join_url="drawpile://studio.example/room?p=secret",
        server_label="Private studio", session_label="Private room",
    ))
    raw = _room_event(state)
    def encode(value):
        return (json.dumps(value, ensure_ascii=False) + "\n").encode()
    parsed = parse_transport_event(encode(raw))
    assert parsed.room_state == state
    assert "secret" not in repr(parsed)
    assert "Private studio" not in repr(parsed)
    for change in ({"mode": "host"}, {"generation": 0}, {"id": 3},
                   {"request_id": 4}, {"text": ""}, {"room_state": None}):
        with pytest.raises(TransportProtocolError):
            parse_transport_event(encode({**raw, **change}))
    raw["room_state"]["reference_video"]["private_path"] = "/private/file"
    with pytest.raises(TransportProtocolError):
        parse_transport_event(encode(raw))
    duplicate = encode(_room_event()).replace(b'"revision": 1', b'"revision": 1, "revision": 2')
    with pytest.raises(TransportProtocolError):
        parse_transport_event(duplicate)


def test_room_event_has_its_own_size_bound_without_expanding_other_events() -> None:
    encoded = (json.dumps(_room_event(), indent=80) + "\n").encode()
    assert transport_runtime.MAX_EVENT_LINE_BYTES < len(encoded) <= transport_runtime.MAX_ROOM_EVENT_LINE_BYTES
    assert parse_transport_event(encoded).room_state is not None
    ready = b'{"version":1,"id":0,"type":"ready","code":"ok","state":"idle","build":"test-build"}'
    with pytest.raises(TransportProtocolError):
        parse_transport_event(ready + b" " * 4100 + b"\n")
    with pytest.raises(TransportProtocolError):
        parse_transport_event(encoded + b" " * 12288 + b"\n")


def test_room_publish_command_and_receipt_are_typed_and_not_diagnostic(monkeypatch) -> None:
    from core.room_state import RoomState
    state = RoomState(2, "art", "talk_and_make")
    process = TransportProcess("/private/webjam-fabric", expected_build="test-build")
    calls = []
    def request(command, **kwargs):
        calls.append((command, kwargs))
        return transport_runtime.TransportEvent(
            event_id=4, request_id=4, event_type="room_state_accepted", code="ok",
            state="connected", mode="host", profile_id="reference-local", generation=3,
        )
    monkeypatch.setattr(process, "_request", request)
    assert process.publish_room_state(state, generation=3).event_type == "room_state_accepted"
    assert calls == [("publish_room_state", {"generation": 3, "room_state": state.to_mapping()})]
    with pytest.raises(ValueError):
        process.publish_room_state(state.to_mapping(), generation=3)
    with pytest.raises(ValueError):
        process.publish_room_state(state, generation=True)


def test_real_ipc_reader_keeps_room_payloads_out_of_process_timeline(tmp_path: Path) -> None:
    from core.room_state import RoomState
    from core.session_transfer import SharedCanvasSessionSnapshot
    state = RoomState(1, "art", "talk_and_make", shared_canvas=SharedCanvasSessionSnapshot(
        generation=1, shared=True, join_url="drawpile://studio.example/room?p=PRIVATE-CANVAS-TOKEN",
        server_label="Private studio", session_label="Private canvas",
    ))
    binary = _sidecar(tmp_path)
    program = binary.read_text()
    program = program.replace(
        '    elif command["type"] == "close_peer":',
        '''    elif command["type"] == "publish_room_state":
        if set(command) != {"version", "id", "type", "generation", "room_state"}:
            raise SystemExit(96)
        event.update(type="room_state_accepted", state="connected", mode="host",
                     profile_id=last[1], generation=last[2], request_id=command["id"])
    elif command["type"] == "close_peer":''',
    )
    room_mapping = json.dumps(state.to_mapping(), separators=(",", ":"))
    program = program.replace(
        '    if command["type"] == "shutdown":',
        f'''    if command["type"] == "open_peer" and command["mode"] == "guest":
        room_event = {{"version":1,"id":0,"type":"room_state_received","code":"ok", "state":"connected","mode":"guest","profile_id":last[1],"generation":last[2], "room_state":json.loads({room_mapping!r})}}
        sys.stdout.write(json.dumps(room_event, separators=(",", ":")) + "\\n")
        sys.stdout.flush()
    if command["type"] == "shutdown":''',
    )
    binary.write_text(program)
    received, arrived = [], threading.Event()
    def on_event(event):
        if event.event_type.startswith("room_state_"):
            received.append(event)
            arrived.set()
    with TransportProcess(binary, expected_build="test-build", on_event=on_event) as process:
        process.open_guest(_invitation(), generation=3)
        assert arrived.wait(1)
        assert received[0].room_state == state
        process.close_peer()
        process.prepare_host()
        process.open_host(_invitation(), generation=4, target_port=22124)
        assert process.publish_room_state(state, generation=4).event_type == "room_state_accepted"
        assert all(not event.event_type.startswith("room_state_") for event in process.timeline)
        assert "PRIVATE-CANVAS-TOKEN" not in repr(process.timeline)
        assert "Private canvas" not in repr(process)


def test_real_room_rate_limit_receipt_is_retryable_without_poisoning_process(tmp_path: Path) -> None:
    from core.room_state import RoomState
    from services.transport_runtime import TransportRoomRateLimitedError
    binary = _sidecar(tmp_path)
    program = binary.read_text().replace('last = None', 'last = None\nroom_attempts = 0')
    program = program.replace(
        '    elif command["type"] == "close_peer":',
        '''    elif command["type"] == "publish_room_state":
        room_attempts += 1
        if room_attempts == 1:
            event.update(type="error", code="room_state_rate_limited", state="connected")
        else:
            event.update(type="room_state_accepted", state="connected", mode="host",
                         profile_id=last[1], generation=last[2], request_id=command["id"])
    elif command["type"] == "close_peer":''',
    )
    binary.write_text(program)
    with TransportProcess(binary, expected_build="test-build") as process:
        process.prepare_host()
        process.open_host(_invitation(), generation=4, target_port=22124)
        with pytest.raises(TransportRoomRateLimitedError):
            process.publish_room_state(RoomState(1, "art", "talk_and_make"), generation=4)
        assert process.running
        assert process.publish_room_state(RoomState(2, "art", "talk_and_make"), generation=4).code == "ok"
        assert process.send_help("Still connected", generation=4).code == "ok"


@pytest.mark.parametrize("command,state", [
    ("send_help", "connected"), ("publish_room_state", "failed"),
])
def test_rate_limit_error_outside_connected_room_publish_is_not_retryable(command, state) -> None:
    from services.transport_runtime import TransportEvent, TransportRoomRateLimitedError
    process = TransportProcess("/private/webjam-fabric", expected_build="test-build")
    process._waiting.add(7)
    process._pending[7] = TransportEvent(
        event_id=7, event_type="error", code="room_state_rate_limited", state=state,
    )
    with pytest.raises(TransportProcessError) as error:
        process._wait_response(7, 0.1, command_type=command)
    assert not isinstance(error.value, TransportRoomRateLimitedError)


def test_failed_child_reap_retains_handle_and_requires_actual_retry(monkeypatch) -> None:
    class UnreapedChild:
        pid = 987654321
        stdin = None
        stdout = None
        reaped = False
        attempts = 0

        def poll(self):
            return 0 if self.reaped else None

        def terminate(self):
            self.attempts += 1
            raise OSError("blocked terminate")

        def kill(self):
            raise OSError("blocked kill")

    def cannot_kill(*args):
        raise OSError("blocked kill")

    monkeypatch.setattr(transport_runtime.os, "killpg", cannot_kill, raising=False)
    process = TransportProcess("/private/webjam-fabric", expected_build="test-build")
    child = UnreapedChild()
    process._process = child
    process._failure = TransportProcessError("The event channel failed.")
    with pytest.raises(TransportProcessError, match="did not stop"):
        process.stop()
    assert process.process_id == child.pid
    assert child.attempts == 1
    with pytest.raises(TransportProcessError, match="did not stop"):
        process.stop()
    assert child.attempts == 2
    child.reaped = True
    process.stop()
    assert process.process_id is None


@pytest.mark.parametrize("mode", ["host", "guest"])
@pytest.mark.parametrize("wrong_field,value", [
    ("generation", 1), ("profile_id", "different-profile"),
])
def test_enrollment_receipt_must_echo_local_generation_and_profile(monkeypatch, mode, wrong_field, value) -> None:
    from services.transport_runtime import TransportEvent
    process = TransportProcess("/private/webjam-fabric", expected_build="test-build")
    process._prepared_host_pin = HOST_PIN
    fields = dict(event_id=7, event_type="host_registered" if mode == "host" else "peer_connected",
                  code="ok", state="host_waiting" if mode == "host" else "connected",
                  mode=mode, profile_id="reference-local", generation=23, loopback_port=43123)
    fields[wrong_field] = value
    monkeypatch.setattr(process, "_request_enrollment", lambda *args, **kwargs: TransportEvent(**fields))
    with pytest.raises(TransportProtocolError, match="^The transport process sent invalid data.$"):
        if mode == "host":
            process.open_host(_invitation(), generation=23, target_port=22124)
        else:
            process.open_guest(_invitation(), generation=23)
    assert isinstance(process._failure, TransportProtocolError)
