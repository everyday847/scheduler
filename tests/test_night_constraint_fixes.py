"""Tests for the night constraint fixes in schedule_solver.py.

Verifies:
1. Night blocking only applies Sun-Thu (not Fri/Sat) for non-vacation services
2. Vacation blocks all 7 nights
3. Stroke criterion checks next day's week, not same week
4. Clinic criterion only fires on Sun/Tue/Wed nights
5. First-week restriction blocks NCC_JR and STROKE until first Friday
6. SCVMC Rehab is in the blocked set
"""

from __future__ import annotations

from datetime import date

import pytest

from parafrost_scheduler.opb_encoder import OpbBuilder
from parafrost_scheduler.schedule_solver import (
    NIGHT_BLOCKED_SHIFTS,
    ScheduleSolverConfig,
    _day_of_week,
    _day_to_week,
    _encode_night_constraints,
    _encode_night_policy_criteria,
    _num_weeks_for,
)
from scheduler.night_call_solver import NightSolverConfig, CountMultiset
from scheduler.weekend_call_solver import WeekendSolverConfig
from scheduler.night_call_solver_policy import (
    CRITERION_CLINIC,
    CRITERION_STROKE,
    NightPolicyWeights,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(
    shifts: list[str],
    fellow_groups: dict[str, list[str]] | None = None,
    start_dow: int = 0,
    num_days: int = 14,
    night_hard_criteria: frozenset[str] | None = None,
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
        always_stroke_eligible=frozenset(),
        telestroke_stroke_eligible=frozenset(),
        stroke_only_eligible=frozenset(),
        total_weekends={},
        weekend_options=None,
        friday_weekend_options=None,
    )
    if night_hard_criteria is None:
        night_hard_criteria = frozenset()
    return ScheduleSolverConfig(
        fellow_groups=fellow_groups,
        shifts=shifts,
        constraints=[],
        night_config=night_config,
        weekend_config=weekend_config,
        night_hard_criteria=night_hard_criteria,
        start_dow=start_dow,
        num_days=num_days,
    )


def _make_vars(opb: OpbBuilder, num_fellows: int, num_days: int, num_shifts: int, num_weeks: int):
    """Allocate xs and xn variable arrays, all active (no CCM)."""
    # xs[f][w][s]
    xs = []
    for f in range(num_fellows):
        xs.append([])
        for w in range(num_weeks):
            xs[f].append([opb.new_var() for _ in range(num_shifts)])
    # xn[d][f]
    xn = []
    for d in range(num_days):
        xn.append([opb.new_var() for _ in range(num_fellows)])
    # wr[w][role][f] — 3 roles, empty dicts (no weekend roles for these tests)
    wr = []
    for w in range(num_weeks):
        wr.append([{} for _ in range(3)])
    return xs, xn, wr


def _count_constraints_containing(opb: OpbBuilder, var_id: int) -> int:
    """Count how many constraints reference a given variable id."""
    var_str_pos = f"x{var_id} "
    var_str_neg = f"~x{var_id} "
    count = 0
    for c in opb._constraints:
        if var_str_pos in c or var_str_neg in c:
            count += 1
    return count


def _get_constraints_containing(opb: OpbBuilder, var_id: int) -> list[str]:
    """Get all constraints referencing a given variable id."""
    var_str_pos = f"x{var_id} "
    var_str_neg = f"~x{var_id} "
    return [c for c in opb._constraints if var_str_pos in c or var_str_neg in c]


# ---------------------------------------------------------------------------
# Test 1: Night blocking only applies Sun-Thu for non-vacation services
# ---------------------------------------------------------------------------

class TestNightBlockingSunThu:
    """Non-vacation night-blocking services (SICU, NS, SCVMC Rehab)
    should only block Sun-Thu nights, NOT Fri/Sat."""

    def test_sicu_blocks_wednesday_night(self):
        """SICU in next-day's week should block a Wednesday night (dow=2)."""
        # start_dow=0 (Monday). Day 2 = Wednesday.
        # Next day = Thursday (day 3), week 0. SICU in week 0 should block.
        shifts = ["NCC1", "SICU"]
        config = _make_config(shifts, start_dow=0, num_days=14)
        shift_idx = {s: i for i, s in enumerate(shifts)}
        num_weeks = config.num_weeks
        opb = OpbBuilder()
        xs, xn, wr = _make_vars(opb, 1, 14, len(shifts), num_weeks)
        soft = []
        baseline = opb.num_constraints

        _encode_night_constraints(opb, xn, xs, wr, config, ["Alice"], shift_idx, soft)

        # Day 2 (Wed) should have a blocking constraint involving SICU
        sicu_var_week0 = xs[0][0][shift_idx["SICU"]]
        night_var_day2 = xn[2][0]
        constraints = _get_constraints_containing(opb, night_var_day2)
        sicu_constraints = [c for c in constraints if f"x{sicu_var_week0} " in c]
        assert len(sicu_constraints) > 0, "SICU should block Wednesday night"

    def test_sicu_does_not_block_friday_night(self):
        """SICU should NOT block Friday night (dow=4)."""
        shifts = ["NCC1", "SICU"]
        config = _make_config(shifts, start_dow=0, num_days=14)
        shift_idx = {s: i for i, s in enumerate(shifts)}
        num_weeks = config.num_weeks
        opb = OpbBuilder()
        xs, xn, wr = _make_vars(opb, 1, 14, len(shifts), num_weeks)
        soft = []

        _encode_night_constraints(opb, xn, xs, wr, config, ["Alice"], shift_idx, soft)

        # Day 4 = Friday. Should NOT have SICU blocking constraint.
        sicu_var_week0 = xs[0][0][shift_idx["SICU"]]
        sicu_var_week1 = xs[0][1][shift_idx["SICU"]]
        night_var_day4 = xn[4][0]
        constraints = _get_constraints_containing(opb, night_var_day4)
        sicu_constraints = [c for c in constraints
                           if f"x{sicu_var_week0} " in c or f"x{sicu_var_week1} " in c]
        assert len(sicu_constraints) == 0, "SICU should NOT block Friday night"

    def test_sicu_does_not_block_saturday_night(self):
        """SICU should NOT block Saturday night (dow=5)."""
        shifts = ["NCC1", "SICU"]
        config = _make_config(shifts, start_dow=0, num_days=14)
        shift_idx = {s: i for i, s in enumerate(shifts)}
        num_weeks = config.num_weeks
        opb = OpbBuilder()
        xs, xn, wr = _make_vars(opb, 1, 14, len(shifts), num_weeks)
        soft = []

        _encode_night_constraints(opb, xn, xs, wr, config, ["Alice"], shift_idx, soft)

        # Day 5 = Saturday. Should NOT have SICU blocking.
        sicu_var_week0 = xs[0][0][shift_idx["SICU"]]
        sicu_var_week1 = xs[0][1][shift_idx["SICU"]]
        night_var_day5 = xn[5][0]
        constraints = _get_constraints_containing(opb, night_var_day5)
        sicu_constraints = [c for c in constraints
                           if f"x{sicu_var_week0} " in c or f"x{sicu_var_week1} " in c]
        assert len(sicu_constraints) == 0, "SICU should NOT block Saturday night"

    def test_sicu_blocks_sunday_night(self):
        """SICU should block Sunday night (dow=6) since next morning is Monday."""
        shifts = ["NCC1", "SICU"]
        config = _make_config(shifts, start_dow=0, num_days=14)
        shift_idx = {s: i for i, s in enumerate(shifts)}
        num_weeks = config.num_weeks
        opb = OpbBuilder()
        xs, xn, wr = _make_vars(opb, 1, 14, len(shifts), num_weeks)
        soft = []

        _encode_night_constraints(opb, xn, xs, wr, config, ["Alice"], shift_idx, soft)

        # Day 6 = Sunday. Next day (Monday) is in week 1.
        # SICU in week 1 should block.
        sicu_var_week1 = xs[0][1][shift_idx["SICU"]]
        night_var_day6 = xn[6][0]
        constraints = _get_constraints_containing(opb, night_var_day6)
        sicu_constraints = [c for c in constraints if f"x{sicu_var_week1} " in c]
        assert len(sicu_constraints) > 0, "SICU should block Sunday night"

    def test_blocking_checks_next_day_week(self):
        """When night is Sunday, blocking should check the NEXT day's week (Monday's week)."""
        # start_dow=0 (Monday). Day 6 = Sunday. Next day = Monday day 7 = week 1.
        shifts = ["NCC1", "SICU"]
        config = _make_config(shifts, start_dow=0, num_days=14)
        shift_idx = {s: i for i, s in enumerate(shifts)}
        num_weeks = config.num_weeks
        opb = OpbBuilder()
        xs, xn, wr = _make_vars(opb, 1, 14, len(shifts), num_weeks)
        soft = []

        _encode_night_constraints(opb, xn, xs, wr, config, ["Alice"], shift_idx, soft)

        # Sunday night (day 6): should check week 1 (next day), NOT week 0 (same day)
        sicu_var_week0 = xs[0][0][shift_idx["SICU"]]
        sicu_var_week1 = xs[0][1][shift_idx["SICU"]]
        night_var_day6 = xn[6][0]
        constraints = _get_constraints_containing(opb, night_var_day6)

        # Should reference week 1's SICU, not week 0's
        week1_refs = [c for c in constraints if f"x{sicu_var_week1} " in c]
        week0_refs = [c for c in constraints if f"x{sicu_var_week0} " in c]
        assert len(week1_refs) > 0, "Sunday night should check next day's week (week 1)"
        # Week 0 should not be referenced for SICU blocking (it IS referenced for vacation)
        assert len(week0_refs) == 0 or all("Vac" in c for c in week0_refs), \
            "Sunday night should NOT check same-day's week for SICU"


# ---------------------------------------------------------------------------
# Test 2: Vacation blocks all 7 nights
# ---------------------------------------------------------------------------

class TestVacationBlocksAllNights:
    """Vacation should block all 7 nights of the week, including Fri/Sat."""

    def test_vacation_blocks_friday_night(self):
        """Vacation should block Friday night (dow=4)."""
        shifts = ["NCC1", "Vac"]
        config = _make_config(shifts, start_dow=0, num_days=14)
        shift_idx = {s: i for i, s in enumerate(shifts)}
        num_weeks = config.num_weeks
        opb = OpbBuilder()
        xs, xn, wr = _make_vars(opb, 1, 14, len(shifts), num_weeks)
        soft = []

        _encode_night_constraints(opb, xn, xs, wr, config, ["Alice"], shift_idx, soft)

        # Day 4 = Friday. Vac in week 0 should still block.
        vac_var_week0 = xs[0][0][shift_idx["Vac"]]
        night_var_day4 = xn[4][0]
        constraints = _get_constraints_containing(opb, night_var_day4)
        vac_constraints = [c for c in constraints if f"x{vac_var_week0} " in c]
        assert len(vac_constraints) > 0, "Vacation should block Friday night"

    def test_vacation_blocks_saturday_night(self):
        """Vacation should block Saturday night (dow=5)."""
        shifts = ["NCC1", "Vac"]
        config = _make_config(shifts, start_dow=0, num_days=14)
        shift_idx = {s: i for i, s in enumerate(shifts)}
        num_weeks = config.num_weeks
        opb = OpbBuilder()
        xs, xn, wr = _make_vars(opb, 1, 14, len(shifts), num_weeks)
        soft = []

        _encode_night_constraints(opb, xn, xs, wr, config, ["Alice"], shift_idx, soft)

        # Day 5 = Saturday. Vac in week 0 should still block.
        vac_var_week0 = xs[0][0][shift_idx["Vac"]]
        night_var_day5 = xn[5][0]
        constraints = _get_constraints_containing(opb, night_var_day5)
        vac_constraints = [c for c in constraints if f"x{vac_var_week0} " in c]
        assert len(vac_constraints) > 0, "Vacation should block Saturday night"

    def test_vacation_blocks_all_7_nights(self):
        """Vacation should block all 7 nights of the week."""
        shifts = ["NCC1", "Vac"]
        config = _make_config(shifts, start_dow=0, num_days=7)
        shift_idx = {s: i for i, s in enumerate(shifts)}
        num_weeks = config.num_weeks
        opb = OpbBuilder()
        xs, xn, wr = _make_vars(opb, 1, 7, len(shifts), num_weeks)
        soft = []

        _encode_night_constraints(opb, xn, xs, wr, config, ["Alice"], shift_idx, soft)

        vac_var_week0 = xs[0][0][shift_idx["Vac"]]
        blocked_count = 0
        for d in range(7):
            night_var = xn[d][0]
            constraints = _get_constraints_containing(opb, night_var)
            vac_constraints = [c for c in constraints if f"x{vac_var_week0} " in c]
            if vac_constraints:
                blocked_count += 1
        assert blocked_count == 7, f"Vacation should block all 7 nights, but only blocked {blocked_count}"


# ---------------------------------------------------------------------------
# Test 3: Stroke criterion checks next day's week
# ---------------------------------------------------------------------------

class TestStrokeCriterionNextDay:
    """Stroke criterion should block the night BEFORE a stroke workday,
    checking next day's week for the stroke assignment."""

    def test_stroke_blocks_sunday_night_before_monday(self):
        """Sunday night should be blocked if fellow is on Stroke the following week."""
        shifts = ["NCC1", "Stroke"]
        config = _make_config(
            shifts, start_dow=0, num_days=14,
            night_hard_criteria=frozenset({CRITERION_STROKE}),
        )
        shift_idx = {s: i for i, s in enumerate(shifts)}
        num_weeks = config.num_weeks
        opb = OpbBuilder()
        xs, xn, wr = _make_vars(opb, 1, 14, len(shifts), num_weeks)
        soft = []

        _encode_night_policy_criteria(opb, xn, xs, wr, config, ["Alice"], shift_idx, soft)

        # Day 6 = Sunday. Next day = Monday (week 1).
        # Stroke in week 1 should trigger the criterion.
        stroke_var_week1 = xs[0][1][shift_idx["Stroke"]]
        night_var_day6 = xn[6][0]
        constraints = _get_constraints_containing(opb, night_var_day6)
        stroke_constraints = [c for c in constraints if f"x{stroke_var_week1} " in c]
        assert len(stroke_constraints) > 0, "Sunday night should be blocked by next week's Stroke"

    def test_stroke_does_not_block_friday_night(self):
        """Friday night (dow=4) should NOT be blocked by Stroke (next day is Sat)."""
        shifts = ["NCC1", "Stroke"]
        config = _make_config(
            shifts, start_dow=0, num_days=14,
            night_hard_criteria=frozenset({CRITERION_STROKE}),
        )
        shift_idx = {s: i for i, s in enumerate(shifts)}
        num_weeks = config.num_weeks
        opb = OpbBuilder()
        xs, xn, wr = _make_vars(opb, 1, 14, len(shifts), num_weeks)
        soft = []

        _encode_night_policy_criteria(opb, xn, xs, wr, config, ["Alice"], shift_idx, soft)

        # Day 4 = Friday.
        stroke_var_week0 = xs[0][0][shift_idx["Stroke"]]
        stroke_var_week1 = xs[0][1][shift_idx["Stroke"]]
        night_var_day4 = xn[4][0]
        constraints = _get_constraints_containing(opb, night_var_day4)
        stroke_constraints = [c for c in constraints
                             if f"x{stroke_var_week0} " in c or f"x{stroke_var_week1} " in c]
        assert len(stroke_constraints) == 0, "Friday night should NOT be blocked by Stroke"

    def test_stroke_does_not_block_saturday_night(self):
        """Saturday night (dow=5) should NOT be blocked by Stroke weekday criterion."""
        shifts = ["NCC1", "Stroke"]
        config = _make_config(
            shifts, start_dow=0, num_days=14,
            night_hard_criteria=frozenset({CRITERION_STROKE}),
        )
        shift_idx = {s: i for i, s in enumerate(shifts)}
        num_weeks = config.num_weeks
        opb = OpbBuilder()
        xs, xn, wr = _make_vars(opb, 1, 14, len(shifts), num_weeks)
        soft = []

        _encode_night_policy_criteria(opb, xn, xs, wr, config, ["Alice"], shift_idx, soft)

        # Day 5 = Saturday.
        stroke_var_week0 = xs[0][0][shift_idx["Stroke"]]
        stroke_var_week1 = xs[0][1][shift_idx["Stroke"]]
        night_var_day5 = xn[5][0]
        constraints = _get_constraints_containing(opb, night_var_day5)
        stroke_constraints = [c for c in constraints
                             if f"x{stroke_var_week0} " in c or f"x{stroke_var_week1} " in c]
        assert len(stroke_constraints) == 0, "Saturday night should NOT be blocked by Stroke weekday criterion"

    def test_stroke_blocks_thursday_night_before_friday(self):
        """Thursday night (dow=3) should be blocked by Stroke (Fri is a workday)."""
        shifts = ["NCC1", "Stroke"]
        config = _make_config(
            shifts, start_dow=0, num_days=14,
            night_hard_criteria=frozenset({CRITERION_STROKE}),
        )
        shift_idx = {s: i for i, s in enumerate(shifts)}
        num_weeks = config.num_weeks
        opb = OpbBuilder()
        xs, xn, wr = _make_vars(opb, 1, 14, len(shifts), num_weeks)
        soft = []

        _encode_night_policy_criteria(opb, xn, xs, wr, config, ["Alice"], shift_idx, soft)

        # Day 3 = Thursday. Next day = Friday (week 0).
        stroke_var_week0 = xs[0][0][shift_idx["Stroke"]]
        night_var_day3 = xn[3][0]
        constraints = _get_constraints_containing(opb, night_var_day3)
        stroke_constraints = [c for c in constraints if f"x{stroke_var_week0} " in c]
        assert len(stroke_constraints) > 0, "Thursday night should be blocked by Stroke"


# ---------------------------------------------------------------------------
# Test 4: Clinic criterion only fires on Sun/Tue/Wed nights
# ---------------------------------------------------------------------------

class TestClinicCriterionDays:
    """Clinic criterion should only block Sunday, Tuesday, and Wednesday nights
    (nights before Mon/Wed/Thu clinic days)."""

    def _run_and_get_clinic_blocked_dows(self, start_dow: int = 0) -> set[int]:
        """Run encoding and return the set of DOWs that have clinic constraints."""
        shifts = ["NCC1", "Clinic/Elective"]
        config = _make_config(
            shifts, start_dow=start_dow, num_days=14,
            night_hard_criteria=frozenset({CRITERION_CLINIC}),
        )
        shift_idx = {s: i for i, s in enumerate(shifts)}
        num_weeks = config.num_weeks
        opb = OpbBuilder()
        xs, xn, wr = _make_vars(opb, 1, 14, len(shifts), num_weeks)
        soft = []

        _encode_night_policy_criteria(opb, xn, xs, wr, config, ["Alice"], shift_idx, soft)

        blocked_dows = set()
        for d in range(14):
            dow = _day_of_week(d, start_dow)
            night_var = xn[d][0]
            constraints = _get_constraints_containing(opb, night_var)
            # Check if any clinic shift var is referenced
            clinic_found = False
            for w in range(num_weeks):
                clinic_var = xs[0][w][shift_idx["Clinic/Elective"]]
                if any(f"x{clinic_var} " in c for c in constraints):
                    clinic_found = True
                    break
            if clinic_found:
                blocked_dows.add(dow)
        return blocked_dows

    def test_clinic_blocks_sunday_night(self):
        assert 6 in self._run_and_get_clinic_blocked_dows(), "Clinic should block Sunday night"

    def test_clinic_blocks_tuesday_night(self):
        assert 1 in self._run_and_get_clinic_blocked_dows(), "Clinic should block Tuesday night"

    def test_clinic_blocks_wednesday_night(self):
        assert 2 in self._run_and_get_clinic_blocked_dows(), "Clinic should block Wednesday night"

    def test_clinic_does_not_block_monday_night(self):
        assert 0 not in self._run_and_get_clinic_blocked_dows(), "Clinic should NOT block Monday night"

    def test_clinic_does_not_block_thursday_night(self):
        assert 3 not in self._run_and_get_clinic_blocked_dows(), "Clinic should NOT block Thursday night"

    def test_clinic_does_not_block_friday_night(self):
        assert 4 not in self._run_and_get_clinic_blocked_dows(), "Clinic should NOT block Friday night"

    def test_clinic_does_not_block_saturday_night(self):
        assert 5 not in self._run_and_get_clinic_blocked_dows(), "Clinic should NOT block Saturday night"

    def test_clinic_only_expected_dows(self):
        """Only Sun, Tue, Wed nights should be blocked."""
        blocked = self._run_and_get_clinic_blocked_dows()
        assert blocked == {6, 1, 2}, f"Expected {{6, 1, 2}}, got {blocked}"


# ---------------------------------------------------------------------------
# Test 5: First-week restriction blocks NCC_JR and STROKE until first Friday
# ---------------------------------------------------------------------------

class TestFirstWeekRestriction:
    """NCC_JR and STROKE fellows cannot take night call until Friday of the first week."""

    def test_ncc_jr_blocked_before_friday(self):
        """NCC_JR fellow should be blocked Mon-Thu of the first week."""
        shifts = ["NCC1"]
        config = _make_config(
            shifts,
            fellow_groups={"NCC_JR": ["Alice"], "NCC_SR": ["Bob"]},
            start_dow=0,  # Monday
            num_days=14,
        )
        shift_idx = {s: i for i, s in enumerate(shifts)}
        num_weeks = config.num_weeks
        opb = OpbBuilder()
        xs, xn, wr = _make_vars(opb, 2, 14, len(shifts), num_weeks)
        soft = []

        _encode_night_constraints(opb, xn, xs, wr, config, ["Alice", "Bob"], shift_idx, soft)

        # Alice (index 0) is NCC_JR. First Friday is day 4.
        # Days 0-3 should have unit clauses forcing Alice's night var to false.
        for d in range(4):
            night_var = xn[d][0]
            constraints = _get_constraints_containing(opb, night_var)
            unit_clauses = [c for c in constraints if f"~x{night_var}" in c and ">= 1" in c]
            assert len(unit_clauses) > 0, f"NCC_JR should be blocked on day {d} (before Friday)"

    def test_ncc_jr_allowed_on_friday(self):
        """NCC_JR fellow should NOT be blocked on Friday (day 4) or after."""
        shifts = ["NCC1"]
        config = _make_config(
            shifts,
            fellow_groups={"NCC_JR": ["Alice"], "NCC_SR": ["Bob"]},
            start_dow=0,
            num_days=14,
        )
        shift_idx = {s: i for i, s in enumerate(shifts)}
        num_weeks = config.num_weeks
        opb = OpbBuilder()
        xs, xn, wr = _make_vars(opb, 2, 14, len(shifts), num_weeks)
        soft = []

        _encode_night_constraints(opb, xn, xs, wr, config, ["Alice", "Bob"], shift_idx, soft)

        # Day 4 = Friday. Should NOT have a first-week unit clause.
        night_var = xn[4][0]
        constraints = _get_constraints_containing(opb, night_var)
        unit_clauses = [c for c in constraints
                       if f"+1 ~x{night_var} >= 1" in c]
        assert len(unit_clauses) == 0, "NCC_JR should NOT be blocked on Friday"

    def test_stroke_fellow_blocked_before_friday(self):
        """STROKE fellow should also be blocked before Friday."""
        shifts = ["NCC1"]
        config = _make_config(
            shifts,
            fellow_groups={"STROKE": ["Alice"], "NCC_SR": ["Bob"]},
            start_dow=0,
            num_days=14,
        )
        shift_idx = {s: i for i, s in enumerate(shifts)}
        num_weeks = config.num_weeks
        opb = OpbBuilder()
        xs, xn, wr = _make_vars(opb, 2, 14, len(shifts), num_weeks)
        soft = []

        _encode_night_constraints(opb, xn, xs, wr, config, ["Alice", "Bob"], shift_idx, soft)

        for d in range(4):
            night_var = xn[d][0]
            constraints = _get_constraints_containing(opb, night_var)
            unit_clauses = [c for c in constraints if f"~x{night_var}" in c and ">= 1" in c]
            assert len(unit_clauses) > 0, f"STROKE fellow should be blocked on day {d}"

    def test_non_restricted_fellow_not_blocked(self):
        """NCC_SR fellow should NOT be blocked by first-week restriction."""
        shifts = ["NCC1"]
        config = _make_config(
            shifts,
            fellow_groups={"NCC_JR": ["Alice"], "NCC_SR": ["Bob"]},
            start_dow=0,
            num_days=14,
        )
        shift_idx = {s: i for i, s in enumerate(shifts)}
        num_weeks = config.num_weeks
        opb = OpbBuilder()
        xs, xn, wr = _make_vars(opb, 2, 14, len(shifts), num_weeks)
        soft = []

        _encode_night_constraints(opb, xn, xs, wr, config, ["Alice", "Bob"], shift_idx, soft)

        # Bob (index 1) is NCC_SR. Should not have first-week unit clauses.
        for d in range(4):
            night_var = xn[d][1]
            constraints = _get_constraints_containing(opb, night_var)
            unit_clauses = [c for c in constraints if f"+1 ~x{night_var} >= 1" in c]
            assert len(unit_clauses) == 0, f"NCC_SR should NOT be blocked on day {d}"

    def test_first_friday_with_wednesday_start(self):
        """When start_dow=2 (Wednesday), first Friday is day 2."""
        shifts = ["NCC1"]
        config = _make_config(
            shifts,
            fellow_groups={"NCC_JR": ["Alice"], "NCC_SR": ["Bob"]},
            start_dow=2,  # Wednesday
            num_days=14,
        )
        shift_idx = {s: i for i, s in enumerate(shifts)}
        num_weeks = config.num_weeks
        opb = OpbBuilder()
        xs, xn, wr = _make_vars(opb, 2, 14, len(shifts), num_weeks)
        soft = []

        _encode_night_constraints(opb, xn, xs, wr, config, ["Alice", "Bob"], shift_idx, soft)

        # start_dow=2 (Wed). Day 0=Wed, Day 1=Thu, Day 2=Fri.
        # Days 0 and 1 should be blocked for Alice.
        for d in range(2):
            night_var = xn[d][0]
            constraints = _get_constraints_containing(opb, night_var)
            unit_clauses = [c for c in constraints if f"~x{night_var}" in c and ">= 1" in c]
            assert len(unit_clauses) > 0, f"NCC_JR should be blocked on day {d} (before first Friday)"

        # Day 2 (Friday) should NOT be blocked.
        night_var = xn[2][0]
        constraints = _get_constraints_containing(opb, night_var)
        unit_clauses = [c for c in constraints if f"+1 ~x{night_var} >= 1" in c]
        assert len(unit_clauses) == 0, "NCC_JR should NOT be blocked on first Friday"


# ---------------------------------------------------------------------------
# Test 6: SCVMC Rehab is in NIGHT_BLOCKED_SHIFTS
# ---------------------------------------------------------------------------

class TestScvmcRehabBlocked:
    """SCVMC Rehab should be in the NIGHT_BLOCKED_SHIFTS constant."""

    def test_scvmc_rehab_in_blocked_set(self):
        assert "SCVMC Rehab" in NIGHT_BLOCKED_SHIFTS

    def test_all_expected_shifts_present(self):
        expected = {"SICU", "Vac", "NS", "SCVMC Rehab"}
        assert NIGHT_BLOCKED_SHIFTS == expected

    def test_scvmc_rehab_generates_blocking_constraint(self):
        """SCVMC Rehab should generate blocking constraints like other blocked services."""
        shifts = ["NCC1", "SCVMC Rehab"]
        config = _make_config(shifts, start_dow=0, num_days=7)
        shift_idx = {s: i for i, s in enumerate(shifts)}
        num_weeks = config.num_weeks
        opb = OpbBuilder()
        xs, xn, wr = _make_vars(opb, 1, 7, len(shifts), num_weeks)
        soft = []

        _encode_night_constraints(opb, xn, xs, wr, config, ["Alice"], shift_idx, soft)

        # Day 0 = Monday. Next day = Tue (week 0). SCVMC Rehab should block.
        rehab_var_week0 = xs[0][0][shift_idx["SCVMC Rehab"]]
        night_var_day0 = xn[0][0]
        constraints = _get_constraints_containing(opb, night_var_day0)
        rehab_constraints = [c for c in constraints if f"x{rehab_var_week0} " in c]
        assert len(rehab_constraints) > 0, "SCVMC Rehab should block Monday night"


# ---------------------------------------------------------------------------
# Test 7: ISC blocking uses Sun-Thu / next-day-week rule
# ---------------------------------------------------------------------------

class TestIscBlocking:
    """ISC should only block Sun-Thu nights and check next day's week."""

    def test_isc_does_not_block_friday_night(self):
        shifts = ["NCC1", "ISC"]
        config = _make_config(shifts, start_dow=0, num_days=14)
        shift_idx = {s: i for i, s in enumerate(shifts)}
        num_weeks = config.num_weeks
        opb = OpbBuilder()
        xs, xn, wr = _make_vars(opb, 1, 14, len(shifts), num_weeks)
        soft = []

        _encode_night_constraints(opb, xn, xs, wr, config, ["Alice"], shift_idx, soft)

        isc_var_week0 = xs[0][0][shift_idx["ISC"]]
        isc_var_week1 = xs[0][1][shift_idx["ISC"]]
        night_var_day4 = xn[4][0]  # Friday
        constraints = _get_constraints_containing(opb, night_var_day4)
        isc_constraints = [c for c in constraints
                          if f"x{isc_var_week0} " in c or f"x{isc_var_week1} " in c]
        assert len(isc_constraints) == 0, "ISC should NOT block Friday night"

    def test_isc_does_not_block_saturday_night(self):
        shifts = ["NCC1", "ISC"]
        config = _make_config(shifts, start_dow=0, num_days=14)
        shift_idx = {s: i for i, s in enumerate(shifts)}
        num_weeks = config.num_weeks
        opb = OpbBuilder()
        xs, xn, wr = _make_vars(opb, 1, 14, len(shifts), num_weeks)
        soft = []

        _encode_night_constraints(opb, xn, xs, wr, config, ["Alice"], shift_idx, soft)

        isc_var_week0 = xs[0][0][shift_idx["ISC"]]
        isc_var_week1 = xs[0][1][shift_idx["ISC"]]
        night_var_day5 = xn[5][0]  # Saturday
        constraints = _get_constraints_containing(opb, night_var_day5)
        isc_constraints = [c for c in constraints
                          if f"x{isc_var_week0} " in c or f"x{isc_var_week1} " in c]
        assert len(isc_constraints) == 0, "ISC should NOT block Saturday night"

    def test_isc_blocks_wednesday_night(self):
        shifts = ["NCC1", "ISC"]
        config = _make_config(shifts, start_dow=0, num_days=14)
        shift_idx = {s: i for i, s in enumerate(shifts)}
        num_weeks = config.num_weeks
        opb = OpbBuilder()
        xs, xn, wr = _make_vars(opb, 1, 14, len(shifts), num_weeks)
        soft = []

        _encode_night_constraints(opb, xn, xs, wr, config, ["Alice"], shift_idx, soft)

        # Day 2 = Wednesday. Next day = Thursday (week 0).
        isc_var_week0 = xs[0][0][shift_idx["ISC"]]
        night_var_day2 = xn[2][0]
        constraints = _get_constraints_containing(opb, night_var_day2)
        isc_constraints = [c for c in constraints if f"x{isc_var_week0} " in c]
        assert len(isc_constraints) > 0, "ISC should block Wednesday night"

    def test_isc_blocks_monday_night(self):
        """ISC should block Monday night (dow=0) — next day is Tuesday (workday)."""
        shifts = ["NCC1", "ISC"]
        config = _make_config(shifts, start_dow=0, num_days=14)
        shift_idx = {s: i for i, s in enumerate(shifts)}
        num_weeks = config.num_weeks
        opb = OpbBuilder()
        xs, xn, wr = _make_vars(opb, 1, 14, len(shifts), num_weeks)
        soft = []

        _encode_night_constraints(opb, xn, xs, wr, config, ["Alice"], shift_idx, soft)

        # Day 0 = Monday. Next day = Tuesday (week 0).
        isc_var_week0 = xs[0][0][shift_idx["ISC"]]
        night_var_day0 = xn[0][0]
        constraints = _get_constraints_containing(opb, night_var_day0)
        isc_constraints = [c for c in constraints if f"x{isc_var_week0} " in c]
        assert len(isc_constraints) > 0, "ISC should block Monday night"

    def test_isc_blocks_sunday_night(self):
        """ISC should block Sunday night (dow=6) — next day is Monday (workday)."""
        shifts = ["NCC1", "ISC"]
        config = _make_config(shifts, start_dow=0, num_days=14)
        shift_idx = {s: i for i, s in enumerate(shifts)}
        num_weeks = config.num_weeks
        opb = OpbBuilder()
        xs, xn, wr = _make_vars(opb, 1, 14, len(shifts), num_weeks)
        soft = []

        _encode_night_constraints(opb, xn, xs, wr, config, ["Alice"], shift_idx, soft)

        # Day 6 = Sunday. Next day = Monday (day 7, week 1).
        isc_var_week1 = xs[0][1][shift_idx["ISC"]]
        night_var_day6 = xn[6][0]
        constraints = _get_constraints_containing(opb, night_var_day6)
        isc_constraints = [c for c in constraints if f"x{isc_var_week1} " in c]
        assert len(isc_constraints) > 0, "ISC should block Sunday night"
