"""Tests for annual call rules (pin/block night/weekend assignments).

Verifies:
1. specific_night_assignment pins the correct night variable
2. blocked_night blocks the correct night variable
3. specific_weekend_assignment pins the correct weekend role
4. blocked_weekend blocks all three roles for the fellow
5. friday_call_assignment pins the correct Friday night
6. Date-to-day conversion works correctly
7. Invalid fellow names are silently skipped
8. active: false rules are skipped
"""

from __future__ import annotations

from datetime import date

import pytest

from parafrost_scheduler.opb_encoder import OpbBuilder
from parafrost_scheduler.schedule_types import (
    ScheduleSolverConfig,
    _ROLE_NCC1,
    _ROLE_NCC2,
    _ROLE_STROKE,
    _date_to_day_index,
    _num_weeks_for,
    _week_day)
from parafrost_scheduler.schedule_encoder import _encode_call_rules
from scheduler.night_call_types import NightSolverConfig
from scheduler.weekend_call_types import WeekendSolverConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(
    start_dow: int = 0,
    num_days: int = 21,
    fellow_groups: dict[str, list[str]] | None = None,
    call_rules: list[dict] | None = None,
    horizon_start: date | None = None) -> ScheduleSolverConfig:
    """Build a minimal ScheduleSolverConfig for testing call rules."""
    if fellow_groups is None:
        fellow_groups = {"NCC_SR": ["Alice", "Bob", "Carol"]}
    if horizon_start is None:
        horizon_start = date(2026, 7, 6)  # Monday
    night_config = NightSolverConfig(
        total_nights={},
        friday_nights={},
        total_night_multisets=(),
        friday_night_multisets=(),
        ccm_fellows=frozenset(),
        holiday_dates=(),
        horizon_start_date=horizon_start)
    weekend_config = WeekendSolverConfig(
        ncc_totals={},
        stroke_totals={},
        stroke_cohort=(),
        stroke_cohort_total=None,
        ccm_fellows=frozenset(),
        always_stroke_eligible=frozenset({"Alice", "Bob", "Carol"}),
        telestroke_stroke_eligible=frozenset(),
        stroke_only_eligible=frozenset())
    return ScheduleSolverConfig(
        fellow_groups=fellow_groups,
        shifts=["NCC1"],
        constraints=[],
        night_config=night_config,
        weekend_config=weekend_config,
        night_hard_criteria=frozenset(),
        start_dow=start_dow,
        num_days=num_days,
        call_rules=call_rules or [])


def _make_xn(opb: OpbBuilder, num_days: int, num_fellows: int) -> list[list[int]]:
    """Create xn variables: xn[d][f] = variable index."""
    xn = []
    for d in range(num_days):
        row = []
        for f in range(num_fellows):
            row.append(opb.new_var())
        xn.append(row)
    return xn


def _make_wr(opb: OpbBuilder, num_weeks: int, num_fellows: int) -> list[list[dict[int, int]]]:
    """Create wr variables: wr[w][role_idx] = {fellow_idx: var}."""
    wr = []
    for w in range(num_weeks):
        week_roles = []
        for role_idx in range(3):
            role_vars = {}
            for f in range(num_fellows):
                role_vars[f] = opb.new_var()
            week_roles.append(role_vars)
        wr.append(week_roles)
    return wr


def _get_units(opb: OpbBuilder) -> list[int]:
    """Extract unit literals added to the builder.

    Unit constraints are stored as "+1 x5 >= 1 ;" (positive) or
    "+1 ~x5 >= 1 ;" (negative) in the _constraints list.
    """
    units = []
    for line in opb._constraints:
        line = line.strip()
        # Unit constraint format: "+1 x5 >= 1 ;" or "+1 ~x5 >= 1 ;"
        parts = line.split()
        if len(parts) == 5 and parts[0] == "+1" and parts[2] == ">=" and parts[3] == "1" and parts[4] == ";":
            var_str = parts[1]
            if var_str.startswith("~x"):
                var_idx = int(var_str[2:])
                units.append(-var_idx)
            elif var_str.startswith("x"):
                var_idx = int(var_str[1:])
                units.append(var_idx)
    return units


# ---------------------------------------------------------------------------
# Tests: _date_to_day_index
# ---------------------------------------------------------------------------

