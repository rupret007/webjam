"""Tests for core.models dataclasses."""

import unittest
from datetime import datetime, timezone

from core.models import AudioLevel, MixerSnapshot, ParticipantState


class TestAudioLevel(unittest.TestCase):
    def test_defaults(self):
        al = AudioLevel(channel_id=0)
        self.assertEqual(al.channel_id, 0)
        self.assertEqual(al.rms, 0.0)
        self.assertEqual(al.peak, 0.0)
        self.assertFalse(al.clipped)
        self.assertIsInstance(al.timestamp, datetime)

    def test_custom_values(self):
        al = AudioLevel(channel_id=5, rms=0.8, peak=1.0, clipped=True)
        self.assertEqual(al.rms, 0.8)
        self.assertTrue(al.clipped)

    def test_timestamp_utc(self):
        al = AudioLevel(channel_id=0)
        self.assertEqual(al.timestamp.tzinfo, timezone.utc)

    def test_boundary_rms(self):
        al = AudioLevel(channel_id=0, rms=-1.0)
        self.assertEqual(al.rms, -1.0)

    def test_boundary_peak(self):
        al = AudioLevel(channel_id=0, peak=999.0)
        self.assertEqual(al.peak, 999.0)


class TestParticipantState(unittest.TestCase):
    def test_defaults(self):
        ps = ParticipantState(channel_id=1, name="Alice")
        self.assertEqual(ps.channel_id, 1)
        self.assertEqual(ps.name, "Alice")
        self.assertEqual(ps.ip_address, "")
        self.assertTrue(ps.is_connected)
        self.assertEqual(ps.fader_level, 100)
        self.assertEqual(ps.pan, 50)
        self.assertFalse(ps.muted)
        self.assertFalse(ps.solo)
        self.assertEqual(ps.audio_level, 0.0)

    def test_custom_values(self):
        ps = ParticipantState(
            channel_id=3,
            name="Bob",
            ip_address="10.0.0.1",
            is_connected=False,
            fader_level=80,
            pan=30,
            muted=True,
            solo=True,
            audio_level=0.5,
        )
        self.assertEqual(ps.fader_level, 80)
        self.assertTrue(ps.muted)

    def test_boundary_fader(self):
        ps = ParticipantState(channel_id=0, name="X", fader_level=0)
        self.assertEqual(ps.fader_level, 0)

    def test_boundary_pan(self):
        ps = ParticipantState(channel_id=0, name="X", pan=0)
        self.assertEqual(ps.pan, 0)
        ps2 = ParticipantState(channel_id=0, name="X", pan=100)
        self.assertEqual(ps2.pan, 100)


class TestMixerSnapshot(unittest.TestCase):
    def test_empty_participants(self):
        snap = MixerSnapshot(participants={})
        self.assertEqual(len(snap.participants), 0)
        self.assertIsInstance(snap.created_at, datetime)
        self.assertIsNone(snap.server_host)
        self.assertIsNone(snap.server_port)

    def test_with_participants(self):
        p1 = ParticipantState(channel_id=1, name="Alice")
        p2 = ParticipantState(channel_id=2, name="Bob")
        snap = MixerSnapshot(
            participants={1: p1, 2: p2},
            server_host="jam.example.com",
            server_port=22124,
        )
        self.assertEqual(len(snap.participants), 2)
        self.assertEqual(snap.server_host, "jam.example.com")
        self.assertEqual(snap.server_port, 22124)


if __name__ == "__main__":
    unittest.main()
