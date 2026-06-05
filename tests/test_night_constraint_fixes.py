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
from scheduler.night_call_types import NightSolverConfig, CountMultiset
from scheduler.weekend_call_types import WeekendSolverConfig
from scheduler.night_policy_types import (
    CRITERION_ANAESTHESIA,
    CRITERION_CLINIC,
    CRITERION_FRIDAY_WEEKEND_NCC1,
    CRITERION_STROKE,
    CRITERION_SUNDAY_FOLLOWING,
    NightPolicyWeights,
)
from scheduler.semantic_constraints import (
    ConstraintLifecycle,
    ConstraintStrength,
    FellowSelector,
    SemanticConstraint,
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
    full_assignment: bool = False,
) -> ScheduleSolverConfig:
    """Build a minimal ScheduleSolverConfig for testing.

    Set full_assignment=True to model NCC/STROKE fellows (who are full_assignment
    in production): this suppresses the NH night-requires-weekday-shift gate, so
    blocking/first-week assertions aren't confounded by the gate's
    ``+1 x{shift} +1 ~x{night} >= 1`` constraint (whose tail substring-matches a
    bare night unit clause).
    """
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
    constraints = []
    if full_assignment:
        constraints.append(SemanticConstraint(
            kind="full_assignment", lifecycle=ConstraintLifecycle.STANDING_RULE,
            strength=ConstraintStrength.HARD,
            fellows=FellowSelector.by_groups(*fellow_groups.keys()),
            params={"name": "full_assignment"},
        ))
    return ScheduleSolverConfig(
        fellow_groups=fellow_groups,
        shifts=shifts,
        constraints=constraints,
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
    """Weekday-only night-blocking services block only Sun-Thu nights, NOT
    Fri/Sat, and check the NEXT day's week (the morning-after workday).

    NOTE: SICU/MICU/Vac/NS block ALL 7 nights (see TestAllWeekBlockedServices) —
    they are NOT Sun-Thu-only. This class uses SCVMC Rehab, which is genuinely
    weekday-only. full_assignment=True suppresses the NH gate so greps aren't
    confounded.
    """

    def test_ns_blocks_wednesday_night(self):
        """SCVMC Rehab in next-day's week should block a Wednesday night (dow=2)."""
        # start_dow=0 (Monday). Day 2 = Wednesday. Next day = Thursday (week 0).
        shifts = ["NCC1", "SCVMC Rehab"]
        config = _make_config(shifts, start_dow=0, num_days=14, full_assignment=True)
        shift_idx = {s: i for i, s in enumerate(shifts)}
        num_weeks = config.num_weeks
        opb = OpbBuilder()
        xs, xn, wr = _make_vars(opb, 1, 14, len(shifts), num_weeks)
        soft = []

        _encode_night_constraints(opb, xn, xs, wr, config, ["Alice"], shift_idx, soft)

        ns_var_week0 = xs[0][0][shift_idx["SCVMC Rehab"]]
        night_var_day2 = xn[2][0]
        constraints = _get_constraints_containing(opb, night_var_day2)
        ns_constraints = [c for c in constraints if f"x{ns_var_week0} " in c]
        assert len(ns_constraints) > 0, "SCVMC Rehab should block Wednesday night"

    def test_ns_does_not_block_friday_night(self):
        """SCVMC Rehab should NOT block Friday night (dow=4) — next morning is Saturday."""
        shifts = ["NCC1", "SCVMC Rehab"]
        config = _make_config(shifts, start_dow=0, num_days=14, full_assignment=True)
        shift_idx = {s: i for i, s in enumerate(shifts)}
        num_weeks = config.num_weeks
        opb = OpbBuilder()
        xs, xn, wr = _make_vars(opb, 1, 14, len(shifts), num_weeks)
        soft = []

        _encode_night_constraints(opb, xn, xs, wr, config, ["Alice"], shift_idx, soft)

        ns_var_week0 = xs[0][0][shift_idx["SCVMC Rehab"]]
        ns_var_week1 = xs[0][1][shift_idx["SCVMC Rehab"]]
        night_var_day4 = xn[4][0]
        constraints = _get_constraints_containing(opb, night_var_day4)
        ns_constraints = [c for c in constraints
                          if f"x{ns_var_week0} " in c or f"x{ns_var_week1} " in c]
        assert len(ns_constraints) == 0, "SCVMC Rehab should NOT block Friday night"

    def test_ns_does_not_block_saturday_night(self):
        """SCVMC Rehab should NOT block Saturday night (dow=5)."""
        shifts = ["NCC1", "SCVMC Rehab"]
        config = _make_config(shifts, start_dow=0, num_days=14, full_assignment=True)
        shift_idx = {s: i for i, s in enumerate(shifts)}
        num_weeks = config.num_weeks
        opb = OpbBuilder()
        xs, xn, wr = _make_vars(opb, 1, 14, len(shifts), num_weeks)
        soft = []

        _encode_night_constraints(opb, xn, xs, wr, config, ["Alice"], shift_idx, soft)

        ns_var_week0 = xs[0][0][shift_idx["SCVMC Rehab"]]
        ns_var_week1 = xs[0][1][shift_idx["SCVMC Rehab"]]
        night_var_day5 = xn[5][0]
        constraints = _get_constraints_containing(opb, night_var_day5)
        ns_constraints = [c for c in constraints
                          if f"x{ns_var_week0} " in c or f"x{ns_var_week1} " in c]
        assert len(ns_constraints) == 0, "SCVMC Rehab should NOT block Saturday night"

    def test_ns_blocks_sunday_night(self):
        """SCVMC Rehab should block Sunday night (dow=6) since next morning is Monday."""
        shifts = ["NCC1", "SCVMC Rehab"]
        config = _make_config(shifts, start_dow=0, num_days=14, full_assignment=True)
        shift_idx = {s: i for i, s in enumerate(shifts)}
        num_weeks = config.num_weeks
        opb = OpbBuilder()
        xs, xn, wr = _make_vars(opb, 1, 14, len(shifts), num_weeks)
        soft = []

        _encode_night_constraints(opb, xn, xs, wr, config, ["Alice"], shift_idx, soft)

        # Day 6 = Sunday. Next day (Monday) is in week 1. SCVMC Rehab in week 1 should block.
        ns_var_week1 = xs[0][1][shift_idx["SCVMC Rehab"]]
        night_var_day6 = xn[6][0]
        constraints = _get_constraints_containing(opb, night_var_day6)
        ns_constraints = [c for c in constraints if f"x{ns_var_week1} " in c]
        assert len(ns_constraints) > 0, "SCVMC Rehab should block Sunday night"

    def test_blocking_checks_next_day_week(self):
        """When night is Sunday, weekday-only blocking checks the NEXT day's week."""
        # start_dow=0 (Monday). Day 6 = Sunday. Next day = Monday day 7 = week 1.
        shifts = ["NCC1", "SCVMC Rehab"]
        config = _make_config(shifts, start_dow=0, num_days=14, full_assignment=True)
        shift_idx = {s: i for i, s in enumerate(shifts)}
        num_weeks = config.num_weeks
        opb = OpbBuilder()
        xs, xn, wr = _make_vars(opb, 1, 14, len(shifts), num_weeks)
        soft = []

        _encode_night_constraints(opb, xn, xs, wr, config, ["Alice"], shift_idx, soft)

        # Sunday night (day 6): should check week 1 (next day), NOT week 0 (same day)
        ns_var_week0 = xs[0][0][shift_idx["SCVMC Rehab"]]
        ns_var_week1 = xs[0][1][shift_idx["SCVMC Rehab"]]
        night_var_day6 = xn[6][0]
        constraints = _get_constraints_containing(opb, night_var_day6)

        week1_refs = [c for c in constraints if f"x{ns_var_week1} " in c]
        week0_refs = [c for c in constraints if f"x{ns_var_week0} " in c]
        assert len(week1_refs) > 0, "Sunday night should check next day's week (week 1)"
        assert len(week0_refs) == 0, "Sunday night should NOT check same-day's week for NS"


class TestAllWeekBlockedServices:
    """SICU, MICU, Vac, and NS block ALL 7 nights (via the current week),
    including Fri/Sat — the all-week policy, distinct from the Sun-Thu-only
    services. NS was moved here (it must block the full week, not just Sun-Thu)."""

    @pytest.mark.parametrize("service", ["SICU", "MICU", "Vac", "NS"])
    @pytest.mark.parametrize("day", list(range(7)))
    def test_blocks_every_night(self, service, day):
        shifts = ["NCC1", service]
        config = _make_config(shifts, start_dow=0, num_days=7, full_assignment=True)
        shift_idx = {s: i for i, s in enumerate(shifts)}
        num_weeks = config.num_weeks
        opb = OpbBuilder()
        xs, xn, wr = _make_vars(opb, 1, 7, len(shifts), num_weeks)
        soft = []

        _encode_night_constraints(opb, xn, xs, wr, config, ["Alice"], shift_idx, soft)

        # All-week services block via the CURRENT week (week 0 for all 7 days).
        svc_var_week0 = xs[0][0][shift_idx[service]]
        night_var = xn[day][0]
        constraints = _get_constraints_containing(opb, night_var)
        block = [c for c in constraints if f"x{svc_var_week0} " in c and "<= 1" in c]
        assert len(block) > 0, f"{service} should block night day {day} (all-week)"


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


class TestTelestrokeClinicNotPenalized:
    """Telestroke/Clinic fellows may take ANY weekday night, so the clinic
    criterion must NEVER reference a Telestroke/Clinic shift variable."""

    def _telestroke_clinic_blocked_dows(self, start_dow: int = 0) -> set[int]:
        """Return the DOWs whose night gets a clinic-criterion constraint when
        the fellow is on Telestroke/Clinic.

        Only the ``clinic`` criterion is enabled (hard) here, but the Sunday
        night also carries the unrelated ``sunday_following`` criterion which
        legitimately references the (Telestroke/Clinic) next-week shift var. We
        therefore detect the clinic criterion by its distinctive *same-week*
        gating on Tue/Wed and the previous-Sunday → next-week Monday gating, and
        we exclude dow 6 (Sunday) from this clinic-only probe to avoid the
        sunday_following confound — Telestroke/Clinic exemption on Sun is covered
        by the canonical-evaluator tests in test_night_policy_clinic_criterion.
        """
        shifts = ["NCC1", "Telestroke/Clinic"]
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
            if dow == 6:
                continue  # Sunday carries sunday_following — see docstring
            night_var = xn[d][0]
            constraints = _get_constraints_containing(opb, night_var)
            for w in range(num_weeks):
                tele_var = xs[0][w][shift_idx["Telestroke/Clinic"]]
                if any(f"x{tele_var} " in c for c in constraints):
                    blocked_dows.add(dow)
                    break
        return blocked_dows

    def test_telestroke_clinic_never_blocked(self):
        assert self._telestroke_clinic_blocked_dows() == set(), (
            "Telestroke/Clinic must not trigger the clinic criterion on any "
            "weekday night (Tue/Wed in particular)"
        )


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
            full_assignment=True,
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
            full_assignment=True,
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
            full_assignment=True,
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
            full_assignment=True,
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
        # Core blocked services that must always be present (superset check so
        # adding new blocked shifts like NHS/AAN/RWC/NCS doesn't break this).
        expected = {"SICU", "MICU", "Vac", "NS", "SCVMC Rehab", "NHS", "AAN", "RWC", "NCS 2026"}
        assert expected <= NIGHT_BLOCKED_SHIFTS

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
        config = _make_config(shifts, start_dow=0, num_days=14, full_assignment=True)
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
        config = _make_config(shifts, start_dow=0, num_days=14, full_assignment=True)
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
        config = _make_config(shifts, start_dow=0, num_days=14, full_assignment=True)
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
        config = _make_config(shifts, start_dow=0, num_days=14, full_assignment=True)
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
        config = _make_config(shifts, start_dow=0, num_days=14, full_assignment=True)
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


# ---------------------------------------------------------------------------
# Default-value guard for night_hard_criteria. Asserts the SHIPPED default,
# separate from behavioral tests (which pin their own). If someone flips the
# default, THIS test fails loudly.
# ---------------------------------------------------------------------------

class TestNightHardCriteriaDefaults:
    def test_default_hard_criteria_excludes_sunday_following(self):
        """sunday_following is SOFT by default: a hard sunday_following conflicts
        with the Stroke weekend-Sunday-night preference, because its non-preferred
        set includes Telestroke/Clinic + Clinic/Elective — the rotations Stroke
        fellows spend ~23/53 weeks on (hard per-fellow totals). anaesthesia and
        friday_weekend_ncc1 stay hard."""
        cfg = _make_config(["NCC1"], num_days=14, night_hard_criteria=None)
        # _make_config passes None -> frozenset(); assert the PRODUCTION default
        # by constructing a config without overriding the field.
        from parafrost_scheduler.schedule_solver import ScheduleSolverConfig
        from scheduler.night_call_types import NightSolverConfig as _NC
        from scheduler.weekend_call_types import WeekendSolverConfig as _WC
        prod = ScheduleSolverConfig(
            fellow_groups={"NCC_SR": ["A"]}, shifts=["NCC1"], constraints=[],
            night_config=_NC(total_nights={}, friday_nights={},
                             total_night_multisets=(), friday_night_multisets=(),
                             ccm_fellows=frozenset(), holiday_dates=(),
                             horizon_start_date=date(2026, 7, 1)),
            weekend_config=_WC(ncc_totals={}, stroke_totals={}, stroke_cohort=(),
                               stroke_cohort_total=None, ccm_fellows=frozenset(),
                               always_stroke_eligible=frozenset(),
                               telestroke_stroke_eligible=frozenset(),
                               stroke_only_eligible=frozenset(), total_weekends={},
                               weekend_options=None, friday_weekend_options=None),
            num_days=14,
        )
        assert prod.night_hard_criteria == frozenset(
            {CRITERION_ANAESTHESIA, CRITERION_FRIDAY_WEEKEND_NCC1}
        )
        assert CRITERION_SUNDAY_FOLLOWING not in prod.night_hard_criteria
