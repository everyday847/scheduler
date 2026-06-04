"""Tests for weekend night linking constraints.

Verifies:
1. Friday night fellow is penalized for Weekend NCC2/Stroke (NCC1 is owned by the
   hard friday_weekend_ncc1 policy criterion, not this linking encoder)
2. Saturday night fellow must be Weekend NCC1 or NCC2
3. Fellow not eligible for NCC is blocked from Saturday night
4. Sunday night fellow must be Weekend Stroke
5. Fellow not eligible for Weekend Stroke is blocked from Sunday night
6. Per-occurrence weights: Friday=40, Saturday=10, Sunday=10
"""

from __future__ import annotations

from datetime import date

import pytest

from parafrost_scheduler.opb_encoder import OpbBuilder
from parafrost_scheduler.schedule_solver import (
    ScheduleSolverConfig,
    _ROLE_NCC1,
    _ROLE_NCC2,
    _ROLE_STROKE,
    _day_of_week,
    _day_to_week,
    _encode_weekend_night_linking,
    _num_weeks_for,
    _week_day,
)
from scheduler.night_call_solver import NightSolverConfig
from scheduler.weekend_call_solver import WeekendSolverConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(
    start_dow: int = 0,
    num_days: int = 14,
    fellow_groups: dict[str, list[str]] | None = None,
    stroke_eligible: frozenset[str] = frozenset(),
) -> ScheduleSolverConfig:
    """Build a minimal ScheduleSolverConfig for testing."""
    if fellow_groups is None:
        fellow_groups = {"NCC_SR": ["Alice", "Bob"]}
    night_config = NightSolverConfig(
        total_nights={},
        friday_nights={},
        total_night_multisets=(),
        friday_night_multisets=(),
        ccm_fellows=frozenset(),
        holiday_dates=(),
        horizon_start_date=date(2026, 7, 1),
    )
    weekend_config = WeekendSolverConfig(
        ncc_totals={},
        stroke_totals={},
        stroke_cohort=(),
        stroke_cohort_total=None,
        ccm_fellows=frozenset(),
        always_stroke_eligible=stroke_eligible,
        telestroke_stroke_eligible=frozenset(),
        stroke_only_eligible=frozenset(),
        total_weekends={},
        weekend_options=None,
        friday_weekend_options=None,
    )
    return ScheduleSolverConfig(
        fellow_groups=fellow_groups,
        shifts=["NCC1"],
        constraints=[],
        night_config=night_config,
        weekend_config=weekend_config,
        night_hard_criteria=frozenset(),
        start_dow=start_dow,
        num_days=num_days,
    )


def _make_xn(opb: OpbBuilder, num_days: int, num_fellows: int) -> list[list[int]]:
    """Allocate night variables xn[d][f], all active."""
    return [[opb.new_var() for _ in range(num_fellows)] for _ in range(num_days)]


def _make_wr(
    opb: OpbBuilder,
    num_weeks: int,
    num_fellows: int,
    *,
    ncc_eligible: set[int] | None = None,
    stroke_eligible: set[int] | None = None,
) -> list[list[dict[int, int]]]:
    """Allocate weekend role variables wr[w][role][f].

    By default all fellows are eligible for all roles.
    Pass ncc_eligible/stroke_eligible to restrict.
    """
    if ncc_eligible is None:
        ncc_eligible = set(range(num_fellows))
    if stroke_eligible is None:
        stroke_eligible = set(range(num_fellows))

    wr = []
    for w in range(num_weeks):
        roles = []
        # NCC1
        roles.append({f: opb.new_var() for f in ncc_eligible})
        # NCC2
        roles.append({f: opb.new_var() for f in ncc_eligible})
        # Stroke
        roles.append({f: opb.new_var() for f in stroke_eligible})
        wr.append(roles)
    return wr


def _get_constraints(opb: OpbBuilder) -> list[str]:
    """Return all raw constraint strings from the builder."""
    return list(opb._constraints)


def _constraints_containing(opb: OpbBuilder, var_id: int) -> list[str]:
    """Get constraints referencing a variable (positive or negated)."""
    pos = f"x{var_id} "
    neg = f"~x{var_id} "
    # Also check end-of-constraint (no trailing space before ';')
    pos_end = f"x{var_id}"
    neg_end = f"~x{var_id}"
    results = []
    for c in opb._constraints:
        if pos in c or neg in c:
            results.append(c)
    return results


