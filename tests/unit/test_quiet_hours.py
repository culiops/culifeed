"""Tests for the quiet-hours window helper."""
from datetime import datetime

import pytest

from culifeed.scheduler.quiet_hours import in_quiet_hours


def _at(h: int, m: int = 0, s: int = 0) -> datetime:
    return datetime(2026, 4, 29, h, m, s)


class TestWrappingWindow:
    """start > end ⇒ window crosses midnight (e.g. 22→7)."""

    @pytest.mark.parametrize("hour", [22, 23, 0, 1, 6])
    def test_inside_window(self, hour):
        assert in_quiet_hours(_at(hour), 22, 7) is True

    @pytest.mark.parametrize("hour", [7, 8, 12, 21])
    def test_outside_window(self, hour):
        assert in_quiet_hours(_at(hour), 22, 7) is False

    def test_exact_start_is_quiet(self):
        assert in_quiet_hours(_at(22, 0, 0), 22, 7) is True

    def test_one_second_before_end_is_quiet(self):
        assert in_quiet_hours(_at(6, 59, 59), 22, 7) is True

    def test_exact_end_is_active(self):
        assert in_quiet_hours(_at(7, 0, 0), 22, 7) is False


class TestNonWrappingWindow:
    """start < end ⇒ window within a single day (e.g. 1→5 = quiet 01:00-04:59)."""

    @pytest.mark.parametrize("hour", [1, 2, 4])
    def test_inside_window(self, hour):
        assert in_quiet_hours(_at(hour), 1, 5) is True

    @pytest.mark.parametrize("hour", [0, 5, 12, 23])
    def test_outside_window(self, hour):
        assert in_quiet_hours(_at(hour), 1, 5) is False


class TestEqualStartEnd:
    """start == end ⇒ no quiet hours, always active."""

    @pytest.mark.parametrize("hour", range(0, 24))
    def test_always_active(self, hour):
        assert in_quiet_hours(_at(hour), 0, 0) is False
        assert in_quiet_hours(_at(hour), 12, 12) is False
