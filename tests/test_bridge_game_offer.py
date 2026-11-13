"""Host Play picker: Chess confirm, grant hashes in the fragment only."""

from __future__ import annotations

import base64
import hashlib
import logging
import os
from types import SimpleNamespace
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox  # noqa: E402

_app = QApplication.instance() or QApplication([])

from webjam_qt.controllers.web_join import WebJoinControls  # noqa: E402
from webjam_qt.widgets import webex_embed as module  # noqa: E402
from webjam_qt.widgets.webex_embed import WebexEmbed  # noqa: E402


def _grant(raw: bytes) -> tuple[str, str]:
    token = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    digest = hashlib.sha256(token.encode("ascii")).hexdigest()
    return token, digest


def _enable_private(monkeypatch) -> None:
    from core import web_join_target as target

    monkeypatch.setattr(target, "web_join_page", lambda: target._PAGES["private-beta"])


def _drive(monkeypatch, hashes, *, game="Chess", confirm=True, pick=0, tokens=()):
    seen = []
    opened = []

    def choose(_parent, _title, label, items, _index, editable):
        seen.append(("item", label, tuple(items)))
        assert editable is False
        if label == "Choose a game.":
            assert list(items) == ["Chess"]
            return game, True
        assert "up next game" in label
        for item in items:
            for secret in (*tokens, *hashes):
                assert secret not in item
        return items[pick], True

    def ask(_parent, _title, text, _buttons, default):
        seen.append(("confirm", text))
        assert text == "Seat 2 people from your WebJam room in Chess?"
        assert default == QMessageBox.StandardButton.No
        if confirm:
            return QMessageBox.StandardButton.Yes
        return QMessageBox.StandardButton.No

    def record(url):
        opened.append(url.toString())
        seen.append(("open", opened[-1]))
        return True

    monkeypatch.setattr(module.QInputDialog, "getItem", choose)
    monkeypatch.setattr(module.QMessageBox, "question", ask)
    monkeypatch.setattr(module.QDesktopServices, "openUrl", record)
    embed = WebexEmbed()
    embed.set_enrolled_browser_grants(hashes)
    embed.show()
    embed._play_btn.click()
    return embed, seen, opened


def test_play_hides_until_private_game_night_is_available():
    embed = WebexEmbed()
    assert embed._play_btn.isHidden()


def test_play_confirms_then_opens_hashes_in_the_fragment_only(monkeypatch, caplog):
    _enable_private(monkeypatch)
    token, digest = _grant(bytes(range(32)))
    assert token != digest
    caplog.set_level(logging.DEBUG)
    _embed, seen, opened = _drive(monkeypatch, [digest], tokens=(token,))
    assert [kind for kind, *_rest in seen] == ["item", "confirm", "open"]
    assert opened
    url = opened[0]
    query, _sep, fragment = url.partition("#")
    assert fragment == "grants=" + digest
    assert digest not in query
    assert token not in url
    assert "deliver" not in url
    assert "?" in query and query.index("?") < url.index("#")
    assert token not in caplog.text
    assert digest not in caplog.text


def test_play_does_not_open_when_the_host_cancels(monkeypatch):
    _enable_private(monkeypatch)
    _token, digest = _grant(b"\x02" * 32)
    _embed, seen, opened = _drive(monkeypatch, [digest], confirm=False)
    assert opened == []
    assert seen[-1][0] == "confirm"


def test_play_rejects_a_non_chess_choice(monkeypatch):
    _enable_private(monkeypatch)

    def fail_confirm(*_args):
        pytest.fail("a non-chess choice must not confirm")

    monkeypatch.setattr(module.QMessageBox, "question", fail_confirm)
    _embed, _seen, opened = _drive(monkeypatch, [_grant(b"\x03" * 32)[1]], game="Crapjack")
    assert opened == []


def test_native_guests_keep_share_invite(monkeypatch):
    _enable_private(monkeypatch)
    notes = []
    monkeypatch.setattr(module.QInputDialog, "getItem", lambda *_args: ("Chess", True))
    monkeypatch.setattr(module.QMessageBox, "information", lambda *_args: notes.append(_args[2]))
    monkeypatch.setattr(module.QDesktopServices, "openUrl", lambda *_args: pytest.fail("no browser guest"))
    embed = WebexEmbed()
    embed.show()
    embed._play_btn.click()
    assert notes == ["Play reaches enrolled browser guests. Native Jamulus guests keep Share invite."]
    embed.set_enrolled_browser_grants("not-a-list-of-hashes")
    assert embed._browser_grant_hashes == ()
    embed.set_enrolled_browser_grants(["short", "has+padding=="])
    assert embed._browser_grant_hashes == ()


