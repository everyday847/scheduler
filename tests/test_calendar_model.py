"""Tests for the calendar model helpers in schedule_solver.py."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from parafrost_scheduler.schedule_solver import (
    _day_of_week,
    _day_to_week,
    _num_weeks_for,
    _week_day,
)


class TestDayOfWeek:
    """Tests for _day_of_week(d, start_dow)."""

    def test_day_zero_wednesday(self):
        """July 1, 2026 is a Wednesday (start_dow=2). Day 0 should be Wed."""
        assert _day_of_week(0, 2) == 2

    def test_day_two_is_friday(self):
        """Day 2 with start_dow=2 → (2+2)%7 = 4 = Friday."""
        assert _day_of_week(2, 2) == 4

    def test_day_five_is_monday(self):
        """Day 5 with start_dow=2 → (2+5)%7 = 0 = Monday."""
        assert _day_of_week(5, 2) == 0

    def test_start_dow_zero(self):
        """When start_dow=0 (Monday), day d has dow = d % 7."""
        for d in range(14):
            assert _day_of_week(d, 0) == d % 7

    def test_wraps_around(self):
        """Day-of-week wraps correctly."""
        # start_dow=5 (Saturday), day 2 → (5+2)%7 = 0 = Monday
        assert _day_of_week(2, 5) == 0


class TestDayToWeek:
    """Tests for _day_to_week(d, start_dow)."""

    def test_first_day_is_week_zero(self):
        """Day 0 is always in week 0."""
        assert _day_to_week(0, 2) == 0

    def test_still_week_zero(self):
        """Day 4 with start_dow=2: (2+4)//7 = 0."""
        assert _day_to_week(4, 2) == 0

    def test_second_week_starts(self):
        """Day 5 with start_dow=2: (2+5)//7 = 1."""
        assert _day_to_week(5, 2) == 1

    def test_start_dow_zero_matches_simple(self):
        """When start_dow=0, week = d // 7."""
        for d in range(30):
            assert _day_to_week(d, 0) == d // 7

    def test_week_boundary_start_dow_6(self):
        """start_dow=6 (Sunday): day 0 is week 0, day 1 is week 1."""
        assert _day_to_week(0, 6) == 0
        assert _day_to_week(1, 6) == 1


class TestNumWeeksFor:
    """Tests for _num_weeks_for(start_dow, num_days)."""

    def test_2026_2027_academic_year(self):
        """July 1 2026 (Wed, start_dow=2), 365 days → 53 weeks."""
        assert _num_weeks_for(2, 365) == 53

    def test_start_monday_365(self):
        """start_dow=0, 365 days: (0+364)//7+1 = 52+1 = 53."""
        assert _num_weeks_for(0, 365) == 53

    def test_start_monday_364(self):
        """start_dow=0, 364 days: (0+363)//7+1 = 51+1 = 52."""
        assert _num_weeks_for(0, 364) == 52

    def test_start_sunday_365(self):
        """start_dow=6, 365 days: (6+364)//7+1 = 52+1 = 53."""
        # partial first week (1 day), 52 full weeks, partial last week
        assert _num_weeks_for(6, 365) == 53

    def test_leap_year_366(self):
        """start_dow=2, 366 days: (2+365)//7+1 = 52+1 = 53."""
        assert _num_weeks_for(2, 366) == 53


