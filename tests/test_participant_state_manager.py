"""Tests for ``ParticipantStateManager`` — Round-4 extraction of mixer
state out of ``JamulusController``.  Each test wires up the manager with
``MagicMock`` callbacks so we can assert the side effects (mixer apply,
RPC gain, cached-participants push, listener notify) without spinning up
the network layer.
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from jamulus_state_manager import JamulusParticipant, ParticipantStateManager


def _build_manager() -> tuple[ParticipantStateManager, dict]:
    callbacks = {
        "apply_mixer": MagicMock(),
        "set_cached": MagicMock(),
        "send_rpc_gain": MagicMock(),
        "notify": MagicMock(),
    }
    manager = ParticipantStateManager(
        apply_mixer_setting=callbacks["apply_mixer"],
        set_cached_participants=callbacks["set_cached"],
        send_rpc_gain=callbacks["send_rpc_gain"],
        notify_callbacks=callbacks["notify"],
        logger=None,
    )
    return manager, callbacks


class TestParticipantStateManager(unittest.TestCase):
    def test_add_then_get_participants_round_trip(self):
        manager, cbs = _build_manager()
        manager.add_participant("Alice", 0)
        manager.add_participant("Bob", 1)

        names = sorted(p.name for p in manager.get_participants())
        self.assertEqual(names, ["Alice", "Bob"])
        self.assertEqual(manager.participants[0].channel_id, 0)
        self.assertEqual(manager.participants[1].channel_id, 1)

        # The transport-cache callback must receive the latest snapshot
        # after each add — the controller forwards this to the UDP adapter.
        cbs["set_cached"].assert_called()
        last_snapshot = cbs["set_cached"].call_args_list[-1].args[0]
        self.assertEqual(last_snapshot, {0: "Alice", 1: "Bob"})

        # No solo active => add_participant should NOT call apply_mixer
        # (the original controller only applied mixer for the late-joiner-
        # during-solo case).
        cbs["apply_mixer"].assert_not_called()
        # But callbacks must be notified for every add.
        self.assertEqual(cbs["notify"].call_count, 2)

    def test_set_solo_pre_mute_snapshot_captured_correctly(self):
        manager, cbs = _build_manager()
        manager.add_participant("Lead", 0)
        manager.add_participant("Rhythm", 1)
        manager.add_participant("Bass", 2)
        # Pre-solo: mute one channel so the snapshot has something to restore.
        manager.set_mute(1, True)

        # Sanity: no solo yet, snapshot should still be empty.
        self.assertEqual(manager._pre_solo_mute, {})

        manager.set_solo(0, True)
        # The snapshot must record each channel's pre-solo mute state.
        self.assertEqual(manager._pre_solo_mute, {0: False, 1: True, 2: False})
        # While solo is active: lead is unmuted, others muted.
        self.assertFalse(manager.participants[0].muted)
        self.assertTrue(manager.participants[0].solo)
        self.assertTrue(manager.participants[1].muted)
        self.assertTrue(manager.participants[2].muted)

        # Leaving solo restores the captured snapshot exactly.
        manager.set_solo(0, False)
        self.assertEqual(manager._pre_solo_mute, {})
        self.assertFalse(manager.participants[0].muted)
        self.assertTrue(manager.participants[1].muted)   # restored from snapshot
        self.assertFalse(manager.participants[2].muted)  # restored from snapshot

        # set_solo must propagate gain via the RPC callback for every
        # affected channel both on entry and on exit.
        rpc_calls = cbs["send_rpc_gain"].call_args_list
        self.assertGreaterEqual(len(rpc_calls), 6)  # 3 enter + 3 exit at minimum

    def test_apply_mix_data_routes_by_channel_id(self):
        manager, cbs = _build_manager()
        manager.add_participant("Alice", 7)
        manager.add_participant("Bob", 8)

        applied = manager.apply_mix_data(
            {
                "participants": [
                    {"channel_id": 7, "fader_level": 30, "pan": 60, "muted": False},
                    {"channel_id": 8, "fader_level": 90, "pan": 40, "muted": True},
                    # Stranger payload — no matching id, no name to match by.
                    {"channel_id": 99, "fader_level": 5},
                ]
            }
        )

        # Two of three rows landed; the strange one was skipped.
        self.assertEqual(applied, 2)
        self.assertEqual(manager.participants[7].fader_level, 30)
        self.assertEqual(manager.participants[7].pan, 60)
        self.assertFalse(manager.participants[7].muted)
        self.assertEqual(manager.participants[8].fader_level, 90)
        self.assertTrue(manager.participants[8].muted)

        # apply_mix_data calls apply_mixer once per matched row (best-effort
        # transport push).
        applied_ids = {call.args[0] for call in cbs["apply_mixer"].call_args_list}
        self.assertEqual(applied_ids, {7, 8})

    def test_serialize_then_apply_round_trip(self):
        manager, _ = _build_manager()
        manager.add_participant("Alice", 0)
        manager.add_participant("Bob", 1)
        manager.set_fader_level(0, 64)
        manager.set_fader_level(1, 110)
        manager.set_mute(1, True)

        snapshot = manager.serialize_mix()
        # Restart-style: build a brand-new manager with the same channels.
        target, _ = _build_manager()
        target.add_participant("Alice", 0)
        target.add_participant("Bob", 1)

        applied = target.apply_mix_data(snapshot)
        self.assertEqual(applied, 2)
        self.assertEqual(target.participants[0].fader_level, 64)
        self.assertEqual(target.participants[0].muted, False)
        self.assertEqual(target.participants[1].fader_level, 110)
        self.assertTrue(target.participants[1].muted)
        # Solo flags survive too.
        self.assertFalse(target.participants[0].solo)
        self.assertFalse(target.participants[1].solo)

    def test_apply_mix_data_returns_none_for_invalid_payload(self):
        manager, _ = _build_manager()
        self.assertIsNone(manager.apply_mix_data({"bad": []}))
        self.assertIsNone(manager.apply_mix_data("nope"))

    def test_remove_soloed_participant_restores_snapshot(self):
        # Mirrors the controller-edge equivalent — proves the same invariant
        # holds at the manager level.
        manager, _ = _build_manager()
        manager.add_participant("Solo", 0)
        manager.add_participant("Muted", 1)
        manager.add_participant("Open", 2)
        manager.set_mute(1, True)
        manager.set_solo(0, True)

        manager.remove_participant(0)

        self.assertFalse(any(p.solo for p in manager.get_participants()))
        self.assertTrue(manager.participants[1].muted)
        self.assertFalse(manager.participants[2].muted)
        self.assertEqual(manager._pre_solo_mute, {})

    def test_jamulus_participant_default_factory(self):
        # Sanity-check the dataclass moved across cleanly.
        p = JamulusParticipant(channel_id=3, name="X")
        self.assertEqual(p.fader_level, 100)
        self.assertEqual(p.pan, 50)
        self.assertFalse(p.muted)
        self.assertFalse(p.solo)
        self.assertFalse(p.is_local)


if __name__ == "__main__":
    unittest.main()
