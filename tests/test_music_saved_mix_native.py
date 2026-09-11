"""Saved personal mixes reach native gain framing without sound or sockets.

The shared fixture constructs a real JamulusController and JamulusRpcClient;
only machine boundaries/readiness and worker scheduling are synthetic. Mix
files here live in a temporary synthetic home, never the user's settings.
"""

import json
from types import SimpleNamespace

import pytest

from tests import test_music_effective_gain as native_test_support
from webjam_qt.controllers.mix_manager import MixManager

mixer = native_test_support.mixer


@pytest.fixture
def saved_mixer(mixer, monkeypatch, tmp_path):
    monkeypatch.setattr("webjam_qt.controllers.mix_manager.Path.home", lambda: tmp_path)
    flashes = []
    manager = MixManager(mixer.controller, lambda text, ms: flashes.append(text))
    assert not mixer.controller.protocol.enabled
    return SimpleNamespace(
        controller=mixer.controller,
        sink=mixer.sink,
        manager=manager,
        flashes=flashes,
        directory=tmp_path,
    )


def _row(channel, name, fader, *, muted=False, solo=False):
    return {
        "channel_id": channel, "name": name, "fader_level": fader,
        "pan": 50, "muted": muted, "solo": solo,
    }


def _model(controller):
    return {
        cid: (person.fader_level, person.muted, person.solo)
        for cid, person in controller.participants.items()
    }


def _record_command_models(native):
    """Inspect outside callbacks so RPC error swallowing cannot hide failure."""
    frames = []
    original = native.sink.sendall

    def record(raw):
        original(raw)
        frames.append((json.loads(raw)["params"], _model(native.controller)))

    native.sink.sendall = record
    return frames


def _save(native, mode):
    path = native.directory / ("named-mix.json" if mode == "named" else ".webjam_mix.json")
    assert native.manager.save_to(path) if mode == "named" else native.manager.save()
    return path


def _restore(native, mode, path):
    native.flashes.clear()
    if mode == "named":
        assert native.manager.load_from(path)
        assert native.flashes == ["Mix loaded from named-mix.json"]
    elif mode == "default":
        assert native.manager.load()
        assert native.flashes == ["Mix loaded"]
    else:
        native.manager.auto_restore()
        assert native.flashes == []


@pytest.mark.parametrize("mode", ["default", "named", "auto"])
def test_real_saved_mix_restores_native_mute_and_chosen_faders(saved_mixer, mode):
    native = saved_mixer
    controller = native.controller
    controller.set_fader_level(7, 40)
    controller.set_mute(7, True)
    controller.set_fader_level(8, 55)
    controller.set_fader_level(9, 90)
    path = _save(native, mode)
    saved_bytes = path.read_bytes()
    assert all("pre_solo_muted" not in row for row in json.loads(saved_bytes)["participants"])
    controller.set_mute(7, False)
    for channel in (7, 8, 9):
        controller.set_fader_level(channel, 127)
    assert native.sink.gains == {7: 100, 8: 100, 9: 100}
    frames = _record_command_models(native)

    _restore(native, mode, path)

    expected = {7: (40, True, False), 8: (55, False, False), 9: (90, False, False)}
    assert _model(controller) == expected
    assert native.sink.gains == {7: 0, 8: 43, 9: 71}
    assert frames and all(model == expected for _, model in frames)
    assert path.read_bytes() == saved_bytes
    controller.set_mute(7, False)
    assert native.sink.gains[7] == 31


def test_full_non_solo_restore_replaces_current_solo_before_native_commands(saved_mixer):
    native = saved_mixer
    controller = native.controller
    controller.set_fader_level(7, 40)
    controller.set_mute(7, True)
    controller.set_fader_level(8, 55)
    controller.set_fader_level(9, 90)
    path = _save(native, "default")
    controller.set_solo(9, True)
    assert native.sink.gains[8] == 0
    frames = _record_command_models(native)

    _restore(native, "default", path)

    expected = {7: (40, True, False), 8: (55, False, False), 9: (90, False, False)}
    assert _model(controller) == expected
    assert native.sink.gains == {7: 0, 8: 43, 9: 71}
    assert frames and all(model == expected for _, model in frames)
    assert controller._state._pre_solo_mute == {}


