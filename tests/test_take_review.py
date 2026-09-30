from __future__ import annotations

import json
import stat
from dataclasses import replace
from types import SimpleNamespace

import pytest

from core.take_review import (
    MAX_REVIEW_BYTES, REVIEW_FILENAME, TakeReviewError, load_take_review,
    save_take_review, take_review_identity, take_source_identity,
)


@pytest.fixture
def take(tmp_path):
    audio = tmp_path / "source.wav"
    audio.write_bytes(b"original recording")
    (tmp_path / "webjam-take.json").write_text(json.dumps({"schema_version": 2, "take_id": "take-a"}))
    return SimpleNamespace(path=tmp_path, take_id="take-a", manifest_schema_version=2,
                           tracks=[SimpleNamespace(path=audio, source="server")])


def test_review_survives_reopen_without_changing_source_or_manifest(take):
    original = {p: p.read_bytes() for p in take.path.iterdir()}
    initial = load_take_review(take)
    saved = save_take_review(take, replace(initial, favorite=True, notes="Keep chorus ✓"))
    assert load_take_review(take) == saved
    assert saved.notes == "Keep chorus ✓" and saved.favorite
    assert {p: p.read_bytes() for p in original} == original
    assert stat.S_IMODE((take.path / REVIEW_FILENAME).stat().st_mode) == 0o600


def test_stale_writer_does_not_erase_newer_review(take):
    initial = load_take_review(take)
    save_take_review(take, replace(initial, notes="newer draft"))
    with pytest.raises(TakeReviewError, match="changed elsewhere"):
        save_take_review(take, replace(initial, notes="stale draft"))
    assert load_take_review(take).notes == "newer draft"


@pytest.mark.parametrize("damage", ["json", "identity", "notes", "oversized", "directory", "symlink"])
def test_invalid_review_is_preserved_and_never_overwritten(take, damage):
    path = take.path / REVIEW_FILENAME
    value = {"schema_version": 1, "take_identity": "take-a", "favorite": False, "notes": ""}
    if damage == "identity":
        value["take_identity"] = "other"
    elif damage == "notes":
        value["notes"] = []
    if damage == "directory":
        path.mkdir()
    elif damage == "symlink":
        path.symlink_to(take.path / "source.wav")
    else:
        path.write_text("{" if damage == "json" else "x" * (MAX_REVIEW_BYTES + 1)
                        if damage == "oversized" else json.dumps(value))
    with pytest.raises(TakeReviewError):
        load_take_review(take)
    assert path.exists()
    assert (take.path / "source.wav").read_bytes() == b"original recording"


def test_failed_save_preserves_previous_review(take, monkeypatch):
    saved = save_take_review(take, replace(load_take_review(take), notes="prior"))
    def fail(*args, **kwargs):
        raise PermissionError("read only")
    monkeypatch.setattr("core.take_review.atomic_write_text", fail)
    with pytest.raises(TakeReviewError):
        save_take_review(take, replace(saved, notes="retained draft"))
    assert load_take_review(take).notes == "prior"


def test_bookmark_identity_changes_with_manifest_and_rejects_missing(take):
    before = take_source_identity(take)
    manifest = take.path / "webjam-take.json"
    manifest.write_text(manifest.read_text() + "\n")
    assert take_source_identity(take) != before
    manifest.unlink()
    with pytest.raises(TakeReviewError):
        take_source_identity(take)


def test_legacy_review_identity_is_order_independent(take):
    take.take_id = ""
    take.tracks.append(SimpleNamespace(path=take.path / "other.wav", source="local"))
    before = take_review_identity(take)
    take.tracks.reverse()
    assert take_review_identity(take) == before


def test_oversized_draft_does_not_publish(take):
    with pytest.raises(TakeReviewError):
        save_take_review(take, replace(load_take_review(take), notes="x" * 8001))
    assert not (take.path / REVIEW_FILENAME).exists()


def test_replaced_recording_does_not_receive_old_review(take):
    review = replace(load_take_review(take), notes="old take draft")
    (take.path / "webjam-take.json").write_text(json.dumps({"take_id": "replacement"}))
    with pytest.raises(TakeReviewError, match="recording changed"):
        save_take_review(take, review)
    assert not (take.path / REVIEW_FILENAME).exists()
