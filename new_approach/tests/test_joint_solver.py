"""Tests for the joint weekend+night OPB solver.

Covers:
1. Formula structure (var/constraint counts, OPB header validity)
2. Weekend constraint satisfaction (distinct roles, eligibility, totals, spacing)
3. Night constraint satisfaction (exactly-one, blocking, totals)
4. friday_weekend_ncc1 linking — joint solution has <= violations vs night-only
5. Hard criteria enforcement
6. 2-week fixture end-to-end solve
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scheduler.call_schedule_common import (
    ParsedCallScheduleCsv,
    WEEKEND_ROLES,
    WeekRow,
)
from scheduler.night_call_solver import (
    NightScheduleSolution,
    NightSolverConfig,
    CountMultiset,
)
from scheduler.weekend_call_solver import (
    WeekendScheduleSolution,
    WeekendSolverConfig,
)
from scheduler.night_call_solver_policy import (
    ALL_POLICY_CRITERIA,
    CRITERION_FRIDAY_WEEKEND_NCC1,
    CRITERION_STROKE,
    NightPolicyCounts,
    NightPolicyWeights,
    NightPolicySolveResult,
)

from parafrost_scheduler.joint_solver import (
    JointSolveResult,
    JointVarMap,
    _ROLE_NCC1,
    _ROLE_NCC2,
    _ROLE_STROKE,
    _ROLE_INDICES,
    build_joint_opb,
    extract_weekend_solution,
    extract_night_solution,
    solve_joint_schedule,
    solve_joint_schedule_incremental,
    weighted_upper_bound_joint,
    _criteria_counts_joint,
)
from parafrost_scheduler.roundingsat_runner import RoundingSatRunner

ROUNDINGSAT_BINARY = (
    Path(__file__).resolve().parent.parent
    / "vendor"
    / "roundingsat"
    / "build"
    / "roundingsat"
)


@pytest.fixture
def runner():
    if not ROUNDINGSAT_BINARY.exists():
        pytest.skip("RoundingSat binary not built")
    return RoundingSatRunner(ROUNDINGSAT_BINARY)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _make_minimal_two_week() -> ParsedCallScheduleCsv:
    """2-week, 10-fellow fixture.

    Fellows:
      A-D  — NCC1/NCC2 eligible, Elective service
      E, F — Stroke-eligible (always_stroke_eligible in weekend_config)
      G    — NCC1/NCC2 eligible, on Stroke service (telestroke group)
      H    — NCC1/NCC2 eligible, Elective service
      I    — NCC1/NCC2 eligible, Anaesthesia service (policy criterion)
      J    — NCC1/NCC2 eligible, Clinic service (policy criterion)
    """
    fellow_names = ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J"]

    w1 = {f: "Elective" for f in fellow_names}
    w1["I"] = "Anesthesia"
    w1["J"] = "Clinic"
    w1["G"] = "Stroke"

    w2 = {f: "Elective" for f in fellow_names}
    w2["I"] = "Anesthesia"
    w2["J"] = "Clinic"
    w2["G"] = "Stroke"

    return ParsedCallScheduleCsv(
        fellow_names=fellow_names,
        existing_schedule_columns=(),  # no pre-existing schedule columns
        week_rows=[
            WeekRow(
                weekday_assignments=w1,
                schedule_assignments={},
                raw_row=list(w1.values()),
            ),
            WeekRow(
                weekday_assignments=w2,
                schedule_assignments={},
                raw_row=list(w2.values()),
            ),
        ],
        trailing_rows=[],
    )


def _minimal_night_config(fellow_names: list[str]) -> NightSolverConfig:
    """2 weeks x 7 nights = 14 nights total. Distribute across all 10 fellows."""
    totals: dict[str, int] = {}
    for i, f in enumerate(fellow_names):
        totals[f] = 2 if i < 4 else 1
    return NightSolverConfig(
        total_nights=totals,
        friday_nights={},
        total_night_multisets=(),
        friday_night_multisets=(),
        ccm_fellows=frozenset(),
        holiday_dates=(),
    )


def _minimal_weekend_config() -> WeekendSolverConfig:
    """Minimal weekend config for 2 weeks, 10 fellows.

    NCC totals sum to 4 (2 weeks x 2 NCC roles).
    Stroke cohort E,F each get 1 stroke; total 2.
    """
    return WeekendSolverConfig(
        ncc_totals={
            "A": 1, "B": 1, "C": 1, "D": 1,
            "G": 0, "H": 0, "I": 0, "J": 0,
        },
        stroke_totals={
            "A": 0, "B": 0, "C": 0, "D": 0,
            "I": 0, "J": 0, "H": 0,
        },
        stroke_cohort=("E", "F"),
        stroke_cohort_total=2,
        stroke_cohort_min=1,
        stroke_cohort_max=1,
        ccm_fellows=frozenset(),
        ccm_ncc_total=None,
        always_stroke_eligible=frozenset({"E", "F"}),
        telestroke_stroke_eligible=frozenset({"G"}),
        stroke_only_eligible=frozenset(),
    )


# ---------------------------------------------------------------------------
# Formula structure tests (no solver needed)
# ---------------------------------------------------------------------------

class TestJointFormulaStructure:
    """Verify the OPB formula has correct structure without running the solver."""

    def test_variable_count_positive(self):
        parsed = _make_minimal_two_week()
        night_config = _minimal_night_config(parsed.fellow_names)
        weekend_config = _minimal_weekend_config()
        weights = NightPolicyWeights()

        opb, var_map = build_joint_opb(
            parsed,
            night_config=night_config,
            weekend_config=weekend_config,
            hard_criteria=frozenset(),
            weights=weights,
            soft_bound=None,
        )

        assert var_map.num_days == 14
        assert var_map.num_fellows == 10
        assert var_map.num_weeks == 2
        # Night vars: 14 x 10 = 140, plus weekend vars
        assert opb.num_vars >= 140

    def test_weekend_var_map_structure(self):
        parsed = _make_minimal_two_week()
        night_config = _minimal_night_config(parsed.fellow_names)
        weekend_config = _minimal_weekend_config()
        weights = NightPolicyWeights()

        opb, var_map = build_joint_opb(
            parsed,
            night_config=night_config,
            weekend_config=weekend_config,
            hard_criteria=frozenset(),
            weights=weights,
            soft_bound=None,
        )

        for w in range(2):
            assert len(var_map.wr[w][_ROLE_NCC1]) > 0
            assert len(var_map.wr[w][_ROLE_NCC2]) > 0
            # Stroke: only E, F (always) and G (telestroke, Stroke service)
            assert len(var_map.wr[w][_ROLE_STROKE]) >= 2

    def test_constraint_count_positive(self):
        parsed = _make_minimal_two_week()
        night_config = _minimal_night_config(parsed.fellow_names)
        weekend_config = _minimal_weekend_config()

        opb, var_map = build_joint_opb(
            parsed,
            night_config=night_config,
            weekend_config=weekend_config,
            hard_criteria=frozenset(),
            weights=NightPolicyWeights(),
            soft_bound=None,
        )

        assert opb.num_constraints > 0

    def test_opb_header_valid(self):
        parsed = _make_minimal_two_week()
        night_config = _minimal_night_config(parsed.fellow_names)
        weekend_config = _minimal_weekend_config()

        opb, var_map = build_joint_opb(
            parsed,
            night_config=night_config,
            weekend_config=weekend_config,
            hard_criteria=frozenset(),
            weights=NightPolicyWeights(),
            soft_bound=100,
        )

        text = opb.to_opb()
        first_line = text.splitlines()[0]
        assert f"#variable= {opb.num_vars}" in first_line
        assert f"#constraint= {opb.num_constraints}" in first_line

    def test_soft_bound_adds_constraint(self):
        parsed = _make_minimal_two_week()
        night_config = _minimal_night_config(parsed.fellow_names)
        weekend_config = _minimal_weekend_config()
        weights = NightPolicyWeights()

        opb_no_bound, _ = build_joint_opb(
            parsed,
            night_config=night_config,
            weekend_config=weekend_config,
            hard_criteria=frozenset(),
            weights=weights,
            soft_bound=None,
        )
        opb_with_bound, _ = build_joint_opb(
            parsed,
            night_config=night_config,
            weekend_config=weekend_config,
            hard_criteria=frozenset(),
            weights=weights,
            soft_bound=50,
        )
        assert opb_with_bound.num_constraints > opb_no_bound.num_constraints

    def test_hard_friday_ncc1_no_indicator_vars(self):
        """With hard friday_weekend_ncc1, no indicator variables are created."""
        parsed = _make_minimal_two_week()
        night_config = _minimal_night_config(parsed.fellow_names)
        weekend_config = _minimal_weekend_config()

        opb_soft, var_map_soft = build_joint_opb(
            parsed,
            night_config=night_config,
            weekend_config=weekend_config,
            hard_criteria=frozenset(),
            weights=NightPolicyWeights(),
            soft_bound=None,
        )
        opb_hard, var_map_hard = build_joint_opb(
            parsed,
            night_config=night_config,
            weekend_config=weekend_config,
            hard_criteria=frozenset({CRITERION_FRIDAY_WEEKEND_NCC1}),
            weights=NightPolicyWeights(),
            soft_bound=None,
        )

        # Hard version has no indicator vars
        assert opb_hard.num_vars <= opb_soft.num_vars
        for w in range(2):
            assert len(var_map_hard.indicator[w]) == 0


# ---------------------------------------------------------------------------
# Solver integration tests
# ---------------------------------------------------------------------------

class TestJointSolverConstraints:
    """End-to-end solve tests using RoundingSat (requires binary)."""

    def test_weekend_exactly_one_per_role(self, runner):
        """Each week must have exactly one fellow per weekend role."""
        parsed = _make_minimal_two_week()
        night_config = _minimal_night_config(parsed.fellow_names)
        weekend_config = _minimal_weekend_config()

        result = solve_joint_schedule(
            parsed,
            night_config=night_config,
            weekend_config=weekend_config,
            hard_criteria=frozenset(),
            weights=NightPolicyWeights(),
            runner=runner,
            emit_summary=False,
        )

        for w, week_assignment in enumerate(result.weekend_solution.assignments_by_week):
            for role in WEEKEND_ROLES:
                assert role in week_assignment, f"Missing {role} in week {w}"
                assert week_assignment[role] in parsed.fellow_names

    def test_weekend_all_distinct_roles(self, runner):
        """No fellow assigned to two roles in the same week."""
        parsed = _make_minimal_two_week()
        night_config = _minimal_night_config(parsed.fellow_names)
        weekend_config = _minimal_weekend_config()

        result = solve_joint_schedule(
            parsed,
            night_config=night_config,
            weekend_config=weekend_config,
            hard_criteria=frozenset(),
            weights=NightPolicyWeights(),
            runner=runner,
            emit_summary=False,
        )

        for w, week_assignment in enumerate(result.weekend_solution.assignments_by_week):
            assigned = list(week_assignment.values())
            assert len(set(assigned)) == 3, (
                f"Week {w}: duplicate assignments {assigned}"
            )

    def test_weekend_stroke_only_stroke_eligible(self, runner):
        """Weekend Stroke role assigned only to stroke-eligible fellows."""
        parsed = _make_minimal_two_week()
        night_config = _minimal_night_config(parsed.fellow_names)
        weekend_config = _minimal_weekend_config()

        result = solve_joint_schedule(
            parsed,
            night_config=night_config,
            weekend_config=weekend_config,
            hard_criteria=frozenset(),
            weights=NightPolicyWeights(),
            runner=runner,
            emit_summary=False,
        )

        # E, F (always_stroke_eligible), G (telestroke + Stroke service)
        stroke_eligible = {"E", "F", "G"}
        for w, week_assignment in enumerate(result.weekend_solution.assignments_by_week):
            stroke_fellow = week_assignment["Weekend Stroke"]
            assert stroke_fellow in stroke_eligible, (
                f"Week {w}: {stroke_fellow} assigned Stroke but not eligible"
            )

    def test_weekend_ncc_totals_satisfied(self, runner):
        """NCC totals per fellow match the weekend config."""
        parsed = _make_minimal_two_week()
        night_config = _minimal_night_config(parsed.fellow_names)
        weekend_config = _minimal_weekend_config()

        result = solve_joint_schedule(
            parsed,
            night_config=night_config,
            weekend_config=weekend_config,
            hard_criteria=frozenset(),
            weights=NightPolicyWeights(),
            runner=runner,
            emit_summary=False,
        )

        ncc_counts: dict[str, int] = {f: 0 for f in parsed.fellow_names}
        for week_assignment in result.weekend_solution.assignments_by_week:
            ncc_counts[week_assignment["Weekend NCC1"]] += 1
            ncc_counts[week_assignment["Weekend NCC2"]] += 1

        for fellow_name, expected in weekend_config.ncc_totals.items():
            assert ncc_counts[fellow_name] == expected, (
                f"{fellow_name}: expected NCC total {expected}, got {ncc_counts[fellow_name]}"
            )

    def test_weekend_stroke_cohort_totals(self, runner):
        """Stroke cohort totals and per-fellow bounds satisfied."""
        parsed = _make_minimal_two_week()
        night_config = _minimal_night_config(parsed.fellow_names)
        weekend_config = _minimal_weekend_config()

        result = solve_joint_schedule(
            parsed,
            night_config=night_config,
            weekend_config=weekend_config,
            hard_criteria=frozenset(),
            weights=NightPolicyWeights(),
            runner=runner,
            emit_summary=False,
        )

        stroke_counts: dict[str, int] = {f: 0 for f in parsed.fellow_names}
        for week_assignment in result.weekend_solution.assignments_by_week:
            stroke_counts[week_assignment["Weekend Stroke"]] += 1

        for fellow_name in weekend_config.stroke_cohort:
            count = stroke_counts[fellow_name]
            assert weekend_config.stroke_cohort_min <= count <= weekend_config.stroke_cohort_max, (
                f"{fellow_name}: stroke count {count} outside "
                f"[{weekend_config.stroke_cohort_min}, {weekend_config.stroke_cohort_max}]"
            )

        if weekend_config.stroke_cohort_total is not None:
            cohort_total = sum(stroke_counts[f] for f in weekend_config.stroke_cohort)
            assert cohort_total == weekend_config.stroke_cohort_total

    def test_night_exactly_one_per_day(self, runner):
        """Each night slot must have exactly one fellow assigned."""
        parsed = _make_minimal_two_week()
        night_config = _minimal_night_config(parsed.fellow_names)
        weekend_config = _minimal_weekend_config()

        result = solve_joint_schedule(
            parsed,
            night_config=night_config,
            weekend_config=weekend_config,
            hard_criteria=frozenset(),
            weights=NightPolicyWeights(),
            runner=runner,
            emit_summary=False,
        )

        from scheduler.call_schedule_common import NIGHT_ROLES
        for w, week_assignments in enumerate(result.night_solution.assignments_by_week):
            assert len(week_assignments) == 7
            for role in NIGHT_ROLES:
                assert role in week_assignments, f"Week {w}: missing {role}"
                assert week_assignments[role] in parsed.fellow_names

    def test_night_total_counts_satisfied(self, runner):
        """Total nights per fellow match the night config exactly."""
        parsed = _make_minimal_two_week()
        night_config = _minimal_night_config(parsed.fellow_names)
        weekend_config = _minimal_weekend_config()

        result = solve_joint_schedule(
            parsed,
            night_config=night_config,
            weekend_config=weekend_config,
            hard_criteria=frozenset(),
            weights=NightPolicyWeights(),
            runner=runner,
            emit_summary=False,
        )

        total_by_fellow: dict[str, int] = {f: 0 for f in parsed.fellow_names}
        for week_assignments in result.night_solution.assignments_by_week:
            for fellow in week_assignments.values():
                total_by_fellow[fellow] += 1

        for fellow_name, expected in night_config.total_nights.items():
            assert total_by_fellow[fellow_name] == expected, (
                f"{fellow_name}: expected {expected} nights, got {total_by_fellow[fellow_name]}"
            )

    def test_night_no_three_consecutive(self, runner):
        """No fellow works 3 or more consecutive nights."""
        parsed = _make_minimal_two_week()
        night_config = _minimal_night_config(parsed.fellow_names)
        weekend_config = _minimal_weekend_config()

        result = solve_joint_schedule(
            parsed,
            night_config=night_config,
            weekend_config=weekend_config,
            hard_criteria=frozenset(),
            weights=NightPolicyWeights(),
            runner=runner,
            emit_summary=False,
        )

        from scheduler.call_schedule_common import NIGHT_ROLES
        all_assignments: list[str] = []
        for week_assignments in result.night_solution.assignments_by_week:
            for role in NIGHT_ROLES:
                all_assignments.append(week_assignments[role])

        for start in range(len(all_assignments) - 2):
            triple = all_assignments[start:start + 3]
            if triple[0] == triple[1] == triple[2]:
                raise AssertionError(
                    f"Three consecutive nights for {triple[0]} starting at day {start}"
                )

    def test_hard_friday_ncc1_enforced(self, runner):
        """Hard friday_weekend_ncc1: no fellow works Friday night AND is Weekend NCC1."""
        parsed = _make_minimal_two_week()
        night_config = _minimal_night_config(parsed.fellow_names)
        weekend_config = _minimal_weekend_config()

        result = solve_joint_schedule(
            parsed,
            night_config=night_config,
            weekend_config=weekend_config,
            hard_criteria=frozenset({CRITERION_FRIDAY_WEEKEND_NCC1}),
            weights=NightPolicyWeights(),
            runner=runner,
            emit_summary=False,
        )

        from scheduler.call_schedule_common import NIGHT_ROLES
        for w, (night_assignments, weekend_assignments) in enumerate(zip(
            result.night_solution.assignments_by_week,
            result.weekend_solution.assignments_by_week,
        )):
            friday_night_fellow = night_assignments["Night Fri"]
            weekend_ncc1_fellow = weekend_assignments["Weekend NCC1"]
            assert friday_night_fellow != weekend_ncc1_fellow, (
                f"Week {w}: {friday_night_fellow} is both Friday night and Weekend NCC1"
            )

    def test_policy_result_counts_consistent(self, runner):
        """Policy counts in the result match independent recomputation."""
        parsed = _make_minimal_two_week()
        night_config = _minimal_night_config(parsed.fellow_names)
        weekend_config = _minimal_weekend_config()

        result = solve_joint_schedule(
            parsed,
            night_config=night_config,
            weekend_config=weekend_config,
            hard_criteria=frozenset(),
            weights=NightPolicyWeights(),
            runner=runner,
            emit_summary=False,
        )

        recomputed = _criteria_counts_joint(
            parsed,
            result.weekend_solution,
            result.night_solution,
            NightPolicyWeights(),
        )
        assert recomputed.weighted_total == result.night_policy_result.counts.weighted_total
        for criterion in ALL_POLICY_CRITERIA:
            assert recomputed.by_criterion[criterion] == result.night_policy_result.counts.by_criterion[criterion]


class TestJointVsNightOnly:
    """Verify joint optimization advantages."""

    def test_joint_incremental_achieves_zero_friday_ncc1(self, runner):
        """Joint incremental solver achieves 0 friday_weekend_ncc1 violations."""
        parsed = _make_minimal_two_week()
        night_config = _minimal_night_config(parsed.fellow_names)
        weekend_config = _minimal_weekend_config()
        weights = NightPolicyWeights(friday_weekend_ncc1=1)

        joint_result = solve_joint_schedule_incremental(
            parsed,
            night_config=night_config,
            weekend_config=weekend_config,
            hard_criteria=frozenset(),
            weights=weights,
            runner=runner,
            emit_summary=False,
        )

        joint_violations = joint_result.night_policy_result.counts.by_criterion[
            CRITERION_FRIDAY_WEEKEND_NCC1
        ]
        assert joint_violations == 0, (
            f"Expected 0 friday_weekend_ncc1 violations, got {joint_violations}"
        )

    def test_incremental_is_optimized(self, runner):
        """Incremental joint solve marks result as optimized."""
        parsed = _make_minimal_two_week()
        night_config = _minimal_night_config(parsed.fellow_names)
        weekend_config = _minimal_weekend_config()

        result = solve_joint_schedule_incremental(
            parsed,
            night_config=night_config,
            weekend_config=weekend_config,
            hard_criteria=frozenset(),
            weights=NightPolicyWeights(),
            runner=runner,
            emit_summary=False,
        )

        assert result.night_policy_result.optimized is True
        assert "joint-roundingsat-incremental-soft<=" in result.night_policy_result.tier


class TestWeightedUpperBound:
    """Test the weighted upper bound computation."""

    def test_upper_bound_non_negative(self):
        parsed = _make_minimal_two_week()
        weekend_config = _minimal_weekend_config()
        weights = NightPolicyWeights()

        bound = weighted_upper_bound_joint(parsed, weights, frozenset(), weekend_config)
        assert bound >= 0

    def test_hard_criteria_reduces_bound(self):
        parsed = _make_minimal_two_week()
        weekend_config = _minimal_weekend_config()
        weights = NightPolicyWeights()

        bound_all_soft = weighted_upper_bound_joint(parsed, weights, frozenset(), weekend_config)
        bound_hard_friday = weighted_upper_bound_joint(
            parsed, weights, frozenset({CRITERION_FRIDAY_WEEKEND_NCC1}), weekend_config
        )

        assert bound_hard_friday < bound_all_soft


# ---------------------------------------------------------------------------
# Smoke test calling joint solve directly
# ---------------------------------------------------------------------------

def test_joint_solve_result_type(runner):
    """solve_joint_schedule returns a JointSolveResult."""
    parsed = _make_minimal_two_week()
    night_config = _minimal_night_config(parsed.fellow_names)
    weekend_config = _minimal_weekend_config()

    result = solve_joint_schedule(
        parsed,
        night_config=night_config,
        weekend_config=weekend_config,
        hard_criteria=frozenset(),
        weights=NightPolicyWeights(),
        runner=runner,
        emit_summary=False,
    )

    assert isinstance(result, JointSolveResult)
    assert isinstance(result.weekend_solution, WeekendScheduleSolution)
    assert isinstance(result.night_solution, NightScheduleSolution)
    assert isinstance(result.night_policy_result, NightPolicySolveResult)
    assert len(result.weekend_solution.assignments_by_week) == 2
    assert len(result.night_solution.assignments_by_week) == 2