@pytest.mark.parametrize("pre_muted, mute_during_solo", [(False, None), (True, None), (False, True)])
def test_saved_solo_roundtrip_preserves_personal_mutes_and_explicit_selected_mute(saved_mixer, pre_muted, mute_during_solo):
    native = saved_mixer
    controller = native.controller
    controller.set_fader_level(7, 55)
    controller.set_fader_level(8, 90)
    controller.set_mute(9, True)
    controller.set_mute(8, pre_muted)
    controller.set_solo(8, True)
    if mute_during_solo is not None:
        controller.set_mute(8, mute_during_solo)
    selected_muted = mute_during_solo is True
    personal_muted = pre_muted if mute_during_solo is None else mute_during_solo
    before = _model(controller)
    path = _save(native, "named")
    saved = json.loads(path.read_text())
    by_id = {row["channel_id"]: row for row in saved["participants"]}
    assert by_id[7]["muted"] is True
    assert by_id[7]["pre_solo_muted"] is False  # Suppression is not personal Mute.
    assert by_id[8]["muted"] is selected_muted
    assert by_id[8]["pre_solo_muted"] is personal_muted
    assert by_id[9]["muted"] is True
    assert by_id[9]["pre_solo_muted"] is True
    assert _model(controller) == before  # Saving itself is not a mixer gesture.
    controller.set_solo(8, False)
    for channel in (7, 8, 9):
        controller.set_mute(channel, False)
        controller.set_fader_level(channel, 127)
    frames = _record_command_models(native)

    _restore(native, "named", path)

    expected = {7: (55, True, False), 8: (90, selected_muted, True), 9: (100, True, False)}
    assert _model(controller) == expected
    assert native.sink.gains == {7: 0, 8: 0 if selected_muted else 71, 9: 0}
    assert frames and all(model == expected for _, model in frames)
    controller.set_solo(8, False)
    assert native.sink.gains == {7: 43, 8: 0 if personal_muted else 71, 9: 0}
    assert not controller.participants[7].muted
    assert controller.participants[8].muted is personal_muted
    assert controller.participants[9].muted


def test_legacy_solo_payload_honors_stored_mutes_without_inventing_prior_choices(saved_mixer):
    native = saved_mixer
    payload = {"participants": [
        _row(7, "WebJam Track", 55, muted=True),
        _row(8, "Collaborator", 90, solo=True),
        _row(9, "My monitor", 100, muted=True),
    ]}
    assert native.controller.apply_mix_data(payload) == 3
    assert native.sink.gains == {7: 0, 8: 71, 9: 0}
    native.controller.set_solo(8, False)
    assert native.sink.gains == {7: 0, 8: 71, 9: 0}


def test_partial_restore_keeps_unaffected_solo_and_saves_suppressed_personal_choice(saved_mixer):
    native = saved_mixer
    controller = native.controller
    controller.set_mute(9, True)
    controller.set_solo(8, True)
    frames = _record_command_models(native)

    assert controller.apply_mix_data({"participants": [_row(7, "WebJam Track", 55)]}) == 1

    expected = {7: (55, True, False), 8: (100, False, True), 9: (100, True, False)}
    assert _model(controller) == expected
    assert native.sink.gains == {7: 0, 8: 79, 9: 0}
    assert frames and all(model == expected for _, model in frames)
    controller.set_solo(8, False)
    assert native.sink.gains == {7: 43, 8: 79, 9: 0}


def test_last_final_solo_candidate_is_exclusive_before_any_native_apply(saved_mixer):
    native = saved_mixer
    frames = _record_command_models(native)
    payload = {"participants": [
        _row(7, "WebJam Track", 40, solo=True),
        _row(8, "Collaborator", 90, solo=True, muted=True),
        _row(9, "My monitor", 55),
    ]}

    assert native.controller.apply_mix_data(payload) == 3

    expected = {7: (40, True, False), 8: (90, True, True), 9: (55, True, False)}
    assert _model(native.controller) == expected
    assert native.sink.gains == {7: 0, 8: 0, 9: 0}
    assert frames and all(model == expected for _, model in frames)
    native.controller.set_solo(8, False)
    assert native.sink.gains == {7: 31, 8: 0, 9: 43}


def test_duplicate_row_final_false_does_not_revive_earlier_solo(saved_mixer):
    native = saved_mixer
    frames = _record_command_models(native)
    payload = {"participants": [
        _row(7, "WebJam Track", 40, solo=True),
        _row(8, "Collaborator", 90),
        _row(7, "WebJam Track", 55, solo=False),
    ]}

    assert native.controller.apply_mix_data(payload) == 2

    expected = {7: (55, False, False), 8: (90, False, False), 9: (100, False, False)}
    assert _model(native.controller) == expected
    assert native.sink.gains == {7: 43, 8: 71}
    assert frames and all(model == expected for _, model in frames)


@pytest.mark.parametrize("saved_name", [None, "", "  "])
def test_legacy_row_without_name_keeps_channel_id_matching(saved_mixer, saved_name):
    native = saved_mixer
    row = _row(7, saved_name, 55)
    if saved_name is None:
        row.pop("name")
    assert native.controller.apply_mix_data({"participants": [row]}) == 1
    assert native.sink.gains == {7: 43}
    assert native.controller.participants[8].fader_level == 100


@pytest.mark.parametrize("saved_channel", [8, 55])
def test_saved_name_remaps_to_unique_current_identity_instead_of_reused_channel(saved_mixer, saved_channel):
    native = saved_mixer
    payload = {"participants": [_row(saved_channel, "  WEBJAM TRACK  ", 40, muted=True)]}

    assert native.controller.apply_mix_data(payload) == 1

    assert native.sink.gains == {7: 0}
    assert _model(native.controller) == {
        7: (40, True, False), 8: (100, False, False), 9: (100, False, False),
    }