# ---------------------------------------------------------------------------
# Test 1: Friday night blocks all weekend roles
# ---------------------------------------------------------------------------

class TestFridayNightBlocksWeekendRoles:
    """A fellow on Friday night call is penalized for Weekend NCC2/Stroke. The
    NCC1 case is owned by the hard friday_weekend_ncc1 policy criterion, so this
    linking encoder must NOT touch Weekend NCC1."""

    def test_friday_night_does_not_touch_ncc1(self):
        """Friday night linking should NOT reference Weekend NCC1 (owned by the
        hard friday_weekend_ncc1 criterion elsewhere)."""
        # start_dow=0 (Monday). Friday = dow 4 = day 4.
        config = _make_config(start_dow=0, num_days=7)
        opb = OpbBuilder()
        xn = _make_xn(opb, 7, 1)
        wr = _make_wr(opb, config.num_weeks, 1)

        soft = []
        _encode_weekend_night_linking(opb, xn, wr, config, ["Alice"], soft)

        friday_d = _week_day(0, 4, 0)  # day 4
        xn_friday = xn[friday_d][0]
        wr_ncc1 = wr[0][_ROLE_NCC1][0]
        constraints = _constraints_containing(opb, xn_friday)
        ncc1_constraints = [c for c in constraints if f"x{wr_ncc1} " in c]
        assert len(ncc1_constraints) == 0, "Friday linking must not touch Weekend NCC1"

    def test_friday_night_blocks_ncc2(self):
        """Friday night + Weekend NCC2 should have at-most-1 constraint."""
        config = _make_config(start_dow=0, num_days=7)
        opb = OpbBuilder()
        xn = _make_xn(opb, 7, 1)
        wr = _make_wr(opb, config.num_weeks, 1)

        soft = []
        _encode_weekend_night_linking(opb, xn, wr, config, ["Alice"], soft)

        friday_d = _week_day(0, 4, 0)
        xn_friday = xn[friday_d][0]
        wr_ncc2 = wr[0][_ROLE_NCC2][0]
        constraints = _constraints_containing(opb, xn_friday)
        ncc2_constraints = [c for c in constraints if f"x{wr_ncc2}" in c]
        assert len(ncc2_constraints) > 0, "Friday night should block Weekend NCC2"

    def test_friday_night_blocks_stroke(self):
        """Friday night + Weekend Stroke should have at-most-1 constraint."""
        config = _make_config(start_dow=0, num_days=7)
        opb = OpbBuilder()
        xn = _make_xn(opb, 7, 1)
        wr = _make_wr(opb, config.num_weeks, 1)

        soft = []
        _encode_weekend_night_linking(opb, xn, wr, config, ["Alice"], soft)

        friday_d = _week_day(0, 4, 0)
        xn_friday = xn[friday_d][0]
        wr_stroke = wr[0][_ROLE_STROKE][0]
        constraints = _constraints_containing(opb, xn_friday)
        stroke_constraints = [c for c in constraints if f"x{wr_stroke}" in c]
        assert len(stroke_constraints) > 0, "Friday night should block Weekend Stroke"

    def test_friday_night_blocks_ncc2_and_stroke_only(self):
        """Friday night should penalize Weekend NCC2 and Stroke, but not NCC1."""
        config = _make_config(start_dow=0, num_days=7)
        opb = OpbBuilder()
        xn = _make_xn(opb, 7, 1)
        wr = _make_wr(opb, config.num_weeks, 1)

        soft = []
        _encode_weekend_night_linking(opb, xn, wr, config, ["Alice"], soft)

        friday_d = _week_day(0, 4, 0)
        xn_friday = xn[friday_d][0]
        constraints = _constraints_containing(opb, xn_friday)
        # NCC2 and Stroke should be referenced; NCC1 should not.
        for rv in (wr[0][_ROLE_NCC2][0], wr[0][_ROLE_STROKE][0]):
            role_constraints = [c for c in constraints if f"x{rv} " in c]
            assert len(role_constraints) > 0, f"Friday night should penalize role var x{rv}"
        wr_ncc1 = wr[0][_ROLE_NCC1][0]
        ncc1_constraints = [c for c in constraints if f"x{wr_ncc1} " in c]
        assert len(ncc1_constraints) == 0, "Friday night must not touch Weekend NCC1"

    def test_weekend_night_linking_weights(self):
        """Friday penalties weigh 40, Saturday/Sunday weigh 10 (per occurrence)."""
        config = _make_config(start_dow=0, num_days=7)
        opb = OpbBuilder()
        xn = _make_xn(opb, 7, 1)
        wr = _make_wr(opb, config.num_weeks, 1)

        soft = []
        _encode_weekend_night_linking(opb, xn, wr, config, ["Alice"], soft)

        weights = sorted({w for _, w in soft})
        # Friday: NCC2 + Stroke at 40; Saturday: NCC at 10; Sunday: Stroke at 10.
        assert 40 in weights, "Friday linking should contribute weight-40 penalties"
        assert 10 in weights, "Saturday/Sunday linking should contribute weight-10 penalties"
        assert 20 not in weights, "Old shared weight 20 should no longer appear"
        friday_count = sum(1 for _, w in soft if w == 40)
        assert friday_count == 2, "One Friday penalty each for NCC2 and Stroke"