def test_host_picks_one_guest_when_the_room_is_bigger(monkeypatch):
    _enable_private(monkeypatch)
    first_token, first = _grant(b"\x04" * 32)
    second_token, second = _grant(b"\x05" * 32)
    _embed, _seen, opened = _drive(
        monkeypatch,
        [first, second],
        pick=1,
        tokens=(first_token, second_token),
    )
    assert len(opened) == 1
    assert second in opened[0].split("#", 1)[1]
    assert first not in opened[0]
    assert first_token not in opened[0]
    assert second_token not in opened[0]


def test_grant_source_runs_only_after_the_host_confirms(monkeypatch):
    _enable_private(monkeypatch)
    token, digest = _grant(b"\x07" * 32)
    calls = []

    def source(index):
        calls.append(index)
        return digest

    def choose(_parent, _title, label, items, _index, _editable):
        assert list(items) == ["Chess"]
        return "Chess", True

    monkeypatch.setattr(module.QInputDialog, "getItem", choose)
    monkeypatch.setattr(module.QDesktopServices, "openUrl", lambda url: opened.append(url.toString()) or True)
    opened = []
    embed = WebexEmbed()
    embed.set_enrolled_browser_guest_count(1)
    embed.set_play_grant_source(source)
    embed.show()
    monkeypatch.setattr(module.QMessageBox, "question", lambda *_args: QMessageBox.StandardButton.No)
    embed._play_btn.click()
    assert calls == []
    assert opened == []
    monkeypatch.setattr(module.QMessageBox, "question", lambda *_args: QMessageBox.StandardButton.Yes)
    embed._play_btn.click()
    assert calls == [0]
    assert opened and digest in opened[0].split("#", 1)[1]
    assert token not in opened[0]
    assert digest not in opened[0].split("#", 1)[0]



def test_failed_browser_launch_reopens_the_same_offer(monkeypatch):
    _enable_private(monkeypatch)
    _token, digest = _grant(b"\x09" * 32)
    source = MagicMock(return_value=digest)
    opened = []
    warn = MagicMock()
    def launch(url):
        opened.append(url.toString())
        return len(opened) == 2

    monkeypatch.setattr(module.QInputDialog, "getItem", lambda *_args: ("Chess", True))
    monkeypatch.setattr(module.QMessageBox, "question", lambda *_args: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(module.QMessageBox, "warning", warn)
    monkeypatch.setattr(module.QDesktopServices, "openUrl", launch)
    embed = WebexEmbed()
    embed.set_enrolled_browser_guest_count(1)
    embed.set_play_grant_source(source)
    embed._play_btn.click()
    assert "reopen the same offer" in warn.call_args.args[2]
    embed._play_btn.click()
    assert len(opened) == 2 and opened[0] == opened[1]
    source.assert_called_once_with(0)
    assert embed._pending_chess_browser is None


def test_chess_confirmation_cannot_use_a_retired_sender(monkeypatch):
    _enable_private(monkeypatch)
    source = MagicMock()
    warn = MagicMock()
    embed = WebexEmbed()
    embed.set_enrolled_browser_guest_count(1)
    embed.set_play_grant_source(source)
    def disconnect(*_args):
        embed.set_play_grant_source(None)
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(module.QInputDialog, "getItem", lambda *_args: ("Chess", True))
    monkeypatch.setattr(module.QMessageBox, "question", disconnect)
    monkeypatch.setattr(module.QMessageBox, "warning", warn)
    embed._play_btn.click()
    source.assert_not_called()
    assert "browser guest changed" in warn.call_args.args[2]


def test_host_page_refuses_query_grants_and_extra_seats(monkeypatch):
    _enable_private(monkeypatch)
    _token, digest = _grant(b"\x06" * 32)
    assert module.chess_host_page([digest]) is not None
    assert module.chess_host_page([digest, digest]) is None
    assert module.chess_host_page([]) is None
    assert module.chess_host_page(digest) is None
    monkeypatch.setattr(module, "game_night_page", lambda: None)
    assert module.chess_host_page([digest]) is None


def test_chess_offer_source_belongs_to_the_current_enrolled_guest():
    window = QMainWindow()
    embed = WebexEmbed(window)
    window.webex_embed = embed
    server = object()
    bridge = SimpleNamespace(hosted_server_process=server, hosted_server_owned=lambda: True)
    controller = SimpleNamespace(window=window, bridge=bridge, settings=SimpleNamespace(host_server_enabled=True))
    controls = WebJoinControls(controller)
    send = MagicMock(return_value="f" * 64)
    host = SimpleNamespace(state="waiting", offer_chess=send, stop=lambda: True)
    controls._host, controls._server = host, server
    try:
        controls._refresh()
        assert embed._play_grant_source is None
        host.state = "connected"
        controls._refresh()
        source = embed._play_grant_source
        assert embed._browser_guest_count == 1
        assert source(1) == ""
        send.assert_not_called()
        assert source(0) == "f" * 64
        send.assert_called_once_with()
        assert controls.stop()
        assert embed._play_grant_source is None
        assert source(0) == ""
        send.assert_called_once_with()
    finally:
        controls.stop()
        controls._timer.stop()
        window.close()
