"""A guest's actual capture owner explains failed optional local inputs."""

from __future__ import annotations

import json
import hashlib
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtTest import QTest

from core.session_transfer import (
    EnrollmentRegistry, RecordingSignal, SessionControlState, SessionCredentials,
    SessionPeerClient, SessionPeerServer, SessionStateSnapshot, TransferStore,
)
from core.session_transfer_runtime import GuestPeerSession, HostPeerSession
from tests.test_art_lan_retry import controllers as _controllers_fixture
from tests.test_art_room_controller import invitation, qapp as _qapp_fixture
from webjam_qt.windows.recording_setup import RecordingSetupDialog


qapp = _qapp_fixture
controllers = _controllers_fixture
PRIVATE_DEVICE_DETAIL = "PRIVATE_INTERFACE_NAME /private/audio-device token=private"


@pytest.fixture
def guest_rig(controllers, monkeypatch, qapp):
    # Effective-setting tests must not inherit a developer's audio overrides.
    for name in (
        "WEBJAM_AUDIO_SAMPLERATE", "WEBJAM_AUDIO_BLOCKSIZE",
        "WEBJAM_LOCAL_CAPTURE_ENABLED", "WEBJAM_AUDIO_INPUT_DEVICE_INDEX",
        "WEBJAM_TAKES_DIRECTORY", "WEBJAM_HOST_SERVER_ENABLED",
    ):
        monkeypatch.delenv(name, raising=False)
    app = controllers()
    app.settings.local_capture_enabled = True
    app.settings.audio_samplerate = 44_100
    app.settings.audio_blocksize = 256
    app.settings.audio_input_device_index = 7
    app.settings.input_maps = [{
        "name": "PRIVATE_TRACK_NAME", "channels": 2,
        "enabled": True, "local_original_enabled": True,
    }]
    query = Mock(return_value={"max_input_channels": 2})
    check = Mock()
    stream = Mock(side_effect=AssertionError("preflight opened an input stream"))
    monkeypatch.setattr("sounddevice.query_devices", query)
    monkeypatch.setattr("sounddevice.check_input_settings", check)
    monkeypatch.setattr("sounddevice.InputStream", stream)
    invite = invitation()
    assert app._configure_guest_peer(invite)
    guest = app.guest_peer
    assert isinstance(guest, GuestPeerSession)
    guest.last_state = SessionStateSnapshot(
        invite.session_id, 0, RecordingSignal.IDLE, creator_profile_key="music",
    )
    qapp.processEvents()
    return SimpleNamespace(
        app=app, guest=guest, invite=invite, query=query, check=check, stream=stream,
    )


def _unknown_contract(guest):
    contract = guest._current_local_original_contract()
    assert contract == (True, None, "", None, (), ())
    assert guest._capture is None
    assert guest.active_take_id == ""
    assert guest._thread is None
    return contract


@pytest.mark.parametrize(
    ("failure", "error_code", "guidance"),
    [
        ("rate", "unsupported_sample_rate", "require 48 kHz"),
        ("channels", "insufficient_input_channels", "needs 2 input channels"),
        ("device", "input_device_or_format_unavailable", "input is unavailable"),
        ("format", "input_device_or_format_unavailable", "input is unavailable"),
        ("buffer", "invalid_block_size", "buffer size is invalid"),
    ],
)
def test_real_guest_retains_specific_preflight_and_refreshes_studio(
    guest_rig, qapp, caplog, failure, error_code, guidance,
):
    rig = guest_rig
    if failure != "rate":
        rig.app.settings.audio_samplerate = 48_000
    if failure == "channels":
        rig.query.return_value = {"max_input_channels": 1}
    elif failure == "device":
        rig.query.side_effect = RuntimeError(PRIVATE_DEVICE_DETAIL)
    elif failure == "format":
        rig.check.side_effect = RuntimeError(PRIVATE_DEVICE_DETAIL)
    elif failure == "buffer":
        rig.app.settings.audio_blocksize = -1

    generation = rig.guest._guidance_notification_generation
    _unknown_contract(rig.guest)
    qapp.processEvents()

    reason = rig.app._guest_recording_reason()
    assert guidance in reason
    assert "Recording Setup" in reason
    preflight = rig.guest.local_capture_preflight
    assert preflight is not None and not preflight.ready
    assert preflight.errors == (error_code,)
    assert preflight.required_input_channels == 2
    assert rig.guest._guidance_notification_generation == generation + 1
    studio = rig.app.window.recording_studio
    assert guidance in studio._hint.text()
    assert guidance in studio._record_btn.toolTip()
    assert guidance in studio._record_btn.accessibleDescription()
    assert not studio._record_btn.isEnabled()
    assert studio._setup_btn.isEnabled()
    assert PRIVATE_DEVICE_DETAIL not in reason + caplog.text
    assert "PRIVATE_TRACK_NAME" not in reason
    rig.stream.assert_not_called()
    assert not tuple(rig.guest.originals_root.rglob("*.wav"))


