"""Names come from fresh authenticated LAN readers, never enrollment inventory."""
from __future__ import annotations

from dataclasses import FrozenInstanceError, fields, replace
from threading import Event, Thread
from types import SimpleNamespace
from uuid import uuid4

import pytest

from core import session_transfer as transfer


@pytest.fixture
def registry(tmp_path):
    credentials = transfer.SessionCredentials.create()
    return transfer.EnrollmentRegistry(tmp_path / "registry", credentials)


def _enroll(registry, name, *, installation_id=None):
    return registry.enroll(
        installation_id or str(uuid4()), name,
        invite_token=registry.credentials.invite_token,
    )


@pytest.fixture
def peer(tmp_path, monkeypatch):
    credentials = transfer.SessionCredentials.create()
    root = tmp_path / "host"
    registry = transfer.EnrollmentRegistry(root, credentials)
    server = transfer.SessionPeerServer(
        "127.0.0.1", 0,
        registry=registry,
        control=transfer.SessionControlState(
            root, credentials.session_id, creator_profile_key="art",
        ),
        transfers=transfer.TransferStore(root, credentials.session_id),
    )
    clock = [100.0]
    monkeypatch.setattr(server, "_room_poll_clock", lambda: clock[0])
    stopped = False

    def stop():
        nonlocal stopped
        if not stopped:
            server.stop()
            stopped = True

    server.start()
    try:
        yield SimpleNamespace(
            server=server, registry=registry, clock=clock, stop=stop,
            client=transfer.SessionPeerClient(*server.address, credentials=credentials),
        )
    finally:
        stop()


@pytest.mark.requires_local_socket
def test_only_authenticated_state_readers_supply_room_names(peer):
    # Hosting enrolls a local identity too; neither that enrollment nor an
    # invitation accepted without a successful state poll proves a guest here.
    _enroll(peer.registry, "Local host")
    first = peer.client.enroll(str(uuid4()), "Mira")
    second = peer.client.enroll(str(uuid4()), "Jon")
    assert peer.server.room_connection_names().names == ()

    assert peer.client.state(first).creator_profile_key == "art"
    assert peer.server.room_connection_names().names == ("Mira",)
    peer.client.state(second)
    assert peer.server.room_connection_names().names == ("Jon", "Mira")


@pytest.mark.requires_local_socket
@pytest.mark.parametrize("age,expected", [
    (4.999, ("Mira",)), (5.0, ()), (-0.001, ()),
])
def test_room_names_use_the_existing_exact_five_second_reader_lease(peer, age, expected):
    enrolled = peer.client.enroll(str(uuid4()), "Mira")
    peer.client.state(enrolled)

    # Supplying now must use the same freshness rule as room_participants,
    # including rejecting a timestamp that is ahead of the current clock.
    assert peer.server.room_connection_names(now=100.0 + age).names == expected


@pytest.mark.requires_local_socket
def test_invalid_authentication_cannot_restore_expired_room_names(peer):
    enrolled = peer.client.enroll(str(uuid4()), "Mira")
    peer.client.state(enrolled)
    peer.clock[0] += 5.0
    assert peer.server.room_connection_names().names == ()
    invalid = replace(
        enrolled,
        participant_token=transfer.SessionCredentials.create().participant_token(
            enrolled.participant_id,
        ),
    )

    with pytest.raises(transfer.TransferAuthenticationError):
        peer.client.state(invalid)
    assert peer.server.room_connection_names().names == ()
    peer.client.state(enrolled)
    assert peer.server.room_connection_names().names == ("Mira",)


@pytest.mark.requires_local_socket
def test_distinct_artists_with_the_same_name_survive_repeated_polls(peer):
    first = peer.client.enroll(str(uuid4()), "Sam")
    second = peer.client.enroll(str(uuid4()), "Sam")
    assert first.participant_id != second.participant_id
    for _ in range(3):
        peer.client.state(first)
        peer.client.state(second)

    assert peer.server.room_connection_names().names == ("Sam", "Sam")
    peer.clock[0] += 4.0
    peer.client.state(second)
    peer.clock[0] += 1.0
    assert peer.server.room_connection_names().names == ("Sam",)


@pytest.mark.requires_local_socket
def test_renaming_a_fresh_enrollment_updates_its_name_without_duplicate_rows(peer):
    installation_id = str(uuid4())
    enrolled = peer.client.enroll(installation_id, "Mira")
    peer.client.state(enrolled)
    previous = peer.server.room_connection_names()

    renamed = peer.client.enroll(installation_id, "Mira Chen")

    assert renamed.participant_id == enrolled.participant_id
    assert peer.server.room_connection_names().names == ("Mira Chen",)
    assert previous.names == ("Mira",)


@pytest.mark.requires_local_socket
def test_stopped_server_drops_names_even_while_registry_keeps_enrollments(peer):
    enrolled = peer.client.enroll(str(uuid4()), "Mira")
    peer.client.state(enrolled)
    assert peer.server.room_connection_names().names == ("Mira",)

    peer.stop()

    assert peer.server.room_connection_names().names == ()
    assert peer.registry.room_connection_names(frozenset({enrolled.participant_id})).names == ("Mira",)


