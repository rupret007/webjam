import io
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from core.web_join_invite_share import (  # noqa: E402
    CLIPBOARD_DENIED_NOTICE,
    COPIED_NOTICE,
    build_web_join_invite_url,
    copy_text_to_qt_clipboard,
    web_join_invite_qr_png,
)


def test_build_web_join_invite_url_matches_host_fragment_shape():
    capability = bytes(range(32))
    session = b"s" * 32
    url = build_web_join_invite_url(
        "https://stalemate.band/join",
        capability,
        session,
        5,
        1_700_000_090,
    )
    assert url.startswith("https://stalemate.band/join/#k=")
    assert "&s=" in url and "&g=5&" in url and url.endswith("&e=1700000090")
    assert "?" not in url


def test_build_web_join_invite_url_rejects_invalid_material():
    with pytest.raises(ValueError):
        build_web_join_invite_url(
            "https://example.com/join/",
            b"short",
            b"s" * 32,
            1,
            123,
        )


def test_qr_png_encodes_the_same_invite_url():
    url = build_web_join_invite_url(
        "https://stalemate.band/join/",
        b"k" * 32,
        b"a" * 32,
        1,
        1_700_000_100,
    )
    png = web_join_invite_qr_png(url)
    assert png.startswith(b"\x89PNG")
    import segno

    reference = io.BytesIO()
    segno.make(url, error="m").save(reference, kind="png", scale=3, border=4, dark="#1a1a1a")
    assert png == reference.getvalue()


def test_copy_text_to_qt_clipboard_success():
    stored = {}

    class Clipboard:
        def setText(self, value):
            stored["text"] = value

    result = copy_text_to_qt_clipboard(Clipboard(), "https://example/join/#k=test")
    assert result.ok and not result.used_fallback
    assert result.notice == COPIED_NOTICE
    assert stored["text"] == "https://example/join/#k=test"


def test_copy_text_to_qt_clipboard_denied():
    class Clipboard:
        def setText(self, _value):
            raise RuntimeError("denied")

    result = copy_text_to_qt_clipboard(Clipboard(), "https://example/join/#k=test")
    assert not result.ok and result.used_fallback
    assert result.notice == CLIPBOARD_DENIED_NOTICE


def test_web_join_invite_dialog_copy_and_qr():
    from PySide6.QtWidgets import QApplication

    from webjam_qt.windows.web_join_invite_dialog import WebJoinInviteDialog

    app = QApplication.instance() or QApplication([])
    url = build_web_join_invite_url(
        "https://stalemate.band/join/",
        b"b" * 32,
        b"c" * 32,
        2,
        1_700_000_200,
    )
    dialog = WebJoinInviteDialog()
    dialog.set_invite_url(url)
    dialog.show()
    app.processEvents()
    try:
        assert dialog._link.text() == url
        assert not dialog._qr.pixmap().isNull()
        assert dialog._qr.accessibleDescription()
        dialog.copy_invite_link()
        app.processEvents()
        assert QApplication.clipboard().text() == url
        assert COPIED_NOTICE in dialog._notice.text()
    finally:
        dialog.close()
