"""Fresh guest Studio is already live; its current capture remedy stays usable."""
from pathlib import Path
from unittest.mock import Mock

import pytest

from tests.test_guest_preflight_shared_recovery import (
    qapp as _qapp_fixture, controllers as _controllers_fixture,
    guest_rig as _guest_rig_fixture, live_guest as _live_guest_fixture,
    deterministic_presentation as _presentation_fixture,
    _fail, _assert_action_is_current,
)
from tests.test_recording_studio import _schema2_studio_take

qapp = _qapp_fixture
controllers = _controllers_fixture
guest_rig = _guest_rig_fixture
live_guest = _live_guest_fixture
deterministic_presentation = _presentation_fixture


@pytest.mark.parametrize("size", [(760, 600), (1000, 740)])
def test_first_empty_guest_studio_keeps_live_recording_setup_action(
    live_guest, qapp, monkeypatch, size,
):
    rig = live_guest
    app = rig.app
    _fail(rig, qapp)
    _assert_action_is_current(app)
    studio = app.window.recording_studio
    assert not studio.guidance_facts().take_available
    app.window.resize(*size)
    app._open_take_deck()
    qapp.processEvents()
    assert studio.isVisibleTo(app.window)
    assert studio._viewing_live
    assert not studio.guidance_facts().take_selected
    _assert_action_is_current(app)
    assert app.window.session_canvas._guidance_next.text() == "Next: Recording Setup"
    assert "Next: Recording Setup" in studio._phase.text()
    assert "require 48 kHz" in studio._hint.text()
    opener = Mock()
    monkeypatch.setattr(app, "_open_recording_setup", opener)
    app.window.session_hud._action.click()
    opener.assert_called_once_with()


def test_guest_completed_take_review_still_owns_shared_next_action(
    live_guest, qapp, monkeypatch,
):
    rig = live_guest
    app = rig.app
    _fail(rig, qapp)
    _assert_action_is_current(app)
    studio = app.window.recording_studio
    _schema2_studio_take(Path(app.settings.takes_directory))
    app._open_take_deck()
    studio.reload()
    studio._take_list.setCurrentRow(0)
    qapp.processEvents()
    assert studio.guidance_facts().take_selected
    assert not studio._viewing_live
    guidance = app._last_musician_guidance
    assert guidance.primary_action.value != "open_recording_setup"
    assert "require 48 kHz" not in guidance.message
    assert "Next: Recording Setup" not in app.window.session_canvas._guidance_next.text()
    opener = Mock()
    monkeypatch.setattr(app, "_open_recording_setup", opener)
    app.window.session_hud.action_requested.emit("recording_setup")
    opener.assert_not_called()
    qapp.processEvents()