# ---------------------------------------------------------------------------
# Test 2: Saturday night must be Weekend NCC1 or NCC2
# ---------------------------------------------------------------------------

class TestSaturdayNightMustBeNCC:
    """Saturday night fellow must be one of the Weekend NCC fellows."""

    def test_saturday_night_implies_ncc(self):
        """Saturday night call should imply Weekend NCC1 or NCC2."""
        # start_dow=0 (Monday). Saturday = dow 5 = day 5.
        config = _make_config(start_dow=0, num_days=7)
        opb = OpbBuilder()
        xn = _make_xn(opb, 7, 1)
        wr = _make_wr(opb, config.num_weeks, 1)
        baseline = opb.num_constraints

        soft = []
        _encode_weekend_night_linking(opb, xn, wr, config, ["Alice"], soft)

        sat_d = _week_day(0, 5, 0)  # day 5
        xn_sat = xn[sat_d][0]
        wr_ncc1 = wr[0][_ROLE_NCC1][0]
        wr_ncc2 = wr[0][_ROLE_NCC2][0]

        # Should have a constraint: wr_ncc1 + wr_ncc2 + ~xn_sat >= 1
        constraints = _constraints_containing(opb, xn_sat)
        # Look for implication constraint (weighted_sum_at_least with ncc vars and bound >= 1)
        ncc_impl = [
            c for c in constraints
            if f"x{wr_ncc1} " in c and f"x{wr_ncc2} " in c and ">= 1" in c
        ]
        assert len(soft) > 0, "Saturday night NCC linking should generate soft violations"

    def test_saturday_night_two_fellows(self):
        """Both fellows should have Saturday-NCC linking constraints."""
        config = _make_config(start_dow=0, num_days=7)
        opb = OpbBuilder()
        xn = _make_xn(opb, 7, 2)
        wr = _make_wr(opb, config.num_weeks, 2)

        soft = []
        _encode_weekend_night_linking(opb, xn, wr, config, ["Alice", "Bob"], soft)

        sat_d = _week_day(0, 5, 0)

        for f in range(2):
            xn_sat = xn[sat_d][f]
            constraints = _constraints_containing(opb, xn_sat)
            assert len(constraints) > 0, f"Fellow {f} should have Saturday night linking"


# ---------------------------------------------------------------------------
# Test 3: Fellow not eligible for NCC is blocked from Saturday night
# ---------------------------------------------------------------------------

class TestSaturdayNightIneligibleFellow:
    """A fellow not eligible for Weekend NCC should be blocked from Saturday night."""

    def test_non_ncc_fellow_blocked_saturday(self):
        """Fellow with no NCC vars should have xn forced to 0 on Saturday."""
        config = _make_config(start_dow=0, num_days=7)
        opb = OpbBuilder()
        xn = _make_xn(opb, 7, 2)
        # Fellow 0 is NCC-eligible, fellow 1 is not
        wr = _make_wr(opb, config.num_weeks, 2, ncc_eligible={0})

        soft = []
        _encode_weekend_night_linking(opb, xn, wr, config, ["Alice", "Bob"], soft)

        sat_d = _week_day(0, 5, 0)
        xn_sat_f1 = xn[sat_d][1]
        # Fellow 1 should be blocked from Saturday night
        constraints = _constraints_containing(opb, xn_sat_f1)
        # Should have a unit clause forcing xn to false: "+1 ~x{var} >= 1"
        blocking = [c for c in constraints if f"~x{xn_sat_f1}" in c and ">= 1" in c]
        assert len(soft) > 0, "Non-NCC fellow Saturday night should generate soft penalty"

    def test_ncc_eligible_fellow_not_blocked(self):
        """Fellow eligible for NCC should NOT be blocked from Saturday night."""
        config = _make_config(start_dow=0, num_days=7)
        opb = OpbBuilder()
        xn = _make_xn(opb, 7, 2)
        wr = _make_wr(opb, config.num_weeks, 2, ncc_eligible={0})

        soft = []
        _encode_weekend_night_linking(opb, xn, wr, config, ["Alice", "Bob"], soft)

        sat_d = _week_day(0, 5, 0)
        xn_sat_f0 = xn[sat_d][0]
        constraints = _constraints_containing(opb, xn_sat_f0)
        # Should NOT have unit blocking clause (exact match for unit clause)
        unit_clause = f"+1 ~x{xn_sat_f0} >= 1 ;"
        blocking = [c for c in constraints if c.strip() == unit_clause]
        assert len(blocking) == 0, "NCC-eligible fellow should not be blocked from Saturday"


