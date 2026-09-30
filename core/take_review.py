"""Private review annotations, kept separate from recordings and arrangements."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import dataclass, replace
from pathlib import Path

from core.file_io import atomic_write_text
from core.studio_store import StudioStoreError, studio_store_lock

REVIEW_FILENAME = ".webjam-review.json"
MAX_REVIEW_BYTES = 64 * 1024
MAX_REVIEW_NOTES = 8000


class TakeReviewError(ValueError):
    """Review could not be read or safely saved; keep the user's draft."""


@dataclass(frozen=True)
class TakeReview:
    take_identity: str
    favorite: bool = False
    notes: str = ""
    token: str | None = None


def _read(path: Path, limit: int = MAX_REVIEW_BYTES) -> bytes | None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
        raise TakeReviewError("Review data is not a bounded regular file.")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as handle:
        opened = os.fstat(handle.fileno())
        if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
            raise TakeReviewError("Review data changed while opening.")
        data = handle.read(limit + 1)
    if len(data) > limit:
        raise TakeReviewError("Review data is too large.")
    return data


def take_review_identity(take) -> str:
    """Durable take ID, or a stable legacy source inventory (not list position)."""
    if take.take_id:
        return str(take.take_id)
    inventory = sorted(
        (str(Path(track.path).relative_to(take.path)), str(track.source))
        for track in take.tracks
    )
    return "legacy-" + hashlib.sha256(json.dumps(inventory).encode()).hexdigest()


def take_source_identity(take) -> str:
    """Exact manifest snapshot for bookmarks; legacy takes use source stat facts."""
    data = _read(Path(take.path) / "webjam-take.json", 16 * 1024 * 1024)
    if data is None:
        if take.manifest_schema_version >= 2:
            raise TakeReviewError("The recording manifest is missing.")
        facts = []
        for track in take.tracks:
            info = Path(track.path).stat()
            facts.append((str(Path(track.path).relative_to(take.path)), info.st_size,
                          info.st_mtime_ns, info.st_ino))
        data = json.dumps(sorted(facts)).encode()
    return hashlib.sha256(data).hexdigest()


def load_take_review(take) -> TakeReview:
    identity = take_review_identity(take)
    try:
        data = _read(Path(take.path) / REVIEW_FILENAME)
        if data is None:
            return TakeReview(identity)
        value = json.loads(data)
        if (not isinstance(value, dict) or value.get("schema_version") != 1
                or value.get("take_identity") != identity
                or type(value.get("favorite")) is not bool
                or not isinstance(value.get("notes"), str)
                or len(value["notes"]) > MAX_REVIEW_NOTES):
            raise TakeReviewError("Review data does not match this take.")
        return TakeReview(identity, value["favorite"], value["notes"],
                          hashlib.sha256(data).hexdigest())
    except (OSError, UnicodeError, ValueError) as exc:
        raise TakeReviewError("Review could not be opened. The recording is unchanged.") from exc


def save_take_review(take, review: TakeReview) -> TakeReview:
    if (review.take_identity != take_review_identity(take)
            or type(review.favorite) is not bool
            or not isinstance(review.notes, str)
            or len(review.notes) > MAX_REVIEW_NOTES):
        raise TakeReviewError("Review does not match this take or exceeds its limit.")
    path = Path(take.path) / REVIEW_FILENAME
    text = json.dumps({"schema_version": 1, "take_identity": review.take_identity,
                       "favorite": review.favorite, "notes": review.notes},
                      ensure_ascii=False, indent=2) + "\n"
    if len(text.encode()) > MAX_REVIEW_BYTES:
        raise TakeReviewError("Review is too large to save.")
    try:
        if not Path(take.path).is_dir():
            raise TakeReviewError("The recording folder is unavailable; keep this review draft.")
        with studio_store_lock(take.path):
            manifest = _read(Path(take.path) / "webjam-take.json", 16 * 1024 * 1024)
            if take.manifest_schema_version >= 2 and (
                manifest is None or json.loads(manifest).get("take_id") != take.take_id
            ):
                raise TakeReviewError("The recording changed. Reopen it before saving this review.")
            current = _read(path)
            token = None if current is None else hashlib.sha256(current).hexdigest()
            if token != review.token:
                raise TakeReviewError("Review changed elsewhere. Reopen it before saving.")
            atomic_write_text(path, text, mode=0o600)
    except (OSError, StudioStoreError, json.JSONDecodeError, UnicodeError, AttributeError) as exc:
        raise TakeReviewError("Review could not be saved. Keep this draft and retry.") from exc
    return replace(review, token=hashlib.sha256(text.encode()).hexdigest())
