from pathlib import Path
import pytest
from tests.test_guest_recording_preflight_guidance import (
    qapp as _qapp_fixture,
    controllers as _controllers_fixture,
    guest_rig as _guest_rig_fixture,
)
from tests.test_recording_studio import _schema2_studio_take
from core.session_conductor import SessionPrimaryAction
from webjam_qt.widgets.participant_card import ParticipantPresentation

qapp = _qapp_fixture
controllers = _controllers_fixture
guest_rig = _guest_rig_fixture


@pytest.mark.parametrize(
    "clears",
    [False, True],
    ids=["failure-arrives-during-review", "failure-clears-during-review"],
)
def test_new_live_take_refreshes_current_guest_readiness(guest_rig, qapp, clears):
    rig = guest_rig
    studio = rig.app.window.recording_studio
    if clears:
        rig.guest._current_local_original_contract()
    else:
        rig.guest._notify_guidance_changed()
    qapp.processEvents()
    initial = studio._recording_unavailable_reason
    _schema2_studio_take(Path(rig.app.settings.takes_directory))
    studio.reload()
    studio._take_list.setCurrentRow(0)
    qapp.processEvents()
    assert studio.guidance_facts().take_selected
    if clears:
        rig.app.settings.audio_samplerate = 48000
    rig.guest._current_local_original_contract()
    qapp.processEvents()
    expected = rig.app._guest_recording_reason()
    assert expected != initial
    assert (rig.guest.local_capture_preflight is None) is clears
    assert ("require 48 kHz" in expected) is (not clears)
    studio._new_take_btn.click()
    qapp.processEvents()
    assert not studio.guidance_facts().take_selected
    generation = rig.guest._guidance_notification_generation
    rig.guest._current_local_original_contract()
    qapp.processEvents()
    assert rig.guest._guidance_notification_generation == generation
    assert not studio._record_btn.isEnabled()
    assert studio._setup_btn.isEnabled()
    assert studio._hint.text() == expected
    assert studio._record_btn.toolTip() == expected
    assert expected in studio._record_btn.accessibleDescription()


@pytest.mark.parametrize(
    "connected", [False, True], ids=["offline", "connected-roster"]
)
def test_new_live_take_does_not_keep_review_as_guest_next_action(
    guest_rig, qapp, connected
):
    app = guest_rig.app
    studio = app.window.recording_studio
    app.window.show()
    app._open_take_deck()
    if connected:
        app.audio.connected = True
        app.bridge.jamulus_state = "Running"
        app.participants = {
            1: ParticipantPresentation(channel_id=1, name="Host"),
            2: ParticipantPresentation(
                channel_id=2, name=app.settings.musician_name, is_local=True
            ),
        }
        app._push_participants_to_grid()
        studio.set_live_participants(app.participants.values())
    try:
        _schema2_studio_take(Path(app.settings.takes_directory))
        studio.reload()
        studio._take_list.setCurrentRow(0)
        qapp.processEvents()
        assert studio.guidance_facts().take_selected
        guest_rig.guest._current_local_original_contract()
        studio._new_take_btn.click()
        qapp.processEvents()
        assert not studio.guidance_facts().take_selected
        assert studio._viewing_live
        assert "require 48 kHz" in studio._hint.text()
        assert studio._setup_btn.isEnabled()
        assert not studio._record_btn.isEnabled()
        assert (
            app._last_musician_guidance.primary_action
            is not SessionPrimaryAction.SELECT_TAKE
        )
        assert "Choose a take to review" not in app.window.session_hud._status.text()
        assert "Next: Choose a Take" not in studio._phase.text()
        assert "Studio not open" not in studio._phase.text()
    finally:
        app.audio.connected = False
        app.bridge.jamulus_state = "Not launched"
        app.participants = {}


def test_live_review_transition_preserves_initial_library_and_save_veto(
    guest_rig, qapp, monkeypatch
):
    app = guest_rig.app
    studio = app.window.recording_studio
    app._open_take_deck()
    assert app._conductor_studio_reviewing
    _schema2_studio_take(Path(app.settings.takes_directory))
    studio.reload()
    studio._take_list.setCurrentRow(0)
    qapp.processEvents()
    assert studio.guidance_facts().take_selected
    with monkeypatch.context() as patch:
        patch.setattr(studio, "_flush_studio_state", lambda: False)
        studio._new_take_btn.click()
    assert studio.guidance_facts().take_selected
    assert app._conductor_studio_reviewing
    studio._new_take_btn.click()
    assert not app._conductor_studio_reviewing
    studio._take_list.setCurrentRow(-1)
    studio._take_list.setCurrentRow(0)
    qapp.processEvents()
    assert studio.guidance_facts().take_selected
    assert app._conductor_studio_reviewing
    app._on_rail_view_changed("stage")
    studio._show_live_session()
    studio._take_list.setCurrentRow(-1)
    studio._take_list.setCurrentRow(0)
    assert not app._conductor_studio_reviewing
    # Finish the selected take's queued layout before the fixture destroys Qt.
    qapp.processEvents()