def test_ambiguous_name_and_unmatched_rows_never_change_current_mix(saved_mixer):
    native = saved_mixer
    native.controller.add_participant("Collaborator", 10)
    native.controller.set_solo(7, True)
    before = _model(native.controller)
    commands = list(native.sink.commands)
    payload = {"participants": [
        _row(55, "COLLABORATOR", 40, solo=True),
        _row(8, "Unknown visitor", 90),
        _row(99, "Gone", 0, muted=True),
    ]}

    assert native.controller.apply_mix_data(payload) == 0

    assert _model(native.controller) == before
    assert native.sink.commands == commands


@pytest.mark.parametrize("payload", [None, [], {}, {"participants": None}, {"participants": "wrong"}])
def test_malformed_top_level_never_applies_partial_native_mix(saved_mixer, payload):
    native = saved_mixer
    before = _model(native.controller)
    assert native.controller.apply_mix_data(payload) is None
    assert _model(native.controller) == before
    assert native.sink.commands == []


def test_invalid_rows_are_ignored_and_valid_stored_values_remain_bounded(saved_mixer):
    native = saved_mixer
    payload = {"participants": [
        None, [], "wrong", {"channel_id": "not a channel"},
        _row(7, "WebJam Track", "999", muted="off"),
        _row(8, "Collaborator", -4, muted="yes"),
    ]}

    assert native.controller.apply_mix_data(payload) == 2

    assert native.sink.gains == {7: 100, 8: 0}
    assert _model(native.controller) == {
        7: (127, False, False), 8: (0, True, False), 9: (100, False, False),
    }


@pytest.mark.parametrize("invalid_mute", [None, "not a boolean", [], {}])
@pytest.mark.parametrize("personal_muted", [False, True])
def test_invalid_stored_mute_during_solo_preserves_personal_choice(saved_mixer, invalid_mute, personal_muted):
    native = saved_mixer
    controller = native.controller
    controller.set_mute(7, personal_muted)
    controller.set_solo(8, True)
    assert controller.participants[7].muted
    assert native.sink.gains[7] == 0

    assert controller.apply_mix_data({"participants": [
        _row(7, "WebJam Track", 55, muted=invalid_mute),
    ]}) == 1

    assert controller.participants[7].muted
    assert controller.participants[7].fader_level == 55
    assert controller.participants[8].solo
    assert native.sink.gains[7] == 0
    controller.set_solo(8, False)
    assert controller.participants[7].muted is personal_muted
    assert native.sink.gains[7] == (0 if personal_muted else 43)


@pytest.mark.parametrize("number", ["1e999", "-1e999", "NaN"])
def test_nonfinite_json_fields_preserve_prior_values_and_apply_finite_rows_together(saved_mixer, number):
    native = saved_mixer
    controller = native.controller
    controller.set_fader_level(7, 40)
    controller.set_pan(7, 25)
    path = native.directory / "nonfinite-mix.json"
    # Parse actual JSON through MixManager, including Python's accepted NaN
    # and overflowing exponent spellings. The unidentifiable row must skip.
    path.write_text(
        '{"participants": ['
        '{"channel_id":7,"name":"WebJam Track","fader_level":' + number + ',"pan":' + number + '},'
        '{"channel_id":' + number + ',"fader_level":0,"muted":true},'
        '{"channel_id":8,"name":"Collaborator","fader_level":55,"pan":75,"muted":false}'
        ']}'
    )
    saved_bytes = path.read_bytes()
    frames = _record_command_models(native)
    callback_models = []
    controller.callbacks.append(lambda participants: callback_models.append(_model(controller)))

    assert native.manager.load_from(path) is True

    expected = {7: (40, False, False), 8: (55, False, False), 9: (100, False, False)}
    assert _model(controller) == expected
    assert controller.participants[7].pan == 25
    assert controller.participants[8].pan == 75
    assert native.sink.gains == {7: 31, 8: 43}
    assert frames and all(model == expected for _, model in frames)
    assert callback_models and all(model == expected for model in callback_models)
    assert path.read_bytes() == saved_bytes


@pytest.mark.parametrize("mode", ["default", "named", "auto"])
@pytest.mark.parametrize("payload, message", [
    ({"participants": "wrong"}, "This file has no valid mix. Choose another file or save a fresh mix."),
    ({"participants": [_row(50, "Absent musician", 40)]}, "No matching participants. Join the intended session or choose another mix."),
])
def test_load_cannot_report_restored_when_file_is_invalid_or_no_one_matches(saved_mixer, mode, payload, message):
    native = saved_mixer
    path = native.directory / ("named-mix.json" if mode == "named" else ".webjam_mix.json")
    path.write_text(json.dumps(payload))
    saved_bytes = path.read_bytes()
    before = _model(native.controller)
    card_updates = []
    native.controller.callbacks.append(lambda participants: card_updates.append(participants))

    if mode == "named":
        assert native.manager.load_from(path) is False
    elif mode == "default":
        assert native.manager.load() is False
    else:
        assert native.manager.auto_restore() is None

    assert native.flashes == ([] if mode == "auto" else [message])
    assert native.sink.commands == []
    assert card_updates == []
    assert _model(native.controller) == before
    assert path.read_bytes() == saved_bytes