def test_repeat_failure_notifies_once_and_fresh_success_clears(guest_rig, qapp):
    rig = guest_rig
    window = rig.app.window
    window.show()
    window.activateWindow()
    window.side_rail.trigger("canvas")
    editor = window.session_canvas._notes
    editor.setFocus()
    qapp.processEvents()
    assert editor.hasFocus()
    QTest.keyClicks(editor, "Keep typing through capture guidance")
    text, cursor = editor.toPlainText(), editor.textCursor().position()
    generation = rig.guest._guidance_notification_generation
    for _ in range(3):
        _unknown_contract(rig.guest)
        qapp.processEvents()
        assert editor.hasFocus()
        assert (editor.toPlainText(), editor.textCursor().position()) == (text, cursor)
    assert rig.guest._guidance_notification_generation == generation + 1
    assert rig.guest.local_capture_preflight is not None

    rig.app.settings.audio_samplerate = 48_000
    contract = rig.guest._current_local_original_contract()
    assert contract[0:2] == (True, 1)
    assert contract[2] and contract[4] == (2,)
    assert rig.guest.local_capture_preflight is None
    assert rig.guest._guidance_notification_generation == generation + 2
    rig.guest._current_local_original_contract()
    assert rig.guest._guidance_notification_generation == generation + 2
    qapp.processEvents()
    assert "require 48 kHz" not in rig.app._guest_recording_reason()
    assert "require 48 kHz" not in rig.app.window.recording_studio._hint.text()
    assert editor.hasFocus()
    assert (editor.toPlainText(), editor.textCursor().position()) == (text, cursor)
    rig.stream.assert_not_called()


@pytest.mark.parametrize("disable", ["preference", "all_tracks"])
def test_effective_opt_out_clears_unknown_without_changing_audio(guest_rig, disable):
    rig = guest_rig
    _unknown_contract(rig.guest)
    assert rig.guest.local_capture_preflight is not None
    before = (rig.app.settings.audio_samplerate, rig.app.settings.audio_blocksize)
    if disable == "preference":
        rig.app.settings.local_capture_enabled = False
    else:
        rig.app.settings.input_maps[0]["local_original_enabled"] = False
    contract = rig.guest._current_local_original_contract()
    assert contract[0:2] == (False, 0)
    assert contract[2] and contract[3:] == ((), (), ())
    assert rig.guest.local_capture_preflight is None
    assert (rig.app.settings.audio_samplerate, rig.app.settings.audio_blocksize) == before
    rig.stream.assert_not_called()


def test_presence_override_does_not_erase_still_enabled_preflight(guest_rig):
    rig = guest_rig
    _unknown_contract(rig.guest)
    original = rig.guest.local_capture_preflight
    assert original is not None
    contract = rig.guest._current_local_original_contract(capture_enabled=False)
    assert contract[0:2] == (False, 0)
    assert rig.app.settings.local_capture_enabled
    assert rig.guest.local_capture_preflight == original
    _unknown_contract(rig.guest)


@pytest.mark.parametrize("older_failure", [False, True])
def test_older_overlapping_preflight_cannot_replace_newer_result(
    guest_rig, older_failure,
):
    rig = guest_rig
    _unknown_contract(rig.guest)
    assert rig.guest.local_capture_preflight is not None
    rig.app.settings.audio_samplerate = 48_000
    entered = threading.Event()
    release = threading.Event()
    calls = []

    def query(*_args, **_kwargs):
        calls.append(object())
        older = len(calls) == 1
        if older:
            entered.set()
            assert release.wait(2), "older preflight was not released"
        if older is older_failure:
            raise RuntimeError(PRIVATE_DEVICE_DETAIL)
        return {"max_input_channels": 2}

    rig.query.side_effect = query
    with ThreadPoolExecutor(max_workers=1) as pool:
        older_check = pool.submit(rig.guest._current_local_original_contract)
        try:
            assert entered.wait(2), "older preflight did not reach the device"
            newer = rig.guest._current_local_original_contract()
            retained = rig.guest.local_capture_preflight
            if older_failure:
                assert newer[0:2] == (True, 1)
                assert retained is None
            else:
                assert newer == (True, None, "", None, (), ())
                assert retained.errors == ("input_device_or_format_unavailable",)
        finally:
            release.set()
        older = older_check.result(timeout=2)
    assert older[1] == (None if older_failure else 1)
    assert rig.guest.local_capture_preflight == retained
    rig.stream.assert_not_called()


