"""The shared canvas projection carried by the private peer plane.

These tests drive the real ``SessionPeerServer`` over loopback, because the
whole point of putting the canvas on the peer plane is that a guest who was
handed one WebJam invitation -- including one who joins late -- receives the
Drawpile invitation too, rather than being sent a second link through a second
product.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from core.session_transfer import (
    EnrollmentRegistry,
    RecordingSignal,
    SessionControlState,
    SessionCredentials,
    SessionPeerClient,
    SessionPeerServer,
    SessionStateSnapshot,
    SharedCanvasSessionSnapshot,
    TransferStore,
)
from core.session_transfer_runtime import HostPeerSession
from core.shared_canvas import (
    HostCanvasProjection,
    SharedCanvasFollower,
    SharedCanvasFollowState,
)

WEB_INVITE = "https://drawpile.net/invites/pub.drawpile.net/kitchen-table?v1#hunter2"
NORMALIZED = "drawpile://pub.drawpile.net/kitchen-table?v1&p=hunter2"


class FakeLauncher:
    def __init__(self, *, installed: bool = True) -> None:
        self.installed = installed
        self.joined: list[str] = []

    def available(self) -> bool:
        return self.installed

    def open_host_page(self) -> None:  # pragma: no cover - not exercised here
        raise AssertionError("a guest never hosts")

    def open_canvas(self, invite) -> None:
        self.joined.append(invite.join_url)


def _shared(**changes: object) -> dict[str, object]:
    values: dict[str, object] = {
        "shared": True,
        "join_url": WEB_INVITE,
        "server_label": "pub.drawpile.net",
        "session_label": "kitchen-table",
    }
    values.update(changes)
    return values


# ---------------------------------------------------------------------------
# Wire schema
# ---------------------------------------------------------------------------


def test_the_canvas_projection_round_trips_and_normalizes_the_address() -> None:
    snapshot = SharedCanvasSessionSnapshot(generation=3, **_shared())

    assert snapshot.join_url == NORMALIZED
    assert SharedCanvasSessionSnapshot.from_mapping(snapshot.to_mapping()) == snapshot
    assert not hasattr(snapshot, "can_control")


def test_the_projection_satisfies_the_domain_layers_protocol() -> None:
    """The schema and the follower must agree without importing each other."""

    assert isinstance(SharedCanvasSessionSnapshot(**_shared()), HostCanvasProjection)


def test_the_projection_never_repeats_its_address() -> None:
    """A canvas address can embed a Drawpile session password."""

    text = repr(SharedCanvasSessionSnapshot(**_shared()))

    assert "hunter2" not in text
    assert "[redacted]" in text
    assert "kitchen-table" in text


@pytest.mark.parametrize(
    "changes",
    [
        {"join_url": "file:///etc/passwd"},
        {"join_url": "https://attacker.example/steal"},
        {"join_url": "not a url"},
        {"join_url": ""},
        {"generation": -1},
        {"generation": "3"},
        {"shared": "yes"},
        {"server_label": "x" * 200},
        {"session_label": "line\nbreak"},
    ],
)
def test_an_unbounded_or_contradictory_projection_is_refused(changes: dict) -> None:
    with pytest.raises(ValueError):
        SharedCanvasSessionSnapshot(**_shared(**changes))


def test_an_unshared_canvas_cannot_expose_canvas_facts() -> None:
    with pytest.raises(ValueError, match="cannot expose canvas facts"):
        SharedCanvasSessionSnapshot(shared=False, join_url=WEB_INVITE)
    with pytest.raises(ValueError, match="cannot expose canvas facts"):
        SharedCanvasSessionSnapshot(shared=False, session_label="kitchen-table")


@pytest.mark.parametrize(
    "payload",
    [
        {"schema": 2, "generation": 0, "shared": False},
        {"generation": 0, "shared": False},
        {"schema": 1, "shared": False},
        "not an object",
        7,
    ],
)
def test_an_incomplete_or_unknown_schema_payload_is_refused(payload: object) -> None:
    with pytest.raises(ValueError):
        SharedCanvasSessionSnapshot.from_mapping(payload)


def test_a_legacy_snapshot_without_a_canvas_defaults_to_unshared() -> None:
    assert SharedCanvasSessionSnapshot.from_mapping(None) == (
        SharedCanvasSessionSnapshot()
    )
    assert SessionStateSnapshot(
        session_id=str(uuid.uuid4()),
        generation=1,
        signal=RecordingSignal.IDLE,
    ).shared_canvas.shared is False


# ---------------------------------------------------------------------------
# Publication
# ---------------------------------------------------------------------------


def test_publication_is_idempotent_and_advances_only_on_real_change(
    tmp_path: Path,
) -> None:
    control = SessionControlState(tmp_path, str(uuid.uuid4()), creator_profile_key="art")

    first = control.publish_shared_canvas(**_shared())
    again = control.publish_shared_canvas(**_shared())
    moved = control.publish_shared_canvas(
        **_shared(join_url="drawpile://pub.drawpile.net/other", session_label="other")
    )

    assert first.generation == 1
    assert again is first
    assert moved.generation == 2


def test_withdrawing_publishes_no_address_at_all(tmp_path: Path) -> None:
    control = SessionControlState(tmp_path, str(uuid.uuid4()), creator_profile_key="art")
    control.publish_shared_canvas(**_shared())

    withdrawn = control.publish_shared_canvas(shared=False)

    assert withdrawn.shared is False
    assert withdrawn.join_url == ""
    assert withdrawn.server_label == ""


def test_the_canvas_address_never_reaches_the_durable_journal(tmp_path: Path) -> None:
    """A Drawpile session password has no business surviving on disk.

    The canvas projection follows the reference video's rule: memory-only, so
    a restarted host offers no canvas until its owner shares one again.
    """

    control = SessionControlState(tmp_path, str(uuid.uuid4()), creator_profile_key="art")
    control.publish_shared_canvas(**_shared())
    control.begin(str(uuid.uuid4()), started_utc="2026-08-21T00:00:00Z")

    written = control.path.read_text(encoding="utf-8")

    assert "hunter2" not in written
    assert "kitchen-table" not in written
    assert "shared_canvas" not in json.loads(written)


def test_a_host_runtime_publishes_only_once_a_session_owns_control(
    tmp_path: Path,
) -> None:
    session = HostPeerSession()

    assert session.publish_shared_canvas_state(**_shared()) is None

    session.control = SessionControlState(
        tmp_path, str(uuid.uuid4()), creator_profile_key="art"
    )
    published = session.publish_shared_canvas_state(**_shared())

    assert published is not None
    assert published.join_url == NORMALIZED


# ---------------------------------------------------------------------------
# End to end over a real authenticated peer
# ---------------------------------------------------------------------------


def _serve(tmp_path: Path, control: SessionControlState, credentials):
    registry = EnrollmentRegistry(tmp_path, credentials)
    transfers = TransferStore(tmp_path, credentials.session_id)
    enrollment = registry.enroll(
        str(uuid.uuid4()), "Guest Artist", invite_token=credentials.invite_token
    )
    server = SessionPeerServer(
        "127.0.0.1",
        0,
        registry=registry,
        control=control,
        transfers=transfers,
    )
    server.start()
    return server, enrollment


def test_one_webjam_invitation_is_enough_to_reach_the_canvas(tmp_path: Path) -> None:
    """A guest must not need a second product to be handed the canvas."""

    credentials = SessionCredentials.create()
    control = SessionControlState(
        tmp_path, credentials.session_id, creator_profile_key="art"
    )
    expected = control.publish_shared_canvas(**_shared())
    server, enrollment = _serve(tmp_path, control, credentials)
    try:
        client = SessionPeerClient(
            "127.0.0.1", server.address[1], credentials=credentials
        )
        observed = client.state(enrollment)
    finally:
        server.stop()

    assert observed.shared_canvas == expected
    assert observed.creator_profile_key == "art"

    launcher = FakeLauncher()
    follower = SharedCanvasFollower(launcher=launcher)
    assert (
        follower.observe(observed.shared_canvas).state
        is SharedCanvasFollowState.READY
    )
    follower.open_canvas()
    assert launcher.joined == [NORMALIZED]


def test_a_late_joining_artist_receives_a_canvas_shared_before_they_arrived(
    tmp_path: Path,
) -> None:
    credentials = SessionCredentials.create()
    control = SessionControlState(
        tmp_path, credentials.session_id, creator_profile_key="art"
    )
    control.publish_shared_canvas(**_shared())
    server, enrollment = _serve(tmp_path, control, credentials)
    try:
        client = SessionPeerClient(
            "127.0.0.1", server.address[1], credentials=credentials
        )
        first_poll = client.state(enrollment)
    finally:
        server.stop()

    # A late joiner's very first poll must already carry the canvas, which is
    # what a non-zero generation on an untouched projection proves.
    assert first_poll.shared_canvas.generation > 0
    assert first_poll.shared_canvas.shared is True


def test_the_invitation_can_carry_a_canvas_and_a_video_at_once(
    tmp_path: Path,
) -> None:
    """Combining the two add-ons is an in-room decision the room supports."""

    from core.session_transfer import ReferenceVideoPlaybackState

    credentials = SessionCredentials.create()
    control = SessionControlState(
        tmp_path, credentials.session_id, creator_profile_key="art"
    )
    control.publish_shared_canvas(**_shared())
    control.publish_reference_video(
        state=ReferenceVideoPlaybackState.PLAYING,
        shared=True,
        source_display_name="lesson.mp4",
        identity_digest="a" * 64,
        position_s=42.0,
        duration_s=600.0,
    )
    server, enrollment = _serve(tmp_path, control, credentials)
    try:
        client = SessionPeerClient(
            "127.0.0.1", server.address[1], credentials=credentials
        )
        observed = client.state(enrollment)
    finally:
        server.stop()

    assert observed.shared_canvas.shared is True
    assert observed.reference_video.shared is True
    assert observed.reference_video.position_s == pytest.approx(42.0)


def test_a_guest_holding_the_projection_still_cannot_change_it(
    tmp_path: Path,
) -> None:
    """Receiving an address is not authority over it."""

    projection = SharedCanvasSessionSnapshot(**_shared())

    with pytest.raises(Exception):
        projection.shared = False  # type: ignore[misc]

    follower = SharedCanvasFollower(launcher=FakeLauncher())
    follower.observe(projection)
    for forbidden in ("share", "withdraw", "publish_shared_canvas"):
        assert not hasattr(follower, forbidden), forbidden