class TestDateToDayIndex:
    def test_same_date_returns_zero(self):
        assert _date_to_day_index("2026-07-06", date(2026, 7, 6)) == 0

    def test_one_day_after(self):
        assert _date_to_day_index("2026-07-07", date(2026, 7, 6)) == 1

    def test_ten_days_after(self):
        assert _date_to_day_index("2026-07-16", date(2026, 7, 6)) == 10

    def test_date_object_input(self):
        assert _date_to_day_index(date(2026, 7, 10), date(2026, 7, 6)) == 4

    def test_date_before_horizon_returns_negative(self):
        assert _date_to_day_index("2026-07-04", date(2026, 7, 6)) == -2


# ---------------------------------------------------------------------------
# Tests: specific_night_assignment
# ---------------------------------------------------------------------------

class TestSpecificNightAssignment:
    def test_pins_correct_night_variable(self):
        """A specific_night_assignment rule adds a positive unit for xn[d][fi]."""
        horizon = date(2026, 7, 6)  # Monday
        config = _make_config(
            start_dow=0,
            num_days=21,
            horizon_start=horizon,
            call_rules=[{
                "type": "specific_night_assignment",
                "name": "Alice covers July 10th",
                "fellow": "Alice",
                "dates": ["2026-07-10"],
                "active": True,
            }])
        opb = OpbBuilder()
        fellow_names = ["Alice", "Bob", "Carol"]
        xn = _make_xn(opb, 21, 3)
        num_weeks = _num_weeks_for(0, 21)
        wr = _make_wr(opb, num_weeks, 3)

        _encode_call_rules(opb, xn, wr, config, fellow_names)

        # Day 4 = July 10 (4 days after July 6), fellow 0 = Alice
        units = _get_units(opb)
        expected_var = xn[4][0]
        assert expected_var in units

    def test_multiple_dates(self):
        """Multiple dates in a single rule create multiple unit constraints."""
        horizon = date(2026, 7, 6)
        config = _make_config(
            start_dow=0,
            num_days=21,
            horizon_start=horizon,
            call_rules=[{
                "type": "specific_night_assignment",
                "fellow": "Bob",
                "dates": ["2026-07-07", "2026-07-08"],
                "active": True,
            }])
        opb = OpbBuilder()
        fellow_names = ["Alice", "Bob", "Carol"]
        xn = _make_xn(opb, 21, 3)
        num_weeks = _num_weeks_for(0, 21)
        wr = _make_wr(opb, num_weeks, 3)

        _encode_call_rules(opb, xn, wr, config, fellow_names)

        units = _get_units(opb)
        assert xn[1][1] in units  # July 7, Bob
        assert xn[2][1] in units  # July 8, Bob


# ---------------------------------------------------------------------------
# Tests: blocked_night
# ---------------------------------------------------------------------------

class TestBlockedNight:
    def test_blocks_correct_night_variable(self):
        """A blocked_night rule adds a negative unit for xn[d][fi]."""
        horizon = date(2026, 7, 6)
        config = _make_config(
            start_dow=0,
            num_days=21,
            horizon_start=horizon,
            call_rules=[{
                "type": "blocked_night",
                "fellow": "Bob",
                "dates": ["2026-07-12"],
                "active": True,
            }])
        opb = OpbBuilder()
        fellow_names = ["Alice", "Bob", "Carol"]
        xn = _make_xn(opb, 21, 3)
        num_weeks = _num_weeks_for(0, 21)
        wr = _make_wr(opb, num_weeks, 3)

        _encode_call_rules(opb, xn, wr, config, fellow_names)

        units = _get_units(opb)
        expected_var = -xn[6][1]  # Day 6 = July 12, Bob
        assert expected_var in units


# ---------------------------------------------------------------------------
# Tests: specific_weekend_assignment
# ---------------------------------------------------------------------------