@pytest.mark.parametrize("queued_before_replacement", [False, True])
def test_old_guest_guidance_cannot_refresh_replacement_ui(
    guest_rig, monkeypatch, queued_before_replacement,
):
    rig = guest_rig
    pending = []
    monkeypatch.setattr(rig.app._ui_invoker, "invoke", pending.append)
    if queued_before_replacement:
        rig.guest._on_guidance_changed()
    old_callbacks = tuple(pending)
    pending.clear()
    assert rig.app._configure_guest_peer(invitation())
    assert rig.app.guest_peer is not rig.guest
    pending.clear()
    render = Mock()
    hud = Mock()
    monkeypatch.setattr(rig.app, "_render_guest_peer_state", render)
    monkeypatch.setattr(rig.app, "_update_session_hud", hud)
    if not queued_before_replacement:
        rig.guest._on_guidance_changed()
    for callback in (*old_callbacks, *pending):
        callback()
    render.assert_not_called()
    hud.assert_not_called()


@pytest.mark.parametrize(
    "owner_state",
    ["active", "finalization", "host_recording", "host_finalizing",
     "host_attention", "selected_take"],
)
def test_old_preflight_does_not_override_active_or_preservation_guidance(
    guest_rig, monkeypatch, qapp, owner_state,
):
    rig = guest_rig
    _unknown_contract(rig.guest)
    qapp.processEvents()
    assert "require 48 kHz" in rig.app._guest_recording_reason()
    if owner_state == "active":
        monkeypatch.setattr(rig.guest, "_active_take_id", "active-take")
    elif owner_state == "finalization":
        monkeypatch.setattr(rig.guest, "_capture_finalization_needs_attention", True)
    elif owner_state == "selected_take":
        studio = rig.app.window.recording_studio
        selected_facts = replace(studio.guidance_facts(), take_selected=True)
        monkeypatch.setattr(studio, "guidance_facts", lambda: selected_facts)
    else:
        signal = {
            "host_recording": RecordingSignal.RECORDING,
            "host_finalizing": RecordingSignal.FINALIZING,
            "host_attention": RecordingSignal.NEEDS_ATTENTION,
        }[owner_state]
        rig.guest.last_state = SessionStateSnapshot(
            rig.invite.session_id, 1, signal, creator_profile_key="music",
        )
    if owner_state != "selected_take":
        assert "require 48 kHz" not in rig.app._guest_recording_reason()
    assert rig.guest.local_capture_preflight is not None
    studio = rig.app.window.recording_studio
    set_can_record = Mock(wraps=studio.set_can_record)
    monkeypatch.setattr(studio, "set_can_record", set_can_record)
    rig.guest._notify_guidance_changed()
    qapp.processEvents()
    set_can_record.assert_not_called()


@pytest.mark.parametrize("environment_keeps_capture_on", [False, True])
def test_studio_setup_opt_out_uses_effective_settings_without_rewriting_audio(
    guest_rig, monkeypatch, qapp, environment_keeps_capture_on,
):
    rig = guest_rig
    _unknown_contract(rig.guest)
    qapp.processEvents()
    assert "require 48 kHz" in rig.app.window.recording_studio._hint.text()
    if environment_keeps_capture_on:
        monkeypatch.setenv("WEBJAM_LOCAL_CAPTURE_ENABLED", "true")
    opened = []

    def choose_off(dialog):
        opened.append(dialog)
        assert dialog._capture.isChecked() and dialog._capture.isEnabled()
        dialog._capture.setChecked(False)
        dialog._save()
        return dialog.result()

    monkeypatch.setattr(
        "webjam_qt.windows.recording_setup.list_input_devices", lambda: [],
    )
    monkeypatch.setattr(RecordingSetupDialog, "exec", choose_off)
    rig.app.window.recording_studio._setup_btn.click()
    assert len(opened) == 1
    saved = json.loads(Path(rig.app.settings.config_file).read_text(encoding="utf-8"))
    assert saved["local_capture_enabled"] is False
    assert rig.app.settings.local_capture_enabled is environment_keeps_capture_on
    assert rig.app.settings.audio_samplerate == 44_100
    assert rig.app.settings.audio_blocksize == 256
    assert rig.app.guest_peer is rig.guest
    contract = rig.guest._current_local_original_contract()
    if environment_keeps_capture_on:
        assert contract == (True, None, "", None, (), ())
        assert rig.guest.local_capture_preflight is not None
    else:
        assert contract[0:2] == (False, 0)
        assert rig.guest.local_capture_preflight is None
    qapp.processEvents()
    assert (
        "require 48 kHz" in rig.app.window.recording_studio._hint.text()
    ) is environment_keeps_capture_on
    rig.stream.assert_not_called()


