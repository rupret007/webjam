"""Explicit lesson requests are passive, bounded Art Conversation controls."""

import os
import logging
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtCore import QPoint, QRect, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QVBoxLayout, QWidget  # noqa: E402

from core.creative_modes import get_creator_profile_by_key  # noqa: E402
from core.lesson_request import LessonRequestIntent, LessonRequestNotice  # noqa: E402
from webjam_qt.theme import load_stylesheet  # noqa: E402
from webjam_qt.widgets.webex_embed import WebexEmbed  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def settle(qapp):
    for _ in range(4):
        qapp.processEvents()


@pytest.fixture(autouse=True)
def no_hidden_callback_errors(monkeypatch, caplog):
    errors = []
    monkeypatch.setattr(sys, "excepthook", lambda kind, error, tb: errors.append(kind.__name__))
    yield
    assert errors == []
    assert [record.getMessage()
            for phase in ("setup", "call", "teardown")
            for record in caplog.get_records(phase)
            if record.name == "webjam.ui_thread" and record.levelno >= logging.ERROR] == []


@pytest.fixture
def card(qapp):
    previous = qapp.styleSheet()
    qapp.setStyleSheet(load_stylesheet())
    owner = QWidget()
    layout = QVBoxLayout(owner)
    layout.setContentsMargins(0, 0, 0, 0)
    panel = WebexEmbed()
    panel.set_creator_profile(get_creator_profile_by_key("art"))
    layout.addWidget(panel)
    layout.addStretch(1)
    effects = []
    for name in ("bring_forward_requested", "open_meeting_requested", "change_link_requested",
                 "mute_in_webex_requested", "install_webex_requested", "meeting_state_changed"):
        getattr(panel, name).connect(lambda *args, event=name: effects.append(event))
    owner.resize(390, 1000)
    owner.show()
    settle(qapp)
    yield owner, panel, effects
    assert effects == []
    owner.close()
    owner.deleteLater()
    settle(qapp)
    qapp.setStyleSheet(previous)


def button(panel, text):
    matches = [item for item in panel.findChildren(QPushButton)
               if item.text() == text and item.isVisibleTo(panel)]
    assert len(matches) == 1
    return matches[0]


def test_guest_intents_require_explicit_enabled_click(card, qapp):
    _, panel, _ = card
    panel.set_shared_lesson_context(False)
    panel.set_lesson_request_guest(status="Ask your host when you need time.", can_pause=True, can_ready=True)
    events = []
    panel.lesson_request_intent.connect(events.append)
    settle(qapp)
    assert events == []
    QTest.mouseClick(button(panel, "Ask for a pause"), Qt.MouseButton.LeftButton)
    QTest.mouseClick(button(panel, "Ready to continue"), Qt.MouseButton.LeftButton)
    assert events == ["pause", "ready"]


def notice(*, admission=1, revision=1, intent="pause", state="accepted", expires=30_000):
    return LessonRequestNotice(
        participant_id=f"00000000-0000-0000-0000-{admission:012d}",
        context_id="a" * 32, admission_id=f"{admission:032x}", revision=revision,
        intent=LessonRequestIntent(intent), state=state, expires_in_ms=expires,
    )


def request_events(panel):
    events = []
    panel.lesson_request_intent.connect(lambda intent: events.append(("intent", intent)))
    panel.lesson_request_retry.connect(lambda: events.append(("retry",)))
    panel.lesson_request_acknowledge.connect(lambda *key: events.append(("ack", *key)))
    return events


def test_guest_uncertain_retry_is_explicit_and_disabled_controls_are_inert(card, qapp):
    _, panel, _ = card
    events = request_events(panel)
    panel.set_shared_lesson_context(False)
    panel.set_lesson_request_guest(status="Sending your request…")
    settle(qapp)
    panel._lesson_pause_button.clicked.emit()
    panel._lesson_ready_button.clicked.emit()
    panel._lesson_retry_button.clicked.emit()
    assert events == []
    assert not panel._lesson_retry_button.isVisibleTo(panel)
    panel.set_lesson_request_guest(
        status="Delivery unconfirmed. Ask aloud, or retry this request.", can_retry=True,
    )
    settle(qapp)
    button(panel, "Retry this request").setFocus()
    QTest.keyClick(button(panel, "Retry this request"), Qt.Key.Key_Space)
    assert events == [("retry",)]
    panel.set_lesson_request_guest(status="Host acknowledged your request.")
    panel._lesson_retry_button.clicked.emit()
    assert events == [("retry",)]
    assert panel._lesson_request_guest_status.text() == "Host acknowledged your request."
    assert panel._lesson_request_guest_status.accessibleDescription() == "Host acknowledged your request."
    assert "Paused" not in panel._lesson_request_guest_status.text()