@pytest.mark.requires_local_socket
def test_room_name_projection_never_materializes_enrollment_bearers(peer, monkeypatch):
    enrolled = peer.client.enroll(str(uuid4()), "PRIVATE_ARTIST_NAME")
    peer.client.state(enrolled)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Name projection must not read bearer-bearing inventory")

    monkeypatch.setattr(peer.registry, "participants", forbidden)
    monkeypatch.setattr(transfer.SessionCredentials, "participant_token", forbidden)

    projection = peer.server.room_connection_names()
    assert projection.names == ("PRIVATE_ARTIST_NAME",)
    assert peer.registry.room_connection_names(frozenset({enrolled.participant_id})) == projection
    assert "PRIVATE_ARTIST_NAME" not in repr(projection)


def test_registry_projects_only_requested_known_ids_in_stable_name_order(registry):
    selected = [_enroll(registry, name) for name in ("zuri", "alex", "Alex", "Alex")]
    _enroll(registry, "Absent artist")
    requested = frozenset({str(uuid4()), *(item.participant_id for item in selected)})

    assert registry.room_connection_names(requested).names == ("Alex", "Alex", "alex", "zuri")
    assert registry.room_connection_names(frozenset({str(uuid4())})).names == ()
    assert registry.room_connection_names(frozenset()).names == ()


@pytest.mark.parametrize("raw,expected", [
    ("  Mira\tChen\n ", "Mira Chen"),
    ("\x00Ada\x1b\x7f", "Ada"),
    ("É" * 90, "É" * 80),
    (" \t\n\x00", "Musician"),
])
def test_room_names_preserve_existing_enrollment_name_normalization(registry, raw, expected):
    enrolled = _enroll(registry, raw)

    assert registry.room_connection_names(frozenset({enrolled.participant_id})).names == (expected,)


def test_room_name_snapshot_is_immutable_and_contains_no_private_identity_fields(registry):
    enrolled = _enroll(registry, "PRIVATE_ARTIST_NAME")
    projection = registry.room_connection_names(frozenset({enrolled.participant_id}))

    assert type(projection) is transfer.RoomConnectionNames
    assert [field.name for field in fields(projection)] == ["names"]
    assert projection.names == ("PRIVATE_ARTIST_NAME",)
    with pytest.raises(FrozenInstanceError):
        projection.names = ()
    diagnostic = repr(projection)
    private_values = (
        enrolled.display_name, enrolled.participant_id, enrolled.installation_id,
        enrolled.participant_token, registry.credentials.invite_token,
    )
    assert all(value not in diagnostic for value in private_values)


@pytest.mark.requires_local_socket
@pytest.mark.parametrize("reader", ["registry", "server"])
def test_name_reads_yield_while_another_thread_owns_the_enrollment_lock(peer, reader):
    enrolled = peer.client.enroll(str(uuid4()), "PRIVATE_BUSY_ARTIST")
    peer.client.state(enrolled)
    acquired, release, returned = Event(), Event(), Event()
    results, errors = [], []

    def hold_enrollment():
        # Enrollment holds this same lock through its durable save/fsync.
        # Reading names on the UI thread must not wait behind that writer.
        with peer.registry._lock:
            acquired.set()
            release.wait(timeout=5.0)

    def read_names():
        try:
            result = (
                peer.registry.room_connection_names(frozenset({enrolled.participant_id}))
                if reader == "registry" else peer.server.room_connection_names()
            )
            results.append(result)
        except BaseException as error:
            errors.append(error)
        finally:
            returned.set()

    holder = Thread(target=hold_enrollment, name="test-art-enrollment-writer", daemon=True)
    observer = Thread(target=read_names, name="test-art-name-reader", daemon=True)
    holder.start()
    observer_started = False
    try:
        assert acquired.wait(timeout=1.0), "The controlled enrollment lock was not acquired"
        observer.start()
        observer_started = True
        assert returned.wait(timeout=0.5), "Name projection blocked behind enrollment persistence"
        assert not errors
        assert results == [None]
    finally:
        release.set()
        holder.join(timeout=2.0)
        if observer_started:
            observer.join(timeout=2.0)
        assert not holder.is_alive()
        assert not observer.is_alive()

    recovered = peer.server.room_connection_names()
    assert recovered.names == ("PRIVATE_BUSY_ARTIST",)
    assert "PRIVATE_BUSY_ARTIST" not in repr(recovered)


@pytest.mark.requires_local_socket
def test_names_expiring_while_the_registry_is_read_are_not_returned_as_current(peer, monkeypatch):
    enrolled = peer.client.enroll(str(uuid4()), "PRIVATE_EXPIRED_ARTIST")
    peer.client.state(enrolled)
    original = peer.registry.room_connection_names

    def read_then_expire(participant_ids):
        snapshot = original(participant_ids)
        peer.clock[0] += 5.0
        return snapshot

    monkeypatch.setattr(peer.registry, "room_connection_names", read_then_expire)

    result = peer.server.room_connection_names()

    assert result is None or result.names == ()
    assert "PRIVATE_EXPIRED_ARTIST" not in repr(result)


@pytest.mark.requires_local_socket
def test_stopping_during_name_projection_cannot_return_a_retired_room(peer, monkeypatch):
    enrolled = peer.client.enroll(str(uuid4()), "PRIVATE_RETIRED_ARTIST")
    peer.client.state(enrolled)
    original = peer.registry.room_connection_names

    def read_then_stop(participant_ids):
        snapshot = original(participant_ids)
        peer.stop()
        return snapshot

    monkeypatch.setattr(peer.registry, "room_connection_names", read_then_stop)

    result = peer.server.room_connection_names()

    assert result is None or result.names == ()
    assert "PRIVATE_RETIRED_ARTIST" not in repr(result)