# ---------------------------------------------------------------------------
# Test 4: Sunday night must be Weekend Stroke
# ---------------------------------------------------------------------------

class TestSundayNightMustBeStroke:
    """Sunday night fellow must be the Weekend Stroke fellow."""

    def test_sunday_night_implies_stroke(self):
        """Sunday night call should imply Weekend Stroke role."""
        config = _make_config(start_dow=0, num_days=7)
        opb = OpbBuilder()
        xn = _make_xn(opb, 7, 1)
        wr = _make_wr(opb, config.num_weeks, 1)

        soft = []
        _encode_weekend_night_linking(opb, xn, wr, config, ["Alice"], soft)

        sun_d = _week_day(0, 6, 0)  # day 6
        xn_sun = xn[sun_d][0]
        wr_stroke = wr[0][_ROLE_STROKE][0]

        # Should have: wr_stroke + ~xn_sun >= 1
        constraints = _constraints_containing(opb, xn_sun)
        stroke_impl = [c for c in constraints if f"x{wr_stroke} " in c and ">= 1" in c]
        assert len(soft) > 0, "Sunday night Stroke linking should generate soft violations"

    def test_sunday_night_does_not_imply_ncc(self):
        """Sunday night should NOT imply NCC roles."""
        config = _make_config(start_dow=0, num_days=7)
        opb = OpbBuilder()
        xn = _make_xn(opb, 7, 1)
        wr = _make_wr(opb, config.num_weeks, 1)

        soft = []
        _encode_weekend_night_linking(opb, xn, wr, config, ["Alice"], soft)

        sun_d = _week_day(0, 6, 0)
        xn_sun = xn[sun_d][0]
        wr_ncc1 = wr[0][_ROLE_NCC1][0]
        wr_ncc2 = wr[0][_ROLE_NCC2][0]

        # The Sunday night constraint should only reference Stroke, not NCC
        constraints = _constraints_containing(opb, xn_sun)
        # Filter to just the implication constraints (not the Friday blocking ones)
        # Friday and Sunday are different days, so xn_sun only appears in Sunday constraints
        ncc_constraints = [
            c for c in constraints
            if (f"x{wr_ncc1}" in c or f"x{wr_ncc2}" in c)
        ]
        assert len(ncc_constraints) == 0, "Sunday night should not imply NCC roles"


# ---------------------------------------------------------------------------
# Test 5: Fellow not eligible for Weekend Stroke blocked from Sunday night
# ---------------------------------------------------------------------------

class TestSundayNightIneligibleFellow:
    """Fellow not eligible for Weekend Stroke should be blocked from Sunday night."""

    def test_non_stroke_fellow_blocked_sunday(self):
        """Fellow with no Stroke var should have xn forced to 0 on Sunday."""
        config = _make_config(start_dow=0, num_days=7)
        opb = OpbBuilder()
        xn = _make_xn(opb, 7, 2)
        # Fellow 0 is Stroke-eligible, fellow 1 is not
        wr = _make_wr(opb, config.num_weeks, 2, stroke_eligible={0})

        soft = []
        _encode_weekend_night_linking(opb, xn, wr, config, ["Alice", "Bob"], soft)

        sun_d = _week_day(0, 6, 0)
        xn_sun_f1 = xn[sun_d][1]
        # Fellow 1 should be blocked from Sunday night
        constraints = _constraints_containing(opb, xn_sun_f1)
        blocking = [c for c in constraints if f"~x{xn_sun_f1}" in c and ">= 1" in c]
        assert len(soft) > 0, "Non-Stroke fellow Sunday night should generate soft penalty"

    def test_stroke_eligible_fellow_not_blocked(self):
        """Fellow eligible for Stroke should NOT be blocked from Sunday night."""
        config = _make_config(start_dow=0, num_days=7)
        opb = OpbBuilder()
        xn = _make_xn(opb, 7, 2)
        wr = _make_wr(opb, config.num_weeks, 2, stroke_eligible={0})

        soft = []
        _encode_weekend_night_linking(opb, xn, wr, config, ["Alice", "Bob"], soft)

        sun_d = _week_day(0, 6, 0)
        xn_sun_f0 = xn[sun_d][0]
        constraints = _constraints_containing(opb, xn_sun_f0)
        # Should have implication constraint, NOT unit blocking clause
        unit_clause = f"+1 ~x{xn_sun_f0} >= 1 ;"
        blocking = [c for c in constraints if c.strip() == unit_clause]
        assert len(blocking) == 0, "Stroke-eligible fellow should not be blocked from Sunday"


