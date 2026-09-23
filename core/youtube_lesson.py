"""Canonical, bounded YouTube source identity for silent Paint along lessons."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from urllib.parse import parse_qs, urlsplit

_VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}\Z")
_TIME = re.compile(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?\Z")
_HOSTS = frozenset({"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"})
INVALID_LINK_MESSAGE = "Paste a YouTube video link, such as youtube.com/watch?v=…."


@dataclass(frozen=True, slots=True, repr=False)
class YouTubeLesson:
    video_id: str
    start_s: int = 0

    def __post_init__(self) -> None:
        if (
            not isinstance(self.video_id, str) or not _VIDEO_ID.fullmatch(self.video_id)
            or type(self.start_s) is not int or not 0 <= self.start_s <= 86_400
        ):
            raise ValueError(INVALID_LINK_MESSAGE)

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.video_id}"

    @property
    def display_name(self) -> str:
        return "YouTube lesson"

    @property
    def content_sha256(self) -> str:
        # The existing room signer HMACs this value. A URL source never
        # borrows a file hash or sends the artist's pasted tracking query.
        return hashlib.sha256(b"webjam-paint-along-youtube-v1\0" + self.video_id.encode("ascii")).hexdigest()

    def __repr__(self) -> str:
        return "YouTubeLesson(video_id=[private])"


def parse_youtube_lesson_url(value: str) -> YouTubeLesson:
    if not isinstance(value, str):
        raise ValueError(INVALID_LINK_MESSAGE)
    value = value.strip()
    if not value or len(value) > 2048 or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError(INVALID_LINK_MESSAGE)
    if value.startswith(tuple(host + "/" for host in _HOSTS)):
        value = "https://" + value
    try:
        url = urlsplit(value)
        if (url.scheme != "https" or url.hostname not in _HOSTS
                or url.username is not None or url.password is not None
                or url.port not in {None, 443}):
            raise ValueError(INVALID_LINK_MESSAGE)
        query = parse_qs(url.query, keep_blank_values=True, max_num_fields=32)
        if url.hostname == "youtu.be":
            video_id = url.path.removeprefix("/")
        elif url.path == "/watch" and len(query.get("v", [])) == 1:
            video_id = query["v"][0]
        elif url.path.startswith(("/embed/", "/shorts/")):
            video_id = url.path.split("/", 2)[2]
        else:
            raise ValueError(INVALID_LINK_MESSAGE)
        starts = query.get("t", []) + query.get("start", [])
        if len(starts) > 1:
            raise ValueError(INVALID_LINK_MESSAGE)
        start_s = 0
        if starts:
            stamp = starts[0]
            if stamp.isascii() and stamp.isdecimal():
                start_s = int(stamp)
            else:
                match = _TIME.fullmatch(stamp)
                if not match or not any(match.groups()):
                    raise ValueError(INVALID_LINK_MESSAGE)
                start_s = sum(int(n or 0) * scale for n, scale in zip(match.groups(), (3600, 60, 1)))
        return YouTubeLesson(video_id, start_s)
    except (ValueError, UnicodeError) as exc:
        raise ValueError(INVALID_LINK_MESSAGE) from exc