class TestWeekDay:
    """Tests for _week_day(w, dow_target, start_dow)."""

    def test_friday_week0_start_wed(self):
        """Friday of week 0 when start_dow=2: d = 0*7 - 2 + 4 = 2."""
        assert _week_day(0, 4, 2) == 2

    def test_monday_week0_start_wed_invalid(self):
        """Monday of week 0 when start_dow=2: d = 0*7 - 2 + 0 = -2 (before start)."""
        assert _week_day(0, 0, 2) == -2

    def test_wednesday_week0_start_wed(self):
        """Wednesday of week 0 when start_dow=2: d = 0*7 - 2 + 2 = 0."""
        assert _week_day(0, 2, 2) == 0

    def test_monday_week1_start_wed(self):
        """Monday of week 1 when start_dow=2: d = 1*7 - 2 + 0 = 5."""
        assert _week_day(1, 0, 2) == 5

    def test_start_dow_zero_classic(self):
        """When start_dow=0, _week_day(w, dow, 0) = w*7 + dow (old formula)."""
        for w in range(5):
            for dow in range(7):
                assert _week_day(w, dow, 0) == w * 7 + dow

    def test_roundtrip_day_to_week_and_back(self):
        """For any valid d, _week_day(_day_to_week(d, s), _day_of_week(d, s), s) == d."""
        for start_dow in range(7):
            for d in range(100):
                w = _day_to_week(d, start_dow)
                dow = _day_of_week(d, start_dow)
                assert _week_day(w, dow, start_dow) == d


class TestLeapYearComputation:
    """Test that academic year day counts are correct for leap years."""

    def test_2026_2027_non_leap(self):
        """July 1 2026 to June 30 2027 → 365 days (2027 is not a leap year)."""
        start = date(2026, 7, 1)
        end = date(2027, 6, 30)
        num_days = (end - start).days + 1
        assert num_days == 365

    def test_2027_2028_leap(self):
        """July 1 2027 to June 30 2028 → 366 days (2028 is a leap year)."""
        start = date(2027, 7, 1)
        end = date(2028, 6, 30)
        num_days = (end - start).days + 1
        assert num_days == 366

    def test_2024_2025_leap(self):
        """July 1 2024 to June 30 2025 → 365 days (leap day in Feb 2025? No, 2024 leap)."""
        # 2024 is a leap year, but Feb 29 is in 2025? No, Feb 29 2024 is before July.
        # Actually July 1 2024 to June 30 2025 does NOT cross Feb 29 of a leap year
        # since Feb 2025 is not leap. So 365 days.
        start = date(2024, 7, 1)
        end = date(2025, 6, 30)
        num_days = (end - start).days + 1
        assert num_days == 365

    def test_2023_2024_crosses_leap_day(self):
        """July 1 2023 to June 30 2024 → 366 days (crosses Feb 29 2024)."""
        start = date(2023, 7, 1)
        end = date(2024, 6, 30)
        num_days = (end - start).days + 1
        assert num_days == 366

    def test_start_dow_july_2026(self):
        """July 1, 2026 is a Wednesday → weekday() == 2."""
        assert date(2026, 7, 1).weekday() == 2

    def test_start_dow_july_2027(self):
        """July 1, 2027 is a Thursday → weekday() == 3."""
        assert date(2027, 7, 1).weekday() == 3


class TestSolverBridgeCalendar:
    """Test that solver_bridge computes calendar params correctly."""

    def test_build_config_computes_start_dow(self):
        """build_solver_config_from_request sets start_dow and num_days."""
        from scheduler.solver_bridge import build_solver_config_from_request

        config = build_solver_config_from_request({
            "fellow_groups": {"PGY5": ["Alice", "Bob"]},
            "shifts": ["NCC1", "NCC2"],
            "horizon_start": "2026-07-01",
        })
        assert config.start_dow == 2  # Wednesday
        assert config.num_days == 365
        assert config.num_weeks == 53  # (2 + 364) // 7 + 1 = 53

    def test_build_config_leap_year(self):
        """Leap year academic year gives 366 days."""
        from scheduler.solver_bridge import build_solver_config_from_request

        config = build_solver_config_from_request({
            "fellow_groups": {"PGY5": ["Alice", "Bob"]},
            "shifts": ["NCC1", "NCC2"],
            "horizon_start": "2027-07-01",
        })
        assert config.start_dow == 3  # Thursday
        assert config.num_days == 366
        # (3 + 365) // 7 + 1 = 52 + 1 = 53
        assert config.num_weeks == 53