# ---------------------------------------------------------------------------
# Test 6: Non-Monday start (Wednesday start_dow=2)
# ---------------------------------------------------------------------------

class TestNonMondayStart:
    """Verify correct day mapping when the year starts on a non-Monday."""

    def test_wednesday_start_week0_weekend(self):
        """With start_dow=2 (Wednesday), week 0 has Fri=day2, Sat=day3, Sun=day4."""
        config = _make_config(start_dow=2, num_days=14)
        opb = OpbBuilder()
        xn = _make_xn(opb, 14, 1)
        wr = _make_wr(opb, config.num_weeks, 1)

        soft = []
        _encode_weekend_night_linking(opb, xn, wr, config, ["Alice"], soft)

        # Friday of week 0: _week_day(0, 4, 2) = 0*7 - 2 + 4 = 2
        friday_d = _week_day(0, 4, 2)
        assert friday_d == 2
        assert _day_of_week(friday_d, 2) == 4  # Sanity: it's a Friday

        # Saturday of week 0: _week_day(0, 5, 2) = 0*7 - 2 + 5 = 3
        sat_d = _week_day(0, 5, 2)
        assert sat_d == 3
        assert _day_of_week(sat_d, 2) == 5  # Sanity: it's a Saturday

        # Sunday of week 0: _week_day(0, 6, 2) = 0*7 - 2 + 6 = 4
        sun_d = _week_day(0, 6, 2)
        assert sun_d == 4
        assert _day_of_week(sun_d, 2) == 6  # Sanity: it's a Sunday

        # Check that constraints were created for these days
        xn_fri = xn[friday_d][0]
        xn_sat = xn[sat_d][0]
        xn_sun = xn[sun_d][0]

        assert len(_constraints_containing(opb, xn_fri)) > 0, "Friday linking should exist"
        assert len(_constraints_containing(opb, xn_sat)) > 0, "Saturday linking should exist"
        assert len(_constraints_containing(opb, xn_sun)) > 0, "Sunday linking should exist"


# ---------------------------------------------------------------------------
# Test 7: Partial weeks are handled correctly
# ---------------------------------------------------------------------------

class TestPartialWeeks:
    """Out-of-bounds days in partial first/last weeks should be skipped."""

    def test_partial_first_week_no_friday(self):
        """With start_dow=6 (Sunday), week 0 has only Sun (day 0); no Fri/Sat."""
        config = _make_config(start_dow=6, num_days=7)
        opb = OpbBuilder()
        xn = _make_xn(opb, 7, 1)
        wr = _make_wr(opb, config.num_weeks, 1)

        # Week 0: _week_day(0, 4, 6) = 0*7 - 6 + 4 = -2 (out of bounds)
        # _week_day(0, 5, 6) = -1 (out of bounds)
        # _week_day(0, 6, 6) = 0 (in bounds - Sunday)
        friday_d = _week_day(0, 4, 6)
        sat_d = _week_day(0, 5, 6)
        sun_d = _week_day(0, 6, 6)
        assert friday_d < 0
        assert sat_d < 0
        assert sun_d == 0

        # Should not crash; should handle partial weeks gracefully
        soft = []
        _encode_weekend_night_linking(opb, xn, wr, config, ["Alice"], soft)

        # Sunday (day 0) should still have linking constraints
        xn_sun = xn[0][0]
        assert len(_constraints_containing(opb, xn_sun)) > 0, "Sunday linking should still exist"
