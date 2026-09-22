"""A current guest capture owner supplies one safe shared recovery action."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock

import pytest

from core.pocket_stage import MobileSessionProjection
from core.session_transfer import RecordingSignal, SessionStateSnapshot
from tests.test_art_room_controller import invitation
from tests.test_guest_recording_preflight_guidance import (
    controllers as _controllers_fixture,
    guest_rig as _guest_rig_fixture,
    qapp as _qapp_fixture,
)
from tests.test_recording_studio import _schema2_studio_take
from webjam_qt.theme import load_stylesheet
from webjam_qt.widgets.participant_card import ParticipantPresentation
from webjam_qt.windows.recording_setup import RecordingSetupDialog

qapp = _qapp_fixture
controllers = _controllers_fixture
guest_rig = _guest_rig_fixture

_PRIVATE_DETAIL = "PRIVATE_INPUT /private/device token=PRIVATE_TOKEN"
_ACTION = "open_recording_setup"
_ACTION_KIND = "recording_setup"


@pytest.fixture(autouse=True)
def deterministic_presentation(qapp):
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(
            "webjam_qt.platform_permissions.microphone_permission_status",
            lambda: "authorized",
        )
        yield


@pytest.fixture
def live_guest(guest_rig, qapp):
    rig = guest_rig
    app = rig.app
    app.window.setStyleSheet(load_stylesheet())
    app.window.resize(1000, 740)
    app.window.show()
    app.audio.connected = True
    app.bridge.jamulus_state = "Running"
    app.participants = {
        1: ParticipantPresentation(channel_id=1, name="Host"),
        2: ParticipantPresentation(
            channel_id=2, name=app.settings.musician_name, is_local=True,
        ),
    }
    app._push_participants_to_grid()
    app.window.recording_studio.set_live_participants(app.participants.values())
    qapp.processEvents()
    yield rig
    app.audio.connected = False
    app.bridge.jamulus_state = "Not launched"
    app.participants = {}


def _fail(rig, qapp, failure="rate"):
    if failure != "rate":
        rig.app.settings.audio_samplerate = 48_000
    if failure == "buffer":
        rig.app.settings.audio_blocksize = -1
    elif failure == "channels":
        rig.query.return_value = {"max_input_channels": 1}
    elif failure == "device":
        rig.query.side_effect = RuntimeError(_PRIVATE_DETAIL)
    elif failure == "format":
        rig.check.side_effect = RuntimeError(_PRIVATE_DETAIL)
    assert rig.guest._current_local_original_contract() == (
        True, None, "", None, (), (),
    )
    qapp.processEvents()
    rig.app._update_session_hud()
    assert rig.guest.local_capture_preflight is not None
    assert not rig.guest.local_capture_preflight.ready
    return rig.app._last_musician_guidance


def _assert_action_is_current(app):
    guidance = app._last_musician_guidance
    assert guidance.primary_action.value == _ACTION
    assert guidance.primary_enabled
    assert guidance.next_step == "Recording Setup"
    hud = app.window.session_hud
    assert hud._action.text() == "Recording Setup"
    assert hud._action.isVisibleTo(app.window)
    assert hud._action.isEnabled()
    return guidance


@pytest.mark.parametrize(
    ("failure", "specific"),
    [
        ("rate", "require 48 kHz"),
        ("buffer", "buffer size is invalid"),
        ("channels", "needs 2 input channels"),
        ("device", "input is unavailable"),
        ("format", "input is unavailable"),
    ],
)
def test_current_guest_failure_is_specific_on_shared_surfaces(
    live_guest, qapp, failure, specific,
):
    rig = live_guest
    app = rig.app
    _fail(rig, qapp, failure)
    guidance = _assert_action_is_current(app)
    assert specific in guidance.message
    assert app.window.session_hud._status.text() == guidance.title
    assert app.window.session_hud._detail.text() == guidance.message
    canvas = app.window.session_canvas
    studio = app.window.recording_studio
    assert canvas._guidance_status.text() == guidance.title
    assert canvas._guidance_next.text() == "Next: Recording Setup"
    assert "Next: Recording Setup" in studio._phase.text()
    assert specific in studio._hint.text()
    assert not studio._record_btn.isEnabled()
    assert studio._setup_btn.isEnabled()
    assert "Recording Setup" in app.window.session_hud._action.accessibleDescription()
    public = guidance.to_public_dict()
    assert public["primary_action"] == _ACTION
    assert app._companion_get_diagnostics()["musician_guidance"] == public
    app._refresh_pocket_projection()
    mobile = app._get_pocket_projection()
    assert mobile.primary_action is guidance.primary_action
    assert mobile.primary_enabled == guidance.primary_enabled
    assert mobile.cue == guidance.title
    assert MobileSessionProjection.from_dict(mobile.to_dict()) == mobile
    rendered = (
        guidance.accessible_description + str(public) + str(mobile.to_dict())
        + canvas._guidance_next.text() + studio._phase.text()
    )
    for private in ("PRIVATE_INPUT", "/private/device", "PRIVATE_TOKEN", "PRIVATE_TRACK_NAME"):
        assert private not in rendered
    rig.stream.assert_not_called()
    assert rig.guest._capture is None
    assert not tuple(rig.guest.originals_root.rglob("*.wav"))


def test_current_hud_action_opens_setup_without_recording_or_changing_choice(
    live_guest, qapp, monkeypatch,
):
    rig = live_guest
    app = rig.app
    _fail(rig, qapp)
    _assert_action_is_current(app)
    before_settings = deepcopy(app.settings)
    path = Path(app.settings.config_file)
    before_saved = path.read_bytes() if path.exists() else None
    record = Mock()
    monkeypatch.setattr(app.recording, "on_record_requested", record)
    monkeypatch.setattr("webjam_qt.windows.recording_setup.list_input_devices", lambda: [])
    opened = []

    def cancel(dialog):
        opened.append(dialog)
        assert dialog._capture.isChecked()
        assert dialog._capture.isEnabled()
        return RecordingSetupDialog.DialogCode.Rejected

    monkeypatch.setattr(RecordingSetupDialog, "exec", cancel)
    app.window.session_hud._action.click()
    assert len(opened) == 1
    assert app.settings == before_settings
    assert (path.read_bytes() if path.exists() else None) == before_saved
    assert app.guest_peer is rig.guest
    record.assert_not_called()
    rig.stream.assert_not_called()
    assert rig.guest._capture is None


@pytest.mark.parametrize(
    "changed",
    [
        "guest", "fingerprint", "ready", "local_capture", "finalization",
        "host_recording", "host_finalizing", "host_attention", "cleanup",
        "review", "export", "shutdown",
    ],
)
def test_delayed_recovery_click_cannot_open_setup_for_changed_authority(
    live_guest, qapp, monkeypatch, changed,
):
    rig = live_guest
    app = rig.app
    _fail(rig, qapp)
    _assert_action_is_current(app)
    # Restore synthetic ownership before the real controller shuts down.
    with monkeypatch.context() as authority:
        if changed == "guest":
            # Replace the actual owner while suppressing its paint notification.
            # The old HUD action must not authorize a new guest, even if its error
            # text is identical when the new observer checks the input.
            with monkeypatch.context() as quiet:
                quiet.setattr(app, "_on_guest_media_guidance_changed", lambda **_kw: None)
                quiet.setattr(app, "_update_session_hud", lambda: None)
                invite = invitation()
                assert app._configure_guest_peer(invite)
                replacement = app.guest_peer
                assert replacement is not rig.guest
                replacement.last_state = SessionStateSnapshot(
                    invite.session_id, 0, RecordingSignal.IDLE,
                    creator_profile_key="music",
                )
                replacement._current_local_original_contract()
        elif changed in {"fingerprint", "ready"}:
            app.settings.audio_samplerate = 48_000
            if changed == "fingerprint":
                rig.query.return_value = {"max_input_channels": 1}
            with monkeypatch.context() as quiet:
                quiet.setattr(rig.guest, "_notify_guidance_changed", lambda: None)
                rig.guest._current_local_original_contract()
        elif changed == "local_capture":
            authority.setattr(rig.guest, "_active_take_id", "current-take")
        elif changed == "finalization":
            authority.setattr(rig.guest, "_capture_finalization_needs_attention", True)
        elif changed.startswith("host_"):
            signal = {
                "host_recording": RecordingSignal.RECORDING,
                "host_finalizing": RecordingSignal.FINALIZING,
                "host_attention": RecordingSignal.NEEDS_ATTENTION,
            }[changed]
            authority.setattr(rig.guest, "last_state", SessionStateSnapshot(
                rig.invite.session_id, 1, signal, creator_profile_key="music",
            ))
        elif changed == "shutdown":
            authority.setattr(app, "_shutdown_in_progress", True)
        elif changed == "cleanup":
            authority.setattr(app.audio, "stopping", True)
        elif changed == "review":
            studio = app.window.recording_studio
            _schema2_studio_take(Path(app.settings.takes_directory))
            app._open_take_deck()
            studio.reload()
            studio._take_list.setCurrentRow(0)
            assert studio.guidance_facts().take_selected
        elif changed == "export":
            authority.setattr(app.window.recording_studio, "_exporting", True)
        opener = Mock()
        authority.setattr(app, "_open_recording_setup", opener)
        # A Qt signal already queued before the owner changed is stronger evidence
        # than directly clicking a button that the current UI has already hidden.
        app.window.session_hud.action_requested.emit(_ACTION_KIND)
        opener.assert_not_called()
        rig.stream.assert_not_called()
        # Resolve queued layout events while every Qt object is still alive.
        qapp.processEvents()


def test_repeated_preflight_projection_is_stable_and_does_not_probe_capture(
    live_guest, qapp,
):
    rig = live_guest
    app = rig.app
    _fail(rig, qapp)
    before_guidance = _assert_action_is_current(app)
    app._refresh_pocket_projection()
    before_mobile = app._get_pocket_projection()
    before_owner_notifications = rig.guest._guidance_notification_generation
    before_settings = deepcopy(app.settings)
    before_canvas = app.window.session_canvas._guidance_next.text()
    for _ in range(3):
        rig.guest._current_local_original_contract()
        qapp.processEvents()
        app._update_session_hud()
        app._refresh_pocket_projection()
        assert app._last_musician_guidance == before_guidance
        assert app._get_pocket_projection() == before_mobile
        assert app.window.session_canvas._guidance_next.text() == before_canvas
    assert rig.guest._guidance_notification_generation == before_owner_notifications
    assert app.settings == before_settings
    rig.stream.assert_not_called()


def test_success_clears_shared_recovery_without_starting_recording(live_guest, qapp):
    rig = live_guest
    app = rig.app
    _fail(rig, qapp)
    _assert_action_is_current(app)
    app.settings.audio_samplerate = 48_000
    assert rig.guest._current_local_original_contract()[:2] == (True, 1)
    qapp.processEvents()
    app._update_session_hud()
    assert rig.guest.local_capture_preflight is None
    assert app._last_musician_guidance.primary_action.value != _ACTION
    assert "require 48 kHz" not in app._last_musician_guidance.message
    assert "require 48 kHz" not in app.window.recording_studio._hint.text()
    assert app.window.session_hud._action.isHidden()
    assert not app.window.recording_studio._record_btn.isEnabled()
    assert rig.guest._capture is None
    rig.stream.assert_not_called()
