"""Tests for pure helpers in plain2code_utils."""

import re

import pytest

from plain2code_utils import format_duration_hms, truncate_middle

OMISSION_MARKER = re.compile(r"\n\n\[\.\.\. ([\d,]+) characters omitted \.\.\.\]\n\n")


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


def split_at_marker(truncated: str) -> tuple[str, int, str]:
    """Splits truncated text into the kept start, the reported omitted count, and the kept end."""
    match = OMISSION_MARKER.search(truncated)
    assert match, f"no omission marker in {truncated!r}"
    return truncated[: match.start()], int(match.group(1).replace(",", "")), truncated[match.end() :]


def numbered_text(length: int) -> str:
    """Text whose every position is distinguishable, so a wrong slice cannot pass by accident."""
    return "".join(str(i % 10) if i % 7 else "\n" for i in range(length))


class TestTruncateMiddle:
    def test_text_under_budget_is_unchanged(self):
        assert truncate_middle("1 failed", 100) == ("1 failed", 0)

    def test_empty_text_is_unchanged(self):
        assert truncate_middle("", 100) == ("", 0)

    def test_text_exactly_at_budget_is_unchanged(self):
        text = numbered_text(100)
        assert truncate_middle(text, 100) == (text, 0)

    def test_text_one_over_budget_fits_and_keeps_both_ends(self):
        text = "S" + "m" * 99 + "E"

        truncated, omitted = truncate_middle(text, 100)

        assert len(truncated) <= 100
        assert truncated.startswith("S")
        assert truncated.endswith("E")
        assert omitted > 0

    def test_kept_parts_are_the_exact_start_and_end_of_the_text(self):
        text = numbered_text(1000)

        truncated, omitted = truncate_middle(text, 200)

        start, reported, end = split_at_marker(truncated)
        assert text.startswith(start)
        assert text.endswith(end)
        assert reported == omitted
        assert len(start) + omitted + len(end) == len(text)

    def test_start_and_end_get_equal_shares(self):
        start, _, end = split_at_marker(truncate_middle(numbered_text(1000), 200)[0])

        assert len(start) == len(end)

    def test_very_long_text_fits_budget_with_a_thousands_separated_count(self):
        text = numbered_text(5_000_000)

        truncated, omitted = truncate_middle(text, 10_000)

        assert len(truncated) <= 10_000
        start, _, end = split_at_marker(truncated)
        assert len(start) + omitted + len(end) == 5_000_000
        assert f"[... {omitted:,} characters omitted ...]" in truncated
        assert omitted > 4_990_000

    def test_findings_printed_last_survive(self):
        output = "".join(f"spec/models/user_spec.rb:{i} example passed\n" for i in range(1000))
        output += "app/models/user.rb:3:1: C: Style/FrozenStringLiteralComment: Missing magic comment.\n"

        truncated, _ = truncate_middle(output, 10_000)

        assert truncated.endswith(
            "app/models/user.rb:3:1: C: Style/FrozenStringLiteralComment: Missing magic comment.\n"
        )

    def test_budget_too_small_for_the_marker_is_rejected(self):
        with pytest.raises(ValueError):
            truncate_middle(numbered_text(1000), 20)
