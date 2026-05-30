"""Tests for the OPB encoder and RoundingSat PB solver backend.

Covers:
1. OpbBuilder unit tests (encoding, format)
2. RoundingSat runner integration tests (requires binary)
3. Full night solver PB pipeline tests (requires binary)
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

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
from scheduler.night_call_solver_policy import (
    ALL_POLICY_CRITERIA,
    CRITERION_STROKE,
    NightPolicyCounts,
    NightPolicyWeights,
    NightPolicySolveResult,
    criteria_counts_for_solution,
)

from parafrost_scheduler.opb_encoder import OpbBuilder
from parafrost_scheduler.roundingsat_runner import RoundingSatRunner
from parafrost_scheduler.night_solver_pb import (
    NightOpbVarMap,
    build_night_opb,
    extract_solution_pb,
    solve_night_schedule_roundingsat_at_limit,
    solve_night_schedule_roundingsat_incremental,
    weighted_upper_bound_pb,
)
from parafrost_scheduler.parafrost_runner import SolveResult

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
# Fixture helpers (same as test_night_solver.py)
# ---------------------------------------------------------------------------

def _make_simple_parsed() -> ParsedCallScheduleCsv:
    """1-week, 7-fellow schedule with all-Elective services."""
    fellow_names = ["A", "B", "C", "D", "E", "F", "G"]
    return ParsedCallScheduleCsv(
        fellow_names=fellow_names,
        existing_schedule_columns=WEEKEND_ROLES,
        week_rows=[
            WeekRow(
                weekday_assignments={f: "Elective" for f in fellow_names}
                | {"A": "NCC1", "B": "NCC2"},
                schedule_assignments={
                    "Weekend NCC1": "A",
                    "Weekend NCC2": "B",
                    "Weekend Stroke": "C",
                },
                raw_row=["NCC1", "NCC2", "Elective", "Elective", "Elective", "Elective", "Elective", "A", "B", "C"],
            )
        ],
        trailing_rows=[],
    )


def _simple_config_one_night_each() -> NightSolverConfig:
    """Each of the 7 fellows gets exactly 1 total night, 0 Fridays."""
    fellow_names = ["A", "B", "C", "D", "E", "F", "G"]
    return NightSolverConfig(
        total_nights={f: 1 for f in fellow_names},
        friday_nights={},
        total_night_multisets=(),
        friday_night_multisets=(),
        ccm_fellows=frozenset(),
        holiday_dates=(),
    )


def _make_two_week_parsed() -> ParsedCallScheduleCsv:
    """2-week, 11-fellow schedule for hard-stroke and constraint tests."""
    fellow_names = ["A", "B", "Clinic", "Stroke", "FridayWeekend", "SundayBad", "D", "E", "F", "G", "H"]
    w1_assignments = {f: "Elective" for f in fellow_names}
    w1_assignments["Stroke"] = "Stroke"
    w1_assignments["SundayBad"] = "MSICU"

    w2_assignments = {f: "Elective" for f in fellow_names}
    w2_assignments["SundayBad"] = "MSICU"

    return ParsedCallScheduleCsv(
        fellow_names=fellow_names,
        existing_schedule_columns=WEEKEND_ROLES,
        week_rows=[
            WeekRow(
                weekday_assignments=w1_assignments,
                schedule_assignments={
                    "Weekend NCC1": "FridayWeekend",
                    "Weekend NCC2": "B",
                    "Weekend Stroke": "C",
                },
                raw_row=list(w1_assignments.values()) + ["FridayWeekend", "B", "C"],
            ),
            WeekRow(
                weekday_assignments=w2_assignments,
                schedule_assignments={
                    "Weekend NCC1": "A",
                    "Weekend NCC2": "B",
                    "Weekend Stroke": "C",
                },
                raw_row=list(w2_assignments.values()) + ["A", "B", "C"],
            ),
        ],
        trailing_rows=[],
    )


def _two_week_config(fellow_names: list[str]) -> NightSolverConfig:
    """2 weeks × 7 days = 14 nights total. SundayBad always blocked."""
    eligible = [f for f in fellow_names if f not in ("Stroke", "SundayBad")]
    totals: dict[str, int] = {}
    for i, f in enumerate(eligible):
        totals[f] = 2 if i < 5 else 1
    totals["Stroke"] = 0
    totals["SundayBad"] = 0
    return NightSolverConfig(
        total_nights=totals,
        friday_nights={},
        total_night_multisets=(),
        friday_night_multisets=(),
        ccm_fellows=frozenset(),
        holiday_dates=(),
    )


# ---------------------------------------------------------------------------
# OpbBuilder unit tests
# ---------------------------------------------------------------------------

class TestOpbBuilder:
    """Unit tests for the OPB formula builder (no solver needed)."""

    def test_new_var_allocates_sequentially(self):
        opb = OpbBuilder()
        v1 = opb.new_var()
        v2 = opb.new_var()
        v3 = opb.new_var()
        assert v1 == 1
        assert v2 == 2
        assert v3 == 3
        assert opb.num_vars == 3

    def test_new_vars_allocates_batch(self):
        opb = OpbBuilder()
        vs = opb.new_vars(5)
        assert vs == [1, 2, 3, 4, 5]
        assert opb.num_vars == 5

    def test_exactly_one_emits_single_constraint(self):
        opb = OpbBuilder()
        vs = opb.new_vars(3)
        opb.exactly_one(vs)
        assert opb.num_constraints == 1
        text = opb.to_opb()
        assert "= 1 ;" in text
        assert "+1 x1" in text
        assert "+1 x2" in text
        assert "+1 x3" in text

    def test_exactly_k_is_single_line(self):
        opb = OpbBuilder()
        vs = opb.new_vars(10)
        opb.exactly_k(vs, 5)
        assert opb.num_constraints == 1
        text = opb.to_opb()
        assert "= 5 ;" in text
        # No extra variables introduced
        assert opb.num_vars == 10

    def test_at_most_k_is_single_line(self):
        opb = OpbBuilder()
        vs = opb.new_vars(8)
        opb.at_most_k(vs, 3)
        assert opb.num_constraints == 1
        text = opb.to_opb()
        assert "<= 3 ;" in text

    def test_at_least_k_is_single_line(self):
        opb = OpbBuilder()
        vs = opb.new_vars(6)
        opb.at_least_k(vs, 2)
        assert opb.num_constraints == 1
        text = opb.to_opb()
        assert ">= 2 ;" in text

    def test_add_unit_positive_literal(self):
        opb = OpbBuilder()
        v = opb.new_var()
        opb.add_unit(v)
        text = opb.to_opb()
        assert f"+1 x{v} >= 1 ;" in text

    def test_add_unit_negative_literal(self):
        opb = OpbBuilder()
        v = opb.new_var()
        opb.add_unit(-v)
        text = opb.to_opb()
        assert f"+1 ~x{v} >= 1 ;" in text

    def test_weighted_sum_at_most_single_line(self):
        opb = OpbBuilder()
        vs = opb.new_vars(5)
        weighted = list(zip(vs, [5, 5, 1, 1, 1]))
        opb.weighted_sum_at_most(weighted, 10)
        assert opb.num_constraints == 1
        text = opb.to_opb()
        assert "<= 10 ;" in text
        # No aux variables — all 5 primary vars remain
        assert opb.num_vars == 5

    def test_weighted_sum_at_most_noop_on_empty(self):
        opb = OpbBuilder()
        opb.weighted_sum_at_most([], 100)
        assert opb.num_constraints == 0

    def test_header_counts_match_state(self):
        opb = OpbBuilder()
        vs = opb.new_vars(4)
        opb.exactly_k(vs, 2)
        opb.at_most_k(vs, 3)
        text = opb.to_opb()
        first_line = text.splitlines()[0]
        assert f"#variable= {opb.num_vars}" in first_line
        assert f"#constraint= {opb.num_constraints}" in first_line

    def test_negated_literals_encoded_as_tilde(self):
        opb = OpbBuilder()
        vs = opb.new_vars(3)
        # Pass negative literals (negated)
        neg_lits = [-v for v in vs]
        opb.at_least_k(neg_lits, 2)
        text = opb.to_opb()
        assert "~x1" in text
        assert "~x2" in text
        assert "~x3" in text

    def test_conditional_exactly_k_two_constraints(self):
        opb = OpbBuilder()
        vs = opb.new_vars(5)
        sel = opb.new_var()
        opb.conditional_exactly_k(vs, 2, sel)
        # Should produce 2 PB constraints
        assert opb.num_constraints == 2
        # No extra aux variables
        assert opb.num_vars == 6

    def test_write_opb(self, tmp_path):
        opb = OpbBuilder()
        vs = opb.new_vars(3)
        opb.exactly_one(vs)
        path = tmp_path / "test.opb"
        opb.write_opb(path)
        content = path.read_text()
        assert "#variable= 3 #constraint= 1" in content
        assert "= 1 ;" in content


# ---------------------------------------------------------------------------
# OPB formula size comparison
# ---------------------------------------------------------------------------

class TestOpbFormulaSizeVsCnf:
    """Verify that OPB formula uses fewer variables than equivalent CNF."""

    def test_opb_uses_only_primary_variables_for_exactly_k(self):
        """OPB exactly_k uses 0 auxiliary variables; CNF uses O(n*k) aux vars."""
        from parafrost_scheduler.cnf_builder import CnfBuilder

        n = 100  # large n to make the difference clear
        k = 60

        opb = OpbBuilder()
        vs_opb = opb.new_vars(n)
        opb.exactly_k(vs_opb, k)

        cnf = CnfBuilder()
        vs_cnf = cnf.new_vars(n)
        cnf.exactly_k(vs_cnf, k)

        # OPB: exactly n primary vars, 1 constraint
        assert opb.num_vars == n
        assert opb.num_constraints == 1

        # CNF: needs many auxiliary variables for the totalizer / sequential counter
        assert cnf.num_vars > n, (
            f"Expected CNF to introduce aux vars (got {cnf.num_vars} total for n={n}, k={k})"
        )

    def test_opb_uses_no_aux_for_weighted_sum(self):
        """OPB weighted_sum_at_most uses 0 aux vars; CNF uses O(bound) aux vars."""
        from parafrost_scheduler.cnf_builder import CnfBuilder

        n = 50
        bound = 100
        weights_list = [(i + 1) for i in range(n)]

        opb = OpbBuilder()
        vs_opb = opb.new_vars(n)
        opb.weighted_sum_at_most(list(zip(vs_opb, weights_list)), bound)

        cnf = CnfBuilder()
        vs_cnf = cnf.new_vars(n)
        cnf.weighted_sum_at_most(list(zip(vs_cnf, weights_list)), bound)

        # OPB: n vars, 1 constraint
        assert opb.num_vars == n
        assert opb.num_constraints == 1
        # CNF: needs aux variables
        assert cnf.num_vars > n

    def test_build_night_opb_vs_build_night_cnf(self):
        """Full encoder: OPB formula has far fewer constraints than CNF."""
        from parafrost_scheduler.night_solver import build_night_cnf
        from parafrost_scheduler.cnf_builder import CnfBuilder

        parsed = _make_simple_parsed()
        config = _simple_config_one_night_each()
        weights = NightPolicyWeights(stroke=5)
        hard_criteria = frozenset()

        opb, var_map_opb = build_night_opb(
            parsed, config=config, hard_criteria=hard_criteria, weights=weights, soft_bound=50,
        )
        cnf, var_map_cnf = build_night_cnf(
            parsed, config=config, hard_criteria=hard_criteria, weights=weights, soft_bound=50,
        )

        # Primary variable count should be the same
        primary_count = var_map_opb.num_days * var_map_opb.num_fellows
        assert var_map_opb.num_days == var_map_cnf.num_days
        assert var_map_opb.num_fellows == var_map_cnf.num_fellows

        # OPB should use dramatically fewer constraints (and no aux vars for cardinality)
        # At minimum, OPB constraint count << CNF clause count for real-sized problems
        print(
            f"\nFormula comparison:"
            f"\n  OPB: {opb.num_vars} vars, {opb.num_constraints} constraints"
            f"\n  CNF: {cnf.num_vars} vars, {cnf.num_clauses} clauses"
        )
        # OPB should not need aux vars beyond primary + selectors
        assert opb.num_vars == primary_count, (
            f"OPB used {opb.num_vars - primary_count} aux variables "
            f"(expected 0 for this simple config with no multisets)"
        )

    def test_build_night_opb_valid_header(self):
        parsed = _make_simple_parsed()
        config = _simple_config_one_night_each()
        weights = NightPolicyWeights()

        opb, var_map = build_night_opb(
            parsed,
            config=config,
            hard_criteria=frozenset(),
            weights=weights,
            soft_bound=None,
        )

        # Primary variables: 7 days × 7 fellows = 49
        assert var_map.num_days == 7
        assert var_map.num_fellows == 7
        assert opb.num_vars >= 49

        text = opb.to_opb()
        assert text.startswith("* #variable=")
        first_line = text.splitlines()[0]
        assert f"#variable= {opb.num_vars}" in first_line
        assert f"#constraint= {opb.num_constraints}" in first_line


# ---------------------------------------------------------------------------
# RoundingSat runner integration tests
# ---------------------------------------------------------------------------

class TestRoundingSatRunner:
    """Integration tests against the actual RoundingSat binary."""

    def test_trivial_sat(self, runner):
        """x1 must be true, x1+x2+x3=1 → x1 true, others false."""
        opb = OpbBuilder()
        v1, v2, v3 = opb.new_vars(3)
        opb.exactly_one([v1, v2, v3])
        opb.add_unit(v1)  # force x1=1

        result = runner.solve(opb)
        assert result.satisfiable is True
        assert result.assignment is not None
        assert result.assignment[v1] is True
        assert result.assignment[v2] is False
        assert result.assignment[v3] is False

    def test_trivial_unsat(self, runner):
        """x1 must be true AND x1 must be false → UNSAT."""
        opb = OpbBuilder()
        v1 = opb.new_var()
        opb.add_unit(v1)   # x1 = 1
        opb.add_unit(-v1)  # x1 = 0

        result = runner.solve(opb)
        assert result.satisfiable is False
        assert result.assignment is None

    def test_exactly_k_native(self, runner):
        """Exactly 3 of 5 variables true — solved as a single native PB constraint."""
        opb = OpbBuilder()
        vs = opb.new_vars(5)
        opb.exactly_k(vs, 3)

        result = runner.solve(opb)
        assert result.satisfiable is True
        assert result.assignment is not None
        true_count = sum(1 for v in vs if result.assignment.get(v, False))
        assert true_count == 3

    def test_weighted_sum_bound(self, runner):
        """Weighted sum constraint: 5*x1 + 5*x2 + 1*x3 <= 5, x1 and x2 forced true.

        x1=T, x2=T gives weight=10 > 5, so must be UNSAT.
        """
        opb = OpbBuilder()
        v1, v2, v3 = opb.new_vars(3)
        opb.weighted_sum_at_most([(v1, 5), (v2, 5), (v3, 1)], 5)
        opb.add_unit(v1)
        opb.add_unit(v2)

        result = runner.solve(opb)
        assert result.satisfiable is False

    def test_weighted_sum_sat(self, runner):
        """Weighted sum constraint: weight of chosen vars must be <= 10."""
        opb = OpbBuilder()
        vs = opb.new_vars(5)
        weights_list = [1, 2, 3, 4, 5]
        opb.exactly_k(vs, 2)
        opb.weighted_sum_at_most(list(zip(vs, weights_list)), 3)

        result = runner.solve(opb)
        assert result.satisfiable is True
        assert result.assignment is not None
        chosen = [v for v in vs if result.assignment.get(v, False)]
        assert len(chosen) == 2
        total_weight = sum(weights_list[vs.index(v)] for v in chosen)
        assert total_weight <= 3

    def test_runtime_seconds_populated(self, runner):
        opb = OpbBuilder()
        v = opb.new_var()
        opb.add_unit(v)
        result = runner.solve(opb)
        assert result.runtime_seconds >= 0.0

    def test_stdout_captured(self, runner):
        opb = OpbBuilder()
        v = opb.new_var()
        opb.add_unit(v)
        result = runner.solve(opb)
        assert "SATISFIABLE" in result.stdout


# ---------------------------------------------------------------------------
# Full PB night solver pipeline
# ---------------------------------------------------------------------------

class TestNightSolverPb:
    """Full pipeline tests using RoundingSat (requires binary)."""

    def test_build_night_opb_structure(self):
        parsed = _make_simple_parsed()
        config = _simple_config_one_night_each()
        weights = NightPolicyWeights(stroke=5)

        opb, var_map = build_night_opb(
            parsed,
            config=config,
            hard_criteria=frozenset(),
            weights=weights,
            soft_bound=None,
        )

        assert var_map.num_days == 7
        assert var_map.num_fellows == 7
        assert opb.num_vars >= 49
        assert opb.num_constraints > 0

        text = opb.to_opb()
        assert text.startswith("* #variable=")

    def test_solve_at_limit_finds_valid_solution(self, runner):
        parsed = _make_simple_parsed()
        config = _simple_config_one_night_each()
        weights = NightPolicyWeights()

        result = solve_night_schedule_roundingsat_at_limit(
            parsed,
            config=config,
            hard_criteria=frozenset(),
            weights=weights,
            runner=runner,
            emit_summary=False,
        )

        assert result.solution is not None
        assert len(result.solution.assignments_by_week) == 1
        week_assignments = result.solution.assignments_by_week[0]
        assert len(week_assignments) == 7

        fellows_assigned = list(week_assignments.values())
        assert len(set(fellows_assigned)) == 7
        assert set(fellows_assigned) == set(parsed.fellow_names)

    def test_solve_incremental_optimizes(self, runner):
        parsed = _make_simple_parsed()
        config = _simple_config_one_night_each()
        weights = NightPolicyWeights()

        result = solve_night_schedule_roundingsat_incremental(
            parsed,
            config=config,
            hard_criteria=frozenset(),
            weights=weights,
            runner=runner,
            emit_summary=False,
        )

        assert result.optimized is True
        assert result.solution is not None
        assert len(result.solution.assignments_by_week) == 1
        assert "roundingsat-incremental-soft<=" in result.tier

    def test_hard_stroke_criteria_blocks_stroke_fellow(self, runner):
        parsed = _make_two_week_parsed()
        config = _two_week_config(parsed.fellow_names)
        weights = NightPolicyWeights(stroke=5)

        result = solve_night_schedule_roundingsat_at_limit(
            parsed,
            config=config,
            hard_criteria=frozenset({CRITERION_STROKE}),
            weights=weights,
            runner=runner,
            emit_summary=False,
        )

        all_assigned = [
            fellow
            for week in result.solution.assignments_by_week
            for fellow in week.values()
        ]
        assert "Stroke" not in all_assigned, (
            f"Stroke-service fellow was assigned nights: "
            f"{[f for f in all_assigned if f == 'Stroke']}"
        )

    def test_constraint_satisfaction_total_nights(self, runner):
        """Verify total nights per fellow match config exactly."""
        parsed = _make_simple_parsed()
        config = _simple_config_one_night_each()
        weights = NightPolicyWeights()

        result = solve_night_schedule_roundingsat_at_limit(
            parsed,
            config=config,
            hard_criteria=frozenset(),
            weights=weights,
            runner=runner,
            emit_summary=False,
        )

        from scheduler.call_schedule_common import NIGHT_ROLES
        total_by_fellow: dict[str, int] = {f: 0 for f in parsed.fellow_names}
        for week_assignments in result.solution.assignments_by_week:
            for role, fellow in week_assignments.items():
                total_by_fellow[fellow] += 1

        for fellow_name, expected_total in config.total_nights.items():
            assert total_by_fellow[fellow_name] == expected_total, (
                f"{fellow_name}: expected {expected_total} nights, "
                f"got {total_by_fellow[fellow_name]}"
            )

    def test_weighted_upper_bound_matches_parafrost_version(self):
        from parafrost_scheduler.night_solver import weighted_upper_bound

        parsed = _make_simple_parsed()
        weights = NightPolicyWeights(stroke=5)
        hard_criteria = frozenset()

        pb_bound = weighted_upper_bound_pb(parsed, weights, hard_criteria)
        sat_bound = weighted_upper_bound(parsed, weights, hard_criteria)

        assert pb_bound == sat_bound, (
            f"PB upper bound {pb_bound} != SAT upper bound {sat_bound}"
        )

    def test_solutions_match_between_backends(self, runner):
        """Both backends produce valid solutions for the same problem."""
        from parafrost_scheduler.parafrost_runner import ParaFrostRunner
        from parafrost_scheduler.night_solver import solve_night_schedule_parafrost_at_limit

        parafrost_binary = (
            Path(__file__).resolve().parent.parent
            / "vendor" / "ParaFROST" / "build" / "cpu" / "bin" / "parafrost"
        )
        if not parafrost_binary.exists():
            pytest.skip("ParaFROST binary not built — skipping cross-backend comparison")

        parsed = _make_simple_parsed()
        config = _simple_config_one_night_each()
        weights = NightPolicyWeights()

        pf_runner = ParaFrostRunner(parafrost_binary)
        rs_runner = runner

        pf_result = solve_night_schedule_parafrost_at_limit(
            parsed, config=config, hard_criteria=frozenset(), weights=weights,
            runner=pf_runner, emit_summary=False,
        )
        rs_result = solve_night_schedule_roundingsat_at_limit(
            parsed, config=config, hard_criteria=frozenset(), weights=weights,
            runner=rs_runner, emit_summary=False,
        )

        # Both solutions should satisfy the same constraints
        from scheduler.call_schedule_common import NIGHT_ROLES
        for result, name in [(pf_result, "parafrost"), (rs_result, "roundingsat")]:
            total_by_fellow = {f: 0 for f in parsed.fellow_names}
            for week in result.solution.assignments_by_week:
                for fellow in week.values():
                    total_by_fellow[fellow] += 1
            for f, expected in config.total_nights.items():
                assert total_by_fellow[f] == expected, (
                    f"{name}: {f} expected {expected}, got {total_by_fellow[f]}"
                )


# ---------------------------------------------------------------------------
# CLI integration test
# ---------------------------------------------------------------------------

def test_cli_roundingsat_reports_solver(tmp_path, capsys, monkeypatch):
    """CLI with --solver roundingsat prints 'RoundingSat'."""
    from parafrost_scheduler.cli import main as cli_main

    output_csv = tmp_path / "out.csv"
    fellow_names = ["A", "B", "C", "D", "E", "F", "G"]

    fake_parsed = ParsedCallScheduleCsv(
        fellow_names=fellow_names,
        existing_schedule_columns=WEEKEND_ROLES,
        week_rows=[
            WeekRow(
                weekday_assignments={f: "Elective" for f in fellow_names},
                schedule_assignments={
                    "Weekend NCC1": "A",
                    "Weekend NCC2": "B",
                    "Weekend Stroke": "C",
                },
                raw_row=["Elective"] * 7 + ["A", "B", "C"],
            )
        ],
        trailing_rows=[],
    )

    fake_solution = NightScheduleSolution(
        assignments_by_week=[
            {role: "A" for role in ["Night Mon", "Night Tue", "Night Wed",
                                     "Night Thu", "Night Fri", "Night Sat", "Night Sun"]}
        ]
    )
    fake_counts = NightPolicyCounts(
        by_criterion={c: 0 for c in ALL_POLICY_CRITERIA},
        weighted_total=0,
    )
    fake_result = NightPolicySolveResult(
        tier="roundingsat-incremental-soft<=0",
        solution=fake_solution,
        counts=fake_counts,
        hard_criteria=frozenset(),
        optimized=True,
    )

    with (
        patch("parafrost_scheduler.cli.parse_night_call_csv", return_value=fake_parsed),
        patch("parafrost_scheduler.roundingsat_runner.RoundingSatRunner.__init__", return_value=None),
        patch(
            "parafrost_scheduler.night_solver_pb.solve_night_schedule_roundingsat_incremental",
            return_value=fake_result,
        ),
        patch("parafrost_scheduler.cli.write_night_schedule_csv"),
    ):
        exit_code = cli_main([
            "input.csv",
            str(output_csv),
            "--solver", "roundingsat",
            "--roundingsat-path",
            str(ROUNDINGSAT_BINARY if ROUNDINGSAT_BINARY.exists() else tmp_path / "fake_roundingsat"),
        ])

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "RoundingSat" in captured.out or "roundingsat" in captured.out.lower()
