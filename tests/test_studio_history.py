"""Deterministic coverage for bounded immutable Studio undo/redo history."""

from __future__ import annotations

import json
import threading

import pytest

from core.studio_history import MAX_HISTORY_LABEL_BYTES, StudioHistory
from core.studio_project import StudioDocument, StudioProjectError, StudioTrack


def _document() -> tuple[StudioDocument, str]:
    track_id = "00000000-0000-4000-8000-000000000003"
    return (
        StudioDocument(
            session_id="00000000-0000-4000-8000-000000000001",
            take_id="00000000-0000-4000-8000-000000000002",
            project_sample_rate=48_000,
            tracks=(StudioTrack(track_id=track_id),),
        ),
        track_id,
    )


def _compact_size(document: StudioDocument) -> int:
    return len(
        json.dumps(
            document.to_dict(),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    )


def test_perform_undo_redo_restore_exact_immutable_snapshots_and_ids() -> None:
    initial, track_id = _document()
    history = StudioHistory(initial)

    muted = history.perform(
        "Mute track",
        lambda document: document.update_track(track_id, muted=True),
    )
    panned = history.perform(
        "Pan track",
        lambda document: document.update_track(track_id, pan=-0.35),
    )

    assert history.document is panned
    assert history.can_undo is True
    assert history.can_redo is False
    assert history.undo_depth == 2
    assert history.redo_depth == 0

    assert history.undo() is muted
    assert history.undo() is initial
    assert history.document.state_for(track_id).track_id == track_id
    assert history.can_undo is False
    assert history.can_redo is True
    assert history.undo_depth == 0
    assert history.redo_depth == 2

    assert history.redo() is muted
    assert history.redo() is panned
    assert history.document.state_for(track_id).track_id == track_id
    assert history.can_undo is True
    assert history.can_redo is False


def test_empty_undo_redo_and_clear_never_replace_current_document() -> None:
    initial, track_id = _document()
    history = StudioHistory(initial)

    assert history.undo() is initial
    assert history.redo() is initial

    changed = history.perform(
        "Raise fader",
        lambda document: document.update_track(track_id, fader_gain=1.25),
    )
    history.undo()
    history.clear()

    assert history.document is initial
    assert history.can_undo is False
    assert history.can_redo is False
    assert history.undo_depth == 0
    assert history.redo_depth == 0
    assert history.undo() is initial
    assert history.redo() is initial
    assert changed is not initial


def test_noop_edit_is_not_recorded_and_preserves_redo() -> None:
    initial, track_id = _document()
    history = StudioHistory(initial)
    changed = history.perform(
        "Mute track",
        lambda document: document.update_track(track_id, muted=True),
    )
    assert history.undo() is initial

    assert history.perform("No change", lambda document: document) is initial
    assert history.undo_depth == 0
    assert history.redo_depth == 1
    assert history.can_redo is True
    assert history.redo() is changed


def test_divergent_edit_clears_redo_without_changing_durable_ids() -> None:
    initial, track_id = _document()
    history = StudioHistory(initial)
    history.perform(
        "Mute track",
        lambda document: document.update_track(track_id, muted=True),
    )
    history.undo()

    divergent = history.perform(
        "Pan instead",
        lambda document: document.update_track(track_id, pan=0.4),
    )

    assert divergent.state_for(track_id).track_id == track_id
    assert divergent.state_for(track_id).muted is False
    assert divergent.state_for(track_id).pan == pytest.approx(0.4)
    assert history.undo_depth == 1
    assert history.redo_depth == 0
    assert history.can_redo is False
    assert history.redo() is divergent


def test_invalid_edit_result_and_edit_error_leave_history_atomic() -> None:
    initial, track_id = _document()
    history = StudioHistory(initial)
    changed = history.perform(
        "Mute track",
        lambda document: document.update_track(track_id, muted=True),
    )
    history.undo()
    before_depths = (history.undo_depth, history.redo_depth)

    with pytest.raises(StudioProjectError, match="return a StudioDocument"):
        history.perform("Invalid", lambda _document: object())  # type: ignore[arg-type,return-value]

    def fail(_document: StudioDocument) -> StudioDocument:
        raise RuntimeError("edit failed")

    with pytest.raises(RuntimeError, match="edit failed"):
        history.perform("Failure", fail)

    assert history.document is initial
    assert (history.undo_depth, history.redo_depth) == before_depths
    assert history.redo() is changed


def test_constructor_and_callable_validation_use_project_error() -> None:
    initial, _track_id = _document()

    with pytest.raises(StudioProjectError, match="requires a StudioDocument"):
        StudioHistory(object())  # type: ignore[arg-type]
    with pytest.raises(StudioProjectError, match="positive integer"):
        StudioHistory(initial, max_entries=0)
    with pytest.raises(StudioProjectError, match="positive integer"):
        StudioHistory(initial, max_bytes=-1)

    history = StudioHistory(initial)
    with pytest.raises(StudioProjectError, match="must be callable"):
        history.perform("Invalid", None)  # type: ignore[arg-type]
    assert history.document is initial
    assert history.undo_depth == 0


def test_entry_cap_evicts_oldest_transition_without_dropping_current() -> None:
    initial, track_id = _document()
    history = StudioHistory(initial, max_entries=2)

    first = history.perform(
        "First",
        lambda document: document.update_track(track_id, fader_gain=1.1),
    )
    second = history.perform(
        "Second",
        lambda document: document.update_track(track_id, fader_gain=1.2),
    )
    third = history.perform(
        "Third",
        lambda document: document.update_track(track_id, fader_gain=1.3),
    )

    assert history.document is third
    assert history.undo_depth == 2
    assert history.undo() is second
    assert history.undo() is first
    assert history.can_undo is False
    assert history.document is first
    assert history.document is not initial


def test_serialized_byte_cap_uses_compact_documents_and_evicts_oldest() -> None:
    initial, track_id = _document()
    first = initial.update_track(track_id, fader_gain=1.1)
    second = first.update_track(track_id, fader_gain=1.2)
    first_entry_bytes = (
        _compact_size(initial) + _compact_size(first) + len("First".encode("utf-8"))
    )
    second_entry_bytes = (
        _compact_size(first) + _compact_size(second) + len("Second".encode("utf-8"))
    )
    history = StudioHistory(
        initial,
        max_entries=8,
        max_bytes=max(first_entry_bytes, second_entry_bytes),
    )

    assert history.perform("First", lambda _document: first) is first
    assert history.undo_depth == 1
    assert history.perform("Second", lambda _document: second) is second

    assert history.document is second
    assert history.undo_depth == 1
    assert history.undo() is first
    assert history.can_undo is False


def test_oversized_transition_keeps_new_current_without_undo_entry() -> None:
    initial, track_id = _document()
    changed = initial.update_track(track_id, fader_gain=1.5)
    entry_bytes = (
        _compact_size(initial)
        + _compact_size(changed)
        + len("Oversized".encode("utf-8"))
    )
    history = StudioHistory(
        initial,
        max_entries=8,
        max_bytes=entry_bytes - 1,
    )

    assert history.perform("Oversized", lambda _document: changed) is changed
    assert history.document is changed
    assert history.undo_depth == 0
    assert history.redo_depth == 0
    assert history.undo() is changed


@pytest.mark.parametrize(
    ("identity_field", "replacement"),
    (
        ("session_id", "00000000-0000-4000-8000-000000000010"),
        ("take_id", "00000000-0000-4000-8000-000000000011"),
        ("project_sample_rate", 96_000),
    ),
)
def test_perform_rejects_cross_project_identity_without_mutating_history(
    identity_field: str,
    replacement: object,
) -> None:
    initial, _track_id = _document()
    history = StudioHistory(initial)
    foreign = initial._bumped(**{identity_field: replacement})

    with pytest.raises(
        StudioProjectError, match="preserve session, take, and sample-rate"
    ):
        history.perform("Foreign snapshot", lambda _document: foreign)

    assert history.document is initial
    assert history.undo_depth == 0
    assert history.redo_depth == 0


def test_perform_rejects_nonforward_revision_and_preserves_redo() -> None:
    initial, track_id = _document()
    history = StudioHistory(initial)
    changed = history.perform(
        "Mute track",
        lambda document: document.update_track(track_id, muted=True),
    )
    assert history.undo() is initial
    same_revision_change = StudioDocument(
        session_id=initial.session_id,
        take_id=initial.take_id,
        project_sample_rate=initial.project_sample_rate,
        tracks=(StudioTrack(track_id=track_id, pan=0.5),),
        revision=initial.revision,
    )

    with pytest.raises(StudioProjectError, match="advance the document revision"):
        history.perform("Stale snapshot", lambda _document: same_revision_change)

    assert history.document is initial
    assert history.undo_depth == 0
    assert history.redo_depth == 1
    assert history.redo() is changed


def test_history_labels_are_utf8_bounded_and_count_toward_byte_limit() -> None:
    initial, track_id = _document()
    changed = initial.update_track(track_id, muted=True)
    exact_label = "é" * (MAX_HISTORY_LABEL_BYTES // 2)
    entry_bytes = (
        _compact_size(initial)
        + _compact_size(changed)
        + len(exact_label.encode("utf-8"))
    )
    retained = StudioHistory(initial, max_bytes=entry_bytes)

    assert retained.perform(exact_label, lambda _document: changed) is changed
    assert retained.undo_depth == 1

    evicted = StudioHistory(initial, max_bytes=entry_bytes - 1)
    assert evicted.perform(exact_label, lambda _document: changed) is changed
    assert evicted.undo_depth == 0

    untouched = StudioHistory(initial)
    with pytest.raises(StudioProjectError, match="label must be text"):
        untouched.perform(123, lambda _document: changed)  # type: ignore[arg-type]
    with pytest.raises(StudioProjectError, match="cannot exceed"):
        untouched.perform(
            exact_label + "x",
            lambda _document: changed,
        )
    assert untouched.document is initial
    assert untouched.undo_depth == 0


def test_concurrent_perform_serializes_edits_without_lost_updates() -> None:
    initial, track_id = _document()
    history = StudioHistory(initial, max_entries=128)
    barrier = threading.Barrier(4)
    errors: list[BaseException] = []
    errors_lock = threading.Lock()

    def worker() -> None:
        try:
            barrier.wait(timeout=10)
            for _ in range(20):
                history.perform(
                    "Increment fader",
                    lambda document: document.update_track(
                        track_id,
                        fader_gain=document.state_for(track_id).fader_gain + 0.001,
                    ),
                )
        except BaseException as exc:  # noqa: BLE001
            with errors_lock:
                errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert all(not thread.is_alive() for thread in threads)
    assert errors == []
    assert history.document.state_for(track_id).fader_gain == pytest.approx(1.08)
    assert history.undo_depth == 80
    for _ in range(80):
        history.undo()
    assert history.document is initial