@pytest.mark.parametrize("hosting", [False, True])
def test_hidden_or_retired_role_cannot_emit_request_or_ack(card, qapp, hosting):
    owner, panel, _ = card
    events = request_events(panel)
    panel.set_shared_lesson_context(hosting)
    row_notice = notice()
    if hosting:
        panel.set_lesson_request_host([("Artist", row_notice)])
        action = button(panel, "Acknowledge request")
        retired_row = next(iter(panel._lesson_request_rows.values()))
    else:
        panel.set_lesson_request_guest(status="Ask when you need time.", can_pause=True)
        action = button(panel, "Ask for a pause")
    owner.hide()
    action.clicked.emit()
    owner.show()
    settle(qapp)
    panel.set_shared_lesson_context(None)
    action.clicked.emit()
    assert events == []
    assert panel._lesson_request_frame.isHidden()
    assert panel._lesson_request_notices == {}
    assert panel._lesson_request_guest_status.text() == ""
    if hosting:
        assert retired_row.name_label.accessibleName() == "Guest"
        assert retired_row.status_label.text() == ""
    panel.set_shared_lesson_context(hosting)
    assert panel._lesson_request_frame.isHidden()  # Old projection is not revived.
    assert events == []


def test_host_acknowledges_exact_request_and_same_names_remain_separate(card, qapp):
    _, panel, _ = card
    events = request_events(panel)
    panel.set_shared_lesson_context(True)
    first, second = notice(), notice(admission=2, intent="ready")
    panel.set_lesson_request_host([("Artist", first), ("Artist", second)])
    settle(qapp)
    assert events == []
    assert len(panel._lesson_request_rows) == 2
    rows = list(panel._lesson_request_rows.values())
    assert "Asked for a pause" in rows[0].status_label.text()
    assert "Ready to continue" in rows[1].status_label.text()
    assert all("everyone" not in row.status_label.text().lower() for row in rows)
    assert all(row.ack_button.accessibleName() == "Acknowledge request from Artist" for row in rows)
    QTest.mouseClick(rows[1].ack_button, Qt.MouseButton.LeftButton)
    assert events == [("ack", second.context_id, second.admission_id, second.revision)]
    assert rows[1].status_label.text() == "Ready to continue · 30s left"
    assert "Acknowledging does not pause or resume" in rows[1].ack_button.accessibleDescription()


@pytest.mark.parametrize("intent, intent_text", [
    ("pause", "Asked for a pause"),
    ("ready", "Ready to continue"),
])
def test_host_receipt_refresh_preserves_intent_focus_and_original_expiry(
    card, qapp, intent, intent_text,
):
    _, panel, _ = card
    events = request_events(panel)
    panel.set_shared_lesson_context(True)
    first = notice(intent=intent, expires=9_000)
    panel.set_lesson_request_host([("Artist", first)])
    settle(qapp)
    row = next(iter(panel._lesson_request_rows.values()))
    row.ack_button.setFocus()
    for _ in range(3):
        panel.set_lesson_request_host([("Artist", first)])
        settle(qapp)
        assert next(iter(panel._lesson_request_rows.values())) is row
        assert QApplication.focusWidget() is row.ack_button
        assert row.status_label.text() == f"{intent_text} · 9s left"
    panel.set_lesson_request_host([(
        "Artist", notice(intent=intent, state="acknowledged", expires=8_000),
    )])
    row.ack_button.clicked.emit()
    assert row.status_label.text() == f"{intent_text} · acknowledged · 8s left"
    assert not row.ack_button.isEnabled()
    panel.set_lesson_request_host([(
        "Artist", notice(intent=intent, state="expired", expires=0),
    )])
    row.ack_button.clicked.emit()
    assert row.status_label.text() == "Request expired"
    assert events == []


def test_replaced_revision_cannot_be_acknowledged_by_an_old_button(card, qapp):
    _, panel, _ = card
    events = request_events(panel)
    panel.set_shared_lesson_context(True)
    panel.set_lesson_request_host([("Artist", notice())])
    old = button(panel, "Acknowledge request")
    next_notice = notice(revision=2, intent="ready")
    panel.set_lesson_request_host([("Artist", next_notice)])
    old.clicked.emit()  # A queued control from revision 1 is not revision 2.
    assert events == []
    settle(qapp)
    current = button(panel, "Acknowledge request")
    assert current is not old
    QTest.mouseClick(current, Qt.MouseButton.LeftButton)
    assert events == [("ack", next_notice.context_id, next_notice.admission_id, 2)]


