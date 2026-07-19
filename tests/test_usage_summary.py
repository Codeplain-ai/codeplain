"""Tests for the shared credit-usage summary line."""

from usage_summary import format_usage_summary


class TestFormatUsageSummary:
    def test_used_credits_equals_functionalities(self):
        line = format_usage_summary(6, 349)
        assert "functionalities  [#FFFFFF]6" in line
        assert "used credits  [#FFFFFF]6" in line
        assert "render time  [#FFFFFF]5m 49s" in line

    def test_zero_usage(self):
        line = format_usage_summary(0, 0)
        assert "functionalities  [#FFFFFF]0" in line
        assert "used credits  [#FFFFFF]0" in line
        assert "render time  [#FFFFFF]0s" in line

    def test_labels_use_muted_color(self):
        line = format_usage_summary(3, 10)
        assert line.startswith("[#8E8F91]functionalities")
        assert "[#8E8F91]used credits" in line
        assert "[#8E8F91]render time" in line

    def test_custom_colors(self):
        line = format_usage_summary(2, 5, label_color="#111111", value_color="#222222")
        assert "[#111111]functionalities  [#222222]2" in line
        assert "[#222222]5s" in line
