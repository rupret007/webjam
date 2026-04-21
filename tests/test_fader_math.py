"""
Tests for fader dB formatting from webjam_qt.widgets.participant_card.

Uses QT_QPA_PLATFORM=offscreen so no display is required.
"""
from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Create QApplication before importing any Qt widgets
from PySide6.QtWidgets import QApplication
_app = QApplication.instance() or QApplication([])

from webjam_qt.widgets.participant_card import ParticipantCard


class TestFormatFader:
    """Tests for the ParticipantCard._format_fader static method."""

    def test_fader_zero_is_negative_inf(self):
        """_format_fader(0) returns negative infinity symbol."""
        result = ParticipantCard._format_fader(0)
        assert result == "-\u221e dB"

    def test_fader_100_is_unity(self):
        """_format_fader(100) returns 0.0 dB (unity gain)."""
        result = ParticipantCard._format_fader(100)
        assert result == "0.0 dB"

    def test_fader_127_is_plus_six(self):
        """_format_fader(127) returns +6.0 dB."""
        result = ParticipantCard._format_fader(127)
        assert result == "+6.0 dB"

    def test_fader_50_is_negative(self):
        """_format_fader(50) is approximately -6.0 dB (20*log10(0.5) = -6.02...)."""
        result = ParticipantCard._format_fader(50)
        assert result == "-6.0 dB"

    def test_fader_1_is_very_negative(self):
        """_format_fader(1) should be very negative (< -30 dB)."""
        result = ParticipantCard._format_fader(1)
        # Should be "-40.0 dB" — 20*log10(0.01) = -40.0
        assert result == "-40.0 dB"

    def test_fader_113_is_positive(self):
        """_format_fader(113) should be a positive dB value between 0 and 6."""
        result = ParticipantCard._format_fader(113)
        # (113-100)/27*6 = 78/27 = 2.888... -> "+2.9 dB"
        assert result == "+2.9 dB"
        # Also verify it parses to a float between 0 and 6
        db_value = float(result.replace("+", "").replace(" dB", ""))
        assert 0.0 < db_value < 6.0

    def test_fader_negative_value_is_negative_inf(self):
        """_format_fader with value <= 0 returns -inf symbol."""
        result = ParticipantCard._format_fader(-1)
        assert result == "-\u221e dB"