class TestSpecificWeekendAssignment:
    def test_pins_correct_weekend_role(self):
        """A specific_weekend_assignment pins the correct wr variable."""
        config = _make_config(
            start_dow=0,
            num_days=21,
            call_rules=[{
                "type": "specific_weekend_assignment",
                "fellow": "Alice",
                "role": "NCC1",
                "weeks": [1],
                "active": True,
            }])
        opb = OpbBuilder()
        fellow_names = ["Alice", "Bob", "Carol"]
        xn = _make_xn(opb, 21, 3)
        num_weeks = _num_weeks_for(0, 21)
        wr = _make_wr(opb, num_weeks, 3)

        _encode_call_rules(opb, xn, wr, config, fellow_names)

        units = _get_units(opb)
        expected_var = wr[1][_ROLE_NCC1][0]  # Week 1, NCC1, Alice (idx 0)
        assert expected_var in units

    def test_stroke_role(self):
        """Stroke role maps to role_idx=2."""
        config = _make_config(
            start_dow=0,
            num_days=21,
            call_rules=[{
                "type": "specific_weekend_assignment",
                "fellow": "Carol",
                "role": "Stroke",
                "weeks": [0],
                "active": True,
            }])
        opb = OpbBuilder()
        fellow_names = ["Alice", "Bob", "Carol"]
        xn = _make_xn(opb, 21, 3)
        num_weeks = _num_weeks_for(0, 21)
        wr = _make_wr(opb, num_weeks, 3)

        _encode_call_rules(opb, xn, wr, config, fellow_names)

        units = _get_units(opb)
        expected_var = wr[0][_ROLE_STROKE][2]  # Week 0, Stroke, Carol (idx 2)
        assert expected_var in units

    def test_invalid_role_skipped(self):
        """An invalid role name is silently skipped."""
        config = _make_config(
            start_dow=0,
            num_days=21,
            call_rules=[{
                "type": "specific_weekend_assignment",
                "fellow": "Alice",
                "role": "InvalidRole",
                "weeks": [0],
                "active": True,
            }])
        opb = OpbBuilder()
        fellow_names = ["Alice", "Bob", "Carol"]
        xn = _make_xn(opb, 21, 3)
        num_weeks = _num_weeks_for(0, 21)
        wr = _make_wr(opb, num_weeks, 3)

        _encode_call_rules(opb, xn, wr, config, fellow_names)

        units = _get_units(opb)
        assert units == []


# ---------------------------------------------------------------------------
# Tests: blocked_weekend
# ---------------------------------------------------------------------------

class TestBlockedWeekend:
    def test_blocks_all_three_roles(self):
        """A blocked_weekend rule blocks all three weekend roles for the fellow."""
        config = _make_config(
            start_dow=0,
            num_days=21,
            call_rules=[{
                "type": "blocked_weekend",
                "fellow": "Bob",
                "weeks": [2],
                "active": True,
            }])
        opb = OpbBuilder()
        fellow_names = ["Alice", "Bob", "Carol"]
        xn = _make_xn(opb, 21, 3)
        num_weeks = _num_weeks_for(0, 21)
        wr = _make_wr(opb, num_weeks, 3)

        _encode_call_rules(opb, xn, wr, config, fellow_names)

        units = _get_units(opb)
        # Bob (idx 1) blocked from all 3 roles in week 2
        assert -wr[2][_ROLE_NCC1][1] in units
        assert -wr[2][_ROLE_NCC2][1] in units
        assert -wr[2][_ROLE_STROKE][1] in units


# ---------------------------------------------------------------------------
# Tests: friday_call_assignment
# ---------------------------------------------------------------------------

class TestFridayCallAssignment:
    def test_pins_correct_friday_night(self):
        """A friday_call_assignment pins the Friday night of the given week."""
        # start_dow=0 means day 0 is Monday. Friday of week 0 is day 4.
        config = _make_config(
            start_dow=0,
            num_days=21,
            call_rules=[{
                "type": "friday_call_assignment",
                "fellow": "Alice",
                "weeks": [0],
                "active": True,
            }])
        opb = OpbBuilder()
        fellow_names = ["Alice", "Bob", "Carol"]
        xn = _make_xn(opb, 21, 3)
        num_weeks = _num_weeks_for(0, 21)
        wr = _make_wr(opb, num_weeks, 3)

        _encode_call_rules(opb, xn, wr, config, fellow_names)

        units = _get_units(opb)
        friday_d = _week_day(0, 4, 0)  # Day 4
        expected_var = xn[friday_d][0]  # Alice (idx 0)
        assert expected_var in units

    def test_friday_with_nonzero_start_dow(self):
        """Friday is computed correctly when start_dow is non-zero."""
        # start_dow=2 means day 0 is Wednesday. Friday of week 0 = day 2.
        config = _make_config(
            start_dow=2,
            num_days=21,
            call_rules=[{
                "type": "friday_call_assignment",
                "fellow": "Carol",
                "weeks": [1],
                "active": True,
            }])
        opb = OpbBuilder()
        fellow_names = ["Alice", "Bob", "Carol"]
        xn = _make_xn(opb, 21, 3)
        num_weeks = _num_weeks_for(2, 21)
        wr = _make_wr(opb, num_weeks, 3)

        _encode_call_rules(opb, xn, wr, config, fellow_names)

        units = _get_units(opb)
        friday_d = _week_day(1, 4, 2)  # week 1, Friday, start_dow=2 -> day 9
        expected_var = xn[friday_d][2]  # Carol (idx 2)
        assert expected_var in units


