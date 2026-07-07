from __future__ import annotations

import unittest

from core.session_health import SessionHealth


class TestSessionHealth(unittest.TestCase):
    def test_process_and_participant_truth_are_separate(self):
        health = SessionHealth()

        health.mark_process("Running", rpc_available=True)

        self.assertEqual(health.process_state, "Running")
        self.assertTrue(health.rpc_available)
        self.assertFalse(health.connected)
        self.assertEqual(health.participant_count, 0)

        health.mark_participants(2, now=10.0)
        self.assertTrue(health.connected)
        self.assertEqual(health.participant_count, 2)
        self.assertAlmostEqual(health.participant_age(now=13.4), 3.4)

    def test_level_recorder_rpc_and_reset_public_snapshot(self):
        health = SessionHealth()

        health.mark_participants(1, now=20.0)
        health.mark_levels("rpc", now=21.0)
        health.mark_recorder(armed=True, recording=False)
        health.mark_rpc_result("self-mute", False, "Jamulus RPC rejected setMuted")

        data = health.to_public_dict(now=24.2)

        self.assertEqual(data["participant_age_s"], 4.2)
        self.assertEqual(data["level_age_s"], 3.2)
        self.assertEqual(data["meter_source"], "rpc")
        self.assertEqual(data["recorder_state"], "armed")
        self.assertEqual(
            data["last_rpc_result"],
            "self-mute failed: Jamulus RPC rejected setMuted",
        )

        health.mark_recorder(armed=True, recording=True)
        self.assertEqual(health.recorder_state, "recording")

        health.reset_live_truth()
        self.assertFalse(health.connected)
        self.assertEqual(health.participant_count, 0)
        self.assertIsNone(health.last_participant_at)
        self.assertIsNone(health.last_level_at)
        self.assertEqual(health.meter_source, "preview")
        self.assertEqual(health.recorder_state, "idle")


if __name__ == "__main__":
    unittest.main()