@pytest.mark.requires_local_socket
def test_studio_opt_out_publishes_fresh_authenticated_zero_track_host_proof(
    guest_rig, tmp_path, monkeypatch, qapp,
):
    rig = guest_rig
    credentials = SessionCredentials(rig.invite.session_id, rig.invite.invite_token)
    root = tmp_path / "authenticated-host"
    registry = EnrollmentRegistry(root, credentials, presence_clock=lambda: 100.0)
    control = SessionControlState(root, credentials.session_id)
    server = SessionPeerServer(
        "127.0.0.1", 0, registry=registry, control=control,
        transfers=TransferStore(root, credentials.session_id),
    )
    host = HostPeerSession()
    host.registry = registry
    host.control = control
    digest = hashlib.sha256(b"one-owned-guest-roster").hexdigest()
    registry.install_presence_v2_roster(
        digest, 1,
        host_roster_fingerprint=hashlib.sha256(b"host-private-roster").hexdigest(),
        ambiguous_ordinals=(), process_generation=11,
        rpc_connection_generation=12, audio_connection_generation=13,
    )
    start_capture = Mock(wraps=rig.guest._start_capture)
    begin_recording = Mock(wraps=control.begin)
    record_worker = Mock(side_effect=AssertionError("Setup started a recorder"))
    monkeypatch.setattr(rig.guest, "_start_capture", start_capture)
    monkeypatch.setattr(control, "begin", begin_recording)
    monkeypatch.setattr(rig.app, "_record_toggle_worker", record_worker)

    def publish_current_observation():
        rig.guest.observe_presence_v2(
            rig.guest.display_name, ordered_roster_digest=digest,
            roster_count=1, self_ordinal=0, process_generation=1,
            rpc_connection_generation=2, audio_connection_generation=3,
        )
        snapshot = rig.guest.poll_once()
        assert snapshot.signal is RecordingSignal.IDLE
        assert not rig.guest.last_presence_v2_error
        qapp.processEvents()

    opened = []

    def choose_off(dialog):
        opened.append(dialog)
        assert dialog._capture.isChecked() and dialog._capture.isEnabled()
        dialog._capture.setChecked(False)
        dialog._save()
        return dialog.result()

    monkeypatch.setattr(
        "webjam_qt.windows.recording_setup.list_input_devices", lambda: [],
    )
    monkeypatch.setattr(RecordingSetupDialog, "exec", choose_off)
    server.start()
    try:
        rig.guest.client = SessionPeerClient(
            "127.0.0.1", server.address[1], credentials=credentials,
        )
        publish_current_observation()
        (before,) = registry.recording_presence_snapshot()
        assert before.participant_id == rig.guest.participant_id
        assert before.recorder_eligible and before.capture_enabled
        assert before.local_original_track_count is None
        blocked_take = str(uuid.uuid4())
        blocked_obligations, issues = host.prepare_local_original_obligations(blocked_take)
        assert len(blocked_obligations) == 1
        assert blocked_obligations[0].track_count is None
        assert any("exact logical-track inventory" in issue for issue in issues)
        assert host.local_original_obligations_for_take(blocked_take) == ()
        assert "require 48 kHz" in rig.app.window.recording_studio._hint.text()

        rig.app.window.recording_studio._setup_btn.click()
        assert len(opened) == 1
        assert rig.app.settings.local_capture_enabled is False
        assert (rig.app.settings.audio_samplerate, rig.app.settings.audio_blocksize) == (
            44_100, 256,
        )
        # Saving a local preference cannot retroactively rewrite host evidence.
        assert registry.recording_presence_snapshot() == (before,)
        assert host.prepare_local_original_obligations(str(uuid.uuid4()))[1]

        publish_current_observation()
        (after,) = registry.recording_presence_snapshot()
        assert after.participant_id == before.participant_id
        assert after.presence_generation > before.presence_generation
        assert after.recorder_eligible and not after.capture_enabled
        assert after.local_original_track_count == 0
        assert after.local_original_topology_exact
        assert after.local_original_channel_counts == ()
        assert after.local_original_source_ids == ()
        allowed_take = str(uuid.uuid4())
        obligations, issues = host.prepare_local_original_obligations(allowed_take)
        assert issues == () and len(obligations) == 1
        assert obligations[0].exact_topology and obligations[0].track_count == 0
        assert host.local_original_obligations_for_take(allowed_take) == obligations
        assert rig.guest.local_capture_preflight is None
        assert "require 48 kHz" not in rig.app.window.recording_studio._hint.text()
        assert control.snapshot().signal is RecordingSignal.IDLE
        assert rig.guest._capture is None and not rig.guest.active_take_id
        assert not tuple(rig.guest.originals_root.rglob("*.wav"))
        start_capture.assert_not_called()
        begin_recording.assert_not_called()
        record_worker.assert_not_called()
        rig.stream.assert_not_called()
    finally:
        server.stop()
