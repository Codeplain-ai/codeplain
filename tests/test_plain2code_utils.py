"""Tests for pure helpers in plain2code_utils."""

from plain2code_utils import format_duration_hms


class TestFormatDurationHms:
    def test_zero_seconds(self):
        assert format_duration_hms(0) == "0s"

    def test_sub_minute_seconds(self):
        assert format_duration_hms(5) == "5s"
        assert format_duration_hms(49) == "49s"

    def test_sub_minute_multiple_of_ten(self):
        # Regression: trailing-zero seconds must not be stripped (10s, not 1s).
        assert format_duration_hms(10) == "10s"
        assert format_duration_hms(20) == "20s"
        assert format_duration_hms(30) == "30s"

    def test_exact_minute(self):
        assert format_duration_hms(60) == "1m 0s"

    def test_minutes_and_seconds(self):
        assert format_duration_hms(90) == "1m 30s"
        assert format_duration_hms(349) == "5m 49s"

    def test_hours_drop_seconds(self):
        assert format_duration_hms(3600) == "1h 0m"
        assert format_duration_hms(3661) == "1h 1m"

    def test_float_input_is_truncated(self):
        assert format_duration_hms(10.9) == "10s"
        assert format_duration_hms(349.4) == "5m 49s"

    def test_negative_input_clamped_to_zero(self):
        assert format_duration_hms(-5) == "0s"