# ---------------------------------------------------------------------------
# Tests: inactive rules and invalid fellows
# ---------------------------------------------------------------------------

class TestInactiveAndInvalidRules:
    def test_inactive_rule_skipped(self):
        """Rules with active: false produce no constraints."""
        config = _make_config(
            start_dow=0,
            num_days=21,
            horizon_start=date(2026, 7, 6),
            call_rules=[{
                "type": "specific_night_assignment",
                "fellow": "Alice",
                "dates": ["2026-07-10"],
                "active": False,
            }])
        opb = OpbBuilder()
        fellow_names = ["Alice", "Bob", "Carol"]
        xn = _make_xn(opb, 21, 3)
        num_weeks = _num_weeks_for(0, 21)
        wr = _make_wr(opb, num_weeks, 3)

        _encode_call_rules(opb, xn, wr, config, fellow_names)

        units = _get_units(opb)
        assert units == []

    def test_invalid_fellow_skipped(self):
        """Rules referencing an unknown fellow produce no constraints."""
        config = _make_config(
            start_dow=0,
            num_days=21,
            horizon_start=date(2026, 7, 6),
            call_rules=[{
                "type": "specific_night_assignment",
                "fellow": "NonexistentPerson",
                "dates": ["2026-07-10"],
                "active": True,
            }])
        opb = OpbBuilder()
        fellow_names = ["Alice", "Bob", "Carol"]
        xn = _make_xn(opb, 21, 3)
        num_weeks = _num_weeks_for(0, 21)
        wr = _make_wr(opb, num_weeks, 3)

        _encode_call_rules(opb, xn, wr, config, fellow_names)

        units = _get_units(opb)
        assert units == []

    def test_out_of_bounds_date_skipped(self):
        """Dates outside the horizon are silently skipped."""
        horizon = date(2026, 7, 6)
        config = _make_config(
            start_dow=0,
            num_days=7,  # Only 7 days
            horizon_start=horizon,
            call_rules=[{
                "type": "specific_night_assignment",
                "fellow": "Alice",
                "dates": ["2026-08-01"],  # Way past 7 days
                "active": True,
            }])
        opb = OpbBuilder()
        fellow_names = ["Alice", "Bob", "Carol"]
        xn = _make_xn(opb, 7, 3)
        num_weeks = _num_weeks_for(0, 7)
        wr = _make_wr(opb, num_weeks, 3)

        _encode_call_rules(opb, xn, wr, config, fellow_names)

        units = _get_units(opb)
        assert units == []

    def test_out_of_bounds_week_skipped(self):
        """Weeks outside num_weeks are silently skipped."""
        config = _make_config(
            start_dow=0,
            num_days=7,  # 1-2 weeks only
            call_rules=[{
                "type": "specific_weekend_assignment",
                "fellow": "Alice",
                "role": "NCC1",
                "weeks": [99],
                "active": True,
            }])
        opb = OpbBuilder()
        fellow_names = ["Alice", "Bob", "Carol"]
        xn = _make_xn(opb, 7, 3)
        num_weeks = _num_weeks_for(0, 7)
        wr = _make_wr(opb, num_weeks, 3)

        _encode_call_rules(opb, xn, wr, config, fellow_names)

        units = _get_units(opb)
        assert units == []

    def test_default_active_is_true(self):
        """Rules without an explicit 'active' field default to active."""
        horizon = date(2026, 7, 6)
        config = _make_config(
            start_dow=0,
            num_days=21,
            horizon_start=horizon,
            call_rules=[{
                "type": "blocked_night",
                "fellow": "Alice",
                "dates": ["2026-07-08"],
                # No "active" key
            }])
        opb = OpbBuilder()
        fellow_names = ["Alice", "Bob", "Carol"]
        xn = _make_xn(opb, 21, 3)
        num_weeks = _num_weeks_for(0, 21)
        wr = _make_wr(opb, num_weeks, 3)

        _encode_call_rules(opb, xn, wr, config, fellow_names)

        units = _get_units(opb)
        # Day 2 = July 8, Alice (idx 0)
        assert -xn[2][0] in units