def test_profile_and_role_changes_clear_passively_but_same_art_refresh_retains(card, qapp):
    _, panel, _ = card
    events = request_events(panel)
    panel.set_shared_lesson_context(False)
    panel.set_lesson_request_guest(status="Delivered to the host's WebJam.", can_ready=True)
    panel.set_creator_profile(get_creator_profile_by_key("art"))
    panel.set_shared_lesson_context(False)
    assert panel._lesson_request_guest_status.text() == "Delivered to the host's WebJam."
    panel.set_shared_lesson_context(True)
    assert panel._lesson_request_frame.isHidden()
    panel.set_lesson_request_host([("Artist", notice())])
    panel.set_creator_profile(get_creator_profile_by_key("music"))
    assert panel._lesson_request_frame.isHidden()
    panel.set_lesson_request_host([("Ignored stale host", notice())])
    panel.set_lesson_request_guest(status="Ignored stale guest", can_pause=True)
    panel.set_creator_profile(get_creator_profile_by_key("art"))
    panel.set_shared_lesson_context(True)
    assert panel._lesson_request_frame.isHidden()
    assert events == []


def test_host_labels_are_bounded_plain_text_and_not_request_authority(card, qapp):
    _, panel, _ = card
    panel.set_shared_lesson_context(True)
    text = '<img src="https://example.invalid/private"><b>Guest & ready</b>' * 5
    panel.set_lesson_request_host([(text, notice(admission=index)) for index in range(1, 34)])
    settle(qapp)
    assert len(panel._lesson_request_rows) == 32
    for row in panel._lesson_request_rows.values():
        assert row.name_label.textFormat() is Qt.TextFormat.PlainText
        assert row.status_label.textFormat() is Qt.TextFormat.PlainText
        assert row.name_label.accessibleName() == text[:80]
        assert len(row.name_label._name) == 80
    last = list(panel._lesson_request_rows.values())[-1]
    panel._lesson_request_scroll.ensureWidgetVisible(last.ack_button)
    settle(qapp)
    viewport = panel._lesson_request_scroll.viewport()
    assert viewport.rect().contains(QRect(last.ack_button.mapTo(viewport, QPoint()), last.ack_button.size()))
    assert panel._lesson_request_scroll.horizontalScrollBar().maximum() == 0


def test_guest_status_is_flat_plain_text_and_nonboolean_flags_do_not_enable(card, qapp):
    _, panel, _ = card
    events = request_events(panel)
    panel.set_shared_lesson_context(False)
    panel.set_lesson_request_guest(
        status="<b>not markup</b>\nAsk aloud.", can_pause="true", can_ready=1, can_retry="yes",
    )
    assert panel._lesson_request_guest_status.textFormat() is Qt.TextFormat.PlainText
    assert panel._lesson_request_guest_status.text() == "<b>not markup</b> Ask aloud."
    for action in (panel._lesson_pause_button, panel._lesson_ready_button, panel._lesson_retry_button):
        assert not action.isEnabled()
        action.clicked.emit()
    assert events == []


@pytest.mark.parametrize("hosting", [False, True])
@pytest.mark.parametrize("width", [320, 390, 760])
@pytest.mark.parametrize("large_font", [False, True])
def test_request_card_fits_compact_and_larger_font_without_focus_change(
    card, qapp, hosting, width, large_font,
):
    owner, panel, _ = card
    if large_font:
        panel.setStyleSheet('QPushButton { font-family: "Helvetica"; font-size: 13pt; }')
    panel.set_service_label("Webex")
    panel.set_meeting_configured(True)
    panel.set_app_status("installed", publisher_verified=True)
    panel.focus_primary_action()
    focused = QApplication.focusWidget()
    panel.set_shared_lesson_context(hosting)
    if hosting:
        panel.set_lesson_request_host([("Artist with a long display name", notice())])
    else:
        panel.set_lesson_request_guest(
            status="Delivery unconfirmed. Ask aloud, or retry this request.",
            can_pause=True, can_ready=True, can_retry=True,
        )
    for size in (width, 760, width):
        owner.resize(size, 1000)
        settle(qapp)
        assert panel.width() == size
        assert panel.height() <= 1000
        assert QApplication.focusWidget() is focused
        for item in panel.findChildren(QPushButton) + panel.findChildren(QLabel):
            if item.isVisibleTo(panel):
                assert panel.rect().contains(QRect(item.mapTo(panel, QPoint()), item.size()))
                if isinstance(item, QPushButton):
                    assert item.width() >= item.minimumSizeHint().width()
                elif item.wordWrap():
                    assert item.height() >= item.heightForWidth(item.width())
        assert panel._lesson_request_scroll.horizontalScrollBar().maximum() == 0
