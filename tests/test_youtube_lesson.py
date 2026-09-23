"""Paint along accepts a video identity, never an arbitrary browser address."""

import pytest

from core.youtube_lesson import YouTubeLesson, parse_youtube_lesson_url


@pytest.mark.parametrize("url", [
    "https://www.youtube.com/watch?v=M7lc1UVf-VE",
    "https://youtube.com/watch?v=M7lc1UVf-VE&feature=shared",
    "https://m.youtube.com/watch?v=M7lc1UVf-VE",
    "https://youtu.be/M7lc1UVf-VE?si=private-tracking",
    "https://www.youtube.com/shorts/M7lc1UVf-VE",
    "https://www.youtube.com/embed/M7lc1UVf-VE",
    "  youtu.be/M7lc1UVf-VE  ",
])
def test_supported_links_resolve_to_one_bounded_video_identity(url):
    lesson = parse_youtube_lesson_url(url)
    assert lesson.video_id == "M7lc1UVf-VE"
    assert lesson.url == "https://www.youtube.com/watch?v=M7lc1UVf-VE"
    assert lesson.content_sha256 == YouTubeLesson("M7lc1UVf-VE").content_sha256
    assert len(lesson.content_sha256) == 64
    assert "private-tracking" not in repr(lesson)


@pytest.mark.parametrize("url", [
    "https://youtube.com.evil.example/watch?v=M7lc1UVf-VE",
    "https://youtube.com@evil.example/watch?v=M7lc1UVf-VE",
    "https://name:secret@youtube.com/watch?v=M7lc1UVf-VE",
    "https://youtube.com:8443/watch?v=M7lc1UVf-VE",
    "http://youtube.com/watch?v=M7lc1UVf-VE",
    "file:///tmp/video.html",
    "javascript:alert(1)",
    "https://youtube.com/playlist?list=PL_private",
    "https://youtube.com/watch?v=M7lc1UVf-VE&v=abcdefghijk",
    "https://youtu.be/M7lc1UVf-VE/extra",
    "https://youtu.be/too-short",
    "https://youtu.be/<script>xxx",
    "https://youtu.be/M7lc1UVf-VE\nsecret",
    "https://youtube.com/redirect?q=https://example.com",
    "",
])
def test_invalid_or_ambiguous_links_cannot_become_player_navigation(url):
    with pytest.raises(ValueError, match="YouTube video link"):
        parse_youtube_lesson_url(url)


def test_identity_is_domain_separated_and_validated():
    import hashlib

    lesson = YouTubeLesson("M7lc1UVf-VE")
    assert lesson.content_sha256 != hashlib.sha256(lesson.video_id.encode()).hexdigest()
    with pytest.raises(ValueError):
        YouTubeLesson("bad/video/id")


@pytest.mark.parametrize("suffix,seconds", [("?t=90", 90), ("?t=1m30s", 90), ("?start=75", 75)])
def test_shared_timestamp_is_local_start_position_not_video_identity(suffix, seconds):
    lesson = parse_youtube_lesson_url("https://youtu.be/M7lc1UVf-VE" + suffix)
    assert lesson.start_s == seconds
    assert lesson.content_sha256 == YouTubeLesson(lesson.video_id).content_sha256


@pytest.mark.parametrize("suffix", ["?t=-1", "?t=nan", "?t=90&t=120", "?t=30&start=50"])
def test_ambiguous_or_invalid_start_position_is_rejected(suffix):
    with pytest.raises(ValueError, match="YouTube video link"):
        parse_youtube_lesson_url("https://youtu.be/M7lc1UVf-VE" + suffix)
